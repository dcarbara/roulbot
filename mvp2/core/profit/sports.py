"""sports.py — the genuine +EV engine.

Unlike a fair-RNG casino game (fixed negative EV), a sportsbook line is priced
by humans/algos and carries a *removable* vig (~2-3% at a sharp book like
Pinnacle, 4-10% at soft books). If you can estimate the TRUE probability better
than a book's posted price implies, you have a real, positive edge. The standard
advantage-play toolkit:

  1. devig a sharp book's two/three-way prices -> a fair probability estimate.
  2. compare that fair prob to ANOTHER book's price -> +EV if fair_prob*odds > 1.
  3. size with fractional Kelly.
  4. across books, detect ARBITRAGE (guaranteed profit) when sum(1/best_odds) < 1.
  5. track CLV (closing line value) to PROVE the edge is real over many bets.

All decimal odds (e.g. 2.10 = +110). Pure functions, no I/O, no dependencies.
Honesty note: this only works on a genuinely beatable market; it is NOT a casino
system. Edges are small (a few % per qualifying bet) and books limit winners.
"""
from typing import Dict, List, Optional, Tuple


def implied_prob(decimal_odds: float) -> float:
    """Raw implied probability of a decimal price (includes the vig)."""
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1.0")
    return 1.0 / decimal_odds


def overround(decimal_odds: List[float]) -> float:
    """Book's overround / 'vig' for a complete market = sum(implied) - 1.
    > 0 for a normal book (the house margin)."""
    return sum(implied_prob(o) for o in decimal_odds) - 1.0


def devig(decimal_odds: List[float], method: str = "proportional") -> List[float]:
    """Remove the vig from a COMPLETE market's prices -> fair probabilities that
    sum to 1. Use a SHARP book (Pinnacle) as the anchor for the fairest estimate.

    method:
      'proportional' (default) — normalize implied probs (a.k.a. multiplicative).
                                  Simple, robust, standard for 2-way markets.
      'shin'         — Shin's model, accounts for insider/favourite-longshot
                       bias; better on lopsided markets. Falls back to
                       proportional if it doesn't converge.
    """
    imp = [implied_prob(o) for o in decimal_odds]
    s = sum(imp)
    if s <= 0:
        raise ValueError("invalid odds")
    if method == "proportional":
        return [p / s for p in imp]
    if method == "shin":
        # Solve for z (insider proportion) s.t. fair probs sum to 1.
        # p_i = (sqrt(z^2 + 4*(1-z)*imp_i^2/s) - z) / (2*(1-z))
        lo, hi = 0.0, 0.5
        for _ in range(100):
            z = 0.5 * (lo + hi)
            probs = []
            for q in imp:
                val = (((z * z + 4 * (1 - z) * q * q / s) ** 0.5) - z) / (2 * (1 - z))
                probs.append(val)
            tot = sum(probs)
            if tot > 1.0:
                lo = z
            else:
                hi = z
        z = 0.5 * (lo + hi)
        probs = []
        for q in imp:
            val = (((z * z + 4 * (1 - z) * q * q / s) ** 0.5) - z) / (2 * (1 - z))
            probs.append(val)
        t = sum(probs)
        return [p / t for p in probs] if t > 0 else [p / s for p in imp]
    raise ValueError(f"unknown devig method {method!r}")


def ev_per_unit(taken_odds: float, fair_prob: float) -> float:
    """Expected value per 1 unit staked at `taken_odds` given the true prob.
    EV = fair_prob*(odds-1) - (1-fair_prob) = fair_prob*odds - 1. +EV iff > 0."""
    return fair_prob * taken_odds - 1.0


def edge_pct(taken_odds: float, fair_prob: float) -> float:
    """The +EV edge as a percentage of stake (same as ev_per_unit, x100)."""
    return 100.0 * ev_per_unit(taken_odds, fair_prob)


