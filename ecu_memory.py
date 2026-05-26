# ecu_memory.py

class VirtualMemory:
    def __init__(self, size=4096):
        self.size = size
        self.ram = bytearray(size)

    def reset(self):
        self.ram = bytearray(self.size)

    def read(self, addr: int, length: int) -> bytes:
        if addr < 0 or length < 0:
            raise ValueError("Negative addr/len")
        if addr + length > len(self.ram):
            raise IndexError("OOB read")
        return bytes(self.ram[addr:addr+length])

    def write(self, addr: int, data: bytes):
        if addr < 0:
            raise ValueError("Negative addr")
        if addr + len(data) > len(self.ram):
            raise IndexError("OOB write")
        self.ram[addr:addr+len(data)] = data


class VirtualNVM:
    def __init__(self):
        self.store = {
            "vin": b"SIMULATORVIN12345",
            "serial": b"SN9876543210",
            "boot_count": 0,
            "persistent_auth_failures": 0
        }
