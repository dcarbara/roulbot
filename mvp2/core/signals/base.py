"""Signal base classes + canonical group definitions.

GROUPS is the single source of truth for partitioning roulette numbers into
betting categories. The label names ("red", "1st12", "col1", etc.) match
ROULETTE_NUMBER_MAPPINGS in strategy_engine.py exactly so payout calc just works.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

REDS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})


def _color_of(n: int) -> Optional[str]:
    if n == 0:
        return None
    return "red" if n in REDS else "black"


def _parity_of(n: int) -> Optional[str]:
    if n == 0:
        return None
    return "even" if n % 2 == 0 else "odd"


def _hilo_of(n: int) -> Optional[str]:
    if n == 0:
        return None
    return "1to18" if 1 <= n <= 18 else "19to36"


def _dozen_of(n: int) -> Optional[str]:
    if n == 0:
        return None
    if n <= 12:
        return "1st12"
    if n <= 24:
        return "2nd12"
    return "3rd12"


def _column_of(n: int) -> Optional[str]:
    if n == 0:
        return None
    rem = n % 3
    if rem == 1:
        return "col1"
    if rem == 2:
        return "col2"
    return "col3"


GROUPS: Dict[str, Dict[str, Any]] = {
    "color":  {"members": ["red", "black"],            "fn": _color_of},
    "parity": {"members": ["even", "odd"],             "fn": _parity_of},
    "hilo":   {"members": ["1to18", "19to36"],         "fn": _hilo_of},
    "dozen":  {"members": ["1st12", "2nd12", "3rd12"], "fn": _dozen_of},
    "column": {"members": ["col1", "col2", "col3"],    "fn": _column_of},
}


def group_member_of(group: str, n: int) -> Optional[str]:
    """Return the group member label for number n, or None if n is group-neutral (e.g. 0)."""
    return GROUPS[group]["fn"](n)


def group_members(group: str) -> List[str]:
    return list(GROUPS[group]["members"])


def opposite_member(group: str, member: str) -> Optional[str]:
    """For 2-member groups, returns the other member. For 3-member groups, returns None
    (no single 'opposite'). Used by AlternationSignal to predict the next flip."""
    members = GROUPS[group]["members"]
    if len(members) != 2:
        return None
    return members[1] if member == members[0] else members[0]


@dataclass(frozen=True)
class SignalReading:
    """Result of a signal evaluating current history.

    Attributes:
        name:       Signal identity (e.g. "color_streak", "dozen_regime").
        state:      Primary classification. Convention varies by signal type but
                    common values are 'active'/'inactive', 'TRENDING'/'CHOPPY'/'NEUTRAL'.
                    A reading is considered "matching" when state != 'inactive' and
                    state != 'NEUTRAL' — but consumers should be explicit about the
                    states they care about rather than relying on this convention.
        member:     The group member relevant to the prediction this signal makes.
                    Convention: this is "the member the signal predicts will be the
                    next outcome assuming the pattern continues." For streak/dominance
                    this is the dominant member. For alternation this is the OPPOSITE
                    of the last observed member (since alternation predicts a flip).
                    None when the signal can't or shouldn't predict.
        confidence: 0.0–1.0 score of how strongly the signal fired. Use cases:
                    bet sizing, ensemble voting, ranking competing rule matches.
        metadata:   Free-form bag for signal-specific details (streak_length,
                    dominance_ratio, etc.). Consumers may inspect but shouldn't depend
                    on specific keys without checking.
    """
    name: str
    state: str
    member: Optional[str] = None
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        """True if the signal is in a 'firing' state (not inactive/neutral)."""
        return self.state.lower() not in ("inactive", "neutral")


class Signal(ABC):
    """Abstract base for all detectors.

    A Signal is configured at construction (group, window, thresholds, etc.) and
    then called repeatedly with the current history. It returns a SignalReading
    describing the current state of the pattern it detects.

    Implementations should be PURE — no mutable state across update() calls.
    The history is the source of truth; the signal just classifies.
    """

    name: str = "signal"

    @abstractmethod
    def update(self, history: List[int]) -> SignalReading:
        """Read current state from history. Pure function — no side effects."""

    def describe(self) -> str:
        """Human-readable description of this signal's config. Override for clarity."""
        return self.name
