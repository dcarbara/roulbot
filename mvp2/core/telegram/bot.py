"""
RouletteTelegramBot — main class.

Architecture
============
- Runs in a single daemon thread with its own asyncio event loop.
- GuiBridge provides typed, thread-safe access to the GUI app.
- BotState holds all mutable state (menu, input mode, round history …).
- NotificationManager handles push messages with per-type rate limiting.
- formatters.py / keyboards.py are pure; no Telegram API calls there.

Backward-compatible public surface (used by main_gui.py)
=========================================================
  .start()
  .stop()
  .wait_until_ready(timeout)
  .is_running                       bool
  .loop                             asyncio event loop
  .token                            str
  .update_live_dashboard(force)
  .send_notification(msg)
  .send_smart_notification(msg, type, value)
  .send_confirmation_request(prompt)
  .request_confirmation(prompt, timeout)
  .request_acknowledgment(message, timeout)
  .request_input(prompt, timeout)
  .expecting_input / .input_value / .input_event
  .expecting_confirmation / .confirmation_value / .confirmation_event
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional, Any

try:
    from telegram import Update, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("[Telegram] python-telegram-bot not installed. Remote control disabled.")

from .state import (
    BotState,
    MENU_DASHBOARD, MENU_SETTINGS, MENU_GUARDRAILS,
    MENU_SESSION, MENU_STRATEGY, MENU_STATS,
    MENU_CONTROL, MENU_BETTING, MENU_RISK, MENU_ROTATION,
    MENU_ESCALATION, MENU_BUNDLES, MENU_PROGRESSION, MENU_QUICK,
    INPUT_NONE, INPUT_CONFIG_MAP,
    INPUT_GLOB_PROFIT, INPUT_GLOB_LOSS,
    INPUT_MAX_BET, INPUT_OBSERVATION, INPUT_WIN_STREAK, INPUT_LOSS_STREAK,
    INPUT_ESC_MULT, INPUT_ESC_STEPS, INPUT_MAX_EXT, INPUT_EXT_GIVEUP,
    INPUT_BALANCE, INPUT_MIN_GAP, INPUT_MAX_GAP, INPUT_ROTATION_LIST,
)
from .bridge import GuiBridge
from .notifications import NotificationManager, NotifType
from .formatters import (
    render_dashboard, render_stats,
    render_settings, render_guardrails, render_session_config,
    render_control_center, render_betting, render_risk, render_rotation,
    render_escalation, render_quick, render_bundles, render_progression,
    HELP_TEXT,
)
from .keyboards import (
    Keyboards,
    CB_DASHBOARD, CB_SETTINGS, CB_GUARDRAILS, CB_SESSION,
    CB_STRATEGY, CB_STATS, CB_GRAPH,
    CB_START, CB_STOP, CB_TOGGLE_PAUSE,
    CB_EDIT_BASE_BET, CB_EDIT_S_PROFIT, CB_EDIT_S_LOSS,
    CB_EDIT_TRAILING, CB_EDIT_G_PROFIT, CB_EDIT_G_LOSS,
    CB_EDIT_DURATION, CB_EDIT_N_SESS,
    CB_TOGGLE_S_STOPS, CB_TOGGLE_TRAIL, CB_TOGGLE_G_STOP, CB_TOGGLE_EXT_WIN,
    CB_CONFIRM_YES, CB_CONFIRM_NO,
    CB_SET_STRAT_PFX,
    CB_SWAP_STRAT_PFX, CB_SWAP_BUNDLE_PFX,
    # Mission control
    CB_CONTROL, CB_BETTING, CB_RISK, CB_ROTATION, CB_ROTATION_MODE,
    CB_ROTATION_TRIG, CB_ESCALATION, CB_BUNDLES, CB_PROGRESSION, CB_QUICK,
    CB_EDIT_MAX_BET, CB_EDIT_OBS, CB_EDIT_WIN_STREAK, CB_EDIT_LOSS_STREAK,
    CB_EDIT_ESC_MULT, CB_EDIT_ESC_STEPS, CB_EDIT_MAX_EXT, CB_EDIT_EXT_GIVEUP,
    CB_EDIT_BALANCE, CB_EDIT_MIN_GAP, CB_EDIT_MAX_GAP, CB_EDIT_ROT_LIST,
    CB_TOGGLE_EXT_HIGH, CB_TOGGLE_ROTATION, CB_TOGGLE_ESC,
    CB_TOGGLE_HUD, CB_TOGGLE_VERBOSE,
    CB_RISK_PFX, CB_PROG_PFX, CB_ROT_MODE_PFX, CB_ROT_TRIG_PFX,
    CB_BUNDLE_PFX, CB_BUNDLE_PAGE_PFX,
    CB_RESET, CB_PANIC,
    RISK_PROFILES, PROGRESSIONS, ROTATION_MODES, ROTATION_TRIGGERS,
)

# Map an "edit" button → (input_mode, back_menu_cb, prompt_text). New numeric
# fields all resolve through INPUT_CONFIG_MAP in _handle_message; this table
# just drives the prompt + where to return afterwards.
_EDIT_PROMPTS: dict = {
    CB_EDIT_MAX_BET:     (INPUT_MAX_BET,     CB_BETTING,    "💸 *Set Max Bet*\n\nReply with a dollar amount.\n_e.g. `50`_"),
    CB_EDIT_BALANCE:     (INPUT_BALANCE,     CB_BETTING,    "💰 *Set Balance*\n\nReply with your current balance.\n_e.g. `500`_"),
    CB_EDIT_OBS:         (INPUT_OBSERVATION, CB_BETTING,    "👁 *Set Observation*\n\nRounds to watch before betting (`0` = bet immediately).\n_e.g. `3`_"),
    CB_EDIT_WIN_STREAK:  (INPUT_WIN_STREAK,  CB_GUARDRAILS, "🔥 *Set Win-Streak Cap*\n\nStop after N wins in a row (`0` = off).\n_e.g. `5`_"),
    CB_EDIT_LOSS_STREAK: (INPUT_LOSS_STREAK, CB_GUARDRAILS, "❄️ *Set Loss-Streak Cap*\n\nStop after N losses in a row (`0` = off).\n_e.g. `5`_"),
    CB_EDIT_ESC_MULT:    (INPUT_ESC_MULT,    CB_ESCALATION, "✖️ *Set Escalation Multiplier*\n\n_e.g. `2.0`_"),
    CB_EDIT_ESC_STEPS:   (INPUT_ESC_STEPS,   CB_ESCALATION, "🔢 *Set Escalation Max Steps*\n\n_e.g. `4`_"),
    CB_EDIT_MAX_EXT:     (INPUT_MAX_EXT,     CB_SESSION,    "♻️ *Set Max Extension Rounds*\n\n_e.g. `20`_"),
    CB_EDIT_EXT_GIVEUP:  (INPUT_EXT_GIVEUP,  CB_SESSION,    "🏳️ *Set Extension Give-Up*\n\nDrop from peak that aborts an extension.\n_e.g. `50`_"),
    CB_EDIT_MIN_GAP:     (INPUT_MIN_GAP,     CB_SESSION,    "⏳ *Set Min Gap*\n\nMinutes between sessions.\n_e.g. `0`_"),
    CB_EDIT_MAX_GAP:     (INPUT_MAX_GAP,     CB_SESSION,    "⏳ *Set Max Gap*\n\nMinutes between sessions.\n_e.g. `1`_"),
    CB_EDIT_ROT_LIST:    (INPUT_ROTATION_LIST, CB_ROTATION, "📝 *Edit Rotation List*\n\nReply with a comma-separated strategy list.\n_e.g. `martingale:flat,fib1:fibonacci`_"),
}

# /set <field> <value> → (config_key, var_name, cast). Power-user shortcut.
_SET_FIELDS: dict = {
    "base_bet":      ("base_bet",                "base_bet_var",                float),
    "max_bet":       ("max_bet",                 "max_bet_var",                 float),
    "max_loss":      ("max_loss",                "max_loss_var",                float),
    "stop_loss":     ("max_loss",                "max_loss_var",                float),
    "profit_target": ("profit_target",           "profit_target_var",           float),
    "trailing":      ("trailing_stop_amount",    "trailing_stop_amount_var",    float),
    "duration":      ("session_duration_minutes","session_duration_var",        int),
    "sessions":      ("num_sessions",            "num_sessions_var",            int),
    "balance":       ("current_balance",         "balance_var",                 float),
    "observation":   ("observation_trigger",     "observation_trigger_var",     int),
}

_MD = "Markdown"
# Dashboard edit interval. Bumped 4 → 8 because at 4 s we were issuing
# ~15 edits/min and stacking notifications on top, which tripped Telegram
# flood control on long sessions. 8 s → ~7 edits/min keeps headroom for
# bursty notifications. Force-refresh on terminal events bypasses this.
_DASHBOARD_RATE = 8.0
# Graph refresh interval. Heavier than the dashboard (matplotlib + photo
# upload), so a slower cadence keeps Telegram bandwidth and rate caps
# happy. 30 s = 2 edits/min — visible "live-ish" without being chatty.
_GRAPH_RATE = 30.0


def _parse_flood_retry(exc: Exception) -> float:
    """Pull "Retry in N seconds" out of a Telegram flood-control error message.

    Returns the cooldown in seconds, or 0 if the message isn't a flood error.
    """
    text = str(exc).lower()
    if "flood control" not in text and "too many requests" not in text:
        return 0.0
    # Messages look like:
    #   "Flood control exceeded. Retry in 15810 seconds"
    #   "Too Many Requests: retry after 5"
    import re
    m = re.search(r"retry (?:in|after)\s+(\d+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 30.0  # unknown duration → conservative pause


class RouletteTelegramBot:
    """Telegram remote-control bot for SpinEdge Engine."""

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, token: str, allowed_chat_id: str, gui_app: Any) -> None:
        self.token          = token
        self.allowed_chat_id = str(allowed_chat_id)
        self.gui_app        = gui_app            # kept for compat; use bridge instead

        self.bridge = GuiBridge(gui_app)
        self.notif  = NotificationManager(self)
        self.state  = BotState()

        self.application: Optional[Application] = None
        self.loop:        Optional[asyncio.AbstractEventLoop] = None
        self.is_running:  bool = False
        self.thread:      Optional[threading.Thread] = None
        self.ready_event  = threading.Event()

        self._dashboard_lock: Optional[asyncio.Lock] = None
        self._last_dashboard: float = 0.0
        self._graph_lock: Optional[asyncio.Lock] = None
        self._last_graph: float = 0.0

        # Flood-control circuit breaker. When Telegram returns
        # "Retry in N seconds", every async send refuses to hit the API
        # until time.monotonic() crosses this deadline. Shared between
        # notifications and dashboard edits.
        self._send_floor_until: float = 0.0
        self._floor_logged: bool = False

        # When True, per-round WIN/LOSS notifications are sent. Default off
        # so the dashboard alone covers the round-by-round case — Telegram's
        # sustained per-chat rate cannot keep up with one notification per
        # spin AND the dashboard edits on a fast session.
        self.verbose_round_notifications: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not TELEGRAM_AVAILABLE:
            print("[Telegram] Module not installed.")
            return
        if not self.token or not self.allowed_chat_id:
            print("[Telegram] Missing token or chat ID.")
            return
        self.ready_event.clear()
        self.thread = threading.Thread(
            target=self._run_bot, daemon=True, name="TelegramBot"
        )
        self.thread.start()

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self.ready_event.wait(timeout)

    def stop(self) -> None:
        self.is_running = False
        if self.application and self.loop and not self.loop.is_closed():
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self.application.stop(), self.loop
                )
                fut.result(timeout=5)
            except Exception:
                pass

    def _run_bot(self) -> None:
        import traceback
        try:
            print(f"[Telegram] Starting bot thread (token={'SET' if self.token else 'MISSING'}, "
                  f"chat_id={'SET' if self.allowed_chat_id else 'MISSING'})")
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._dashboard_lock = asyncio.Lock()
            self._graph_lock = asyncio.Lock()

            print("[Telegram] Building application…")

            # post_init starts the state-watcher loop after the event loop is
            # running.  This replaces job_queue (which requires the optional
            # [job-queue] extra that may not be installed).
            async def _post_init(application) -> None:
                asyncio.create_task(self._state_watcher_loop())

            self.application = (
                Application.builder()
                .token(self.token)
                .post_init(_post_init)
                .build()
            )
            self.is_running = True
            self.ready_event.set()

            app = self.application
            app.add_handler(CommandHandler("start",  self._cmd_start))
            app.add_handler(CommandHandler("status", self._cmd_status))
            app.add_handler(CommandHandler("help",   self._cmd_help))
            app.add_handler(CommandHandler("menu",   self._cmd_menu))
            app.add_handler(CommandHandler("pause",  self._cmd_pause))
            app.add_handler(CommandHandler("resume", self._cmd_resume))
            app.add_handler(CommandHandler("go",     self._cmd_go))
            app.add_handler(CommandHandler("stop",   self._cmd_stop))
            app.add_handler(CommandHandler("panic",  self._cmd_panic))
            app.add_handler(CommandHandler("swap",   self._cmd_swap))
            app.add_handler(CommandHandler("set",    self._cmd_set))
            app.add_handler(CommandHandler("bias",   self._cmd_bias))
            app.add_handler(CommandHandler("feedtap", self._cmd_feedtap))
            app.add_handler(CommandHandler("collector", self._cmd_collector))
            app.add_handler(CallbackQueryHandler(self._handle_button))
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )
            app.add_error_handler(self._error_handler)

            print("[Telegram] Bot polling started.")
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as exc:
            print(f"[Telegram] Fatal error: {exc}")
            traceback.print_exc()
            self.is_running = False

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _check_auth(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        if str(update.effective_chat.id) != self.allowed_chat_id:
            await update.message.reply_text(
                f"⛔ Unauthorized. Your ID: `{update.effective_chat.id}`",
                parse_mode=_MD,
            )
            return False
        return True

    def _auth_query(self, query) -> bool:
        try:
            return str(query.message.chat.id) == self.allowed_chat_id
        except Exception:
            return False

    # ── Error handler ─────────────────────────────────────────────────────────

    async def _error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        import traceback
        err = str(context.error).lower()
        if any(k in err for k in ("not modified", "query is too old")):
            return
        print(f"[Telegram] Handler error: {context.error}")
        traceback.print_exc()

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else "None"
        print(f"[Telegram] /start from chat_id={chat_id} (allowed={self.allowed_chat_id})")
        if not await self._check_auth(update):
            return
        msg = await update.message.reply_text("⏳ _Connecting to SpinEdge…_", parse_mode=_MD)
        await asyncio.sleep(0.4)
        await msg.edit_text("🔄 _Syncing state…_", parse_mode=_MD)
        await asyncio.sleep(0.4)
        await msg.delete()
        await self.update_live_dashboard(force=True)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return
        await self.update_live_dashboard(force=True)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return
        await update.message.reply_text(HELP_TEXT, parse_mode=_MD)

    async def _cmd_bias(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/bias — per-wheel bias-scout report: which live wheels show a
        statistically confirmed (proven +EV) bias, with the Kelly-sized bet, or
        'sitting out everywhere' when no real edge exists."""
        if not await self._check_auth(update):
            return
        try:
            app = self.bridge._app
            report = app.bias_scout.report()
            bankroll = float(app.config.get("current_balance", 0) or 0)
            alert = app.bias_scout.alert_line(bankroll)
            if alert:
                report = report + "\n\n" + alert
        except Exception as e:
            report = f"Bias scout unavailable: {e}"
        # Plain text — the report contains $, ->, emoji that would need MarkdownV2 escaping.
        await update.message.reply_text(report[:3900])

    async def _cmd_feedtap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/feedtap [on|off|status] — control the result-feed tap that streams
        every visible table's spins into the bias scout over one WebSocket
        (no OCR). Default action: status."""
        if not await self._check_auth(update):
            return
        arg = (context.args[0].lower() if context.args else "status")
        try:
            app = self.bridge._app
            if arg in ("on", "start"):
                app.start_bias_feed_tap()
                msg = "📡 Feed tap STARTING — " + app.bias_feed_tap.status()
            elif arg in ("off", "stop"):
                app.stop_bias_feed_tap()
                msg = "📡 Feed tap stopping. " + app.bias_feed_tap.status()
            else:
                msg = app.bias_feed_tap.status()
        except Exception as e:
            msg = f"Feed tap unavailable: {e}"
        await update.message.reply_text(msg[:3900])

    async def _cmd_collector(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/collector [on|off|status] — control the standalone result collector
        that connects DIRECTLY to the provider feeds (no browser/CDP) configured
        in ~/.spinedge/config/feed_endpoints.json. Default action: status."""
        if not await self._check_auth(update):
            return
        arg = (context.args[0].lower() if context.args else "status")
        try:
            app = self.bridge._app
            if arg in ("on", "start"):
                app.start_bias_collector()
                msg = "🛰 Collector STARTING — " + app.bias_collector.status()
            elif arg in ("off", "stop"):
                app.stop_bias_collector()
                msg = "🛰 Collector stopping. " + app.bias_collector.status()
            else:
                msg = app.bias_collector.status()
        except Exception as e:
            msg = f"Collector unavailable: {e}"
        await update.message.reply_text(msg[:3900])

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return
        if bool(getattr(self.bridge._app, "bot_running", False)):
            self.bridge.toggle_pause()
            await self.update_live_dashboard(force=True)
        else:
            await update.message.reply_text("ℹ️ No active session.", parse_mode=_MD)

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._check_auth(update):
            return
        self.bridge.stop_session()
        await self.update_live_dashboard(force=True)

    async def _cmd_swap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/swap — show favorited strategies and bundles as inline buttons.
        Tap one to queue a swap (strategies at next round boundary, bundles
        immediately + queued strategy rebuild if a session is live)."""
        if not await self._check_auth(update):
            return

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        except Exception:
            await update.message.reply_text("Inline keyboards not available.")
            return

        strat_favs = list(self.bridge.get_favorite_strategies() or [])[:9]
        bundle_favs = list(self.bridge.get_favorite_bundles() or [])[:9]
        active_strat = self.bridge.active_strategy()
        active_bundle = self.bridge.active_bundle()

        if not strat_favs and not bundle_favs:
            await update.message.reply_text(
                "*No favorites yet.*\n\nIn the desktop app, right-click the strategy "
                "dropdown or the bundle dropdown and pick *Add to favorites*. "
                "Favorites then become available here as one-tap swaps.",
                parse_mode=_MD,
            )
            return

        rows = []
        if strat_favs:
            for i, name in enumerate(strat_favs):
                marker = "✅ " if name == active_strat else "★ "
                rows.append([InlineKeyboardButton(
                    f"{marker}{name}", callback_data=f"{CB_SWAP_STRAT_PFX}{i}"
                )])
        if bundle_favs:
            for i, name in enumerate(bundle_favs):
                marker = "✅ " if name == active_bundle else "◆ "
                rows.append([InlineKeyboardButton(
                    f"{marker}{name}", callback_data=f"{CB_SWAP_BUNDLE_PFX}{i}"
                )])

        # Stash the snapshot so the callback resolves to the exact name the
        # user saw (favorites could change between render and click).
        self.state.swap_strat_snapshot = list(strat_favs)
        self.state.swap_bundle_snapshot = list(bundle_favs)

        await update.message.reply_text(
            "⚡ *Quick Swap*\nTap a favorite to swap. Strategies apply at the "
            "next round boundary; bundles apply immediately.",
            parse_mode=_MD,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Open the Control Center — the full remote."""
        if not await self._check_auth(update):
            return
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        await asyncio.sleep(0.1)
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        self.state.menu = MENU_CONTROL
        await update.message.reply_text(
            render_control_center(data), parse_mode=_MD,
            reply_markup=Keyboards.control_center(data),
        )

    async def _cmd_go(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Start a session."""
        if not await self._check_auth(update):
            return
        if bool(getattr(self.bridge._app, "bot_running", False)):
            await update.message.reply_text("ℹ️ Already running.", parse_mode=_MD)
            return
        self.bridge.start_session()
        await self._wait_for_state(running=True, timeout=3.0)
        await self.update_live_dashboard(force=True)

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Resume a paused session."""
        if not await self._check_auth(update):
            return
        app = self.bridge._app
        if not bool(getattr(app, "bot_running", False)):
            await update.message.reply_text("ℹ️ No active session.", parse_mode=_MD)
            return
        if bool(getattr(app, "is_paused", False)):
            self.bridge.toggle_pause()
            await asyncio.sleep(0.3)
        await self.update_live_dashboard(force=True)

    async def _cmd_panic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """🚨 Emergency stop."""
        if not await self._check_auth(update):
            return
        self.bridge.stop_session()
        await update.message.reply_text("🚨 *EMERGENCY STOP sent.*", parse_mode=_MD)
        await self._wait_for_state(running=False, timeout=3.0)
        await self.update_live_dashboard(force=True)

    async def _cmd_set(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/set <field> <value> — power-user one-liner config change."""
        if not await self._check_auth(update):
            return
        parts = (update.message.text or "").split()
        if len(parts) < 3:
            fields = ", ".join(sorted(_SET_FIELDS.keys()))
            await update.message.reply_text(
                f"Usage: `/set <field> <value>`\n\nFields: {fields}", parse_mode=_MD
            )
            return
        field = parts[1].lower().strip()
        raw = parts[2].lstrip("$").replace(",", "")
        spec = _SET_FIELDS.get(field)
        if not spec:
            await update.message.reply_text(
                f"❌ Unknown field `{field}`.", parse_mode=_MD
            )
            return
        key, var_name, cast = spec
        try:
            val = cast(raw)
            if val < 0:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text("❌ Invalid value.", parse_mode=_MD)
            return
        self.bridge.set_config(key, val, var_name)
        await update.message.reply_text(f"✅ `{field}` → `{val}`", parse_mode=_MD)

    # ── Button callback router ────────────────────────────────────────────────

    async def _handle_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query or not self._auth_query(query):
            return
        await query.answer()

        cb = query.data or ""

        # ── Confirmation responses (blocking GUI flow) ─────────────────────
        if self.state.expecting_confirmation and cb in (CB_CONFIRM_YES, CB_CONFIRM_NO):
            self.state.confirmation_value = (cb == CB_CONFIRM_YES)
            self.state.confirmation_event.set()
            return

        # ── Strategy selection (indexed) ───────────────────────────────────
        if cb.startswith(CB_SET_STRAT_PFX):
            await self._select_strategy(query, int(cb[len(CB_SET_STRAT_PFX):]))
            return

        # ── Quick-swap favorites (strategies + bundles) ───────────────────
        if cb.startswith(CB_SWAP_STRAT_PFX):
            try:
                idx = int(cb[len(CB_SWAP_STRAT_PFX):])
                snap = getattr(self.state, "swap_strat_snapshot", []) or []
                if 0 <= idx < len(snap):
                    name = snap[idx]
                    self.bridge.request_strategy_swap(name)
                    await query.answer(f"⏳ Queued: {name}")
                else:
                    await query.answer("❌ Favorite no longer available")
            except Exception as e:
                await query.answer(f"⚠ {e}")
            return

        if cb.startswith(CB_SWAP_BUNDLE_PFX):
            try:
                idx = int(cb[len(CB_SWAP_BUNDLE_PFX):])
                snap = getattr(self.state, "swap_bundle_snapshot", []) or []
                if 0 <= idx < len(snap):
                    name = snap[idx]
                    self.bridge.request_bundle_swap(name)
                    await query.answer(f"📦 Loading: {name}")
                else:
                    await query.answer("❌ Favorite no longer available")
            except Exception as e:
                await query.answer(f"⚠ {e}")
            return

        # ── Mission-control: indexed pickers ───────────────────────────────
        if cb.startswith(CB_RISK_PFX):
            await self._pick_risk(query, self._safe_idx(cb, CB_RISK_PFX))
            return
        if cb.startswith(CB_PROG_PFX):
            await self._pick_progression(query, self._safe_idx(cb, CB_PROG_PFX))
            return
        if cb.startswith(CB_ROT_MODE_PFX):
            await self._pick_rotation_mode(query, self._safe_idx(cb, CB_ROT_MODE_PFX))
            return
        if cb.startswith(CB_ROT_TRIG_PFX):
            await self._pick_rotation_trigger(query, self._safe_idx(cb, CB_ROT_TRIG_PFX))
            return
        if cb.startswith(CB_BUNDLE_PAGE_PFX):
            self.state.bundle_browse_page = self._safe_idx(cb, CB_BUNDLE_PAGE_PFX)
            await self._show_bundles(query, refresh=False)
            return
        if cb.startswith(CB_BUNDLE_PFX):
            await self._pick_bundle(query, self._safe_idx(cb, CB_BUNDLE_PFX))
            return

        # ── Mission-control: toggles ────────────────────────────────────────
        if cb == CB_TOGGLE_EXT_HIGH:
            data = self.bridge.get_cached_data()
            new_val = not data.ext_at_high
            self.bridge.set_config("session_ext_at_high", new_val, "session_ext_at_high_var")
            await query.answer("✅ Extend at High ON" if new_val else "❌ Extend at High OFF")
            await asyncio.sleep(0.15)
            await self._refresh_menu(query, CB_SESSION)
            return
        if cb == CB_TOGGLE_ROTATION:
            new = self.bridge.toggle_var("enable_strategy_rotation", "enable_strategy_rotation_var")
            await query.answer("✅ Rotation ON" if new else "❌ Rotation OFF")
            await asyncio.sleep(0.15)
            await self._refresh_menu(query, CB_ROTATION)
            return
        if cb == CB_TOGGLE_ESC:
            new = self.bridge.toggle_var("enable_escalation_on_loss", "enable_escalation_on_loss_var")
            await query.answer("✅ Escalation ON" if new else "❌ Escalation OFF")
            await asyncio.sleep(0.15)
            await self._refresh_menu(query, CB_ESCALATION)
            return
        if cb == CB_TOGGLE_HUD:
            new = self.bridge.toggle_hud()
            await query.answer("🖥 HUD ON" if new else "🖥 HUD OFF")
            await asyncio.sleep(0.15)
            await self._refresh_menu(query, CB_QUICK)
            return
        if cb == CB_TOGGLE_VERBOSE:
            self.verbose_round_notifications = not self.verbose_round_notifications
            await query.answer("🔔 Verbose alerts ON" if self.verbose_round_notifications else "🔕 Verbose alerts OFF")
            await asyncio.sleep(0.1)
            await self._refresh_menu(query, CB_QUICK)
            return

        # ── Mission-control: quick actions ──────────────────────────────────
        if cb == CB_RESET:
            self.bridge.session_reset()
            await query.answer("🔄 Session stats reset")
            await asyncio.sleep(0.2)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._refresh_menu(query, CB_QUICK)
            return
        if cb == CB_PANIC:
            self.bridge.stop_session()
            await query.answer("🚨 EMERGENCY STOP", show_alert=True)
            await self._wait_for_state(running=False, timeout=3.0)
            await self.update_live_dashboard(force=True)
            return

        # ── Mission-control: edit prompts (table-driven) ────────────────────
        if cb in _EDIT_PROMPTS:
            mode, back, prompt = _EDIT_PROMPTS[cb]
            await self._set_input_mode(query, mode, back, prompt)
            return

        # ── Toggle guardrails ──────────────────────────────────────────────
        if cb == CB_TOGGLE_S_STOPS:
            new = self.bridge.toggle_var("enable_session_stops", "enable_session_stops_var")
            await query.answer("✅ ON" if new else "❌ OFF")
            await asyncio.sleep(0.15)
            await self._refresh_guardrails(query)
            return

        if cb == CB_TOGGLE_TRAIL:
            new = self.bridge.toggle_var("enable_trailing_stop", "enable_trailing_stop_var")
            await query.answer("✅ ON" if new else "❌ OFF")
            await asyncio.sleep(0.15)
            await self._refresh_guardrails(query)
            return

        if cb == CB_TOGGLE_G_STOP:
            new = self.bridge.toggle_var("enable_global_stop", "enable_global_stop_var")
            await query.answer("✅ ON" if new else "❌ OFF")
            await asyncio.sleep(0.15)
            await self._refresh_guardrails(query)
            return

        if cb == CB_TOGGLE_EXT_WIN:
            data = self.bridge.get_cached_data()
            new_val = not data.ext_after_win
            self.bridge.set_config("session_ext_after_win", new_val, "session_ext_after_win_var")
            await query.answer("✅ Extend After Win ON" if new_val else "❌ Extend After Win OFF")
            await asyncio.sleep(0.15)
            data2 = self.bridge.get_cached_data(list(self.state.recent_results))
            await self._safe_edit(
                query, render_session_config(data2), Keyboards.session_config(data2)
            )
            return

        # ── Navigation ─────────────────────────────────────────────────────
        nav = {
            CB_DASHBOARD: self._show_dashboard,
            CB_SETTINGS:  self._show_settings,
            CB_GUARDRAILS: self._show_guardrails,
            CB_SESSION:   self._show_session,
            CB_STRATEGY:  self._show_strategy_list,
            CB_STATS:     self._show_stats,
            CB_CONTROL:   self._show_control,
            CB_BETTING:   self._show_betting,
            CB_RISK:      self._show_risk,
            CB_ROTATION:  self._show_rotation,
            CB_ROTATION_MODE: self._show_rotation_mode,
            CB_ROTATION_TRIG: self._show_rotation_trigger,
            CB_ESCALATION: self._show_escalation,
            CB_BUNDLES:   self._show_bundles,
            CB_PROGRESSION: self._show_progression,
            CB_QUICK:     self._show_quick,
        }
        if cb in nav:
            await nav[cb](query)
            return

        # ── Session controls ────────────────────────────────────────────────
        if cb == CB_START:
            await self._do_start(query)
        elif cb == CB_STOP:
            await self._do_stop(query)
        elif cb == CB_TOGGLE_PAUSE:
            await self._do_toggle_pause(query)
        elif cb == CB_GRAPH:
            await self._send_graph(query)

        # ── Edit prompts ────────────────────────────────────────────────────
        elif cb == CB_EDIT_BASE_BET:
            await self._set_input_mode(query, "BASE_BET", CB_SETTINGS,
                                       "✏️ *Set Base Bet*\n\nReply with a dollar amount.\n_e.g. `2.50`_")
        elif cb == CB_EDIT_S_PROFIT:
            await self._set_input_mode(query, "SESS_PROFIT", CB_GUARDRAILS,
                                       "✏️ *Set Session Profit Target*\n\nReply with a dollar amount.\n_e.g. `100`_")
        elif cb == CB_EDIT_S_LOSS:
            await self._set_input_mode(query, "SESS_LOSS", CB_GUARDRAILS,
                                       "✏️ *Set Session Stop Loss*\n\nReply with a dollar amount.\n_e.g. `50`_")
        elif cb == CB_EDIT_TRAILING:
            await self._set_input_mode(query, "TRAILING", CB_GUARDRAILS,
                                       "✏️ *Set Trailing Stop*\n\nReply with a dollar amount.\n_e.g. `30`_")
        elif cb == CB_EDIT_G_PROFIT:
            await self._set_input_mode(query, INPUT_GLOB_PROFIT, CB_GUARDRAILS,
                                       "✏️ *Set Global Profit Target*\n\nReply with:\n"
                                       "_`100` = $100_\n_`10%` = 10% of balance_")
        elif cb == CB_EDIT_G_LOSS:
            await self._set_input_mode(query, INPUT_GLOB_LOSS, CB_GUARDRAILS,
                                       "✏️ *Set Global Stop Loss*\n\nReply with:\n"
                                       "_`50` = $50_\n_`5%` = 5% of balance_")
        elif cb == CB_EDIT_DURATION:
            await self._set_input_mode(query, "DURATION", CB_SESSION,
                                       "✏️ *Set Session Duration*\n\nReply with minutes.\n_e.g. `15`_")
        elif cb == CB_EDIT_N_SESS:
            await self._set_input_mode(query, "NUM_SESSIONS", CB_SESSION,
                                       "✏️ *Set Number of Sessions*\n\nReply with a number (`0` = unlimited).\n_e.g. `3`_")

    # ── Navigation helpers ────────────────────────────────────────────────────

    async def _show_dashboard(self, query) -> None:
        self.state.menu = MENU_DASHBOARD
        await self.update_live_dashboard(force=True)

    async def _show_settings(self, query) -> None:
        self.state.menu = MENU_SETTINGS
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_settings(data), Keyboards.settings(data))

    async def _show_guardrails(self, query) -> None:
        self.state.menu = MENU_GUARDRAILS
        await self._refresh_guardrails(query)

    async def _refresh_guardrails(self, query) -> None:
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_guardrails(data), Keyboards.guardrails(data))

    async def _show_session(self, query) -> None:
        self.state.menu = MENU_SESSION
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_session_config(data), Keyboards.session_config(data))

    async def _show_strategy_list(self, query) -> None:
        self.state.menu = MENU_STRATEGY
        strategies = self.bridge.get_strategies()
        self.state.strategy_list_cache = strategies
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        text = (
            "🎯 *Select Strategy*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"Current: `{data.strategy}`\n\n"
            "_Tap a strategy to switch._"
        )
        await self._safe_edit(query, text, Keyboards.strategy_list(strategies, data.strategy))

    async def _show_stats(self, query) -> None:
        self.state.menu = MENU_STATS
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_stats(data), Keyboards.stats_view())

    # ── Mission-control navigation ─────────────────────────────────────────────

    @staticmethod
    def _safe_idx(cb: str, prefix: str) -> int:
        try:
            return int(cb[len(prefix):])
        except (ValueError, TypeError):
            return -1

    async def _refresh_menu(self, query, menu_cb: str) -> None:
        """Re-render one of the data-driven menus in place (used after toggles)."""
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        m = {
            CB_CONTROL:   (render_control_center, Keyboards.control_center),
            CB_BETTING:   (render_betting,        Keyboards.betting),
            CB_GUARDRAILS: (render_guardrails,    Keyboards.guardrails),
            CB_SESSION:   (render_session_config, Keyboards.session_config),
            CB_ROTATION:  (render_rotation,       Keyboards.rotation),
            CB_ESCALATION: (render_escalation,    Keyboards.escalation),
            CB_QUICK:     (render_quick,          Keyboards.quick_actions),
            CB_SETTINGS:  (render_settings,       Keyboards.settings),
        }
        entry = m.get(menu_cb)
        if not entry:
            return
        render_fn, kb_fn = entry
        await self._safe_edit(query, render_fn(data), kb_fn(data))

    async def _show_control(self, query) -> None:
        self.state.menu = MENU_CONTROL
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        await asyncio.sleep(0.08)
        await self._refresh_menu(query, CB_CONTROL)

    async def _show_betting(self, query) -> None:
        self.state.menu = MENU_BETTING
        await self._refresh_menu(query, CB_BETTING)

    async def _show_risk(self, query) -> None:
        self.state.menu = MENU_RISK
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_risk(data), Keyboards.risk_profile(data.risk_profile))

    async def _show_rotation(self, query) -> None:
        self.state.menu = MENU_ROTATION
        await self._refresh_menu(query, CB_ROTATION)

    async def _show_rotation_mode(self, query) -> None:
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(
            query, "🎛 *Rotation Mode*\n\nHow the next strategy is chosen.",
            Keyboards.rotation_mode_picker(data.rotation_mode),
        )

    async def _show_rotation_trigger(self, query) -> None:
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(
            query, "⚡ *Rotation Trigger*\n\nWhen to switch strategy.",
            Keyboards.rotation_trigger_picker(data.rotation_trigger),
        )

    async def _show_escalation(self, query) -> None:
        self.state.menu = MENU_ESCALATION
        await self._refresh_menu(query, CB_ESCALATION)

    async def _show_quick(self, query) -> None:
        self.state.menu = MENU_QUICK
        await self._refresh_menu(query, CB_QUICK)

    async def _show_progression(self, query) -> None:
        self.state.menu = MENU_PROGRESSION
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(
            query, render_progression(data.progression),
            Keyboards.progression_picker(data.progression),
        )

    async def _show_bundles(self, query, refresh: bool = True) -> None:
        self.state.menu = MENU_BUNDLES
        if refresh or not self.state.bundle_browse_cache:
            self.state.bundle_browse_cache = self.bridge.get_all_bundles()
            self.state.bundle_browse_page = 0
        bundles = self.state.bundle_browse_cache
        page = self.state.bundle_browse_page
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(
            query, render_bundles(data, len(bundles), page),
            Keyboards.bundles_browser(bundles, data.active_bundle, page),
        )

    # ── Mission-control pickers ────────────────────────────────────────────────

    async def _pick_risk(self, query, idx: int) -> None:
        if 0 <= idx < len(RISK_PROFILES):
            name = RISK_PROFILES[idx]
            self.bridge.set_risk_profile(name)
            await query.answer(f"🎚 {name}")
            await asyncio.sleep(0.2)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._show_risk(query)
        else:
            await query.answer("Invalid", show_alert=True)

    async def _pick_progression(self, query, idx: int) -> None:
        if 0 <= idx < len(PROGRESSIONS):
            name = PROGRESSIONS[idx]
            self.bridge.set_config("progression_type", name, "progression_var")
            await query.answer(f"📐 {name}")
            await asyncio.sleep(0.15)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._show_betting(query)
        else:
            await query.answer("Invalid", show_alert=True)

    async def _pick_rotation_mode(self, query, idx: int) -> None:
        if 0 <= idx < len(ROTATION_MODES):
            name = ROTATION_MODES[idx]
            self.bridge.set_config("rotation_mode", name, "rotation_mode_var")
            await query.answer(f"🎛 {name}")
            await asyncio.sleep(0.15)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._show_rotation(query)
        else:
            await query.answer("Invalid", show_alert=True)

    async def _pick_rotation_trigger(self, query, idx: int) -> None:
        if 0 <= idx < len(ROTATION_TRIGGERS):
            name = ROTATION_TRIGGERS[idx]
            self.bridge.set_config("rotation_trigger", name, "rotation_trigger_var")
            await query.answer(f"⚡ {name}")
            await asyncio.sleep(0.15)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._show_rotation(query)
        else:
            await query.answer("Invalid", show_alert=True)

    async def _pick_bundle(self, query, idx: int) -> None:
        bundles = self.state.bundle_browse_cache or []
        if 0 <= idx < len(bundles):
            name = bundles[idx]
            self.bridge.request_bundle_swap(name)
            await query.answer(f"📦 Loading: {name}")
            await asyncio.sleep(0.3)
            self.bridge.schedule_cache_refresh(list(self.state.recent_results))
            await asyncio.sleep(0.1)
            await self._show_bundles(query, refresh=False)
        else:
            await query.answer("❌ Not available", show_alert=True)

    # ── Session control helpers ───────────────────────────────────────────────

    async def _do_start(self, query) -> None:
        if bool(getattr(self.bridge._app, "bot_running", False)):
            await query.answer("Already running!", show_alert=True)
            return
        self.bridge.start_session()          # schedules start_bot via root.after
        await query.answer("▶️ Starting session…")
        # Poll until bot_running flips True (or give up after 3 s).
        await self._wait_for_state(running=True, timeout=3.0)
        await self.update_live_dashboard(force=True)

    async def _do_stop(self, query) -> None:
        self.bridge.stop_session()           # schedules stop_bot via root.after
        await query.answer("⏹ Stopping…")
        await self._wait_for_state(running=False, timeout=3.0)
        await self.update_live_dashboard(force=True)

    async def _do_toggle_pause(self, query) -> None:
        app = self.bridge._app
        if not bool(getattr(app, "bot_running", False)):
            await query.answer("No active session.", show_alert=True)
            return
        was_paused = bool(getattr(app, "is_paused", False))
        self.bridge.toggle_pause()           # schedules toggle_pause via root.after
        label = "▶️ Resumed" if was_paused else "⏸ Paused"
        await query.answer(label)
        await asyncio.sleep(0.4)             # give root.after time to execute
        await self.update_live_dashboard(force=True)

    async def _wait_for_state(self, running: bool, timeout: float = 3.0) -> None:
        """Poll until bot_running matches the expected value or timeout expires.
        Reads only plain Python bool — never calls Tkinter var.get()."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if bool(getattr(self.bridge._app, "bot_running", False)) == running:
                return
            await asyncio.sleep(0.15)
        # Timed out — refresh anyway with whatever state we have

    async def _send_graph(self, query) -> None:
        bio = self.bridge.get_graph_png()
        if not bio:
            await query.answer("No graph data yet.", show_alert=True)
            return
        try:
            await query.message.reply_photo(
                photo=bio,
                caption="📈 *Profit Chart*",
                parse_mode=_MD,
            )
        except Exception:
            await query.answer("Graph unavailable.", show_alert=True)

    # ── Strategy selection ────────────────────────────────────────────────────

    async def _select_strategy(self, query, idx: int) -> None:
        strategies = self.state.strategy_list_cache or self.bridge.get_strategies()
        if idx >= len(strategies):
            await query.answer("Invalid selection.", show_alert=True)
            return
        selected = strategies[idx]
        self.bridge.set_config("strategy",              selected, "strategy_var")
        self.bridge.set_config("auto_roulette_strategy", selected, "auto_roulette_strategy_var")
        await query.answer(f"✅ {selected}")
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        await self._safe_edit(query, render_settings(data), Keyboards.settings(data))

    # ── Input mode helpers ────────────────────────────────────────────────────

    async def _set_input_mode(
        self, query, mode: str, back_data: str, prompt_text: str
    ) -> None:
        self.state.input_mode    = mode
        self.state.input_back_data = back_data
        await self._safe_edit(query, prompt_text, Keyboards.cancel(back_data))

    async def _prompt_input(self, query, label: str, mode: str, back_data: str) -> None:
        """Generic numeric input prompt."""
        text = (
            f"✏️ *{label}*\n"
            "━━━━━━━━━━━━━━━━\n\n"
            "Reply with a number.\n"
            "_e.g. `100` or `10.5`_"
        )
        self.state.input_mode      = mode
        self.state.input_back_data = back_data
        await self._safe_edit(query, text, Keyboards.cancel(back_data))

    # ── Message handler (input collection) ───────────────────────────────────

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._check_auth(update):
            return

        text = (update.message.text or "").strip()
        mode = self.state.input_mode

        # ── Blocking input for GUI flows (window selection, etc.) ──────────
        if self.state.expecting_input:
            self.state.input_value = text
            self.state.input_event.set()
            return

        if mode == INPUT_NONE:
            return

        # ── Global profit/loss — accept "100" or "10%" ─────────────────────
        if mode in (INPUT_GLOB_PROFIT, INPUT_GLOB_LOSS):
            key  = "global_profit_stop"  if mode == INPUT_GLOB_PROFIT else "global_stop_loss"
            var  = "global_profit_stop_var" if mode == INPUT_GLOB_PROFIT else "global_stop_loss_var"
            if text.endswith("%"):
                try:
                    float(text[:-1])
                    val: Any = text
                except ValueError:
                    await update.message.reply_text(
                        "❌ Invalid format. Use `100` or `10%`", parse_mode=_MD
                    )
                    return
            else:
                try:
                    val = float(text.lstrip("$").replace(",", ""))
                    if val < 0:
                        raise ValueError
                except ValueError:
                    await update.message.reply_text(
                        "❌ Invalid number. Try `100` or `10%`", parse_mode=_MD
                    )
                    return
            back = self.state.input_back_data
            self.bridge.set_config(key, val, var)
            self.state.input_mode = INPUT_NONE
            await update.message.reply_text(f"✅ Updated to `{text}`", parse_mode=_MD)
            await self._send_back_menu(update.effective_chat.id, back)
            return

        # ── Rotation list (free-text, comma-separated) ─────────────────────
        if mode == INPUT_ROTATION_LIST:
            self.bridge.set_config("rotation_strategies", text, "rotation_strategies_var")
            # Enable rotation so the new list actually drives the next run.
            self.bridge.set_config("enable_strategy_rotation", True, "enable_strategy_rotation_var")
            self.state.input_mode = INPUT_NONE
            await update.message.reply_text(f"✅ Rotation list set:\n`{text}`", parse_mode=_MD)
            await self._send_back_menu(update.effective_chat.id, self.state.input_back_data)
            return

        # ── Standard numeric fields ────────────────────────────────────────
        if mode in INPUT_CONFIG_MAP:
            config_key, var_name, cast, unit = INPUT_CONFIG_MAP[mode]
            try:
                val = cast(text.lstrip("$").replace(",", ""))
                if val < 0:
                    raise ValueError
            except (ValueError, TypeError):
                kind = "integer" if cast is int else "number"
                await update.message.reply_text(
                    f"❌ Please enter a positive {kind}.", parse_mode=_MD
                )
                return
            back = self.state.input_back_data
            self.bridge.set_config(config_key, val, var_name)
            self.state.input_mode = INPUT_NONE
            suffix = f" {unit}" if unit else ""
            await update.message.reply_text(f"✅ Updated to `{val}{suffix}`", parse_mode=_MD)
            await self._send_back_menu(update.effective_chat.id, back)

    async def _send_back_menu(self, chat_id: int, back_data: str) -> None:
        """After text input is accepted, send a fresh menu so the user isn't left on the prompt."""
        # Trigger a cache refresh so the updated config value appears in the menu.
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        await asyncio.sleep(0.08)   # give root.after time to populate the cache
        data = self.bridge.get_cached_data(list(self.state.recent_results))
        menu_map = {
            CB_SETTINGS:   (render_settings,       Keyboards.settings),
            CB_GUARDRAILS: (render_guardrails,      Keyboards.guardrails),
            CB_SESSION:    (render_session_config,  Keyboards.session_config),
            CB_BETTING:    (render_betting,         Keyboards.betting),
            CB_ESCALATION: (render_escalation,      Keyboards.escalation),
            CB_ROTATION:   (render_rotation,        Keyboards.rotation),
            CB_CONTROL:    (render_control_center,  Keyboards.control_center),
            CB_DASHBOARD:  (None,                   None),
        }
        entry = menu_map.get(back_data)
        if not entry or entry[0] is None:
            # Fall back to dashboard update
            await self.update_live_dashboard(force=True)
            return
        render_fn, kb_fn = entry
        try:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=render_fn(data),
                parse_mode=_MD,
                reply_markup=kb_fn(data),
            )
        except Exception as exc:
            print(f"[Telegram] Back-menu send error: {exc}")

    # ── Dashboard ─────────────────────────────────────────────────────────────

    async def update_live_dashboard(self, force: bool = False) -> None:
        """Create or refresh the pinned dashboard message."""
        if not self.application or not self.loop or not self.allowed_chat_id:
            return

        # Respect a flood-control hold from a previous failure. force=True
        # cannot bypass this — Telegram's clock, not ours, decides.
        now = time.monotonic()
        if now < self._send_floor_until:
            return
        # Floor expired; arm the logger so the next flood event is reported.
        if self._send_floor_until > 0 and self._floor_logged:
            print("[Telegram] Flood-control window cleared. Resuming sends.")
            self._send_floor_until = 0.0
            self._floor_logged = False

        if not force and (now - self._last_dashboard) < _DASHBOARD_RATE:
            return

        async with self._dashboard_lock:
            self._last_dashboard = time.monotonic()

            # Use the GUI-thread cache — never calls Tkinter var.get() from here.
            data = self.bridge.get_cached_data(list(self.state.recent_results))
            text = render_dashboard(data)
            kb   = Keyboards.main_dashboard(data.is_running, data.is_paused)
            # NOTE: get_graph_png() is intentionally NOT called here.
            # Calling it from the bot thread can deadlock the asyncio event loop
            # if the app uses a Tkinter-backed matplotlib.  The graph is only
            # fetched when the user explicitly presses the graph button.

            try:
                if self.state.dashboard_msg_id:
                    edited = await self._edit_dashboard(text, kb)
                    if edited:
                        return
                    # Fall through to create new

                await self._create_dashboard(text, kb)
            except Exception as exc:
                print(f"[Telegram] Dashboard error: {exc}")

    async def _edit_dashboard(self, text: str, kb) -> bool:
        """Try to edit existing dashboard message. Returns True on success."""
        mid = self.state.dashboard_msg_id
        cid = self.allowed_chat_id
        bot = self.application.bot

        try:
            await bot.edit_message_text(
                chat_id=cid, message_id=mid,
                text=text, parse_mode=_MD, reply_markup=kb,
            )
            return True
        except Exception as exc:
            err = str(exc).lower()
            if "not modified" in err:
                return True
            # Flood control — engage circuit breaker so we don't make it worse
            retry_in = _parse_flood_retry(exc)
            if retry_in > 0:
                self._engage_flood_breaker(retry_in, source="dashboard edit")
                return True  # don't fall through to _create_dashboard
            if "message to edit not found" in err or "message_id_invalid" in err:
                self.state.dashboard_msg_id = None
                return False
            if "there is no text in the message" in err:
                # Previous dashboard was a photo — convert to text
                try:
                    self.state.dashboard_msg_id = None  # force recreate as text
                    return False
                except Exception:
                    return False
            # Other error — skip recreate
            return True

    def _engage_flood_breaker(self, retry_in: float, source: str) -> None:
        """Park all outbound traffic until the retry window passes."""
        self._send_floor_until = time.monotonic() + retry_in
        if not self._floor_logged:
            self._floor_logged = True
            print(f"[Telegram] Flood control hit ({source}). "
                  f"Pausing all sends for {int(retry_in)}s.")

    async def _create_dashboard(self, text: str, kb) -> None:
        """Send a new dashboard text message and pin it."""
        bot = self.application.bot
        cid = self.allowed_chat_id

        try:
            msg = await bot.send_message(
                chat_id=cid, text=text, parse_mode=_MD, reply_markup=kb,
            )
        except Exception as exc:
            retry_in = _parse_flood_retry(exc)
            if retry_in > 0:
                self._engage_flood_breaker(retry_in, source="dashboard send")
                return
            print(f"[Telegram] Dashboard create error: {exc}")
            return
        self.state.dashboard_msg_id = msg.message_id
        try:
            await msg.pin(disable_notification=True)
        except Exception:
            pass

    # ── Live PnL graph (auto-updating pinned photo) ──────────────────────────

    async def update_live_graph(self, force: bool = False) -> None:
        """Create or refresh the pinned PnL graph message.

        Generates a fresh PNG via the bridge, then either edits the existing
        pinned photo in place (atomic edit_message_media) or sends a new one
        and pins it. Honors the bot-wide flood-control breaker and an
        independent rate limit (_GRAPH_RATE) so we don't spam Telegram.
        """
        if not self.application or not self.loop or not self.allowed_chat_id:
            return

        now = time.monotonic()
        # Respect a flood-control hold.
        if now < self._send_floor_until:
            return
        if not force and (now - self._last_graph) < _GRAPH_RATE:
            return

        async with self._graph_lock:
            # Re-check inside the lock to avoid a thundering-herd refresh
            # if multiple watcher ticks pile up.
            now = time.monotonic()
            if now < self._send_floor_until:
                return
            if not force and (now - self._last_graph) < _GRAPH_RATE:
                return
            self._last_graph = now

            try:
                bio = self.bridge.get_graph_png()
            except Exception as exc:
                print(f"[Telegram] Graph PNG render failed: {exc}")
                return
            if not bio:
                return

            try:
                if self.state.graph_msg_id:
                    edited = await self._edit_graph(bio)
                    if edited:
                        return
                    # Fall through to recreate
                await self._create_graph(bio)
            except Exception as exc:
                print(f"[Telegram] Graph refresh error: {exc}")

    async def _edit_graph(self, bio) -> bool:
        from telegram import InputMediaPhoto
        bot = self.application.bot
        try:
            await bot.edit_message_media(
                chat_id=self.allowed_chat_id,
                message_id=self.state.graph_msg_id,
                media=InputMediaPhoto(media=bio, caption="📈 *Live PnL Graph*",
                                      parse_mode=_MD),
            )
            return True
        except Exception as exc:
            err = str(exc).lower()
            if "not modified" in err:
                return True
            retry_in = _parse_flood_retry(exc)
            if retry_in > 0:
                self._engage_flood_breaker(retry_in, source="graph edit")
                return True
            if "message to edit not found" in err or "message_id_invalid" in err:
                self.state.graph_msg_id = None
                return False
            # Unknown error — drop the id and try a fresh send next tick
            print(f"[Telegram] Graph edit error: {exc}")
            self.state.graph_msg_id = None
            return False

    async def _create_graph(self, bio) -> None:
        bot = self.application.bot
        try:
            msg = await bot.send_photo(
                chat_id=self.allowed_chat_id, photo=bio,
                caption="📈 *Live PnL Graph*", parse_mode=_MD,
            )
        except Exception as exc:
            retry_in = _parse_flood_retry(exc)
            if retry_in > 0:
                self._engage_flood_breaker(retry_in, source="graph send")
                return
            print(f"[Telegram] Graph send error: {exc}")
            return
        self.state.graph_msg_id = msg.message_id
        try:
            await msg.pin(disable_notification=True)
        except Exception:
            pass

    # ── Safe message edit ─────────────────────────────────────────────────────

    async def _safe_edit(
        self, query, text: str, keyboard: InlineKeyboardMarkup,
        parse_mode: str = _MD,
    ) -> None:
        try:
            await query.message.edit_text(
                text, parse_mode=parse_mode, reply_markup=keyboard
            )
        except Exception as exc:
            err = str(exc).lower()
            if "not modified" in err:
                return
            if "there is no text in the message" in err:
                try:
                    await query.message.edit_caption(
                        text, parse_mode=parse_mode, reply_markup=keyboard
                    )
                except Exception:
                    pass
            else:
                print(f"[Telegram] Edit error: {exc}")

    # ── State watcher (background asyncio task, every 2 s) ───────────────────

    async def _state_watcher_loop(self) -> None:
        """Runs as an asyncio task started via post_init. Replaces job_queue."""
        await asyncio.sleep(3)          # initial delay — same as first=3
        while self.is_running:
            try:
                await self._state_watcher(None)
            except Exception as exc:
                print(f"[Telegram] State watcher error: {exc}")
            await asyncio.sleep(2)      # interval=2

    async def _state_watcher(self, _context) -> None:
        """
        Detect GUI-initiated state changes (start/stop/pause from the software UI)
        and push a dashboard update when they happen.

        Thread-safety rules enforced here:
          - Only reads plain Python bool attributes — never calls Tkinter var.get().
          - schedule_cache_refresh() delegates the var.get() work to the GUI thread
            via root.after, so update_live_dashboard() reads a fresh but safe snapshot.
        """
        app = self.bridge._app
        try:
            is_running = bool(getattr(app, "bot_running", False))
            is_paused  = bool(getattr(app, "is_paused",   False))
        except Exception:
            return

        prev_r = getattr(self, "_prev_running_state", None)
        prev_p = getattr(self, "_prev_paused_state",  None)
        self._prev_running_state = is_running
        self._prev_paused_state  = is_paused

        state_changed = prev_r is None or prev_r != is_running or prev_p != is_paused

        # Always keep the cache warm.  schedule_cache_refresh() uses root.after so
        # the actual var.get() calls happen on the Tkinter thread — never here.
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))

        if state_changed:
            # Small delay so root.after callback can run before we read the cache.
            await asyncio.sleep(0.05)
            await self.update_live_dashboard(force=True)
            # Force the graph to refresh on state transitions too — start /
            # stop / pause should land a fresh chart immediately.
            await self.update_live_graph(force=True)
        elif is_running:
            # Periodically sync dashboard during active gameplay.
            # update_live_dashboard is rate-limited by _DASHBOARD_RATE and
            # update_live_graph by _GRAPH_RATE, so neither floods Telegram.
            await self.update_live_dashboard()
            await self.update_live_graph()

    # ── Scheduled dashboard refresh (call from any thread) ────────────────────

    def _schedule_dashboard_update(self) -> None:
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.update_live_dashboard(), self.loop
            )

    # ── Public notification API (called from GUI / session runner) ────────────

    def notify_win(self, amount: float, message: str = "") -> None:
        self.state.recent_results.append(True)
        # Always notify big wins / jackpots — those are signal events.
        # Per-round small wins go through verbose mode only; the pinned
        # dashboard already shows the running tally and last spin.
        if amount >= 500:
            self.notif.send(NotifType.JACKPOT, amount=amount)
        elif amount >= 50:
            self.notif.send(NotifType.BIG_WIN, amount=amount)
        elif self.verbose_round_notifications:
            self.notif.send(NotifType.WIN, amount=amount)
        streak = self._win_streak()
        if streak >= 5:
            self.notif.send(NotifType.WIN_STREAK, streak=streak, amount=amount)
        # Refresh cache on GUI thread so the dashboard picks up the new win state.
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        self._schedule_dashboard_update()

    def notify_loss(self, amount: float, message: str = "") -> None:
        self.state.recent_results.append(False)
        if self.verbose_round_notifications:
            self.notif.send(NotifType.LOSS, amount=amount)
        streak = self._loss_streak()
        if streak >= 5:
            self.notif.send(NotifType.LOSS_STREAK, streak=streak, amount=amount)
        # Refresh cache on GUI thread so the dashboard picks up the new loss state.
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        self._schedule_dashboard_update()

    def notify_session_start(self, session_num: int = 1, total: int = 1) -> None:
        label = f"Session {session_num}/{total}" if total > 1 else "Session started"
        self.notif.send(NotifType.SESSION_START, label=label)
        self._schedule_dashboard_update()

    def notify_session_end(self, pnl: float, wins: int, losses: int) -> None:
        wr = (wins / (wins + losses) * 100) if (wins + losses) else 0.0
        self.notif.send(
            NotifType.SESSION_END, pnl=pnl,
            wins=wins, losses=losses, wr=wr,
        )
        # Force-refresh so the pinned dashboard shows the FINAL session state
        # (PnL, peak, recent results) instead of staying frozen on a mid-session
        # snapshot until the next state-watcher tick. Also force the live PnL
        # graph so the user sees the closing curve right away.
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.update_live_dashboard(force=True), self.loop
            )
            asyncio.run_coroutine_threadsafe(
                self.update_live_graph(force=True), self.loop
            )

    def notify_stop_triggered(self, reason: str, pnl: float) -> None:
        # Stash the reason so the bridge surfaces it on the next snapshot — the
        # dashboard banner reads it and the ack/notification echoes it back.
        try:
            self.bridge._app.last_stop_reason = str(reason or "")
        except Exception:
            pass
        self.notif.send(NotifType.STOP, reason=reason, pnl=pnl)
        self.bridge.schedule_cache_refresh(list(self.state.recent_results))
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.update_live_dashboard(force=True), self.loop
            )
            asyncio.run_coroutine_threadsafe(
                self.update_live_graph(force=True), self.loop
            )

    # ── Backward-compat public API ────────────────────────────────────────────

    def send_notification(self, message: str, reply_markup=None) -> None:
        """Legacy: send raw text notification."""
        self.notif.send_raw(message)

    def send_smart_notification(
        self, message: str, type: str = "info", value: float = 0.0, reply_markup=None
    ) -> None:
        """Legacy: send typed notification."""
        self.notif.send_smart(message, notif_type=type, value=value)

    def send_confirmation_request(self, prompt: str) -> None:
        """Legacy: send a Yes/No prompt (non-blocking)."""
        text = (
            "❓ *CONFIRMATION REQUIRED*\n\n"
            f"{prompt}"
        )
        self.notif.send_raw(text)
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._send_confirm_keyboard(text), self.loop
            )

    async def _send_confirm_keyboard(self, text: str) -> None:
        if not self.application or not self.allowed_chat_id:
            return
        try:
            await self.application.bot.send_message(
                chat_id=self.allowed_chat_id,
                text=text,
                parse_mode=_MD,
                reply_markup=Keyboards.confirm(),
            )
        except Exception as exc:
            print(f"[Telegram] Confirmation send error: {exc}")

    def request_confirmation(self, prompt: str, timeout: float = 60) -> bool:
        """Blocking call: send prompt and wait for Yes/No. Returns True = Yes."""
        self.state.confirmation_value = None
        self.state.confirmation_event.clear()
        self.state.expecting_confirmation = True
        self.send_confirmation_request(prompt)
        self.state.confirmation_event.wait(timeout=timeout)
        self.state.expecting_confirmation = False
        return bool(self.state.confirmation_value)

    def request_acknowledgment(self, message: str, timeout: float = 60) -> None:
        """Blocking call: send an informational message and wait for OK."""
        text = f"ℹ️ *INFO*\n\n{message}"
        self.notif.send_raw(text)
        if self.loop and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._send_ack_keyboard(text), self.loop
            )
        self.state.confirmation_event.clear()
        self.state.expecting_confirmation = True
        self.state.confirmation_event.wait(timeout=timeout)
        self.state.expecting_confirmation = False

    async def _send_ack_keyboard(self, text: str) -> None:
        if not self.application or not self.allowed_chat_id:
            return
        try:
            await self.application.bot.send_message(
                chat_id=self.allowed_chat_id,
                text=text,
                parse_mode=_MD,
                reply_markup=Keyboards.ack(),
            )
        except Exception as exc:
            print(f"[Telegram] Ack send error: {exc}")

    def request_input(self, prompt: str, timeout: float = 60) -> Optional[str]:  # noqa: ARG002
        """
        Send an input prompt via Telegram and return immediately.

        Both call sites in main_gui.py (select_window_dialog, simple_input) run on
        the Tkinter main thread and follow this pattern:
            request_input(msg)          # send prompt — do NOT block GUI thread
            while dialog.winfo_exists():
                if input_event.is_set():  # poll for reply
                    ...

        Blocking here would freeze the Tkinter event loop, preventing the dialog
        from opening.  The while-loop in main_gui.py handles detection instead.
        """
        self.state.input_value = None
        self.state.input_event.clear()
        self.state.expecting_input = True
        text = f"❓ *INPUT REQUIRED*\n\n{prompt}\n\n_Reply to this message._"
        self.notif.send_raw(text)
        # Return immediately — caller polls input_event.is_set() in its own loop.
        return None

    # ── Internal streak helpers ───────────────────────────────────────────────

    def _win_streak(self) -> int:
        count = 0
        for r in reversed(list(self.state.recent_results)):
            if r:
                count += 1
            else:
                break
        return count

    def _loss_streak(self) -> int:
        count = 0
        for r in reversed(list(self.state.recent_results)):
            if not r:
                count += 1
            else:
                break
        return count

    # ── Backward-compat property aliases ─────────────────────────────────────

    @property
    def expecting_input(self) -> bool:
        return self.state.expecting_input

    @expecting_input.setter
    def expecting_input(self, val: bool) -> None:
        self.state.expecting_input = val

    @property
    def input_value(self):
        return self.state.input_value

    @input_value.setter
    def input_value(self, val) -> None:
        self.state.input_value = val

    @property
    def input_event(self) -> threading.Event:
        return self.state.input_event

    @property
    def expecting_confirmation(self) -> bool:
        return self.state.expecting_confirmation

    @expecting_confirmation.setter
    def expecting_confirmation(self, val: bool) -> None:
        self.state.expecting_confirmation = val

    @property
    def confirmation_value(self):
        return self.state.confirmation_value

    @confirmation_value.setter
    def confirmation_value(self, val) -> None:
        self.state.confirmation_value = val

    @property
    def confirmation_event(self) -> threading.Event:
        return self.state.confirmation_event

    @property
    def dashboard_message_id(self) -> Optional[int]:
        return self.state.dashboard_msg_id

    @dashboard_message_id.setter
    def dashboard_message_id(self, val: Optional[int]) -> None:
        self.state.dashboard_msg_id = val
