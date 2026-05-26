# doorlock_ecu.py — Door Lock Virtual ECU
# CAN ID: 0x19B  (FucyFuzz Demo → Door Lock Fuzz)

import time
import threading
import random

try:
    import can
    CAN_AVAILABLE = True
except ImportError:
    CAN_AVAILABLE = False

from doorlock_state import DoorLockState
from uds_constants import (
    SID_DIAGNOSTIC_SESSION_CONTROL, SID_ECU_RESET,
    SID_SECURITY_ACCESS, SID_TESTER_PRESENT,
    SID_READ_DATA_BY_IDENTIFIER  as SID_READ_DATA_BY_ID,
    SID_WRITE_DATA_BY_IDENTIFIER as SID_WRITE_DATA_BY_ID,
    NRC_SERVICE_NOT_SUPPORTED, NRC_CONDITIONS_NOT_CORRECT,
    NRC_REQUEST_OUT_OF_RANGE, NRC_SECURITY_ACCESS_DENIED,
    NRC_INVALID_KEY, NRC_EXCEEDED_NUMBER_OF_ATTEMPTS,
    NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED,
    NRC_SUBFUNCTION_NOT_SUPPORTED as NRC_SUB_FUNCTION_NOT_SUPPORTED,
)

SID_READ_DTC_INFORMATION  = 0x19
SID_CLEAR_DTC             = 0x14
SID_COMMUNICATION_CONTROL = 0x28
POS_RESP_OFFSET           = 0x40

SESSION_DEFAULT     = 0x01
SESSION_EXTENDED    = 0x03
SESSION_PROGRAMMING = 0x02

ECU_NAME   = "DLOCK-ECU-001"
ECU_SW_VER = "SW_1.1.4"
ECU_HW_VER = "HW_1.0.2"
ECU_PART   = "4F0-962-107"

try:
    from logger import ECULogger
    _elog = ECULogger("doorlock_ecu")
except ImportError:
    class _FakeLog:
        def __getattr__(self, _): return lambda *a, **k: None
    _elog = _FakeLog()


def nrc_name(n): return f"NRC_0x{n:02X}"


