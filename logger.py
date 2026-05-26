# logger.py
#
# Centralized logging system for the Virtual ECU Simulator.
#
# Outputs:
#   logs/ecu_simulation.log   — Human-readable structured text log
#   logs/ecu_simulation.jsonl — Machine-readable JSON Lines log (used by log_replay.py)
#
# Log levels:
#   DEBUG   → Internal processing details (state snapshots, CAN frames)
#   INFO    → Normal ECU operations (session changes, successful responses)
#   WARNING → Abnormal conditions (NRC responses, vulnerability triggers)
#   ERROR   → Failures, crashes, exceptions
#
# Usage:
#   from logger import ECULogger
#   _log = ECULogger("uds_core")
#   _log.info("Service 0x22 handled")
#   _log.log_state_snapshot(state)
#   _log.log_uds_request(payload, state)
#   _log.log_vulnerability(vuln_dict, payload)
#   _log.log_failure("BUFFER_OVERFLOW", "VIN write > 20 bytes", payload, state=state)
#   _log.log_exception("Unhandled crash", exc, payload, state)

import logging
import os
import sys
import json
import traceback
from datetime import datetime
from typing import Optional, Callable

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_DIR  = "logs"
LOG_FILE  = os.path.join(LOG_DIR, "ecu_simulation.log")
JSONL_FILE = os.path.join(LOG_DIR, "ecu_simulation.jsonl")

