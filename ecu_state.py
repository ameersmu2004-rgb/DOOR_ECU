# ecu_state.py

class ECUState:
    SESSION_DEFAULT = 0x01
    SESSION_PROGRAMMING = 0x02
    SESSION_EXTENDED = 0x03

    def __init__(self):
        self.session = self.SESSION_DEFAULT
        self.security_level = 0
        self.last_seed_level = None
        self.last_seed_value = 0

        self.max_attempts = 3
        self.required_delay_s = 3.0
        self.locked_until = 0.0
        self.auth_failures_ram = 0
        self.persistent_lockout = False

        self.p2_ms = 50
        self.p2_star_ms = 2000

        self.faulted = False
        self.fault_reason = ""
        self.fault_until = 0.0

        self.hang_until = 0.0

    def reset_volatile(self):
        self.session = self.SESSION_DEFAULT
        self.security_level = 0
        self.last_seed_level = None
        self.last_seed_value = 0
        self.locked_until = 0.0
        self.auth_failures_ram = 0

        self.faulted = False
        self.fault_reason = ""
        self.fault_until = 0.0

        self.hang_until = 0.0
