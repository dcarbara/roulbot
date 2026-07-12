"""edges.py — the "minimum-cost value harvester" math.

The casino bet is unbeatable, but you still have to generate turnover to unlock
rakeback / VIP / reload rewards. The ONLY rational objective is then: clear that
required turnover at the LOWEST possible cost, and harvest external value only
when it exceeds that cost. This module is the arithmetic for that.

Key facts the math encodes:
  - Clearing cost = turnover x house_edge. Rakeback returns only a small % OF
    the edge, so net clearing cost = turnover x edge x (1 - rakeback_rate).
    => ALWAYS clear on the lowest-edge game; NEVER on roulette.
  - Most wagering-requirement (WR) bonuses are engineered net-negative; the gate
    here auto-rejects them.
  - Session EV ledger: only bet when harvested external value > edge cost.
"""
from typing import Dict, List, Optional, Tuple


# House edge as a fraction of turnover (lower = cheaper to clear volume on).
# Figures are standard Stake-style RTPs; treat as defaults, override per casino.
GAME_EDGES: Dict[str, float] = {
    "blackjack_basic":      0.0057,   # ~99.43% RTP, lowest on platform
    "baccarat_banker":      0.0106,   # incl. 5% commission
    "video_poker_max":      0.0050,   # if available, full-pay
    "baccarat_player":      0.0124,
    "dice_low":             0.0100,   # tunable; depends on target
    "craps_passline":       0.0141,
    "roulette_single_zero": 0.0270,   # the doomed one — never clear here
    "roulette_double_zero": 0.0526,
}


def clearing_cost(turnover: float, game: str) -> float:
    """Expected cost (loss) to wager `turnover` on `game` = turnover x edge."""
    return turnover * GAME_EDGES[game]


def rakeback_value(turnover: float, game: str, rakeback_rate: float) -> float:
    """Rakeback paid on this turnover. Stake's formula: rakeback is a fraction
    OF the house edge, not of turnover: turnover x edge x rakeback_rate."""
    return turnover * GAME_EDGES[game] * rakeback_rate


def net_clearing_cost(turnover: float, game: str, rakeback_rate: float = 0.0) -> float:
    """Clearing cost after rakeback = turnover x edge x (1 - rakeback_rate).
    Minimized by the lowest-edge game — this is the whole point."""
    return turnover * GAME_EDGES[game] * (1.0 - rakeback_rate)


def lowest_edge_game(allowed: Optional[List[str]] = None) -> Tuple[str, float]:
    """Return (game, edge) of the cheapest allowed clearing instrument."""
    pool = allowed or list(GAME_EDGES)
    g = min(pool, key=lambda k: GAME_EDGES[k])
    return g, GAME_EDGES[g]


def bonus_ev(bonus: float, deposit: float, wr_multiplier: float,
             clearing_game: str = "blackjack_basic",
             contribution: float = 1.0, wr_base: str = "deposit_plus_bonus"
             ) -> Dict:
    """First-order EV of accepting a wagering-requirement (WR) bonus.

    WR turnover required = wr_multiplier x base, where base is the bonus alone or
    deposit+bonus (set by terms). Because table games often contribute only a
    FRACTION (`contribution`, e.g. 0.10-0.25), the REAL turnover you must wager is
    WR / contribution, and clearing it costs (real_turnover x game_edge).

    EV ~= bonus - expected_clearing_loss.  +EV iff EV > 0.
    (Ignores bust/variance and max-bet caps, which only make it WORSE — so a
    negative first-order EV is a hard reject.)
    """
    base = (deposit + bonus) if wr_base == "deposit_plus_bonus" else bonus
    wr_turnover = wr_multiplier * base
    real_turnover = wr_turnover / max(contribution, 1e-9)
    expected_loss = real_turnover * GAME_EDGES[clearing_game]
    ev = bonus - expected_loss
    return {
        "ev": ev,
        "accept": ev > 0,
        "wr_turnover": wr_turnover,
        "real_turnover": real_turnover,
        "expected_clearing_loss": expected_loss,
        "breakeven_wr_multiplier": (bonus / (base * GAME_EDGES[clearing_game]
                                             / max(contribution, 1e-9)))
        if base > 0 else 0.0,
    }


def session_ev(harvested: float, bets: List[Tuple[float, str]]) -> Dict:
    """The decision gate. `harvested` = external value claimed this session
    (rakeback + reloads + weekly/monthly + level-ups + loss-rebate + caught rain
    + affiliate credit). `bets` = list of (stake, game). Bet ONLY when net > 0.

    Returns net EV and the verdict.
    """
    edge_cost = sum(stake * GAME_EDGES[game] for stake, game in bets)
    net = harvested - edge_cost
    return {
        "harvested": harvested,
        "edge_cost": edge_cost,
        "net_ev": net,
        "worth_it": net > 0,
    }


def min_turnover_to_threshold(current_wagered: float, threshold: float) -> float:
    """Wager-target minimization: the MINIMUM extra turnover to cross the next
    reward threshold (then STOP — never grind past it)."""
    return max(0.0, threshold - current_wagered)
