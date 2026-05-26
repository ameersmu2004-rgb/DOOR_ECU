# doorlock_gui.py — Door Lock ECU Simulator GUI
# Aesthetic: Holographic HUD × Automotive Telemetry — matches Speedometer style
# CAN ID: 0x19B  (FucyFuzz Demo → Door Lock Fuzz)

import time
import os
import math
import threading
import tkinter as tk
from tkinter import filedialog
import can

from doorlock_ecu import DoorLockECU
from doorlock_state import DoorLockState

try:
    from vulnerability_config import VulnerabilityConfig
    from vulnerability_engine import VulnerabilityEngine
    VULN_AVAILABLE = True
except ImportError:
    VULN_AVAILABLE = False

try:
    from logger import ECULogger
    _elog = ECULogger("doorlock_gui")
except ImportError:
    class _FakeLog:
        def __getattr__(self, _): return lambda *a, **k: None
    _elog = _FakeLog()

try:
    from isotp_server import ISOTPServer
    from config import INTERFACE
    ISOTP_AVAILABLE = True
except Exception:
    ISOTP_AVAILABLE = False
    INTERFACE = "vcan0"

ECU_RX_ID = 0x19B   # FucyFuzz Demo: Door Lock Fuzz target
ECU_TX_ID = 0x19C   # Response ID

# ═══════════════════════════════════════════════════════════════════════════════
# COLOUR PALETTE — Cyan / Blue on Deep Space Black (security/lock theme)
# ═══════════════════════════════════════════════════════════════════════════════
C = {
    "bg":           "#080C10",
    "bg2":          "#0C1218",
    "bg3":          "#111820",
    "bg4":          "#161E28",
    "border":       "#1A2535",
    "border_hi":    "#243040",

    # Primary — Cyan/Blue (lock theme)
    "cyan":         "#00CFFF",
    "cyan_dim":     "#006680",
    "cyan_lo":      "#002830",
    "blue":         "#0090FF",
    "blue_dim":     "#003866",
    "blue_lo":      "#001833",

    # Unlocked — Green
    "green":        "#00E57A",
    "green_dim":    "#007A40",
    "green_lo":     "#003020",

    # Locked — Red/dim
    "red":          "#FF3333",
    "red_dim":      "#7A0000",
    "red_lo":       "#330000",

    # Amber warnings
    "amber":        "#FFB300",
    "amber_dim":    "#7A5500",
    "amber_lo":     "#3A2800",

    # Teal accents
    "teal":         "#00E5CC",
    "teal_dim":     "#007A6E",
    "teal_lo":      "#003A34",

    "white":        "#F0F8FF",
    "mid":          "#6A8FA0",
    "dim":          "#3A5060",

    "font_main":    "Consolas",
    "font_hud":     "Consolas",
}


# ═══════════════════════════════════════════════════════════════════════════════
# ANIMATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class Animator:
    def __init__(self, root):
        self.root = root
        self._tasks = []
        self._running = True
        self._tick()

    def _tick(self):
        if not self._running:
            return
        now = time.monotonic()
        for t in list(self._tasks):
            if now >= t["next"]:
                try:
                    keep = t["fn"]()
                except Exception:
                    keep = False
                if keep is False:
                    self._tasks.remove(t)
                else:
                    t["next"] = now + t["interval"]
        self.root.after(16, self._tick)

    def repeat(self, interval_ms, fn):
        task = {"fn": fn, "interval": interval_ms / 1000, "next": time.monotonic()}
        self._tasks.append(task)
        return task

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# CAR TOP-VIEW PANEL — centrepiece
# ═══════════════════════════════════════════════════════════════════════════════

