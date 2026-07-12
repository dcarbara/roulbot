"""
Bias Detector — Statistical analysis of roulette spin history to detect wheel biases.

Uses chi-squared goodness-of-fit tests and binomial z-tests to identify numbers,
sectors, and groups that appear significantly more often than expected on a fair wheel.

Only physical (live dealer) wheels can exhibit real bias — RNG tables cannot.
"""

import math
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from scipy.stats import chi2 as chi2_dist
    from scipy.stats import norm

    def chi2_pvalue(statistic: float, df: int) -> float:
        return float(chi2_dist.sf(statistic, df))

    def normal_pvalue_two_sided(z: float) -> float:
        return float(2 * norm.sf(abs(z)))

except ImportError:
    logger.warning("scipy not available — using fallback chi-squared approximation")

    def chi2_pvalue(statistic: float, df: int) -> float:
        """Wilson-Hilferty approximation for chi-squared survival function."""
        if df <= 0 or statistic <= 0:
            return 1.0
        z = ((statistic / df) ** (1 / 3) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
        # Standard normal CDF approximation
        p = 0.5 * math.erfc(z / math.sqrt(2))
        return max(0.0, min(1.0, p))

    def normal_pvalue_two_sided(z: float) -> float:
        p = math.erfc(abs(z) / math.sqrt(2))
        return max(0.0, min(1.0, p))


# ── Roulette constants ──────────────────────────────────────────────────

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
BLACK_NUMBERS = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

SECTORS = {
    "Voisins": {22, 18, 29, 7, 28, 12, 35, 3, 26, 0, 32, 15, 19, 4, 21, 2, 25},
    "Tiers": {27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33},
    "Orphelins": {17, 34, 6, 1, 20, 14, 31, 9},
}

DOZENS = {
    "1st12": set(range(1, 13)),
    "2nd12": set(range(13, 25)),
    "3rd12": set(range(25, 37)),
}

COLUMNS = {
    "col1": {n for n in range(1, 37) if n % 3 == 1},
    "col2": {n for n in range(1, 37) if n % 3 == 2},
    "col3": {n for n in range(1, 37) if n % 3 == 0},
}

STREETS = {
    f"{i}-{i+2}strt": set(range(i, i + 3))
    for i in range(1, 35, 3)
}

TOTAL_POCKETS = 37  # European roulette (0-36)


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class BiasResult:
    category: str       # "number", "color", "dozen", "column", "street", "sector"
    label: str          # "17", "red", "1st12", "col1", "1-3strt", "Voisins"
    observed: int
    expected: float
    z_score: float
    p_value: float
    confidence_pct: float
    is_biased: bool
    direction: str      # "OVER" or "UNDER"


@dataclass
class BiasReport:
    total_spins: int
    source: Optional[str]
    threshold: float
    bonferroni_corrected: bool
    timestamp: str
    numbers: List[BiasResult] = field(default_factory=list)
    colors: List[BiasResult] = field(default_factory=list)
    dozens: List[BiasResult] = field(default_factory=list)
    columns: List[BiasResult] = field(default_factory=list)
    streets: List[BiasResult] = field(default_factory=list)
    sectors: List[BiasResult] = field(default_factory=list)

    def get_hot_targets(self, top_n: int = 5) -> List[BiasResult]:
        """Return top N over-represented biased results, sorted by confidence."""
        all_biased = [
            r for group in (self.numbers, self.colors, self.dozens,
                            self.columns, self.streets, self.sectors)
            for r in group
            if r.is_biased and r.direction == "OVER"
        ]
        all_biased.sort(key=lambda r: r.p_value)
        return all_biased[:top_n]

    def get_cold_targets(self, top_n: int = 5) -> List[BiasResult]:
        """Return top N under-represented biased results."""
        all_biased = [
            r for group in (self.numbers, self.colors, self.dozens,
                            self.columns, self.streets, self.sectors)
            for r in group
            if r.is_biased and r.direction == "UNDER"
        ]
        all_biased.sort(key=lambda r: r.p_value)
        return all_biased[:top_n]

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"Bias Report — {self.total_spins} spins analyzed"]
        if self.source:
            lines.append(f"Source: {self.source}")
        lines.append(f"Confidence threshold: {self.threshold * 100:.1f}%"
                      + (" (Bonferroni corrected)" if self.bonferroni_corrected else ""))

        hot = self.get_hot_targets(10)
        if hot:
            lines.append("\nHOT (over-represented):")
            for r in hot:
                lines.append(
                    f"  {r.category:8s} {r.label:10s}  "
                    f"obs={r.observed}  exp={r.expected:.1f}  "
                    f"z={r.z_score:+.2f}  conf={r.confidence_pct:.2f}%"
                )
        else:
            lines.append("\nNo statistically significant biases detected.")

        cold = self.get_cold_targets(5)
        if cold:
            lines.append("\nCOLD (under-represented):")
            for r in cold:
                lines.append(
                    f"  {r.category:8s} {r.label:10s}  "
                    f"obs={r.observed}  exp={r.expected:.1f}  "
                    f"z={r.z_score:+.2f}  conf={r.confidence_pct:.2f}%"
                )

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Core detector ────────────────────────────────────────────────────────

