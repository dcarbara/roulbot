"""
Drawdown Analyzer for conservativeV2Bundle
==========================================
Simulates the bundle's strategy rotation with martingale progression
across many sessions to find the optimal stop-loss value.

Goal: Find min(S) such that S > d for max count(d),
      where d = intra-session drawdowns (dips from peak).

This means: the smallest stop loss that survives the most dips.

Usage:
    cd spinedge/engine/mvp2
    python tools/drawdown_analyzer.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import random
import json
import numpy as np
from collections import Counter
from dataclasses import dataclass
from typing import List, Dict, Tuple
from core.strategy_engine import StrategyEngine, ROULETTE_NUMBER_MAPPINGS, calculate_win_amount


def load_bundle(path: str) -> dict:
    with open(path, 'r') as f:
        return json.load(f)


def load_custom_strategies(config_path: str) -> dict:
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        return config.get('custom_strategies', {})
    except Exception:
        return {}


def parse_rotation_list(rotation_str: str) -> List[Tuple[str, str]]:
    """Parse rotation_list_str into list of (strategy_name, progression_config)."""
    entries = []
    for item in rotation_str.split(','):
        item = item.strip()
        if ':' in item:
            name, prog = item.split(':', 1)
            entries.append((name.strip(), prog.strip()))
        else:
            entries.append((item, 'flat'))
    return entries


def simulate_session(engines: List[StrategyEngine],
                     custom_strategies: dict,
                     rotation_mode: str,
                     switch_on_loss: bool,
                     carry_progression: bool,
                     base_bet: float,
                     max_rounds: int = 20000) -> List[float]:
    """
    Simulate one session with rotation + martingale.
    Returns the PnL curve (cumulative PnL at each round).
    """
    pnl_curve = [0.0]
    cumulative_pnl = 0.0
    active_idx = 0
    engine = engines[active_idx]

    for rnd in range(max_rounds):
        # Get bet
        bet_amounts = engine.get_bet_amounts()
        if not bet_amounts:
            # Strategy returned no labels — skip
            active_idx = (active_idx + 1) % len(engines)
            engine = engines[active_idx]
            bet_amounts = engine.get_bet_amounts()
            if not bet_amounts:
                break

        total_bet = sum(bet_amounts.values())
        if total_bet <= 0:
            break

        # Spin the wheel
        outcome = random.randint(0, 36)

        # Calculate result
        bets_list = [{'label': l, 'amount': a} for l, a in bet_amounts.items()]
        win_amt, win_details = calculate_win_amount(bets_list, outcome)
        total_return = win_amt + sum(b['amount'] for b, d in zip(bets_list, win_details) if d['win'])
        pnl = total_return - total_bet
        is_win = pnl > 0

        cumulative_pnl += pnl
        pnl_curve.append(cumulative_pnl)

        # Record result and update progression
        engine.record_result(is_win, cumulative_pnl)

        # Rotation on loss
        if not is_win and switch_on_loss and len(engines) > 1:
            active_idx = (active_idx + 1) % len(engines)
            engine = engines[active_idx]

        # Session extension logic: if we're at a new high, keep going
        peak = max(pnl_curve)
        current_drawdown = peak - cumulative_pnl

        # Stop if we've played enough and are at/above session high
        if rnd >= 1 and cumulative_pnl >= peak and is_win:
            # At session high after a win — good stopping point
            break

    return pnl_curve


def extract_drawdowns(pnl_curve: List[float]) -> List[float]:
    """
    Extract all drawdown depths from a PnL curve.
    A drawdown is measured from a local peak to the subsequent trough
    before a new peak is established.
    """
    drawdowns = []
    peak = pnl_curve[0]
    trough = pnl_curve[0]
    in_drawdown = False

    for val in pnl_curve[1:]:
        if val >= peak:
            # New peak — if we were in a drawdown, record it
            if in_drawdown and (peak - trough) > 0.001:
                drawdowns.append(peak - trough)
            peak = val
            trough = val
            in_drawdown = False
        else:
            in_drawdown = True
            trough = min(trough, val)

    # Record final drawdown if still in one
    if in_drawdown and (peak - trough) > 0.001:
        drawdowns.append(peak - trough)

    return drawdowns


def find_optimal_stop_loss(all_drawdowns: List[float],
                           percentiles: List[float] = None) -> dict:
    """
    Analyze drawdown distribution and find optimal stop loss.

    The optimal S = min value such that S > d for max count(d).
    This means: find S where CDF(S) is maximized relative to S.
    Practically: the point where most drawdowns have already been survived.
    """
    if not all_drawdowns:
        return {"error": "No drawdowns found"}

    arr = np.array(all_drawdowns)

    # Basic stats
    stats = {
        "count": len(arr),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }

    # Percentiles
    if percentiles is None:
        percentiles = [50, 75, 80, 85, 90, 95, 99]
    stats["percentiles"] = {
        f"p{p}": float(np.percentile(arr, p)) for p in percentiles
    }

    # Distribution: bin drawdowns and find the value where
    # cumulative survival is maximized relative to the stop loss value
    # i.e., max(count_survived / S) — the most "efficient" stop loss
    sorted_dd = np.sort(arr)
    n = len(sorted_dd)

    # For each possible stop loss S, count how many drawdowns < S (survived)
    # We want: min(S) such that count(d < S) is maximized
    # This is essentially the CDF — we want the "knee" point

    # Find the knee: where adding more stop loss budget yields diminishing returns
    # Use the "elbow" method: find S where the survival rate gain per unit S drops

    best_efficiency = 0
    best_s = sorted_dd[0]
    efficiency_data = []

    test_values = np.linspace(0.1, float(np.percentile(arr, 99)), 200)
    for s in test_values:
        survived = np.sum(arr <= s)
        survival_rate = survived / n
        # Efficiency = survival_rate / s (survive more dips per unit of stop loss)
        efficiency = survival_rate / s if s > 0 else 0
        efficiency_data.append((s, survived, survival_rate, efficiency))

        if efficiency > best_efficiency:
            best_efficiency = efficiency
            best_s = s

    stats["optimal_stop_loss"] = round(best_s, 2)
    stats["optimal_survival_rate"] = round(np.sum(arr <= best_s) / n * 100, 1)

    # Also find stop loss for specific survival targets
    for target in [80, 85, 90, 95]:
        idx = int(n * target / 100)
        if idx < n:
            stats[f"stop_loss_for_{target}pct_survival"] = round(float(sorted_dd[idx]), 2)

    return stats


def build_engines(bundle: dict, custom_strategies: dict) -> List[StrategyEngine]:
    """Build strategy engines from bundle config."""
    strat_config = bundle['strategy_config']
    bet_config = bundle['betting_config']
    base_bet = bet_config['base_bet']
    max_loss = bet_config['max_loss']
    max_bet = bet_config.get('max_bet', 100000)

    rotation_entries = parse_rotation_list(strat_config['rotation_list_str'])
    engines = []

    for name, prog_str in rotation_entries:
        # Parse progression from rotation string
        prog_type = 'flat'
        rules = None
        if '|' in prog_str:
            parts = prog_str.split('|')
            prog_type = parts[0]
            # Parse rules if present
            for part in parts[1:]:
                if part.startswith('rules='):
                    rules_str = part[6:]
                    # Parse dynamic rules
                    rules = []
                    for rule in rules_str.split(';'):
                        rule = rule.strip()
                        if ':' in rule:
                            trigger, action = rule.split(':', 1)
                            rule_dict = {"on": trigger.strip(), "action": action.strip()}
                            # Check for condition
                            if '|condition=' in action:
                                action_part, condition = action.split('|condition=', 1)
                                rule_dict["action"] = action_part.strip()
                                rule_dict["condition"] = condition.strip()
                            rules.append(rule_dict)

        try:
            engine = StrategyEngine(
                strategy_name=name,
                base_bet=base_bet,
                max_loss=max_loss,
                custom_strategies=custom_strategies,
                progression_type=prog_type,
                max_bet=max_bet,
                dynamic_rules=rules,
                session_start_balance=0
            )
            engines.append(engine)
        except Exception as e:
            print(f"  Warning: Failed to create engine for {name}: {e}")

    return engines


def run_analysis(bundle_path: str, config_path: str, num_simulations: int = 5000):
    """Run the full drawdown analysis."""
    print("=" * 60)
    print("DRAWDOWN ANALYSIS — Optimal Stop Loss Finder")
    print("=" * 60)

    # Load data
    bundle = load_bundle(bundle_path)
    custom_strategies = load_custom_strategies(config_path)
    strat_config = bundle['strategy_config']
    bet_config = bundle['betting_config']

    print(f"\nBundle: {bundle.get('name', 'Unknown')}")
    print(f"Base Bet: {bet_config['base_bet']}")
    print(f"Current Max Loss: {bet_config['max_loss']}")
    print(f"Progression: {strat_config['progression_type']}")
    print(f"Rotation: {strat_config['rotation_mode']} (switch on {strat_config['rotation_trigger']})")

    rotation_entries = parse_rotation_list(strat_config['rotation_list_str'])
    print(f"Strategies in rotation: {len(rotation_entries)}")
    for name, prog in rotation_entries[:3]:
        print(f"  - {name}: {prog[:50]}...")
    if len(rotation_entries) > 3:
        print(f"  ... and {len(rotation_entries) - 3} more")

    # Run simulations
    print(f"\nRunning {num_simulations} session simulations...")
    all_drawdowns = []
    session_max_drawdowns = []
    session_final_pnls = []

    for i in range(num_simulations):
        if (i + 1) % 1000 == 0:
            print(f"  Progress: {i+1}/{num_simulations}")

        # Rebuild engines each session (reset progression)
        engines = build_engines(bundle, custom_strategies)
        if not engines:
            print("ERROR: No engines could be built. Check strategy names and config.")
            return

        switch_on_loss = strat_config.get('rotation_trigger') == 'on_loss'

        pnl_curve = simulate_session(
            engines=engines,
            custom_strategies=custom_strategies,
            rotation_mode=strat_config.get('rotation_mode', 'sequential'),
            switch_on_loss=switch_on_loss,
            carry_progression=strat_config.get('carry_progression_on_switch', True),
            base_bet=bet_config['base_bet'],
            max_rounds=2000  # Cap per session for analysis
        )

        # Extract drawdowns from this session
        dds = extract_drawdowns(pnl_curve)
        all_drawdowns.extend(dds)

        # Track max drawdown per session
        if dds:
            session_max_drawdowns.append(max(dds))
        else:
            session_max_drawdowns.append(0.0)

        session_final_pnls.append(pnl_curve[-1])

    # Analyze
    print(f"\nTotal drawdowns observed: {len(all_drawdowns)}")
    print(f"Sessions simulated: {num_simulations}")
    print(f"Avg drawdowns per session: {len(all_drawdowns)/num_simulations:.1f}")

    # All drawdowns analysis
    print("\n" + "=" * 60)
    print("ALL DRAWDOWNS (every dip from peak to trough)")
    print("=" * 60)
    stats = find_optimal_stop_loss(all_drawdowns)
    print_stats(stats)

    # Session max drawdowns (worst dip per session)
    print("\n" + "=" * 60)
    print("SESSION MAX DRAWDOWNS (worst dip per session)")
    print("=" * 60)
    max_stats = find_optimal_stop_loss(session_max_drawdowns)
    print_stats(max_stats)

    # Session PnL stats
    pnl_arr = np.array(session_final_pnls)
    win_sessions = np.sum(pnl_arr > 0)
    print(f"\n{'=' * 60}")
    print(f"SESSION OUTCOMES")
    print(f"{'=' * 60}")
    print(f"  Win sessions: {win_sessions}/{num_simulations} ({win_sessions/num_simulations*100:.1f}%)")
    print(f"  Avg session PnL: {np.mean(pnl_arr):.2f}")
    print(f"  Median session PnL: {np.median(pnl_arr):.2f}")

    # Recommendation
    print(f"\n{'=' * 60}")
    print(f"RECOMMENDATION")
    print(f"{'=' * 60}")
    optimal = max_stats.get('optimal_stop_loss', bet_config['max_loss'])
    p90 = max_stats.get('stop_loss_for_90pct_survival', optimal)
    p95 = max_stats.get('stop_loss_for_95pct_survival', optimal)

    print(f"  Current stop loss:     {bet_config['max_loss']:.2f}")
    print(f"  Optimal (efficiency):  {optimal:.2f}  (survives {max_stats.get('optimal_survival_rate', 0)}% of sessions)")
    print(f"  For 90% survival:      {p90:.2f}")
    print(f"  For 95% survival:      {p95:.2f}")

    # Histogram data
    print(f"\n{'=' * 60}")
    print(f"DRAWDOWN DISTRIBUTION (session max drawdowns)")
    print(f"{'=' * 60}")
    bins = np.arange(0, float(np.percentile(session_max_drawdowns, 99)) + 1, max(0.5, optimal / 20))
    hist, edges = np.histogram(session_max_drawdowns, bins=bins)
    for i in range(len(hist)):
        bar = "#" * min(60, int(hist[i] / max(1, max(hist)) * 60))
        print(f"  {edges[i]:6.1f}-{edges[i+1]:6.1f}: {hist[i]:5d} {bar}")


def print_stats(stats: dict):
    if "error" in stats:
        print(f"  {stats['error']}")
        return
    print(f"  Count:   {stats['count']}")
    print(f"  Mean:    {stats['mean']:.2f}")
    print(f"  Median:  {stats['median']:.2f}")
    print(f"  Std Dev: {stats['std']:.2f}")
    print(f"  Min:     {stats['min']:.2f}")
    print(f"  Max:     {stats['max']:.2f}")
    print(f"  Percentiles:")
    for k, v in stats.get('percentiles', {}).items():
        print(f"    {k}: {v:.2f}")
    print(f"  Optimal Stop Loss: {stats.get('optimal_stop_loss', 'N/A')}")
    print(f"  Survival at Optimal: {stats.get('optimal_survival_rate', 'N/A')}%")


if __name__ == "__main__":
    bundle_path = os.path.expanduser("~/.spinedge/bundles/conservativeV2Bundle.json")
    config_path = os.path.expanduser("~/.spinedge/config/config.json")

    if not os.path.exists(bundle_path):
        print(f"Bundle not found: {bundle_path}")
        sys.exit(1)

    run_analysis(bundle_path, config_path, num_simulations=5000)
