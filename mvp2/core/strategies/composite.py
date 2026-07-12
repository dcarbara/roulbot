"""CompositeStrategy — multi-condition, multi-action strategy mode.

Supports:
- Compound conditions: `when` is a list of conditions, ALL must match (AND)
- Multiple action types: bet labels / follow group / contra group / target a
  specific label / delegate to another custom strategy (= regime router)
- First-match-wins rule evaluation
- Warm sub-strategies: every spin fans out to all delegated sub-strategies so
  they're history-current when activated

The strategy data shape is documented in core/decision/rules.py.
"""
import inspect
import logging
from typing import Any, Dict, List, Optional

from core.decision.rules import (
    BetGroupAction,
    BetLabelsAction,
    DelegateAction,
    Rule,
    evaluate_rules,
    extract_delegate_names,
    labels_for_action,
    parse_rule,
)

logger = logging.getLogger(__name__)


class CompositeStrategy:
    """Multi-condition rule-driven strategy with optional sub-strategy delegation.

    Args:
        base_bet:        Base bet for non-delegating actions (used by progression).
        rules:           List of rule specs (composite or flat shape).
        history_size:    Buffer of past winning numbers retained.
        sub_strategies:  dict[str, Strategy] of pre-instantiated sub-strategies
                         that delegate actions can reference. Names are
                         case-sensitive matches against `DelegateAction.strategy_name`.

    Raises:
        ValueError: if any rule references a sub-strategy not present in
                    `sub_strategies`, or if any rule fails to parse.
    """

    def __init__(self, base_bet: float, rules: List[Dict[str, Any]],
                 history_size: int = 50,
                 sub_strategies: Optional[Dict[str, Any]] = None):
        self.base_bet = base_bet
        self._sub_strategies: Dict[str, Any] = dict(sub_strategies or {})

        # Parse all rules eagerly — fail fast on bad config.
        if not isinstance(rules, list) or not rules:
            raise ValueError("CompositeStrategy: 'rules' must be a non-empty list")
        self._rules: List[Rule] = []
        for i, spec in enumerate(rules):
            try:
                self._rules.append(parse_rule(spec))
            except ValueError as e:
                raise ValueError(f"CompositeStrategy rule {i}: {e}") from e

        # Verify all delegated strategy names resolve.
        missing = [n for n in extract_delegate_names(self._rules)
                   if n not in self._sub_strategies]
        if missing:
            raise ValueError(
                f"CompositeStrategy: delegate actions reference unknown sub-strategies: "
                f"{missing}. Provide them in sub_strategies={{name: strategy_obj}}."
            )

        # History buffer sizing — at minimum the deepest signal window we'll see.
        self._history: List[int] = []
        deepest_window = self._infer_deepest_window()
        self._history_size = max(history_size, deepest_window, 1)

        # Cache of last-evaluation result for get_bet_amounts() following get_labels()
        # (so we don't double-evaluate rules per spin).
        self._last_fired_rule: Optional[Rule] = None
        self._last_primary_reading = None

        # Rule-persistence lock state. When a fired rule's persist_until is set,
        # we cache its labels and keep emitting them until the release condition
        # is met by an incoming bet result. Progression continues to escalate on
        # the same bet (martingale-style) until released.
        self._locked_rule: Optional[Rule] = None
        self._locked_labels: Optional[List[str]] = None
        self._locked_spins_elapsed: int = 0
        self._locked_consec_losses: int = 0
        # Profit high-water mark for persist_until="session_high". Tracks the
        # peak current_profit seen so a locked pattern releases only once profit
        # recovers to that prior peak (full drawdown recovery).
        self._session_high_profit: float = 0.0

    # ----- public API (matches the inner-strategy contract used by StrategyEngine) -----

    def get_next_bet(self) -> float:
        return self.base_bet

    def get_current_bet(self) -> float:
        return self.base_bet

    def reset(self) -> None:
        self._history.clear()
        self._last_fired_rule = None
        self._last_primary_reading = None
        self._session_high_profit = 0.0
        self._release_lock()
        for sub in self._sub_strategies.values():
            if hasattr(sub, "reset"):
                try:
                    sub.reset()
                except Exception as e:  # never let a sub's reset crash composite
                    logger.warning("[Composite] sub.reset() failed: %s", e)

    def _release_lock(self) -> None:
        self._locked_rule = None
        self._locked_labels = None
        self._locked_spins_elapsed = 0
        self._locked_consec_losses = 0

    def record_result(self, win: bool, last_number: int = None,
                      current_profit: float = None) -> None:
        """Update history, fan out to warm sub-strategies, and apply lock-release
        logic for persistent rules.

        current_profit (session P&L) is required only by persist_until=
        'session_high'; other persist modes ignore it.
        """
        if last_number is not None and 0 <= last_number <= 36:
            self._history.append(last_number)
            if len(self._history) > self._history_size:
                self._history = self._history[-self._history_size:]

        # Fan out to ALL sub-strategies regardless of which (if any) was active.
        # Sub-strategies should be history-driven label-pickers; the win flag is
        # informational and uses the composite-level outcome.
        for name, sub in self._sub_strategies.items():
            if not hasattr(sub, "record_result"):
                continue
            try:
                sig = inspect.signature(sub.record_result)
                if "last_number" in sig.parameters and last_number is not None:
                    sub.record_result(win, last_number=last_number)
                else:
                    sub.record_result(win)
            except Exception as e:
                logger.warning("[Composite] sub %r record_result failed: %s", name, e)

        # "Recovered to the prior session high?" — computed against the PREVIOUS
        # high-water mark, BEFORE we update it below, so persist_until=
        # 'session_high' means climbing back to a prior peak (full drawdown
        # recovery), not merely setting a fresh high on the first win.
        recovered_to_high = (
            current_profit is not None and current_profit >= self._session_high_profit
        )

        # Lock-release logic for persistent rules
        if self._locked_rule is not None:
            persist = self._locked_rule.persist_until
            release = False
            self._locked_spins_elapsed += 1
            if not win:
                self._locked_consec_losses += 1
            else:
                self._locked_consec_losses = 0

            if persist == "win" and win:
                release = True
            elif persist == "loss" and not win:
                release = True
            elif persist == "session_high":
                # Hold the pattern through wins AND losses until profit recovers
                # to the prior peak. If current_profit wasn't supplied we can't
                # evaluate it, so we keep holding (caller must thread profit in).
                release = recovered_to_high
            elif isinstance(persist, dict):
                if (
                    "max_losses" in persist
                    and self._locked_consec_losses >= int(persist["max_losses"])
                ):
                    release = True
                elif (
                    "max_spins" in persist
                    and self._locked_spins_elapsed >= int(persist["max_spins"])
                ):
                    release = True

            if release:
                logger.debug(
                    "[Composite] releasing lock (persist=%r, win=%s, spins=%d, losses=%d, profit=%s, high=%s)",
                    persist, win, self._locked_spins_elapsed, self._locked_consec_losses,
                    current_profit, self._session_high_profit,
                )
                self._release_lock()

        # Update the high-water mark AFTER the release check.
        if current_profit is not None and current_profit > self._session_high_profit:
            self._session_high_profit = current_profit

    def get_labels(self) -> List[str]:
        # If a persistent rule is currently locked, emit its cached labels
        # without re-evaluating. Progression keeps escalating on the same bet.
        if self._locked_rule is not None and self._locked_labels is not None:
            self._last_fired_rule = self._locked_rule
            logger.debug(
                "[Composite] lock active; emitting cached labels: %s",
                self._locked_labels,
            )
            return list(self._locked_labels)

        rule, readings = evaluate_rules(self._rules, self._history)
        self._last_fired_rule = rule
        self._last_primary_reading = readings[0] if readings else None
        if rule is None:
            logger.debug("[Composite] no rule matched; sitting out")
            return []

        action = rule.then
        if isinstance(action, DelegateAction):
            sub = self._sub_strategies[action.strategy_name]
            if hasattr(sub, "get_labels"):
                labels = list(sub.get_labels())
            else:
                labels = []
            logger.debug("[Composite] delegate -> %r -> %s", action.strategy_name, labels)
        else:
            # Pass history so history-aware actions (follow_last/coldest/
            # hottest/combo) can resolve against recent spins.
            labels = labels_for_action(action, self._last_primary_reading,
                                       history=self._history)
            logger.debug("[Composite] action=%s reading=%s labels=%s",
                         action, self._last_primary_reading, labels)

        # Lock this rule's labels if it requested persistence and produced a bet.
        if rule.persist_until is not None and labels:
            self._locked_rule = rule
            self._locked_labels = list(labels)
            self._locked_spins_elapsed = 0
            self._locked_consec_losses = 0
            logger.debug(
                "[Composite] locking rule (persist_until=%r) labels=%s",
                rule.persist_until, labels,
            )

        return labels

    def get_bet_amounts(self, current_progression_bet: float = None
                        ) -> Dict[str, float]:
        """Return {label: amount} for the currently firing rule.

        For Delegate actions, defers to the sub-strategy's get_bet_amounts so
        the sub can apply its own per-label weighting (e.g., bet_units).
        """
        # Re-evaluate to get the current state — get_labels() may not have been
        # called recently, and rule evaluation is cheap.
        labels = self.get_labels()
        if not labels:
            return {}

        # If we just delegated, ask the sub for amounts (it may have bet_units).
        rule = self._last_fired_rule
        if rule is not None and isinstance(rule.then, DelegateAction):
            sub = self._sub_strategies[rule.then.strategy_name]
            if hasattr(sub, "get_bet_amounts"):
                return sub.get_bet_amounts(current_progression_bet)
            # Fall back: equal distribution
            amount = current_progression_bet if current_progression_bet is not None else self.base_bet
            return {label: amount for label in labels}

        amount = current_progression_bet if current_progression_bet is not None else self.base_bet
        return {label: amount for label in labels}

    def get_total_bet_amount(self, current_progression_bet: float = None) -> float:
        return sum(self.get_bet_amounts(current_progression_bet).values())

    def describe(self) -> str:
        if not self._rules:
            return "Composite (no rules)"
        parts = []
        for i, r in enumerate(self._rules, 1):
            cond_descs = [c.signal.describe() for c in r.when]
            cond_str = " AND ".join(cond_descs)
            then = r.then
            if isinstance(then, DelegateAction):
                action_str = f"delegate -> {then.strategy_name}"
            elif isinstance(then, BetLabelsAction):
                action_str = f"bet labels {then.labels}"
            elif isinstance(then, BetGroupAction):
                tail = f" {then.target}" if then.mode == "target" else ""
                action_str = f"{then.mode} {then.group}{tail}"
            else:
                action_str = "?"
            parts.append(f"R{i}: IF {cond_str} -> {action_str}")
        return " ; ".join(parts)

    # ----- internals -----

    def _infer_deepest_window(self) -> int:
        """Largest window any rule needs — from signal windows/min_length AND
        from history-aware action lookbacks (coldest/hottest), so the history
        buffer is deep enough to rank coldness correctly."""
        deepest = 1
        for rule in self._rules:
            for cond in rule.when:
                signal = cond.signal
                # StreakSignal exposes min_length; window-based signals expose window.
                for attr in ("min_length", "window"):
                    if hasattr(signal, attr):
                        try:
                            v = int(getattr(signal, attr))
                            if v > deepest:
                                deepest = v
                        except (TypeError, ValueError):
                            pass
            deepest = max(deepest, self._action_lookback(rule.then))
        return deepest

    def _action_lookback(self, action) -> int:
        """Deepest lookback an action needs (recursing into combos)."""
        depth = 1
        lookback = getattr(action, "lookback", None)
        if isinstance(lookback, int):
            depth = max(depth, lookback)
        for sub in getattr(action, "actions", []) or []:
            depth = max(depth, self._action_lookback(sub))
        return depth