class BiasDetector:
    """
    Analyzes roulette spin history for statistically significant biases.

    Uses per-target binomial z-tests with optional Bonferroni correction
    to control false positives when testing many targets simultaneously.
    """

    def __init__(self, confidence_threshold: float = 0.99,
                 min_spins: int = 200,
                 bonferroni: bool = True):
        """
        Args:
            confidence_threshold: Required confidence level (0-1). Default 0.99 = 99%.
            min_spins: Minimum spins required before analysis is meaningful.
            bonferroni: Apply Bonferroni correction for multiple testing.
        """
        self.confidence_threshold = confidence_threshold
        self.base_alpha = 1.0 - confidence_threshold
        self.min_spins = min_spins
        self.bonferroni = bonferroni

    def analyze(self, spins: List[int], source: str = None) -> BiasReport:
        """
        Run full bias analysis on a list of spin results (integers 0-36).
        Returns a BiasReport with all categories analyzed.
        """
        n = len(spins)
        report = BiasReport(
            total_spins=n,
            source=source,
            threshold=self.confidence_threshold,
            bonferroni_corrected=self.bonferroni,
            timestamp=datetime.utcnow().isoformat(),
        )

        if n < self.min_spins:
            logger.warning(f"Insufficient spins ({n} < {self.min_spins}). Returning empty report.")
            return report

        # Count occurrences
        counts = {}
        for s in spins:
            counts[s] = counts.get(s, 0) + 1

        # Individual numbers (37 tests)
        report.numbers = self._test_group(
            counts_map={str(num): counts.get(num, 0) for num in range(TOTAL_POCKETS)},
            expected_prob={str(num): 1.0 / TOTAL_POCKETS for num in range(TOTAL_POCKETS)},
            n=n,
            category="number",
            num_tests=TOTAL_POCKETS,
        )

        # Colors (3 tests: red, black, green)
        color_counts = {
            "red": sum(counts.get(num, 0) for num in RED_NUMBERS),
            "black": sum(counts.get(num, 0) for num in BLACK_NUMBERS),
            "green": counts.get(0, 0),
        }
        color_probs = {"red": 18 / 37, "black": 18 / 37, "green": 1 / 37}
        report.colors = self._test_group(color_counts, color_probs, n, "color", 3)

        # Dozens (3 tests)
        dozen_counts = {
            name: sum(counts.get(num, 0) for num in nums)
            for name, nums in DOZENS.items()
        }
        dozen_probs = {name: len(nums) / TOTAL_POCKETS for name, nums in DOZENS.items()}
        report.dozens = self._test_group(dozen_counts, dozen_probs, n, "dozen", 3)

        # Columns (3 tests)
        col_counts = {
            name: sum(counts.get(num, 0) for num in nums)
            for name, nums in COLUMNS.items()
        }
        col_probs = {name: len(nums) / TOTAL_POCKETS for name, nums in COLUMNS.items()}
        report.columns = self._test_group(col_counts, col_probs, n, "column", 3)

        # Streets (12 tests)
        street_counts = {
            name: sum(counts.get(num, 0) for num in nums)
            for name, nums in STREETS.items()
        }
        street_probs = {name: len(nums) / TOTAL_POCKETS for name, nums in STREETS.items()}
        report.streets = self._test_group(street_counts, street_probs, n, "street", 12)

        # Sectors (3 tests)
        sector_counts = {
            name: sum(counts.get(num, 0) for num in nums)
            for name, nums in SECTORS.items()
        }
        sector_probs = {name: len(nums) / TOTAL_POCKETS for name, nums in SECTORS.items()}
        report.sectors = self._test_group(sector_counts, sector_probs, n, "sector", 3)

        hot = report.get_hot_targets(5)
        if hot:
            logger.info(f"Bias detected! Top target: {hot[0].label} "
                        f"(conf={hot[0].confidence_pct:.2f}%)")
        else:
            logger.info(f"No significant bias detected in {n} spins.")

        return report

    def _test_group(self, counts_map: dict, expected_prob: dict,
                    n: int, category: str, num_tests: int) -> List[BiasResult]:
        """
        Run per-target binomial z-tests for a group of targets.
        Applies Bonferroni correction if enabled.
        """
        alpha = self.base_alpha
        if self.bonferroni:
            alpha = self.base_alpha / num_tests

        results = []
        for label, observed in counts_map.items():
            p = expected_prob[label]
            expected = n * p
            # Binomial z-test: z = (observed - expected) / sqrt(n * p * (1 - p))
            std = math.sqrt(n * p * (1 - p)) if p > 0 and p < 1 else 1.0
            z = (observed - expected) / std if std > 0 else 0.0
            p_val = normal_pvalue_two_sided(z)
            conf = (1.0 - p_val) * 100
            is_biased = p_val < alpha
            direction = "OVER" if observed > expected else "UNDER"

            results.append(BiasResult(
                category=category,
                label=label,
                observed=observed,
                expected=round(expected, 2),
                z_score=round(z, 3),
                p_value=p_val,
                confidence_pct=round(conf, 4),
                is_biased=is_biased,
                direction=direction,
            ))

        # Sort: biased first, then by p_value ascending
        results.sort(key=lambda r: (not r.is_biased, r.p_value))
        return results

    def get_biased_bets(self, spins: List[int], top_n: int = 5) -> List[BiasResult]:
        """Convenience: analyze and return top N hot targets."""
        report = self.analyze(spins)
        return report.get_hot_targets(top_n)


