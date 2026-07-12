"""
Keyboard builders — pure functions that return InlineKeyboardMarkup objects.
All callback_data strings are defined as constants at the top so
handlers.py can import them instead of duplicating string literals.
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    InlineKeyboardButton = None  # type: ignore
    InlineKeyboardMarkup = None  # type: ignore

if TYPE_CHECKING:
    from .bridge import SessionData

# ── Callback data constants ───────────────────────────────────────────────────

CB_DASHBOARD      = "menu_dashboard"
CB_SETTINGS       = "menu_settings"
CB_GUARDRAILS     = "menu_guardrails"
CB_SESSION        = "menu_session"
CB_STRATEGY       = "menu_strategy"
CB_STATS          = "menu_stats"
CB_GRAPH          = "get_graph"
CB_START          = "start_session"
CB_STOP           = "stop_session"
CB_TOGGLE_PAUSE   = "toggle_pause"
CB_EDIT_BASE_BET  = "edit_base_bet"
CB_EDIT_S_PROFIT  = "edit_sess_profit"
CB_EDIT_S_LOSS    = "edit_sess_loss"
CB_EDIT_TRAILING  = "edit_trailing"
CB_EDIT_G_PROFIT  = "edit_glob_profit"
CB_EDIT_G_LOSS    = "edit_glob_loss"
CB_EDIT_DURATION  = "edit_duration"
CB_EDIT_N_SESS    = "edit_num_sessions"
CB_TOGGLE_S_STOPS = "toggle_sess_stops"
CB_TOGGLE_TRAIL   = "toggle_trailing"
CB_TOGGLE_G_STOP  = "toggle_glob_stop"
CB_TOGGLE_EXT_WIN = "toggle_ext_win"
CB_CONFIRM_YES    = "confirm_yes"
CB_CONFIRM_NO     = "confirm_no"

# Prefix for strategy selection (index appended)
CB_SET_STRAT_PFX  = "set_strategy_"

# Quick-swap (favorites) — index appended; resolved against the favorites
# snapshot the bot sent in the keyboard, so the user can't accidentally swap
# to a name that changed between menu render and click.
CB_SWAP_STRAT_PFX  = "swap_strat_"
CB_SWAP_BUNDLE_PFX = "swap_bundle_"

# ── Mission-control: hub + submenus ───────────────────────────────────────────
CB_CONTROL        = "menu_control"
CB_BETTING        = "menu_betting"
CB_RISK           = "menu_risk"
CB_ROTATION       = "menu_rotation"
CB_ROTATION_MODE  = "menu_rotation_mode"
CB_ROTATION_TRIG  = "menu_rotation_trig"
CB_ESCALATION     = "menu_escalation"
CB_BUNDLES        = "menu_bundles"
CB_PROGRESSION    = "menu_progression"
CB_QUICK          = "menu_quick"

# ── Mission-control: edit prompts ─────────────────────────────────────────────
CB_EDIT_MAX_BET     = "edit_max_bet"
CB_EDIT_OBS         = "edit_observation"
CB_EDIT_WIN_STREAK  = "edit_win_streak"
CB_EDIT_LOSS_STREAK = "edit_loss_streak"
CB_EDIT_ESC_MULT    = "edit_esc_mult"
CB_EDIT_ESC_STEPS   = "edit_esc_steps"
CB_EDIT_MAX_EXT     = "edit_max_ext"
CB_EDIT_EXT_GIVEUP  = "edit_ext_giveup"
CB_EDIT_BALANCE     = "edit_balance"
CB_EDIT_MIN_GAP     = "edit_min_gap"
CB_EDIT_MAX_GAP     = "edit_max_gap"
CB_EDIT_ROT_LIST    = "edit_rot_list"

# ── Mission-control: toggles ──────────────────────────────────────────────────
CB_TOGGLE_EXT_HIGH  = "toggle_ext_high"
CB_TOGGLE_ROTATION  = "toggle_rotation"
CB_TOGGLE_ESC       = "toggle_escalation"
CB_TOGGLE_HUD       = "toggle_hud"
CB_TOGGLE_VERBOSE   = "toggle_verbose"

# ── Mission-control: pickers (index/value appended) ───────────────────────────
CB_RISK_PFX        = "risk_"        # index into RISK_PROFILES
CB_PROG_PFX        = "prog_"        # index into PROGRESSIONS
CB_ROT_MODE_PFX    = "rotmode_"     # index into ROTATION_MODES
CB_ROT_TRIG_PFX    = "rottrig_"     # index into ROTATION_TRIGGERS
CB_BUNDLE_PFX      = "bundle_"      # index into bundle_browse_cache
CB_BUNDLE_PAGE_PFX = "bpage_"       # page index for the bundle browser

# ── Mission-control: quick actions ────────────────────────────────────────────
CB_RESET          = "act_reset"
CB_PANIC          = "act_panic"     # emergency stop

# ── Shared option lists (kept here so bot.py imports one source of truth) ──────
RISK_PROFILES = [
    "Use Bundle Values",
    "Auto (Smart Default)",
    "Conservative (0.5% Risk)",
    "Balanced (1% Risk)",
    "Aggressive (5.0% Risk)",
]
PROGRESSIONS = ["flat", "martingale", "fibonacci", "dalembert", "custom_sequence", "dynamic"]
ROTATION_MODES = ["sequential", "random", "smart_ranking", "smart_ranking_reverse"]
ROTATION_TRIGGERS = ["session_end", "on_loss"]
_BUNDLES_PER_PAGE = 8


# ── Button factory ────────────────────────────────────────────────────────────

def _b(text: str, data: str) -> "InlineKeyboardButton":
    return InlineKeyboardButton(text, callback_data=data)


# ── Keyboards ─────────────────────────────────────────────────────────────────

class Keyboards:

    @staticmethod
    def main_dashboard(is_running: bool, is_paused: bool) -> "InlineKeyboardMarkup":
        """Primary dashboard controls."""
        row1: list = []
        if is_running:
            label = "▶️ Resume" if is_paused else "⏸ Pause"
            row1.append(_b(label, CB_TOGGLE_PAUSE))
            row1.append(_b("⏹ Stop", CB_STOP))
        else:
            row1.append(_b("▶️ Start", CB_START))

        row2 = [
            _b("🎛 Control", CB_CONTROL),
            _b("⚡ Quick",   CB_QUICK),
        ]
        row3 = [
            _b("⚙️ Settings", CB_SETTINGS),
            _b("📊 Stats",    CB_STATS),
            _b("📈 Graph",    CB_GRAPH),
        ]
        return InlineKeyboardMarkup([row1, row2, row3])

    # ── Mission-control hub ────────────────────────────────────────────────────

    @staticmethod
    def control_center(data: "SessionData") -> "InlineKeyboardMarkup":
        """The hub — every controllable area is one tap from here."""
        src = (data.strategy_source or "").lower()
        src_tag = "📦 Bundle" if src == "bundle" else ("🎯 Strategy" if src == "manual" else "—")
        rows = [
            [
                _b("▶️ Start" if not data.is_running else "⏹ Stop",
                   CB_START if not data.is_running else CB_STOP),
                _b("⏸ Pause" if (data.is_running and not data.is_paused) else "▶️ Resume",
                   CB_TOGGLE_PAUSE),
            ],
            [_b(f"🎯 Strategy / Bundle  ({src_tag})", CB_STRATEGY)],
            [_b("📦 Browse Bundles →", CB_BUNDLES), _b("⭐ Favorites Swap", CB_STRATEGY)],
            [_b("💵 Betting →", CB_BETTING), _b("🛡 Guardrails →", CB_GUARDRAILS)],
            [_b("🎚 Risk Profile →", CB_RISK), _b("⏱ Session →", CB_SESSION)],
            [_b("🔁 Rotation →", CB_ROTATION), _b("📈 Escalation →", CB_ESCALATION)],
            [_b("⚡ Quick Actions →", CB_QUICK), _b("📊 Stats", CB_STATS)],
            [_b("🔙 Dashboard", CB_DASHBOARD)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def settings(data: "SessionData") -> "InlineKeyboardMarkup":
        """Settings menu — shows current values inline."""
        strat = data.strategy
        if len(strat) > 18:
            strat = strat[:17] + "…"
        bet = f"${data.base_bet:.2f}"

        rows = [
            [_b(f"💵  Base Bet: {bet} ✏️",       CB_EDIT_BASE_BET)],
            [_b(f"🎯  Strategy: {strat} ✏️",      CB_STRATEGY)],
            [_b("🛡  Guardrails →",               CB_GUARDRAILS)],
            [_b("⏱  Session Config →",            CB_SESSION)],
            [_b("🎛  Control Center →",           CB_CONTROL)],
            [_b("🔙  Dashboard",                  CB_DASHBOARD)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def guardrails(data: "SessionData") -> "InlineKeyboardMarkup":
        """Guardrails menu — value + toggle per row."""
        def _val(raw, enabled: bool) -> str:
            if not enabled:
                return "OFF"
            try:
                return f"${float(raw):,.0f}"
            except (TypeError, ValueError):
                return str(raw)

        def _tog(enabled: bool) -> str:
            return "✅ ON" if enabled else "❌ OFF"

        rows = [
            [
                _b(f"🎯 S.Profit: {_val(data.sess_profit_target, data.sess_profit_enabled)} ✏️", CB_EDIT_S_PROFIT),
                _b(_tog(data.sess_profit_enabled), CB_TOGGLE_S_STOPS),
            ],
            [
                _b(f"🛑 S.Loss: {_val(data.sess_loss, data.sess_loss_enabled)} ✏️", CB_EDIT_S_LOSS),
                _b(_tog(data.sess_loss_enabled), CB_TOGGLE_S_STOPS),
            ],
            [
                _b(f"📉 Trailing: {_val(data.trailing_stop, data.trailing_enabled)} ✏️", CB_EDIT_TRAILING),
                _b(_tog(data.trailing_enabled), CB_TOGGLE_TRAIL),
            ],
            [
                _b(f"🌍 G.Profit: {_val(data.glob_profit_raw, data.glob_profit_enabled)} ✏️", CB_EDIT_G_PROFIT),
                _b(_tog(data.glob_profit_enabled), CB_TOGGLE_G_STOP),
            ],
            [
                _b(f"🌍 G.Loss: {_val(data.glob_loss_raw, data.glob_loss_enabled)} ✏️", CB_EDIT_G_LOSS),
                _b(_tog(data.glob_loss_enabled), CB_TOGGLE_G_STOP),
            ],
            [
                _b(f"🔥 Win Cap: {data.max_win_streak or 'OFF'} ✏️", CB_EDIT_WIN_STREAK),
                _b(f"❄️ Loss Cap: {data.max_loss_streak or 'OFF'} ✏️", CB_EDIT_LOSS_STREAK),
            ],
            [_b("🔙  Control", CB_CONTROL), _b("⚙️ Settings", CB_SETTINGS)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def session_config(data: "SessionData") -> "InlineKeyboardMarkup":
        sess = (
            f"{data.current_session}/{data.total_sessions}"
            if data.total_sessions > 1 else "1/∞"
        )
        def _t(b: bool) -> str:
            return "✅" if b else "❌"
        rows = [
            [_b(f"⏱  Duration: {data.session_duration} min ✏️", CB_EDIT_DURATION)],
            [_b(f"🔄  Sessions: {sess} ✏️",                      CB_EDIT_N_SESS)],
            [
                _b(f"⏳ Min Gap: {data.min_gap}m ✏️", CB_EDIT_MIN_GAP),
                _b(f"Max Gap: {data.max_gap}m ✏️",    CB_EDIT_MAX_GAP),
            ],
            [
                _b(f"🔁 Ext/Win {_t(data.ext_after_win)}",  CB_TOGGLE_EXT_WIN),
                _b(f"📈 Ext/High {_t(data.ext_at_high)}",   CB_TOGGLE_EXT_HIGH),
            ],
            [
                _b(f"♻️ Max Ext: {data.max_ext_rounds} ✏️",   CB_EDIT_MAX_EXT),
                _b(f"🏳️ Give-Up: ${data.ext_give_up:.0f} ✏️", CB_EDIT_EXT_GIVEUP),
            ],
            [_b("🔙  Control", CB_CONTROL), _b("⚙️ Settings", CB_SETTINGS)],
        ]
        return InlineKeyboardMarkup(rows)

    # ── Mission-control submenus ───────────────────────────────────────────────

    @staticmethod
    def betting(data: "SessionData") -> "InlineKeyboardMarkup":
        rows = [
            [_b(f"💵 Base Bet: ${data.base_bet:.2f} ✏️", CB_EDIT_BASE_BET)],
            [_b(f"💸 Max Bet: ${data.max_bet:.2f} ✏️",   CB_EDIT_MAX_BET)],
            [_b(f"💰 Balance: ${data.balance:,.2f} ✏️",  CB_EDIT_BALANCE)],
            [_b(f"👁 Observe: {data.observation_trigger} rounds ✏️", CB_EDIT_OBS)],
            [_b(f"📐 Progression: {data.progression} →", CB_PROGRESSION)],
            [_b("🔙 Control", CB_CONTROL)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def progression_picker(current: str) -> "InlineKeyboardMarkup":
        rows, row = [], []
        for i, name in enumerate(PROGRESSIONS):
            marker = "✓ " if name == current else ""
            row.append(_b(f"{marker}{name}", f"{CB_PROG_PFX}{i}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        rows.append([_b("🔙 Betting", CB_BETTING)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def risk_profile(current: str) -> "InlineKeyboardMarkup":
        rows = []
        for i, name in enumerate(RISK_PROFILES):
            marker = "✅ " if name == current else "○ "
            rows.append([_b(f"{marker}{name}", f"{CB_RISK_PFX}{i}")])
        rows.append([_b("🔙 Control", CB_CONTROL)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def rotation(data: "SessionData") -> "InlineKeyboardMarkup":
        def _t(b: bool) -> str:
            return "✅ ON" if b else "❌ OFF"
        rows = [
            [_b(f"🔁 Rotation: {_t(data.rotation_enabled)}", CB_TOGGLE_ROTATION)],
            [_b(f"🎛 Mode: {data.rotation_mode or '—'} →", CB_ROTATION_MODE)],
            [_b(f"⚡ Trigger: {data.rotation_trigger or '—'} →", CB_ROTATION_TRIG)],
            [_b("📝 Edit strategy list", CB_EDIT_ROT_LIST)],
            [_b("🔙 Control", CB_CONTROL)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def rotation_mode_picker(current: str) -> "InlineKeyboardMarkup":
        rows = []
        for i, name in enumerate(ROTATION_MODES):
            marker = "✅ " if name == current else "○ "
            rows.append([_b(f"{marker}{name}", f"{CB_ROT_MODE_PFX}{i}")])
        rows.append([_b("🔙 Rotation", CB_ROTATION)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def rotation_trigger_picker(current: str) -> "InlineKeyboardMarkup":
        rows = []
        for i, name in enumerate(ROTATION_TRIGGERS):
            marker = "✅ " if name == current else "○ "
            rows.append([_b(f"{marker}{name}", f"{CB_ROT_TRIG_PFX}{i}")])
        rows.append([_b("🔙 Rotation", CB_ROTATION)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def escalation(data: "SessionData") -> "InlineKeyboardMarkup":
        on = "✅ ON" if data.esc_enabled else "❌ OFF"
        rows = [
            [_b(f"📈 Escalation: {on}", CB_TOGGLE_ESC)],
            [_b(f"✖️ Multiplier: {data.esc_multiplier:.2f}× ✏️", CB_EDIT_ESC_MULT)],
            [_b(f"🔢 Max Steps: {data.esc_max_steps} ✏️", CB_EDIT_ESC_STEPS)],
            [_b("🔙 Control", CB_CONTROL)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def quick_actions(data: "SessionData") -> "InlineKeyboardMarkup":
        hud = "🖥 HUD: ✅" if data.hud_visible else "🖥 HUD: ❌"
        rows = [
            [
                _b("▶️ Start", CB_START) if not data.is_running else _b("⏹ Stop", CB_STOP),
                _b("⏸ Pause" if (data.is_running and not data.is_paused) else "▶️ Resume", CB_TOGGLE_PAUSE),
            ],
            [_b("🚨 EMERGENCY STOP", CB_PANIC)],
            [_b("🔄 Reset Stats", CB_RESET), _b(hud, CB_TOGGLE_HUD)],
            [_b("🔔 Verbose Alerts", CB_TOGGLE_VERBOSE), _b("📈 Graph", CB_GRAPH)],
            [_b("🔙 Control", CB_CONTROL), _b("🏠 Dashboard", CB_DASHBOARD)],
        ]
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def bundles_browser(bundles: List[str], active: str, page: int = 0) -> "InlineKeyboardMarkup":
        """Paginated list of ALL local bundles. callback_data carries the index
        into the page-flattened list the bot snapshotted, so taps are stable."""
        total = len(bundles)
        start = page * _BUNDLES_PER_PAGE
        end = min(start + _BUNDLES_PER_PAGE, total)
        rows = []
        for i in range(start, end):
            name = bundles[i]
            marker = "✅ " if name == active else "◆ "
            label = name if len(name) <= 28 else name[:27] + "…"
            rows.append([_b(f"{marker}{label}", f"{CB_BUNDLE_PFX}{i}")])
        # Pager
        nav = []
        if page > 0:
            nav.append(_b("◀ Prev", f"{CB_BUNDLE_PAGE_PFX}{page - 1}"))
        if end < total:
            nav.append(_b("Next ▶", f"{CB_BUNDLE_PAGE_PFX}{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([_b("🔙 Control", CB_CONTROL)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def strategy_list(strategies: List[str], current: str) -> "InlineKeyboardMarkup":
        """Two-column strategy picker. Uses index in callback_data to stay under 64 bytes."""
        rows: list = []
        row: list = []
        for i, name in enumerate(strategies):
            marker = "✓ " if name == current else ""
            label = f"{marker}{name[:17]}"
            row.append(_b(label, f"{CB_SET_STRAT_PFX}{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([_b("🔙  Settings", CB_SETTINGS)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def stats_view() -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[
            _b("🔄 Refresh",    CB_STATS),
            _b("🔙 Dashboard",  CB_DASHBOARD),
        ]])

    @staticmethod
    def confirm(yes_data: str = CB_CONFIRM_YES, no_data: str = CB_CONFIRM_NO) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[
            _b("✅ Yes", yes_data),
            _b("❌ No",  no_data),
        ]])

    @staticmethod
    def cancel(back_data: str = CB_DASHBOARD) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[_b("❌ Cancel", back_data)]])

    @staticmethod
    def ack(back_data: str = CB_DASHBOARD) -> "InlineKeyboardMarkup":
        return InlineKeyboardMarkup([[_b("✅ OK", back_data)]])
