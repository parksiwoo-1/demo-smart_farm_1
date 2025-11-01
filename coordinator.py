"""Coordinator for tinyIoT server and multiple sensor simulators.

Coordinates the complete lifecycle:
- Starts tinyIoT server (if not already running)
- Launches sensors sequentially from device_profile.dat
- Monitors all processes and handles graceful shutdown
"""

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional

import requests

import config_coord as config

logging.basicConfig(level=getattr(config, 'LOG_LEVEL', logging.INFO), format='[%(levelname)s] %(message)s')


# Sensor configuration data class

class SensorConfig:
    """Configuration for a single sensor simulator."""

    def __init__(self, sensor_type: str, protocol: str = 'mqtt', mode: str = 'random',
                 frequency: int = 2, registration: int = 1) -> None:
        self.sensor_type = sensor_type
        self.protocol = protocol
        self.mode = mode
        self.frequency = frequency
        self.registration = registration


# Device profile loading

def _load_sensor_configs() -> List[SensorConfig]:
    """Load sensor configurations from device_profile.dat file."""
    profile_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'device_profile.dat')
    sensors: List[SensorConfig] = []

    if not os.path.exists(profile_path):
        logging.error(f"[COORD] device_profile.dat not found at: {profile_path}")
        logging.error("[COORD] Please create device_profile.dat in the same directory as coordinator.py")
        return sensors

    try:
        with open(profile_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                if not line or line.startswith('#'):
                    continue

                parts = [p.strip() for p in line.split(',')]
                if len(parts) != 5:
                    logging.error(
                        f"[COORD] Invalid format at line {line_num}: expected 5 fields, got {len(parts)}"
                    )
                    continue

                sensor_type, protocol, mode, frequency_str, registration_str = parts

                if protocol not in ['http', 'mqtt']:
                    logging.error(
                        f"[COORD] Invalid protocol '{protocol}' at line {line_num}: must be 'http' or 'mqtt'"
                    )
                    continue

                if mode not in ['csv', 'random']:
                    logging.error(
                        f"[COORD] Invalid mode '{mode}' at line {line_num}: must be 'csv' or 'random'"
                    )
                    continue

                try:
                    frequency = int(frequency_str)
                    if frequency <= 0:
                        raise ValueError("frequency must be positive")
                except ValueError as e:
                    logging.error(
                        f"[COORD] Invalid frequency '{frequency_str}' at line {line_num}: {e}"
                    )
                    continue

                if registration_str not in ['0', '1']:
                    logging.error(
                        f"[COORD] Invalid registration '{registration_str}' at line {line_num}: must be 0 or 1"
                    )
                    continue
                registration = int(registration_str)

                try:
                    sensors.append(SensorConfig(
                        sensor_type=sensor_type,
                        protocol=protocol,
                        mode=mode,
                        frequency=frequency,
                        registration=registration
                    ))
                    logging.debug(
                        f"[COORD] Loaded sensor config: {sensor_type} ({protocol}, {mode}, {frequency}s, reg={registration})"
                    )
                except Exception as exc:
                    logging.error(
                        f"[COORD] Failed to create sensor config at line {line_num}: {exc}"
                    )
                    continue

    except Exception as e:
        logging.error(f"[COORD] Failed to read device_profile.dat: {e}")
        return []

    if not sensors:
        logging.warning("[COORD] No valid sensor configurations found in device_profile.dat")
    else:
        logging.info(f"[COORD] Loaded {len(sensors)} sensor configuration(s) from device_profile.dat")

    return sensors


SENSORS_TO_RUN: List[SensorConfig] = _load_sensor_configs()


# Server health check

def check_server_running(base_url: str) -> bool:
    """Check if tinyIoT server is currently running."""
    headers = config.HEALTHCHECK_HEADERS
    req_timeout = getattr(config, 'REQUEST_TIMEOUT', 2)
    try:
        res = requests.get(base_url, headers=headers, timeout=req_timeout)
        if res.status_code == 200:
            return True
    except requests.exceptions.RequestException:
        pass
    return False


def wait_for_server(base_url: str, timeout: int = getattr(config, 'WAIT_SERVER_TIMEOUT', 30)) -> bool:
    """Wait for tinyIoT server to become responsive."""
    headers = config.HEALTHCHECK_HEADERS
    req_timeout = getattr(config, 'REQUEST_TIMEOUT', 2)
    for _ in range(timeout):
        try:
            res = requests.get(base_url, headers=headers, timeout=req_timeout)
            if res.status_code == 200:
                logging.info("[COORD] tinyIoT server is responsive.")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)
    logging.error("[COORD] Unable to connect to tinyIoT server.")
    return False


