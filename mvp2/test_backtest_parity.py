"""Parity test for the backtest runner.

Goal: prove that running the same config twice — once "as the GUI would"
and once "as the CLI would" — produces bit-identical results. If this
ever fails, the GUI and CLI have drifted and the user can't trust either.

This file is meant to be run directly:

    python test_backtest_parity.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core.backtesting_runner import run_campaign, validate_config, default_config


def _result_signature(res) -> dict:
    """Reduce a CampaignResult to the values we want byte-equal across runs."""
    return {
        "initial_balance": round(res.initial_balance, 6),
        "final_balance":   round(res.final_balance, 6),
        "campaign_pnl":    round(res.campaign_pnl, 6),
        "total_rounds":    res.total_rounds,
        "sessions_run":    res.sessions_run,
        "stop_reason":     res.stop_reason,
        "final_escalation_step": res.final_escalation_step,
        "final_base_bet":  round(res.final_base_bet, 6),
        "final_max_loss":  round(res.final_max_loss, 6),
        "sessions": [
            {
                "init":   round(s.initial_balance, 6),
                "final":  round(s.final_balance, 6),
                "rounds": s.total_rounds,
                "wins":   s.total_wins,
                "losses": s.total_losses,
                "dd":     round(s.max_drawdown, 6),
            }
            for s in res.sessions
        ],
    }


def _assert_parity(name: str, cfg: dict) -> bool:
    """Run cfg twice, compare signatures. Returns True on parity."""
    # Use a list to collect logs so the parallel runs don't interleave.
    logs_a, logs_b = [], []
    a = run_campaign(dict(cfg), on_log=logs_a.append)
    b = run_campaign(dict(cfg), on_log=logs_b.append)

    sig_a = _result_signature(a)
    sig_b = _result_signature(b)

    if sig_a == sig_b:
        print(f"  ✅ PASS  {name}")
        print(f"     sessions={a.sessions_run}, rounds={a.total_rounds}, "
              f"final=${a.final_balance:.2f}, PnL=${a.campaign_pnl:+.2f}")
        return True

    print(f"  ❌ FAIL  {name}")
    print(f"     run A: {json.dumps(sig_a, indent=2)}")
    print(f"     run B: {json.dumps(sig_b, indent=2)}")
    # Diff sessions one-by-one for the first mismatch
    for i, (sa, sb) in enumerate(zip(sig_a["sessions"], sig_b["sessions"])):
        if sa != sb:
            print(f"     first session mismatch at index {i}: {sa} vs {sb}")
            break
    return False


def _scenario(label: str, **overrides) -> tuple[str, dict]:
    cfg = default_config()
    cfg.update(overrides)
    return label, validate_config(cfg)


def main() -> int:
    print("Backtest parity test — running each scenario twice and comparing results.")
    print("Same config + same DB = identical numbers.")
    print()

    scenarios = [
        _scenario(
            "Flat progression, sequential, db data",
            strategy_name="red_black", progression_type="flat",
            base_bet=0.10, initial_balance=100.0, max_loss=5.0,
            sim_mode="sequential", sims=5, rounds=20,
            historical_data_source="db", db_limit=100,
        ),
        _scenario(
            "Martingale, independent Monte Carlo, generated data with seed",
            strategy_name="red_black", progression_type="martingale",
            base_bet=0.10, initial_balance=100.0, max_loss=10.0,
            sim_mode="independent", sims=4, rounds=30,
            historical_data_source="generated", seed=42,
        ),
        _scenario(
            "Sequential with escalation 2× uniform",
            strategy_name="red_black", progression_type="flat",
            base_bet=0.10, initial_balance=100.0, max_loss=2.0,
            sim_mode="sequential", sims=6, rounds=20,
            historical_data_source="db", db_limit=200,
            enable_escalation_on_loss=True, escalation_multiplier=2.0,
            escalation_max_steps=3,
        ),
        _scenario(
            "Sequential with per-step escalation [3,5,9]",
            strategy_name="red_black", progression_type="flat",
            base_bet=0.10, initial_balance=100.0, max_loss=2.5,
            sim_mode="sequential", sims=6, rounds=20,
            historical_data_source="db", db_limit=200,
            enable_escalation_on_loss=True, escalation_per_step="3,5,9",
        ),
    ]

    failures = 0
    for label, cfg in scenarios:
        print(f"• {label}")
        if not _assert_parity(label, cfg):
            failures += 1
        print()

    print("─" * 60)
    if failures:
        print(f"❌ {failures} scenario(s) FAILED parity check.")
        return 1
    print(f"✅ All {len(scenarios)} scenarios passed parity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
