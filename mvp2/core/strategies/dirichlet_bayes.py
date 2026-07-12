"""DirichletBayesStrategy — self-evolving Bayesian bias hunter (mode 'dirichlet_bayes').

Maintains ONE discounted Dirichlet-multinomial posterior over the 37 pockets
(0..36) and bets a label ONLY when the one-sided lower credible bound on that
label's true probability provably exceeds its payout break-even — i.e. only when
there is statistical evidence of a real, persistent bias. Otherwise it returns
[] (sits out, zero turnover).

HONESTY (by construction, not emergent): on a provably-fair single-zero RNG every
pocket has p = 1/37 = 0.02703, which is BELOW the straight break-even 1/36 =
0.02778 (and a fair dozen's 12/37 = 0.3243 < 1/3). So even the posterior MEAN of a
fair label sits under threshold; the 95% lower bound sits far below it and can only
cross by sampling noise (bounded by `delta`, further controlled by Bonferroni and a
per-label evidence floor). The model therefore CANNOT manufacture an edge on fair
RNG — it converges to sitting out, and any turnover it does leak realizes the fixed
-1/37 = -2.70% house edge. It can only profit on a genuinely non-uniform wheel /
flawed RNG (physical bias, dealer signature, weak PRNG).

SELF-EVOLVING: evidence mass is geometrically discounted each spin (the prior is
anchored, only evidence decays), giving a steady-state effective sample size
N_eff = 1/(1-gamma) (gamma=0.998 -> ~500 recent spins). A drifting bias is tracked;
a vanished bias auto-disarms within ~N_eff spins. A cheap change-point guard
(surprise EWMA) temporarily speeds forgetting after an abrupt regime shift.

Design from the Spinedge model-design panel (Dirichlet-Bayes winner, hybridized
with a surprise-guard change-point flush and a scipy-optional Beta inverse-CDF).
"""
import math
from typing import List, Optional, Tuple

from core.signals.base import GROUPS


# ---- regularized incomplete beta + inverse (scipy-optional, vetted fallback) ----

def _betacf(a: float, b: float, x: float) -> float:
    MAXIT, EPS, FPMIN = 200, 3.0e-12, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


try:  # prefer scipy's vetted, fast ppf when available
    from scipy.stats import beta as _scipy_beta  # type: ignore

    def _beta_ppf(p: float, a: float, b: float) -> float:
        return float(_scipy_beta.ppf(p, a, b))
except Exception:  # self-contained bisection inverse of I_x(a,b)=p (NOT a normal approx)
    def _beta_ppf(p: float, a: float, b: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if _betai(a, b, mid) < p:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)


def _sector_labels() -> List[Tuple[str, frozenset, int]]:
    """(label, pocket index set, payout multiplier m) for every GROUPS member.
    dozen/column pay 2:1; color/parity/hilo pay 1:1."""
    out = []
    for gname, gdef in GROUPS.items():
        fn = gdef["fn"]
        m = 2 if gname in ("dozen", "column") else 1
        for member in gdef["members"]:
            idxs = frozenset(n for n in range(37) if fn(n) == member)
            out.append((member, idxs, m))
    return out


_STRAIGHTS = [(str(n), frozenset({n}), 35) for n in range(37)]
_SECTORS = _sector_labels()


