"""BiasAdaptiveStrategy — bet only when the wheel shows statistically
significant deviation from uniform.

This is what professional bias-hunters do on physical roulette wheels: watch
many spins, compute chi-square against the expected uniform distribution for
each bet category, and only place bets when the observed distribution is
biased at a configurable significance level. The bet target is the most
over-represented category in the current window.

On a fair RNG wheel this strategy almost never fires (which is the correct
behavior). On a physically biased wheel — live dealer roulette with fret
wear, ball jumps clustering, dealer signature — bias windows occur and the
strategy hunts them with edge-proportional sizing.

Constructor params (passed via custom_strategies registry):
    group:           "color" | "parity" | "hilo" | "dozen" | "column"
    window:          int, default 30 — how many recent spins to test
    chi2_threshold:  float, default 5.99 (~p<0.05 for 3-cell groups,
                                          ~p<0.025 for 2-cell)
    min_samples:     int, default 20 — minimum non-neutral spins before
                                       evaluating (rejects early-session
                                       noise where small n inflates chi²)

The strategy returns `[member]` for `get_labels()` ONLY when the test fires;
otherwise returns `[]` (sit out). Bet amounts are uniform across labels —
edge sizing is handled by the progression layer (use flat or fractional
Kelly via dynamic rules).
"""

from typing import List, Optional


# Critical chi-square thresholds for common significance levels.
# Indexed by degrees of freedom (df = members - 1).
# Saved here so callers don't need scipy.
CHI2_CRITICAL = {
    1: {0.10: 2.71, 0.05: 3.84, 0.025: 5.02, 0.01: 6.63, 0.005: 7.88},   # 2-member groups
    2: {0.10: 4.61, 0.05: 5.99, 0.025: 7.38, 0.01: 9.21, 0.005: 10.60},  # 3-member groups
}


class BiasAdaptiveStrategy:
    def __init__(self, base_bet: float,
                 group: str = "dozen",
                 window: int = 30,
                 chi2_threshold: Optional[float] = None,
                 min_samples: int = 20,
                 contra: bool = False):
        # Local imports to keep module load-order tidy.
        from core.signals.base import GROUPS
        if group not in GROUPS:
            raise ValueError(f"BiasAdaptive: unknown group {group!r}; "
                             f"valid: {list(GROUPS)}")
        self.group = group
        self.members = list(GROUPS[group]["members"])
        self.classify = GROUPS[group]["fn"]
        self.window = max(5, int(window))
        df = len(self.members) - 1
        # Default threshold = p<0.05 critical value for this df.
        if chi2_threshold is None:
            chi2_threshold = CHI2_CRITICAL.get(df, {0.05: 5.99})[0.05]
        self.chi2_threshold = float(chi2_threshold)
        self.min_samples = max(5, int(min_samples))
        # `contra` flag: when True, bet AGAINST the dominant member (i.e.
        # gambler's-fallacy mode — assumes regression to mean). Default
        # False = bet WITH the bias (professional bias-hunter mode).
        self.contra = bool(contra)
        self.base_bet = base_bet

        # Local ring buffer — keeps the strategy independent of any external
        # NumberHistory so it works standalone.
        self.last_numbers: list[int] = []
        self.regime_tags = ["NEUTRAL"]  # for compatibility with ranking_engine

        print(f"[BiasAdaptive] group={group!r} window={self.window} "
              f"threshold={self.chi2_threshold} contra={self.contra}")

    # ── Public API matching the inner-strategy contract ────────────────

    def get_next_bet(self) -> float:
        return self.base_bet

    def get_current_bet(self) -> float:
        return self.base_bet

    def reset(self) -> None:
        self.last_numbers.clear()

    def record_result(self, win: bool = False, last_number: int = None) -> None:
        if last_number is None:
            return
        try:
            n = int(last_number)
        except (TypeError, ValueError):
            return
        if not (0 <= n <= 36):
            return
        self.last_numbers.append(n)
        # Keep buffer ~3× window so chi-square has rolling fresh data.
        cap = max(self.window * 3, 100)
        if len(self.last_numbers) > cap:
            self.last_numbers = self.last_numbers[-cap:]

    # ── Bias detection core ─────────────────────────────────────────────

    def _current_bias(self) -> Optional[dict]:
        """Compute chi-square over the last `window` spins. Returns dict
        with member counts + significance flag, or None when too few
        non-neutral samples to test."""
        sample = self.last_numbers[-self.window:] if self.last_numbers else []
        counts = {m: 0 for m in self.members}
        valid = 0
        for spin in sample:
            cat = self.classify(spin)
            if cat in counts:
                counts[cat] += 1
                valid += 1
        if valid < self.min_samples:
            return None
        expected = valid / len(counts)
        if expected <= 0:
            return None
        chi2 = sum((c - expected) ** 2 / expected for c in counts.values())
        # Pick the dominant member (or weakest if contra mode).
        if self.contra:
            target = min(counts.items(), key=lambda kv: kv[1])[0]
        else:
            target = max(counts.items(), key=lambda kv: kv[1])[0]
        return {
            "chi2": chi2,
            "armed": chi2 >= self.chi2_threshold,
            "target": target,
            "counts": counts,
            "expected": expected,
            "n": valid,
        }

    def get_labels(self) -> List[str]:
        bias = self._current_bias()
        if bias is None or not bias["armed"]:
            return []
        return [bias["target"]]

    def get_bet_amounts(self, current_progression_bet: float = None) -> dict:
        amount = float(current_progression_bet if current_progression_bet is not None
                       else self.base_bet)
        return {label: amount for label in self.get_labels()}

    def get_total_bet_amount(self, current_progression_bet: float = None) -> float:
        return sum(self.get_bet_amounts(current_progression_bet).values())

    # Diagnostic helper for the GUI / audit log.
    def explain(self) -> str:
        bias = self._current_bias()
        if bias is None:
            return f"BiasAdaptive[{self.group}]: warming up ({len(self.last_numbers)}/{self.min_samples} spins)"
        tag = "ARMED" if bias["armed"] else "below threshold"
        return (f"BiasAdaptive[{self.group}]: chi²={bias['chi2']:.2f} "
                f"(thr={self.chi2_threshold:.2f}) — {tag} → target={bias['target']} "
                f"(n={bias['n']}, counts={bias['counts']})")
