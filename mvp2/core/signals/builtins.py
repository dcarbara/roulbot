"""Built-in signal implementations.

Each signal is a pure-ish function: configured at construction, called with
history, returns a SignalReading. To add a new signal:
    1. Subclass Signal here.
    2. Register in registry.py SIGNAL_REGISTRY.
"""
from collections import Counter
from typing import List, Optional

from core.signals.base import (
    GROUPS,
    Signal,
    SignalReading,
    group_member_of,
    opposite_member,
)


def _validate_group(group: str, signal_name: str) -> None:
    if group not in GROUPS:
        raise ValueError(
            f"{signal_name}: unknown group {group!r}, expected one of {list(GROUPS)}"
        )


class AlwaysSignal(Signal):
    """Always matches. Use as the condition for an unconditional rule (e.g. an
    always-bet strategy) — the rule engine also accepts a rule with no `when`,
    but this gives the GUI (which requires at least one condition) a clean way
    to express 'fire every spin'. Group-less.

    Reading semantics: state='active' (matched), member=None, confidence=1.0.
    """
    name = "always"

    def update(self, history: List[int]) -> SignalReading:
        return SignalReading(name="always", state="active", member=None,
                             confidence=1.0, metadata={})


class StreakSignal(Signal):
    """Detects the current run-length within a group.

    Reading semantics:
        state    = 'active' if a streak exists (last spin maps to a member), else 'inactive'
        member   = current streak's member (the one that would 'continue')
        confidence = streak_length / group_size_factor (clipped to 1.0)
        metadata = {streak_length: N}

    When `min_length` is configured, the reading is only 'active' when length >= min_length.
    Otherwise streak length 1+ is reported as active.
    """

    def __init__(self, group: str, min_length: int = 1):
        _validate_group(group, "StreakSignal")
        if not isinstance(min_length, int) or min_length < 1:
            raise ValueError(f"StreakSignal: min_length must be a positive int, got {min_length!r}")
        self.group = group
        self.min_length = min_length
        self.name = f"streak({group}, min_length={min_length})"

    def update(self, history: List[int]) -> SignalReading:
        if not history:
            return SignalReading(name=self.name, state="inactive")

        last_member = group_member_of(self.group, history[-1])
        if last_member is None:
            return SignalReading(name=self.name, state="inactive",
                                 metadata={"reason": "last_spin_group_neutral"})

        length = 0
        for n in reversed(history):
            if group_member_of(self.group, n) == last_member:
                length += 1
            else:
                break

        state = "active" if length >= self.min_length else "inactive"
        # Confidence saturates around 6 (longer streaks have diminishing extra signal)
        confidence = min(1.0, length / 6.0)
        return SignalReading(
            name=self.name, state=state, member=last_member,
            confidence=confidence, metadata={"streak_length": length},
        )

    def describe(self) -> str:
        return f"{self.group} streak ≥ {self.min_length}"


class DominanceSignal(Signal):
    """Detects when one member of the group dominates a window of past spins.

    Reading semantics:
        state    = 'trending' if max_member_share >= threshold, else 'neutral'
        member   = the dominant member (highest count, with ties broken arbitrarily)
        confidence = (share - 0.5) / 0.5 for 2-member groups; (share - 1/k) / (1 - 1/k) for k-member
        metadata = {window_size, share, member_counts}
    """

    def __init__(self, group: str, window: int = 20, threshold: float = 0.6):
        _validate_group(group, "DominanceSignal")
        if not isinstance(window, int) or window < 2:
            raise ValueError(f"DominanceSignal: window must be int >= 2, got {window!r}")
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"DominanceSignal: threshold must be in (0, 1], got {threshold!r}")
        self.group = group
        self.window = window
        self.threshold = threshold
        self.name = f"dominance({group}, window={window}, threshold={threshold})"

    def update(self, history: List[int]) -> SignalReading:
        if not history:
            return SignalReading(name=self.name, state="neutral")

        window_slice = history[-self.window:]
        members = [group_member_of(self.group, n) for n in window_slice]
        members = [m for m in members if m is not None]
        if not members:
            return SignalReading(name=self.name, state="neutral",
                                 metadata={"reason": "all_window_group_neutral"})

        counts = Counter(members)
        top_member, top_count = counts.most_common(1)[0]
        share = top_count / len(members)

        # Confidence: how far above 'random' (1/k) we are, normalized
        k = len(GROUPS[self.group]["members"])
        baseline = 1.0 / k
        confidence = max(0.0, min(1.0, (share - baseline) / (1.0 - baseline)))

        state = "trending" if share >= self.threshold else "neutral"
        return SignalReading(
            name=self.name, state=state, member=top_member, confidence=confidence,
            metadata={
                "window_size": len(members),
                "share": share,
                "counts": dict(counts),
            },
        )

    def describe(self) -> str:
        return f"{self.group} dominance ≥ {self.threshold:.0%} in last {self.window}"


