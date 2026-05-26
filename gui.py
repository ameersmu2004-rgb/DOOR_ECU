# gui.py  —  Enhanced ECU Simulator GUI
# Aesthetic: Industrial phosphor-terminal × automotive diagnostic telemetry
# Animations: fully Tkinter-native, root.after() driven — zero thread blocking

import time
import os
import math
import threading
import colorsys
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import can

from virtual_ecu import VirtualECU
from ecu_state import ECUState
from vulnerability_engine import VulnerabilityEngine

try:
    from logger import ECULogger
    _elog = ECULogger("gui")
except ImportError:
    class _FakeLog:
        def __getattr__(self, _): return lambda *a, **k: None
    _elog = _FakeLog()

# ─────────────────────────── PALETTE ──────────────────────────────────────────
# Readability pass: backgrounds lifted to #1E1E1E range, text boosted to near-
# white, accent colours brightened so they pop on the lighter base.
C = {
    "bg":           "#1E1E1E",   # VS Code-style dark grey — easy on the eyes
    "bg2":          "#252526",   # panel background (slightly lighter)
    "bg3":          "#2D2D30",   # widget / button surface
    "border":       "#3C3C3C",   # visible but unobtrusive border
    "border_hi":    "#555558",   # hover / highlight border
    "phosphor":     "#4EC994",   # muted spring-green — readable on #1E1E1E
    "phosphor_dim": "#2A7A58",   # dimmed state
    "phosphor_lo":  "#1A4A38",   # very dim (bar graph floor)
    "amber":        "#FFB84D",   # warm amber — higher contrast than pure orange
    "amber_dim":    "#8A6020",
    "red":          "#F44747",   # VS Code error red — vivid but not harsh
    "red_dim":      "#7A1A1A",
    "blue":         "#4FC1FF",   # DEFAULT session — bright sky blue
    "blue_dim":     "#1A4A70",
    "yellow":       "#FFD866",   # EXTENDED session — warm yellow
    "yellow_dim":   "#6A5010",
    "cyan":         "#56D8FF",   # accent / processing
    "cyan_dim":     "#1A5060",
    "white":        "#F0F0F0",   # primary log text — true near-white
    "mid":          "#9DABB8",   # secondary labels — clearly readable
    "text_dim":     "#6A7B88",   # tertiary / disabled text
    "font_mono":    "Consolas",  # Consolas: clean monospace, great at any size
    "font_hud":     "Consolas",
}

# Session colour maps
SESSION_PALETTE = {
    ECUState.SESSION_DEFAULT:     {"name": "DEFAULT",     "hi": C["blue"],   "lo": C["blue_dim"],   "led": "#2090FF"},
    ECUState.SESSION_PROGRAMMING: {"name": "PROGRAMMING", "hi": C["red"],    "lo": C["red_dim"],    "led": "#FF3040"},
    ECUState.SESSION_EXTENDED:    {"name": "EXTENDED",    "hi": C["yellow"], "lo": C["yellow_dim"], "led": "#FFD700"},
}

# ─────────────────────────── ANIMATION ENGINE ─────────────────────────────────

class Animator:
    """Lightweight scheduler that drives all canvas animations via root.after()."""

    def __init__(self, root: tk.Tk):
        self.root   = root
        self._tasks: list[dict] = []
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
        self.root.after(16, self._tick)   # ~60 fps driver

    def repeat(self, interval_ms: float, fn) -> dict:
        """Schedule *fn* every interval_ms milliseconds. fn returns False to stop."""
        task = {"fn": fn, "interval": interval_ms / 1000, "next": time.monotonic()}
        self._tasks.append(task)
        return task

    def once(self, delay_ms: float, fn):
        """Run *fn* once after delay_ms."""
        def _wrap():
            fn()
            return False
        self.repeat(delay_ms, _wrap)

    def cancel(self, task: dict):
        if task in self._tasks:
            self._tasks.remove(task)

    def stop(self):
        self._running = False


# ─────────────────────────── CANVAS WIDGETS ───────────────────────────────────

