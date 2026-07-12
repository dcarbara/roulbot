import time
import logging

logger = logging.getLogger(__name__)

class StopReason:
    CONTINUE = "CONTINUE"
    USER_STOP = "USER_STOP"
    GLOBAL_PROFIT = "GLOBAL_PROFIT"
    GLOBAL_LOSS = "GLOBAL_LOSS"
    STOP_LOSS = "STOP_LOSS"
    PROFIT_TARGET = "PROFIT_TARGET"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_LIMIT = "TIME_LIMIT"
    
class ExtensionState:
    NONE = "NONE"
    WAITING_FOR_WIN = "WAITING_FOR_WIN"
    CHASING_PEAK = "CHASING_PEAK"

class SessionManager:
    """
    Manages session lifecycle, stop conditions, and extension policies.
    Decouples risk management logic from the GUI.
    """
    def __init__(self, config):
        self.config = config
        
        # Session State
        self.start_time = time.time()
        self.session_pnl = 0.0
        self.peak_pnl = 0.0
        self.total_rounds = 0
        self.wins = 0
        self.losses = 0
        
        # Extension State
        self.extension_mode = ExtensionState.NONE
        self.extension_start_round = 0
        self.max_extension_rounds = config.get("max_extension_rounds", 20)
        self.extension_give_up_amount = config.get("extension_give_up_amount", 50.0)
        
        # Limits (parsed from config, with defensive type coercion)
        self.stop_loss = abs(float(config.get("max_loss", 100)))
        self.profit_target = float(config.get("profit_target", 0))
        self.trailing_stop = float(config.get("trailing_stop_amount", 0))
        self.session_duration = int(float(config.get("session_duration_minutes", 60))) * 60
        self.max_win_streak = int(float(config.get("max_session_wins_streak", 0)))
        self.max_loss_streak = int(float(config.get("max_session_losses_streak", 0)))

        # Global limits (passed in context usually, but stored here if needed)
        self.global_pnl = 0.0 # Needs to be updated externally
        self.current_streak = 0 # +ve for wins, -ve for losses

        # Simulated elapsed seconds — set by the backtest runner so the
        # TIME_LIMIT soft stop fires deterministically even though the whole
        # campaign runs in milliseconds of wall-clock time. Live bot leaves
        # this at None and the manager falls back to `time.time() - start_time`.
        # Without this, session_ext_at_high never activated in backtest
        # because TIME_LIMIT was never reached → soft_stop stayed None →
        # the extension branch in check_stop_conditions was skipped.
        self.simulated_elapsed_seconds: float | None = None

    def update_state(self, pnl, wins, losses, global_pnl=0.0, streak=0):
        self.session_pnl = pnl
        self.global_pnl = global_pnl
        self.wins = wins
        self.losses = losses
        self.total_rounds = wins + losses
        self.current_streak = streak
        
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl

    def check_stop_conditions(self, bot_running, last_result):
        """
        Determines if the session should stop.
        Returns: (should_stop: bool, reason: str, message: str)

        Ordering matters: hard safety stops (stop loss, trailing stop, streak caps,
        time limit) are evaluated before extension logic so they always win.
        Extension may only defer the soft profit-target stop.
        """
        # 1. User Stop
        if not bot_running:
            return True, StopReason.USER_STOP, "User stopped bot"

        # 2. Hard Safety Stops — honored even when an extension is active.
        # These are risk controls; extension must never bypass them.
        if self.stop_loss > 0 and self.session_pnl <= -self.stop_loss:
            return True, StopReason.STOP_LOSS, f"Session Stop Loss hit: ${self.session_pnl:.2f}"

        if self.config.get("enable_trailing_stop", False) and self.trailing_stop > 0:
            if (self.peak_pnl - self.session_pnl) >= self.trailing_stop:
                return True, StopReason.TRAILING_STOP, f"Trailing Stop hit: Drop of ${self.peak_pnl - self.session_pnl:.2f}"

        if self.max_win_streak > 0 and self.current_streak >= self.max_win_streak:
             return True, "STREAK_LIMIT", f"Win Streak Limit reached ({self.current_streak})"

        if self.max_loss_streak > 0 and self.current_streak <= -self.max_loss_streak:
             return True, "STREAK_LIMIT", f"Loss Streak Limit reached ({abs(self.current_streak)})"

        # 3. Soft Stops — evaluate first to know IF anything would stop, then
        # let extension defer it. Previously this was wired the other way
        # ("extension always returns EXTENDING when applicable, regardless of
        # whether anything would stop") — which preemptively swallowed every
        # intermediate round and looked like stops were being ignored.
        soft_stop: tuple[str, str] | None = None
        if self.config.get("enable_profit_target", False) and self.profit_target > 0:
            if self.session_pnl >= self.profit_target:
                soft_stop = (StopReason.PROFIT_TARGET,
                             f"Profit Target reached: ${self.session_pnl:.2f}")
        elapsed_seconds = (self.simulated_elapsed_seconds
                           if self.simulated_elapsed_seconds is not None
                           else time.time() - self.start_time)
        if soft_stop is None and elapsed_seconds >= self.session_duration:
            soft_stop = (StopReason.TIME_LIMIT, "Session time limit reached")

        # 4. Extension — only relevant when a soft stop WOULD fire. Otherwise
        # the bot just keeps running normally. This is what "extend" means
        # operationally: defer the stop, not preempt every other check.
        if soft_stop is not None:
            # Extend until Win — skip on initial state where no round resolved
            if self.config.get("session_ext_after_win", False) and last_result is not None \
                    and last_result != 'win':
                if self._check_extension_safety():
                    return False, "EXTENDING", "Extending session until win..."
                else:
                    return True, "EXTENSION_LIMIT", "Max extension rounds reached without win."
            # Extend until High — defers the soft stop while ANY of:
            #   a) PnL is below peak (the classic "chase the high" case), OR
            #   b) Fewer than min_session_rounds_before_stop real bets have
            #      been played (default 10). Without (b), a session that only
            #      managed 1 win in the time limit would stop immediately at
            #      the next check — pnl == peak (just won) means (a) is false
            #      too. The user enabled "extend at high" expecting the bot
            #      to keep playing for a chance at a higher high; this gives
            #      that semantic a minimum number of attempts.
            #
            # Both arms are bounded by 2× session_duration so the bot can't
            # loop forever if conditional triggers never qualify or the
            # session genuinely can't reach the minimum.
            if self.config.get("session_ext_at_high", False):
                if self.session_pnl < self.peak_pnl - 0.01:
                    if self._check_extension_safety():
                        return False, "EXTENDING", f"Chasing peak (${self.peak_pnl:.2f})..."
                    else:
                        return True, "EXTENSION_LIMIT", "Max extension rounds or give-up limit reached."
                min_rounds = int(self.config.get('min_session_rounds_before_stop', 10))
                if min_rounds > 0 and self.total_rounds < min_rounds:
                    elapsed = (self.simulated_elapsed_seconds
                               if self.simulated_elapsed_seconds is not None
                               else time.time() - self.start_time)
                    if elapsed < self.session_duration * 2:
                        if self.total_rounds == 0:
                            return False, "EXTENDING", (
                                f"Waiting for first armed trigger "
                                f"(elapsed {elapsed/60:.1f}min, cap {self.session_duration*2/60:.0f}min)"
                            )
                        return False, "EXTENDING", (
                            f"Played {self.total_rounds}/{min_rounds} rounds — "
                            f"extending for more attempts at session high"
                        )
                    return True, "EXTENSION_LIMIT", (
                        f"Played {self.total_rounds} rounds in {self.session_duration*2/60:.0f}min — "
                        f"giving up extension."
                    )
            # No extension applicable → soft stop fires
            return True, soft_stop[0], soft_stop[1]

        return False, StopReason.CONTINUE, ""

    def _check_extension_safety(self):
        """
        Safety caps for extensions.
        Returns True if safe to continue, False if we should abort extension.
        """
        # Initialize extension tracking if not already
        if self.extension_mode == ExtensionState.NONE:
            self.extension_mode = ExtensionState.WAITING_FOR_WIN # Simplified state for now
            self.extension_start_round = self.total_rounds
            
        current_extension_rounds = self.total_rounds - self.extension_start_round
        
        # 1. Round Limit
        if current_extension_rounds >= self.max_extension_rounds:
            logger.warning(f"🛑 Extension Safety: Max rounds ({self.max_extension_rounds}) exceeded.")
            return False
            
        # 2. Give Up Logic (for Peak Chasing)
        if self.config.get("session_ext_at_high", False):
             # If we dropped significantly from peak during extension
             # Note: logic here could be complex. For now, let's use a simple absolute drop check.
             # If [Current PnL] < [PnL when extension started] - [Give Up Amount]
             # But "PnL when extension started" is tricky if we just switched modes.
             # Alternative: If Peak - Current > GiveUpAmount (Similar to Trailing Stop but specific context)
             if (self.peak_pnl - self.session_pnl) > self.extension_give_up_amount:
                 logger.warning(f"🛑 Extension Safety: Give Up limit reached (Drop > ${self.extension_give_up_amount}).")
                 return False

        return True
