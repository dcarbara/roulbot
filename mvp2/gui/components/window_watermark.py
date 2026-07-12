"""
Premium border overlay placed on top of the selected gameplay window.
Shows a sleek "SpinEdge" pill tag + pulsing corner brackets so the user
always knows which window is being tracked.
"""

import tkinter as tk
import math

# ── Design tokens ──────────────────────────────────────────────────
_GOLD           = "#EAB308"
_GOLD_DIM       = "#CA8A04"
_GOLD_SOFT      = "#D4A520"
_TAG_BG         = "#12141C"
_TAG_FG         = "#EAB308"
_BORDER_WIDTH   = 2
_CORNER_LEN     = 38          # length of each L-bracket arm
_CORNER_THICK   = 4           # thickness of the L-bracket arm
_PULSE_STEPS    = 40          # frames per full pulse cycle
_PULSE_INTERVAL = 50          # ms between pulse frames
_POLL_INTERVAL  = 400         # ms between position syncs


class WindowWatermark(tk.Toplevel):
    """Transparent overlay that draws a gold corner-bracket frame + pill tag."""

    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-toolwindow", True)

        # Transparency
        self.transparent_color = "#000001"
        self.config(bg=self.transparent_color)
        self.attributes("-transparentcolor", self.transparent_color)
        self.attributes("-alpha", 0.92)

        # Canvas
        self.canvas = tk.Canvas(self, bg=self.transparent_color, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._target_win = None
        self._tracking = False
        self._poll_id = None
        self._pulse_id = None
        self._pulse_step = 0
        self._last_w = 0
        self._last_h = 0

    # ── Public API ─────────────────────────────────────────────────

    def attach(self, target_win):
        """Start tracking a pygetwindow Window object."""
        self._target_win = target_win
        self._tracking = True
        self._pulse_step = 0
        self._sync_position()
        self.deiconify()
        self._start_polling()
        self._start_pulse()

    def detach(self):
        """Stop tracking and hide."""
        self._tracking = False
        self._target_win = None
        if self._poll_id:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self._pulse_id:
            self.after_cancel(self._pulse_id)
            self._pulse_id = None
        self.withdraw()

    # ── Polling ────────────────────────────────────────────────────

    def _start_polling(self):
        if not self._tracking:
            return
        self._sync_position()
        self._poll_id = self.after(_POLL_INTERVAL, self._start_polling)

    def _sync_position(self):
        if not self._target_win:
            return
        try:
            left = self._target_win.left
            top = self._target_win.top
            width = self._target_win.width
            height = self._target_win.height
            if width <= 0 or height <= 0:
                return
            self.geometry(f"{width}x{height}+{left}+{top}")
            # Only full redraw if size changed
            if width != self._last_w or height != self._last_h:
                self._last_w = width
                self._last_h = height
                self._draw(width, height)
        except Exception:
            pass

    # ── Pulse animation ────────────────────────────────────────────

    def _start_pulse(self):
        if not self._tracking:
            return
        self._pulse_step = (self._pulse_step + 1) % _PULSE_STEPS
        # Update only the pulsing elements (corner brackets + dot)
        self._update_pulse()
        self._pulse_id = self.after(_PULSE_INTERVAL, self._start_pulse)

    def _pulse_alpha(self):
        """Returns 0.0–1.0 sinusoidal pulse value."""
        return 0.5 + 0.5 * math.sin(2 * math.pi * self._pulse_step / _PULSE_STEPS)

    @staticmethod
    def _blend(hex_color, alpha, bg=(18, 20, 28)):
        """Blend hex_color with bg at given alpha (0–1)."""
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        mr = int(r * alpha + bg[0] * (1 - alpha))
        mg = int(g * alpha + bg[1] * (1 - alpha))
        mb = int(b * alpha + bg[2] * (1 - alpha))
        return f"#{mr:02x}{mg:02x}{mb:02x}"

    # ── Drawing ────────────────────────────────────────────────────

    def _draw(self, w, h):
        """Full redraw — called when size changes."""
        self.canvas.delete("all")
        bw = _BORDER_WIDTH

        # Subtle thin border (all four sides)
        self.canvas.create_rectangle(
            0, 0, w - 1, h - 1,
            outline=_GOLD_DIM, width=bw, fill="",
        )

        # Corner L-brackets (drawn as "static" tags, pulse updates color)
        cl = min(_CORNER_LEN, w // 5, h // 5)
        ct = _CORNER_THICK

        corners = [
            (0, 0, 1, 1),       # top-left
            (w, 0, -1, 1),      # top-right
            (0, h, 1, -1),      # bottom-left
            (w, h, -1, -1),     # bottom-right
        ]
        for cx, cy, dx, dy in corners:
            # Horizontal arm
            hx1 = cx if dx > 0 else cx - cl
            hx2 = cx + cl if dx > 0 else cx
            hy1 = cy if dy > 0 else cy - ct
            hy2 = cy + ct if dy > 0 else cy
            self.canvas.create_rectangle(
                hx1, hy1, hx2, hy2,
                fill=_GOLD, outline="", tags="corner",
            )
            # Vertical arm
            vx1 = cx if dx > 0 else cx - ct
            vx2 = cx + ct if dx > 0 else cx
            vy1 = cy if dy > 0 else cy - cl
            vy2 = cy + cl if dy > 0 else cy
            self.canvas.create_rectangle(
                vx1, vy1, vx2, vy2,
                fill=_GOLD, outline="", tags="corner",
            )

        # ── Pill tag (top-center) ──────────────────────────────────
        tag_w = 160
        tag_h = 26
        tag_x = (w - tag_w) // 2
        tag_y = bw + 4
        pill_r = tag_h // 2

        # Pill background (rounded via overlapping shapes)
        self.canvas.create_rectangle(
            tag_x + pill_r, tag_y, tag_x + tag_w - pill_r, tag_y + tag_h,
            fill=_TAG_BG, outline="",
        )
        self.canvas.create_oval(
            tag_x, tag_y, tag_x + tag_h, tag_y + tag_h,
            fill=_TAG_BG, outline="",
        )
        self.canvas.create_oval(
            tag_x + tag_w - tag_h, tag_y, tag_x + tag_w, tag_y + tag_h,
            fill=_TAG_BG, outline="",
        )
        # Pill outline
        self.canvas.create_arc(
            tag_x, tag_y, tag_x + tag_h, tag_y + tag_h,
            start=90, extent=180, style="arc", outline=_GOLD_DIM, width=1,
        )
        self.canvas.create_arc(
            tag_x + tag_w - tag_h, tag_y, tag_x + tag_w, tag_y + tag_h,
            start=-90, extent=180, style="arc", outline=_GOLD_DIM, width=1,
        )
        self.canvas.create_line(
            tag_x + pill_r, tag_y, tag_x + tag_w - pill_r, tag_y,
            fill=_GOLD_DIM, width=1,
        )
        self.canvas.create_line(
            tag_x + pill_r, tag_y + tag_h, tag_x + tag_w - pill_r, tag_y + tag_h,
            fill=_GOLD_DIM, width=1,
        )

        # Pulsing status dot (placeholder, color updated by pulse)
        dot_r = 4
        dot_cx = tag_x + 16
        dot_cy = tag_y + tag_h // 2
        self.canvas.create_oval(
            dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r,
            fill=_GOLD, outline="", tags="pulse_dot",
        )

        # Tag text
        self.canvas.create_text(
            tag_x + tag_w // 2 + 6, tag_y + tag_h // 2,
            text="SpinEdge  TRACKING",
            fill=_TAG_FG, font=("Segoe UI", 9, "bold"),
        )

    def _update_pulse(self):
        """Animate corner brackets and status dot brightness."""
        t = self._pulse_alpha()
        # Interpolate gold brightness: dim ↔ full
        color = self._blend(_GOLD, 0.45 + 0.55 * t, bg=(18, 20, 28))

        # Update corner bracket colors
        for item_id in self.canvas.find_withtag("corner"):
            self.canvas.itemconfigure(item_id, fill=color)

        # Update status dot
        for item_id in self.canvas.find_withtag("pulse_dot"):
            self.canvas.itemconfigure(item_id, fill=color)