class LED(tk.Canvas):
    """Blinking LED indicator with glow halo."""

    def __init__(self, parent, color: str = "#00FF7F", size: int = 14, **kw):
        super().__init__(parent, width=size + 8, height=size + 8,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._color   = color
        self._size    = size
        self._on      = True
        self._glow    = 0.0   # 0..1 glow intensity
        self._phase   = 0.0
        cx = cy = (size + 8) // 2
        r  = size // 2
        rg = r + 3
        # halo (glow ring)
        self._halo = self.create_oval(cx-rg, cy-rg, cx+rg, cy+rg,
                                      fill="", outline="", width=0)
        # core circle
        self._core = self.create_oval(cx-r, cy-r, cx+r, cy+r,
                                      fill=color, outline=self._dim(color, 0.5), width=1)

    @staticmethod
    def _dim(hex_color: str, factor: float) -> str:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return "#{:02x}{:02x}{:02x}".format(int(r*factor), int(g*factor), int(b*factor))

    @staticmethod
    def _blend(hex_a: str, hex_b: str, t: float) -> str:
        def parse(h): h=h.lstrip("#"); return int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        ar,ag,ab = parse(hex_a); br,bg,bb = parse(hex_b)
        return "#{:02x}{:02x}{:02x}".format(int(ar+(br-ar)*t), int(ag+(bg-ag)*t), int(ab+(bb-ab)*t))

    def set_color(self, color: str):
        self._color = color
        self._redraw()

    def _redraw(self):
        if self._on:
            core_col  = self._blend(self._dim(self._color, 0.3), self._color, self._glow)
            halo_col  = self._dim(self._color, self._glow * 0.35)
            out_col   = self._dim(self._color, 0.6)
        else:
            core_col  = self._dim(self._color, 0.12)
            halo_col  = ""
            out_col   = self._dim(self._color, 0.2)
        self.itemconfig(self._core, fill=core_col, outline=out_col)
        self.itemconfig(self._halo, fill=halo_col, outline="")

    def pulse(self, speed: float = 0.06):
        """Soft breathing pulse. Call from Animator."""
        self._phase = (self._phase + speed) % (2 * math.pi)
        self._glow  = 0.5 + 0.5 * math.sin(self._phase)
        self._on    = True
        self._redraw()

    def blink_once(self, on_ms: int = 80):
        """Flash bright for on_ms then return to dim."""
        self._on   = True
        self._glow = 1.0
        self._redraw()
        def _off():
            self._on   = True
            self._glow = 0.15
            self._redraw()
        self.after(on_ms, _off)

    def set_state(self, on: bool):
        self._on   = on
        self._glow = 1.0 if on else 0.0
        self._redraw()


class Spinner(tk.Canvas):
    """Rotating arc spinner indicating active processing."""

    def __init__(self, parent, size: int = 22, color: str = "#00FF7F", **kw):
        super().__init__(parent, width=size, height=size,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._size    = size
        self._color   = color
        self._angle   = 0
        self._visible = False
        cx = cy = size // 2
        r  = cx - 3
        # track ring
        self._track = self.create_arc(cx-r, cy-r, cx+r, cy+r,
                                      start=0, extent=359,
                                      outline=self._dim(color, 0.15), width=2, style="arc")
        # spinning arc
        self._arc = self.create_arc(cx-r, cy-r, cx+r, cy+r,
                                    start=0, extent=90,
                                    outline=color, width=2, style="arc")
        self.itemconfig(self._arc, state="hidden")
        self.itemconfig(self._track, state="hidden")

    @staticmethod
    def _dim(hex_color: str, f: float) -> str:
        h=hex_color.lstrip("#"); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        return "#{:02x}{:02x}{:02x}".format(int(r*f),int(g*f),int(b*f))

    def spin(self):
        """Advance one animation frame. Call from Animator."""
        if not self._visible:
            return
        self._angle = (self._angle - 12) % 360
        tail_len = 110 + 30 * math.sin(math.radians(self._angle * 2))
        self.itemconfig(self._arc, start=self._angle, extent=int(tail_len))

    def show(self):
        self._visible = True
        self.itemconfig(self._arc,   state="normal")
        self.itemconfig(self._track, state="normal")

    def hide(self):
        self._visible = False
        self.itemconfig(self._arc,   state="hidden")
        self.itemconfig(self._track, state="hidden")


class FlowArrow(tk.Canvas):
    """Animated tester → ECU → response message-flow diagram."""

    W, H = 520, 46

    def __init__(self, parent, **kw):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=C["bg2"], bd=0, highlightthickness=0, **kw)
        self._particles: list[dict] = []
        self._active    = False
        self._direction = 1   # 1 = left→right (TX),  -1 = right→left (RX response)
        self._color     = C["phosphor"]
        self._build_static()

    def _build_static(self):
        W, H = self.W, self.H
        mid   = H // 2
        # Labels
        self.create_text(10, mid, text="TESTER", fill=C["mid"],
                         font=(C["font_hud"], 9, "bold"), anchor="w")
        self.create_text(W-10, mid, text="ECU", fill=C["mid"],
                         font=(C["font_hud"], 9, "bold"), anchor="e")
        # Static track line
        lx1, lx2 = 60, W - 50
        self._track_line = self.create_line(lx1, mid, lx2, mid,
                                            fill=C["border_hi"], width=1, dash=(4,4))
        # Node circles
        self._node_t = self.create_oval(52,mid-7, 68,mid+7, fill=C["bg3"], outline=C["border_hi"], width=1)
        self._node_e = self.create_oval(W-67,mid-7, W-51,mid+7, fill=C["bg3"], outline=C["border_hi"], width=1)
        # Direction arrow head (repositioned dynamically)
        self._arrowhead = self.create_polygon(0,0,0,0,0,0, fill=C["phosphor"], state="hidden")
        # Particle dots
        self._dots = [
            self.create_oval(0,0,0,0, fill=C["phosphor"], outline="", state="hidden")
            for _ in range(6)
        ]
        # Direction label
        self._dir_label = self.create_text(W//2, 8, text="", fill=C["phosphor_dim"],
                                           font=(C["font_hud"], 9), anchor="center")

    def _update_particles(self):
        W, H = self.W, self.H
        mid   = H // 2
        lx1, lx2 = 62, W - 52
        track_len = lx2 - lx1
        still_alive = []
        for p in self._particles:
            p["t"] += p["speed"]
            if p["t"] > 1.2:
                self.itemconfig(p["dot"], state="hidden")
                continue
            t_clamped = max(0, min(1, p["t"]))
            x  = lx1 + track_len * t_clamped if self._direction == 1 else lx2 - track_len * t_clamped
            alpha = 1.0 - abs(p["t"] - 0.5) * 2
            alpha = max(0, alpha)
            size = int(3 + 3 * alpha)
            col  = self._fade_color(self._color, alpha)
            self.coords(p["dot"], x-size, mid-size, x+size, mid+size)
            self.itemconfig(p["dot"], fill=col, state="normal")
            still_alive.append(p)
        self._particles = still_alive

    @staticmethod
    def _fade_color(hex_color: str, alpha: float) -> str:
        h=hex_color.lstrip("#"); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        bg_r,bg_g,bg_b=13,19,24
        return "#{:02x}{:02x}{:02x}".format(
            int(bg_r + (r-bg_r)*alpha), int(bg_g + (g-bg_g)*alpha), int(bg_b + (b-bg_b)*alpha))

    def tick(self):
        self._update_particles()

    def fire(self, direction: int = 1, color: str = None, label: str = ""):
        """Spawn a burst of particles. direction: 1=TX  -1=RX."""
        self._direction = direction
        self._color     = color or C["phosphor"]
        self.itemconfig(self._dir_label, text=label, fill=self._color)
        for i, dot in enumerate(self._dots):
            self._particles.append({
                "dot":   dot,
                "t":     -i * 0.07,
                "speed": 0.045 + i * 0.004,
            })


class BarGraph(tk.Canvas):
    """Mini live bar graph for CAN traffic density."""

    BARS, W, H = 30, 180, 36

    def __init__(self, parent, **kw):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=C["bg2"], bd=0, highlightthickness=0, **kw)
        self._values = [0] * self.BARS
        self._rects  = []
        bw = self.W / self.BARS
        for i in range(self.BARS):
            x0 = i * bw + 1
            x1 = x0 + bw - 2
            r  = self.create_rectangle(x0, self.H, x1, self.H, fill=C["phosphor_lo"], outline="")
            self._rects.append(r)

    def push(self, value: float):
        self._values.append(min(value, 1.0))
        if len(self._values) > self.BARS:
            self._values.pop(0)

    def redraw(self):
        for i, (rect, val) in enumerate(zip(self._rects, self._values)):
            h   = int(val * (self.H - 4)) + 2
            x0, _, x1, _ = self.coords(rect)
            self.coords(rect, x0, self.H - h, x1, self.H)
            # colour by intensity
            if val > 0.7:   col = C["amber"]
            elif val > 0.4: col = C["phosphor"]
            else:           col = C["phosphor_dim"]
            self.itemconfig(rect, fill=col)