# ── Strategy integration ─────────────────────────────────────────────────

def bias_to_strategy(report: BiasReport, top_n: int = 3,
                     use_numbers_only: bool = True) -> dict:
    """
    Convert a BiasReport into a custom_strategies-compatible dict.

    Returns format compatible with StrategyEngine/CustomStrategy:
        {'labels': ['17', '23'], 'bet_units': {'17': 2, '23': 1}}

    Bet units are proportional to z-score (higher bias = more units).
    If no biases found, returns empty strategy.
    """
    if use_numbers_only:
        # Only use individual number biases (straight bets — highest payout)
        targets = [
            r for r in report.numbers
            if r.is_biased and r.direction == "OVER"
        ]
        targets.sort(key=lambda r: r.p_value)
        targets = targets[:top_n]
    else:
        targets = report.get_hot_targets(top_n)

    if not targets:
        return {"labels": [], "bet_units": {}}

    # Scale bet units by z-score (minimum 1 unit)
    min_z = min(t.z_score for t in targets)
    max_z = max(t.z_score for t in targets)
    z_range = max_z - min_z if max_z > min_z else 1.0

    labels = []
    bet_units = {}
    for t in targets:
        label = t.label
        # Scale units 1-3 based on relative z-score strength
        if z_range > 0:
            normalized = (t.z_score - min_z) / z_range
            units = 1 + round(normalized * 2)  # 1 to 3
        else:
            units = 1
        labels.append(label)
        bet_units[label] = units

    return {"labels": labels, "bet_units": bet_units}


def bias_backtest(training_spins: List[int], test_spins: List[int],
                  base_bet: float = 1.0, initial_balance: float = 1000.0,
                  top_n: int = 3, confidence: float = 0.99) -> dict:
    """
    Split-sample bias backtest:
    1. Detect bias on training_spins
    2. Generate strategy from detected biases
    3. Simulate on test_spins

    Returns dict with backtest results and the detected strategy.
    """
    detector = BiasDetector(confidence_threshold=confidence)
    report = detector.analyze(training_spins)
    strategy_def = bias_to_strategy(report, top_n=top_n)

    if not strategy_def["labels"]:
        return {
            "strategy": strategy_def,
            "report_summary": report.summary(),
            "result": "NO_BIAS_DETECTED",
            "pnl": 0.0,
        }

    # Simple flat-bet simulation on test data
    from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS, PAYOUT_TABLE, get_bet_type_and_numbers

    balance = initial_balance
    pnl_history = []
    wins = 0
    losses = 0

    for spin in test_spins:
        total_wagered = 0.0
        total_won = 0.0

        for label in strategy_def["labels"]:
            units = strategy_def["bet_units"].get(label, 1)
            wager = base_bet * units
            total_wagered += wager

            bet_type, numbers = get_bet_type_and_numbers(label)
            payout = PAYOUT_TABLE.get(bet_type, 0)

            if spin in numbers:
                total_won += wager * payout  # net win (excludes returned stake in our calc)

        round_pnl = total_won - total_wagered
        balance += round_pnl
        pnl_history.append(round_pnl)

        if total_won > 0:
            wins += 1
        else:
            losses += 1

    total_pnl = sum(pnl_history)
    max_drawdown = 0.0
    peak = 0.0
    running = 0.0
    for p in pnl_history:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_drawdown:
            max_drawdown = dd

    return {
        "strategy": strategy_def,
        "report_summary": report.summary(),
        "result": "COMPLETED",
        "total_rounds": len(test_spins),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(test_spins) * 100 if test_spins else 0,
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(balance, 2),
        "max_drawdown": round(max_drawdown, 2),
        "pnl_per_round": round(total_pnl / len(test_spins), 4) if test_spins else 0,
    }
