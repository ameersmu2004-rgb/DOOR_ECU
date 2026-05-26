# config.py

INTERFACE = "vcan0"

# Tester -> ECU (request)
ECU_RX_ID = 0x7E0
# ECU -> Tester (response)
ECU_TX_ID = 0x7E8

# Default JSON path (button can override)
VULN_JSON_PATH = "./vulnerabilities.json"

# Crash recovery time (seconds)
CRASH_RECOVERY_S = 5.0
