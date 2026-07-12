"""PatternFollowerStrategy — thin wrapper over CompositeStrategy.

Kept as its own class for backwards compatibility with stored presets, the GUI
editor, and the engine dispatcher. The 'flat' rule shape this class accepts —
e.g. {"detect": "streak", "group": "color", "min_length": 3, "action": "follow"}
— is parsed by core/decision/rules.py:parse_rule() into the same internal Rule
representation used by composite mode, so semantics are identical.

Differences from CompositeStrategy:
- No sub-strategies allowed (delegate actions raise at construction).
- Rule shape is flat by convention; composite shape (`{when, then}`) also works
  but is more naturally written in composite mode presets.

Re-exports GROUPS, REDS, group_member_of, opposite_member from core/signals/base
so existing imports (notably the GUI editor) keep working unchanged.
"""
from typing import Any, Dict, List

from core.signals.base import (  # re-export for backwards compat
    GROUPS,
    REDS,
    group_member_of,
    opposite_member,
)
from core.strategies.composite import CompositeStrategy

# Set of detect names allowed today by the rule editor's UI. The strategy itself
# accepts everything the registry knows about; this constant is exposed for any
# UI/tooling that wants to sanity-check rule presets before letting users edit.
EDITOR_SUPPORTED_DETECTORS = frozenset({"streak"})

VALID_DETECTORS = frozenset({"streak", "dominance", "alternation", "regime"})
VALID_ACTIONS = frozenset({"follow", "contra", "target", "labels",
                           "follow_last", "coldest", "hottest", "combo"})


class PatternFollowerStrategy:
    """Single-strategy rule-based label picker. See module docstring for shape."""

    def __init__(self, base_bet: float, rules: List[Dict[str, Any]],
                 history_size: int = 50):
        self.base_bet = base_bet
        self.rules = list(rules or [])
        # CompositeStrategy parses, validates, and rejects delegate actions
        # (since we pass sub_strategies={}).
        try:
            self._composite = CompositeStrategy(
                base_bet=base_bet,
                rules=self.rules,
                history_size=history_size,
                sub_strategies={},
            )
        except ValueError as e:
            # Rewrap so existing callers that catch ValueError keep working.
            raise ValueError(str(e)) from e

    # ----- forward the standard inner-strategy contract -----

    def get_next_bet(self):
        return self._composite.get_next_bet()

    def get_current_bet(self):
        return self._composite.get_current_bet()

    def reset(self) -> None:
        self._composite.reset()

    def record_result(self, win: bool, last_number: int = None,
                      current_profit: float = None) -> None:
        self._composite.record_result(win, last_number=last_number,
                                      current_profit=current_profit)

    def get_labels(self) -> List[str]:
        return self._composite.get_labels()

    def get_bet_amounts(self, current_progression_bet: float = None) -> Dict[str, float]:
        return self._composite.get_bet_amounts(current_progression_bet)

    def get_total_bet_amount(self, current_progression_bet: float = None) -> float:
        return self._composite.get_total_bet_amount(current_progression_bet)

    def describe(self) -> str:
        return self._composite.describe()


__all__ = [
    "PatternFollowerStrategy",
    "GROUPS", "REDS",
    "group_member_of", "opposite_member",
    "VALID_DETECTORS", "VALID_ACTIONS", "EDITOR_SUPPORTED_DETECTORS",
]
