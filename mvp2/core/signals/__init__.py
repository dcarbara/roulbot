"""Signal layer — pluggable detectors that produce structured readings from history.

The Signal layer decouples 'reading the past' from 'deciding what to bet'.
Each Signal is a pure(-ish) function: history -> SignalReading.

Public API:
    SignalReading  — dataclass returned by signal.update()
    GROUPS         — group definitions (color, parity, hilo, dozen, column)
    make_signal(spec) — factory: {"detect": "streak", "group": "color", ...} -> Signal
    SIGNAL_REGISTRY  — name -> Signal class

Built-in signals: streak, dominance, alternation, regime.
Add new signals by subclassing Signal and registering in builtins.py.
"""
from core.signals.base import GROUPS, REDS, Signal, SignalReading
from core.signals.builtins import (
    AlternationSignal,
    DominanceSignal,
    LastNumberInSignal,
    RegimeSignal,
    StreakSignal,
)
from core.signals.registry import SIGNAL_REGISTRY, make_signal

__all__ = [
    "GROUPS", "REDS",
    "Signal", "SignalReading",
    "StreakSignal", "DominanceSignal", "AlternationSignal", "RegimeSignal",
    "LastNumberInSignal",
    "SIGNAL_REGISTRY", "make_signal",
]
