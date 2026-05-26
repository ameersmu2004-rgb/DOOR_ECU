# virtual_ecu.py

import time
import threading
import can

from config import INTERFACE, ECU_RX_ID, ECU_TX_ID, VULN_JSON_PATH
from uds_constants import SID_WRITE_MEMORY_BY_ADDRESS, NRC_RESPONSE_PENDING
from uds_helpers import hex2, uds_sid_name, nrc_name

from ecu_state import ECUState
from ecu_memory import VirtualMemory, VirtualNVM
from uds_core import UDSCore
from isotp_server import ISOTPServer
from vulnerability_config import VulnerabilityConfig
from vulnerability_engine import VulnerabilityEngine
from logger import ECULogger

_elog = ECULogger("virtual_ecu")


class VirtualECU:
    def __init__(self, log_callback, raw_can_callback, oracle_callback):
        self.log    = log_callback
        self.raw_log = raw_can_callback
        self.oracle  = oracle_callback

        # Wire GUI callbacks into the structured logger so every event
        # reaches both the Tkinter console and the log files.
        _elog._gui_cb = log_callback

        self.state = ECUState()
        self.mem   = VirtualMemory(4096)
        self.nvm   = VirtualNVM()

        self.uds = UDSCore(self.state, self.mem, self.nvm, self.log)
        self.tp  = ISOTPServer(INTERFACE, ECU_RX_ID, ECU_TX_ID, self.log)

        self.running = True

        try:
            self.bus_sniffer = can.Bus(INTERFACE, bustype="socketcan")
            _elog.info(f"[SYSTEM] CAN sniffer attached to {INTERFACE}")
        except Exception as exc:
            self.bus_sniffer = None
            _elog.warning(f"[SYSTEM] CAN sniffer unavailable on {INTERFACE}: {exc}")

        self.sniffer_thread = threading.Thread(target=self._sniff_raw_can, daemon=True)

        self.cfg = VulnerabilityConfig(VULN_JSON_PATH, self.log, self.oracle)
        self.cfg.load()

        self.apply_cfg()

        self.vuln_engine = VulnerabilityEngine(self.cfg, self.state, self.log, self.oracle)
        _elog.info(
            f"[SYSTEM] VirtualECU ready — profile={self.cfg.profile} "
            f"vulns={len(self.cfg.vulnerabilities)}"
        )

    def apply_cfg(self):
        self.state.p2_ms      = int(self.cfg.uds_settings.get("p2_timeout_ms", 50))
        self.state.p2_star_ms = int(self.cfg.uds_settings.get("p2_star_timeout_ms", 2000))
        _elog.debug(
            f"[CFG] p2={self.state.p2_ms}ms  p2*={self.state.p2_star_ms}ms"
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def start(self):
        self.log("[SYSTEM] ECU Simulator Started")
        self.oracle("[SYSTEM] Oracle Log started")
        _elog.info("[SYSTEM] ECU main loop starting")
        self.sniffer_thread.start()

        while self.running:
            # ── Fault / recovery handling ─────────────────────────────
            if self.state.faulted:
                if time.time() >= self.state.fault_until:
                    reason = self.state.fault_reason
                    self.oracle(
                        f"[{time.strftime('%H:%M:%S')}] ECU reboot after fault ({reason})"
                    )
                    _elog.info(
                        f"[RECOVERY] Rebooting after fault '{reason}' — "
                        f"boot_count will be {self.nvm.store['boot_count'] + 1}"
                    )
                    self.state.reset_volatile()
                    self.mem.reset()
                    self.nvm.store["boot_count"] += 1
                    self.log("[ECU] Reboot complete")
                else:
                    time.sleep(0.01)
                    continue

            self.tp.process()

            if self.tp.available():
                req = self.tp.recv()
                self._handle_request(req)

            time.sleep(0.002)

        _elog.info("[SYSTEM] ECU main loop exited")

    def stop(self):
        _elog.info("[SYSTEM] VirtualECU.stop() called")
        self.running = False

    # ------------------------------------------------------------------
    # CAN sniffer
    # ------------------------------------------------------------------
    def _sniff_raw_can(self):
        _elog.debug("[SNIFFER] CAN sniffer thread started")
        while self.running and self.bus_sniffer:
            try:
                msg = self.bus_sniffer.recv(timeout=0.5)
                if msg:
                    self.raw_log(msg)
                    _elog.debug(
                        f"[CAN-RAW] ID=0x{msg.arbitration_id:03X} "
                        f"DLC={msg.dlc} DATA={msg.data.hex().upper()}"
                    )
                    self.vuln_engine.on_raw_can_frame(msg)
            except Exception as exc:
                _elog.warning(f"[SNIFFER] recv error: {exc}")

    # ------------------------------------------------------------------
    # Request handler
    # ------------------------------------------------------------------
    def _handle_request(self, req: bytes):
        if not req:
            return

        # ── Emit state snapshot BEFORE processing ─────────────────────
        _elog.log_state_snapshot(
            self.state,
            extra=f"incoming SID=0x{req[0]:02X} payload={req.hex().upper()}"
        )

        if time.time() < self.state.hang_until:
            _elog.debug(
                f"[HANG] Request 0x{req[0]:02X} suppressed "
                f"(hang until {self.state.hang_until:.2f})"
            )
            return

        sid = req[0]
        self.log(f"[RX][UDS] {hex2(sid)} {uds_sid_name(sid)} | {req.hex().upper()}")
        # Structured log of the incoming UDS frame
        _elog.log_uds_request(req, state=self.state)

        action = self.vuln_engine.evaluate_uds(req)

        if action:
            action_type = action.get("type")
            _elog.info(
                f"[VULN-ACTION] SID=0x{sid:02X} action={action_type} "
                f"payload={req.hex().upper()}"
            )

            if action_type == "FORCED_RESPONSE":
                resp = action["response"]
                time.sleep(self.state.p2_ms / 1000.0)
                self.tp.send(resp)
                self._log_response(resp)
                return

            if action_type == "FAULTED":
                self.log("[ECU] Fault triggered")
                _elog.log_failure(
                    failure_type=self.state.fault_reason or "CRASH",
                    description="ECU entered faulted state via vulnerability action",
                    payload=req,
                    state=self.state,
                )
                return

            if action_type == "HANG":
                self.log("[ECU] Hang triggered")
                _elog.log_failure(
                    failure_type="DOS_HANG",
                    description=f"ECU hang triggered — unresponsive until {self.state.hang_until:.2f}",
                    payload=req,
                    state=self.state,
                )
                return

            if action_type == "BYPASS_WRITE_DID":
                if len(req) >= 3:
                    resp = bytes([0x6E, req[1], req[2]])
                    self.tp.send(resp)
                    self._log_response(resp)
                    return

            if action_type == "ACCEPT_PROGRAMMING_SESSION":
                if len(req) >= 2 and req[0] == 0x10 and req[1] == 0x02:
                    self.state.session = ECUState.SESSION_PROGRAMMING
                    resp = bytes([0x50, 0x02, 0x00, 0x32, 0x01, 0xF4])
                    self.tp.send(resp)
                    self._log_response(resp)
                    _elog.warning(
                        "[VULN-ACTION] ACCEPT_PROGRAMMING_SESSION — "
                        "illegal session transition forced by vulnerability"
                    )
                    return

        # ── Standard UDS handling ─────────────────────────────────────
        if sid == SID_WRITE_MEMORY_BY_ADDRESS:
            _elog.debug(f"[UDS] WriteMemoryByAddress — sending ResponsePending (0x78)")
            self.tp.send(bytes([0x7F, sid, NRC_RESPONSE_PENDING]))
            time.sleep(0.35)

        try:
            resp = self.uds.handle(req)
        except Exception as exc:
            _elog.log_exception(
                "uds.handle raised unexpectedly",
                exc, payload=req, state=self.state,
            )
            resp = None

        time.sleep(self.state.p2_ms / 1000.0)

        if resp:
            self.tp.send(resp)
            self._log_response(resp)

    # ------------------------------------------------------------------
    # Response helper
    # ------------------------------------------------------------------
    def _log_response(self, resp: bytes):
        if resp[0] == 0x7F and len(resp) >= 3:
            self.log(
                f"[TX][UDS] 7F {hex2(resp[1])} {hex2(resp[2])} "
                f"({nrc_name(resp[2])}) | {resp.hex().upper()}"
            )
        else:
            self.log(f"[TX][UDS] {resp.hex().upper()}")

        # Structured response log (includes negative-response classification)
        _elog.log_uds_response(resp)