# ─────────────────────────── STYLED LOG WIDGET ────────────────────────────────

class PhosphorLog(tk.Frame):
    """ScrolledText log panel with highlight-flash on new entries."""

    # ── Readability pass: all tag colours verified for WCAG AA contrast on #1E1E1E
    TAG_STYLES = {
        "normal":  "#C8E6C8",   # soft green — readable body text (was dim phosphor)
        "warn":    "#FFD080",   # warm amber — WARN / VULN lines
        "error":   "#F47C7C",   # desaturated red — avoids eye strain on dark bg
        "oracle":  "#FFE066",   # bright yellow — oracle / detection messages
        "system":  "#7ECFED",   # sky blue — [SYSTEM] / [CONFIG] lines
        "rx":      "#A8D8A8",   # pale green — incoming RX frames
        "tx":      "#B8F0C8",   # lighter green — outgoing TX frames
        "dim":     "#7A9A7A",   # muted green — low-priority debug
    }
    FLASH_COLOR = "#FFFFFF"
    FLASH_MS    = 140   # slightly longer flash so it registers at a glance

    # ── Font config — change these two constants to tune all log panels
    LOG_FONT_FAMILY = "Consolas"   # Consolas ships with Windows & most Linux
    LOG_FONT_SIZE   = 14           # 14 pt: clear at arm's length on any monitor

    def __init__(self, parent, title: str = "", **kw):
        super().__init__(parent, bg=C["bg2"], **kw)
        # header
        hdr = tk.Frame(self, bg=C["bg2"], pady=3)
        hdr.pack(fill="x")
        self._title_var = tk.StringVar(value=title)
        tk.Label(hdr, textvariable=self._title_var,
                 bg=C["bg2"], fg=C["mid"],
                 font=(C["font_hud"], 10, "bold")).pack(side="left", padx=8)
        self._count_var = tk.StringVar(value="0 msgs")
        tk.Label(hdr, textvariable=self._count_var,
                 bg=C["bg2"], fg=C["text_dim"],
                 font=(C["font_hud"], 9)).pack(side="right", padx=8)
        # canvas border
        border = tk.Frame(self, bg=C["border"], padx=1, pady=1)
        border.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._text = tk.Text(
            border,
            bg=C["bg"],
            fg="#C8E6C8",                          # default text colour (matches "normal" tag)
            insertbackground=C["phosphor"],
            font=(self.LOG_FONT_FAMILY, self.LOG_FONT_SIZE),
            wrap="none", bd=0, relief="flat",
            width=1,      # ← suppress natural-width demand; geometry manager owns sizing
            selectbackground=C["border_hi"],
            selectforeground=C["white"],
            spacing1=2,   # pixels above each line — adds breathing room
            spacing3=2,   # pixels below each line
        )
        sb = tk.Scrollbar(border, orient="vertical", command=self._text.yview,
                          bg=C["bg3"], troughcolor=C["bg2"], width=10)
        self._text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)

        # configure tags
        for tag, col in self.TAG_STYLES.items():
            self._text.tag_config(tag, foreground=col)
        self._text.tag_config("flash",    background=C["border_hi"], foreground=C["white"])
        self._text.tag_config("flash_hi", background="#2A3D2E",      foreground=C["white"])

        self._count  = 0
        self._parent = parent

    def _classify(self, msg: str) -> str:
        m = msg.upper()
        if any(x in m for x in ("[ERR]", "[FAILURE]", "CRASH", "FAULT")):
            return "error"
        if any(x in m for x in ("[WARN]", "[WRN]", "WARN", "VULN", "OVERFLOW")):
            return "warn"
        if "[SYSTEM]" in m or "[INIT]" in m or "[CONFIG]" in m:
            return "system"
        if "[TX]" in m:
            return "tx"
        if "[RX]" in m:
            return "rx"
        return "normal"

    def append(self, msg: str, tag: str = None):
        ts   = time.strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}\n"
        tag  = tag or self._classify(msg)

        self._text.config(state="normal")
        start = self._text.index("end-1c")
        self._text.insert("end", line, (tag,))
        end   = self._text.index("end-1c")

        # flash highlight — brief pale-green background so the new line pops
        flash_tag = f"fl_{self._count}"
        self._text.tag_config(flash_tag, background="#2E3D2E", foreground=C["white"])
        self._text.tag_add(flash_tag, start, end)
        self._text.after(self.FLASH_MS, lambda t=flash_tag: self._clear_flash(t))

        self._text.config(state="disabled")
        self._text.see("end")

        self._count += 1
        self._count_var.set(f"{self._count} msgs")

    def _clear_flash(self, tag: str):
        try:
            self._text.tag_delete(tag)
        except Exception:
            pass

    def clear(self):
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.config(state="disabled")
        self._count = 0
        self._count_var.set("0 msgs")


