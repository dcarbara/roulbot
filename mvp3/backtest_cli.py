"""Standalone backtest runner — same engine as the GUI Backtesting tab.

Usage:
    python backtest_cli.py path/to/config.json
    python backtest_cli.py path/to/config.json --out results.json
    python backtest_cli.py --template > my_config.json     # dump a starter

The config JSON schema is defined in core/backtesting_runner.default_config().
Run the same config through the GUI tab (via its "Export Config" button) and
you'll get bit-identical results — that's the whole point.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Ensure local imports work regardless of cwd
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core.backtesting_runner import (
    run_campaign, default_config, validate_config,
    load_config_json, save_config_json, CampaignResult,
)


def _print_result(res: CampaignResult, cfg: dict) -> None:
    print()
    print("─" * 72)
    print(f"  CAMPAIGN RESULTS")
    print("─" * 72)
    print(f"  Mode:                {cfg['sim_mode']}")
    print(f"  Strategy:            {cfg['strategy_name']}")
    print(f"  Progression:         {cfg['progression_type']}")
    print(f"  Sessions configured: {cfg['sims']}")
    print(f"  Sessions actually run: {res.sessions_run}")
    print(f"  Rounds per session:  {cfg['rounds']}")
    print(f"  Total rounds run:    {res.total_rounds}")
    print()
    print(f"  Initial balance:     ${res.initial_balance:,.2f}")
    print(f"  Final balance:       ${res.final_balance:,.2f}")
    print(f"  Campaign PnL:        ${res.campaign_pnl:+,.2f}")
    print(f"  Stop reason:         {res.stop_reason}")
    if res.error:
        print(f"  Error:               {res.error}")

    # Per-session breakdown
    if res.sessions:
        print()
        print(f"  Per-session breakdown:")
        print(f"  {'Sess':>5s} {'Initial':>10s} {'Final':>10s} {'PnL':>9s} {'Rounds':>7s} "
              f"{'Wins':>5s} {'Losses':>6s} {'MaxDD':>8s}")
        for i, s in enumerate(res.sessions, 1):
            pnl = s.final_balance - s.initial_balance
            print(f"  {i:>5d} ${s.initial_balance:>9.2f} ${s.final_balance:>9.2f} "
                  f"{pnl:>+8.2f} {s.total_rounds:>7d} {s.total_wins:>5d} "
                  f"{s.total_losses:>6d} ${s.max_drawdown:>7.2f}")

    # Escalation activity
    if res.escalation_log:
        print()
        print(f"  Escalation log ({len(res.escalation_log)} events):")
        for entry in res.escalation_log:
            print(f"    {entry}")
        print(f"  Final state: step {res.final_escalation_step}, "
              f"base ${res.final_base_bet:.2f}, SL ${res.final_max_loss:.2f}")

    print("─" * 72)
    print()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Run a backtest campaign — same engine as the GUI Backtesting tab.")
    ap.add_argument("config", nargs="?", help="Path to JSON config file")
    ap.add_argument("--out", help="Optional path to save results as JSON")
    ap.add_argument("--template", action="store_true",
                    help="Print a starter config to stdout and exit")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress per-session progress lines")
    args = ap.parse_args(argv)

    if args.template:
        print(json.dumps(default_config(), indent=2))
        return 0

    if not args.config:
        ap.print_help()
        return 2
    if not os.path.exists(args.config):
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        return 1

    try:
        user_cfg = load_config_json(args.config)
    except Exception as exc:
        print(f"Error: failed to parse config: {exc}", file=sys.stderr)
        return 1

    try:
        cfg = validate_config(user_cfg)
    except ValueError as exc:
        print(f"Error: invalid config: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded config from {args.config}")
    print(f"  Strategy: {cfg['strategy_name']}  |  Progression: {cfg['progression_type']}")
    print(f"  Sessions: {cfg['sims']} × {cfg['rounds']} rounds  |  Mode: {cfg['sim_mode']}")
    if cfg["enable_escalation_on_loss"]:
        print(f"  Escalation: ENABLED  (multiplier={cfg['escalation_multiplier']}, "
              f"max_steps={cfg['escalation_max_steps']}, per_step={cfg['escalation_per_step'] or 'n/a'})")
    print()

    t0 = time.monotonic()
    def _log(msg: str) -> None:
        if not args.quiet:
            print(msg)
    res = run_campaign(cfg, on_log=_log)
    elapsed = time.monotonic() - t0

    _print_result(res, cfg)
    print(f"  Elapsed: {elapsed:.2f}s")

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"config": cfg, "result": res.to_dict()}, f, indent=2)
            print(f"  Saved results to {args.out}")
        except Exception as exc:
            print(f"  Warning: could not save results: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
