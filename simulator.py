"""oneM2M sensor simulator supporting HTTP and MQTT protocols."""

import argparse
import csv
import json
import os
import random
import signal
import string
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

import config_sim as config

HTTP = requests.Session()


# Configuration helpers

def _env_or_config(name: str, default=None):
    return os.getenv(name, getattr(config, name, default))


def _env_or_config_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is not None:
        try:
            return int(raw)
        except ValueError:
            pass
    try:
        return int(getattr(config, name))
    except AttributeError:
        return default
    except (TypeError, ValueError):
        return default


def _env_or_config_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    value = getattr(config, name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_optional(value):
    """Convert empty/null strings to None."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() in {"none", "null"}:
            return None
        return stripped
    return value


def _describe_mqtt_rc(reason) -> str:
    """Convert MQTT reason code to human-readable description."""
    if hasattr(reason, "getName"):
        try:
            return reason.getName()
        except Exception:
            pass
    name = getattr(reason, "name", None)
    if isinstance(name, str) and name:
        return name.replace("_", " ")
    value = getattr(reason, "value", reason)
    if value is None:
        return "Unknown error"
    try:
        return mqtt.error_string(int(value))
    except Exception:
        return "Unknown error"


# HTTP headers builder

class Headers:
    """Build oneM2M HTTP headers with origin, RVI, RI, and content-type."""

    def __init__(self, content_type: Optional[str] = None, origin: str = "CAdmin", ri: str = "req"):
        self.headers = dict(config.HTTP_DEFAULT_HEADERS)
        self.headers["X-M2M-Origin"] = origin
        self.headers["X-M2M-RI"] = ri
        if content_type:
            self.headers["Content-Type"] = f"application/json;ty={self.get_content_type(content_type)}"

    @staticmethod
    def get_content_type(content_type: str):
        return config.HTTP_CONTENT_TYPE_MAP.get(content_type)


# URL builders

def build_ae_url(ae: str) -> str:
    return f"{config.BASE_URL}/{ae}"


def build_cnt_url(ae: str, cnt: str) -> str:
    return f"{config.BASE_URL}/{ae}/{cnt}"


# oneM2M response parsing

def _get_response_code(resp) -> Optional[int]:
    """Extract oneM2M RSC from HTTP headers."""
    try:
        v = resp.headers.get("X-M2M-RSC") or resp.headers.get("x-m2m-rsc")
        return int(v) if v is not None else None
    except Exception:
        return None


# Resource deletion with admin privileges

def _delete_as_admin(paths: List[str]) -> bool:
    """Delete resources as CAdmin (used for conflict resolution)."""
    hdr = Headers(origin="CAdmin").headers
    for p in dict.fromkeys(paths):
        try:
            r = HTTP.delete(p, headers=hdr, timeout=config.HTTP_REQUEST_TIMEOUT)
            rsc = _get_response_code(r)
            if not (r.status_code in (200, 202, 204) or rsc == 2002):
                print(f"[ERROR] DELETE {p} -> {r.status_code} rsc={rsc} body={getattr(r, 'text', '')}")
                sys.exit(1)
        except Exception as exc:
            print(f"[ERROR] DELETE {p} failed: {exc}")
            sys.exit(1)
    return True


# HTTP resource creation

def http_create_ae(ae_rn: str, api: str) -> Tuple[bool, Optional[str]]:
    """Create AE via HTTP with automatic conflict resolution.

    Returns (success, aei) tuple. On conflict, deletes existing resource and retries.
    """
    unique_origin = f"{ae_rn}-{uuid.uuid4().hex[:8]}"

    def _try_create(origin_for_create: str):
        try:
            r = HTTP.post(
                config.BASE_URL,
                headers=Headers("ae", origin=origin_for_create).headers,
                json={"m2m:ae": {"rn": ae_rn, "api": api, "rr": True}},
                timeout=config.HTTP_REQUEST_TIMEOUT
            )
            rsc_hdr = _get_response_code(r)
            if r.status_code in (200, 201) or rsc_hdr == 2001:
                try:
                    js = r.json()
                    aei = js.get("m2m:ae", {}).get("aei")
                except Exception:
                    aei = None
                if not aei:
                    aei = origin_for_create
                return True, aei, r
            return False, None, r
        except Exception as e:
            return False, None, e

    ok, aei, r = _try_create(unique_origin)
    if ok:
        return True, aei

    # Handle conflict
    if isinstance(r, requests.Response):
        rsc = _get_response_code(r)
        body_text = r.text if hasattr(r, "text") else ""
        if r.status_code in (409,) or rsc == 4105 or "already exists" in (body_text or "").lower():
            candidates = [build_ae_url(ae_rn)]
            print(f"[HTTP] AE RN duplicate -> DELETE {candidates} (as CAdmin) and retry")
            if not _delete_as_admin(candidates):
                return False, None
            ok2, aei2, r2 = _try_create(unique_origin)
            if ok2:
                return True, aei2
            if isinstance(r2, requests.Response):
                print(f"[ERROR] AE re-create HTTP -> {r2.status_code} {r2.text}")
            else:
                print(f"[ERROR] AE re-create HTTP failed: {r2}")
            return False, None
        else:
            print(f"[ERROR] AE create HTTP -> {r.status_code} {body_text}")
            return False, None
    else:
        print(f"[ERROR] AE create HTTP failed: {r}")
        return False, None


def http_create_cnt(ae_rn: str, cnt_rn: str, origin_aei: str) -> bool:
    """Create Container via HTTP with automatic conflict resolution."""

    def _try_create():
        try:
            r = HTTP.post(
                build_ae_url(ae_rn),
                headers=Headers("cnt", origin=origin_aei).headers,
                json={"m2m:cnt": {"rn": cnt_rn, "mni": config.CNT_MNI, "mbs": config.CNT_MBS}},
                timeout=config.HTTP_REQUEST_TIMEOUT
            )
            if r.status_code in (200, 201):
                return True, r
            return False, r
        except Exception as e:
            return False, e

    ok, r = _try_create()
    if ok:
        return True

    # Handle conflict
    if isinstance(r, requests.Response):
        rsc = _get_response_code(r)
        body_text = r.text if hasattr(r, "text") else ""
        if r.status_code in (409,) or rsc == 4105 or "already exists" in (body_text or "").lower():
            candidates = [build_cnt_url(ae_rn, cnt_rn)]
            print(f"[HTTP] CNT RN duplicate -> DELETE {candidates} (as CAdmin) and retry")
            if not _delete_as_admin(candidates):
                return False
            ok2, r2 = _try_create()
            if ok2:
                return True
            if isinstance(r2, requests.Response):
                print(f"[ERROR] CNT re-create HTTP -> {r2.status_code} {r2.text}")
            else:
                print(f"[ERROR] CNT re-create HTTP failed: {r2}")
            return False
        else:
            print(f"[ERROR] CNT create HTTP -> {r.status_code} {body_text}")
            return False
    else:
        print(f"[ERROR] CNT create HTTP failed: {r}")
        return False


def get_latest_content(ae_rn, cnt_rn) -> Optional[str]:
    """Retrieve latest CIN value from container (for timeout verification)."""
    la = f"{build_cnt_url(ae_rn, cnt_rn)}/la"
    try:
        r = HTTP.get(la, headers=config.HTTP_GET_HEADERS, timeout=config.HTTP_REQUEST_TIMEOUT)
        if r.status_code == 200:
            js = r.json()
            return js.get("m2m:cin", {}).get("con")
    except Exception:
        pass
    return None


def check_server_health() -> bool:
    """Perform health check against CSE server."""
    url = getattr(config, "CSE_URL", None) or getattr(config, "BASE_URL", None)
    if not url:
        return True
    headers = config.HTTP_GET_HEADERS
    ct = getattr(config, "CONNECT_TIMEOUT", 1)
    rt = getattr(config, "READ_TIMEOUT", 1)
    try:
        r = HTTP.get(url, headers=headers, timeout=(ct, rt))
        ok = r.status_code == 200
        if ok:
            print(f"[SIM] tinyIoT server is responsive at {url}.")
        else:
            print(f"[SIM][ERROR] tinyIoT healthcheck failed: HTTP {r.status_code} {url}")
        return ok
    except requests.exceptions.RequestException as e:
        print(f"[SIM][ERROR] tinyIoT healthcheck error at {url}: {e}")
        return False


def send_cin_http(ae_rn: str, cnt_rn: str, value, origin_aei: str) -> bool:
    """Send CIN via HTTP. On timeout, verifies if data was stored via /la endpoint."""
    hdr = Headers(content_type="cin", origin=origin_aei).headers
    body = {"m2m:cin": {"con": value}}
    u = build_cnt_url(ae_rn, cnt_rn)
    try:
        r = HTTP.post(u, headers=hdr, json=body, timeout=config.HTTP_REQUEST_TIMEOUT)
        if r.status_code in (200, 201):
            return True
        try:
            text = r.json()
        except Exception:
            text = r.text
        print(f"[ERROR] POST {u} -> {r.status_code} {text}")
        return False
    except requests.exceptions.ReadTimeout:
        latest = get_latest_content(ae_rn, cnt_rn)
        if latest == str(value):
            print("[WARN] POST timed out but verified via /la (stored).")
            return True
        print("[WARN] POST timed out and not verified; will retry.")
        return False
    except Exception as e:
        print(f"[ERROR] POST {u} failed: {e}")
        return False


# MQTT oneM2M client

class MqttOneM2MClient:
    """MQTT client implementing oneM2M request/response pattern.

    Uses /oneM2M/req/{origin}/{cse-id}/json and /oneM2M/resp/{origin}/{cse-id}/json topics.
    Supports origin switching after AE registration (temp → AEI).
    """

    def __init__(self, broker, port, origin, cse_csi, cse_rn="TinyIoT"):
        self.broker = broker
        self.port = int(port)
        self.origin = origin
        self.cse_csi = cse_csi
        self.cse_rn = cse_rn
        self.response_received = threading.Event()
        self.last_response = None
        self.connected = threading.Event()
        self._connect_event = threading.Event()
        self._last_connect_rc: Optional[int] = None
        self._last_connect_desc: Optional[str] = None

        self.username = _normalize_optional(_env_or_config("MQTT_USER", None))
        self.password = _normalize_optional(_env_or_config("MQTT_PASS", None))

        self.keepalive = max(1, _env_or_config_int("MQTT_KEEPALIVE", 60))
        self.connect_wait = max(1, _env_or_config_int("MQTT_CONNECT_WAIT", 10))
        self.response_timeout = max(1, _env_or_config_int("MQTT_RESPONSE_TIMEOUT", 5))

        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)

        self.req_topic = f"/oneM2M/req/{self.origin}/{self.cse_csi}/json"
        self.resp_topic = f"/oneM2M/resp/{self.origin}/{self.cse_csi}/json"

    def _update_topics(self):
        self.req_topic = f"/oneM2M/req/{self.origin}/{self.cse_csi}/json"
        self.resp_topic = f"/oneM2M/resp/{self.origin}/{self.cse_csi}/json"

    def set_origin(self, new_origin: str):
        """Update origin and resubscribe (called after AE registration to switch to AEI)."""
        previous_resp_topic = self.resp_topic
        self.origin = new_origin
        self._update_topics()
        if self.connected.is_set():
            try:
                if previous_resp_topic != self.resp_topic:
                    self.client.unsubscribe(previous_resp_topic)
                self.client.subscribe(self.resp_topic, qos=0)
                print(f"[MQTT] SWITCH ORIGIN -> {self.origin}; SUB {self.resp_topic}")
            except Exception as e:
                print(f"[ERROR] Failed to resubscribe with new origin: {e}")

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        code = 0 if code is None else code
        self._last_connect_rc = code
        self._last_connect_desc = _describe_mqtt_rc(reason_code)
        if code == 0:
            try:
                self.client.subscribe(self.resp_topic, qos=0)
                print(f"[MQTT] SUB {self.resp_topic}")
            except Exception as exc:
                print(f"[ERROR] MQTT subscribe failed after connect: {exc}")
            print("[MQTT] Connection successful.")
            self.connected.set()
            self._last_connect_desc = "Connection accepted"
        else:
            desc = self._last_connect_desc or "Unknown error"
            print(f"[MQTT] Connection failed: rc={code} ({desc})")
        self._connect_event.set()

    def on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        code = getattr(reason_code, "value", reason_code)
        code = 0 if code is None else code
        self.connected.clear()

    def on_message(self, client, userdata, msg):
        try:
            payload_txt = msg.payload.decode()
            self.last_response = json.loads(payload_txt)
            self.response_received.set()
            print(f"\n[MQTT][RECV] {payload_txt}\n\n", end="\n\n", flush=True)
        except Exception as e:
            print(f"[ERROR] Failed to parse MQTT response: {e}")

    def connect(self) -> bool:
        """Connect to MQTT broker and wait for confirmation."""
        try:
            if self.username is not None:
                self.client.username_pw_set(self.username, self.password or "")

            self._connect_event.clear()
            self.connected.clear()
            self._last_connect_rc = None
            self._last_connect_desc = None
            self.client.connect(self.broker, self.port, keepalive=self.keepalive)
            self.client.loop_start()

            if not self._connect_event.wait(timeout=self.connect_wait):
                print(f"[ERROR] MQTT broker handshake timeout after {self.connect_wait}s.")
                self.client.loop_stop()
                return False
            if self._last_connect_rc != 0:
                desc = self._last_connect_desc or _describe_mqtt_rc(self._last_connect_rc or -1)
                print(f"[ERROR] MQTT broker rejected connection rc={self._last_connect_rc} ({desc}).")
                self.client.loop_stop()
                return False
            print(f"[MQTT] Connected to broker {self.broker}:{self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to connect to MQTT broker: {e}")
            return False

    def disconnect(self):
        try:
            if self.connected.is_set():
                try:
                    self.client.unsubscribe(self.resp_topic)
                except Exception:
                    pass
            self.client.disconnect()
        except Exception:
            pass
        finally:
            try:
                self.client.loop_stop()
            except Exception:
                pass
            self.connected.clear()

    def _send_request(self, body, ok_rsc=(2000, 2001, 2004)):
        """Send oneM2M request via MQTT and wait for response."""
        if not self.connected.is_set():
            print("[ERROR] MQTT request attempted while client is disconnected.")
            return "timeout", None

        request_id = str(uuid.uuid4())
        message = {
            "fr": self.origin,
            "to": body["to"],
            "op": body["op"],
            "rqi": request_id,
            "ty": body.get("ty"),
            "pc": body.get("pc", {}),
            "rvi": "2a"
        }
        print(f"[MQTT][SEND] {json.dumps(message, ensure_ascii=False)}")
        self.response_received.clear()
        self.client.publish(self.req_topic, json.dumps(message))

        if self.response_received.wait(timeout=self.response_timeout):
            response = self.last_response or {}
            try:
                rsc = int(response.get("rsc"))
            except Exception:
                rsc = 0
            if rsc in ok_rsc:
                return "ok", response
            return "error", response
        print(f"[ERROR] No MQTT response within timeout ({self.response_timeout}s).")
        return "timeout", None

    def create_ae(self, ae_name: str, api: str) -> Tuple[bool, Optional[str]]:
        """Create AE via MQTT with automatic conflict resolution."""
        req = {
            "to": self.cse_rn,
            "op": 1,  # CREATE
            "ty": 2,  # AE
            "pc": {"m2m:ae": {"rn": ae_name, "api": api, "rr": True}}
        }
        status, resp = self._send_request(req, ok_rsc=(2001,))
        if status == "ok":
            aei = None
            try:
                aei = (resp or {}).get("pc", {}).get("m2m:ae", {}).get("aei")
            except Exception:
                aei = None
            return True, (aei or self.origin)
        if status == "error":
            try:
                rsc = int((resp or {}).get("rsc", 0))
            except Exception:
                rsc = 0
            if rsc == 4105:  # Conflict
                candidates = [build_ae_url(ae_name)]
                print(f"[MQTT] AE RN duplicate -> DELETE {candidates} (as CAdmin) and retry")
                if not _delete_as_admin(candidates):
                    return False, None
                status2, resp2 = self._send_request(req, ok_rsc=(2001,))
                if status2 == "ok":
                    aei2 = None
                    try:
                        aei2 = (resp2 or {}).get("pc", {}).get("m2m:ae", {}).get("aei")
                    except Exception:
                        aei2 = None
                    return True, (aei2 or self.origin)
                return False, None
            print(f"[ERROR] MQTT AE create failed rsc={rsc} msg={resp}")
            return False, None
        print("[ERROR] MQTT AE create timed out (no response).")
        return False, None

    def create_cnt(self, ae_name: str, cnt_name: str) -> bool:
        """Create Container via MQTT with automatic conflict resolution."""
        req = {
            "to": f"{self.cse_rn}/{ae_name}",
            "op": 1,  # CREATE
            "ty": 3,  # CNT
            "pc": {"m2m:cnt": {"rn": cnt_name}}
        }
        status, resp = self._send_request(req, ok_rsc=(2001,))
        if status == "ok":
            return True
        if status == "error":
            try:
                rsc = int((resp or {}).get("rsc", 0))
            except Exception:
                rsc = 0
            if rsc == 4105:  # Conflict
                candidates = [build_cnt_url(ae_name, cnt_name)]
                print(f"[MQTT] CNT RN duplicate -> DELETE {candidates} (as CAdmin) and retry")
                if not _delete_as_admin(candidates):
                    return False
                status2, _ = self._send_request(req, ok_rsc=(2001,))
                return status2 == "ok"
            print(f"[ERROR] MQTT CNT create failed rsc={rsc} msg={resp}")
            return False
        print("[ERROR] MQTT CNT create timed out (no response).")
        return False

    def send_cin(self, ae_name: str, cnt_name: str, value):
        """Send CIN via MQTT with single retry."""
        attempts = 0
        status, resp = "", None
        while attempts < 2:
            attempts += 1
            status, resp = self._send_request({
                "to": f"{self.cse_rn}/{ae_name}/{cnt_name}",
                "op": 1,  # CREATE
                "ty": 4,  # CIN
                "pc": {"m2m:cin": {"con": value}}
            }, ok_rsc=(2001,))
            if status == "ok":
                return True
            if attempts < 2:
                print("[MQTT] CIN send failed; retrying once...")

        if status == "timeout":
            print("[ERROR] No MQTT response within timeout during CIN send (after retry).")
        elif resp:
            print(f"[ERROR] CIN send failed after retry rsc={resp.get('rsc')} msg={resp}")
        else:
            print("[ERROR] CIN send failed after retry (unknown response).")
        return False


# HTTP resource registration

def ensure_http_registration(ae: str, cnt: str, api: str, do_register: bool) -> Tuple[bool, Optional[str]]:
    """Ensure AE and Container resources exist via HTTP."""
    if not do_register:
        return True, None
    print(f"[HTTP] AE create -> {ae}")
    ok, aei = http_create_ae(ae, api)
    if not ok or not aei:
        return False, None
    print(f"[HTTP] CNT create -> {ae}/{cnt}")
    if not http_create_cnt(ae, cnt, origin_aei=aei):
        return False, None
    return True, aei


# Random data generation

VALID_PROFILE_TYPES = {"int", "float", "string"}


def validate_random_profile(sensor_key: str, profile: Any) -> Dict[str, Any]:
    """Validate and normalize random data generation profile."""
    if not isinstance(profile, dict):
        raise ValueError(f"[{sensor_key}] Random profile must be a mapping, got {type(profile).__name__}.")

    profile = dict(profile)
    data_type = profile.get("data_type")
    if data_type not in VALID_PROFILE_TYPES:
        raise ValueError(f"[{sensor_key}] Unsupported data_type '{data_type}'. Allowed: {sorted(VALID_PROFILE_TYPES)}")

    if data_type in {"int", "float"}:
        try:
            minimum = float(profile["min"])
            maximum = float(profile["max"])
        except KeyError as exc:
            raise ValueError(f"[{sensor_key}] Random profile missing key: {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"[{sensor_key}] Random profile min/max must be numeric.") from exc
        if minimum > maximum:
            raise ValueError(f"[{sensor_key}] Random profile min value cannot exceed max value.")
        if data_type == "int":
            profile["min"] = int(minimum)
            profile["max"] = int(maximum)
        else:
            profile["min"] = minimum
            profile["max"] = maximum

    if data_type == "string":
        try:
            length = int(profile.get("length", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"[{sensor_key}] Random profile length must be an integer.") from exc
        if length <= 0:
            raise ValueError(f"[{sensor_key}] Random profile length must be positive.")
        profile["length"] = length

    return profile


# Sensor metadata builder

def build_sensor_metadata(name: str) -> Dict:
    """Build sensor metadata from config or generate from template."""
    sensor_key = name.lower()
    upper = sensor_key.upper()
    resources = getattr(config, "SENSOR_RESOURCES", {})
    meta = dict(resources.get(sensor_key, {}))

    if not meta:
        template = getattr(config, "GENERIC_SENSOR_TEMPLATE", {})
        if template:
            meta = {k: v.format(sensor=sensor_key) for k, v in template.items()}
        else:
            meta = {
                "ae": f"C{sensor_key}Sensor",
                "cnt": sensor_key,
                "api": f"N.{sensor_key}",
                "origin": f"C{sensor_key}Sensor",
            }

    meta.setdefault("ae", f"C{sensor_key}Sensor")
    meta.setdefault("cnt", sensor_key)
    meta.setdefault("api", f"N.{sensor_key}")
    meta.setdefault("origin", meta.get("ae") or f"C{sensor_key}Sensor")

    if not meta.get("csv"):
        if config.CSV_PATH:
            meta["csv"] = config.CSV_PATH

    profile = meta.get("profile") or getattr(config, f"{upper}_PROFILE", None)
    if profile is None:
        profile = getattr(config, "GENERIC_RANDOM_PROFILE", {"data_type": "float", "min": 0.0, "max": 100.0})

    meta["profile"] = validate_random_profile(sensor_key, profile)
    return meta


# Sensor worker

class SensorWorker:
    """Main sensor simulator managing lifecycle: setup → run → stop."""

    def __init__(self, sensor_name: str, protocol: str, mode: str,
                 period_sec: float, registration: int):
        try:
            self.meta = build_sensor_metadata(sensor_name)
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            raise SystemExit(1) from exc
        self.sensor_name = sensor_name
        self.protocol = protocol
        self.mode = mode
        self.period_sec = float(period_sec)
        self.registration = registration
        self.stop_flag = threading.Event()
        self.csv_data, self.csv_index = [], 0
        self.mqtt = None
        self.aei: Optional[str] = None
        self._signals_installed = False

    def _install_signal_handlers_once(self):
        if self._signals_installed:
            return
        self._signals_installed = True

        def _handler(signum, frame):
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(sig, _handler)
            except Exception:
                pass

    def setup(self):
        """Register resources and load data."""
        self._install_signal_handlers_once()

        if self.protocol == "http":
            ok, aei = ensure_http_registration(
                self.meta["ae"], self.meta["cnt"], self.meta["api"], do_register=(self.registration == 1)
            )
            if self.registration == 1 and not ok:
                print("[ERROR] HTTP registration failed.")
                raise SystemExit(1)
            self.aei = aei or f"{self.meta['ae']}-{uuid.uuid4().hex[:8]}"
        else:
            tmp_origin = f"{self.meta['ae']}-{uuid.uuid4().hex[:8]}"
            broker_host = _env_or_config("MQTT_HOST", config.MQTT_HOST)
            broker_port = _env_or_config_int("MQTT_PORT", config.MQTT_PORT)
            self.mqtt = MqttOneM2MClient(
                broker_host,
                broker_port,
                tmp_origin,
                config.CSE_ID,
                config.CSE_RN,
            )
            if not self.mqtt.connect():
                print("[ERROR] MQTT broker connect failed. terminate.")
                raise SystemExit(1)

            if self.registration == 1:
                print(f"[MQTT] AE create -> {self.meta['ae']}")
                ok, aei = self.mqtt.create_ae(self.meta["ae"], self.meta["api"])
                if not ok or not aei:
                    print("[ERROR] MQTT AE create failed.")
                    raise SystemExit(1)
                self.mqtt.set_origin(aei)
                print(f"[MQTT] CNT create -> {self.meta['ae']}/{self.meta['cnt']}")
                if not self.mqtt.create_cnt(self.meta["ae"], self.meta["cnt"]):
                    print("[ERROR] MQTT CNT create failed.")
                    raise SystemExit(1)

        if self.mode == "csv":
            path = self.meta.get("csv")
            if not path:
                print(f"[{self.sensor_name.upper()}][ERROR] CSV path not configured in config for '{self.sensor_name}'.")
                raise SystemExit(1)
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    if not reader.fieldnames or "value" not in [h.strip() for h in reader.fieldnames]:
                        print(f"[{self.sensor_name.upper()}][ERROR] CSV header must include 'value' column.")
                        raise SystemExit(1)
                    self.csv_data = []
                    for row in reader:
                        try:
                            v = str(row.get("value", "")).strip()
                        except Exception:
                            v = ""
                        if v != "":
                            self.csv_data.append(v)
            except Exception as e:
                print(f"[{self.sensor_name.upper()}][ERROR] CSV open/parse failed: {e}")
                raise SystemExit(1)
            if not self.csv_data:
                print(f"[{self.sensor_name.upper()}][ERROR] CSV has no 'value' data.")
                raise SystemExit(1)

    def stop(self):
        self.stop_flag.set()

    def _next_value(self) -> str:
        """Generate next sensor reading value."""
        if self.mode == "csv":
            v = self.csv_data[self.csv_index]
            self.csv_index += 1
            return v

        profile = self.meta["profile"]
        dt = profile.get("data_type", "float")
        if dt == "int":
            return str(random.randint(int(profile.get("min", 0)), int(profile.get("max", 100))))
        if dt == "float":
            return f"{random.uniform(float(profile.get('min', 0.0)), float(profile.get('max', 100.0))):.2f}"
        if dt == "string":
            length = int(profile.get("length", 8))
            return "".join(random.choices(string.ascii_letters + string.digits, k=length))
        return "0"

    def run(self):
        """Main sensor loop sending readings at configured frequency."""
        print(f"[{self.sensor_name.upper()}] run (protocol={self.protocol}, mode={self.mode}, period={self.period_sec}s)")
        next_send = time.time() + self.period_sec
        try:
            while not self.stop_flag.is_set():
                remaining = next_send - time.time()
                while remaining > 0 and not self.stop_flag.is_set():
                    sleep_slice = remaining if remaining < 0.1 else 0.1
                    time.sleep(sleep_slice)
                    remaining = next_send - time.time()
                if self.stop_flag.is_set():
                    break

                if self.mode == "csv" and self.csv_index >= len(self.csv_data):
                    print(f"[{self.sensor_name.upper()}] CSV done. stop.")
                    break

                value = self._next_value()

                if self.protocol == "http":
                    ok = send_cin_http(self.meta["ae"], self.meta["cnt"], value, origin_aei=self.aei)
                else:
                    ok = self.mqtt.send_cin(self.meta["ae"], self.meta["cnt"], value)

                if not ok:
                    print(f"[{self.sensor_name.upper()}][ERROR] send failed: {value}. exiting.")
                    raise SystemExit(1)

                print(f"[{self.sensor_name.upper()}] Sent value: {value}\n")

                if self.mode == "csv" and self.csv_index >= len(self.csv_data):
                    print(f"[{self.sensor_name.upper()}] CSV done. stop.")
                    break

                next_send += self.period_sec
        finally:
            if self.mqtt:
                try:
                    self.mqtt.disconnect()
                except Exception:
                    pass

            try:
                HTTP.close()
            except Exception:
                pass


# Command-line argument parser

def parse_args(argv):
    p = argparse.ArgumentParser(description="Single-sensor oneM2M simulator (HTTP/MQTT).")
    p.add_argument("--sensor", required=True)
    p.add_argument("--protocol", choices=["http", "mqtt"], required=True)
    p.add_argument("--mode", choices=["csv", "random"], required=True)
    p.add_argument("--frequency", type=float, required=True)
    p.add_argument("--registration", type=int, choices=[0, 1], required=True)

    p.add_argument("--base-url", required=True, help="Base URL with CSE RN (e.g., http://127.0.0.1:3000/TinyIoT)")
    p.add_argument("--csv-path", help="CSV file path (required when --mode csv)")
    p.add_argument("--cse-id", help="CSE-ID for MQTT topic (required when --protocol mqtt)")
    p.add_argument("--mqtt-port", type=int, help="MQTT broker port (required when --protocol mqtt)")

    return p.parse_args(argv)


# Main entry point

def main():
    load_dotenv()  # Load environment variables from .env file
    args = parse_args(sys.argv[1:])

    if not args.sensor or not args.sensor.strip():
        print("[ERROR] --sensor cannot be empty")
        sys.exit(1)

    if args.frequency <= 0:
        print(f"[ERROR] --frequency must be positive, got {args.frequency}")
        sys.exit(1)

    parsed_url = urlparse(args.base_url)
    if not parsed_url.hostname:
        print(f"[ERROR] Invalid --base-url: hostname not found in '{args.base_url}'")
        sys.exit(1)
    if not parsed_url.port:
        print(f"[ERROR] Invalid --base-url: port not specified in '{args.base_url}' (e.g., http://127.0.0.1:3000/TinyIoT)")
        sys.exit(1)
    if not parsed_url.path or parsed_url.path == '/':
        print(f"[ERROR] Invalid --base-url: CSE RN not specified in path (e.g., http://127.0.0.1:3000/TinyIoT)")
        sys.exit(1)

    config.HTTP_HOST = parsed_url.hostname
    config.HTTP_PORT = parsed_url.port
    config.CSE_RN = parsed_url.path.lstrip('/')
    config.HTTP_BASE = f"{parsed_url.scheme}://{parsed_url.hostname}:{parsed_url.port}"
    config.BASE_URL = args.base_url.rstrip('/')

    if args.mode == "csv":
        if not args.csv_path:
            print("[ERROR] --csv-path is required when --mode csv")
            sys.exit(1)
        if not os.path.isfile(args.csv_path):
            print(f"[ERROR] CSV file not found: {args.csv_path}")
            sys.exit(1)
        config.CSV_PATH = args.csv_path

    if args.protocol == "mqtt":
        if not args.mqtt_port:
            print("[ERROR] --mqtt-port is required when --protocol mqtt")
            sys.exit(1)
        if not args.cse_id:
            print("[ERROR] --cse-id is required when --protocol mqtt")
            sys.exit(1)
        config.MQTT_HOST = config.HTTP_HOST
        config.MQTT_PORT = args.mqtt_port
        config.CSE_ID = args.cse_id

    if os.getenv("SKIP_HEALTHCHECK", "0") != "1":
        if not check_server_health():
            sys.exit(1)

    worker = SensorWorker(
        sensor_name=args.sensor,
        protocol=args.protocol,
        mode=args.mode,
        period_sec=args.frequency,
        registration=args.registration,
    )
    worker._install_signal_handlers_once()
    worker.setup()
    try:
        worker.run()
        if os.getenv("SKIP_HEALTHCHECK", "0") != "1":
            print(f"\n[{args.sensor.upper()}] Sensor simulator terminated.")
    except KeyboardInterrupt:
        worker.stop()
        print(f"\n\n[{args.sensor.upper()}] Interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
