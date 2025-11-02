# smart_farm_1

## Overview

This project provides:

- **Sensor Simulator**: Sends user-selected data—temperature, humidity, CO₂, soil, etc.—to the tinyIoT server.
- **Coordinator Script**: Controls and manages multi-sensor data transmission via the simulator, including all related processes.
- **Smart Farm CSV Data**: About 1,000 real smart-farm data points (temperature, humidity, CO₂, soil) collected from September 24–27, 2025.

The simulator complies with the **oneM2M standard** and supports both **HTTP** and **MQTT** protocols.

## Purpose

This project aims to run a multi-sensor simulation using real temperature, humidity, CO₂, and soil data by leveraging a coordinator that drives a simulator, then transmits the data to tinyIoT over user-selected protocols such as HTTP or MQTT.

## Directory Structure

```
smart_farm_1/
├── coordinator.py          # Main script to control tinyIoT server and simulators
├── simulator.py            # Single-sensor simulator
├── config_coord.py         # Settings for coordinator.py
├── config_sim.py           # Settings for simulator.py
├── device_profile.dat      # Sensor profile configuration
├── requirements.txt        # Python dependencies
├── smartfarm_data/         # Smart farm sensor CSV files
│   ├── temperature_data.csv
│   ├── humidity_data.csv
│   ├── co2_data.csv
│   └── soil_data.csv
└── README.md
```

## Key Features

- **Unified Control**: Manage all simulators at once through `coordinator.py`.
- **4 Sensor Types**: Temperature, humidity, CO₂, soil.
- **Protocol Selection**: Choose between **HTTP** and **MQTT**.
- **Operation Modes**
    - `csv`: Sequentially send real smart-farm data from CSV files
    - `random`: Generate random data within configured ranges
- **Auto Registration**: Automatically create oneM2M AE/CNT resources with the `--registration` option.
- **Profile-Based Settings**: Manage per-sensor settings via `device_profile.dat`.

## Environment

- **OS**: Ubuntu 24.04.2 LTS
- **Language**: Python 3.8+
- **IoT Platform**: tinyIoT
- **MQTT Broker**: Mosquitto

## Installation

### 1. Set Up the tinyIoT Server

- Open the URL below and follow the README to set up the environment for tinyIoT.

```bash
https://github.com/seslabSJU/tinyIoT
```

- Configure `config.h` (modify IP and port to match your environment)

```c
// #define NIC_NAME "eth0"
#define SERVER_IP "127.0.0.1"
#define SERVER_PORT "3000"
#define CSE_BASE_NAME "TinyIoT"
#define CSE_BASE_RI "tinyiot"
#define CSE_BASE_SP_ID "tinyiot.example.com"
#define CSE_RVI RVI_2a
```

- Enable MQTT: uncomment `#define ENABLE_MQTT`

```c
// To enable MQTT, de-comment the following line
#define ENABLE_MQTT

#ifdef ENABLE_MQTT
#define MQTT_HOST "127.0.0.1"
#define MQTT_QOS MQTT_QOS_0
#define MQTT_KEEP_ALIVE_SEC 60
#define MQTT_CMD_TIMEOUT_MS 30000
#define MQTT_CON_TIMEOUT_MS 5000
#define MQTT_CLIENT_ID "TinyIoT"
#define MQTT_USERNAME "test"
#define MQTT_PASSWORD "mqtt"
#endif
```

### 2. Configure the MQTT Broker (Mosquitto)

1. Install Mosquitto

    - Visit:

    ```bash
    https://mosquitto.org
    ```

    - Click **Download** on the site.

    - On Windows, install `mosquitto-2.0.22-install-windows-x64.exe`.

