# uds_core.py

import time
import struct
import random

from ecu_state import ECUState
from uds_constants import *
from uds_helpers import nrc_name
from logger import ECULogger

_elog = ECULogger("uds_core")


class UDSCore:
    def __init__(self, state: ECUState, mem, nvm, log):
        self.state = state
        self.mem   = mem
        self.nvm   = nvm
        self.log   = log

        self.dids = {
            0xF190: ("VIN",            lambda: self.nvm.store["vin"],                              self._set_vin),
            0xF18C: ("SerialNumber",   lambda: self.nvm.store["serial"],                           self._set_serial),
            0xF187: ("BootCounter",    lambda: struct.pack(">I", self.nvm.store["boot_count"]),    None),
            0x0101: ("MagicBypassDID", lambda: b"\x00",                                            self._set_magic_did),
        }
        _elog.debug(f"[INIT] UDSCore initialised — {len(self.dids)} DIDs registered")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _set_magic_did(self, payload: bytes):
        self.mem.write(0x100, payload[:8])

    def negative(self, req_sid: int, nrc: int) -> bytes:
        _elog.debug(
            f"[NRC] SID=0x{req_sid:02X} NRC=0x{nrc:02X}({nrc_name(nrc)}) — "
            f"Session={ECULogger.SESSION_NAMES.get(self.state.session,'?')} "
            f"SecLvl={self.state.security_level}"
        )
        return bytes([0x7F, req_sid, nrc])

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def handle(self, payload: bytes):
        if not payload:
            return None

        sid = payload[0]

        handler = {
            SID_DIAGNOSTIC_SESSION_CONTROL: self.srv_10_session_control,
            SID_ECU_RESET:                  self.srv_11_ecu_reset,
            SID_READ_DATA_BY_IDENTIFIER:    self.srv_22_read_did,
            SID_WRITE_DATA_BY_IDENTIFIER:   self.srv_2e_write_did,
            SID_SECURITY_ACCESS:            self.srv_27_security_access,
            SID_READ_MEMORY_BY_ADDRESS:     self.srv_23_read_memory,
            SID_WRITE_MEMORY_BY_ADDRESS:    self.srv_3d_write_memory,
            SID_TESTER_PRESENT:             self.srv_3e_tester_present,
        }.get(sid)

        if not handler:
            _elog.warning(
                f"[DISPATCH] SID=0x{sid:02X} not supported — "
                f"returning NRC_SERVICE_NOT_SUPPORTED"
            )
            return self.negative(sid, NRC_SERVICE_NOT_SUPPORTED)

        _elog.debug(
            f"[DISPATCH] SID=0x{sid:02X} → {handler.__name__} | "
            f"payload={payload.hex().upper()}"
        )

        try:
            return handler(payload)
        except IndexError as exc:
            _elog.log_failure(
                failure_type="INCORRECT_MESSAGE_LENGTH",
                description=f"IndexError in {handler.__name__}: {exc}",
                payload=payload,
                exc_info=None,
                state=self.state,
            )
            return self.negative(sid, NRC_INCORRECT_MESSAGE_LENGTH)
        except Exception as exc:
            _elog.log_exception(
                f"Unexpected exception in {handler.__name__}",
                exc, payload=payload, state=self.state,
            )
            self.log(f"[UDS][ERR] {exc}")
            return self.negative(sid, NRC_GENERAL_REJECT)

    # ------------------------------------------------------------------
    # Service 0x10 — Diagnostic Session Control
    # ------------------------------------------------------------------
    def srv_10_session_control(self, data: bytes):
        if len(data) < 2:
            return self.negative(SID_DIAGNOSTIC_SESSION_CONTROL, NRC_INCORRECT_MESSAGE_LENGTH)

        sub      = data[1]
        old_sess = self.state.session
        sess_map = {0x01: ECUState.SESSION_DEFAULT, 0x03: ECUState.SESSION_EXTENDED}

        if sub == 0x01:
            self.state.session = ECUState.SESSION_DEFAULT
            self.state.security_level = 0
            _elog.info(
                f"[SVC-10] Session DEFAULT — "
                f"prev=0x{old_sess:02X} security reset to 0"
            )
            return bytes([0x50, 0x01, 0x00, 0x32, 0x01, 0xF4])

        if sub == 0x03:
            self.state.session = ECUState.SESSION_EXTENDED
            _elog.info(
                f"[SVC-10] Session EXTENDED — prev=0x{old_sess:02X}"
            )
            return bytes([0x50, 0x03, 0x00, 0x32, 0x01, 0xF4])

        if sub == 0x02:
            _elog.warning(
                f"[SVC-10] Programming session (0x02) rejected — "
                f"current session=0x{old_sess:02X} (requires 0x03)"
            )
            return self.negative(SID_DIAGNOSTIC_SESSION_CONTROL, NRC_SUBFUNCTION_NOT_SUPPORTED)

        _elog.warning(f"[SVC-10] Unknown sub-function 0x{sub:02X}")
        return self.negative(SID_DIAGNOSTIC_SESSION_CONTROL, NRC_SUBFUNCTION_NOT_SUPPORTED)

    # ------------------------------------------------------------------
    # Service 0x11 — ECU Reset
    # ------------------------------------------------------------------
    def srv_11_ecu_reset(self, data: bytes):
        if len(data) < 2:
            return self.negative(SID_ECU_RESET, NRC_INCORRECT_MESSAGE_LENGTH)

        reset_type = data[1]
        if reset_type != 0x01:
            _elog.warning(f"[SVC-11] Unsupported reset type 0x{reset_type:02X}")
            return self.negative(SID_ECU_RESET, NRC_SUBFUNCTION_NOT_SUPPORTED)

        _elog.info(
            f"[SVC-11] ECU Hard Reset — "
            f"boot_count={self.nvm.store['boot_count']} → {self.nvm.store['boot_count']+1}"
        )
        self.state.reset_volatile()
        self.mem.reset()
        self.nvm.store["boot_count"] += 1
        return bytes([0x51, 0x01])

    # ------------------------------------------------------------------
    # Service 0x3E — Tester Present
    # ------------------------------------------------------------------
    def srv_3e_tester_present(self, data: bytes):
        if len(data) < 2:
            return self.negative(SID_TESTER_PRESENT, NRC_INCORRECT_MESSAGE_LENGTH)

        sub = data[1]
        _elog.debug(f"[SVC-3E] TesterPresent sub=0x{sub:02X}")
        if sub == 0x80:
            return None         # suppress response
        return bytes([0x7E, sub])

    # ------------------------------------------------------------------
    # Service 0x22 — Read Data By Identifier
    # ------------------------------------------------------------------
    def srv_22_read_did(self, data: bytes):
        if len(data) < 3:
            return self.negative(SID_READ_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH)

        did = (data[1] << 8) | data[2]

        if did not in self.dids:
            _elog.warning(
                f"[SVC-22] DID=0x{did:04X} not found — "
                f"NRC_REQUEST_OUT_OF_RANGE"
            )
            return self.negative(SID_READ_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE)

        name, getter, _ = self.dids[did]
        value = getter()
        _elog.debug(
            f"[SVC-22] Read DID=0x{did:04X} ({name}) → {value.hex().upper()}"
        )
        return bytes([0x62, data[1], data[2]]) + value

    # ------------------------------------------------------------------
    # Service 0x2E — Write Data By Identifier
    # ------------------------------------------------------------------
    def srv_2e_write_did(self, data: bytes):
        if len(data) < 4:
            return self.negative(SID_WRITE_DATA_BY_IDENTIFIER, NRC_INCORRECT_MESSAGE_LENGTH)

        if self.state.session != ECUState.SESSION_EXTENDED:
            _elog.warning(
                f"[SVC-2E] Write DID rejected — "
                f"wrong session 0x{self.state.session:02X} (need EXTENDED)"
            )
            return self.negative(
                SID_WRITE_DATA_BY_IDENTIFIER,
                NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
            )

        if self.state.security_level < 1:
            _elog.warning(
                f"[SVC-2E] Write DID rejected — "
                f"security locked (level={self.state.security_level})"
            )
            return self.negative(SID_WRITE_DATA_BY_IDENTIFIER, NRC_SECURITY_ACCESS_DENIED)

        did     = (data[1] << 8) | data[2]
        payload = data[3:]

        if did not in self.dids:
            _elog.warning(f"[SVC-2E] DID=0x{did:04X} not found")
            return self.negative(SID_WRITE_DATA_BY_IDENTIFIER, NRC_REQUEST_OUT_OF_RANGE)

        name, _, setter = self.dids[did]
        if not setter:
            _elog.warning(f"[SVC-2E] DID=0x{did:04X} ({name}) is read-only")
            return self.negative(SID_WRITE_DATA_BY_IDENTIFIER, NRC_CONDITIONS_NOT_CORRECT)

        _elog.info(
            f"[SVC-2E] Write DID=0x{did:04X} ({name}) "
            f"len={len(payload)} payload={payload.hex().upper()}"
        )
        setter(payload)
        return bytes([0x6E, data[1], data[2]])

    def _set_vin(self, payload: bytes):
        self.nvm.store["vin"] = payload[:17]

    def _set_serial(self, payload: bytes):
        self.nvm.store["serial"] = payload[:16]

    # ------------------------------------------------------------------
    # Service 0x27 — Security Access
    # ------------------------------------------------------------------
    def srv_27_security_access(self, data: bytes):
        if len(data) < 2:
            return self.negative(SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH)

        sub = data[1]
        now = time.time()

        if now < self.state.locked_until:
            remaining = self.state.locked_until - now
            _elog.warning(
                f"[SVC-27] Access denied — time-delay lockout "
                f"({remaining:.1f}s remaining)"
            )
            return self.negative(SID_SECURITY_ACCESS, NRC_REQUIRED_TIME_DELAY_NOT_EXPIRED)

        attempts = (
            self.nvm.store["persistent_auth_failures"]
            if self.state.persistent_lockout
            else self.state.auth_failures_ram
        )
        if attempts >= self.state.max_attempts:
            _elog.warning(
                f"[SVC-27] ExceededAttempts — "
                f"attempts={attempts}/{self.state.max_attempts}"
            )
            _elog.log_failure(
                failure_type="EXCEEDED_AUTH_ATTEMPTS",
                description=f"Security access blocked after {attempts} failed attempts",
                payload=data,
                state=self.state,
            )
            return self.negative(SID_SECURITY_ACCESS, NRC_EXCEEDED_NUMBER_OF_ATTEMPTS)

        # ── Seed request ─────────────────────────────────────────────
        if sub in (0x01, 0x05):
            level = 1 if sub == 0x01 else 3
            seed  = random.randint(1, 65535)
            self.state.last_seed_level = level
            self.state.last_seed_value = seed
            _elog.info(
                f"[SVC-27] Seed issued — sub=0x{sub:02X} level={level} "
                f"seed=0x{seed:04X}"
            )
            return bytes([0x67, sub]) + struct.pack(">H", seed)

        # ── Key verification ─────────────────────────────────────────
        if sub in (0x02, 0x06):
            if len(data) < 4:
                return self.negative(SID_SECURITY_ACCESS, NRC_INCORRECT_MESSAGE_LENGTH)

            level    = 1 if sub == 0x02 else 3
            recv_key = struct.unpack(">H", data[2:4])[0]

            if self.state.last_seed_level != level:
                _elog.warning(
                    f"[SVC-27] Key sub mismatch — "
                    f"expected seed-level {level}, got {self.state.last_seed_level}"
                )
                return self.negative(SID_SECURITY_ACCESS, NRC_CONDITIONS_NOT_CORRECT)

            seed     = self.state.last_seed_value
            expected = (
                (seed ^ 0x4567)
                if level == 1
                else ((seed * 7) + 0x1234) & 0xFFFF
            )

            if recv_key == expected:
                self.state.security_level = level
                if self.state.persistent_lockout:
                    self.nvm.store["persistent_auth_failures"] = 0
                else:
                    self.state.auth_failures_ram = 0
                _elog.info(
                    f"[SVC-27] Access GRANTED — level={level} "
                    f"key=0x{recv_key:04X}"
                )
                return bytes([0x67, sub])

            # Wrong key
            if self.state.persistent_lockout:
                self.nvm.store["persistent_auth_failures"] += 1
                fail_count = self.nvm.store["persistent_auth_failures"]
            else:
                self.state.auth_failures_ram += 1
                fail_count = self.state.auth_failures_ram

            self.state.locked_until = time.time() + self.state.required_delay_s
            _elog.warning(
                f"[SVC-27] INVALID KEY — "
                f"got=0x{recv_key:04X} expected=0x{expected:04X} "
                f"failures={fail_count}/{self.state.max_attempts}"
            )
            if fail_count >= self.state.max_attempts:
                _elog.log_failure(
                    failure_type="AUTH_LOCKOUT",
                    description=(
                        f"Security access permanently locked after "
                        f"{fail_count} failed attempts"
                    ),
                    payload=data,
                    state=self.state,
                )
            return self.negative(SID_SECURITY_ACCESS, NRC_INVALID_KEY)

        _elog.warning(f"[SVC-27] Unknown sub-function 0x{sub:02X}")
        return self.negative(SID_SECURITY_ACCESS, NRC_SUBFUNCTION_NOT_SUPPORTED)

    # ------------------------------------------------------------------
    # Service 0x23 — Read Memory By Address
    # ------------------------------------------------------------------
    def srv_23_read_memory(self, data: bytes):
        if len(data) < 5:
            return self.negative(SID_READ_MEMORY_BY_ADDRESS, NRC_INCORRECT_MESSAGE_LENGTH)

        addr = (data[2] << 8) | data[3]
        size = data[4]

        _elog.debug(
            f"[SVC-23] ReadMemory addr=0x{addr:04X} size={size}"
        )
        try:
            mem = self.mem.read(addr, size)
            return bytes([0x63, data[1]]) + mem
        except IndexError:
            _elog.log_failure(
                failure_type="BUFFER_OVERFLOW",
                description=f"ReadMemory OOB addr=0x{addr:04X} size={size} "
                            f"(mem_size={self.mem.size})",
                payload=data,
                state=self.state,
            )
            return self.negative(SID_READ_MEMORY_BY_ADDRESS, NRC_REQUEST_OUT_OF_RANGE)

    # ------------------------------------------------------------------
    # Service 0x3D — Write Memory By Address
    # ------------------------------------------------------------------
    def srv_3d_write_memory(self, data: bytes):
        if len(data) < 5:
            return self.negative(SID_WRITE_MEMORY_BY_ADDRESS, NRC_INCORRECT_MESSAGE_LENGTH)

        if self.state.session != ECUState.SESSION_EXTENDED:
            _elog.warning(
                f"[SVC-3D] WriteMemory rejected — "
                f"wrong session 0x{self.state.session:02X}"
            )
            return self.negative(
                SID_WRITE_MEMORY_BY_ADDRESS,
                NRC_SERVICE_NOT_SUPPORTED_IN_ACTIVE_SESSION,
            )

        if self.state.security_level < 1:
            _elog.warning(
                f"[SVC-3D] WriteMemory rejected — security locked"
            )
            return self.negative(SID_WRITE_MEMORY_BY_ADDRESS, NRC_SECURITY_ACCESS_DENIED)

        addr    = (data[2] << 8) | data[3]
        size    = data[4]
        payload = data[5:]

        if len(payload) < size:
            _elog.warning(
                f"[SVC-3D] WriteMemory payload too short — "
                f"expected {size} bytes got {len(payload)}"
            )
            return self.negative(SID_WRITE_MEMORY_BY_ADDRESS, NRC_INCORRECT_MESSAGE_LENGTH)

        _elog.info(
            f"[SVC-3D] WriteMemory addr=0x{addr:04X} size={size} "
            f"data={payload[:size].hex().upper()}"
        )
        try:
            self.mem.write(addr, payload[:size])
            return bytes([0x7D, data[1], data[2], data[3]])
        except IndexError:
            _elog.log_failure(
                failure_type="BUFFER_OVERFLOW",
                description=f"WriteMemory OOB addr=0x{addr:04X} size={size} "
                            f"(mem_size={self.mem.size})",
                payload=data,
                state=self.state,
            )
            return self.negative(SID_WRITE_MEMORY_BY_ADDRESS, NRC_REQUEST_OUT_OF_RANGE)
