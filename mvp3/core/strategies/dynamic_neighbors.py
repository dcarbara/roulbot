from typing import List, Optional
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# European roulette wheel layout (physical order)
EUROPEAN_WHEEL = [
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30,
    8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7,
    28, 12, 35, 3, 26
]

ALL_NUMBERS = set(range(0, 37))

# Precompute position lookup: number -> index on wheel
_WHEEL_INDEX = {num: idx for idx, num in enumerate(EUROPEAN_WHEEL)}


class DynamicNeighborsStrategy:
    """
    Dynamic strategy that bets on N neighbors of anchor numbers
    on the European roulette wheel.

    Anchor sources:
      - nth-last:  anchor_offsets=[1] (last), [1,3] (last + 3rd last), etc.
      - hot:       hot_count=2 => top 2 most frequent numbers in lookback window
      - cold:      cold_count=1 => the least frequent (most overdue) number in lookback

    Overlapping positions are deduplicated (bet placed once, not doubled).
    Progression (bet sizing on loss) is handled externally by StrategyEngine.
    """

    def __init__(self, base_bet: float, neighbors: int = 2,
                 anchor_offsets: Optional[List[int]] = None,
                 hot_count: int = 0, cold_count: int = 0,
                 lookback: int = 30):
        """
        Args:
            base_bet: Base bet amount (used by progression).
            neighbors: Number of neighbors on EACH side of each anchor number.
            anchor_offsets: Which past numbers to use as anchors.
                           [1] = last number (default), [2] = 2nd last, etc.
            hot_count: How many hot (most frequent) numbers to add as anchors.
                       0 = disabled.
            cold_count: How many cold (least frequent / most overdue) numbers
                        to add as anchors. 0 = disabled.
            lookback: Number of past spins to analyze for hot/cold frequency.
        """
        self.base_bet = base_bet
        self.neighbors = neighbors
        self.anchor_offsets = anchor_offsets or [1]
        self.hot_count = hot_count
        self.cold_count = cold_count
        self.lookback = max(lookback, 1)

        # History buffer — keep enough for both nth-last offsets and hot/cold lookback
        max_offset = max(self.anchor_offsets) if self.anchor_offsets else 1
        self._max_history = max(max_offset, self.lookback) if (hot_count or cold_count) else max_offset
        self._history: List[int] = []

    # Backward compat: expose last_number
    @property
    def last_number(self) -> Optional[int]:
        return self._history[-1] if self._history else None

    @last_number.setter
    def last_number(self, value):
        if value is not None:
            self._history.append(value)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    def reset(self):
        self._history.clear()

    def record_result(self, win: bool, last_number: int = None):
        """Update history with the latest winning number."""
        if last_number is not None and 0 <= last_number <= 36:
            self._history.append(last_number)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            logger.debug(f"[DynamicNeighbors] History len={len(self._history)}, "
                         f"latest={last_number}")

    def get_next_bet(self):
        return self.base_bet

    def get_current_bet(self):
        return self.base_bet

    def _get_hot_numbers(self, exclude: set) -> List[int]:
        """Get the top N most frequent numbers in the lookback window."""
        if self.hot_count <= 0 or not self._history:
            return []
        window = self._history[-self.lookback:]
        freq = Counter(window)
        # Sort by frequency desc, then by most recent appearance for ties
        candidates = sorted(freq.keys(),
                            key=lambda n: (-freq[n], -self._last_seen_index(n)))
        result = []
        for num in candidates:
            if num not in exclude:
                result.append(num)
                if len(result) >= self.hot_count:
                    break
        return result

    def _get_cold_numbers(self, exclude: set) -> List[int]:
        """Get the top N least frequent / most overdue numbers.
        Works even with empty history — all numbers are equally cold,
        so picks from wheel order starting at 0."""
        if self.cold_count <= 0:
            return []
        if not self._history:
            # No history: all numbers equally cold — pick from wheel order
            candidates = list(EUROPEAN_WHEEL)
        else:
            window = self._history[-self.lookback:]
            freq = Counter(window)
            # All 37 numbers sorted by: lowest frequency first, then longest since last seen
            candidates = sorted(ALL_NUMBERS,
                                key=lambda n: (freq.get(n, 0), self._last_seen_index(n)))
        result = []
        for num in candidates:
            if num not in exclude:
                result.append(num)
                if len(result) >= self.cold_count:
                    break
        return result

    def _last_seen_index(self, number: int) -> int:
        """Index of last occurrence in history. -1 if never seen (sorts earliest)."""
        for i in range(len(self._history) - 1, -1, -1):
            if self._history[i] == number:
                return i
        return -1

    def _get_anchor_numbers(self) -> List[int]:
        """Get all anchor numbers: nth-last + hot + cold, deduplicated."""
        anchors = []
        seen = set()

        # 1. Nth-last anchors
        for offset in self.anchor_offsets:
            idx = -offset
            if abs(idx) <= len(self._history):
                num = self._history[idx]
                if num not in seen:
                    seen.add(num)
                    anchors.append(num)

        # 2. Hot numbers
        for num in self._get_hot_numbers(seen):
            seen.add(num)
            anchors.append(num)

        # 3. Cold numbers
        for num in self._get_cold_numbers(seen):
            seen.add(num)
            anchors.append(num)

        return anchors

    def get_labels(self) -> List[str]:
        """Return straight-up bet labels for all anchor numbers and their neighbors."""
        anchors = self._get_anchor_numbers()
        if not anchors:
            logger.debug("[DynamicNeighbors] Not enough history yet — waiting for spins")
            return []

        # Collect all positions, deduplicate while preserving order
        seen = set()
        all_numbers = []
        for anchor in anchors:
            for num in self._get_neighbor_numbers(anchor):
                if num not in seen:
                    seen.add(num)
                    all_numbers.append(num)

        labels = [str(n) for n in all_numbers]
        logger.debug(f"[DynamicNeighbors] Anchors={anchors}, betting on {labels} "
                     f"({len(labels)} unique positions)")
        return labels

    def _get_neighbor_numbers(self, number: int) -> List[int]:
        """Get the number and its N neighbors on each side of the European wheel."""
        if number not in _WHEEL_INDEX:
            return [number]

        idx = _WHEEL_INDEX[number]
        wheel_size = len(EUROPEAN_WHEEL)
        numbers = []
        for offset in range(-self.neighbors, self.neighbors + 1):
            neighbor_idx = (idx + offset) % wheel_size
            numbers.append(EUROPEAN_WHEEL[neighbor_idx])
        return numbers

    def get_bet_amounts(self, current_progression_bet=None):
        """Equal bet on each unique neighbor position."""
        if current_progression_bet is None:
            current_progression_bet = self.base_bet
        labels = self.get_labels()
        return {label: current_progression_bet for label in labels}

    def get_total_bet_amount(self, current_progression_bet=None):
        """Total bet across all neighbor positions."""
        bet_amounts = self.get_bet_amounts(current_progression_bet)
        return sum(bet_amounts.values())

    def describe(self) -> str:
        """Human-readable description of this strategy's config."""
        parts = []
        # Nth-last anchors
        if self.anchor_offsets:
            for o in self.anchor_offsets:
                if o == 1:
                    parts.append("last")
                elif o == 2:
                    parts.append("2nd last")
                elif o == 3:
                    parts.append("3rd last")
                else:
                    parts.append(f"{o}th last")
        if self.hot_count > 0:
            parts.append(f"top {self.hot_count} hot")
        if self.cold_count > 0:
            parts.append(f"top {self.cold_count} cold")
        anchor_desc = " + ".join(parts) if parts else "last number"
        return f"Neighbors +/-{self.neighbors} of {anchor_desc}"
