"""
Text formatters — pure functions that turn SessionData into Telegram message strings.
No Telegram API calls here; just string assembly.

All messages use Markdown (V1) parse_mode for maximum compatibility.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bridge import SessionData


# ── Low-level helpers ─────────────────────────────────────────────────────────

def fmt_money(val: float, show_sign: bool = True) -> str:
    sign = "+" if (show_sign and val > 0) else ""
    return f"{sign}${val:,.2f}"


def fmt_pct(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}{val:.1f}%"


def fmt_guardrail_val(raw: str, balance: float = 0.0) -> str:
    """Display a guardrail value that may be numeric or a percentage string."""
    raw = str(raw).strip()
    if raw.endswith("%"):
        try:
            pct = float(raw[:-1])
            dollar = (pct / 100.0) * balance if balance else 0.0
            return f"{raw} (${dollar:,.2f})"
        except ValueError:
            return raw
    try:
        return fmt_money(float(raw), show_sign=False)
    except ValueError:
        return raw or "─"


def fmt_results(results: list, max_n: int = 8) -> str:
    """Render last N round results as green/red circles."""
    if not results:
        return "─"
    return "".join("🟢" if r else "🔴" for r in list(results)[-max_n:])


def fmt_streak(streak: int) -> str:
    if streak >= 5:
        return f"🔥 +{streak} win streak!"
    elif streak > 0:
        return f"✅ +{streak} wins"
    elif streak <= -5:
        return f"❄️ {streak} loss streak"
    elif streak < 0:
        return f"📉 {streak} losses"
    return "─"


def fmt_status(is_running: bool, is_paused: bool) -> str:
    if is_paused:
        return "⏸ PAUSED"
    if is_running:
        return "🟢 LIVE"
    return "🔴 STOPPED"


_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def fmt_last_number(num, color: str = "") -> str:
    """Render the last roulette number with its color square."""
    if num is None:
        return "─"
    c = (color or "").strip().upper()
    if not c:
        if num == 0:
            c = "GREEN"
        elif num in _RED_NUMBERS:
            c = "RED"
        else:
            c = "BLACK"
    sq = {"RED": "🟥", "BLACK": "⬛", "GREEN": "🟩"}.get(c, "⬜")
    return f"{sq} `{num}`  _{c.title()}_"


def fmt_guard_line(icon: str, label: str, val_raw, enabled: bool, balance: float, last: bool = False) -> str:
    prefix = "└" if last else "├"
    if not enabled:
        return f"{prefix} {icon} {label}: `OFF`"
    val_str = fmt_guardrail_val(str(val_raw), balance)
    return f"{prefix} {icon} {label}: `{val_str}` ✅"


def _now() -> str:
    return time.strftime("%H:%M:%S")


# ── Main dashboard ─────────────────────────────────────────────────────────────

def render_dashboard(data: "SessionData") -> str:
    status = fmt_status(data.is_running, data.is_paused)
    recent = fmt_results(data.recent_results, 8)
    streak = fmt_streak(data.streak)

    sess_label = (
        f"  •  Session {data.current_session}/{data.total_sessions}"
        if data.total_sessions > 1 else ""
    )

    mult = ""
    if data.base_bet > 0 and abs(data.current_bet - data.base_bet) > 0.001:
        mult = f"  `({data.bet_multiplier:.0f}× base)`"

    # Strategy block — show rotation context when rotation is in use
    strat_lines = [
        "🎯 *STRATEGY*",
        f"├ Name:  `{getattr(data, 'rotation_active', '') or data.strategy}`",
    ]
    if getattr(data, "rotation_count", 0) >= 2 and getattr(data, "rotation_next", ""):
        strat_lines.append(f"├ Next:  `{data.rotation_next}` _(of {data.rotation_count})_")
    strat_lines += [
        f"├ Prog:  `{data.progression}`",
        f"├ Base:  `{fmt_money(data.base_bet, False)}`",
        f"└ Bet:   `{fmt_money(data.current_bet, False)}`{mult}",
    ]

    # Last spin (only when we actually have one)
    last_spin_block = []
    if getattr(data, "last_number", None) is not None:
        last_spin_block = [
            "🎲 *LAST SPIN*",
            f"└ {fmt_last_number(data.last_number, getattr(data, 'last_color', ''))}",
            "",
        ]

    # Banner area — when stopped, surface why; when paused, surface by-whom.
    banner = []
    if not data.is_running and getattr(data, "last_stop_reason", ""):
        banner = ["🏁 *Last stop:* `" + str(data.last_stop_reason) + "`", ""]
    elif data.is_paused and getattr(data, "paused_by", ""):
        banner = [f"⏸ *Paused by:* `{data.paused_by}`", ""]

    guards = [
        fmt_guard_line("🎯", "S.TP  ", data.sess_profit_target, data.sess_profit_enabled, data.balance),
        fmt_guard_line("🛑", "S.SL  ", data.sess_loss,          data.sess_loss_enabled,  data.balance),
        fmt_guard_line("📉", "Trail ", data.trailing_stop,       data.trailing_enabled,   data.balance),
        fmt_guard_line("🌍", "G.TP  ", data.glob_profit_raw,     data.glob_profit_enabled, data.balance),
        fmt_guard_line("🌍", "G.SL  ", data.glob_loss_raw,       data.glob_loss_enabled,  data.balance, last=True),
    ]

    lines = [
        "🎰 *SPINEDGE ENGINE*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"*{status}*{sess_label}",
        "",
        *banner,
        "💰 *FINANCIALS*",
        f"├ S.PnL:  `{fmt_money(data.sess_pnl)}`  `({fmt_pct(data.sess_pnl_pct)})`",
        f"├ G.PnL:  `{fmt_money(data.glob_pnl)}`  `({fmt_pct(data.glob_pnl_pct)})`",
        f"├ Peak:   `{fmt_money(data.peak_pnl)}`",
        f"└ Bal:    `${data.balance:,.2f}`",
        "",
        "📊 *PERFORMANCE*",
        f"├ Rounds: `{data.total_rounds}`   WR: `{data.win_rate:.1f}%`",
        f"├ W/L:    `{data.wins}W`  `{data.losses}L`",
        f"├ Streak: `{streak}`",
        f"└ Recent: {recent}",
        "",
        *last_spin_block,
        *strat_lines,
        "",
        "⏱ *TIMING*",
        f"├ Left:  `{data.time_remaining}`",
        f"└ Dur:   `{data.session_duration} min`",
        "",
        "🛡 *GUARDRAILS*",
        *guards,
        "",
        f"🕐 `{_now()}`",
    ]

    if data.next_session_timer:
        lines.insert(-1, f"⏳ *Next session:* `{data.next_session_timer}`")

    return "\n".join(lines)


# ── Stats view ─────────────────────────────────────────────────────────────────

def render_stats(data: "SessionData") -> str:
    sess_label = (
        f"#{data.current_session} of {data.total_sessions}"
        if data.total_sessions > 1 else "#1"
    )
    streak_str = fmt_streak(data.streak)
    recent = fmt_results(data.recent_results, 10)

    # Compute best/worst streaks from recent history
    best_win = worst_loss = cur_w = cur_l = 0
    for r in data.recent_results:
        if r:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        best_win   = max(best_win, cur_w)
        worst_loss = max(worst_loss, cur_l)

    lines = [
        "📊 *DETAILED STATS*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Session {sess_label}",
        "",
        "💰 *P&L*",
        f"├ Session:  `{fmt_money(data.sess_pnl)}`  `({fmt_pct(data.sess_pnl_pct)})`",
        f"├ Global:   `{fmt_money(data.glob_pnl)}`  `({fmt_pct(data.glob_pnl_pct)})`",
        f"├ Peak:     `{fmt_money(data.peak_pnl)}`",
        f"└ Balance:  `${data.balance:,.2f}`",
        "",
        f"🎲 *ROUNDS* — `{data.total_rounds}` total",
        f"├ ✅ Wins:   `{data.wins}`  `({data.win_rate:.1f}%)`",
        f"├ ❌ Losses: `{data.losses}`  `({100 - data.win_rate:.1f}%)`",
        f"├ Streak:   `{streak_str}`",
        f"├ Best W:   `{best_win} in a row`",
        f"└ Worst L:  `{worst_loss} in a row`",
        "",
        f"📋 *LAST {min(len(data.recent_results), 10)} ROUNDS*",
        recent,
        "",
        "💵 *BETTING*",
        f"├ Strategy:    `{data.strategy}`",
        f"├ Progression: `{data.progression}`",
        f"├ Base:    `{fmt_money(data.base_bet, False)}`",
        f"└ Current: `{fmt_money(data.current_bet, False)}`",
        "",
        f"🕐 `{_now()}`",
    ]
    return "\n".join(lines)


# ── Settings header ────────────────────────────────────────────────────────────

def render_settings(data: "SessionData") -> str:
    lines = [
        "⚙️ *SETTINGS*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💵 Base Bet:  `{fmt_money(data.base_bet, False)}`",
        f"🎯 Strategy:  `{data.strategy}`",
        f"📐 Prog.:     `{data.progression}`",
        "",
        "_Tap a button below to edit._",
    ]
    return "\n".join(lines)


# ── Guardrails header ──────────────────────────────────────────────────────────

def render_guardrails(data: "SessionData") -> str:
    def line(icon, label, val_raw, enabled):
        state = "✅ ON" if enabled else "❌ OFF"
        if enabled:
            val = fmt_guardrail_val(str(val_raw), data.balance)
            return f"  {icon} {label}: `{val}`  {state}"
        return f"  {icon} {label}: {state}"

    lines = [
        "🛡 *GUARDRAILS*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        line("🎯", "Sess. Profit Target", data.sess_profit_target, data.sess_profit_enabled),
        line("🛑", "Sess. Stop Loss    ", data.sess_loss,          data.sess_loss_enabled),
        line("📉", "Trailing Stop      ", data.trailing_stop,      data.trailing_enabled),
        line("🌍", "Global Profit TP   ", data.glob_profit_raw,    data.glob_profit_enabled),
        line("🌍", "Global Stop Loss   ", data.glob_loss_raw,      data.glob_loss_enabled),
        "",
        "_Toggle ON/OFF or ✏️ to change values._",
    ]
    return "\n".join(lines)


# ── Session config header ──────────────────────────────────────────────────────

def render_session_config(data: "SessionData") -> str:
    sess_label = (
        f"{data.current_session}/{data.total_sessions}"
        if data.total_sessions > 1 else "1/∞"
    )
    ext = "✅ ON" if data.ext_after_win else "❌ OFF"

    lines = [
        "⏱ *SESSION CONFIG*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  Duration:       `{data.session_duration} min`",
        f"  Sessions:       `{sess_label}`",
        f"  Extend on Win:  {ext}",
        "",
        "_Tap a button below to edit._",
    ]
    return "\n".join(lines)


# ── Mission-control menus ──────────────────────────────────────────────────────

def render_control_center(data: "SessionData") -> str:
    status = fmt_status(data.is_running, data.is_paused)
    src = (data.strategy_source or "").lower()
    src_label = "📦 Bundle" if src == "bundle" else ("🎯 Single strategy" if src == "manual" else "—")
    active = data.active_bundle if src == "bundle" and data.active_bundle else data.strategy
    lines = [
        "🎛 *CONTROL CENTER*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"*{status}*   PnL `{fmt_money(data.sess_pnl)}`   Bal `${data.balance:,.2f}`",
        "",
        f"Source:   {src_label}",
        f"Active:   `{active}`",
        f"Base/Bet: `{fmt_money(data.base_bet, False)}` → `{fmt_money(data.current_bet, False)}`",
        "",
        "_Everything is one tap from here._",
    ]
    return "\n".join(lines)


def render_betting(data: "SessionData") -> str:
    lines = [
        "💵 *BETTING*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  Base Bet:    `{fmt_money(data.base_bet, False)}`",
        f"  Max Bet:     `{fmt_money(data.max_bet, False)}`",
        f"  Balance:     `${data.balance:,.2f}`",
        f"  Observation: `{data.observation_trigger} rounds`",
        f"  Progression: `{data.progression}`",
        "",
        "_Tap to edit._",
    ]
    return "\n".join(lines)


def render_risk(data: "SessionData") -> str:
    lines = [
        "🎚 *RISK PROFILE*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Current: `{data.risk_profile or 'Use Bundle Values'}`",
        "",
        "Profiles set base-bet % and stop-loss % from balance:",
        "  • Conservative — 0.5% bet / 10% stop",
        "  • Balanced — 1% bet / 20% stop",
        "  • Aggressive — 5% bet / 40% stop",
        "  • Auto — smart default",
        "",
        "_Tap a profile to apply it now._",
    ]
    return "\n".join(lines)


def render_rotation(data: "SessionData") -> str:
    on = "✅ ON" if data.rotation_enabled else "❌ OFF"
    lines = [
        "🔁 *STRATEGY ROTATION*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  Rotation: {on}",
        f"  Mode:     `{data.rotation_mode or '—'}`",
        f"  Trigger:  `{data.rotation_trigger or '—'}`",
        f"  In list:  `{data.rotation_count}` strategies",
        "",
        "_Toggle, pick mode/trigger, or edit the list._",
    ]
    return "\n".join(lines)


def render_escalation(data: "SessionData") -> str:
    on = "✅ ON" if data.esc_enabled else "❌ OFF"
    lines = [
        "📈 *ESCALATION ON LOSS*",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  Escalation: {on}",
        f"  Multiplier: `{data.esc_multiplier:.2f}×`",
        f"  Max Steps:  `{data.esc_max_steps}`",
        "",
        "_Scales base bet + stop-loss after a session stop-loss hit._",
    ]
    return "\n".join(lines)


def render_quick(data: "SessionData") -> str:
    status = fmt_status(data.is_running, data.is_paused)
    hud = "visible" if data.hud_visible else "hidden"
    lines = [
        "⚡ *QUICK ACTIONS*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"*{status}*   HUD: `{hud}`",
        "",
        "🚨 *Emergency Stop* halts the bot immediately.",
        "🔄 *Reset Stats* clears session counters & refreshes OCR.",
        "🔔 *Verbose Alerts* toggles per-round win/loss pings.",
    ]
    return "\n".join(lines)


def render_bundles(data: "SessionData", total: int, page: int, per_page: int = 8) -> str:
    start = page * per_page + 1
    end = min(start + per_page - 1, total)
    active = data.active_bundle or "—"
    lines = [
        "📦 *BROWSE BUNDLES*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Active: `{active}`",
        f"Showing `{start}`–`{end}` of `{total}`" if total else "_No bundles found._",
        "",
        "_Tap a bundle to load it (applies immediately)._",
    ]
    return "\n".join(lines)


def render_progression(current: str) -> str:
    return (
        "📐 *PROGRESSION*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Current: `{current}`\n\n"
        "_Tap to switch the bet-sizing progression._"
    )


# ── Help text ──────────────────────────────────────────────────────────────────

HELP_TEXT = """\
🎰 *SpinEdge Remote — Help*
━━━━━━━━━━━━━━━━━━━━━━

*Commands*
/start  — Open dashboard
/menu   — Open Control Center (full remote)
/status — Refresh dashboard
/go     — Start a session
/pause  — Pause / resume session
/resume — Resume a paused session
/stop   — Stop session
/panic  — 🚨 Emergency stop
/swap   — Quick-swap a favorited strategy/bundle
/set    — Quick set, e.g. `/set base_bet 2.5`
/help   — This help screen

*🎛 Control Center* (`/menu`)
One tap to everything:
• Session start/stop/pause
• Strategy & bundle (browse ALL, not just favorites)
• Betting — base/max bet, balance, observation, progression
• Guardrails — profit/loss/trailing/global + streak caps
• Risk Profile — Conservative/Balanced/Aggressive/Auto
• Session — duration, sessions, gaps, extensions
• Rotation — enable, mode, trigger, list
• Escalation — enable, multiplier, steps
• Quick Actions — emergency stop, reset, HUD, alerts

*Editing values*
Toggles switch ON/OFF instantly.
✏️ buttons prompt for a value — reply with a number
(global stops also accept `10%`).

`/set <field> <value>` fields: base_bet, max_bet, max_loss,
profit_target, trailing, duration, balance, observation.

_Type /menu for the full remote._
"""
