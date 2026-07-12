
import customtkinter as ctk
import tkinter as tk
from gui.theme import (
    FONT_SMALL, FONT_CAPTION, FONT_TINY,
    GOLD, GOLD_DIM, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    BG_CARD, BG_ELEVATED,
    BORDER_SUBTLE, BORDER_DEFAULT,
    SUCCESS, SUCCESS_HOVER, WARNING, DANGER, PURPLE,
    CORNER_LARGE, CORNER_SMALL,
)

# Overlay-specific colors
_OV_BG = "#12141C"
_OV_BORDER = GOLD_DIM
_OV_DIVIDER = "#2A2F45"
_ROULETTE_RED = "#C0392B"
_ROULETTE_BLACK = "#1C1C2E"
_ROULETTE_GREEN = "#1E8449"

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


class BotOverlay(ctk.CTkToplevel):
    def __init__(self, master, *args, **kwargs):
        self.close_callback = kwargs.pop('close_callback', None)
        self.pause_callback = kwargs.pop('pause_callback', None)
        self.refresh_callback = kwargs.pop('refresh_callback', None)
        super().__init__(master, *args, **kwargs)

        # Window configuration
        self.title("SpinEdge HUD")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-toolwindow", True)
        self.attributes("-alpha", 0.96)

        # Transparent background magic
        self.transparent_color = "#000001"
        self.config(bg=self.transparent_color)
        self.attributes("-transparentcolor", self.transparent_color)

        # Main Wrapper
        self.main_frame = ctk.CTkFrame(
            self, fg_color=_OV_BG, corner_radius=CORNER_LARGE,
            border_width=1, border_color=_OV_BORDER,
        )
        self.main_frame.pack(fill="both", expand=True, padx=2, pady=2)

        # Drag Handle
        self.drag_handle = ctk.CTkLabel(
            self.main_frame, text="\u2801\u2801\u2801",
            font=FONT_CAPTION, text_color=TEXT_MUTED, cursor="fleur",
        )
        self.drag_handle.place(relx=0.5, rely=0.0, anchor="n", y=4)

        # Close Button
        self.close_btn = ctk.CTkLabel(
            self.main_frame, text="\u2715",
            font=FONT_SMALL, text_color=TEXT_MUTED, cursor="hand2",
        )
        self.close_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=8)
        self.close_btn.bind("<Button-1>", lambda e: self.close_overlay())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.configure(text_color=DANGER))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.configure(text_color=TEXT_MUTED))

        # Scrollable Content Area
        self.scroll_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True, padx=6, pady=(28, 18))

        # --- Initialize State Variables ---
        self.graph_scroll_offset = 0
        self.graph_view_size = 50
        self.is_dragging_graph = False
        self.graph_drag_start_x = 0
        self.graph_drag_start_offset = 0
        self.show_markers_var = tk.BooleanVar(value=True)
        self.fit_graph_var = tk.BooleanVar(value=False)

        # ═══════════════════════════════════════════════════════════════
        # 1. Header: Bundle Name + Status + Detected Number Badge
        # ═══════════════════════════════════════════════════════════════

        # Bundle name row (top of HUD): [bundle name]  [bundle pill] [bundle pill]
        self._bundle_row = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self._bundle_row.pack(fill="x", padx=8, pady=(0, 1))
        self.bundle_label = ctk.CTkLabel(
            self._bundle_row, text="",
            font=FONT_CAPTION, text_color=TEXT_SECONDARY, anchor="w",
        )
        self.bundle_label.pack(side="left", anchor="w")
        self._fav_bundle_pills_container = ctk.CTkFrame(self._bundle_row, fg_color="transparent")
        self._fav_bundle_pills_container.pack(side="right", anchor="e")

        self.header_row = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.header_row.pack(fill="x", padx=6, pady=(0, 2))

        self.header_label = ctk.CTkLabel(
            self.header_row, text="SpinEdge: ACTIVE",
            font=FONT_SMALL, text_color=GOLD, anchor="w",
        )
        self.header_label.pack(side="left", fill="x", expand=True)

        # Number badge — colored circle with number
        self.number_badge = tk.Canvas(
            self.header_row, width=44, height=44,
            bg=_OV_BG, highlightthickness=0,
        )
        self.number_badge.pack(side="right", padx=(4, 0))
        self._draw_number_badge("--", TEXT_MUTED)

        # Result badge
        self.result_label = ctk.CTkLabel(
            self.scroll_frame, text="WAITING",
            font=FONT_CAPTION, text_color=TEXT_MUTED,
        )
        self.result_label.pack(pady=(0, 4))

        # Divider
        ctk.CTkFrame(
            self.scroll_frame, fg_color=_OV_DIVIDER, height=1, corner_radius=0,
        ).pack(fill="x", padx=6, pady=(0, 6))

        # ═══════════════════════════════════════════════════════════════
        # 2. Primary Stats — grouped by relevance
        # ═══════════════════════════════════════════════════════════════
        self.primary_stats_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.primary_stats_frame.pack(fill="x", padx=8, pady=2)

        # --- Group 1: Financial (most important) ---
        self._fin_frame = ctk.CTkFrame(self.primary_stats_frame, fg_color="transparent")
        self._fin_frame.pack(fill="x")
        self.balance_label = self._metric(self._fin_frame, 0, 0, "Balance:", "$0.00", SUCCESS)
        self.pnl_label = self._metric(self._fin_frame, 0, 1, "PnL:", "$0.00", TEXT_PRIMARY)
        self.bet_label = self._metric(self._fin_frame, 1, 0, "Bet:", "--", GOLD)
        self.winrate_label = self._metric(self._fin_frame, 1, 1, "W/R:", "--", TEXT_SECONDARY)

        # Micro-divider
        ctk.CTkFrame(self.primary_stats_frame, fg_color=_OV_DIVIDER, height=1,
                      corner_radius=0).pack(fill="x", padx=2, pady=2)

        # --- Group 2: Strategy + Game State ---
        # Horizontal row: [strategy name]   [pill] [pill] ...
        # Pills are populated via set_favorites() from main_gui.update_hud_safe.
        self._strategy_row = ctk.CTkFrame(self.primary_stats_frame, fg_color="transparent")
        self._strategy_row.pack(fill="x", pady=(1, 1))
        self.strategy_label = ctk.CTkLabel(
            self._strategy_row,
            text="\u25B8 Strategy: --", font=FONT_TINY, text_color=GOLD,
        )
        self.strategy_label.pack(side="left", anchor="w")
        # Pills container - cleared + rebuilt when favorites change.
        self._fav_pills_container = ctk.CTkFrame(self._strategy_row, fg_color="transparent")
        self._fav_pills_container.pack(side="right", anchor="e", padx=(4, 0))
        self._fav_strategy_click_cb = None
        self._fav_bundle_click_cb = None
        self._last_fav_signature = None

        self._game_frame = ctk.CTkFrame(self.primary_stats_frame, fg_color="transparent")
        self._game_frame.pack(fill="x")
        self.streak_label = self._metric(self._game_frame, 0, 0, "Streak:", "0", TEXT_PRIMARY)
        self.round_label = self._metric(self._game_frame, 0, 1, "Round:", "0", TEXT_SECONDARY)

        # Micro-divider
        ctk.CTkFrame(self.primary_stats_frame, fg_color=_OV_DIVIDER, height=1,
                      corner_radius=0).pack(fill="x", padx=2, pady=2)

        # --- Group 3: Timing (least urgent) ---
        self._time_frame = ctk.CTkFrame(self.primary_stats_frame, fg_color="transparent")
        self._time_frame.pack(fill="x")
        self.time_rem_label = self._metric(self._time_frame, 0, 0, "Remain:", "--:--", TEXT_SECONDARY)
        self.global_time_label = self._metric(self._time_frame, 0, 1, "Elapsed:", "00:00:00", PURPLE)

        # ═══════════════════════════════════════════════════════════════
        # 3. Progress Bars (Session Target & Stop Loss)
        # ═══════════════════════════════════════════════════════════════
        self.progress_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.progress_frame.pack(fill="x", padx=8, pady=(6, 2))

        # Session Target progress
        self._sess_target_row = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self._sess_target_row.pack(fill="x", pady=1)
        ctk.CTkLabel(self._sess_target_row, text="Target", font=FONT_TINY, text_color=TEXT_MUTED, width=42).pack(side="left")
        self._target_bar_canvas = tk.Canvas(self._sess_target_row, height=10, bg=_OV_BG, highlightthickness=0)
        self._target_bar_canvas.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._target_pct_label = ctk.CTkLabel(self._sess_target_row, text="--", font=FONT_TINY, text_color=TEXT_MUTED, width=36)
        self._target_pct_label.pack(side="right")

        # Session Stop progress
        self._sess_stop_row = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        self._sess_stop_row.pack(fill="x", pady=1)
        ctk.CTkLabel(self._sess_stop_row, text="Stop", font=FONT_TINY, text_color=TEXT_MUTED, width=42).pack(side="left")
        self._stop_bar_canvas = tk.Canvas(self._sess_stop_row, height=10, bg=_OV_BG, highlightthickness=0)
        self._stop_bar_canvas.pack(side="left", fill="x", expand=True, padx=(4, 4))
        self._stop_pct_label = ctk.CTkLabel(self._sess_stop_row, text="--", font=FONT_TINY, text_color=TEXT_MUTED, width=36)
        self._stop_pct_label.pack(side="right")

        # Divider
        ctk.CTkFrame(
            self.scroll_frame, fg_color=_OV_DIVIDER, height=1, corner_radius=0,
        ).pack(fill="x", padx=6, pady=(4, 2))

        # ═══════════════════════════════════════════════════════════════
        # 4. Collapsible Advanced Metrics
        # ═══════════════════════════════════════════════════════════════
        self.show_advanced = False
        self._base_geometry = None

        self.toggle_adv_btn = ctk.CTkButton(
            self.scroll_frame, text="\u25BC Advanced",
            font=FONT_TINY, fg_color="transparent", text_color=TEXT_MUTED,
            hover_color=BG_ELEVATED, height=22, corner_radius=CORNER_SMALL,
            command=self.toggle_advanced_metrics,
        )
        self.toggle_adv_btn.pack(pady=(4, 0))

        self.advanced_frame = ctk.CTkFrame(
            self.scroll_frame, fg_color=BG_CARD,
            corner_radius=CORNER_SMALL, border_width=1, border_color=BORDER_SUBTLE,
        )

        self.global_pnl_label = self._metric(self.advanced_frame, 0, 0, "G.PnL:", "$0.00", TEXT_PRIMARY)
        self.global_limit_label = self._metric(self.advanced_frame, 0, 1, "G.Tgt:", "--", TEXT_PRIMARY)
        self.session_limit_label = self._metric(self.advanced_frame, 1, 0, "S.Tgt:", "--", TEXT_PRIMARY)
        self.session_stop_label = self._metric(self.advanced_frame, 1, 1, "S.Stop:", "--", DANGER)
        self.global_stop_label = self._metric(self.advanced_frame, 2, 0, "G.Stop:", "--", DANGER)
        self.trailing_stop_label = self._metric(self.advanced_frame, 2, 1, "Trail:", "--", PURPLE)

        # ═══════════════════════════════════════════════════════════════
        # 5. Mini Graph with gradient fill
        # ═══════════════════════════════════════════════════════════════
        self.graph_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.graph_frame.pack(fill="x", padx=5, pady=2, expand=True)

        self.graph_canvas = ctk.CTkCanvas(
            self.graph_frame, height=90, bg=_OV_BG, highlightthickness=0,
        )
        self.graph_canvas.pack(fill="both", expand=True)

        # 6. Next Session Timer
        self.timer_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.timer_frame.pack(fill="x", padx=5, pady=(2, 5))
        self.next_sess_label = ctk.CTkLabel(
            self.timer_frame, text="Next: --:--",
            font=FONT_CAPTION, text_color=GOLD,
        )
        self.next_sess_label.pack(side="right", padx=(0, 5))

        # 7. Control Buttons
        self.control_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.control_frame.pack(fill="x", padx=5, pady=(4, 4))

        self.pause_btn = ctk.CTkButton(
            self.control_frame, text="\u23F8 PAUSE", width=80, height=26,
            font=FONT_CAPTION, fg_color=WARNING, hover_color="#D4940F",
            text_color="#1a1a1a", corner_radius=CORNER_SMALL,
            command=self.toggle_pause,
        )
        self.pause_btn.pack(side="left", padx=(0, 4), expand=True)

        self.refresh_btn = ctk.CTkButton(
            self.control_frame, text="\u21BB RESET", width=80, height=26,
            font=FONT_CAPTION, fg_color=BG_ELEVATED, hover_color=BORDER_DEFAULT,
            text_color=TEXT_SECONDARY, corner_radius=CORNER_SMALL,
            command=self.trigger_refresh,
        )
        self.refresh_btn.pack(side="right", padx=(4, 0), expand=True)

        # 8. Settings Checkboxes
        self.settings_frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent")
        self.settings_frame.pack(fill="x", padx=5, pady=(0, 4))

        chk_kwargs = dict(font=FONT_TINY, width=18, height=18, border_width=1, corner_radius=4)

        self.markers_chk = ctk.CTkCheckBox(
            self.settings_frame, text="Show Info", variable=self.show_markers_var,
            command=lambda: self.draw_mini_graph(
                getattr(self, 'current_graph_data', []),
                getattr(self, 'current_graph_markers', None)),
            **chk_kwargs,
        )
        self.markers_chk.pack(anchor="w", side="left", padx=(0, 10))

        self.fit_chk = ctk.CTkCheckBox(
            self.settings_frame, text="Fit Graph", variable=self.fit_graph_var,
            command=lambda: self.draw_mini_graph(
                getattr(self, 'current_graph_data', []),
                getattr(self, 'current_graph_markers', None)),
            **chk_kwargs,
        )
        self.fit_chk.pack(anchor="w", side="left")

        self.is_paused = False

        # Resize Grip
        self.sizegrip = ctk.CTkFrame(self.main_frame, width=20, height=20, fg_color="transparent", cursor="sizing")
        self.sizegrip.place(relx=1.0, rely=1.0, anchor="se")
        self.grip_label = ctk.CTkLabel(
            self.sizegrip, text="\u21F2", font=FONT_SMALL, text_color=TEXT_MUTED, cursor="sizing",
        )
        self.grip_label.place(relx=1, rely=1, anchor="se")

        self.sizegrip.bind("<Button-1>", self.start_resize)
        self.sizegrip.bind("<B1-Motion>", self.do_resize)
        self.grip_label.bind("<Button-1>", self.start_resize)
        self.grip_label.bind("<B1-Motion>", self.do_resize)

        # Dragging logic
        self.main_frame.bind("<Button-1>", self.start_drag)
        self.main_frame.bind("<B1-Motion>", self.do_drag)
        self.drag_handle.bind("<Button-1>", self.start_drag)
        self.drag_handle.bind("<B1-Motion>", self.do_drag)
        self.scroll_frame.bind("<Button-1>", self.start_drag)
        self.scroll_frame.bind("<B1-Motion>", self.do_drag)

        self.drag_start_x = 0
        self.drag_start_y = 0
        self.resize_start_x = 0
        self.resize_start_y = 0
        self.start_w = 0
        self.start_h = 0

        # Initial geometry
        screen_w = self.winfo_screenwidth()
        default_x = screen_w - 280
        self.geometry(f"260x440+{default_x}+50")

        # Graph Interaction Bindings
        self.graph_canvas.bind("<MouseWheel>", self.on_graph_scroll)
        self.graph_canvas.bind("<Button-1>", self.on_graph_pan_start)
        self.graph_canvas.bind("<B1-Motion>", self.on_graph_pan_drag)
        self.graph_canvas.bind("<Double-Button-1>", self.on_graph_reset)
        self.graph_canvas.bind("<Button-4>", lambda e: self.on_graph_scroll(e, 1))
        self.graph_canvas.bind("<Button-5>", lambda e: self.on_graph_scroll(e, -1))

        # Internal tracking for progress bars
        self._session_pnl = 0.0
        self._session_target_val = 0.0
        self._session_stop_val = 0.0

    # ─── Helpers ──────────────────────────────────────────────────────

    def _metric(self, parent, row, col, label_text, val_text, val_color=TEXT_PRIMARY, pad_x=8):
        """Create a label: value metric pair in a grid."""
        lbl = ctk.CTkLabel(parent, text=label_text, font=FONT_TINY, text_color=TEXT_MUTED)
        lbl.grid(row=row, column=col * 2, sticky="w", padx=(0, 3), pady=1)
        val = ctk.CTkLabel(parent, text=val_text, font=FONT_CAPTION, text_color=val_color)
        val.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, pad_x), pady=1)
        return val

    def _draw_number_badge(self, number_text, bg_color):
        """Draw a roulette-colored circle badge for the detected number.
        Accepts formats like '11', '11 BLACK', '0 GREEN', etc."""
        c = self.number_badge
        c.delete("all")
        w, h = 44, 44
        cx, cy = w // 2, h // 2
        r = 18

        raw = str(number_text).strip()

        # Parse: extract the numeric part and optional color word
        parts = raw.split(None, 1)  # e.g. ["11", "BLACK"] or ["0"] or ["--"]
        display_num = parts[0] if parts else raw
        color_hint = parts[1].upper() if len(parts) > 1 else ""

        # Determine badge color: prefer the color hint, fall back to number lookup
        badge_bg = bg_color
        if "GREEN" in color_hint:
            badge_bg = _ROULETTE_GREEN
        elif "RED" in color_hint:
            badge_bg = _ROULETTE_RED
        elif "BLACK" in color_hint:
            badge_bg = _ROULETTE_BLACK
        else:
            try:
                num = int(display_num)
                if num == 0:
                    badge_bg = _ROULETTE_GREEN
                elif num in RED_NUMBERS:
                    badge_bg = _ROULETTE_RED
                else:
                    badge_bg = _ROULETTE_BLACK
            except (ValueError, TypeError):
                badge_bg = "#2A2F45"

        # Circle
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=badge_bg, outline=GOLD_DIM, width=2)
        # Show only the number inside the badge
        c.create_text(cx, cy, text=display_num, fill="#FFFFFF",
                       font=("Segoe UI", 14, "bold"))

    def _draw_progress_bar(self, canvas, ratio, color, warn_threshold=0.8):
        """Draw a rounded progress bar on a canvas. ratio: 0.0 to 1.0."""
        canvas.delete("all")
        canvas.update_idletasks()
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 1:
            w = 100
        if h <= 1:
            h = 10

        r = h // 2  # corner radius

        # Background track
        canvas.create_rectangle(0, 0, w, h, fill="#1E2030", outline="")

        # Fill
        ratio = max(0.0, min(1.0, ratio))
        fill_w = max(0, int(w * ratio))
        if fill_w > 0:
            fill_color = DANGER if ratio >= warn_threshold else color
            canvas.create_rectangle(0, 0, fill_w, h, fill=fill_color, outline="")

        # Thin border
        canvas.create_rectangle(0, 0, w, h, fill="", outline="#2A2F45", width=1)

    # ─── Drag / Resize ────────────────────────────────────────────────

    def start_drag(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def do_drag(self, event):
        x = self.winfo_x() + event.x - self.drag_start_x
        y = self.winfo_y() + event.y - self.drag_start_y
        self.geometry(f"+{x}+{y}")

    def start_resize(self, event):
        self.resize_start_x = event.x_root
        self.resize_start_y = event.y_root
        self.start_w = self.winfo_width()
        self.start_h = self.winfo_height()

    def do_resize(self, event):
        dx = event.x_root - self.resize_start_x
        dy = event.y_root - self.resize_start_y
        new_w = max(200, self.start_w + dx)
        new_h = max(150, self.start_h + dy)
        self.geometry(f"{new_w}x{new_h}")

    # ─── Actions ──────────────────────────────────────────────────────

    def close_overlay(self):
        if self.close_callback:
            self.close_callback()
        else:
            self.destroy()

    def toggle_pause(self):
        if self.pause_callback:
            self.pause_callback()

    def trigger_refresh(self):
        if self.refresh_callback:
            self.refresh_callback()

    def toggle_advanced_metrics(self):
        if self.show_advanced:
            self.advanced_frame.pack_forget()
            self.toggle_adv_btn.configure(text="\u25BC Advanced")
            self.show_advanced = False
            if self._base_geometry:
                self.geometry(self._base_geometry)
                self._base_geometry = None
        else:
            self._base_geometry = self.geometry()
            self.advanced_frame.pack(fill="x", padx=8, pady=(0, 8), after=self.toggle_adv_btn)
            self.toggle_adv_btn.configure(text="\u25B2 Hide Advanced")
            self.show_advanced = True
            self.update_idletasks()
            extra = self.advanced_frame.winfo_reqheight() + 14
            new_h = self.winfo_height() + extra
            self.geometry(f"{self.winfo_width()}x{new_h}")

    def update_pause_state(self, is_paused):
        self.is_paused = is_paused
        if is_paused:
            self.pause_btn.configure(
                text="\u25B6 RESUME", fg_color=SUCCESS, hover_color=SUCCESS_HOVER,
                text_color="#1a1a1a",
            )
            self.header_label.configure(text="SpinEdge: PAUSED", text_color=WARNING)
        else:
            self.pause_btn.configure(
                text="\u23F8 PAUSE", fg_color=WARNING, hover_color="#D4940F",
                text_color="#1a1a1a",
            )
            self.header_label.configure(text="SpinEdge: ACTIVE", text_color=GOLD)

    # ─── Graph Interaction ────────────────────────────────────────────

    def on_graph_scroll(self, event, direction=None):
        if not hasattr(self, 'current_graph_data') or not self.current_graph_data:
            return

        if direction is None:
            delta = 1 if event.delta > 0 else -1
        else:
            delta = direction

        total_points = len(self.current_graph_data)
        max_offset = max(0, total_points - self.graph_view_size)
        step = int(self.graph_view_size * 0.1) or 1
        new_offset = self.graph_scroll_offset + (delta * step)
        self.graph_scroll_offset = max(0, min(new_offset, max_offset))
        self.draw_mini_graph(self.current_graph_data, getattr(self, 'current_graph_markers', None))

    def on_graph_pan_start(self, event):
        self.is_dragging_graph = True
        self.graph_drag_start_x = event.x
        self.graph_drag_start_offset = self.graph_scroll_offset
        self.graph_canvas.config(cursor="fleur")

    def on_graph_pan_drag(self, event):
        if not self.is_dragging_graph:
            return
        if not hasattr(self, 'current_graph_data') or not self.current_graph_data:
            return

        dx = event.x - self.graph_drag_start_x
        w = self.graph_canvas.winfo_width()
        points_per_pixel = self.graph_view_size / w if w > 0 else 0.2
        offset_delta = int(dx * points_per_pixel)
        new_offset = self.graph_drag_start_offset + offset_delta
        total_points = len(self.current_graph_data)
        max_offset = max(0, total_points - self.graph_view_size)
        self.graph_scroll_offset = max(0, min(new_offset, max_offset))
        self.draw_mini_graph(self.current_graph_data, getattr(self, 'current_graph_markers', None))

    def on_graph_reset(self, event):
        """Double click to reset to live view"""
        self.graph_scroll_offset = 0
        self.draw_mini_graph(self.current_graph_data, getattr(self, 'current_graph_markers', None))
        self.graph_canvas.config(cursor="")
        self.is_dragging_graph = False

    def draw_mini_graph(self, data_points, markers=None):
        """Draw scrollable graph with gradient fill and visibility toggle."""
        self.graph_canvas.delete("all")
        w = self.graph_canvas.winfo_width()
        h = self.graph_canvas.winfo_height()

        if w <= 1: w = 230
        if h <= 1: h = 100

        self.current_graph_data = data_points
        self.current_graph_markers = markers
        self.current_graph_min = 0
        self.current_graph_max = 0

        # Bind Resize Event (One-time)
        if not hasattr(self, '_resize_bind_id'):
            self.graph_canvas.bind("<Configure>", self.on_resize)
            self._resize_bind_id = True

        if not hasattr(self, '_hover_bind_id'):
            self.graph_canvas.bind("<Motion>", self.on_graph_hover)
            self._hover_bind_id = True

        if not data_points:
            self.graph_canvas.create_text(
                w / 2, h / 2, text="No Data", fill=TEXT_MUTED,
                font=FONT_CAPTION,
            )
            return

        # Viewport Logic
        total_points = len(data_points)

        if self.fit_graph_var.get():
            visible_data = data_points
            start_idx = 0
            end_idx = total_points
            visible_markers = markers if markers else []
        else:
            max_offset = max(0, total_points - self.graph_view_size)
            self.graph_scroll_offset = max(0, min(self.graph_scroll_offset, max_offset))
            end_idx = total_points - int(self.graph_scroll_offset)
            start_idx = max(0, end_idx - self.graph_view_size)
            visible_data = data_points[start_idx:end_idx]
            visible_markers = []
            if markers:
                for item in markers:
                    idx = item[0]
                    if start_idx <= idx < end_idx:
                        rel_idx = idx - start_idx
                        visible_markers.append((rel_idx,) + item[1:])

        if not visible_data:
            self.graph_canvas.create_text(
                w / 2, h / 2, text="No Data in View", fill=TEXT_MUTED,
                font=FONT_CAPTION,
            )
            return

        min_val = min(min(visible_data), 0)
        max_val = max(max(visible_data), 0)

        padding = (max_val - min_val) * 0.05 if max_val != min_val else 1.0
        min_val -= padding
        max_val += padding

        self.current_graph_min = min_val
        self.current_graph_max = max_val
        self.current_visible_start_idx = start_idx

        rng = max_val - min_val if max_val != min_val else 1.0

        def get_y(v):
            return h - ((v - min_val) / rng * h)

        step_x = w / (len(visible_data) - 1) if len(visible_data) > 1 else w / 2
        self.current_step_x = step_x

        # 1. Zero line
        zero_y = get_y(0)
        self.graph_canvas.create_line(
            0, zero_y, w, zero_y, fill=BORDER_SUBTLE, dash=(3, 3), width=1,
        )

        # 2. Markers
        if self.show_markers_var.get() and visible_markers:
            for item in visible_markers:
                duration = ""
                strategy = ""
                if len(item) == 4:
                    idx, label, strategy, duration = item
                elif len(item) == 3:
                    idx, label, strategy = item
                else:
                    idx, label = item

                marker_x = idx * step_x
                self.graph_canvas.create_line(
                    marker_x, 0, marker_x, h, fill=GOLD_DIM, dash=(2, 4), width=1,
                )
                self.graph_canvas.create_text(
                    marker_x + 4, h - 2, text=label, anchor="sw",
                    fill=GOLD, font=FONT_TINY,
                )
                if duration:
                    self.graph_canvas.create_text(
                        marker_x + 25, h - 2, text=f"({duration})", anchor="sw",
                        fill=TEXT_MUTED, font=FONT_TINY,
                    )
                if strategy:
                    self.graph_canvas.create_text(
                        marker_x + 4, 3, text=strategy, anchor="nw",
                        fill=TEXT_MUTED, font=FONT_TINY,
                    )

        # 3. Gradient fill + line
        if len(visible_data) > 1:
            is_positive = visible_data[-1] >= 0
            line_color = SUCCESS if is_positive else DANGER

            # Build point list for the line
            line_points = []
            for i, val in enumerate(visible_data):
                x_pt = i * step_x
                y_pt = get_y(val)
                line_points.append((x_pt, y_pt))

            # Draw gradient fill (semi-transparent strips from line to zero)
            fill_base_color = (52, 211, 153) if is_positive else (248, 113, 113)
            num_strips = min(8, len(line_points) - 1)
            strip_size = max(1, len(line_points) // num_strips) if num_strips > 0 else 1

            for strip_idx in range(num_strips):
                alpha = int(40 * (1.0 - strip_idx / num_strips))  # Fading alpha
                r, g, b = fill_base_color
                # Approximate alpha blending with the dark background (18, 20, 28)
                bg_r, bg_g, bg_b = 18, 20, 28
                t = alpha / 255.0
                mr = int(r * t + bg_r * (1 - t))
                mg = int(g * t + bg_g * (1 - t))
                mb = int(b * t + bg_b * (1 - t))
                fill_color = f"#{mr:02x}{mg:02x}{mb:02x}"

                start_i = strip_idx * strip_size
                end_i = min(start_i + strip_size + 1, len(line_points))
                if start_i >= len(line_points) - 1:
                    break

                polygon = []
                for i in range(start_i, end_i):
                    polygon.append(line_points[i])
                # Close polygon to zero line
                polygon.append((line_points[end_i - 1][0], zero_y))
                polygon.append((line_points[start_i][0], zero_y))

                flat = []
                for pt in polygon:
                    flat.extend(pt)
                if len(flat) >= 6:
                    self.graph_canvas.create_polygon(*flat, fill=fill_color, outline="")

            # Draw main line
            flat_line = []
            for pt in line_points:
                flat_line.extend(pt)
            self.graph_canvas.create_line(*flat_line, fill=line_color, width=2, smooth=True)

            # Endpoint dot
            last_x, last_y = line_points[-1]
            self.graph_canvas.create_oval(
                last_x - 3, last_y - 3, last_x + 3, last_y + 3,
                fill=line_color, outline="#FFFFFF", width=1,
            )

        # 4. Min/Max labels
        self.graph_canvas.create_text(
            3, 3, text=f"${max_val:.1f}", anchor="nw",
            fill=TEXT_MUTED, font=("Segoe UI", 7),
        )
        self.graph_canvas.create_text(
            3, h - 3, text=f"${min_val:.1f}", anchor="sw",
            fill=TEXT_MUTED, font=("Segoe UI", 7),
        )

        # 5. Scrollbar Indicator
        if not self.fit_graph_var.get() and total_points > self.graph_view_size:
            bar_h = 3
            visible_ratio = self.graph_view_size / total_points
            bar_w = max(20, w * visible_ratio)
            max_scroll = total_points - self.graph_view_size
            scroll_ratio = self.graph_scroll_offset / max_scroll if max_scroll > 0 else 0
            bar_x = (1 - scroll_ratio) * (w - bar_w)
            self.graph_canvas.create_rectangle(
                bar_x, h - bar_h, bar_x + bar_w, h,
                fill=TEXT_MUTED, outline="",
            )

    def on_resize(self, event):
        if hasattr(self, 'current_graph_data') and self.current_graph_data:
            markers = getattr(self, 'current_graph_markers', None)
            self.draw_mini_graph(self.current_graph_data, markers)

    def on_graph_hover(self, event):
        if not hasattr(self, 'current_graph_data') or not self.current_graph_data:
            return

        data = self.current_graph_data
        start_idx = getattr(self, 'current_visible_start_idx', 0)
        total_visible = len(data) - start_idx
        if total_visible <= 0:
            return

        step_x = getattr(self, 'current_step_x', 1)
        rel_idx = int((event.x + step_x / 2) / step_x)
        abs_idx = start_idx + rel_idx
        abs_idx = max(0, min(abs_idx, len(data) - 1))
        val = data[abs_idx]

        x = rel_idx * step_x
        min_val = self.current_graph_min
        max_val = self.current_graph_max
        h = self.graph_canvas.winfo_height()
        w = self.graph_canvas.winfo_width()
        rng = max_val - min_val if max_val != min_val else 1.0
        y = h - ((val - min_val) / rng * h)

        self.graph_canvas.delete("hover")

        # Crosshair
        self.graph_canvas.create_line(x, 0, x, h, fill=TEXT_MUTED, dash=(1, 2), tags="hover")
        self.graph_canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                                       fill=TEXT_PRIMARY, outline=GOLD, width=1, tags="hover")

        # Tooltip
        tip_x = x + 10 if x < w / 2 else x - 80
        tip_y = y - 25 if y > h / 2 else y + 10

        text = f"#{abs_idx + 1}  ${val:.2f}"
        color = SUCCESS if val >= 0 else DANGER

        self.graph_canvas.create_rectangle(
            tip_x - 2, tip_y - 2, tip_x + 76, tip_y + 20,
            fill=BG_CARD, outline=BORDER_DEFAULT, tags="hover",
        )
        self.graph_canvas.create_text(
            tip_x + 37, tip_y + 9, text=text, fill=color,
            font=FONT_TINY, tags="hover",
        )

    def reset_hover(self, event):
        self.graph_canvas.delete("hover")

    def start_global_timer(self):
        """Called by main app when bot starts to begin the global elapsed timer."""
        import time as _time
        self._timer_start_ref = _time.time()
        self._tick_global_timer()

    def stop_global_timer(self):
        """Called when bot stops — freezes the timer display."""
        self._timer_start_ref = None

    def _tick_global_timer(self):
        if not hasattr(self, '_timer_start_ref') or self._timer_start_ref is None:
            return
        import time as _time
        elapsed = int(_time.time() - self._timer_start_ref)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        try:
            self.global_time_label.configure(text=f"{h:02}:{m:02}:{s:02}")
        except Exception:
            return
        self.after(1000, self._tick_global_timer)

    # ─── Main update entry point ──────────────────────────────────────

    def update_info(self, number=None, result=None, pnl=None, streak=None,
                    global_time=None, time_rem=None,
                    graph_data=None, graph_markers=None,
                    global_pnl=None, global_target=None, global_stop=None,
                    session_target=None, session_stop=None, trailing_stop=None,
                    balance=None, strategy_name=None, is_paused=None,
                    header=None, next_sess=None, session_num=None,
                    round_num=None, win_rate=None, current_bet=None,
                    session_pnl=None, session_target_val=None, session_stop_val=None,
                    bundle_name=None,
                    **kwargs):

        if bundle_name is not None:
            display = str(bundle_name).strip()
            if display and display not in ("Select Bundle...", "No Bundles Found", ""):
                self.bundle_label.configure(text=f"\U0001F4E6 {display}", text_color=TEXT_SECONDARY)
            else:
                self.bundle_label.configure(text="")

        if is_paused is not None:
            self.update_pause_state(is_paused)

        if header is not None:
            self.header_label.configure(text=str(header))
            if "STOP" in str(header) or "MET" in str(header):
                self.header_label.configure(text_color=DANGER)
            else:
                self.header_label.configure(text_color=GOLD)

        if next_sess is not None:
            self.next_sess_label.configure(text=f"Next: {next_sess}")

        if strategy_name:
            # Show only the strategy name — strip rules, labels, bracket content
            short_name = str(strategy_name).split(":")[0].split("[")[0].split("(")[0].strip()
            if session_num is not None:
                self.strategy_label.configure(text=f"\u25B8 #{session_num} | {short_name}")
            else:
                self.strategy_label.configure(text=f"\u25B8 {short_name}")
        elif session_num is not None:
            current_text = self.strategy_label.cget("text").replace("\u25B8 ", "")
            if "|" in current_text:
                _, strat = current_text.split("|", 1)
                self.strategy_label.configure(text=f"\u25B8 #{session_num} | {strat.strip()}")
            else:
                self.strategy_label.configure(text=f"\u25B8 #{session_num} | {current_text.strip()}")

        # Number badge with roulette color
        if number is not None:
            self._draw_number_badge(str(number), TEXT_PRIMARY)

        if result is not None:
            self.result_label.configure(text=str(result))
            if "WIN" in str(result).upper():
                self.result_label.configure(text_color=SUCCESS)
            elif "LOSS" in str(result).upper():
                self.result_label.configure(text_color=DANGER)
            else:
                self.result_label.configure(text_color=TEXT_MUTED)

        if balance is not None:
            self.balance_label.configure(text=f"${balance}")

        if pnl is not None:
            try:
                val_str = str(pnl).replace('$', '').replace(',', '')
                val = float(val_str)
                color = SUCCESS if val >= 0 else DANGER
                sign = "+" if val >= 0 else ""
                self.pnl_label.configure(text=f"{sign}${val:.2f}", text_color=color)
            except Exception:
                self.pnl_label.configure(text=str(pnl))

        if streak is not None:
            streak_str = str(streak)
            try:
                if "W" in str(streak) or int(streak) > 0:
                    self.streak_label.configure(text=streak_str, text_color=SUCCESS)
                elif "L" in str(streak) or int(streak) < 0:
                    self.streak_label.configure(text=streak_str, text_color=DANGER)
                else:
                    self.streak_label.configure(text=streak_str, text_color=TEXT_PRIMARY)
            except (ValueError, TypeError):
                self.streak_label.configure(text=streak_str, text_color=TEXT_PRIMARY)

        if round_num is not None:
            self.round_label.configure(text=str(round_num))

        if win_rate is not None:
            try:
                wr = float(str(win_rate).replace('%', ''))
                color = SUCCESS if wr >= 50 else (WARNING if wr >= 40 else DANGER)
                self.winrate_label.configure(text=f"{wr:.0f}%", text_color=color)
            except (ValueError, TypeError):
                self.winrate_label.configure(text=str(win_rate))

        if current_bet is not None:
            self.bet_label.configure(text=str(current_bet))

        if time_rem is not None:
            self.time_rem_label.configure(text=str(time_rem))

        if global_time is not None:
            self.global_time_label.configure(text=str(global_time))
            if not hasattr(self, '_timer_start_ref'):
                import time as _time
                self._timer_start_ref = _time.time()
                self._tick_global_timer()

        if global_pnl is not None:
            val = float(str(global_pnl).replace("$", "")) if isinstance(global_pnl, str) else global_pnl
            color = SUCCESS if val >= 0 else DANGER
            sign = "+" if val >= 0 else ""
            self.global_pnl_label.configure(text=f"{sign}${val:.2f}", text_color=color)

        if global_target is not None:
            self.global_limit_label.configure(text=str(global_target))

        if global_stop is not None:
            self.global_stop_label.configure(text=str(global_stop))

        if session_target is not None:
            self.session_limit_label.configure(text=str(session_target))

        if session_stop is not None:
            self.session_stop_label.configure(text=str(session_stop))

        if trailing_stop is not None:
            self.trailing_stop_label.configure(text=str(trailing_stop))

        # Update progress bars
        if session_pnl is not None:
            self._session_pnl = float(session_pnl)
        if session_target_val is not None:
            self._session_target_val = float(session_target_val)
        if session_stop_val is not None:
            self._session_stop_val = float(session_stop_val)

        self._update_progress_bars()

        if graph_data:
            self.draw_mini_graph(graph_data, markers=graph_markers)

    def _update_progress_bars(self):
        """Refresh the session target and stop loss progress bars."""
        pnl = self._session_pnl

        # Target progress (pnl / target)
        if self._session_target_val > 0:
            ratio = max(0, pnl / self._session_target_val)
            self._draw_progress_bar(self._target_bar_canvas, ratio, SUCCESS, warn_threshold=1.1)
            self._target_pct_label.configure(text=f"{min(ratio * 100, 999):.0f}%",
                                              text_color=SUCCESS if ratio < 1.0 else GOLD)
        else:
            self._draw_progress_bar(self._target_bar_canvas, 0, SUCCESS)
            self._target_pct_label.configure(text="--", text_color=TEXT_MUTED)

        # Stop loss progress (abs(pnl) / stop_val when losing)
        if self._session_stop_val > 0:
            loss = max(0, -pnl)  # Only show when losing
            ratio = loss / self._session_stop_val
            self._draw_progress_bar(self._stop_bar_canvas, ratio, WARNING, warn_threshold=0.75)
            self._stop_pct_label.configure(text=f"{min(ratio * 100, 999):.0f}%",
                                            text_color=WARNING if ratio < 0.75 else DANGER)
        else:
            self._draw_progress_bar(self._stop_bar_canvas, 0, WARNING)
            self._stop_pct_label.configure(text="--", text_color=TEXT_MUTED)

    def attach_to_window(self, target_win):
        """Snap to top-right of target window"""
        if target_win:
            try:
                x = target_win.left + target_win.width - 240
                y = target_win.top + 10
                self.geometry(f"+{x}+{y}")
                self.lift()
            except Exception:
                pass

    def set_favorites(self, strategy_favs=None, bundle_favs=None,
                      active_strategy=None, active_bundle=None,
                      on_strategy_click=None, on_bundle_click=None,
                      max_each=3):
        """Render favorite-pills in TWO rows:
            • strategy pills (★) → next to the strategy label
            • bundle pills (◆)   → next to the bundle label (top of HUD)

        Numbered superscripts (1..9) hint at the Ctrl+N global hotkey binding.
        Hotkey ordering still goes strategies-first-then-bundles so the badges
        match what _hotkey_dispatch_slot resolves.

        No-ops when the favorites signature is unchanged so it's cheap to call
        from update_hud_safe on every HUD tick.
        """
        try:
            strategy_favs = list(strategy_favs or [])[:max_each]
            bundle_favs = list(bundle_favs or [])[:max_each]
            sig = (tuple(strategy_favs), tuple(bundle_favs),
                   active_strategy, active_bundle)
            if sig == self._last_fav_signature:
                return
            self._last_fav_signature = sig
            self._fav_strategy_click_cb = on_strategy_click
            self._fav_bundle_click_cb = on_bundle_click

            strat_container = getattr(self, "_fav_pills_container", None)
            bundle_container = getattr(self, "_fav_bundle_pills_container", None)

            def _clear(container):
                if container is None:
                    return
                for child in container.winfo_children():
                    try:
                        child.destroy()
                    except Exception:
                        pass

            _clear(strat_container)
            _clear(bundle_container)

            superscripts = ["¹", "²", "³", "⁴", "⁵",
                            "⁶", "⁷", "⁸", "⁹"]

            def _make_pill(container, text, is_active, click_cb, name):
                if container is None:
                    return
                btn = ctk.CTkButton(
                    container,
                    text=text,
                    width=24, height=18,
                    corner_radius=4,
                    font=("Segoe UI", 9, "bold" if is_active else "normal"),
                    # Green = the currently-running source (strict XOR: the
                    # caller nulls out the inactive side's active_* arg).
                    fg_color="#27ae60" if is_active else "#34495e",
                    hover_color="#2ecc71",
                    text_color="#FFFFFF",
                    command=(lambda n=name: click_cb(n)) if click_cb else None,
                )
                btn.pack(side="left", padx=1)

            slot = 0
            for name in strategy_favs:
                badge = superscripts[slot] if slot < len(superscripts) else ""
                is_active = (name == active_strategy)
                short = (name[:5] + "…") if len(name) > 6 else name
                _make_pill(strat_container, f"★{short}{badge}", is_active,
                           on_strategy_click, name)
                slot += 1

            for name in bundle_favs:
                badge = superscripts[slot] if slot < len(superscripts) else ""
                is_active = (name == active_bundle)
                short = (name[:5] + "…") if len(name) > 6 else name
                _make_pill(bundle_container, f"◆{short}{badge}", is_active,
                           on_bundle_click, name)
                slot += 1
        except Exception:
            pass

    # Alias for legacy calls
    update_hud = update_info
