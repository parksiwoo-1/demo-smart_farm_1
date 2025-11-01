"""Configuration for simulator."""

# CSE Configuration
CSE_RN = None  # CSE Resource Name (from --base-url)
CSE_ID = None  # CSE-ID for MQTT topics (from --cse-id)

# HTTP Configuration
HTTP_HOST = None
HTTP_PORT = None
HTTP_BASE = None
BASE_URL = None

HTTP_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "X-M2M-Origin": "CAdmin",
    "X-M2M-RVI": "2a",
    "X-M2M-RI": "req",
}

HTTP_GET_HEADERS = {
    "Accept": "application/json",
    "X-M2M-Origin": "CAdmin",
    "X-M2M-RVI": "2a",
    "X-M2M-RI": "check",
}

HTTP_CONTENT_TYPE_MAP = {
    "ae": 2,
    "cnt": 3,
    "cin": 4,
}

# MQTT Configuration
MQTT_HOST = None
MQTT_PORT = None
MQTT_USER = None  # Optional: Set if broker requires authentication
MQTT_PASS = None  # Optional: Set if broker requires authentication

# CSV Data Configuration
CSV_PATH = None

# Sensor Resource Definitions
SENSOR_RESOURCES = {
    "temperature": {
        "ae": "CTemperatureSensor",
        "cnt": "temperature",
        "api": "N.temperature",
        "origin": "CTemperatureSensor",
    },
    "humidity": {
        "ae": "CHumiditySensor",
        "cnt": "humidity",
        "api": "N.humidity",
        "origin": "CHumiditySensor",
    },
    "co2": {
        "ae": "Cco2Sensor",
        "cnt": "co2",
        "api": "N.co2",
        "origin": "Cco2Sensor",
    },
    "soil": {
        "ae": "CsoilSensor",
        "cnt": "soil",
        "api": "N.soil",
        "origin": "CsoilSensor",
    },
}

GENERIC_SENSOR_TEMPLATE = {
    "ae": "C{sensor}Sensor",
    "cnt": "{sensor}",
    "api": "N.{sensor}",
    "origin": "C{sensor}Sensor",
}

# Random Data Generation Profiles
TEMPERATURE_PROFILE = {
    "data_type": "float",
    "min": 20.0,
    "max": 35.0,
}

HUMIDITY_PROFILE = {
    "data_type": "float",
    "min": 50.0,
    "max": 90.0,
}

CO2_PROFILE = {
    "data_type": "float",
    "min": 350.0,
    "max": 800.0,
}

SOIL_PROFILE = {
    "data_type": "float",
    "min": 20.0,
    "max": 60.0,
}

GENERIC_RANDOM_PROFILE = {
    "data_type": "float",
    "min": 0.0,
    "max": 100.0,
}

# Timeout and Connection Settings
CONNECT_TIMEOUT = 2
READ_TIMEOUT = 10
HTTP_REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

RETRY_WAIT_SECONDS = 5
SEND_ERROR_THRESHOLD = 5

MQTT_KEEPALIVE = 60
MQTT_CONNECT_WAIT = 10
MQTT_RESPONSE_TIMEOUT = 5

# Container Retention Limits
CNT_MNI = 1000
CNT_MBS = 10485760  # 10MB
