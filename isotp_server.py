# isotp_server.py

import can
import isotp

from logger import ECULogger

_elog = ECULogger("isotp_server")


class ISOTPServer:
    def __init__(self, interface: str, rx_id: int, tx_id: int, log):
        self.log   = log
        self.stack = None

        _elog.info(
            f"[INIT] ISO-TP server starting — "
            f"interface={interface} RX=0x{rx_id:03X} TX=0x{tx_id:03X}"
        )

        try:
            self.bus = can.Bus(interface, bustype="socketcan")
            self.stack = isotp.CanStack(
                bus=self.bus,
                address=isotp.Address(
                    isotp.AddressingMode.Normal_11bits,
                    rxid=rx_id,
                    txid=tx_id,
                ),
                params={"stmin": 5, "blocksize": 8, "wftmax": 2},
            )
            _elog.info(
                f"[INIT] ISO-TP stack ready — "
                f"stmin=5ms blocksize=8 wftmax=2"
            )
        except Exception as exc:
            msg = f"[SYSTEM][ERR] CAN/ISO-TP init failed: {exc}"
            self.log(msg)
            _elog.log_failure(
                failure_type="CAN_INIT_ERROR",
                description=f"Failed to initialise CAN bus on {interface}: {exc}",
            )
            self.stack = None

    # ------------------------------------------------------------------
    def process(self) -> None:
        if self.stack:
            try:
                self.stack.process()
            except Exception as exc:
                _elog.warning(f"[PROCESS] ISO-TP stack.process() error: {exc}")

    def available(self) -> bool:
        return self.stack.available() if self.stack else False

    def recv(self) -> bytes:
        if not self.stack:
            return None
        try:
            data = self.stack.recv()
            if data:
                _elog.debug(
                    f"[RECV] ISO-TP frame — len={len(data)} "
                    f"hex={data.hex().upper()}"
                )
            return data
        except Exception as exc:
            _elog.warning(f"[RECV] ISO-TP recv() error: {exc}")
            return None

    def send(self, payload: bytes) -> None:
        if not self.stack:
            _elog.warning("[SEND] Attempted send on uninitialised ISO-TP stack")
            return
        try:
            _elog.debug(
                f"[SEND] ISO-TP frame — len={len(payload)} "
                f"hex={payload.hex().upper()}"
            )
            self.stack.send(payload)
        except Exception as exc:
            _elog.log_failure(
                failure_type="ISOTP_SEND_ERROR",
                description=f"ISO-TP send() raised: {exc}",
                payload=payload,
            )
