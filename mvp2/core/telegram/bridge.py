"""
GuiBridge — typed, thread-safe interface between the Telegram bot and the GUI app.

All attribute access on gui_app is centralised here so:
  - bot.py never calls getattr(gui_app, ...) directly
  - Adding a new data point requires changing only this file
  - gui_app changes don't silently break the bot
"""
from __future__ import annotations

import threading
import time as _time
from copy import copy as _copy
from dataclasses import dataclass, field
from typing import List, Optional, Any


# ── Data snapshot passed to formatters / keyboards ────────────────────────────

@dataclass
class SessionData:
    # ── Status ────────────────────────────────────────────────────────────────
    is_running: bool = False
    is_paused: bool = False

    # ── Financial ─────────────────────────────────────────────────────────────
    sess_pnl: float = 0.0
    glob_pnl: float = 0.0
    peak_pnl: float = 0.0
    balance: float = 0.0

    # ── Statistics ────────────────────────────────────────────────────────────
    wins: int = 0
    losses: int = 0
    streak: int = 0                    # + = win streak, - = loss streak
    recent_results: List[bool] = field(default_factory=list)

    # ── Betting ───────────────────────────────────────────────────────────────
    current_bet: float = 0.0
    base_bet: float = 0.0
    strategy: str = "─"
    progression: str = "─"

    # ── Timing ────────────────────────────────────────────────────────────────
    time_remaining: str = "─"
    next_session_timer: str = ""
    session_duration: int = 15

    # ── Session info ──────────────────────────────────────────────────────────
    current_session: int = 1
    total_sessions: int = 1

    # ── Last roulette spin ────────────────────────────────────────────────────
    last_number: Optional[int] = None
    last_color: str = ""

    # ── Rotation ──────────────────────────────────────────────────────────────
    rotation_active: str = ""           # currently selected strategy in rotation
    rotation_next: str = ""             # peek at next strategy if known
    rotation_count: int = 0             # total strategies in the rotation list

    # ── Stop / pause context ──────────────────────────────────────────────────
    last_stop_reason: str = ""          # e.g. "Session profit target" / "Time up"
    paused_by: str = ""                 # "user" | "guardrail" | ""

    # ── Guardrails ────────────────────────────────────────────────────────────
    sess_stops_enabled: bool = False    # enable_session_stops_var (master toggle)
    sess_profit_target: float = 0.0
    sess_profit_enabled: bool = False
    sess_loss: float = 0.0
    sess_loss_enabled: bool = False
    trailing_stop: float = 0.0
    trailing_enabled: bool = False
    glob_profit_raw: str = "0"         # May be "100" or "10%"
    glob_profit_enabled: bool = False
    glob_loss_raw: str = "0"
    glob_loss_enabled: bool = False
    ext_after_win: bool = False

    # ── Mission-control extras ─────────────────────────────────────────────────
    max_bet: float = 0.0
    observation_trigger: int = 0
    risk_profile: str = ""
    strategy_source: str = ""           # 'bundle' | 'manual' | ''
    active_bundle: str = ""
    # Session streak caps
    max_win_streak: int = 0
    max_loss_streak: int = 0
    # Extensions (beyond ext_after_win)
    ext_at_high: bool = False
    max_ext_rounds: int = 0
    ext_give_up: float = 0.0
    # Inter-session gaps
    min_gap: int = 0
    max_gap: int = 0
    # Strategy rotation
    rotation_enabled: bool = False
    rotation_mode: str = ""
    rotation_trigger: str = ""
    # Escalation on loss
    esc_enabled: bool = False
    esc_multiplier: float = 2.0
    esc_max_steps: int = 0
    # HUD / notifications
    hud_visible: bool = False

    # ── Derived ───────────────────────────────────────────────────────────────
    @property
    def total_rounds(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_rounds * 100) if self.total_rounds else 0.0

    @property
    def sess_pnl_pct(self) -> float:
        return (self.sess_pnl / self.balance * 100) if self.balance else 0.0

    @property
    def glob_pnl_pct(self) -> float:
        return (self.glob_pnl / self.balance * 100) if self.balance else 0.0

    @property
    def bet_multiplier(self) -> float:
        return (self.current_bet / self.base_bet) if self.base_bet else 1.0


