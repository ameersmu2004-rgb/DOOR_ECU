# doorlock_state.py — Door Lock ECU State

import time
import random


class DoorLockState:
    """Live state for the Door Lock ECU."""

    # CAN ID this ECU responds to (FucyFuzz Demo: Door Lock Fuzz)
    CAN_ID = 0x19B

    # DIDs
    DID_DOOR_FL         = 0xF601   # Front-Left  0=locked 1=unlocked
    DID_DOOR_FR         = 0xF602   # Front-Right 0=locked 1=unlocked
    DID_DOOR_RL         = 0xF603   # Rear-Left   0=locked 1=unlocked
    DID_DOOR_RR         = 0xF604   # Rear-Right  0=locked 1=unlocked
    DID_TRUNK           = 0xF605   # Trunk       0=closed 1=open
    DID_CHILD_LOCK_RL   = 0xF606   # Rear-Left child lock  0=off 1=on
    DID_CHILD_LOCK_RR   = 0xF607   # Rear-Right child lock 0=off 1=on
    DID_LOCK_VOLTAGE    = 0xF608   # mV
    DID_LOCK_CMD_COUNT  = 0xF609   # total lock/unlock commands since boot
    DID_FAULT_CODE      = 0xF60A   # 0=none 1=FL_motor 2=FR_motor 3=RL 4=RR 5=trunk

    def __init__(self):
        # --- Door states ---
        self.door_fl    = False   # False=locked True=unlocked
        self.door_fr    = False
        self.door_rl    = False
        self.door_rr    = False
        self.trunk_open = False
        self.child_rl   = True    # child lock on by default
        self.child_rr   = True

        # --- Electrical ---
        self.lock_voltage   = 12500    # mV
        self.fault_code     = 0
        self.cmd_count      = 0

        # --- Fuzz detection ---
        self.fuzz_detected      = False
        self.fuzz_frame_count   = 0
        self.last_fuzz_payload  = None

        # --- Simulation ---
        self._sim_mode     = "all_locked"
        self._sim_timer    = 0.0
        self._sim_duration = random.uniform(5.0, 15.0)
        self._anim_phase   = 0.0   # for lock/unlock animation

        # --- UDS session / security ---
        self.session            = 0x01
        self.security_level     = 0
        self.auth_failures_ram  = 0
        self.max_attempts       = 3
        self.required_delay_s   = 3.0
        self.locked_until       = 0.0
        self.persistent_lockout = False
        self.p2_ms              = 50
        self.p2_star_ms         = 2000
        self.faulted            = False
        self.fault_reason       = ""
        self.fault_until        = 0.0
        self.hang_until         = 0.0
        self.last_seed_level    = None
        self.last_seed_value    = 0

        self._last_tick = time.monotonic()

    # ------------------------------------------------------------------
    def tick(self):
        now = time.monotonic()
        dt  = min(now - self._last_tick, 0.1)
        self._last_tick = now
        self._simulate(dt)

    # ------------------------------------------------------------------
    def _simulate(self, dt: float):
        self._sim_timer  += dt
        self._anim_phase += dt

        # Voltage variation
        self.lock_voltage = int(12400 + random.uniform(-100, 100))

        if self._sim_timer < self._sim_duration:
            return

        # Advance to next state
        self._sim_timer    = 0.0
        self._sim_duration = random.uniform(4.0, 18.0)

        r = random.random()

        if self._sim_mode == "all_locked":
            if r < 0.6:
                self._sim_mode = "all_unlocked"
                self.door_fl = self.door_fr = True
                self.door_rl = self.door_rr = True
                self.cmd_count += 1
            elif r < 0.75:
                self._sim_mode = "driver_only"
                self.door_fl = True
                self.door_fr = self.door_rl = self.door_rr = False
                self.cmd_count += 1

        elif self._sim_mode == "all_unlocked":
            if r < 0.5:
                self._sim_mode = "all_locked"
                self.door_fl = self.door_fr = False
                self.door_rl = self.door_rr = False
                self.cmd_count += 1
            elif r < 0.6:
                # Open trunk briefly
                self._sim_mode = "trunk_open"
                self.trunk_open = True
                self._sim_duration = random.uniform(2.0, 6.0)

        elif self._sim_mode == "driver_only":
            self._sim_mode = "all_locked"
            self.door_fl = False
            self.cmd_count += 1

        elif self._sim_mode == "trunk_open":
            self.trunk_open = False
            self._sim_mode  = "all_locked"
            self.door_fl = self.door_fr = False
            self.door_rl = self.door_rr = False

        # Rare fault injection
        if random.random() < 0.0003:
            self.fault_code = random.choice([0, 0, 1, 2, 3, 4])

    @property
    def all_locked(self) -> bool:
        return not (self.door_fl or self.door_fr or self.door_rl or self.door_rr)

    @property
    def any_unlocked(self) -> bool:
        return self.door_fl or self.door_fr or self.door_rl or self.door_rr

    @property
    def active_mode(self) -> str:
        if self.trunk_open:                         return "TRUNK"
        if not self.any_unlocked:                   return "LOCKED"
        if self.door_fl and not self.door_fr:       return "DRIVER"
        if self.door_fl and self.door_fr and self.door_rl and self.door_rr:
            return "UNLOCKED"
        return "PARTIAL"

    # ------------------------------------------------------------------
    def read_did(self, did: int) -> bytes | None:
        if did == self.DID_DOOR_FL:       return bytes([1 if self.door_fl else 0])
        if did == self.DID_DOOR_FR:       return bytes([1 if self.door_fr else 0])
        if did == self.DID_DOOR_RL:       return bytes([1 if self.door_rl else 0])
        if did == self.DID_DOOR_RR:       return bytes([1 if self.door_rr else 0])
        if did == self.DID_TRUNK:         return bytes([1 if self.trunk_open else 0])
        if did == self.DID_CHILD_LOCK_RL: return bytes([1 if self.child_rl else 0])
        if did == self.DID_CHILD_LOCK_RR: return bytes([1 if self.child_rr else 0])
        if did == self.DID_LOCK_VOLTAGE:  return self.lock_voltage.to_bytes(2, "big")
        if did == self.DID_LOCK_CMD_COUNT:return self.cmd_count.to_bytes(4, "big")
        if did == self.DID_FAULT_CODE:    return bytes([self.fault_code])
        return None

    def write_did(self, did: int, data: bytes) -> bool:
        if did == self.DID_DOOR_FL:
            self.door_fl = bool(data[0]); self.cmd_count += 1; return True
        if did == self.DID_DOOR_FR:
            self.door_fr = bool(data[0]); self.cmd_count += 1; return True
        if did == self.DID_DOOR_RL:
            self.door_rl = bool(data[0]); self.cmd_count += 1; return True
        if did == self.DID_DOOR_RR:
            self.door_rr = bool(data[0]); self.cmd_count += 1; return True
        if did == self.DID_TRUNK:
            self.trunk_open = bool(data[0]); return True
        if did == self.DID_CHILD_LOCK_RL:
            self.child_rl = bool(data[0]); return True
        if did == self.DID_CHILD_LOCK_RR:
            self.child_rr = bool(data[0]); return True
        return False

    def reset_volatile(self):
        self.session            = 0x01
        self.security_level     = 0
        self.last_seed_level    = None
        self.last_seed_value    = 0
        self.locked_until       = 0.0
        self.auth_failures_ram  = 0
        self.faulted            = False
        self.fault_reason       = ""
        self.fault_until        = 0.0
        self.hang_until         = 0.0
