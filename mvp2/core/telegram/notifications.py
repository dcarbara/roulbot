"""
NotificationManager — push notifications with per-type rate limiting.

Usage (from bot):
    self.notif.send(NotifType.WIN, amount=12.50)
    self.notif.send(NotifType.SESSION_END, pnl=47.50, wins=14, losses=6)
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import RouletteTelegramBot


# ── Notification types ────────────────────────────────────────────────────────

class NotifType(Enum):
    WIN          = "win"
    LOSS         = "loss"
    BIG_WIN      = "big_win"
    JACKPOT      = "jackpot"
    WIN_STREAK   = "win_streak"
    LOSS_STREAK  = "loss_streak"
    SESSION_START = "session_start"
    SESSION_END  = "session_end"
    STOP         = "stop"
    MILESTONE    = "milestone"
    INFO         = "info"
    WARNING      = "warning"
    ERROR        = "error"


# Minimum seconds between successive sends of the same type (0 = unlimited).
# Streak cooldowns dropped from 45s → 10s so users following live see rapid
# momentum swings (a long streak no longer fires once and then goes quiet).
_COOLDOWNS: dict[NotifType, float] = {
    NotifType.WIN:           0,
    NotifType.LOSS:          0,
    NotifType.BIG_WIN:       0,
    NotifType.JACKPOT:       0,
    NotifType.WIN_STREAK:    10,
    NotifType.LOSS_STREAK:   10,
    NotifType.SESSION_START: 0,
    NotifType.SESSION_END:   0,
    NotifType.STOP:          0,
    NotifType.MILESTONE:     120,
    NotifType.INFO:          5,
    NotifType.WARNING:       10,
    NotifType.ERROR:         10,
}


# ── Message templates ─────────────────────────────────────────────────────────
# Use {key} placeholders — rendered via .format(**kwargs)

_TEMPLATES: dict[NotifType, str] = {
    NotifType.WIN: (
        "✅ *Win!*  `{spin}`\n"
        "Round `#{round}` · Bet `${bet:.2f}` · `+${amount:.2f}`\n"
        "Sess `{sess_pnl_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.LOSS: (
        "❌ *Loss*  `{spin}`\n"
        "Round `#{round}` · Bet `${bet:.2f}` · `-${amount:.2f}`\n"
        "Sess `{sess_pnl_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.BIG_WIN: (
        "🔥 *Big Win!*  `{spin}`\n"
        "Round `#{round}` · Bet `${bet:.2f}` · `+${amount:.2f}`\n"
        "Sess `{sess_pnl_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.JACKPOT: (
        "💰 *JACKPOT!* 💰  `{spin}`\n"
        "Round `#{round}` · Bet `${bet:.2f}` · `+${amount:.2f}`\n"
        "Sess `{sess_pnl_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.WIN_STREAK: (
        "🔥 *Win Streak!*  `{streak}` in a row\n"
        "Last `+${amount:.2f}` · Sess `{sess_pnl_str}`"
    ),
    NotifType.LOSS_STREAK: (
        "❄️ *Loss Streak*  `{streak}` in a row\n"
        "Last `-${amount:.2f}` · Sess `{sess_pnl_str}`\n"
        "_Stay disciplined._"
    ),
    NotifType.SESSION_START: (
        "▶️ *Session Started*  `{label}`\n"
        "{strategy_line}"
    ),
    NotifType.SESSION_END: (
        "🏁 *Session Ended*  `{label}`\n"
        "PnL `{pnl_str}` · `{wins}W / {losses}L` (`{wr:.1f}%` WR)\n"
        "Peak `{peak_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.STOP: (
        "🚨 *Stop Triggered*\n"
        "Reason: `{reason}`\n"
        "PnL `{pnl_str}` · Bal `${balance:,.2f}`"
    ),
    NotifType.MILESTONE: (
        "🏆 *Milestone!*\n"
        "`{message}`"
    ),
    NotifType.INFO:    "ℹ️ {message}",
    NotifType.WARNING: "⚠️ *Warning*\n`{message}`",
    NotifType.ERROR:   "🚨 *Error*\n`{message}`",
}


def _fmt_money(val: float) -> str:
    sign = "+" if val > 0 else ""
    return f"{sign}${val:,.2f}"


# ── Manager ───────────────────────────────────────────────────────────────────

class NotificationManager:
    """Thread-safe push notification dispatcher with per-type rate limiting."""

    def __init__(self, bot: "RouletteTelegramBot") -> None:
        self._bot = bot
        self._last: dict[NotifType, float] = {}

    def _can_send(self, ntype: NotifType) -> bool:
        cooldown = _COOLDOWNS.get(ntype, 0.0)
        if cooldown == 0:
            return True
        return (time.monotonic() - self._last.get(ntype, 0.0)) >= cooldown

    def send(self, ntype: NotifType, **kwargs: Any) -> None:
        """Send a notification. Safe to call from any thread."""
        if not self._can_send(ntype):
            return
        self._last[ntype] = time.monotonic()

        # Pull a fresh snapshot so per-round notifications can carry round#,
        # session PnL, balance, and the last-spin label without requiring every
        # caller to pass them. Snapshot is the cached one; safe across threads.
        try:
            snap = self._bot.bridge.get_cached_data() if self._bot and self._bot.bridge else None
        except Exception:
            snap = None

        try:
            # Default scalars
            kwargs.setdefault("amount", 0.0)
            kwargs.setdefault("message", "")
            kwargs.setdefault("label", "")
            kwargs.setdefault("reason", "")
            kwargs.setdefault("streak", 0)
            kwargs.setdefault("wins", 0)
            kwargs.setdefault("losses", 0)
            kwargs.setdefault("wr", 0.0)

            # Snapshot-derived defaults
            if snap is not None:
                kwargs.setdefault("round",      snap.total_rounds)
                kwargs.setdefault("bet",        snap.current_bet)
                kwargs.setdefault("balance",    snap.balance)
                kwargs.setdefault("sess_pnl",   snap.sess_pnl)
                kwargs.setdefault("peak",       snap.peak_pnl)
                last_n = getattr(snap, "last_number", None)
                if last_n is not None:
                    last_c = getattr(snap, "last_color", "") or ""
                    kwargs.setdefault("spin", f"{last_n} {last_c}".strip())
                else:
                    kwargs.setdefault("spin", "—")
                strat = getattr(snap, "rotation_active", "") or snap.strategy
                kwargs.setdefault("strategy_line",
                                  f"Strategy `{strat}`" if strat and strat != "─" else "")
            else:
                kwargs.setdefault("round", 0)
                kwargs.setdefault("bet", 0.0)
                kwargs.setdefault("balance", 0.0)
                kwargs.setdefault("sess_pnl", 0.0)
                kwargs.setdefault("peak", 0.0)
                kwargs.setdefault("spin", "—")
                kwargs.setdefault("strategy_line", "")

            # Pre-formatted strings expected by templates
            if "pnl" in kwargs and "pnl_str" not in kwargs:
                kwargs["pnl_str"] = _fmt_money(kwargs["pnl"])
            kwargs.setdefault("pnl_str",      _fmt_money(kwargs.get("sess_pnl", 0.0)))
            kwargs.setdefault("sess_pnl_str", _fmt_money(kwargs.get("sess_pnl", 0.0)))
            kwargs.setdefault("peak_str",     _fmt_money(kwargs.get("peak", 0.0)))

            template = _TEMPLATES.get(ntype, "{message}")
            text = template.format(**kwargs)
        except (KeyError, ValueError) as exc:
            print(f"[Telegram] Notification template error ({ntype.value}): {exc}")
            text = str(kwargs.get("message", "") or kwargs.get("reason", ""))

        self._dispatch(text)

    # ── Legacy convenience wrappers ───────────────────────────────────────────

    def send_raw(self, text: str) -> None:
        """Send arbitrary pre-formatted Markdown text."""
        self._dispatch(text)

    def send_smart(self, message: str, notif_type: str = "info", value: float = 0.0) -> None:
        """
        Legacy shim for old send_smart_notification(message, type, value) calls.
        Maps the old string type → NotifType and delegates.
        """
        if value >= 500:
            self.send(NotifType.JACKPOT, amount=value)
            return
        if value >= 50 and notif_type == "win":
            self.send(NotifType.BIG_WIN, amount=value)
            return
        mapping = {
            "win":     NotifType.WIN,
            "loss":    NotifType.LOSS,
            "info":    NotifType.INFO,
            "warning": NotifType.WARNING,
        }
        ntype = mapping.get(notif_type, NotifType.INFO)
        self.send(ntype, message=message, amount=value)

    # ── Internal dispatch ─────────────────────────────────────────────────────

    def _dispatch(self, text: str) -> None:
        bot = self._bot
        if not bot.loop or bot.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._send_async(text), bot.loop)

    async def _send_async(self, text: str) -> None:
        bot = self._bot
        if not bot.application or not bot.allowed_chat_id:
            return
        # Honor the bot-wide flood-control hold so notifications don't
        # extend the punishment window the dashboard already triggered.
        if time.monotonic() < getattr(bot, "_send_floor_until", 0.0):
            return
        try:
            await bot.application.bot.send_message(
                chat_id=bot.allowed_chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as exc:
            err = str(exc).lower()
            if "message is not modified" in err or "query is too old" in err:
                return
            # Flood control — engage the bot-wide circuit breaker
            if "flood control" in err or "too many requests" in err:
                import re as _re
                m = _re.search(r"retry in (\d+)", err)
                retry_in = float(m.group(1)) if m else 30.0
                engager = getattr(bot, "_engage_flood_breaker", None)
                if callable(engager):
                    engager(retry_in, source="notification")
                else:
                    bot._send_floor_until = time.monotonic() + retry_in
                return
            print(f"[Telegram] Notification send error: {exc}")