# ── Bridge ────────────────────────────────────────────────────────────────────

class GuiBridge:
    """Thin wrapper around the GUI app providing a clean, typed interface."""

    def __init__(self, gui_app: Any):
        self._app = gui_app
        self._cache_lock = threading.Lock()
        self._cached_data: Optional[SessionData] = None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get(self, attr: str, default: Any = None) -> Any:
        return getattr(self._app, attr, default)

    def _var(self, name: str, default: Any = "") -> Any:
        var = getattr(self._app, name, None)
        if var is None:
            return default
        try:
            return var.get()
        except Exception:
            return default

    def _f(self, val: Any, default: float = 0.0) -> float:
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _compute_time_remaining(self, is_running: bool, is_paused: bool, duration_min: int) -> str:
        """Live MM:SS string from session_start_timestamp.

        Falls back to the GUI's HUD-set value if we can't compute live (e.g. no
        session has started yet). This avoids the prior bug where time_remaining
        was always "─" if the user had the in-app HUD hidden.
        """
        if not is_running:
            return "─"
        if is_paused:
            return "⏸ paused"
        start_ts = self._get("session_start_timestamp", None)
        try:
            start_ts = float(start_ts) if start_ts is not None else None
        except (TypeError, ValueError):
            start_ts = None
        if start_ts and duration_min > 0:
            paused_total = self._f(self._get("total_paused_duration", 0.0))
            elapsed = max(0.0, _time.time() - start_ts - paused_total)
            remaining = max(0, int(duration_min * 60 - elapsed))
            return f"{remaining // 60:02d}:{remaining % 60:02d}"
        # Fallback to whatever the HUD last set
        return str(self._get("latest_time_remaining", "─"))

    # ── Data snapshot ─────────────────────────────────────────────────────────

    def get_session_data(self, recent_results: Optional[List[bool]] = None) -> SessionData:
        app = self._app
        d = SessionData()

        d.is_running = bool(self._get("bot_running", False))
        d.is_paused  = bool(self._get("is_paused", False))

        sess_pnl         = self._f(self._get("cumulative_net_profit", 0))
        glob_offset      = self._f(self._get("cumulative_profit_offset", 0))
        d.sess_pnl       = sess_pnl
        d.glob_pnl       = glob_offset + sess_pnl
        d.peak_pnl       = self._f(self._get("peak_net_profit", 0))
        d.balance        = self._f(
            self._var("balance_var") or app.config.get("current_balance", 0)
        )

        d.wins   = int(self._get("total_wins", 0))
        d.losses = int(self._get("total_losses", 0))
        d.streak = int(self._get("current_streak", 0))
        d.recent_results = list(recent_results or [])

        d.current_bet = self._f(self._get("latest_bet_amount", 0))
        d.base_bet    = self._f(self._var("base_bet_var", "0"))

        is_auto    = bool(self._var("auto_roulette_var", False))
        d.strategy = str(
            self._var("auto_roulette_strategy_var", "─")
            if is_auto else
            self._var("strategy_var", "─")
        )
        d.progression = str(
            app.config.get("progression_type")
            or self._var("progression_var", "─")
        )

        # Time remaining: prefer live computation from session_start_timestamp +
        # configured duration so the bot stays accurate even if the GUI HUD is
        # hidden (which is what previously kept latest_time_remaining stale).
        d.session_duration  = int(self._f(self._var("session_duration_var", "15")))
        d.time_remaining    = self._compute_time_remaining(d.is_running, d.is_paused, d.session_duration)
        d.next_session_timer = str(self._get("latest_next_session_timer", ""))
        d.current_session   = int(self._get("current_session_num", 1))
        d.total_sessions    = int(app.config.get("num_sessions", 1))

        # Last roulette spin shown in dashboard + win/loss notifications
        ln = self._get("latest_winning_number", None)
        try:
            d.last_number = int(ln) if ln is not None else None
        except (TypeError, ValueError):
            d.last_number = None
        d.last_color = str(self._get("latest_winning_color", "") or "")

        # Rotation context — read the rotation list and locate the active strategy
        rot_str = str(self._var("rotation_strategies_var", "") or app.config.get("rotation_strategies", ""))
        rot_items = [s.strip().split(":")[0] for s in rot_str.split(",") if s.strip()]
        d.rotation_count = len(rot_items)
        active_strategy = str(app.config.get("strategy", "") or d.strategy)
        active_base = active_strategy.split(":")[0].strip()
        if active_base and rot_items:
            try:
                idx = rot_items.index(active_base)
                d.rotation_active = rot_items[idx]
                if idx + 1 < len(rot_items):
                    d.rotation_next = rot_items[idx + 1]
            except ValueError:
                d.rotation_active = active_base
        elif active_base:
            d.rotation_active = active_base

        d.last_stop_reason = str(self._get("last_stop_reason", "") or "")
        d.paused_by        = str(self._get("paused_by", "") or "")

        # Guardrails
        sess_stops_on        = bool(self._var("enable_session_stops_var", False))
        d.sess_stops_enabled  = sess_stops_on
        d.sess_profit_target = self._f(self._var("profit_target_var", "0"))
        d.sess_profit_enabled = sess_stops_on and d.sess_profit_target > 0
        d.sess_loss          = self._f(self._var("max_loss_var", "0"))
        d.sess_loss_enabled  = sess_stops_on and d.sess_loss > 0
        d.trailing_stop      = self._f(self._var("trailing_stop_amount_var", "0"))
        d.trailing_enabled   = bool(self._var("enable_trailing_stop_var", False))
        d.glob_profit_raw    = str(self._var("global_profit_stop_var", "0"))
        d.glob_profit_enabled = bool(self._var("enable_global_stop_var", False))
        d.glob_loss_raw      = str(self._var("global_stop_loss_var", "0"))
        d.glob_loss_enabled  = bool(self._var("enable_global_stop_var", False))
        d.ext_after_win      = bool(self._var("session_ext_after_win_var", False))

        # ── Mission-control extras ────────────────────────────────────────────
        d.max_bet            = self._f(self._var("max_bet_var", "0"))
        d.observation_trigger = int(self._f(self._var("observation_trigger_var", "0")))
        d.risk_profile       = str(self._var("dash_risk_profile_var", "") or "")
        d.strategy_source    = str(self._get("active_strategy_source", "") or "")
        d.active_bundle      = str(self._var("dashboard_bundle_var", "") or "")
        d.max_win_streak     = int(self._f(self._var("max_session_wins_streak_var", "0")))
        d.max_loss_streak    = int(self._f(self._var("max_session_losses_streak_var", "0")))
        d.ext_at_high        = bool(self._var("session_ext_at_high_var", False))
        d.max_ext_rounds     = int(self._f(self._var("max_ext_rounds_var", "0")))
        d.ext_give_up        = self._f(self._var("ext_give_up_var", "0"))
        d.min_gap            = int(self._f(self._var("min_gap_var", "0")))
        d.max_gap            = int(self._f(self._var("max_gap_var", "0")))
        d.rotation_enabled   = bool(self._var("enable_strategy_rotation_var", False))
        d.rotation_mode      = str(self._var("rotation_mode_var", "") or "")
        d.rotation_trigger   = str(self._var("rotation_trigger_var", "") or "")
        d.esc_enabled        = bool(self._var("enable_escalation_on_loss_var", False))
        d.esc_multiplier     = self._f(self._var("escalation_multiplier_var", "2.0"), 2.0)
        d.esc_max_steps      = int(self._f(self._var("escalation_max_steps_var", "0")))
        d.hud_visible        = bool(self._var("show_hud_var", False))

        return d

    # ── Thread-safe session data cache ────────────────────────────────────────
    # get_session_data() calls Tkinter var.get() and MUST only run on the GUI
    # thread.  The cache lets the bot thread read a recent snapshot safely.

    def schedule_cache_refresh(self, recent_results: Optional[List[bool]] = None) -> None:
        """Schedule a GUI-thread snapshot.  Safe to call from any thread."""
        rr = list(recent_results or [])
        try:
            self._app.root.after(0, lambda: self._refresh_cache_on_gui(rr))
        except Exception:
            pass

    def _refresh_cache_on_gui(self, recent_results: List[bool]) -> None:
        """Must be called on the Tkinter main thread only."""
        try:
            data = self.get_session_data(recent_results)
            with self._cache_lock:
                self._cached_data = data
        except Exception:
            pass

    def get_cached_data(self, recent_results: Optional[List[bool]] = None) -> SessionData:
        """Return the last GUI-thread snapshot.  Thread-safe, never calls var.get()."""
        with self._cache_lock:
            if self._cached_data is not None:
                if recent_results is not None:
                    d = _copy(self._cached_data)
                    d.recent_results = list(recent_results)
                    return d
                return self._cached_data
        # No cache yet — safe defaults
        return SessionData(recent_results=list(recent_results or []))

    # ── Actions ───────────────────────────────────────────────────────────────

    def start_session(self) -> None:
        # Always dispatch to the Tkinter main thread — start_bot touches GUI state.
        self._app.root.after(0, self._app.start_bot)

    def stop_session(self) -> None:
        # Route through root.after so Tkinter variables are only touched on the GUI thread.
        self._app.root.after(0, self._app.stop_bot)

    def toggle_pause(self) -> None:
        self._app.root.after(0, self._app.toggle_pause)

    def set_config(self, key: str, value: Any, var_name: str) -> None:
        app = self._app
        app.root.after(
            0,
            lambda k=key, v=value, vn=var_name: app.handle_remote_config(k, v, vn),
        )

    def get_strategies(self) -> List[str]:
        strategies = getattr(self._app, "strategies", {})
        if isinstance(strategies, dict):
            return list(strategies.keys())
        return []

    def get_favorite_strategies(self) -> List[str]:
        """Favorited strategies (★ pills) — read directly, no thread marshal needed."""
        fn = getattr(self._app, "_get_favorite_strategies", None)
        if callable(fn):
            try:
                return list(fn() or [])
            except Exception:
                return []
        return []

    def get_favorite_bundles(self) -> List[str]:
        """Favorited dashboard bundles (◆ pills)."""
        fn = getattr(self._app, "_get_favorite_dashboard_bundles", None)
        if callable(fn):
            try:
                return list(fn() or [])
            except Exception:
                return []
        return []

    def active_strategy(self) -> str:
        try:
            return str(self._app.config.get("strategy", "") or "")
        except Exception:
            return ""

    def active_bundle(self) -> str:
        try:
            var = getattr(self._app, "dashboard_bundle_var", None)
            return str(var.get()) if var is not None else ""
        except Exception:
            return ""

    def request_strategy_swap(self, name: str) -> None:
        """Queue a round-boundary strategy swap from the Telegram thread."""
        self._app.root.after(0, lambda n=name: self._app._on_quick_toggle_click(n))

    def request_bundle_swap(self, name: str) -> None:
        """Load a bundle from the Telegram thread (mid-session: also queues a
        strategy swap to the bundle's strategy)."""
        self._app.root.after(0, lambda n=name: self._app._on_dashboard_bundle_pill_click(n))

    def get_graph_png(self):
        if hasattr(self._app, "get_graph_png"):
            try:
                return self._app.get_graph_png()
            except Exception:
                return None
        return None

    def toggle_var(self, key: str, var_name: str) -> bool:
        """Toggle a boolean config var on the GUI thread.

        The actual var.get() runs on the GUI thread via root.after to avoid
        calling Tkinter from the bot thread.  Returns an optimistic new value
        based on the cached snapshot (for immediate UI feedback).
        """
        # Optimistic new value from cache — avoids var.get() from bot thread
        _VAR_FIELD: dict = {
            "enable_session_stops_var": "sess_stops_enabled",
            "enable_trailing_stop_var": "trailing_enabled",
            "enable_global_stop_var":   "glob_profit_enabled",
        }
        with self._cache_lock:
            cached = self._cached_data
        field_name = _VAR_FIELD.get(var_name)
        if cached is not None and field_name:
            optimistic_new = not bool(getattr(cached, field_name, False))
        else:
            optimistic_new = True  # safe default: assume we're enabling

        # Perform actual toggle on the GUI thread
        def _do_toggle():
            var = getattr(self._app, var_name, None)
            if var is None:
                return
            try:
                actual_new = not bool(var.get())
            except Exception:
                actual_new = optimistic_new
            self._app.handle_remote_config(key, actual_new, var_name)

        self._app.root.after(0, _do_toggle)
        return optimistic_new

    # ── Mission-control actions ────────────────────────────────────────────────

    def set_var_choice(self, key: str, value: Any, var_name: str) -> None:
        """Set a string/enum config value (risk profile, rotation mode/trigger,
        progression). Same marshalling as set_config — handle_remote_config sets
        both self.config[key] and the named tk var on the GUI thread."""
        self.set_config(key, value, var_name)

    def set_risk_profile(self, profile: str) -> None:
        """Apply a dashboard risk profile (recomputes base bet / stop loss).
        Sets the var then calls the GUI's preview/apply hook on the Tk thread."""
        def _apply():
            try:
                var = getattr(self._app, "dash_risk_profile_var", None)
                if var is not None:
                    var.set(profile)
                fn = getattr(self._app, "update_risk_profile_preview", None)
                if callable(fn):
                    fn()
            except Exception:
                pass
        self._app.root.after(0, _apply)

    def set_balance(self, value: float) -> None:
        """Update the working balance (config['current_balance'] + balance_var)."""
        self.set_config("current_balance", float(value), "balance_var")

    def session_reset(self) -> None:
        """Reset session stats / refresh OCR via the overlay refresh hook."""
        fn = getattr(self._app, "manual_refresh_overlay", None)
        if callable(fn):
            self._app.root.after(0, fn)

    def toggle_hud(self) -> bool:
        """Flip the in-app HUD overlay on/off. Returns optimistic new state."""
        with self._cache_lock:
            cached = self._cached_data
        optimistic_new = not bool(getattr(cached, "hud_visible", False)) if cached else True

        def _do():
            try:
                var = getattr(self._app, "show_hud_var", None)
                if var is None:
                    return
                var.set(not bool(var.get()))
                fn = getattr(self._app, "toggle_hud", None)
                if callable(fn):
                    fn()
            except Exception:
                pass
        self._app.root.after(0, _do)
        return optimistic_new

    def set_verbose_notifications(self, on: bool, telegram_bot: Any = None) -> None:
        """Enable/disable per-round WIN/LOSS Telegram notifications."""
        if telegram_bot is not None:
            try:
                telegram_bot.verbose_round_notifications = bool(on)
            except Exception:
                pass

    def get_all_bundles(self) -> List[str]:
        """List ALL local bundle names (json + spine), de-duplicated and sorted.
        Pure filesystem read — safe to call from the bot thread (no tk access)."""
        import os, glob
        try:
            bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
            names = set()
            for pat in ("*.json", "*.spine"):
                for p in glob.glob(os.path.join(bundles_dir, pat)):
                    names.add(os.path.splitext(os.path.basename(p))[0])
            return sorted(names)
        except Exception:
            return []
