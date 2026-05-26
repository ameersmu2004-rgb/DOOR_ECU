#!/bin/bash
# run_doorlock.sh — Auto-setup and launch Door Lock ECU Simulator

set -e

echo "🔒 Door Lock ECU Simulator — CAN ID 0x19B"
echo "==========================================="

if ! command -v python3 &>/dev/null; then
    echo "[ERR] Python 3 not found."
    exit 1
fi

echo "[INFO] Checking dependencies..."
python3 -c "import tkinter" 2>/dev/null || sudo apt install -y python3-tk
python3 -c "import can" 2>/dev/null       || pip3 install python-can --break-system-packages
python3 -c "import isotp" 2>/dev/null     || pip3 install can-isotp --break-system-packages

echo "[INFO] Setting up vcan0..."
sudo modprobe vcan 2>/dev/null || true
if ! ip link show vcan0 &>/dev/null; then
    sudo ip link add dev vcan0 type vcan
fi
sudo ip link set up vcan0 2>/dev/null || true
echo "[INFO] vcan0 is UP"

for f in uds_constants.py uds_helpers.py logger.py isotp_server.py config.py \
          vulnerability_config.py vulnerability_engine.py ecu_state.py; do
    if [ ! -f "$f" ] && [ -f "../speedo-ecu/$f" ]; then
        cp "../speedo-ecu/$f" .
        echo "[INFO] Copied $f from speedo-ecu"
    fi
done

echo "[INFO] Launching Door Lock ECU..."
python3 main_doorlock.py