class AlternationSignal(Signal):
    """Detects high alternation rate (chop) in a window of past spins.

    Alternation rate = (# transitions where member changes) / (# valid pairs).
    Only meaningful for 2-member groups; for 3+ members, alternation is computed
    the same way (any change counts as a flip) but its predictive power is lower.

    Reading semantics:
        state    = 'choppy' if flip_rate >= threshold, else 'neutral'
        member   = OPPOSITE of last member (the predicted next outcome under
                   the assumption alternation continues). For 3-member groups
                   where 'opposite' is undefined, member is None.
        confidence = (flip_rate - 0.5) / 0.5 (clipped to [0,1])
        metadata = {window_size, flip_rate, last_member}
    """

    def __init__(self, group: str, window: int = 10, threshold: float = 0.7):
        _validate_group(group, "AlternationSignal")
        if not isinstance(window, int) or window < 3:
            raise ValueError(f"AlternationSignal: window must be int >= 3, got {window!r}")
        if not (0.0 < threshold <= 1.0):
            raise ValueError(f"AlternationSignal: threshold must be in (0, 1], got {threshold!r}")
        self.group = group
        self.window = window
        self.threshold = threshold
        self.name = f"alternation({group}, window={window}, threshold={threshold})"

    def update(self, history: List[int]) -> SignalReading:
        if not history:
            return SignalReading(name=self.name, state="neutral")

        window_slice = history[-self.window:]
        members = [group_member_of(self.group, n) for n in window_slice]
        members = [m for m in members if m is not None]
        if len(members) < 2:
            return SignalReading(name=self.name, state="neutral",
                                 metadata={"reason": "insufficient_valid_spins"})

        flips = sum(1 for i in range(1, len(members)) if members[i] != members[i - 1])
        flip_rate = flips / (len(members) - 1)
        confidence = max(0.0, min(1.0, (flip_rate - 0.5) / 0.5))

        state = "choppy" if flip_rate >= self.threshold else "neutral"
        last_member = members[-1]
        predicted_next = opposite_member(self.group, last_member)
        return SignalReading(
            name=self.name, state=state, member=predicted_next,
            confidence=confidence,
            metadata={
                "window_size": len(members),
                "flip_rate": flip_rate,
                "last_member": last_member,
            },
        )

    def describe(self) -> str:
        return f"{self.group} alternation ≥ {self.threshold:.0%} in last {self.window}"