class DoorLockECU:
    """Door Lock ECU — UDS handler + lock simulation."""

    def __init__(self, log_cb, oracle_cb, raw_can_cb=None):
        self.log     = log_cb
        self.oracle  = oracle_cb
        self.raw_log = raw_can_cb or (lambda m: None)

        self.state   = DoorLockState()
        self.running = True

        self.cfg         = None
        self.vuln_engine = None

        self._sim_thread = threading.Thread(target=self._sim_loop, daemon=True)

    def start(self):
        self._sim_thread.start()
        self.log(f"[SYSTEM] {ECU_NAME} online — CAN ID 0x19B")
        self.log(f"[SYSTEM] SW: {ECU_SW_VER}  HW: {ECU_HW_VER}  Part: {ECU_PART}")

    def stop(self):
        self.running = False

    def _sim_loop(self):
        while self.running:
            self.state.tick()
            time.sleep(0.05)

    # ------------------------------------------------------------------
    def handle_request(self, data: bytes) -> bytes | None:
        if not data:
            return None
        sid = data[0]
        payload = data[1:]

        now = time.monotonic()
        if self.state.hang_until > now:
            return None
        if self.state.faulted and now < self.state.fault_until:
            return self._nrc(sid, NRC_CONDITIONS_NOT_CORRECT)

        self.state.fuzz_frame_count += 1
        self.state.last_fuzz_payload = data

        if sid == SID_DIAGNOSTIC_SESSION_CONTROL:
            return self._handle_session(payload)
        if sid == SID_ECU_RESET:
            return self._handle_reset(payload)
        if sid == SID_SECURITY_ACCESS:
            return self._handle_security(payload)
        if sid == SID_TESTER_PRESENT:
            return self._handle_tester_present(payload)
        if sid == SID_READ_DATA_BY_ID:
            return self._handle_read_did(payload)
        if sid == SID_WRITE_DATA_BY_ID:
            return self._handle_write_did(payload)
        if sid == SID_READ_DTC_INFORMATION:
            return self._handle_read_dtc(payload)
        if sid == SID_CLEAR_DTC:
            return self._handle_clear_dtc(payload)

        return self._nrc(sid, NRC_SERVICE_NOT_SUPPORTED)

    # ------------------------------------------------------------------
    def _handle_session(self, payload):
        sid = SID_DIAGNOSTIC_SESSION_CONTROL
        if not payload:
            return self._nrc(sid, NRC_CONDITIONS_NOT_CORRECT)
        sub = payload[0]
        if sub not in (SESSION_DEFAULT, SESSION_EXTENDED, SESSION_PROGRAMMING):
            return self._nrc(sid, NRC_SUB_FUNCTION_NOT_SUPPORTED)
        if sub == SESSION_PROGRAMMING and self.state.session != SESSION_EXTENDED:
            self.oracle("[VULN] DLOCK-004 — Programming session without Extended pre-condition")
        self.state.session = sub
        names = {0x01:"DEFAULT", 0x02:"PROGRAMMING", 0x03:"EXTENDED"}
        self.log(f"[RX] DiagSessionControl → {names.get(sub)}")
        return bytes([SID_DIAGNOSTIC_SESSION_CONTROL + POS_RESP_OFFSET, sub,
                      0x00, 0x32, 0x01, 0xF4])

    def _handle_reset(self, payload):
        sub = payload[0] if payload else 0x01
        self.log(f"[RX] ECUReset sub=0x{sub:02X}")
        self.state.reset_volatile()
        return bytes([SID_ECU_RESET + POS_RESP_OFFSET, sub])

    def _handle_security(self, payload):
        sid = SID_SECURITY_ACCESS
        if not payload:
            return self._nrc(sid, NRC_CONDITIONS_NOT_CORRECT)
        sub = payload[0]
        now = time.monotonic()
        if self.state.locked_until > now:
            return self._nrc(sid, NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED)

        if sub % 2 == 1:  # seed
            seed = 0xBEEF  # weak fixed seed
            self.state.last_seed_level = sub
            self.state.last_seed_value = seed
            self.oracle("[VULN] DLOCK-005 — Weak fixed seed 0xBEEF")
            self.log(f"[RX] SecurityAccess requestSeed → seed=0x{seed:04X}")
            return bytes([SID_SECURITY_ACCESS + POS_RESP_OFFSET, sub,
                          (seed >> 8) & 0xFF, seed & 0xFF])
        else:  # key
            key = int.from_bytes(payload[1:3], "big") if len(payload) >= 3 else 0
            expected = (self.state.last_seed_value ^ 0xFFFF) & 0xFFFF
            if key == expected:
                self.state.security_level = sub - 1
                self.state.auth_failures_ram = 0
                self.log(f"[RX] SecurityAccess — ACCEPTED L{sub-1}")
                return bytes([SID_SECURITY_ACCESS + POS_RESP_OFFSET, sub])
            else:
                self.state.auth_failures_ram += 1
                if self.state.auth_failures_ram >= self.state.max_attempts:
                    self.state.locked_until = now + self.state.required_delay_s
                    self.oracle("[WARN] DLOCK — SecurityAccess lockout triggered")
                    return self._nrc(sid, NRC_EXCEEDED_NUMBER_OF_ATTEMPTS)
                return self._nrc(sid, NRC_INVALID_KEY)

    def _handle_tester_present(self, payload):
        self.log("[RX] TesterPresent")
        return bytes([SID_TESTER_PRESENT + POS_RESP_OFFSET, 0x00])

    def _handle_read_did(self, payload):
        sid = SID_READ_DATA_BY_ID
        if len(payload) < 2:
            return self._nrc(sid, NRC_CONDITIONS_NOT_CORRECT)
        did  = int.from_bytes(payload[:2], "big")
        data = self.state.read_did(did)
        if data is None:
            return self._nrc(sid, NRC_REQUEST_OUT_OF_RANGE)
        self.log(f"[RX] ReadDID 0x{did:04X} → {data.hex().upper()}")
        return bytes([SID_READ_DATA_BY_ID + POS_RESP_OFFSET,
                      (did >> 8) & 0xFF, did & 0xFF]) + data

    def _handle_write_did(self, payload):
        sid = SID_WRITE_DATA_BY_ID
        if len(payload) < 3:
            return self._nrc(sid, NRC_CONDITIONS_NOT_CORRECT)
        did  = int.from_bytes(payload[:2], "big")
        data = payload[2:]

        # Critical vulnerability: door lock writable without security unlock
        if did in (self.state.DID_DOOR_FL, self.state.DID_DOOR_FR,
                   self.state.DID_DOOR_RL, self.state.DID_DOOR_RR):
            if self.state.security_level == 0:
                self.oracle(
                    f"[VULN] DLOCK-007 — Door 0x{did:04X} writeable without SecurityAccess! "
                    f"Physical access bypass possible.")

        ok = self.state.write_did(did, data)
        if not ok:
            return self._nrc(sid, NRC_REQUEST_OUT_OF_RANGE)
        door_names = {
            self.state.DID_DOOR_FL: "FL", self.state.DID_DOOR_FR: "FR",
            self.state.DID_DOOR_RL: "RL", self.state.DID_DOOR_RR: "RR",
            self.state.DID_TRUNK:   "TRUNK",
        }
        dname = door_names.get(did, f"0x{did:04X}")
        action = "UNLOCK" if data[0] else "LOCK"
        self.log(f"[RX] WriteDID {dname} → {action}")
        return bytes([SID_WRITE_DATA_BY_ID + POS_RESP_OFFSET,
                      (did >> 8) & 0xFF, did & 0xFF])

    def _handle_read_dtc(self, payload):
        self.log("[RX] ReadDTCInformation")
        if self.state.fault_code:
            return bytes([SID_READ_DTC_INFORMATION + POS_RESP_OFFSET, 0x02, 0x01,
                          0xF6, self.state.fault_code, 0x09])
        return bytes([SID_READ_DTC_INFORMATION + POS_RESP_OFFSET, 0x02, 0x00])

    def _handle_clear_dtc(self, payload):
        self.log("[RX] ClearDTC")
        self.state.fault_code = 0
        return bytes([SID_CLEAR_DTC + POS_RESP_OFFSET])

    def _nrc(self, sid, nrc):
        self.log(f"[TX] NegativeResponse SID=0x{sid:02X} NRC=0x{nrc:02X} ({nrc_name(nrc)})")
        return bytes([0x7F, sid, nrc])
