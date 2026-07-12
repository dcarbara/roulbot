from typing import List, Dict, Any
import copy
import logging

logger = logging.getLogger(__name__)

class DynamicProgressionStrategy:
    """
    A flexible progression system that determines the next bet amount based on a set of user-defined rules.
    This class does NOT handle bet labels/locations; it only manages the bet amount progression.
    It is designed to be used alongside any strategy that provides bet locations (labels).
    """
    def __init__(self, base_bet: float, rules: List[Dict[str, Any]], session_start_balance: float = 0.0,
                 custom_sequence: List[float] = None, dalembert_step: float = 1.0):
        self.base_bet = base_bet
        self.rules = rules
        logger.info(f"[DynamicProgression] Initialized with rules: {self.rules}")
        self.current_bet = base_bet
        self.session_start_balance = session_start_balance
        self.session_high = 0.0
        self.total_profit = 0.0
        self.martingale_level = 0
        self.global_custom_sequence = custom_sequence or [1]
        if str(dalembert_step) == "base_bet":
            self.dalembert_step = 1.0
        elif str(dalembert_step).startswith("base_bet_") and str(dalembert_step).endswith("x"):
            try:
                self.dalembert_step = float(str(dalembert_step).split("_")[2][:-1])
            except (ValueError, IndexError):
                self.dalembert_step = 1.0
        else:
            try:
                self.dalembert_step = float(dalembert_step)
            except (ValueError, TypeError):
                self.dalembert_step = 1.0
        self.custom_sequence = self.global_custom_sequence
        self.custom_index = 0
        self.last_action = None

    def get_next_bet(self):
        """Return the next bet amount based on the current progression state."""
        return self.current_bet

    def get_current_bet(self):
        """Alias for compatibility with other progression systems."""
        return self.current_bet

    def record_result(self, win: bool, current_profit: float = None):
        """
        Update the progression state based on the result of the last bet.
        - win: True if the last bet was a win, False if a loss.
        - current_profit: The player's current profit from payout calculations (for session-based rules).
        """
        logger.info(f"[DynamicProgression] record_result called: win={win}, current_profit={current_profit}, "
                    f"session_high={self.session_high}, current_bet={self.current_bet}, martingale_lvl={self.martingale_level}")
        if current_profit is None:
            raise ValueError("DynamicProgressionStrategy requires current_profit for record_result()")

        self.total_profit = current_profit

        # Determine if we're at/above the PREVIOUS session high BEFORE updating it.
        # This ensures "profit_at_or_above_session_high" means recovering to a prior
        # peak after a drawdown, NOT simply setting a new high on every win.
        is_new_high = self.total_profit > self.session_high
        logger.info(f"[DynamicProgression] is_new_high={is_new_high} (profit={self.total_profit} vs high={self.session_high})")

        executed_high_rule = False
        if win:
            # Evaluate win rules BEFORE updating session_high so conditions
            # compare against the previous peak (drawdown recovery logic).
            if not is_new_high:
                self._apply_rule('win')
            else:
                # New session high on a win — check for explicit 'session_high' rules first
                executed_high_rule = self._apply_rule('session_high')
                if not executed_high_rule:
                    # No session_high rule matched; apply 'win' rules against the OLD high
                    self._apply_rule('win')
                # NOW update the high watermark
                self.session_high = self.total_profit
        else:
            self._apply_rule('loss')

        # Also update session_high for non-win cases (shouldn't happen, but be safe)
        if self.total_profit > self.session_high:
            self.session_high = self.total_profit

        logger.info(f"[DynamicProgression] AFTER: current_bet={self.current_bet}, session_high={self.session_high}, "
                    f"last_action={self.last_action}")

    def _apply_rule(self, event: str) -> bool:
        for rule in self.rules:
            if rule.get('on') == event:
                # Check for condition
                condition = rule.get('condition')
                if condition == 'profit_below_session_high' and not (self.total_profit < self.session_high):
                    continue
                if condition == 'profit_at_or_above_session_high' and not (self.total_profit >= self.session_high):
                    continue
                action = rule.get('action')
                if action == 'martingale':
                    self.current_bet *= 2
                    self.martingale_level += 1
                elif action == 'flat':
                    self.current_bet = self.base_bet
                    self.martingale_level = 0
                elif action == 'reset_to_base':
                    self.current_bet = self.base_bet
                    self.martingale_level = 0
                elif action == 'custom_sequence':
                    if 'sequence' in rule:
                        self.custom_sequence = rule['sequence']
                    else:
                        self.custom_sequence = self.global_custom_sequence
                    
                    if self.custom_index >= len(self.custom_sequence):
                        self.custom_index = len(self.custom_sequence) - 1
                    self.current_bet = self.base_bet * self.custom_sequence[self.custom_index]
                    self.custom_index += 1
                elif action in ['dalembert', 'step_up', 'step_down']:
                    # Parse step_multiplier from rule config
                    raw_step = rule.get('step', self.dalembert_step)
                    if raw_step == "base_bet":
                        step_value = self.base_bet * 1.0
                    elif isinstance(raw_step, str) and raw_step.startswith("base_bet_") and raw_step.endswith("x"):
                        try:
                            step_multiplier = float(raw_step.split("_")[2][:-1])
                            step_value = self.base_bet * step_multiplier
                        except (ValueError, IndexError):
                            step_value = self.base_bet * 1.0
                    else:
                        try:
                            # If it's a raw number, it's a custom unit, NO base_bet multiplication is needed!
                            step_value = float(raw_step)
                        except (ValueError, TypeError):
                            # Fallback to dalembert_step multiplier if parsing completely fails?
                            # Wait, dalembert_step from StrategyEngine is passed down as a direct explicit unit in UI.
                            step_value = self.dalembert_step
                    
                    if action == 'step_up':
                        self.current_bet += step_value
                    elif action == 'step_down':
                        self.current_bet = max(self.base_bet, self.current_bet - step_value)
                    elif action == 'dalembert':
                        if event == 'loss':
                            self.current_bet += step_value
                        elif event == 'win':
                            self.current_bet = max(self.base_bet, self.current_bet - step_value)
                elif action == 'keep':
                    pass  # Do nothing, keep current bet
                # Add more actions as needed
                self.last_action = action
                logger.info(f"[DynamicProgression] Event: {event}, Rule: {rule}, New Bet: {self.current_bet}")
                return True
        return False

    def reset(self):
        """Reset the progression state to the initial values."""
        self.current_bet = self.base_bet
        self.martingale_level = 0
        self.custom_index = 0
        self.session_high = 0.0
        self.total_profit = 0.0
        self.last_action = None 