class DoorLockPanel(tk.Canvas):
    """
    Top-down car silhouette with 4 door indicators + trunk.
    Each door glows green (unlocked) or red (locked).
    """

    W = 460
    H = 340

    def __init__(self, parent, **kw):
        super().__init__(parent,
                         width=self.W, height=self.H,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._state  = {"fl": False, "fr": False, "rl": False, "rr": False,
                        "trunk": False}
        self._mode   = "LOCKED"
        self._fuzz   = 0
        self._cmds   = 0
        self._phase  = 0.0
        self._glow   = {"fl":0.0,"fr":0.0,"rl":0.0,"rr":0.0,"trunk":0.0}
        self._build_static()

    # ------------------------------------------------------------------
    def _build_static(self):
        W, H = self.W, self.H
        cx = W // 2

        # Panel border
        self.create_rectangle(4, 4, W-4, H-4,
                              outline=C["border_hi"], fill=C["bg2"], width=1)
        self.create_rectangle(8, 8, W-8, H-8,
                              outline=C["border"], fill="", width=1)

        # Title
        self.create_text(cx, 22, text="◈  DOOR LOCK ECU  —  CAN ID: 0x19B",
                         fill=C["cyan_dim"], font=(C["font_hud"], 11, "bold"))
        self.create_line(20, 34, W-20, 34, fill=C["cyan_lo"], width=1)

        # ── Car silhouette (top-down outline) ────────────────────────
        car_cx, car_cy = cx, H // 2 + 10
        cw, ch = 90, 160   # car half-width, half-height

        # Body
        self._car_body = self.create_rounded_rect(
            car_cx - cw, car_cy - ch,
            car_cx + cw, car_cy + ch,
            radius=22, fill=C["bg3"], outline=C["border_hi"], width=2)

        # Windscreen front
        self.create_rounded_rect(
            car_cx - 62, car_cy - ch + 18,
            car_cx + 62, car_cy - ch + 68,
            radius=10, fill=C["bg2"], outline=C["cyan_lo"], width=1)

        # Windscreen rear
        self.create_rounded_rect(
            car_cx - 60, car_cy + ch - 68,
            car_cx + 60, car_cy + ch - 18,
            radius=10, fill=C["bg2"], outline=C["cyan_lo"], width=1)

        # Centre console divider
        self.create_line(car_cx, car_cy - 40, car_cx, car_cy + 40,
                         fill=C["border"], width=1, dash=(4,4))

        # Front axle
        self.create_line(car_cx - cw - 10, car_cy - 55,
                         car_cx + cw + 10, car_cy - 55,
                         fill=C["border_hi"], width=2)
        # Rear axle
        self.create_line(car_cx - cw - 10, car_cy + 55,
                         car_cx + cw + 10, car_cy + 55,
                         fill=C["border_hi"], width=2)

        # Wheels (4 corners)
        for wx, wy, ww, wh in [
            (car_cx - cw - 18, car_cy - 55, 18, 32),
            (car_cx + cw,      car_cy - 55, 18, 32),
            (car_cx - cw - 18, car_cy + 55, 18, 32),
            (car_cx + cw,      car_cy + 55, 18, 32),
        ]:
            self.create_rectangle(wx - ww//2, wy - wh//2,
                                  wx + ww//2, wy + wh//2,
                                  fill=C["bg4"], outline=C["border_hi"], width=1)

        # Trunk label
        self.create_text(car_cx, car_cy + ch + 16, text="TRUNK",
                         fill=C["dim"], font=(C["font_hud"], 8))

        # ── Door indicator boxes ─────────────────────────────────────
        door_configs = {
            "fl":    (car_cx - cw - 44, car_cy - 55, "FL"),
            "fr":    (car_cx + cw + 44, car_cy - 55, "FR"),
            "rl":    (car_cx - cw - 44, car_cy + 55, "RL"),
            "rr":    (car_cx + cw + 44, car_cy + 55, "RR"),
        }
        self._door_boxes  = {}
        self._door_icons  = {}
        self._door_labels = {}
        self._door_glows  = {}

        for key, (dx, dy, lbl) in door_configs.items():
            bw, bh = 46, 30
            # Glow oval
            go = self.create_oval(dx - bw, dy - bh, dx + bw, dy + bh,
                                  fill="", outline="", width=0)
            self._door_glows[key] = go
            # Box
            box = self.create_rectangle(dx - bw//2, dy - bh//2,
                                        dx + bw//2, dy + bh//2,
                                        fill=C["bg4"], outline=C["border"], width=1)
            self._door_boxes[key] = box
            # Lock icon text
            icon = self.create_text(dx, dy - 3, text="🔒",
                                    fill=C["cyan_dim"],
                                    font=(C["font_hud"], 14))
            self._door_icons[key] = icon
            # Label
            lbl_id = self.create_text(dx, dy + 14, text=lbl,
                                      fill=C["dim"],
                                      font=(C["font_hud"], 8, "bold"))
            self._door_labels[key] = lbl_id

        # Trunk box (bottom centre)
        tx, ty = car_cx, car_cy + ch + 30
        tw, th = 50, 20
        self._trunk_glow = self.create_oval(tx - tw - 10, ty - th - 10,
                                             tx + tw + 10, ty + th + 10,
                                             fill="", outline="", width=0)
        self._trunk_box  = self.create_rectangle(tx - tw, ty - th,
                                                  tx + tw, ty + th,
                                                  fill=C["bg4"],
                                                  outline=C["border"], width=1)
        self._trunk_icon = self.create_text(tx, ty, text="🔒",
                                             fill=C["cyan_dim"],
                                             font=(C["font_hud"], 11))

        # ── Mode badge ──────────────────────────────────────────────
        self._mode_text = self.create_text(cx, H - 46,
                                            text="ALL LOCKED",
                                            fill=C["red"],
                                            font=(C["font_hud"], 16, "bold"))

        # ── Bottom stats ────────────────────────────────────────────
        self._cmd_text = self.create_text(cx - 90, H - 20,
                                           text="CMDS: 0",
                                           fill=C["teal_dim"],
                                           font=(C["font_hud"], 9))
        self._fuzz_text = self.create_text(cx + 90, H - 20,
                                            text="FUZZ: 0",
                                            fill=C["dim"],
                                            font=(C["font_hud"], 9))

    # ------------------------------------------------------------------
    def create_rounded_rect(self, x1, y1, x2, y2, radius=15, **kw):
        pts = [
            x1+radius, y1,
            x2-radius, y1,
            x2, y1,
            x2, y1+radius,
            x2, y2-radius,
            x2, y2,
            x2-radius, y2,
            x1+radius, y2,
            x1, y2,
            x1, y2-radius,
            x1, y1+radius,
            x1, y1,
        ]
        return self.create_polygon(pts, smooth=True, **kw)

    # ------------------------------------------------------------------
    def update(self, fl, fr, rl, rr, trunk, mode, cmd_count, fuzz_frames):
        doors = {"fl": fl, "fr": fr, "rl": rl, "rr": rr}
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        glow = 0.5 + 0.5 * math.sin(self._phase)

        for key, unlocked in doors.items():
            if unlocked:
                self._glow[key] = min(1.0, self._glow[key] + 0.20)
                box_col  = C["green_lo"]
                border_c = C["green_dim"]
                icon_col = C["green"]
                lbl_col  = C["green"]
                icon_txt = "🔓"
            else:
                self._glow[key] = max(0.0, self._glow[key] - 0.12)
                box_col  = C["bg4"]
                border_c = C["border"]
                icon_col = C["cyan_dim"]
                lbl_col  = C["dim"]
                icon_txt = "🔒"

            g = self._glow[key]
            glow_col = self._blend_alpha(C["green"] if unlocked else C["red"], g * 0.08)
            self.itemconfig(self._door_glows[key], outline=glow_col)
            self.itemconfig(self._door_boxes[key],  fill=box_col, outline=border_c)
            self.itemconfig(self._door_icons[key],  text=icon_txt, fill=icon_col)
            self.itemconfig(self._door_labels[key], fill=lbl_col)

        # Trunk
        if trunk:
            self._glow["trunk"] = min(1.0, self._glow["trunk"] + 0.20)
            self.itemconfig(self._trunk_glow, outline=self._blend_alpha(C["amber"], 0.08))
            self.itemconfig(self._trunk_box,  fill=C["amber_lo"], outline=C["amber_dim"])
            self.itemconfig(self._trunk_icon, text="🔓", fill=C["amber"])
        else:
            self._glow["trunk"] = max(0.0, self._glow["trunk"] - 0.12)
            self.itemconfig(self._trunk_glow, outline="")
            self.itemconfig(self._trunk_box,  fill=C["bg4"], outline=C["border"])
            self.itemconfig(self._trunk_icon, text="🔒", fill=C["cyan_dim"])

        # Mode label
        mode_map = {
            "LOCKED":   (C["red"],   "◉ ALL LOCKED"),
            "UNLOCKED": (C["green"], "◉ ALL UNLOCKED"),
            "DRIVER":   (C["cyan"],  "◉ DRIVER ONLY"),
            "PARTIAL":  (C["amber"], "◉ PARTIAL"),
            "TRUNK":    (C["amber"], "◉ TRUNK OPEN"),
        }
        col, txt = mode_map.get(mode, (C["mid"], mode))
        self.itemconfig(self._mode_text, text=txt, fill=col)

        # Stats
        self.itemconfig(self._cmd_text,  text=f"CMDS: {cmd_count}")
        fuzz_col = C["red"] if fuzz_frames > 100 else (C["amber_dim"] if fuzz_frames > 10 else C["dim"])
        self.itemconfig(self._fuzz_text, text=f"FUZZ: {fuzz_frames}", fill=fuzz_col)

    # ------------------------------------------------------------------
    @staticmethod
    def _blend_alpha(hex_color, alpha):
        h=hex_color.lstrip("#"); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        br,bg_,bb=8,12,16
        return "#{:02x}{:02x}{:02x}".format(
            int(br+(r-br)*alpha), int(bg_+(g-bg_)*alpha), int(bb+(b-bb)*alpha))


# ═══════════════════════════════════════════════════════════════════════════════
# MINI GAUGE
# ═══════════════════════════════════════════════════════════════════════════════

class MiniGauge(tk.Canvas):
    SIZE = 110
    def __init__(self, parent, label="", unit="", max_val=100,
                 low_warn=None, high_warn=None, color=None, **kw):
        super().__init__(parent, width=self.SIZE, height=self.SIZE + 20,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._max=max_val; self._low=low_warn; self._high=high_warn
        self._color=color or C["cyan"]; self._display=0.0
        self._label=label; self._unit=unit
        self._build()

    def _build(self):
        cx=cy=self.SIZE//2; r=cx-10
        self.create_arc(cx-r,cy-r,cx+r,cy+r,start=-30,extent=-300,
                        outline=C["border"],style="arc",width=3)
        for i in range(11):
            frac=i/10; angle=math.radians(-30-frac*300+90)
            x1=cx+(r-2)*math.cos(angle); y1=cy-(r-2)*math.sin(angle)
            x2=cx+(r-8 if i%2==0 else r-5)*math.cos(angle)
            y2=cy-(r-8 if i%2==0 else r-5)*math.sin(angle)
            self.create_line(x1,y1,x2,y2,fill=C["border_hi"],width=1)
        self._arc=self.create_arc(cx-r,cy-r,cx+r,cy+r,start=-30+90,extent=-1,
                                   outline=self._color,style="arc",width=4)
        self._val_text=self.create_text(cx,cy-4,text="0",fill=self._color,
                                         font=(C["font_hud"],16,"bold"))
        self._unit_text=self.create_text(cx,cy+14,text=self._unit,fill=C["dim"],
                                          font=(C["font_hud"],8))
        self.create_text(cx,self.SIZE+10,text=self._label,fill=C["mid"],
                         font=(C["font_hud"],9,"bold"))

    def update(self, value: float):
        self._display+=(value-self._display)*0.2
        cx=cy=self.SIZE//2; r=cx-10
        frac=max(0.0,min(1.0,self._display/self._max))
        extent=-frac*300
        col=C["amber"] if (self._low and self._display<self._low) else \
            (C["red"] if (self._high and self._display>self._high) else self._color)
        self.itemconfig(self._arc,extent=extent,outline=col)
        self.coords(self._arc,cx-r,cy-r,cx+r,cy+r)
        self.itemconfig(self._val_text,text=f"{value:.0f}",fill=col)


# ═══════════════════════════════════════════════════════════════════════════════
# LOG PANEL
# ═══════════════════════════════════════════════════════════════════════════════

class DoorLockLog(tk.Frame):
    TAG_STYLES = {
        "normal": "#00CFFF", "warn": "#FFB300", "error": "#FF5555",
        "oracle": "#FFE044", "system": "#44CFFF",
        "rx":     "#80E8FF", "tx":    "#B0F0FF", "dim":   "#1A3A50",
    }
    def __init__(self, parent, title="", **kw):
        super().__init__(parent, bg=C["bg2"], **kw)
        hdr=tk.Frame(self,bg=C["bg2"],pady=3); hdr.pack(fill="x")
        tk.Label(hdr,text=title,bg=C["bg2"],fg=C["cyan_dim"],
                 font=(C["font_hud"],10,"bold")).pack(side="left",padx=8)
        self._count_var=tk.StringVar(value="0")
        tk.Label(hdr,textvariable=self._count_var,bg=C["bg2"],fg=C["dim"],
                 font=(C["font_hud"],9)).pack(side="right",padx=8)
        border=tk.Frame(self,bg=C["border"],padx=1,pady=1)
        border.pack(fill="both",expand=True,padx=4,pady=(0,4))
        self._text=tk.Text(border,bg=C["bg"],fg=C["cyan_dim"],
                           insertbackground=C["cyan"],
                           font=(C["font_hud"],12),wrap="none",bd=0,
                           relief="flat",width=1,
                           selectbackground=C["border_hi"],
                           selectforeground=C["white"],spacing1=1,spacing3=1)
        sb=tk.Scrollbar(border,orient="vertical",command=self._text.yview,
                        bg=C["bg3"],troughcolor=C["bg2"],width=8)
        self._text.configure(yscrollcommand=sb.set)
        for tag,col in self.TAG_STYLES.items():
            self._text.tag_config(tag,foreground=col)
        sb.pack(side="right",fill="y")
        self._text.pack(side="left",fill="both",expand=True)
        self._count=0

    def _classify(self, msg):
        m=msg.upper()
        if any(x in m for x in ("[ERR]","FAULT","CRASH")): return "error"
        if any(x in m for x in ("[WARN]","VULN","OVERFLOW")): return "warn"
        if any(x in m for x in ("[SYSTEM]","[INIT]")): return "system"
        if "[TX]" in m: return "tx"
        if "[RX]" in m: return "rx"
        return "normal"

    def append(self, msg, tag=None):
        ts=time.strftime("%H:%M:%S"); line=f"[{ts}]  {msg}\n"
        tag=tag or self._classify(msg)
        self._text.config(state="normal")
        self._text.insert("end",line,(tag,))
        self._text.config(state="disabled")
        self._text.see("end")
        self._count+=1; self._count_var.set(f"{self._count} msgs")

    def clear(self):
        self._text.config(state="normal"); self._text.delete("1.0","end")
        self._text.config(state="disabled"); self._count=0; self._count_var.set("0")


# ═══════════════════════════════════════════════════════════════════════════════
# LED + BADGE
# ═══════════════════════════════════════════════════════════════════════════════

class LED(tk.Canvas):
    def __init__(self, parent, color=C["cyan"], size=12, **kw):
        super().__init__(parent,width=size+8,height=size+8,
                         bg=C["bg"],bd=0,highlightthickness=0,**kw)
        self._color=color; self._size=size; self._glow=0.0; self._phase=0.0
        cx=cy=(size+8)//2; r=size//2
        self._halo=self.create_oval(cx-r-3,cy-r-3,cx+r+3,cy+r+3,fill="",outline="",width=0)
        self._core=self.create_oval(cx-r,cy-r,cx+r,cy+r,fill=color,
                                    outline=self._dim(color,0.5),width=1)
    @staticmethod
    def _dim(h,f):
        h=h.lstrip("#"); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        return "#{:02x}{:02x}{:02x}".format(int(r*f),int(g*f),int(b*f))
    @staticmethod
    def _blend(ha,hb,t):
        def p(h): h=h.lstrip("#"); return int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        ar,ag,ab=p(ha); br,bg,bb=p(hb)
        return "#{:02x}{:02x}{:02x}".format(int(ar+(br-ar)*t),int(ag+(bg-ag)*t),int(ab+(bb-ab)*t))
    def _redraw(self):
        cc=self._blend(self._dim(self._color,0.3),self._color,self._glow)
        hc=self._dim(self._color,self._glow*0.3)
        oc=self._dim(self._color,0.6)
        self.itemconfig(self._core,fill=cc,outline=oc)
        self.itemconfig(self._halo,fill=hc)
    def pulse(self,speed=0.07):
        self._phase=(self._phase+speed)%(2*math.pi)
        self._glow=0.5+0.5*math.sin(self._phase); self._redraw()
    def blink_once(self,on_ms=80):
        self._glow=1.0; self._redraw()
        self.after(on_ms,lambda:(setattr(self,'_glow',0.1),self._redraw()))


class TeleBadge(tk.Frame):
    def __init__(self, parent, label, initial="—", color=None, **kw):
        super().__init__(parent, bg=C["bg3"], padx=10, pady=4, **kw)
        self._color=color or C["cyan"]
        tk.Frame(self, bg=self._color, height=2).pack(fill="x")
        self._var=tk.StringVar(value=initial)
        self._lbl=tk.Label(self,textvariable=self._var,bg=C["bg3"],fg=self._color,
                           font=(C["font_hud"],14,"bold")); self._lbl.pack()
        tk.Label(self,text=label,bg=C["bg3"],fg=C["dim"],
                 font=(C["font_hud"],8)).pack()
    def set(self, value, color=None):
        self._var.set(value)
        if color: self._color=color; self._lbl.config(fg=color)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN GUI
# ═══════════════════════════════════════════════════════════════════════════════

class DoorLockGUI:

    def __init__(self, root):
        self.root = root
        self.root.title("🔒 Door Lock ECU Simulator  ·  CAN ID 0x19B")
        self.root.geometry("1680x980")
        self.root.configure(bg=C["bg"])
        self.root.resizable(True, True)

        self._build_ui()

        self.ecu = DoorLockECU(self.log_uds, self.log_oracle)
        if VULN_AVAILABLE:
            try:
                self.ecu.cfg = VulnerabilityConfig(
                    "./doorlock_vulns.json", self.log_uds, self.log_oracle)
                if self.ecu.cfg.load():
                    self.ecu.vuln_engine = VulnerabilityEngine(
                        self.ecu.cfg, self.ecu.state, self.log_uds, self.log_oracle)
                    self._btn_unload.config(state="normal", fg=C["amber"])
            except Exception:
                pass

        self.ecu_thread = threading.Thread(target=self._ecu_loop, daemon=True)
        self.ecu_thread.start()

        self.raw_thread = threading.Thread(target=self._raw_sniffer_loop, daemon=True)
        self.raw_thread.start()

        self.anim = Animator(self.root)
        self._start_animations()
        self.root.after(200, self._poll)

    # ------------------------------------------------------------------
    def _ecu_loop(self):
        self.ecu.start()
        if ISOTP_AVAILABLE:
            try:
                tp = ISOTPServer(INTERFACE, ECU_RX_ID, ECU_TX_ID, self.log_uds)
                while self.ecu.running:
                    tp.process()
                    if tp.available():
                        req  = tp.recv()
                        resp = self.ecu.handle_request(req)
                        if resp:
                            tp.send(resp)
                    time.sleep(0.002)
            except Exception as exc:
                self.log_uds(f"[SYSTEM] ISO-TP unavailable: {exc}")

    # ------------------------------------------------------------------
    def _raw_sniffer_loop(self):
        """Listen on vcan0 and log every raw CAN frame to the Raw Frame panel."""
        try:
            bus = can.Bus(INTERFACE, bustype="socketcan")
            self.log_raw(f"[SYSTEM] Raw sniffer active on {INTERFACE}")
            while self.ecu.running:
                msg = bus.recv(timeout=0.5)
                if msg is None:
                    continue
                direction = "RX" if msg.arbitration_id == ECU_RX_ID else \
                            "TX" if msg.arbitration_id == ECU_TX_ID else "BUS"
                payload   = msg.data.hex(" ").upper()
                fuzz_hit  = msg.arbitration_id == ECU_RX_ID
                tag_str   = " ◄ FUZZ" if fuzz_hit else ""
                log_line  = (f"[{direction}] ID=0x{msg.arbitration_id:03X} "
                             f"DLC={msg.dlc}  {payload}{tag_str}")
                self.root.after(0, lambda l=log_line, fz=fuzz_hit:
                                self._append_raw(l, fz))
                if fuzz_hit:
                    self.ecu.state.fuzz_frame_count += 1
            bus.shutdown()
        except Exception as exc:
            self.root.after(0, lambda: self.log_raw(
                f"[SYSTEM] Raw sniffer unavailable: {exc}"))

    def log_raw(self, msg):
        self.root.after(0, lambda: self._append_raw(msg, False))

    def _append_raw(self, msg, is_fuzz=False):
        tag = "warn" if is_fuzz else "normal"
        self.log_raw_panel.append(msg, tag=tag)
        if is_fuzz:
            self.led_warn.blink_once(80)

    # ------------------------------------------------------------------
    def _build_ui(self):
        self._build_title_bar()
        self._build_centre()
        self._build_log_panels()

    def _build_title_bar(self):
        bar = tk.Frame(self.root, bg=C["bg"], pady=5)
        bar.pack(fill="x", padx=10)
        left = tk.Frame(bar, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text="🔒", bg=C["bg"], fg=C["cyan"],
                 font=(C["font_hud"], 16)).pack(side="left")
        tk.Label(left, text="  DOOR LOCK ECU SIMULATOR",
                 bg=C["bg"], fg=C["cyan"],
                 font=(C["font_hud"], 14, "bold")).pack(side="left")
        tk.Label(left, text="  CAN ID 0x19B  ·  DIAGNOSTIC CONSOLE v1.0",
                 bg=C["bg"], fg=C["mid"],
                 font=(C["font_hud"], 10)).pack(side="left", pady=2)

        right = tk.Frame(bar, bg=C["bg"])
        right.pack(side="right")
        self._make_btn(right, "LOAD JSON",   self._load_vuln_json).pack(side="left", padx=3)
        self._btn_unload = self._make_btn(right, "UNLOAD JSON", self._unload_vuln_json,
                                          color=C["amber_dim"])
        self._btn_unload.pack(side="left", padx=3)
        self._btn_unload.config(state="disabled")
        self._make_btn(right, "EXPORT LOG",  self._export_log).pack(side="left", padx=3)
        self._make_btn(right, "CLEAR",       self._clear_logs, color=C["dim"]).pack(side="left", padx=3)
        self._make_btn(right, "EXIT",        self._exit, color=C["red"]).pack(side="left", padx=3)

        tk.Frame(self.root, bg=C["cyan_lo"], height=1).pack(fill="x", padx=10)

    def _build_centre(self):
        centre = tk.Frame(self.root, bg=C["bg"])
        centre.pack(fill="x", padx=10, pady=4)

        left_col = tk.Frame(centre, bg=C["bg"], width=220)
        left_col.pack(side="left", fill="y", padx=(0, 8))
        left_col.pack_propagate(False)
        self._build_status_column(left_col)

        mid = tk.Frame(centre, bg=C["bg"])
        mid.pack(side="left", padx=8)
        self.panel = DoorLockPanel(mid)
        self.panel.pack()

        right_col = tk.Frame(centre, bg=C["bg"])
        right_col.pack(side="left", fill="y", padx=(8, 0))
        self._build_right_column(right_col)

    def _build_status_column(self, parent):
        tk.Label(parent, text="◈ ECU STATUS", bg=C["bg"], fg=C["dim"],
                 font=(C["font_hud"], 9, "bold")).pack(anchor="w", padx=6, pady=(8,0))
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=3)

        led_f = tk.Frame(parent, bg=C["bg"])
        led_f.pack(fill="x", padx=4, pady=4)
        self.led_ecu  = self._led_block(led_f, "ECU",    C["cyan"])
        self.led_conn = self._led_block(led_f, "CAN BUS",C["teal"])
        self.led_proc = self._led_block(led_f, "UDS",    C["blue"])
        self.led_warn = self._led_block(led_f, "WARN",   C["red"])
        for l in (self.led_ecu, self.led_conn, self.led_proc, self.led_warn):
            l.pack(side="left", padx=5)

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=4)

        sess_f = tk.Frame(parent, bg=C["bg3"], padx=8, pady=6)
        sess_f.pack(fill="x", padx=6, pady=2)
        tk.Label(sess_f, text="SESSION", bg=C["bg3"], fg=C["dim"],
                 font=(C["font_hud"], 8)).pack()
        self._session_var = tk.StringVar(value="DEFAULT")
        self._session_lbl = tk.Label(sess_f, textvariable=self._session_var,
                                     bg=C["bg3"], fg=C["cyan"],
                                     font=(C["font_hud"], 13, "bold"))
        self._session_lbl.pack()

        sec_f = tk.Frame(parent, bg=C["bg3"], padx=8, pady=6)
        sec_f.pack(fill="x", padx=6, pady=2)
        tk.Label(sec_f, text="SECURITY", bg=C["bg3"], fg=C["dim"],
                 font=(C["font_hud"], 8)).pack()
        self._security_var = tk.StringVar(value="LOCKED")
        self._security_lbl = tk.Label(sec_f, textvariable=self._security_var,
                                      bg=C["bg3"], fg=C["red"],
                                      font=(C["font_hud"], 13, "bold"))
        self._security_lbl.pack()

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=4)

        row1 = tk.Frame(parent, bg=C["bg"])
        row1.pack(fill="x", padx=4)
        self.b_mode = TeleBadge(row1, "LOCK STATE", "LOCKED", color=C["red"])
        self.b_cmds = TeleBadge(row1, "COMMANDS",   "0",      color=C["teal"])
        self.b_mode.pack(side="left", padx=2, pady=2)
        self.b_cmds.pack(side="left", padx=2, pady=2)

        row2 = tk.Frame(parent, bg=C["bg"])
        row2.pack(fill="x", padx=4)
        self.b_fuzz  = TeleBadge(row2, "FUZZ FRAMES","0",  color=C["mid"])
        self.b_fault = TeleBadge(row2, "FAULT",      "OK", color=C["mid"])
        self.b_fuzz.pack(side="left", padx=2, pady=2)
        self.b_fault.pack(side="left", padx=2, pady=2)

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=4)
        self.b_auth = TeleBadge(parent, "AUTH ATTEMPTS", "0/3", color=C["mid"])
        self.b_auth.pack(padx=6, pady=2)

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=4)
        self._clock_var = tk.StringVar(value="00:00:00")
        tk.Label(parent, textvariable=self._clock_var,
                 bg=C["bg"], fg=C["cyan_dim"],
                 font=(C["font_hud"], 20, "bold")).pack(pady=4)

    def _build_right_column(self, parent):
        tk.Label(parent, text="◈ TELEMETRY", bg=C["bg"], fg=C["dim"],
                 font=(C["font_hud"], 9, "bold")).pack(anchor="w", padx=6, pady=(8,0))
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=3)

        gauges = tk.Frame(parent, bg=C["bg"])
        gauges.pack(padx=4, pady=4)
        self.gauge_voltage = MiniGauge(gauges, label="VOLTAGE",  unit="mV",
                                        max_val=15000, low_warn=11000, color=C["cyan"])
        self.gauge_cmds    = MiniGauge(gauges, label="CMD COUNT",unit="",
                                        max_val=1000, color=C["teal"])
        self.gauge_fuzz    = MiniGauge(gauges, label="FUZZ",     unit="pkts",
                                        max_val=500,  high_warn=200, color=C["blue"])
        self.gauge_voltage.grid(row=0, column=0, padx=4)
        self.gauge_cmds.grid(   row=0, column=1, padx=4)
        self.gauge_fuzz.grid(   row=0, column=2, padx=4)

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=6)

        warn_f = tk.Frame(parent, bg=C["bg"])
        warn_f.pack(padx=4, pady=2)
        tk.Label(warn_f, text="DOOR STATUS", bg=C["bg"], fg=C["dim"],
                 font=(C["font_hud"], 8)).pack()
        icons_f = tk.Frame(warn_f, bg=C["bg"])
        icons_f.pack()

        def door_icon(txt, col_on, col_off):
            lbl = tk.Label(icons_f, text=txt, bg=C["bg"],
                           fg=col_off, font=(C["font_hud"], 10, "bold"))
            lbl.pack(side="left", padx=4, pady=2)
            return lbl, col_on, col_off

        self._wi_fl,    *self._wi_fl_c    = door_icon("FL",    C["green"], C["border_hi"])
        self._wi_fr,    *self._wi_fr_c    = door_icon("FR",    C["green"], C["border_hi"])
        self._wi_rl,    *self._wi_rl_c    = door_icon("RL",    C["green"], C["border_hi"])
        self._wi_rr,    *self._wi_rr_c    = door_icon("RR",    C["green"], C["border_hi"])
        self._wi_trunk, *self._wi_trunk_c = door_icon("TRUNK", C["amber"], C["border_hi"])

        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=6, pady=6)

        env_f = tk.Frame(parent, bg=C["bg"])
        env_f.pack(fill="x", padx=4)
        self.b_child = TeleBadge(env_f, "CHILD LOCK", "RL+RR", color=C["cyan"])
        self.b_volt  = TeleBadge(env_f, "VOLTAGE mV", "12500", color=C["blue"])
        self.b_child.pack(side="left", padx=2)
        self.b_volt.pack(side="left", padx=2)

    def _build_log_panels(self):
        panels = tk.Frame(self.root, bg=C["bg"])
        panels.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        panels.columnconfigure(0, weight=1, uniform="lc")
        panels.columnconfigure(1, weight=1, uniform="lc")
        panels.columnconfigure(2, weight=1, uniform="lc")
        panels.rowconfigure(0, weight=1)

        self.log_uds_panel    = DoorLockLog(panels, title="◈ UDS DIAGNOSTIC LOG")
        self.log_raw_panel    = DoorLockLog(panels, title="◈ RAW FRAME LOG")
        self.log_oracle_panel = DoorLockLog(panels, title="◈ ORACLE / VULN LOG")
        self.log_uds_panel.grid(   row=0, column=0, sticky="nsew", padx=(0,3))
        self.log_raw_panel.grid(   row=0, column=1, sticky="nsew", padx=3)
        self.log_oracle_panel.grid(row=0, column=2, sticky="nsew", padx=(3,0))

    # ------------------------------------------------------------------
    def _led_block(self, parent, label, color):
        f = tk.Frame(parent, bg=C["bg"])
        led = LED(f, color=color, size=12)
        led.pack()
        tk.Label(f, text=label, bg=C["bg"], fg=C["dim"],
                 font=(C["font_hud"], 7)).pack()
        return led

    def _make_btn(self, parent, text, cmd, color=None):
        col = color or C["cyan_dim"]
        btn = tk.Button(parent, text=text, command=cmd,
                        bg=C["bg3"], fg=col, activebackground=C["border_hi"],
                        activeforeground=col, relief="flat", bd=0,
                        padx=10, pady=4, font=(C["font_hud"], 9, "bold"), cursor="hand2")
        btn.bind("<Enter>", lambda e, b=btn, c=col: b.config(fg=C["white"], bg=C["border_hi"]))
        btn.bind("<Leave>", lambda e, b=btn, c=col: b.config(fg=c, bg=C["bg3"]))
        return btn

    # ------------------------------------------------------------------
    def _start_animations(self):
        self.anim.repeat(30,   lambda: self.led_ecu.pulse(0.05))
        self.anim.repeat(40,   lambda: self.led_conn.pulse(0.04))
        self.anim.repeat(1000, self._update_clock)
        self.anim.repeat(50,   self._update_panel)

    def _update_clock(self):
        self._clock_var.set(time.strftime("%H:%M:%S"))

    def _update_panel(self):
        st = self.ecu.state
        self.panel.update(
            fl=st.door_fl, fr=st.door_fr, rl=st.door_rl, rr=st.door_rr,
            trunk=st.trunk_open, mode=st.active_mode,
            cmd_count=st.cmd_count, fuzz_frames=st.fuzz_frame_count)
        self.gauge_voltage.update(st.lock_voltage)
        self.gauge_cmds.update(st.cmd_count)
        self.gauge_fuzz.update(st.fuzz_frame_count)

    # ------------------------------------------------------------------
    def log_uds(self, msg):
        self.root.after(0, lambda: self._append_uds(msg))
    def _append_uds(self, msg):
        self.log_uds_panel.append(msg); self.led_proc.blink_once(150)
    def log_oracle(self, msg):
        self.root.after(0, lambda: self._append_oracle(msg))
    def _append_oracle(self, msg):
        self.log_oracle_panel.append(msg, tag="oracle"); self.led_warn.blink_once(300)

    # ------------------------------------------------------------------
    def _poll(self):
        try:
            st = self.ecu.state
            sess_names = {0x01:"DEFAULT", 0x02:"PROGRAMMING", 0x03:"EXTENDED"}
            sess_cols  = {0x01:C["cyan"], 0x02:C["red"], 0x03:C["amber"]}
            self._session_var.set(sess_names.get(st.session, f"0x{st.session:02X}"))
            self._session_lbl.config(fg=sess_cols.get(st.session, C["cyan"]))

            if st.security_level > 0:
                self._security_var.set(f"UNLOCKED L{st.security_level}")
                self._security_lbl.config(fg=C["green"])
            else:
                self._security_var.set("LOCKED")
                self._security_lbl.config(fg=C["red"])

            mode_cols = {"LOCKED":C["red"],"UNLOCKED":C["green"],
                         "DRIVER":C["cyan"],"PARTIAL":C["amber"],"TRUNK":C["amber"]}
            self.b_mode.set(st.active_mode, color=mode_cols.get(st.active_mode, C["mid"]))
            self.b_cmds.set(str(st.cmd_count))
            fuzz_col = C["red"] if st.fuzz_frame_count > 100 else C["mid"]
            self.b_fuzz.set(str(st.fuzz_frame_count), color=fuzz_col)
            fault_names = {0:"OK",1:"FL MOTOR",2:"FR MOTOR",3:"RL MOTOR",4:"RR MOTOR",5:"TRUNK"}
            self.b_fault.set(fault_names.get(st.fault_code,"ERR"),
                             color=C["red"] if st.fault_code else C["mid"])

            col = C["red"] if st.auth_failures_ram >= st.max_attempts else \
                  (C["amber"] if st.auth_failures_ram > 0 else C["mid"])
            self.b_auth.set(f"{st.auth_failures_ram}/{st.max_attempts}", color=col)

            child_str = "RL" if st.child_rl and not st.child_rr else \
                        "RR" if st.child_rr and not st.child_rl else \
                        "RL+RR" if (st.child_rl and st.child_rr) else "OFF"
            self.b_child.set(child_str)
            self.b_volt.set(str(st.lock_voltage))

            self._set_warn(self._wi_fl,    self._wi_fl_c,    st.door_fl)
            self._set_warn(self._wi_fr,    self._wi_fr_c,    st.door_fr)
            self._set_warn(self._wi_rl,    self._wi_rl_c,    st.door_rl)
            self._set_warn(self._wi_rr,    self._wi_rr_c,    st.door_rr)
            self._set_warn(self._wi_trunk, self._wi_trunk_c, st.trunk_open)

        except Exception as exc:
            _elog.warning(f"[GUI] Poll error: {exc}")

        self.root.after(200, self._poll)

    @staticmethod
    def _set_warn(lbl, cols, active):
        lbl.config(fg=cols[0] if active else cols[1])

    # ------------------------------------------------------------------
    def _load_vuln_json(self):
        path = filedialog.askopenfilename(
            title="Select Vulnerability JSON",
            filetypes=(("JSON files","*.json"),("All files","*.*")))
        if not path or not VULN_AVAILABLE: return
        try:
            self.ecu.cfg = VulnerabilityConfig(path, self.log_uds, self.log_oracle)
            if self.ecu.cfg.load():
                self.ecu.vuln_engine = VulnerabilityEngine(
                    self.ecu.cfg, self.ecu.state, self.log_uds, self.log_oracle)
                self.log_uds(f"[SYSTEM] Loaded {len(self.ecu.cfg.vulnerabilities)} vulnerabilities")
                self._btn_unload.config(fg=C["amber"], state="normal")
        except Exception as exc:
            self.log_uds(f"[SYSTEM][ERR] Load failed: {exc}")

    def _unload_vuln_json(self):
        if not self.ecu.cfg: return
        count = len(self.ecu.cfg.vulnerabilities)
        self.ecu.cfg.unload(); self.ecu.vuln_engine = None
        self.log_uds(f"[SYSTEM] Unloaded {count} vulnerabilities")
        self._btn_unload.config(fg=C["amber_dim"], state="disabled")

    def _export_log(self):
        dest = filedialog.asksaveasfilename(
            title="Export Log", defaultextension=".log",
            filetypes=(("Log files","*.log"),("All","*.*")),
            initialfile="doorlock_ecu_export.log")
        if not dest: return
        try:
            content = self.log_uds_panel._text.get("1.0","end")
            with open(dest,"w") as f: f.write(content)
            self.log_uds(f"[SYSTEM] Exported → {dest}")
        except Exception as exc:
            self.log_uds(f"[SYSTEM][ERR] Export failed: {exc}")

    def _clear_logs(self):
        self.log_uds_panel.clear(); self.log_raw_panel.clear(); self.log_oracle_panel.clear()

    def _exit(self):
        self.anim.stop(); self.ecu.stop(); self.root.destroy()