# Simulator process management

class SimulatorHandle:
    """Handle for managing a simulator subprocess with output monitoring."""

    def __init__(self, proc: subprocess.Popen, sensor_type: str) -> None:
        self.proc = proc
        self.sensor_type = sensor_type
        self._ready = threading.Event()
        self._failed = threading.Event()
        self._reader = threading.Thread(target=self._pump_output, daemon=True)
        self._reader.start()

    def _pump_output(self) -> None:
        """Forward stdout and monitor for state transitions."""
        tag = f"[{self.sensor_type.upper()}]"
        try:
            for line in self.proc.stdout:
                text = line.rstrip('\r\n')
                print(text, flush=True)

                if text.startswith(f"{tag} run "):
                    self._ready.set()
                elif 'registration failed' in text.lower():
                    self._failed.set()
                elif '[INFO] Stopping per user request' in text:
                    self._failed.set()
        finally:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            self.proc.wait()
            if not self._ready.is_set():
                self._failed.set()

    def wait_until_ready(self) -> bool:
        """Wait for simulator to complete initialization."""
        while True:
            if self._ready.wait(timeout=0.2):
                return True
            if self._failed.is_set():
                return False
            if self.proc.poll() is not None and not self._ready.is_set():
                return False

    def terminate(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except Exception:
                pass

    def join_reader(self, timeout: float = 1.0) -> None:
        if self._reader.is_alive():
            self._reader.join(timeout)


# Simulator launching

def start_simulator(sensor_config: SensorConfig, index: int,
                    simulator_path: str, base_url: str,
                    csv_base_path: Optional[str],
                    cse_id: Optional[str],
                    mqtt_port: Optional[int],
                    mqtt_user: Optional[str],
                    mqtt_pass: Optional[str]) -> Optional[SimulatorHandle]:
    """Start a sensor simulator subprocess."""
    python_exec = getattr(config, 'PYTHON_EXEC', 'python3')
    sim_args = [
        python_exec, simulator_path,
        '--sensor', sensor_config.sensor_type,
        '--protocol', sensor_config.protocol,
        '--mode', sensor_config.mode,
        '--frequency', str(sensor_config.frequency),
        '--registration', str(sensor_config.registration),
        '--base-url', base_url,
    ]

    if sensor_config.mode == 'csv':
        if csv_base_path:
            csv_file = os.path.join(csv_base_path, f"{sensor_config.sensor_type}_data.csv")
        else:
            csv_file = f"smartfarm_data/{sensor_config.sensor_type}_data.csv"
        sim_args.extend(['--csv-path', csv_file])

    if sensor_config.protocol == 'mqtt':
        if cse_id:
            sim_args.extend(['--cse-id', cse_id])
        if mqtt_port:
            sim_args.extend(['--mqtt-port', str(mqtt_port)])
        if mqtt_user:
            sim_args.extend(['--mqtt-user', mqtt_user])
        if mqtt_pass:
            sim_args.extend(['--mqtt-pass', mqtt_pass])

    logging.info(f"[COORD] Starting simulator [{sensor_config.sensor_type}]...")
    logging.debug(f"[COORD] Command: {' '.join(sim_args)}")

    env = os.environ.copy()
    sim_env = getattr(config, 'SIM_ENV', None)
    if isinstance(sim_env, dict):
        env.update(sim_env)
    env['PYTHONUNBUFFERED'] = '1'
    env['SKIP_HEALTHCHECK'] = '1'  # Coordinator already verified server health

    try:
        proc = subprocess.Popen(
            sim_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env
        )
        return SimulatorHandle(proc, sensor_config.sensor_type)
    except Exception as e:
        logging.error(f"[COORD] Failed to start simulator #{index + 1}: {e}")
        return None


# Main entry point

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Coordinator for tinyIoT server and sensor simulators")
    parser.add_argument('--server-path', help='Path to tinyIoT server executable (required if server is not already running)')
    parser.add_argument('--simulator-path', required=True, help='Path to simulator.py')
    parser.add_argument('--base-url', required=True, help='Base URL with CSE RN (e.g., http://127.0.0.1:3000/TinyIoT)')
    parser.add_argument('--csv-base-path', help='Base directory for CSV files (default: smartfarm_data/)')
    parser.add_argument('--cse-id', help='CSE-ID for MQTT topic (required when using MQTT protocol)')
    parser.add_argument('--mqtt-port', type=int, help='MQTT broker port (required when using MQTT protocol)')
    parser.add_argument('--mqtt-user', help='MQTT broker username (optional, for authenticated brokers)')
    parser.add_argument('--mqtt-pass', help='MQTT broker password (optional, for authenticated brokers)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode (show all simulator output)')
    args = parser.parse_args()

    if not SENSORS_TO_RUN:
        logging.error("[COORD] No sensors configured in device_profile.dat")
        logging.error("[COORD] Please create device_profile.dat with sensor configurations")
        logging.error("[COORD] Example format: temperature,http,csv,3,1")
        sys.exit(1)

    if not os.path.isfile(args.simulator_path):
        logging.error(f"[COORD] Simulator not found: {args.simulator_path}")
        sys.exit(1)

    has_mqtt = any(s.protocol == 'mqtt' for s in SENSORS_TO_RUN)
    has_csv = any(s.mode == 'csv' for s in SENSORS_TO_RUN)

    if has_mqtt and not args.cse_id:
        logging.error("[COORD] --cse-id is required when using MQTT protocol")
        sys.exit(1)

    if has_mqtt and not args.mqtt_port:
        logging.error("[COORD] --mqtt-port is required when using MQTT protocol")
        sys.exit(1)

    if args.server_path and not os.path.isfile(args.server_path):
        logging.error(f"[COORD] Server executable not found: {args.server_path}")
        sys.exit(1)

    server_proc = None
    server_already_running = check_server_running(args.base_url)

    if server_already_running:
        logging.info("[COORD] tinyIoT server is already running.")
    else:
        logging.info("[COORD] tinyIoT server is not running.")
        if not args.server_path:
            logging.error("[COORD] Server is not running and --server-path was not provided.")
            logging.error("[COORD] Please either:")
            logging.error("[COORD]   1. Start tinyIoT server manually, or")
            logging.error("[COORD]   2. Provide --server-path parameter")
            sys.exit(1)

        logging.info(f"[COORD] Starting tinyIoT server from: {args.server_path}")
        try:
            server_proc = subprocess.Popen([args.server_path])
        except Exception as e:
            logging.error(f"[COORD] Failed to start tinyIoT server: {e}")
            sys.exit(1)

        if not wait_for_server(args.base_url):
            try:
                server_proc.terminate()
            except Exception:
                pass
            sys.exit(1)

    simulator_handles: List[SimulatorHandle] = []
    logging.info(f"[COORD] starting {len(SENSORS_TO_RUN)} sensor simulators...")

    try:
        for i, sensor_conf in enumerate(SENSORS_TO_RUN):
            handle = start_simulator(
                sensor_conf, i,
                args.simulator_path,
                args.base_url,
                args.csv_base_path,
                args.cse_id,
                args.mqtt_port,
                args.mqtt_user,
                args.mqtt_pass
            )
            if not handle:
                logging.error(f"[COORD] Failed to start simulator [{sensor_conf.sensor_type}]")
                continue
            logging.info(f"[COORD] Waiting for simulator [{sensor_conf.sensor_type}] to finish registration...")
            if handle.wait_until_ready():
                logging.info(f"[COORD] Simulator [{sensor_conf.sensor_type}] ready.")
                simulator_handles.append(handle)
            else:
                logging.error(f"[COORD] Simulator [{sensor_conf.sensor_type}] failed during setup; terminating start sequence.")
                handle.terminate()
                handle.join_reader()
                break
    except KeyboardInterrupt:
        logging.info("[COORD] start interrupted by user.")

    if not simulator_handles:
        logging.error("[COORD] No simulators were started successfully.")
        if server_proc:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=getattr(config, 'SERVER_TERM_WAIT', 5))
            except Exception:
                pass
        sys.exit(1)

    ok = len(simulator_handles)
    need = len(SENSORS_TO_RUN)
    if ok != need:
        ok_names = [h.sensor_type for h in simulator_handles]
        need_names = [c.sensor_type for c in SENSORS_TO_RUN]
        fail_names = [n for n in need_names if n not in ok_names]
        logging.error(f"[COORD] start failed: started {ok}/{need}. failed={fail_names}")

        running_count = sum(1 for h in simulator_handles if h.proc and h.proc.poll() is None)
        if running_count > 0:
            logging.info(f"[COORD] Terminating {running_count} simulator(s)...")

        for handle in simulator_handles:
            if handle.proc and handle.proc.poll() is None:
                try:
                    handle.terminate()
                    handle.proc.wait(timeout=getattr(config, 'PROC_TERM_WAIT', 5))
                except Exception:
                    if args.debug:
                        logging.warning(f"[COORD] Failed to terminate simulator [{handle.sensor_type}]; killing...")
                    handle.kill()
            handle.join_reader(getattr(config, 'JOIN_READER_TIMEOUT', 1.0))

        if server_proc and server_proc.poll() is None:
            try:
                logging.info("[COORD] Terminating tinyIoT server...")
                server_proc.terminate()
                server_proc.wait(timeout=getattr(config, 'SERVER_TERM_WAIT', 5))
            except Exception:
                logging.warning("[COORD] Failed to terminate server; killing...")
                try:
                    server_proc.kill()
                except Exception:
                    pass
        sys.exit(1)

    logging.info("[COORD] Successfully started all simulators.")

    try:
        while True:
            if server_proc and server_proc.poll() is not None:
                logging.error("[COORD] tinyIoT server has stopped unexpectedly.")
                break
            running_sims = sum(1 for h in simulator_handles if h.proc.poll() is None)
            if running_sims == 0:
                logging.info("[COORD] All simulators have stopped.")
                break
            time.sleep(5)
    except KeyboardInterrupt:
        time.sleep(0.5)
        print()
    finally:
        cleanup_interrupted = False

        running_count = sum(1 for h in simulator_handles if h.proc and h.proc.poll() is None)

        for handle in simulator_handles:
            if handle.proc and handle.proc.poll() is None:
                try:
                    handle.terminate()
                    handle.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if args.debug:
                        logging.warning(f"[COORD] Simulator [{handle.sensor_type}] did not exit in time; killing...")
                    handle.kill()
                except KeyboardInterrupt:
                    cleanup_interrupted = True
                    if args.debug:
                        logging.warning(
                            f"[COORD] Cleanup interrupted while waiting for simulator [{handle.sensor_type}]; killing..."
                        )
                    handle.kill()
                except Exception as e:
                    if args.debug:
                        logging.warning(f"[COORD] Failed to terminate simulator [{handle.sensor_type}]: {e}; killing...")
                    handle.kill()
            handle.join_reader()

        if server_proc and server_proc.poll() is None:
            try:
                logging.info("[COORD] Terminating tinyIoT server...")
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logging.warning("[COORD] Server did not exit in time; killing...")
                try:
                    server_proc.kill()
                except Exception:
                    pass
            except KeyboardInterrupt:
                cleanup_interrupted = True
                logging.warning("[COORD] Cleanup interrupted while waiting for server; killing...")
                try:
                    server_proc.kill()
                except Exception:
                    pass
            except Exception as e:
                logging.warning(f"[COORD] Failed to terminate server: {e}; killing...")
                try:
                    server_proc.kill()
                except Exception:
                    pass

        if cleanup_interrupted:
            logging.warning("[COORD] Cleanup was interrupted by user input; forced termination may leave child logs incomplete.")
        logging.info("[COORD] All processes terminated.")