os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Human-readable text formatter
# ---------------------------------------------------------------------------
class _TextFormatter(logging.Formatter):
    _LEVEL = {
        "DEBUG":    "DBG",
        "INFO":     "INF",
        "WARNING":  "WRN",
        "ERROR":    "ERR",
        "CRITICAL": "CRT",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts      = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        lvl     = self._LEVEL.get(record.levelname, record.levelname)
        loc     = f"{record.filename}:{record.funcName}:{record.lineno}"
        line    = f"[{ts}] [{lvl}] [{record.name:<28}] [{loc}] {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ---------------------------------------------------------------------------
# Machine-readable JSONL handler
# ---------------------------------------------------------------------------
class _JSONLHandler(logging.Handler):
    """Appends one JSON object per line to the replay log."""

    def __init__(self, path: str):
        super().__init__()
        self._fh = open(path, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict = {
                "timestamp":  datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level":      record.levelname,
                "logger":     record.name,
                "module":     record.module,
                "function":   record.funcName,
                "line":       record.lineno,
                "message":    record.getMessage(),
            }
            # Attach optional structured payloads when present
            for key in ("ecu_context", "uds_payload", "vuln_info", "failure_info"):
                val = getattr(record, key, None)
                if val is not None:
                    entry[key] = val
            if record.exc_info:
                entry["exception"] = self.formatException(record.exc_info)
            self._fh.write(json.dumps(entry) + "\n")
            self._fh.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
        super().close()


# ---------------------------------------------------------------------------
# Root logger bootstrap  (called once at import time)
# ---------------------------------------------------------------------------
def _bootstrap() -> logging.Logger:
    root = logging.getLogger("ecu")
    if root.handlers:           # already initialised (e.g. reimport)
        return root

    root.setLevel(logging.DEBUG)
    root.propagate = False

    # Console — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_TextFormatter())
    root.addHandler(ch)

    # Text file — DEBUG and above
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_TextFormatter())
    root.addHandler(fh)

    # JSONL file — DEBUG and above
    jh = _JSONLHandler(JSONL_FILE)
    jh.setLevel(logging.DEBUG)
    root.addHandler(jh)

    root.info(
        "=" * 72 + f"\n  ECU Simulation logging started  —  "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" + "=" * 72
    )
    return root


_ROOT = _bootstrap()


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'ecu' namespace."""
    return logging.getLogger(f"ecu.{name}")


# ---------------------------------------------------------------------------
# ECULogger — domain-specific helper wrapping Python's logging.Logger
# ---------------------------------------------------------------------------
class ECULogger:
    """
    Structured logging helper for ECU components.

    All methods write to both the text log and the JSONL replay log.
    An optional *gui_callback* is called with a plain-text string so
    that the Tkinter console continues to receive messages.
    """

    SESSION_NAMES = {0x01: "DEFAULT", 0x02: "PROGRAMMING", 0x03: "EXTENDED"}

    def __init__(self, component: str, gui_callback: Optional[Callable[[str], None]] = None):
        self._logger   = get_logger(component)
        self._component = component
        self._gui_cb   = gui_callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _gui(self, msg: str) -> None:
        if self._gui_cb:
            try:
                self._gui_cb(msg)
            except Exception:
                pass

    @staticmethod
    def _session_name(raw: int) -> str:
        return ECULogger.SESSION_NAMES.get(raw, f"0x{raw:02X}")

    def _record_extra(self, record: logging.LogRecord, **fields) -> logging.LogRecord:
        for k, v in fields.items():
            setattr(record, k, v)
        return record

    # ------------------------------------------------------------------
    # Pass-through level methods  (also forward to GUI callback)
    # ------------------------------------------------------------------
    def debug(self, msg: str) -> None:
        self._logger.debug(msg, stacklevel=2)

    def info(self, msg: str) -> None:
        self._logger.info(msg, stacklevel=2)
        self._gui(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg, stacklevel=2)
        self._gui(f"[WARN] {msg}")

    def error(self, msg: str) -> None:
        self._logger.error(msg, stacklevel=2)
        self._gui(f"[ERR] {msg}")

    # ------------------------------------------------------------------
    # ECU state snapshot  (DEBUG)
    # ------------------------------------------------------------------
    def log_state_snapshot(self, state, extra: str = "") -> None:
        """Emit a full ECU state snapshot (called before each request)."""
        sess = self._session_name(state.session)
        sec  = f"UNLOCKED(lvl={state.security_level})" if state.security_level > 0 else "LOCKED"
        msg  = (
            f"[STATE] Session={sess} Security={sec} "
            f"Faulted={state.faulted}({state.fault_reason or '-'}) "
            f"HangUntil={state.hang_until:.2f}"
        )
        if extra:
            msg += f" | {extra}"

        context = {
            "session":         sess,
            "session_raw":     state.session,
            "security_level":  state.security_level,
            "faulted":         state.faulted,
            "fault_reason":    state.fault_reason,
            "auth_failures":   state.auth_failures_ram,
            "locked_until":    round(state.locked_until, 3),
            "hang_until":      round(state.hang_until, 3),
        }
        r = self._logger.makeRecord(
            self._logger.name, logging.DEBUG,
            __file__, 0, msg, [], None, "log_state_snapshot"
        )
        self._record_extra(r, ecu_context=context)
        self._logger.handle(r)

    # ------------------------------------------------------------------
    # UDS request / response  (INFO / WARNING)
    # ------------------------------------------------------------------
    def log_uds_request(self, payload: bytes, state=None) -> None:
        """Log an incoming UDS request; include pre-processing ECU state."""
        try:
            from uds_helpers import uds_sid_name
        except ImportError:
            def uds_sid_name(x): return f"SID(0x{x:02X})"

        sid      = payload[0] if payload else 0
        hex_pay  = payload.hex().upper()
        sid_name = uds_sid_name(sid)

        msg = (
            f"[RX] SID=0x{sid:02X}({sid_name}) "
            f"Payload={hex_pay} Len={len(payload)}"
        )
        if state:
            msg += (
                f" | PreState: Session={self._session_name(state.session)} "
                f"SecLvl={state.security_level} Faulted={state.faulted}"
            )

        context: dict = {}
        if state:
            context["pre_state"] = {
                "session":        self._session_name(state.session),
                "security_level": state.security_level,
                "faulted":        state.faulted,
            }

        r = self._logger.makeRecord(
            self._logger.name, logging.INFO,
            __file__, 0, msg, [], None, "log_uds_request"
        )
        self._record_extra(r,
            uds_payload={"hex": hex_pay, "sid": f"0x{sid:02X}",
                         "sid_name": sid_name, "length": len(payload),
                         "direction": "RX"},
            ecu_context=context or None,
        )
        self._logger.handle(r)
        self._gui(f"[RX][UDS] {hex_pay}")

    def log_uds_response(self, payload: bytes) -> None:
        """Log an outgoing UDS response; WARNING level for negative responses."""
        try:
            from uds_helpers import nrc_name
        except ImportError:
            def nrc_name(x): return f"NRC(0x{x:02X})"

        hex_pay = payload.hex().upper()
        if payload[0] == 0x7F and len(payload) >= 3:
            nrc  = payload[2]
            msg  = f"[TX] NEGATIVE SID=0x{payload[1]:02X} NRC=0x{nrc:02X}({nrc_name(nrc)}) {hex_pay}"
            lvl  = logging.WARNING
        else:
            msg  = f"[TX] POSITIVE {hex_pay}"
            lvl  = logging.INFO

        r = self._logger.makeRecord(
            self._logger.name, lvl,
            __file__, 0, msg, [], None, "log_uds_response"
        )
        self._record_extra(r, uds_payload={"hex": hex_pay, "direction": "TX"})
        self._logger.handle(r)
        self._gui(f"[TX][UDS] {hex_pay}")

    # ------------------------------------------------------------------
    # Vulnerability trigger  (WARNING)
    # ------------------------------------------------------------------
    def log_vulnerability(self, vuln: dict, payload: bytes, module: str = "") -> None:
        """
        Log a triggered vulnerability with full diagnostic context.

        Recorded fields (available in JSONL for replay):
          - vulnerability id, name, trigger condition
          - input payload (hex)
          - effect / action
          - triggering module
        """
        vid      = vuln.get("id", "UNKNOWN")
        name     = vuln.get("name", "Unnamed")
        effect   = vuln.get("effect", {})
        action   = effect.get("action", "NONE")
        log_msg  = effect.get("log_message", "")
        trigger  = vuln.get("trigger", {})
        hex_pay  = payload.hex().upper() if payload else ""
        mod      = module or self._component

        msg = (
            f"[VULN] {vid} '{name}' TRIGGERED | "
            f"Action={action} | Payload={hex_pay} | {log_msg}"
        )
        r = self._logger.makeRecord(
            self._logger.name, logging.WARNING,
            __file__, 0, msg, [], None, "log_vulnerability"
        )
        self._record_extra(r, vuln_info={
            "id":            vid,
            "name":          name,
            "action":        action,
            "trigger":       trigger,
            "log_message":   log_msg,
            "module":        mod,
            "input_payload": hex_pay,
            "reproduce":     f"Send payload hex: {hex_pay}",
        })
        self._logger.handle(r)
        self._gui(f"[VULN] {vid} {name} -> {action}")

    # ------------------------------------------------------------------
    # Failure / crash  (ERROR)
    # ------------------------------------------------------------------
    def log_failure(
        self,
        failure_type: str,
        description:  str,
        payload:      bytes = b"",
        exc_info=     None,
        state=        None,
    ) -> None:
        """
        Log an exact failure point.

        Recorded fields (for post-mortem / reproduction):
          - failure_type  e.g. BUFFER_OVERFLOW, INVALID_SESSION, DOS, EXCEPTION
          - description   human-readable cause
          - file/function/line  of the *caller* (stacklevel=2)
          - input payload (hex)
          - ECU state at time of failure
          - reproduction steps
        """
        hex_pay = payload.hex().upper() if payload else ""
        state_snap: dict = {}
        if state:
            state_snap = {
                "session":        self._session_name(state.session),
                "security_level": state.security_level,
                "faulted":        state.faulted,
                "fault_reason":   state.fault_reason,
            }

        reproduce_steps = []
        if state:
            sess = self._session_name(state.session)
            if sess != "DEFAULT":
                reproduce_steps.append(f"1. Enter {sess} session (0x10 sub={state.session:02X})")
            if state.security_level > 0:
                reproduce_steps.append(f"2. Unlock security level {state.security_level}")
        if hex_pay:
            reproduce_steps.append(f"{len(reproduce_steps)+1}. Send payload: cansend vcan0 7E0#{hex_pay}")
        if not reproduce_steps:
            reproduce_steps = ["See payload and state in this log entry"]

        msg = (
            f"[FAILURE] Type={failure_type} | {description} | "
            f"Payload={hex_pay}"
        )
        r = self._logger.makeRecord(
            self._logger.name, logging.ERROR,
            __file__, 0, msg, [], exc_info, "log_failure"
        )
        self._record_extra(r, failure_info={
            "failure_type":     failure_type,
            "description":      description,
            "input_payload":    hex_pay,
            "ecu_state":        state_snap,
            "reproduce_steps":  reproduce_steps,
        })
        self._logger.handle(r)
        self._gui(f"[FAILURE] {failure_type}: {description}")

    def log_exception(
        self,
        description: str,
        exc:         Exception,
        payload:     bytes = b"",
        state=       None,
    ) -> None:
        """Log an unhandled exception with full traceback."""
        self.log_failure(
            failure_type="EXCEPTION",
            description=f"{description} — {type(exc).__name__}: {exc}",
            payload=payload,
            exc_info=sys.exc_info(),
            state=state,
        )
