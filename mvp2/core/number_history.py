"""Rolling buffer of recent roulette spins with bet-type query helpers.

Used by `core.triggers` to evaluate per-strategy conditions like "labels have
not hit in the last N spins". Kept separate from the trigger module so the
same buffer can be reused by other features (heatmaps, sector analysis, etc.)
without pulling the registries in.

Numbers are stored as ints. Label-to-number resolution defers to
`core.strategy_engine.ROULETTE_NUMBER_MAPPINGS` so a single source of truth
covers straight numbers, splits, streets, corners, dozens, columns, halves,
parity, and colors.
"""

from __future__ import annotations

from collections import deque
from typing import Callable, Iterable, Optional


class NumberHistory:
    def __init__(self, maxlen: int = 200):
        self._buf: deque[int] = deque(maxlen=maxlen)

    def append(self, num: int) -> None:
        try:
            self._buf.append(int(num))
        except (TypeError, ValueError):
            pass  # silently drop garbage rather than poison the buffer

    def extend(self, nums: Iterable[int]) -> None:
        for n in nums:
            self.append(n)

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)

    def last_n(self, n: int) -> list[int]:
        if n <= 0:
            return []
        return list(self._buf)[-n:]

    @staticmethod
    def _label_numbers(labels: Iterable[str]) -> set[int]:
        from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS
        out: set[int] = set()
        for lbl in labels or []:
            nums = ROULETTE_NUMBER_MAPPINGS.get(lbl)
            if nums:
                out.update(nums)
        return out

    def hits_in_last(self, labels: Iterable[str], n: int) -> int:
        if n <= 0:
            return 0
        nums = self._label_numbers(labels)
        if not nums:
            return 0
        return sum(1 for s in self.last_n(n) if s in nums)

    def consecutive_misses(self, labels: Iterable[str]) -> int:
        """Run of most-recent consecutive spins where none of the labels' numbers appeared."""
        nums = self._label_numbers(labels)
        if not nums:
            return 0
        count = 0
        for s in reversed(self._buf):
            if s in nums:
                break
            count += 1
        return count

    def consecutive_hits(self, labels: Iterable[str]) -> int:
        nums = self._label_numbers(labels)
        if not nums:
            return 0
        count = 0
        for s in reversed(self._buf):
            if s not in nums:
                break
            count += 1
        return count

    def _outside_streak(self, classifier: Callable[[int], Optional[str]]) -> tuple[Optional[str], int]:
        run_cat: Optional[str] = None
        run_len = 0
        for s in reversed(self._buf):
            cat = classifier(s)
            if cat is None:
                break  # 0/00 break outside-bet streaks
            if run_cat is None:
                run_cat = cat
                run_len = 1
            elif cat == run_cat:
                run_len += 1
            else:
                break
        return run_cat, run_len

    def color_streak(self) -> tuple[Optional[str], int]:
        from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS
        reds = set(ROULETTE_NUMBER_MAPPINGS.get("red", []))
        blacks = set(ROULETTE_NUMBER_MAPPINGS.get("black", []))
        def cls(n: int) -> Optional[str]:
            if n in reds:
                return "red"
            if n in blacks:
                return "black"
            return None
        return self._outside_streak(cls)

    def parity_streak(self) -> tuple[Optional[str], int]:
        def cls(n: int) -> Optional[str]:
            if n == 0:
                return None
            return "even" if n % 2 == 0 else "odd"
        return self._outside_streak(cls)

    def dozen_streak(self) -> tuple[Optional[str], int]:
        def cls(n: int) -> Optional[str]:
            if 1 <= n <= 12:
                return "1st12"
            if 13 <= n <= 24:
                return "2nd12"
            if 25 <= n <= 36:
                return "3rd12"
            return None
        return self._outside_streak(cls)
