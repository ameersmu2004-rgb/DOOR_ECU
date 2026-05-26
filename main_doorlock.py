# main_doorlock.py — Door Lock ECU Simulator Entry Point
# CAN ID: 0x19B  (FucyFuzz Demo → Door Lock Fuzz)

import tkinter as tk
from doorlock_gui import DoorLockGUI

if __name__ == "__main__":
    root = tk.Tk()
    app  = DoorLockGUI(root)
    root.mainloop()