def kelly_fraction(taken_odds: float, fair_prob: float, fraction: float = 0.25) -> float:
    """Fractional-Kelly stake as a fraction of bankroll. b = odds-1.
    full_kelly f* = (b*p - (1-p)) / b. Returns max(0, f*) * fraction.
    Default quarter-Kelly (fraction=0.25) — full Kelly is too volatile in
    practice and assumes a perfectly known edge."""
    b = taken_odds - 1.0
    if b <= 0:
        return 0.0
    f_star = (b * fair_prob - (1.0 - fair_prob)) / b
    return max(0.0, f_star) * fraction


def find_value_bet(sharp_odds: List[float], book_odds: List[float],
                   min_edge_pct: float = 1.0, devig_method: str = "proportional"
                   ) -> Optional[Dict]:
    """Compare a soft book's prices to a sharp book's devigged fair probs.
    Returns the best +EV outcome over `min_edge_pct`, or None.

    sharp_odds / book_odds: aligned per-outcome decimal prices for the SAME market.
    """
    if len(sharp_odds) != len(book_odds) or not sharp_odds:
        raise ValueError("sharp_odds and book_odds must be aligned non-empty lists")
    fair = devig(sharp_odds, method=devig_method)
    best = None
    for i, (fp, bo) in enumerate(zip(fair, book_odds)):
        e = edge_pct(bo, fp)
        if e >= min_edge_pct and (best is None or e > best["edge_pct"]):
            best = {
                "outcome": i, "fair_prob": fp, "book_odds": bo,
                "edge_pct": e, "ev_per_unit": e / 100.0,
                "kelly_quarter": kelly_fraction(bo, fp, 0.25),
            }
    return best


def detect_arbitrage(best_odds: List[float], total_stake: float = 1.0
                     ) -> Optional[Dict]:
    """Given the BEST available decimal odds for each mutually-exclusive outcome
    (each possibly from a different book), detect a guaranteed-profit arb.

    Arb exists iff sum(1/odds) < 1. Returns the stake split that locks equal
    return on every outcome, the guaranteed profit, and the margin. Else None.
    """
    if not best_odds or any(o <= 1.0 for o in best_odds):
        raise ValueError("need decimal odds > 1.0 for every outcome")
    inv = [1.0 / o for o in best_odds]
    s = sum(inv)
    if s >= 1.0:
        return None  # no arb (book margin not beaten)
    stakes = [total_stake * (q / s) for q in inv]   # equalize payout across outcomes
    payout = stakes[0] * best_odds[0]               # identical for all outcomes
    profit = payout - total_stake
    return {
        "arb": True,
        "sum_inv": s,
        "margin_pct": 100.0 * (1.0 - s),
        "stakes": stakes,
        "guaranteed_profit": profit,
        "roi_pct": 100.0 * profit / total_stake,
    }


def clv_pct(taken_odds: float, closing_odds: float) -> float:
    """Closing Line Value: how much better your price was than the closing line.
    Positive = you beat the close (the single best long-run predictor that your
    edge is real). Expressed as % edge vs the (vig-inclusive) closing implied
    prob: CLV% = taken_odds/closing_odds - 1, in percent."""
    if closing_odds <= 1.0:
        raise ValueError("closing odds must be > 1.0")
    return 100.0 * (taken_odds / closing_odds - 1.0)


def free_bet_value(face_value: float, decimal_odds: float,
                   fair_prob: Optional[float] = None) -> float:
    """Expected cash value of a 'stake-not-returned' free bet placed at
    `decimal_odds`. If you win you keep only the profit (odds-1)*face. With the
    true win prob p, EV = p*(odds-1)*face. If fair_prob is None, assume a fair
    (no-edge) bet where p = 1/odds -> the classic ~ (odds-1)/odds retention."""
    p = fair_prob if fair_prob is not None else 1.0 / decimal_odds
    return p * (decimal_odds - 1.0) * face_value
