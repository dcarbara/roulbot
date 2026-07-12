"""
Simulation: Compare rotation picker strategies using real historical data.

Tests: sequential, random, smart_ranking, smart_ranking_reverse
Against: conservativeV2 bundle config with 22k+ real spins
"""
import sys
import os
import json
import random
import statistics
import sqlite3
from collections import defaultdict
from copy import deepcopy

# -- Setup path -----------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.strategy_engine import StrategyEngine, calculate_win_amount
from core.ranking_engine import RankingEngine

# -- Load real data -------------------------------------------------
def load_history(limit=5000):
    conn = sqlite3.connect("winning_numbers.db")
    c = conn.cursor()
    c.execute("SELECT number, color FROM winning_numbers ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    rows.reverse()  # chronological
    return [{"outcome": r[0], "color": r[1]} for r in rows]

# -- Load custom strategies -----------------------------------------
def load_custom_strategies():
    config_path = os.path.expanduser("~/.spinedge/config/config.json")
    with open(config_path) as f:
        config = json.load(f)
    return config.get("custom_strategies", {})

# -- Bundle config --------------------------------------------------
STRATEGIES = [
    "romanvski1", "romanvski2", "romanvski3", "romanvski4",
    "ds1", "romanvski5", "romanvski6",
    "ds2", "ds3", "ds4", "ds5", "ds6"
]

BUNDLE_CONFIG = {
    "base_bet": 0.1,
    "max_loss_per_session": 15.5,
    "max_bet": 100000.0,
    "session_rounds": 60,        # ~1 min at 1 spin/sec
    "num_sessions": 100,
    "carry_progression": True,
    "switch_after_n_losses": 1,
    "session_ext_at_high": True,
    "max_extension_rounds": 500,  # cap for simulation
    "extension_give_up": 100000.0,
}

# -- Core simulation -----------------------------------------------
def create_engine(name, custom_strategies, base_bet=0.1):
    engine = StrategyEngine(
        strategy_name=name,
        base_bet=base_bet,
        max_loss=100000,
        custom_strategies=custom_strategies,
        progression_type="flat",
        max_bet=100000,
        session_start_balance=1000.0,
    )
    engine._ranking_simulation = True  # bypass license
    return engine


def simulate_bundle(history, custom_strategies, rotation_mode, seed=42,
                    num_sessions=100, verbose=False):
    """
    Simulate the conservativeV2 bundle with a given rotation mode.

    Each session:
    - Pick strategy via rotation_mode
    - On loss: switch to next strategy (on_loss trigger), carry martingale level
    - On win below session high: keep current bet
    - On win at/above session high: reset to base
    - Session ends when session_rounds reached AND profit >= session_high (ext_at_high)
    - Or session stop loss hit
    """
    rng = random.Random(seed)
    cfg = BUNDLE_CONFIG
    base_bet = cfg["base_bet"]

    # State
    total_balance = 1000.0
    starting_balance = total_balance
    balance_curve = [total_balance]
    session_results = []

    # Rotation state
    rotation_index = 0
    smart_ranking_index = 0
    ranking_engine = RankingEngine(custom_strategies=custom_strategies) if "smart" in rotation_mode else None

    # Track per-strategy stats for smart ranking
    strategy_stats = {name: {"wins": 0, "losses": 0, "pnl": 0.0} for name in STRATEGIES}

    spin_idx = 0  # global position in history

    for session_num in range(num_sessions):
        if spin_idx >= len(history) - 10:
            break

        # -- Pick starting strategy -----------------------------
        if rotation_mode == "sequential":
            current_strat = STRATEGIES[rotation_index % len(STRATEGIES)]
            rotation_index += 1
        elif rotation_mode == "random":
            current_strat = rng.choice(STRATEGIES)
        elif rotation_mode in ("smart_ranking", "smart_ranking_reverse"):
            # Use recent history for ranking
            recent = [h["outcome"] for h in history[max(0, spin_idx-50):spin_idx]]
            if len(recent) >= 10:
                try:
                    ranked = ranking_engine.rank_strategies(
                        list(STRATEGIES), recent, filter_by_regime=False
                    )
                    if ranked:
                        if rotation_mode == "smart_ranking_reverse":
                            ranked.reverse()
                        pick_idx = smart_ranking_index % len(ranked)
                        current_strat = ranked[pick_idx]["name"]
                        smart_ranking_index += 1
                    else:
                        current_strat = rng.choice(STRATEGIES)
                except Exception:
                    current_strat = rng.choice(STRATEGIES)
            else:
                current_strat = STRATEGIES[0]
        elif rotation_mode == "round_robin_on_loss":
            # Sequential but only advances on loss (not per session)
            current_strat = STRATEGIES[rotation_index % len(STRATEGIES)]
        else:
            current_strat = STRATEGIES[0]

        # -- Session state --------------------------------------
        session_start_bal = total_balance
        session_pnl = 0.0
        session_high = 0.0
        current_bet = base_bet
        martingale_level = 0
        rounds_played = 0
        session_wins = 0
        session_losses = 0
        consec_losses = 0
        strat_switch_count = 0
        base_rounds_done = False

        while spin_idx < len(history):
            rounds_played += 1

            # Check session stop loss
            if session_pnl <= -cfg["max_loss_per_session"]:
                break

            # Check session length + extension
            if rounds_played > cfg["session_rounds"]:
                base_rounds_done = True
                if cfg["session_ext_at_high"]:
                    if session_pnl >= session_high:
                        break  # At or above session high, can end
                    if rounds_played > cfg["session_rounds"] + cfg["max_extension_rounds"]:
                        break  # Extension cap
                    if abs(session_pnl - session_high) > cfg["extension_give_up"]:
                        break
                else:
                    break

            # -- Get bet ----------------------------------------
            engine = create_engine(current_strat, custom_strategies, base_bet)
            bet_labels = engine.get_bet_labels()

            if not bet_labels:
                spin_idx += 1
                continue

            # Per-label bet
            total_wager = current_bet * len(bet_labels)
            if total_wager > total_balance:
                break  # Can't afford

            # -- Resolve outcome --------------------------------
            outcome = history[spin_idx]["outcome"]
            spin_idx += 1

            # Check win
            bets_list = [{"label": l, "amount": current_bet} for l in bet_labels]
            win_amt, win_details = calculate_win_amount(bets_list, outcome)

            # Correct PnL: stake returned + profit for winners, minus stake for losers
            total_return = win_amt
            for b, d in zip(bets_list, win_details):
                if d["win"]:
                    total_return += b["amount"]  # stake returned

            pnl = total_return - total_wager
            is_win = pnl > 0

            session_pnl += pnl
            total_balance += pnl

            if session_pnl > session_high:
                session_high = session_pnl

            # -- Progression logic ------------------------------
            if is_win:
                session_wins += 1
                consec_losses = 0
                strategy_stats[current_strat]["wins"] += 1
                strategy_stats[current_strat]["pnl"] += pnl

                if session_pnl >= session_high:
                    # At or above session high → reset
                    current_bet = base_bet
                    martingale_level = 0
                else:
                    # Below session high → keep current bet
                    pass
            else:
                session_losses += 1
                consec_losses += 1
                strategy_stats[current_strat]["losses"] += 1
                strategy_stats[current_strat]["pnl"] += pnl

                # Martingale on loss
                martingale_level += 1
                current_bet = base_bet * (2 ** martingale_level)

                # Switch on loss
                if consec_losses >= cfg["switch_after_n_losses"]:
                    if rotation_mode == "round_robin_on_loss":
                        rotation_index += 1
                        current_strat = STRATEGIES[rotation_index % len(STRATEGIES)]
                    elif rotation_mode == "sequential":
                        # In sequential with on_loss trigger, advance
                        current_strat = STRATEGIES[(STRATEGIES.index(current_strat) + 1) % len(STRATEGIES)]
                    elif rotation_mode == "random":
                        current_strat = rng.choice(STRATEGIES)
                    else:
                        # smart modes: pick next in ranked list
                        current_strat = STRATEGIES[(STRATEGIES.index(current_strat) + 1) % len(STRATEGIES)]

                    strat_switch_count += 1
                    consec_losses = 0

                    # Carry progression (keep martingale_level and current_bet)
                    if not cfg["carry_progression"]:
                        current_bet = base_bet
                        martingale_level = 0

            balance_curve.append(total_balance)

        # Session done
        session_results.append({
            "session": session_num + 1,
            "pnl": session_pnl,
            "rounds": rounds_played,
            "wins": session_wins,
            "losses": session_losses,
            "switches": strat_switch_count,
            "end_balance": total_balance,
            "max_bet_reached": current_bet,
        })

        # Reset for next session
        smart_ranking_index = 0  # reset_rotation_on_session
        if rotation_mode == "round_robin_on_loss":
            rotation_index = 0  # reset to 1st

    # -- Compute metrics ----------------------------------------
    total_pnl = total_balance - starting_balance
    session_pnls = [s["pnl"] for s in session_results]
    winning_sessions = sum(1 for p in session_pnls if p > 0)
    losing_sessions = sum(1 for p in session_pnls if p < 0)

    # Max drawdown from balance curve
    peak = balance_curve[0]
    max_dd = 0
    for b in balance_curve:
        if b > peak:
            peak = b
        dd = peak - b
        if dd > max_dd:
            max_dd = dd

    # Sharpe-like ratio
    if session_pnls and len(session_pnls) > 1:
        avg = statistics.mean(session_pnls)
        std = statistics.stdev(session_pnls)
        sharpe = avg / std if std > 0 else 0
    else:
        sharpe = 0

    return {
        "mode": rotation_mode,
        "total_pnl": total_pnl,
        "sessions": len(session_results),
        "winning_sessions": winning_sessions,
        "losing_sessions": losing_sessions,
        "session_win_rate": winning_sessions / max(len(session_results), 1) * 100,
        "avg_session_pnl": statistics.mean(session_pnls) if session_pnls else 0,
        "median_session_pnl": statistics.median(session_pnls) if session_pnls else 0,
        "best_session": max(session_pnls) if session_pnls else 0,
        "worst_session": min(session_pnls) if session_pnls else 0,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "final_balance": total_balance,
        "spins_used": spin_idx,
        "balance_curve": balance_curve,
    }


# -- Main -----------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("  ROTATION MODE COMPARISON SIMULATION")
    print("  Bundle: conservativeV2 | Data: Real historical spins")
    print("=" * 70)

    history = load_history(limit=10000)
    print(f"\nLoaded {len(history)} real spins from DB")

    custom_strategies = load_custom_strategies()
    print(f"Loaded {len(custom_strategies)} custom strategies")

    # Verify our strategies exist
    for name in STRATEGIES:
        if name not in custom_strategies:
            print(f"  WARNING: {name} not found in custom_strategies!")

    modes = ["sequential", "random", "round_robin_on_loss", "smart_ranking", "smart_ranking_reverse"]
    results = []

    # Run multiple seeds for random modes to get averages
    NUM_SEEDS = 5
    NUM_SESSIONS = 100

    for mode in modes:
        print(f"\n{'-' * 50}")
        print(f"Testing: {mode}")

        if mode in ("random",):
            # Average over multiple seeds
            seed_results = []
            for seed in range(1, NUM_SEEDS + 1):
                r = simulate_bundle(history, custom_strategies, mode,
                                   seed=seed, num_sessions=NUM_SESSIONS)
                seed_results.append(r)
                print(f"  Seed {seed}: PnL=${r['total_pnl']:.2f}, Sessions={r['sessions']}, "
                      f"WR={r['session_win_rate']:.0f}%, DD=${r['max_drawdown']:.2f}")

            # Average
            avg_result = {
                "mode": mode,
                "total_pnl": statistics.mean([r["total_pnl"] for r in seed_results]),
                "sessions": int(statistics.mean([r["sessions"] for r in seed_results])),
                "winning_sessions": int(statistics.mean([r["winning_sessions"] for r in seed_results])),
                "losing_sessions": int(statistics.mean([r["losing_sessions"] for r in seed_results])),
                "session_win_rate": statistics.mean([r["session_win_rate"] for r in seed_results]),
                "avg_session_pnl": statistics.mean([r["avg_session_pnl"] for r in seed_results]),
                "median_session_pnl": statistics.mean([r["median_session_pnl"] for r in seed_results]),
                "best_session": statistics.mean([r["best_session"] for r in seed_results]),
                "worst_session": statistics.mean([r["worst_session"] for r in seed_results]),
                "max_drawdown": statistics.mean([r["max_drawdown"] for r in seed_results]),
                "sharpe": statistics.mean([r["sharpe"] for r in seed_results]),
                "final_balance": statistics.mean([r["final_balance"] for r in seed_results]),
                "spins_used": int(statistics.mean([r["spins_used"] for r in seed_results])),
            }
            results.append(avg_result)
        else:
            r = simulate_bundle(history, custom_strategies, mode,
                               seed=42, num_sessions=NUM_SESSIONS)
            results.append(r)

    # -- Print comparison table ---------------------------------
    print("\n" + "=" * 90)
    print(f"{'ROTATION MODE COMPARISON':^90}")
    print("=" * 90)
    print(f"{'Mode':<25} {'PnL':>8} {'Sessions':>9} {'Win%':>6} {'AvgPnL':>8} {'MedPnL':>8} "
          f"{'MaxDD':>8} {'Sharpe':>7} {'Spins':>6}")
    print("-" * 90)

    # Sort by total PnL
    results.sort(key=lambda x: x["total_pnl"], reverse=True)

    for r in results:
        print(f"{r['mode']:<25} "
              f"${r['total_pnl']:>7.2f} "
              f"{r['sessions']:>9} "
              f"{r['session_win_rate']:>5.0f}% "
              f"${r['avg_session_pnl']:>7.2f} "
              f"${r['median_session_pnl']:>7.2f} "
              f"${r['max_drawdown']:>7.2f} "
              f"{r['sharpe']:>7.3f} "
              f"{r['spins_used']:>6}")

    print("-" * 90)
    print(f"\nBest: {results[0]['mode']} (PnL: ${results[0]['total_pnl']:.2f})")
    print(f"Worst: {results[-1]['mode']} (PnL: ${results[-1]['total_pnl']:.2f})")

    # Detail on best/worst sessions
    print(f"\n{'Session Detail':^90}")
    print("-" * 90)
    for r in results:
        print(f"{r['mode']:<25} Best: ${r['best_session']:>7.2f} | Worst: ${r['worst_session']:>7.2f} | "
              f"W/L: {r['winning_sessions']}/{r['losing_sessions']}")