# ─────────────────────────── STAT BADGE ───────────────────────────────────────

class StatBadge(tk.Frame):
    """Glowing labelled value badge for the HUD bar."""

    def __init__(self, parent, label: str, initial: str = "—",
                 color: str = None, **kw):
        super().__init__(parent, bg=C["bg"], padx=8, pady=3, **kw)
        self._color  = color or C["mid"]
        tk.Label(self, text=label, bg=C["bg"], fg=C["text_dim"],
                 font=(C["font_hud"], 9)).pack()
        self._val_var = tk.StringVar(value=initial)
        self._val_lbl = tk.Label(self, textvariable=self._val_var,
                                 bg=C["bg"], fg=self._color,
                                 font=(C["font_hud"], 13, "bold"))
        self._val_lbl.pack()

    def set(self, value: str, color: str = None):
        self._val_var.set(value)
        if color:
            self._color = color
            self._val_lbl.config(fg=color)

    def flash(self):
        orig = self._color
        self._val_lbl.config(fg=C["white"])
        self.after(120, lambda: self._val_lbl.config(fg=orig))


# ─────────────────────────── COUNTER DISPLAY ──────────────────────────────────

class CounterBox(tk.Frame):
    """Two animated integer counters: CAN FRAMES and UDS REQUESTS."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["bg"], **kw)
        self._can_n   = self._make("CAN FRAMES",   C["phosphor"])
        self._uds_n   = self._make("UDS REQUESTS", C["cyan"])
        self.can_count  = 0
        self.uds_count  = 0

    def _make(self, label: str, color: str) -> dict:
        f = tk.Frame(self, bg=C["bg3"], padx=10, pady=4,
                     relief="flat", bd=0)
        f.pack(side="left", padx=3)
        tk.Frame(f, bg=color, height=3).pack(fill="x")
        v = tk.StringVar(value="0")
        tk.Label(f, textvariable=v, bg=C["bg3"], fg=color,
                 font=(C["font_hud"], 18, "bold")).pack()
        tk.Label(f, text=label, bg=C["bg3"], fg=C["mid"],
                 font=(C["font_hud"], 9)).pack()
        return {"var": v, "widget": f, "color": color}

    def bump_can(self):
        self.can_count += 1
        self._can_n["var"].set(str(self.can_count))
        self._flash(self._can_n)

    def bump_uds(self):
        self.uds_count += 1
        self._uds_n["var"].set(str(self.uds_count))
        self._flash(self._uds_n)

    def bump_vuln(self):
        pass   # VULN_HIT display removed; method kept so callers need no changes

    @staticmethod
    def _flash(d: dict):
        lbl = d["widget"].winfo_children()[1]   # the number label
        lbl.config(fg=C["white"])
        lbl.after(150, lambda: lbl.config(fg=d["color"]))


# ─────────────────────────── SESSION INDICATOR ────────────────────────────────

class SessionIndicator(tk.Canvas):
    """Animated session badge that morphs colour on state change."""

    W, H = 160, 38

    def __init__(self, parent, **kw):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._session = ECUState.SESSION_DEFAULT
        self._phase   = 0.0
        self._bg_rect = self.create_rectangle(2, 2, self.W-2, self.H-2,
                                              fill=C["blue_dim"], outline=C["blue"],
                                              width=1)
        self._label   = self.create_text(self.W//2, self.H//2 - 5,
                                         text="DEFAULT",
                                         fill=C["blue"], font=(C["font_hud"], 11, "bold"))
        self._sub     = self.create_text(self.W//2, self.H//2 + 9,
                                         text="SESSION", fill=C["mid"],
                                         font=(C["font_hud"], 8))

    def tick(self):
        pal = SESSION_PALETTE.get(self._session, SESSION_PALETTE[ECUState.SESSION_DEFAULT])
        self._phase = (self._phase + 0.04) % (2 * math.pi)
        glow = 0.55 + 0.45 * math.sin(self._phase)
        # lerp fill brightness
        lo  = pal["lo"]
        hi  = pal["hi"]
        col = self._lerp(lo, hi, glow * 0.5)
        self.itemconfig(self._bg_rect, fill=col, outline=pal["hi"])
        self.itemconfig(self._label, fill=pal["hi"])

    def set_session(self, session: int):
        self._session = session
        pal = SESSION_PALETTE.get(session, SESSION_PALETTE[ECUState.SESSION_DEFAULT])
        self.itemconfig(self._label, text=pal["name"])

    @staticmethod
    def _lerp(hex_a: str, hex_b: str, t: float) -> str:
        def p(h): h=h.lstrip("#"); return int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        ar,ag,ab=p(hex_a); br,bg,bb=p(hex_b)
        return "#{:02x}{:02x}{:02x}".format(int(ar+(br-ar)*t),int(ag+(bg-ag)*t),int(ab+(bb-ab)*t))


# ─────────────────────────── SECURITY INDICATOR ───────────────────────────────

class SecurityIndicator(tk.Canvas):
    """Lock icon that animates unlock/lock transitions."""

    W, H = 100, 38

    def __init__(self, parent, **kw):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=C["bg"], bd=0, highlightthickness=0, **kw)
        self._locked = True
        self._phase  = 0.0
        self._bg = self.create_rectangle(2,2,self.W-2,self.H-2,
                                         fill=C["red_dim"], outline=C["red"], width=1)
        # Lock body
        cx, cy = self.W//2, self.H//2 + 4
        self._body = self.create_rectangle(cx-8,cy-5, cx+8,cy+7,
                                           fill=C["red"], outline="", width=0)
        # Lock shackle (arc)
        self._shackle = self.create_arc(cx-6, cy-14, cx+6, cy-4,
                                        start=0, extent=180,
                                        outline=C["red"], width=2, style="arc")
        self._label = self.create_text(self.W//2, 9, text="LOCKED",
                                       fill=C["red"], font=(C["font_hud"], 9, "bold"))

    def tick(self):
        self._phase = (self._phase + 0.05) % (2*math.pi)
        glow = 0.5 + 0.5*math.sin(self._phase)
        if self._locked:
            col  = self._lerp(C["red_dim"], C["red"], glow*0.6)
            fill = self._lerp(C["red_dim"], C["red"], glow*0.8)
            self.itemconfig(self._bg,     fill=col, outline=C["red"])
            self.itemconfig(self._body,   fill=fill)
            self.itemconfig(self._shackle, outline=fill)
            self.itemconfig(self._label,  text="LOCKED", fill=C["red"])
        else:
            col  = self._lerp(C["bg3"], C["phosphor_dim"], glow*0.4)
            self.itemconfig(self._bg,     fill=col, outline=C["phosphor_dim"])
            self.itemconfig(self._body,   fill=C["phosphor_dim"])
            self.itemconfig(self._shackle, outline=C["phosphor_dim"])
            self.itemconfig(self._label,  text="UNLOCKED", fill=C["phosphor"])

    def set_locked(self, locked: bool):
        self._locked = locked

    @staticmethod
    def _lerp(hex_a:str, hex_b:str, t:float) -> str:
        def p(h): h=h.lstrip("#"); return int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
        ar,ag,ab=p(hex_a); br,bg,bb=p(hex_b)
        return "#{:02x}{:02x}{:02x}".format(int(ar+(br-ar)*t),int(ag+(bg-ag)*t),int(ab+(bb-ab)*t))


# ─────────────────────────── MAIN GUI CLASS ───────────────────────────────────

class ECU_GUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("UDS ECU Simulator  ·  Diagnostic Console")
        self.root.geometry("1560x900")
        self.root.configure(bg=C["bg"])
        self.root.resizable(True, True)

        # throttle tracking
        self._last_can_time   = 0.0
        self._can_rate_bucket = 0

        self._build_ui()

        # Start ECU
        _elog.info("[INIT] Building ECU_GUI and starting VirtualECU")
        self.ecu = VirtualECU(self.log_uds, self.log_raw_can, self.log_oracle)

        # If the default vulnerabilities.json loaded on startup, arm the button
        if self.ecu.cfg.vulnerabilities:
            self._btn_unload.config(fg=C["amber"], state="normal")

        self.ecu_thread = threading.Thread(target=self.ecu.start, daemon=True)
        self.ecu_thread.start()

        # Animation engine
        self.anim = Animator(self.root)
        self._start_animations()

        # Status polling
        self.root.after(250, self._poll_status)

    # ──────────────────────── UI BUILD ────────────────────────────────────────

    def _build_ui(self):
        self._build_title_bar()
        self._build_status_bar()
        self._build_ecu_state_row()
        self._build_flow_row()
        self._build_log_panels()

    def _build_title_bar(self):
        bar = tk.Frame(self.root, bg=C["bg"], pady=6)
        bar.pack(fill="x", padx=8)

        # Left — title
        left = tk.Frame(bar, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text="◈  UDS ECU SIMULATOR",
                 bg=C["bg"], fg=C["phosphor"],
                 font=(C["font_hud"], 15, "bold")).pack(side="left")
        tk.Label(left, text="  DIAGNOSTIC CONSOLE v2.0",
                 bg=C["bg"], fg=C["mid"],
                 font=(C["font_hud"], 11)).pack(side="left", pady=4)

        # Right — controls
        right = tk.Frame(bar, bg=C["bg"])
        right.pack(side="right")

        self._make_btn(right, "LOAD JSON",   self._load_vulnerability_file).pack(side="left", padx=4)
        self._btn_unload = self._make_btn(right, "UNLOAD JSON", self._unload_vulnerability_file,
                                          color=C["amber_dim"])
        self._btn_unload.pack(side="left", padx=4)
        self._btn_unload.config(state="disabled")   # enabled once a JSON file loads
        self._make_btn(right, "EXPORT LOG",  self._export_log).pack(side="left", padx=4)

        self.var_persistent = tk.BooleanVar(value=False)
        chk = tk.Checkbutton(right, text="PERSIST LOCKOUT",
                              variable=self.var_persistent,
                              command=self._toggle_lockout,
                              bg=C["bg"], fg=C["mid"], selectcolor=C["bg3"],
                              activebackground=C["bg"], activeforeground=C["phosphor"],
                              font=(C["font_hud"], 10))
        chk.pack(side="left", padx=6)

        self._make_btn(right, "CLEAR",  self._clear_logs, color=C["mid"]).pack(side="left", padx=4)
        self._make_btn(right, "EXIT",   self._exit, color=C["red"]).pack(side="left", padx=4)

        # Thin accent line
        tk.Frame(self.root, bg=C["phosphor_lo"], height=1).pack(fill="x", padx=8)

    def _build_status_bar(self):
        """LED status indicators + counters."""
        bar = tk.Frame(self.root, bg=C["bg"], pady=6)
        bar.pack(fill="x", padx=8)

        # ── LEDs ─────────────────────────────────────────────────────────────
        led_frame = tk.Frame(bar, bg=C["bg"])
        led_frame.pack(side="left")

        self.led_can    = self._make_led_block(led_frame, "CAN BUS",    C["phosphor"])
        self.led_ecu    = self._make_led_block(led_frame, "ECU",        C["cyan"])
        self.led_proc   = self._make_led_block(led_frame, "PROCESSING", C["amber"])
        self.led_vuln   = self._make_led_block(led_frame, "VULN DET",   C["red"])

        # ── Spinner ───────────────────────────────────────────────────────────
        spin_f = tk.Frame(bar, bg=C["bg"], padx=6)
        spin_f.pack(side="left")
        self.spinner = Spinner(spin_f, size=26, color=C["cyan"])
        self.spinner.pack()

        # Separator
        tk.Frame(bar, bg=C["border"], width=1).pack(side="left", fill="y", padx=8, pady=2)

        # ── Counters ──────────────────────────────────────────────────────────
        self.counters = CounterBox(bar)
        self.counters.pack(side="left", padx=4)

        # ── Bar graph ─────────────────────────────────────────────────────────
        tk.Frame(bar, bg=C["border"], width=1).pack(side="left", fill="y", padx=8, pady=2)
        graph_frame = tk.Frame(bar, bg=C["bg"])
        graph_frame.pack(side="left")
        tk.Label(graph_frame, text="CAN DENSITY", bg=C["bg"], fg=C["text_dim"],
                 font=(C["font_hud"], 9)).pack()
        self.bargraph = BarGraph(graph_frame)
        self.bargraph.pack()

        # ── Clock ─────────────────────────────────────────────────────────────
        tk.Frame(bar, bg=C["border"], width=1).pack(side="right", fill="y", padx=8, pady=2)
        clock_f = tk.Frame(bar, bg=C["bg"])
        clock_f.pack(side="right")
        self._clock_var = tk.StringVar(value="00:00:00")
        tk.Label(clock_f, textvariable=self._clock_var,
                 bg=C["bg"], fg=C["mid"],
                 font=(C["font_hud"], 16, "bold")).pack()

        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x", padx=8)

    def _build_ecu_state_row(self):
        """Session / security / fault / boot counters."""
        row = tk.Frame(self.root, bg=C["bg"], pady=5)
        row.pack(fill="x", padx=8)

        tk.Label(row, text="ECU STATE", bg=C["bg"], fg=C["text_dim"],
                 font=(C["font_hud"], 9, "bold")).pack(side="left", padx=6)

        # Session badge
        self.session_ind = SessionIndicator(row)
        self.session_ind.pack(side="left", padx=6)

        # Security badge
        self.security_ind = SecurityIndicator(row)
        self.security_ind.pack(side="left", padx=6)

        # Stat badges
        self.badge_boot    = StatBadge(row, "BOOT COUNT", "0",  color=C["mid"])
        self.badge_boot.pack(side="left", padx=4)
        self.badge_fault   = StatBadge(row, "FAULT",     "—",  color=C["phosphor"])
        self.badge_fault.pack(side="left", padx=4)
        self.badge_attempt = StatBadge(row, "AUTH ATTEMPTS", "0/3", color=C["mid"])
        self.badge_attempt.pack(side="left", padx=4)
        self.badge_p2      = StatBadge(row, "P2 TIMEOUT", "50ms", color=C["mid"])
        self.badge_p2.pack(side="left", padx=4)

        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x", padx=8)

    def _build_flow_row(self):
        """CAN message flow animation."""
        row = tk.Frame(self.root, bg=C["bg2"], pady=4)
        row.pack(fill="x", padx=8, pady=2)

        tk.Label(row, text="MSG FLOW", bg=C["bg2"], fg=C["text_dim"],
                 font=(C["font_hud"], 9, "bold")).pack(side="left", padx=8)

        self.flow = FlowArrow(row)
        self.flow.pack(side="left", padx=4)

        # Right side — last message preview
        tk.Frame(row, bg=C["border"], width=1).pack(side="left", fill="y", padx=8, pady=4)
        msg_f = tk.Frame(row, bg=C["bg2"])
        msg_f.pack(side="left", fill="x", expand=True)
        tk.Label(msg_f, text="LAST FRAME", bg=C["bg2"], fg=C["text_dim"],
                 font=(C["font_hud"], 9)).pack(anchor="w")
        self._last_frame_var = tk.StringVar(value="—")
        tk.Label(msg_f, textvariable=self._last_frame_var,
                 bg=C["bg2"], fg=C["phosphor"],
                 font=(C["font_mono"], 12, "bold")).pack(anchor="w")

        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x", padx=8)

    def _build_log_panels(self):
        panels = tk.Frame(self.root, bg=C["bg"])
        panels.pack(fill="both", expand=True, padx=8, pady=6)

        # Give every column an equal weight=1 so Tkinter divides available
        # horizontal space in exact thirds — immune to font-size-driven minimum widths.
        panels.columnconfigure(0, weight=1, uniform="logcol")
        panels.columnconfigure(1, weight=1, uniform="logcol")
        panels.columnconfigure(2, weight=1, uniform="logcol")
        panels.rowconfigure(0, weight=1)

        self.log_uds_panel    = PhosphorLog(panels, title="◈ UDS DIAGNOSTIC LOG")
        self.log_can_panel    = PhosphorLog(panels, title="◈ CAN FRAME LOG")
        self.log_oracle_panel = PhosphorLog(panels, title="◈ ORACLE / VULN LOG")

        self.log_uds_panel.grid(   row=0, column=0, sticky="nsew", padx=(0, 3))
        self.log_can_panel.grid(   row=0, column=1, sticky="nsew", padx=3)
        self.log_oracle_panel.grid(row=0, column=2, sticky="nsew", padx=(3, 0))

    # ──────────────────────── WIDGET FACTORIES ────────────────────────────────

    def _make_led_block(self, parent, label: str, color: str) -> LED:
        f = tk.Frame(parent, bg=C["bg"], padx=6)
        f.pack(side="left")
        led = LED(f, color=color)
        led.pack()
        tk.Label(f, text=label, bg=C["bg"], fg=C["text_dim"],
                 font=(C["font_hud"], 8)).pack()
        return led

    def _make_btn(self, parent, text: str, cmd, color: str = None) -> tk.Button:
        col = color or C["phosphor_dim"]
        btn = tk.Button(
            parent, text=text, command=cmd,
            bg=C["bg3"], fg=col, activebackground=C["border_hi"],
            activeforeground=col, relief="flat", bd=0, padx=10, pady=4,
            font=(C["font_hud"], 10, "bold"), cursor="hand2",
        )
        btn.bind("<Enter>", lambda e, b=btn, c=col: b.config(fg=C["white"], bg=C["border_hi"]))
        btn.bind("<Leave>", lambda e, b=btn, c=col: b.config(fg=c, bg=C["bg3"]))
        return btn

    # ──────────────────────── ANIMATION LOOP ──────────────────────────────────

    def _start_animations(self):
        a = self.anim

        # LED breathing animations
        a.repeat(30, self.led_can.pulse)
        a.repeat(40, lambda: self.led_ecu.pulse(speed=0.03))

        # Spinner
        a.repeat(25, self.spinner.spin)
        self.spinner.hide()   # shown only when actively processing

        # Session + security badge animation
        a.repeat(30, self.session_ind.tick)
        a.repeat(40, self.security_ind.tick)

        # Flow arrow
        a.repeat(25, self.flow.tick)

        # Bar graph redraw
        a.repeat(120, self.bargraph.redraw)

        # CAN density bucket flusher (1/s)
        a.repeat(1000, self._flush_can_bucket)

        # Clock update
        a.repeat(1000, self._update_clock)

    def _flush_can_bucket(self):
        rate = min(1.0, self._can_rate_bucket / 20.0)
        self.bargraph.push(rate)
        self._can_rate_bucket = 0

    def _update_clock(self):
        self._clock_var.set(time.strftime("%H:%M:%S"))

    # ──────────────────────── LOG CALLBACKS ───────────────────────────────────

    def log_uds(self, msg: str):
        self.root.after(0, lambda: self._append_uds(msg))

    def _append_uds(self, msg: str):
        self.log_uds_panel.append(msg)
        self.counters.bump_uds()
        # Flash processing LED briefly
        self.spinner.show()
        self.led_proc.blink_once(on_ms=200)
        self.root.after(400, self.spinner.hide)
        # Fire flow arrow
        if "[RX]" in msg.upper() or "[RX][UDS]" in msg.upper():
            self.flow.fire(direction=1, color=C["phosphor"], label="TESTER → ECU")
        elif "[TX]" in msg.upper() or "[TX][UDS]" in msg.upper():
            self.flow.fire(direction=-1, color=C["cyan"], label="ECU → TESTER")
        # File logger
        skip = ("[RX][UDS]","[TX][UDS]","[WARN]","[ERR]","[FAILURE]","[VULN]","[STATE]")
        if not any(msg.startswith(p) for p in skip):
            _elog.info(msg)

    def log_raw_can(self, msg: can.Message):
        self.root.after(0, lambda m=msg: self._append_can(m))

    def _append_can(self, msg: can.Message):
        line = (f"ID={msg.arbitration_id:03X}  DLC={msg.dlc}"
                f"  {msg.data.hex().upper()}")
        self.log_can_panel.append(line)
        self._last_frame_var.set(f"0x{msg.arbitration_id:03X}  {msg.data.hex().upper()}")
        self.counters.bump_can()
        self.led_can.blink_once(on_ms=60)
        self._can_rate_bucket += 1
        _elog.debug(f"[CAN] {line}")

    def log_oracle(self, msg: str):
        self.root.after(0, lambda: self._append_oracle(msg))

    def _append_oracle(self, msg: str):
        self.log_oracle_panel.append(msg, tag="oracle")
        self.counters.bump_vuln()
        # Danger flash
        self.led_vuln.blink_once(on_ms=350)
        self.flow.fire(direction=1, color=C["amber"], label="⚡ VULN TRIGGERED")
        _elog.warning(f"[ORACLE] {msg}")

    # ──────────────────────── STATUS POLLING ──────────────────────────────────

    def _poll_status(self):
        try:
            st  = self.ecu.state
            nvm = self.ecu.nvm

            # Session
            if self.session_ind._session != st.session:
                self.session_ind.set_session(st.session)
                pal = SESSION_PALETTE.get(st.session, SESSION_PALETTE[ECUState.SESSION_DEFAULT])
                self.led_ecu.set_color(pal["led"])

            # Security
            locked = st.security_level == 0
            self.security_ind.set_locked(locked)

            # Fault
            if st.faulted:
                self.badge_fault.set(st.fault_reason or "YES", color=C["red"])
                self.led_ecu.set_color(C["red"])
            else:
                self.badge_fault.set("—", color=C["phosphor"])

            # Boot count
            boot = nvm.store.get("boot_count", 0)
            self.badge_boot.set(str(boot))

            # Auth attempts
            attempts = (nvm.store.get("persistent_auth_failures",0)
                        if st.persistent_lockout else st.auth_failures_ram)
            attempt_str = f"{attempts}/{st.max_attempts}"
            col = C["red"] if attempts >= st.max_attempts else (C["amber"] if attempts > 0 else C["mid"])
            self.badge_attempt.set(attempt_str, color=col)

            # P2 timeout
            self.badge_p2.set(f"{st.p2_ms}ms")

        except Exception as exc:
            _elog.warning(f"[GUI] Status poll error: {exc}")

        self.root.after(250, self._poll_status)

    # ──────────────────────── CONTROLS ────────────────────────────────────────

    def _load_vulnerability_file(self):
        path = filedialog.askopenfilename(
            title="Select Vulnerability JSON",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        self.log_uds(f"[SYSTEM] Loading: {os.path.basename(path)}")
        _elog.info(f"[CONFIG] Loading vulnerability file: {path}")
        self.ecu.cfg.path = path
        if self.ecu.cfg.load():
            self.ecu.apply_cfg()
            self.ecu.vuln_engine = VulnerabilityEngine(
                self.ecu.cfg, self.ecu.state, self.ecu.log, self.ecu.oracle)
            n = len(self.ecu.cfg.vulnerabilities)
            self.log_uds(f"[SYSTEM] Reloaded {n} vulnerabilities")
            _elog.info(f"[CONFIG] Reloaded {n} vulnerabilities")
            # Enable UNLOAD button now that a file is armed
            self._btn_unload.config(fg=C["amber"], state="normal")
        else:
            self.log_uds("[SYSTEM][ERR] Failed to load JSON")
            _elog.error(f"[CONFIG] Failed to load JSON from {path}")

    def _unload_vulnerability_file(self):
        """Clear all loaded vulnerabilities and reset engine to empty state."""
        if not self.ecu.cfg.vulnerabilities:
            # Nothing loaded — inform the user gracefully, no crash
            self.log_uds("[SYSTEM] No vulnerability file is currently loaded")
            self.log_oracle("[SYSTEM] Nothing to unload — no file was loaded")
            _elog.info("[CONFIG] Unload requested but no file was loaded")
            return

        prev_count   = len(self.ecu.cfg.vulnerabilities)
        prev_profile = self.ecu.cfg.profile

        # 1. Clear config (also logs to UDS + oracle panels via callbacks)
        self.ecu.cfg.unload()

        # 2. Replace engine with an inert empty instance (zero rules)
        self.ecu.vuln_engine = VulnerabilityEngine(
            self.ecu.cfg, self.ecu.state, self.ecu.log, self.ecu.oracle)

        # 3. Reset UDS timing to bare defaults
        self.ecu.apply_cfg()

        # 4. GUI feedback
        self.log_uds(f"[SYSTEM] Vulnerability file removed — "
                     f"{prev_count} rules cleared (was: {prev_profile})")
        _elog.info(f"[CONFIG] Unloaded {prev_count} vulnerabilities from '{prev_profile}'")

        # 5. Grey out the UNLOAD button — nothing left to unload
        self._btn_unload.config(fg=C["amber_dim"], state="disabled")

    def _toggle_lockout(self):
        val = self.var_persistent.get()
        self.ecu.state.persistent_lockout = val
        _elog.info(f"[GUI] Persistent lockout → {val}")

    def _clear_logs(self):
        self.log_uds_panel.clear()
        self.log_can_panel.clear()
        self.log_oracle_panel.clear()
        _elog.info("[GUI] Logs cleared")

    def _export_log(self):
        from shutil import copyfile
        dest = filedialog.asksaveasfilename(
            title="Export ECU Log",
            defaultextension=".log",
            filetypes=(("Log files","*.log"),("JSONL","*.jsonl"),("All","*.*")),
            initialfile="ecu_simulation_export.log",
        )
        if not dest:
            return
        try:
            copyfile("logs/ecu_simulation.log", dest)
            self.log_uds(f"[SYSTEM] Exported → {dest}")
            _elog.info(f"[GUI] Log exported to {dest}")
        except Exception as exc:
            self.log_uds(f"[SYSTEM][ERR] Export failed: {exc}")
            _elog.error(f"[GUI] Export failed: {exc}")

    def _exit(self):
        _elog.info("[GUI] Exit requested")
        self.anim.stop()
        self.ecu.stop()
        self.root.destroy()
