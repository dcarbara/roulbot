"""Signal factory: turns a `{"detect": "...", ...}` spec into a configured Signal."""
from typing import Any, Callable, Dict

from core.signals.base import Signal
from core.signals.builtins import (
    AlternationSignal,
    AlwaysSignal,
    DominanceSignal,
    LastNumberInSignal,
    RegimeSignal,
    StreakSignal,
)

# Each entry maps the 'detect' name to a builder that takes the spec dict
# (with 'detect' already removed) and returns a Signal instance.
SIGNAL_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Signal]] = {
    "streak": lambda s: StreakSignal(
        group=s["group"],
        min_length=int(s.get("min_length", 1)),
    ),
    "dominance": lambda s: DominanceSignal(
        group=s["group"],
        window=int(s.get("window", 20)),
        threshold=float(s.get("threshold", 0.6)),
    ),
    "alternation": lambda s: AlternationSignal(
        group=s["group"],
        window=int(s.get("window", 10)),
        threshold=float(s.get("threshold", 0.7)),
    ),
    "regime": lambda s: RegimeSignal(
        group=s["group"],
        window=int(s.get("window", 20)),
        trend_threshold=float(s.get("trend_threshold", 0.6)),
        chop_threshold=float(s.get("chop_threshold", 0.7)),
    ),
    "last_number_in": lambda s: LastNumberInSignal(
        numbers=s["numbers"],
        offset=int(s.get("offset", 1)),
    ),
    "always": lambda s: AlwaysSignal(),
}

# Detectors that operate on raw spin numbers and don't use the group concept.
# Excluded from the global 'group' presence check in make_signal().
_NO_GROUP_DETECTORS = {"last_number_in", "always"}


def make_signal(spec: Dict[str, Any]) -> Signal:
    """Build a Signal from a spec dict.

    Spec must contain 'detect' (the signal type) plus signal-specific config.
    Example: {"detect": "streak", "group": "color", "min_length": 3}

    Raises ValueError on unknown detect type, missing required fields, or
    out-of-range parameters (the latter from the Signal constructor itself).
    """
    if not isinstance(spec, dict):
        raise ValueError(f"signal spec must be a dict, got {type(spec).__name__}")
    detect = spec.get("detect")
    if detect not in SIGNAL_REGISTRY:
        raise ValueError(
            f"unknown detect {detect!r}, expected one of {sorted(SIGNAL_REGISTRY)}"
        )
    if detect not in _NO_GROUP_DETECTORS and "group" not in spec:
        raise ValueError(f"signal spec missing 'group'")
    try:
        return SIGNAL_REGISTRY[detect](spec)
    except KeyError as e:
        raise ValueError(f"signal spec missing required key {e}") from e