2. Edit mosquitto.conf

    - Open the `mosquitto.conf` file.

    - Add `listener 1883` and `protocol mqtt`.

    ```bash
    # listener port-number [ip address/host name/unix socket path]
    listener 1883
    protocol mqtt
    ```

    - Uncomment `allow_anonymous false`. (If it doesn't exist, add it.)

    ```bash
    # the local machine.
    allow_anonymous false
    ```

    - Then change `false` to `true`.

    ```bash
    # the local machine.
    allow_anonymous true
    ```

    For this project, we proceed with anonymous access enabled. If you intend to use MQTT authentication, do not change it to `true`—keep it as `false`.

    If you use authentication, fill your values in `.env`. If you use anonymous access, you don't need to fill `.env`.

    ```bash
    MQTT_USER=
    MQTT_PASS=
    ```

3. Run Mosquitto (performed from WSL via Windows PowerShell)

    - Start mosquitto:

    ```bash
    sudo systemctl start mosquitto
    ```

    - Check if mosquitto is running:

    ```bash
    sudo systemctl status mosquitto
    ```

    - Successful status example:

    ```bash
    ● mosquitto.service - Mosquitto MQTT Broker
         Loaded: loaded (/usr/lib/systemd/system/mosquitto.service; enabled; preset: enabled)
         Active: active (running) since ...
    ```

    **If you see `active (running)`, it's working.**

### 3. Clone the Project

Clone this project. Choose a path that won't conflict with an existing tinyIoT directory.

Enter the following in Ubuntu in order:

```bash
cd {path}

git clone --filter=blob:none --no-checkout --branch dev --single-branch https://github.com/seslabSJU/tinyIoT.git

cd tinyIoT

git sparse-checkout init --cone

git sparse-checkout set demo/smart_farm_1

git checkout
```

### 4. Modify Configuration Files

#### 4.1 device_profile.dat (Sensor Profile Settings)

When you run the coordinator, sensor options are managed here.

This project leverages real smart-farm data, so users typically only need to modify `protocol` and `registration`, aside from sensor type and mode, before running.

**Format:**

```bash
sensor_type,protocol,mode,period(seconds),registration
```

**Default settings:**

```bash
temperature,http,csv,3,1
humidity,http,csv,3,1
co2,mqtt,csv,3,1
soil,mqtt,csv,3,1
```

- **sensor_type**: temperature, humidity, co2, soil, etc.
- **protocol**: http or mqtt
- **mode**: csv (use CSV file) or random (generate random data)
- **period**: data transmission interval in seconds
- **registration**: 1 (auto-create AE/CNT) or 0 (skip)

## Parameter Reference

### Parameters for coordinator.py

#### Required

| Parameter | Description |
|-----------|-------------|
| `--base-url` | Base URL of the tinyIoT server (http://host:port/CSE_RN) |
| `--simulator-path` | Path to simulator.py (relative or absolute) |

#### Optional

| Parameter | Description | When Needed |
|-----------|-------------|-------------|
| `--server-path` | Path to tinyIoT server executable | When the server is not running |
| `--csv-base-path` | Directory path containing CSV data files | When using csv mode |
| `--cse-id` | CSE identifier used for MQTT topics | Required when protocol is mqtt |
| `--mqtt-port` | Port number of the MQTT broker | Required when protocol is mqtt |
| `--debug` | Enable debug mode (verbose logging) | For troubleshooting |


## How to Run

### Multi-Sensor Simulation Using the Coordinator (SmartFarm Project)

**Activate a virtual environment and install Python libraries**

Navigate to the project path:

```bash
cd path/tinyIoT/demo/smart_farm_1  # example path

python3 -m venv .venv

source .venv/bin/activate

python -m pip install --upgrade pip  # recommended (upgrade pip inside venv)

pip install -r requirements.txt
```

**Run the project**

Refer to **Parameter Reference** above and supply parameters appropriate for your environment.

Provide only the parameters required based on the options defined in `device_profile.dat`.

 - When the tinyIoT server is already running:

    ```bash
    python3 coordinator.py \
        --base-url {URL}/{CSE_RN} \
        --simulator-path simulator.py \
        --csv-base-path smartfarm_data \
        --cse-id {CSE_ID} \
        --mqtt-port {PORT}
    ```


  - When the tinyIoT server is not running:

    ```bash
    python3 coordinator.py \
        --server-path {SERVER_PATH} \
        --base-url {URL}/{CSE_RN} \
        --simulator-path simulator.py \
        --csv-base-path smartfarm_data \
        --cse-id {CSE_ID} \
        --mqtt-port {PORT}
    ```


 **Running example**
    
    ```bash
    python3 coordinator.py \
        --base-url http://127.0.0.1:TinyIoT \
        --simulator-path ./simulator.py \
        --csv-base-path ./smartfarm_data \
        --cse-id tinyiot \
        --mqtt-port 1883
    ```


### How to Stop

Press `Ctrl+C` in the running terminal to gracefully stop all simulators and the server.

## CSV Data Format

CSV files in the `smartfarm_data/` directory follow this format:

```csv
timestamp,value,source_table
20250924T000746,24.0,cnt:Temperature
20250924T000811,24.5,cnt:Temperature
```

- **timestamp**: Data collection time (ISO 8601 format)
- **value**: Sensor reading
- **source_table**: Origin table info

Each CSV file contains about 1,000 real smart-farm data points.

## Bonus: Run Individual Simulators

You can also run an individual sensor directly:
```bash
# Temperature sensor (HTTP, CSV mode)
python3 simulator.py \
    --sensor {SENSOR} \
    --protocol {http OR mqtt} \
    --mode {csv OR random} \
    --frequency {seconds} \
    --registration {1 OR 0} \
    --base-url {URL}/{CSE_RN} \
    --csv-path {CSV_PATH}

# CO2 sensor (MQTT, random mode)
python3 simulator.py \
    --sensor {SENSOR} \
    --protocol {http OR mqtt} \
    --mode {csv OR random} \
    --frequency {seconds} \
    --registration {1 OR 0} \
    --base-url {URL}/{CSE_RN} \
    --cse-id {CSE_ID} \
    --mqtt-port {PORT}
```

### Parameters for simulator.py

#### Required

| Parameter | Description | Allowed Values |
|-----------|-------------|----------------|
| `--sensor` | Sensor type to simulate | temperature, humidity, co2, soil, etc. |
| `--protocol` | Data transmission protocol | http, mqtt |
| `--mode` | Data generation method | csv (read from file), random (generate) |
| `--frequency` | Data transmission interval (seconds) | positive integer |
| `--registration` | Auto-register AE/CNT resources | 0 (skip), 1 (register) |
| `--base-url` | Base URL of tinyIoT server | http://host:port/CSE_RN |

#### Conditionally Required

| Parameter | Description | When Needed |
|-----------|-------------|-------------|
| `--csv-path` | Path to the CSV data file | Required when --mode csv |
| `--cse-id` | CSE identifier for MQTT topics | Required when --protocol mqtt |
| `--mqtt-port` | MQTT broker port | Required when --protocol mqtt |