class LastNumberInSignal(Signal):
    """Fires when the Nth-most-recent spin's number is in a configured set.

    Useful for 'when X hits, bet straight numbers [...]' lookup-table strategies
    (e.g. wheel-neighbor coverage triggered by a specific spin landing).

    Reading semantics:
        state    = 'active' if history[-offset] is in `numbers`, else 'inactive'
        member   = the matched number as a string (e.g. '17') — exposed for any
                   downstream action that wants to reference it; not used by the
                   typical pairing with action='labels'
        confidence = 1.0 on match, 0.0 otherwise
        metadata = {'matched_number': N, 'offset': offset}

    This signal does NOT use the group concept (it operates on raw spin numbers).
    """

    def __init__(self, numbers, offset: int = 1):
        if not isinstance(numbers, (list, tuple, set)) or len(numbers) == 0:
            raise ValueError(
                "LastNumberInSignal: 'numbers' must be a non-empty list of ints 0-36"
            )
        normalized = []
        for n in numbers:
            try:
                v = int(n)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"LastNumberInSignal: number {n!r} is not a valid int"
                ) from e
            if not (0 <= v <= 36):
                raise ValueError(f"LastNumberInSignal: number {v} out of range 0-36")
            normalized.append(v)
        if not isinstance(offset, int) or offset < 1:
            raise ValueError(f"LastNumberInSignal: offset must be int >= 1, got {offset!r}")
        # Preserve order but dedupe
        seen = set()
        self.numbers = []
        for v in normalized:
            if v not in seen:
                seen.add(v)
                self.numbers.append(v)
        self.offset = offset
        nums_str = ",".join(str(n) for n in self.numbers)
        self.name = f"last_number_in([{nums_str}], offset={offset})"

    def update(self, history):
        if len(history) < self.offset:
            return SignalReading(name=self.name, state="inactive")
        n = history[-self.offset]
        if n in self.numbers:
            return SignalReading(
                name=self.name, state="active", member=str(n),
                confidence=1.0,
                metadata={"matched_number": n, "offset": self.offset},
            )
        return SignalReading(name=self.name, state="inactive",
                             metadata={"observed": n, "offset": self.offset})

    def describe(self) -> str:
        nums = ", ".join(str(n) for n in self.numbers)
        slot = "last" if self.offset == 1 else f"#{self.offset}-back"
        return f"{slot} number in [{nums}]"


class RegimeSignal(Signal):
    """Composite regime classifier: TRENDING / CHOPPY / NEUTRAL.

    Internally evaluates DominanceSignal and AlternationSignal over the same
    window, and picks the more confident classification (trend wins ties).

    Reading semantics:
        state    = 'TRENDING' | 'CHOPPY' | 'NEUTRAL'
        member   = TRENDING -> dominant member; CHOPPY -> predicted-next (opposite of last);
                   NEUTRAL -> None
        confidence = the winning signal's confidence
        metadata = {dominance: {...}, alternation: {...}}

    When configuring a rule, you can match on a specific regime via the
    'regime' key in the rule spec (e.g. "regime": "TRENDING").
    """

    def __init__(self, group: str, window: int = 20,
                 trend_threshold: float = 0.6, chop_threshold: float = 0.7):
        _validate_group(group, "RegimeSignal")
        self.group = group
        self.window = window
        self.trend_threshold = trend_threshold
        self.chop_threshold = chop_threshold
        self._dominance = DominanceSignal(group=group, window=window, threshold=trend_threshold)
        self._alternation = AlternationSignal(group=group, window=window, threshold=chop_threshold)
        self.name = (f"regime({group}, window={window}, "
                     f"trend_threshold={trend_threshold}, chop_threshold={chop_threshold})")

    def update(self, history: List[int]) -> SignalReading:
        dom = self._dominance.update(history)
        alt = self._alternation.update(history)
        meta = {"dominance": dom.metadata, "alternation": alt.metadata}

        dom_match = dom.state == "trending"
        alt_match = alt.state == "choppy"

        if dom_match and alt_match:
            # Both fire — pick the more confident
            if dom.confidence >= alt.confidence:
                return SignalReading(name=self.name, state="TRENDING",
                                     member=dom.member, confidence=dom.confidence,
                                     metadata=meta)
            return SignalReading(name=self.name, state="CHOPPY",
                                 member=alt.member, confidence=alt.confidence,
                                 metadata=meta)
        if dom_match:
            return SignalReading(name=self.name, state="TRENDING",
                                 member=dom.member, confidence=dom.confidence,
                                 metadata=meta)
        if alt_match:
            return SignalReading(name=self.name, state="CHOPPY",
                                 member=alt.member, confidence=alt.confidence,
                                 metadata=meta)
        return SignalReading(name=self.name, state="NEUTRAL", member=None,
                             confidence=0.0, metadata=meta)

    def describe(self) -> str:
        return (f"{self.group} regime (window={self.window}, "
                f"trend≥{self.trend_threshold:.0%}, chop≥{self.chop_threshold:.0%})")
