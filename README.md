# ◈ Speedometer ECU Simulator

A futuristic, holographic-HUD style Speedometer ECU Simulator built on the
UDS/CAN diagnostic framework. Features a live animated speedometer dial with
real-time physics simulation.

## Quick Start

```bash
cd Speedometer-ECU-Simulator
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
python3 main_speedo.py
```

## Features

### 🖥 Futuristic Speedometer Dial
- Animated analogue needle with glow effect and zone-based colouring
- 0–300 km/h sweep with green / amber / red speed zones
- RPM arc ring on the outer edge
- Live digital speed readout in centre
- Gear indicator (N / 1–7 / R)
- Speed limit warning flash
- Smooth physics simulation: parked → accelerating → cruising → braking

### 📊 Mini Telemetry Gauges
- Fuel level (% with low-fuel warning)
- Coolant temperature (°C with overheat warning)
- Battery voltage (V with low-voltage warning)

### 🔢 Live Telemetry Badges
- Engine RPM
- Odometer (km)
- Trip distance (km)
- Battery voltage
- Ambient temperature
- Turn signal state

### ⚠ Active Warning Icons
- SPD LIM — speed limit exceeded
- ABS — anti-lock brakes active
- TCS — traction control active
- LOW FUEL — fuel < 15%
- OVERHEAT — coolant > 105°C

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
| DID    | Description       | Encoding |
|--------|-------------------|----------|
| 0xF40D | Vehicle Speed     | 3 bytes, value/100 = km/h |
| 0xF40C | Engine RPM        | 2 bytes OBD (raw/4 = rpm) |
| 0xF4A3 | Odometer          | 4 bytes (km) |
| 0xF4A4 | Gear Position     | 1 byte (0=N, 1-7=gear) |
| 0xF4A5 | Fuel Level        | 1 byte (%) |
| 0xF4A6 | Coolant Temp      | 1 byte (val-40 = °C) |
| 0xF4A7 | Trip Distance     | 3 bytes (val/10 = km) |
| 0xF4AB | Battery Voltage   | 2 bytes (mV) |
| 0xF4AC | Ambient Temp      | 1 byte (val-40 = °C) |
| 0xF186 | Active Session    | 1 byte |
| 0xF187 | Part Number       | ASCII |
| 0xF189 | SW Version        | ASCII |

### 🔴 Oracle / Vulnerability Engine
Load the included `speedometer_vulns.json` (or any compatible JSON) to arm
the vulnerability engine:

- **SPEEDO-001** — Speed write overflow (>3 bytes → crash)
- **SPEEDO-002** — Odometer magic byte bypass (0xC0 → skip write-protect)
- **SPEEDO-003** — Rapid ReadDID DoS (>80 Hz → MCU hang) *(disabled by default)*
- **SPEEDO-004** — Programming session without Extended pre-condition
- **SPEEDO-005** — Weak seed (constant 0xDEADBEEF)
- **SPEEDO-006** — Fuel level writeable in Default session

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

# Send a ReadDataByIdentifier for Vehicle Speed (0xF40D)
python3 -c "
import can, isotp, time
bus = can.Bus('vcan0', bustype='socketcan')
addr = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=0x7E0, rxid=0x7E8)
stack = isotp.CanStack(bus, address=addr)
stack.send(bytes([0x22, 0xF4, 0x0D]))
time.sleep(0.2)
stack.process()
if stack.available():
    print('Response:', stack.recv().hex().upper())
bus.shutdown()
"
```

## File Structure

```
Speedometer-ECU-Simulator/
├── main_speedo.py          ← Entry point
├── speedo_gui.py           ← Futuristic GUI (dial, gauges, logs)
├── speedo_ecu.py           ← UDS service handler
├── speedo_state.py         ← ECU state + physics simulation
├── speedometer_vulns.json  ← Speedometer-specific vulnerability profiles
├── run.sh                  ← One-click launcher
│
│   (Shared from original ECU framework:)
├── uds_core.py             ← UDS protocol core
├── uds_constants.py        ← SID / NRC constants
├── uds_helpers.py          ← Hex formatting helpers
├── isotp_server.py         ← ISO-TP transport layer
├── vulnerability_engine.py ← Vulnerability evaluation engine
├── vulnerability_config.py ← JSON config loader
├── logger.py               ← Structured logger (text + JSONL)
├── ecu_memory.py           ← Virtual memory / NVM
└── logs/                   ← Auto-created log directory
```