class DirichletBayesStrategy:
    def __init__(self, base_bet: float,
                 gamma: float = 0.998, gamma_fast: float = 0.95,
                 alpha_prior: float = 1.0 / 37,
                 delta: float = 0.05, min_neff: int = 200,
                 min_label_hits: int = 12, top_k: int = 3, margin: float = 0.0,
                 targets: str = "both",
                 changepoint_z: float = 4.0, changepoint_run: int = 5,
                 flush_spins: int = 150, bonferroni: bool = True):
        self.base_bet = base_bet
        self.gamma = float(gamma)
        self.gamma_fast = float(gamma_fast)
        self.a0 = float(alpha_prior)
        self.delta = float(delta)
        self.min_neff = int(min_neff)
        self.min_hits = float(min_label_hits)
        self.K = int(top_k)
        self.margin = float(margin)
        self.targets = targets if targets in ("straights", "sectors", "both") else "both"
        self.cz = float(changepoint_z)
        self.crun = int(changepoint_run)
        self.flush = int(flush_spins)
        self.bonferroni = bool(bonferroni)

        self.alpha = [self.a0] * 37
        self.n_eff = 0.0
        self.s_mean = 0.0
        self.s_var = 1.0
        self.run = 0
        self.flush_left = 0
        self.last_numbers: List[int] = []
        self.regime_tags = ["NEUTRAL"]  # ranking_engine compat

        self._pool = (
            (_STRAIGHTS if self.targets in ("straights", "both") else [])
            + (_SECTORS if self.targets in ("sectors", "both") else [])
        )
        print(f"[DirichletBayes] gamma={self.gamma} delta={self.delta} "
              f"min_neff={self.min_neff} targets={self.targets} bonferroni={self.bonferroni}")

    # ----- inner-strategy contract -----
    def get_next_bet(self) -> float:
        return self.base_bet

    def get_current_bet(self) -> float:
        return self.base_bet

    def reset(self) -> None:
        self.alpha = [self.a0] * 37
        self.n_eff = 0.0
        self.s_mean = 0.0
        self.s_var = 1.0
        self.run = 0
        self.flush_left = 0
        self.last_numbers.clear()

    def record_result(self, win: bool = False, last_number: int = None) -> None:
        # P&L (win) is ignored — the label set is decided from the winning-number
        # distribution, exactly like BiasAdaptiveStrategy.
        if last_number is None:
            return
        try:
            k = int(last_number)
        except (TypeError, ValueError):
            return
        if not (0 <= k <= 36):
            return
        self.last_numbers.append(k)
        if len(self.last_numbers) > 200:
            self.last_numbers = self.last_numbers[-200:]

        tot = math.fsum(self.alpha)
        p_pred = self.alpha[k] / tot
        surprise = -math.log(max(p_pred, 1e-12))
        z = (surprise - self.s_mean) / math.sqrt(self.s_var + 1e-9)
        self.run = self.run + 1 if z > self.cz else 0
        if self.run >= self.crun:
            self.flush_left = self.flush
            self.run = 0
        # update surprise EWMA (after the z-test so the spike isn't self-masked)
        bw = 0.02
        d = surprise - self.s_mean
        self.s_mean += bw * d
        self.s_var = max(self.s_var + bw * (d * d - self.s_var), 1e-6)

        g = self.gamma_fast if self.flush_left > 0 else self.gamma
        if self.flush_left > 0:
            self.flush_left -= 1
        a0 = self.a0
        for i in range(37):
            self.alpha[i] = g * (self.alpha[i] - a0) + a0   # decay evidence, anchor prior
        self.alpha[k] += 1.0
        self.n_eff = g * self.n_eff + 1.0

    # ----- bias evaluation -----
    def _assess(self) -> List[dict]:
        """Detailed assessment of every label that provably clears its payout
        break-even at the lower credible bound, sorted best-edge first. Each
        dict has the posterior mean/lower-bound probability, the break-even, the
        payout, and the worst-case + mean edge (for Kelly sizing). Empty list =
        sit out (no proven bias)."""
        if self.n_eff < self.min_neff:
            return []  # warmup
        tot = math.fsum(self.alpha)
        eff_delta = self.delta / len(self._pool) if self.bonferroni else self.delta
        out = []
        for label, idxs, m in self._pool:
            a_L = math.fsum(self.alpha[i] for i in idxs)
            mean = a_L / tot
            p_be = 1.0 / (m + 1)
            thr = p_be * (1.0 + self.margin)
            # The lower credible bound is always <= mean, so if the mean itself is
            # below threshold the label can't pass — skip the expensive Beta inverse.
            if mean <= thr:
                continue
            hits = a_L - self.a0 * len(idxs)
            if hits < self.min_hits:
                continue
            lb = _beta_ppf(eff_delta, a_L, tot - a_L)
            if lb > thr:
                out.append({
                    "label": label, "payout": m, "decimal_odds": float(m + 1),
                    "mean_prob": mean, "lb_prob": lb, "break_even": p_be,
                    "edge_lb": lb * (m + 1) - 1.0,
                    "edge_mean": mean * (m + 1) - 1.0,
                    "idxs": idxs,   # pocket set, for overlap-aware sizing
                })
        out.sort(key=lambda d: -d["edge_lb"])
        return out

    def get_labels(self) -> List[str]:
        return [d["label"] for d in self._assess()[:self.K]]

    def armed_bets(self) -> List[dict]:
        """Public: the detailed _assess() output (mean/lower-bound prob, edge,
        payout) for every provably +EV label. Used by the bias scout for
        ranking and Kelly sizing. Empty = sit out."""
        return self._assess()

    # ----- persistence (so a wheel's posterior survives restarts) -----
    def state_dict(self) -> dict:
        return {"alpha": list(self.alpha), "n_eff": self.n_eff,
                "s_mean": self.s_mean, "s_var": self.s_var,
                "run": self.run, "flush_left": self.flush_left,
                "last_numbers": list(self.last_numbers)}

    def load_state(self, st: dict) -> None:
        a = list(st.get("alpha") or [])
        self.alpha = a if len(a) == 37 else [self.a0] * 37
        self.n_eff = float(st.get("n_eff", 0.0))
        self.s_mean = float(st.get("s_mean", 0.0))
        self.s_var = float(st.get("s_var", 1.0))
        self.run = int(st.get("run", 0))
        self.flush_left = int(st.get("flush_left", 0))
        self.last_numbers = list(st.get("last_numbers") or [])

    def get_bet_amounts(self, current_progression_bet: float = None) -> dict:
        amt = float(current_progression_bet if current_progression_bet is not None
                    else self.base_bet)
        return {lab: amt for lab in self.get_labels()}

    def get_total_bet_amount(self, current_progression_bet: float = None) -> float:
        return sum(self.get_bet_amounts(current_progression_bet).values())

    # ----- diagnostics -----
    def explain(self) -> str:
        if self.n_eff < self.min_neff:
            return f"DirichletBayes: learning ({self.n_eff:.0f}/{self.min_neff} eff. spins)"
        ev = self._assess()
        if not ev:
            return (f"DirichletBayes: no bias above break-even (N_eff={self.n_eff:.0f}) "
                    f"— sitting out"
                    + (f" [flush {self.flush_left}]" if self.flush_left else ""))
        top = ", ".join(f"{d['label']}(mean={d['mean_prob']:.4f}, edge={d['edge_lb']:+.2f})"
                        for d in ev[:3])
        return f"DirichletBayes: ARMED N_eff={self.n_eff:.0f} -> {top}"

    def describe(self) -> str:
        return (f"Dirichlet-Bayes bias hunter (gamma={self.gamma}, delta={self.delta}, "
                f"targets={self.targets}) — bets only on proven +EV bias")
