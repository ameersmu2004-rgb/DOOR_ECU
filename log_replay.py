#!/usr/bin/env python3
# log_replay.py
#
# ECU Simulation Log Replay Tool
# --------------------------------
# Reads logs/ecu_simulation.jsonl and replays UDS messages in original order.
# Use --dry-run to print reproduction steps without sending any frames.
#
# Usage:
#   python log_replay.py                          # replay everything
#   python log_replay.py --dry-run                # print steps only
#   python log_replay.py --filter-vuln VULN-001   # replay up to first VULN-001 trigger
#   python log_replay.py --log path/to/file.jsonl # custom log path
#   python log_replay.py --delay 0.1              # 100 ms between frames
#
# Example output (--dry-run):
#   ════════════════════════════════════════════════
#   REPRODUCTION STEPS FOR ECU FAILURE
#   ════════════════════════════════════════════════
#   Step 1 [2024-01-15T10:23:01.042]
#     SID     : 0x10 (DiagnosticSessionControl)
#     Payload : 1003
#     Pre-State: Session=DEFAULT Security=0 Faulted=False
#     CAN cmd : cansend vcan0 7E0#1003
#   ...

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _load_events(jsonl_path: str) -> List[Dict[str, Any]]:
    """Parse JSONL log and extract all inbound UDS request events."""
    events: List[Dict[str, Any]] = []
    path = Path(jsonl_path)
    if not path.exists():
        print(f"[REPLAY][ERROR] Log file not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"[REPLAY][WARN] Skipping malformed line {lineno}: {exc}", file=sys.stderr)
                continue

            uds = rec.get("uds_payload")
            if not uds:
                continue
            # Only replay inbound (RX) frames
            if uds.get("direction", "RX") == "TX":
                continue

            events.append({
                "timestamp": rec.get("timestamp", "??"),
                "hex":       uds.get("hex", ""),
                "sid":       uds.get("sid", "??"),
                "sid_name":  uds.get("sid_name", ""),
                "pre_state": rec.get("ecu_context", {}).get("pre_state", {}),
                "vuln_info": rec.get("vuln_info"),           # None if not a vuln event
                "failure":   rec.get("failure_info"),         # None if not a failure
            })

    return events


def _load_vuln_events(jsonl_path: str) -> List[Dict[str, Any]]:
    """Return only entries that contain vulnerability trigger data."""
    path = Path(jsonl_path)
    vulns: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "vuln_info" in rec:
                vulns.append(rec)
    return vulns


def _load_failure_events(jsonl_path: str) -> List[Dict[str, Any]]:
    """Return only entries that contain failure/crash data."""
    path = Path(jsonl_path)
    failures: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "failure_info" in rec:
                failures.append(rec)
    return failures


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _filter_up_to_vuln(events: List[Dict], vuln_id: str) -> List[Dict]:
    """
    Return the subsequence of events that led up to the first trigger
    of *vuln_id*.  Includes the triggering event itself.
    """
    result: List[Dict] = []
    vuln_path = Path(events[0].get("timestamp", "")) if events else Path("")
    # We need the full JSONL to find the vuln trigger timestamp
    # Just return events that occurred before or at the first vuln trigger
    # Since events list only has RX frames, we mark the cut-off by index
    return events   # caller handles via --filter-vuln against vuln log


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------

SEP = "═" * 68

def _print_report(events: List[Dict], vuln_events: List[Dict],
                  failure_events: List[Dict]) -> None:
    print(SEP)
    print("  ECU SIMULATION — FAILURE REPRODUCTION REPORT")
    print(SEP)

    # ── Failures ────────────────────────────────────────────────────────
    if failure_events:
        print("\n[FAILURES DETECTED]")
        for i, f_rec in enumerate(failure_events, 1):
            fi = f_rec.get("failure_info", {})
            print(f"\n  Failure #{i}  [{f_rec.get('timestamp','??')}]")
            print(f"    Type        : {fi.get('failure_type','?')}")
            print(f"    Description : {fi.get('description','?')}")
            print(f"    Payload     : {fi.get('input_payload','?')}")
            st = fi.get("ecu_state", {})
            if st:
                print(f"    ECU State   : Session={st.get('session','?')} "
                      f"SecLvl={st.get('security_level','?')} "
                      f"Faulted={st.get('faulted','?')}")
            steps = fi.get("reproduce_steps", [])
            if steps:
                print("    Reproduce   :")
                for step in steps:
                    print(f"      {step}")
    else:
        print("\n[No failures recorded in this log]")

    # ── Vulnerabilities ──────────────────────────────────────────────────
    if vuln_events:
        print(f"\n[VULNERABILITIES TRIGGERED — {len(vuln_events)} event(s)]")
        for i, v_rec in enumerate(vuln_events, 1):
            vi = v_rec.get("vuln_info", {})
            print(f"\n  Trigger #{i}  [{v_rec.get('timestamp','??')}]")
            print(f"    ID          : {vi.get('id','?')} — {vi.get('name','?')}")
            print(f"    Action      : {vi.get('action','?')}")
            print(f"    Module      : {vi.get('module','?')}")
            print(f"    Payload     : {vi.get('input_payload','?')}")
            print(f"    Condition   : {vi.get('trigger',{})}")
            print(f"    Message     : {vi.get('log_message','?')}")
            print(f"    Reproduce   : {vi.get('reproduce','?')}")
    else:
        print("\n[No vulnerability triggers recorded in this log]")

    # ── Replay steps ─────────────────────────────────────────────────────
    print(f"\n[UDS MESSAGE REPLAY SEQUENCE — {len(events)} request(s)]")
    print(SEP)
    for i, ev in enumerate(events, 1):
        ps = ev.get("pre_state", {})
        print(f"\nStep {i:>3}  [{ev['timestamp']}]")
        print(f"  SID       : {ev['sid']} ({ev['sid_name']})")
        print(f"  Payload   : {ev['hex']}")
        if ps:
            print(f"  Pre-State : Session={ps.get('session','?')} "
                  f"SecLvl={ps.get('security_level','?')} "
                  f"Faulted={ps.get('faulted','?')}")
        print(f"  CAN cmd   : cansend vcan0 7E0#{ev['hex']}")


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def _send_events(events: List[Dict], interface: str, delay: float) -> None:
    print(f"\n[REPLAY] Sending {len(events)} frames on {interface} "
          f"with {delay*1000:.0f} ms delay ...")
    print(SEP)
    for i, ev in enumerate(events, 1):
        hex_data = ev["hex"]
        cmd = ["cansend", interface, f"7E0#{hex_data}"]
        print(f"  [{i:>3}] {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"         ERROR: {result.stderr.strip()}", file=sys.stderr)
        time.sleep(delay)
    print("[REPLAY] Replay complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ECU Simulation Log Replay Tool — reproduce failures from log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python log_replay.py --dry-run
  python log_replay.py --filter-vuln VULN-001 --dry-run
  python log_replay.py --delay 0.1 --interface vcan0
        """,
    )
    parser.add_argument(
        "--log", default="logs/ecu_simulation.jsonl",
        help="Path to JSONL log file (default: logs/ecu_simulation.jsonl)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print reproduction steps without sending any CAN frames",
    )
    parser.add_argument(
        "--delay", type=float, default=0.05,
        help="Delay in seconds between replayed frames (default: 0.05)",
    )
    parser.add_argument(
        "--interface", default="vcan0",
        help="SocketCAN interface to send on (default: vcan0)",
    )
    parser.add_argument(
        "--filter-vuln", metavar="VULN_ID",
        help="Only show context around a specific vulnerability ID (e.g. VULN-001)",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Print only the failure/vulnerability summary, not the full step list",
    )
    args = parser.parse_args()

    events        = _load_events(args.log)
    vuln_events   = _load_vuln_events(args.log)
    failure_events = _load_failure_events(args.log)

    # Optional: narrow to a specific vulnerability
    if args.filter_vuln:
        vuln_id = args.filter_vuln.upper()
        vuln_events = [v for v in vuln_events
                       if v.get("vuln_info", {}).get("id", "").upper() == vuln_id]
        if not vuln_events:
            print(f"[REPLAY][WARN] No events found for vulnerability id '{vuln_id}'")
        else:
            # Keep only the events up to the first matching trigger timestamp
            cut_ts = vuln_events[0]["timestamp"]
            events = [e for e in events if e["timestamp"] <= cut_ts]

    if args.summary_only:
        events = []     # suppress step list

    _print_report(events, vuln_events, failure_events)

    if not args.dry_run and events:
        confirm = input("\n[REPLAY] Send these frames? [y/N] ").strip().lower()
        if confirm == "y":
            _send_events(events, args.interface, args.delay)
        else:
            print("[REPLAY] Aborted.")


if __name__ == "__main__":
    main()
