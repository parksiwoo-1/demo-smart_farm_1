"""Configuration for coordinator."""

# Python Execution
PYTHON_EXEC = "python3"

# Server Health Check Headers
HEALTHCHECK_HEADERS = {
    "X-M2M-Origin": "CAdmin",
    "X-M2M-RVI": "2a",
    "X-M2M-RI": "healthcheck",
    "Accept": "application/json",
}

# Timeout Configuration
WAIT_SERVER_TIMEOUT = 10
REQUEST_TIMEOUT = 2

PROC_TERM_WAIT = 5.0
SERVER_TERM_WAIT = 5.0
JOIN_READER_TIMEOUT = 1.0
