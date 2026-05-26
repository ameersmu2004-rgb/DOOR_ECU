# ◈ DOOR ECU Simulator

A futuristic, holographic-HUD style Door ECU Simulator built on the
UDS/CAN diagnostic framework. Features a live animated automotive door control
system with real-time ECU state simulation.

## Quick Start

```bash
cd DOOR_ECU
chmod +x run.sh
./run.sh
```

The script will auto-install missing dependencies and set up vcan0.

## Manual Run

```bash
# Install deps
pip3 install python-can --break-system-packages

# Setup vcan (optional — for real CAN tester tools)
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0

# Launch
python3 main_door.py
```

## Features

### 🖥 Futuristic Door ECU Dashboard
- Animated door lock / unlock controls
- Live window up/down simulation
- Child lock indicator
- Mirror fold / unfold animation
- Interior light status indicators
- Real-time ECU state updates

### 📊 Live Telemetry Indicators
- Door Lock Status
- Window Position
- Child Lock State
- Mirror Position
- Cabin Light Status
- ECU Power State

### ⚠ Active Warning Indicators
- DOOR OPEN
- CHILD LOCK ACTIVE
- WINDOW JAM WARNING
- LOW BATTERY
- COMMUNICATION ERROR

### 🔬 UDS Diagnostic Support
All standard UDS services supported:

| SID  | Service |
|------|---------|
| 0x10 | DiagnosticSessionControl (Default / Extended / Programming) |
| 0x11 | ECUReset |
| 0x27 | SecurityAccess (seed/key challenge) |
| 0x22 | ReadDataByIdentifier |
| 0x2E | WriteDataByIdentifier |
| 0x3E | TesterPresent |
| 0x19 | ReadDTCInformation |
| 0x14 | ClearDTC |
| 0x28 | CommunicationControl |

### 📋 DIDs Supported

| DID    | Description |
|--------|-------------|
| 0xF190 | Door Lock Status |
| 0xF191 | Window Position |
| 0xF192 | Child Lock State |
| 0xF193 | Mirror Position |
| 0xF194 | Cabin Light State |
| 0xF186 | Active Session |
| 0xF187 | Part Number |
| 0xF189 | SW Version |

### 🔴 Oracle / Vulnerability Engine

Load the included `door_vulns.json` (or any compatible JSON) to arm
the vulnerability engine:

- **DOOR-001** — Door lock state overflow
- **DOOR-002** — Child lock bypass
- **DOOR-003** — Rapid ReadDID DoS attack
- **DOOR-004** — Programming session bypass
- **DOOR-005** — Weak security seed challenge
- **DOOR-006** — Unauthorized window control write

### 📁 Log Panels
- **UDS Diagnostic Log** — All UDS requests / responses
- **Raw Frame Log** — CAN frames (when vcan0 is active)
- **Oracle / Vuln Log** — Vulnerability detections and oracle events

### 💾 JSON Load / Unload
Hot-swap vulnerability profiles without restarting the ECU.

### 📤 Export Log
Save the session log to `.log` or `.jsonl` files.

## Testing with cantools / isotp

```bash
# Install tools
pip3 install can-isotp python-can

# Send a ReadDataByIdentifier for Door Lock Status
python3 -c "
import can, isotp, time
bus = can.Bus('vcan0', bustype='socketcan')
addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E0, rxid=0x7E8)
stack = isotp.CanStack(bus, address=addr)
stack.send(bytes([0x22, 0xF1, 0x90]))
time.sleep(0.2)
stack.process()
if stack.available():
    print('Response:', stack.recv().hex().upper())
bus.shutdown()
"
```

## File Structure

```text
DOOR_ECU/
├── main_door.py            ← Entry point
├── door_gui.py             ← Futuristic GUI
├── door_ecu.py             ← UDS service handler
├── door_state.py           ← ECU state simulation
├── door_vulns.json         ← Vulnerability profiles
├── run.sh                  ← One-click launcher
│
│   (Shared ECU framework:)
├── uds_core.py             ← UDS protocol core
├── uds_constants.py        ← SID / NRC constants
├── uds_helpers.py          ← Hex formatting helpers
├── isotp_server.py         ← ISO-TP transport layer
├── vulnerability_engine.py ← Vulnerability evaluation engine
├── vulnerability_config.py ← JSON config loader
├── logger.py               ← Structured logger
├── ecu_memory.py           ← Virtual memory / NVM
└── logs/                   ← Auto-created log directory
```
