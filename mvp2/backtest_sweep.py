"""Parameter sweep / grid search over the backtest runner.

Idea: take a base config and a list of "sweep" specs (one parameter +
values to try), run every combination through `run_campaign`, rank by
campaign PnL. Single parameter or Cartesian product across multiple.

Examples
========

Sweep one parameter (5 runs):
    python backtest_sweep.py base.json \\
        --sweep max_consec_losses=5,10,15,20,25

Two-dimensional sweep (15 runs = 5 × 3):
    python backtest_sweep.py base.json \\
        --sweep max_consec_losses=5,10,15,20,25 \\
        --sweep base_bet=0.05,0.10,0.20

Sweep over the escalation per-step ladder (3 runs):
    python backtest_sweep.py base.json \\
        --sweep escalation_per_step="2,4,8" "3,5,9" "1.5,2,3"

Output:
    - Console leaderboard sorted by campaign PnL (best first)
    - --csv-out: per-combination row in CSV
    - --json-out: full per-combination result blob (config + result)
    - --top N: show only the top N in the console

Use the same JSON config you'd pass to `backtest_cli.py`. Generate one
from the GUI's "📤 Export Config" button or from
`python backtest_cli.py --template`.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
import time
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core.backtesting_runner import (
    run_campaign, validate_config, default_config,
    load_config_json, CampaignResult,
)


# ── Sweep parsing ────────────────────────────────────────────────────────────

def _coerce_value(raw: str) -> Any:
    """Best-effort conversion of a sweep value string to int / float / str.
    Lists like \"2,3,5,10\" stay as strings — that's the format
    escalation_per_step expects."""
    raw = raw.strip()
    if "," in raw:
        # CSV list — keep as string (e.g. escalation_per_step="2,3,5,10")
        return raw
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        v = int(raw)
        return v
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_sweep_spec(spec: str) -> tuple[str, list[Any]]:
    """\"max_consec_losses=5,10,15\" → (\"max_consec_losses\", [5, 10, 15]).

    Values containing commas must be quoted as one token by the shell, e.g.:
        --sweep escalation_per_step=\"2,4,8\"
    """
    if "=" not in spec:
        raise ValueError(f"Sweep spec must be key=val1,val2,... — got: {spec!r}")
    key, values_str = spec.split("=", 1)
    key = key.strip()
    # For CSV-list parameters (e.g. escalation_per_step) the user may pass
    # multiple list values separated by spaces in argv. Each one is a single
    # complete CSV; we don't split on inner commas. Falling back to comma-split
    # for simple scalar lists.
    if key in ("escalation_per_step",):
        # Caller is expected to supply multiple separate --sweep flags OR
        # space-separated list values in this single flag. Here we treat the
        # whole RHS as one value if it contains commas (a single CSV list).
        # Use --sweep multiple times for multiple CSVs.
        return key, [values_str.strip()]
    values = [_coerce_value(v) for v in values_str.split(",")]
    return key, values


def merge_sweep_specs(specs: list[str]) -> dict[str, list[Any]]:
    """Combine multiple --sweep flags. Same key on multiple flags MERGES values.

    e.g. --sweep escalation_per_step=2,4,8 --sweep escalation_per_step=3,5,9
    → {"escalation_per_step": ["2,4,8", "3,5,9"]}
    """
    out: dict[str, list[Any]] = {}
    for spec in specs:
        k, vs = parse_sweep_spec(spec)
        out.setdefault(k, []).extend(vs)
    return out


# ── Running a single combination ──────────────────────────────────────────────

def run_one(base_cfg: dict, overrides: dict, quiet: bool = True) -> CampaignResult:
    cfg = dict(base_cfg)
    cfg.update(overrides)
    cfg = validate_config(cfg)
    log = (lambda _m: None) if quiet else print
    return run_campaign(cfg, on_log=log)


# ── Leaderboard formatting ────────────────────────────────────────────────────

def _format_row(rank: int, overrides: dict, res: CampaignResult, elapsed: float) -> str:
    over_str = "  ".join(f"{k}={v}" for k, v in overrides.items())
    return (f"  #{rank:>2d}  PnL=${res.campaign_pnl:>+10.2f}  "
            f"Final=${res.final_balance:>9.2f}  "
            f"Sessions={res.sessions_run:>3d}  "
            f"Rounds={res.total_rounds:>5d}  "
            f"Stop={res.stop_reason:<14s}  "
            f"({elapsed*1000:.0f}ms)  "
            f"[{over_str}]")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Parameter sweep over a backtest config — finds the combination with the best campaign PnL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("config", help="Base JSON config (same shape as backtest_cli.py).")
    ap.add_argument("--sweep", action="append", required=True,
                    help="Parameter sweep spec: key=val1,val2,... Repeat for multiple parameters.")
    ap.add_argument("--top", type=int, default=10,
                    help="Show top N combinations on the leaderboard (default 10).")
    ap.add_argument("--sort-by", default="campaign_pnl",
                    choices=("campaign_pnl", "final_balance", "max_drawdown_min"),
                    help="Leaderboard sort key (default campaign_pnl).")
    ap.add_argument("--csv-out", help="Optional CSV file with every combination's row.")
    ap.add_argument("--json-out", help="Optional JSON file with every combination's full result.")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress per-run progress.")
    args = ap.parse_args(argv)

    if not os.path.exists(args.config):
        print(f"Error: config not found: {args.config}", file=sys.stderr)
        return 1

    base = load_config_json(args.config)
    try:
        validate_config(base)  # sanity check
    except ValueError as exc:
        print(f"Error: base config invalid: {exc}", file=sys.stderr)
        return 1

    # Build the Cartesian product of all swept parameters
    sweeps = merge_sweep_specs(args.sweep)
    keys = list(sweeps.keys())
    value_lists = [sweeps[k] for k in keys]
    combinations = list(itertools.product(*value_lists))

    print(f"Base config:  {args.config}")
    print(f"Sweeping:")
    for k in keys:
        print(f"  {k}: {sweeps[k]}")
    print(f"Total combinations: {len(combinations)}")
    print(f"Strategy: {base.get('strategy_name', '?')}, "
          f"Progression: {base.get('progression_type', '?')}, "
          f"Sessions: {base.get('sims', '?')} × {base.get('rounds', '?')} rounds")
    print()

    # Run them all
    results: list[tuple[dict, CampaignResult, float]] = []
    t0 = time.monotonic()
    for i, combo in enumerate(combinations, 1):
        overrides = dict(zip(keys, combo))
        ct0 = time.monotonic()
        try:
            res = run_one(base, overrides, quiet=args.quiet)
        except Exception as exc:
            print(f"  #{i}/{len(combinations)} {overrides}  → ERROR: {exc}", file=sys.stderr)
            continue
        elapsed = time.monotonic() - ct0
        results.append((overrides, res, elapsed))
        if not args.quiet:
            print(f"  [{i:>3d}/{len(combinations)}] {overrides} → PnL=${res.campaign_pnl:+.2f}, "
                  f"final=${res.final_balance:.2f}, "
                  f"stop={res.stop_reason}  ({elapsed*1000:.0f}ms)")

    total_elapsed = time.monotonic() - t0

    # Sort + leaderboard
    def _key(item):
        _ov, res, _el = item
        if args.sort_by == "campaign_pnl":
            return -res.campaign_pnl                  # descending
        if args.sort_by == "final_balance":
            return -res.final_balance
        if args.sort_by == "max_drawdown_min":
            dds = [s.max_drawdown for s in res.sessions] if res.sessions else [0]
            return max(dds)                           # ascending (smaller drawdown first)
        return 0
    results.sort(key=_key)

    print()
    print("─" * 100)
    print(f"  LEADERBOARD  (sorted by {args.sort_by}, best first)")
    print("─" * 100)
    for rank, (overrides, res, elapsed) in enumerate(results[:args.top], 1):
        print(_format_row(rank, overrides, res, elapsed))
    if len(results) > args.top:
        print(f"  ... ({len(results) - args.top} more)")
    print("─" * 100)
    print(f"  Total runs: {len(results)}  |  total elapsed: {total_elapsed:.2f}s")
    if results:
        best_overrides, best_res, _ = results[0]
        print()
        print(f"  🏆 Best config overrides: {best_overrides}")
        print(f"      Campaign PnL:  ${best_res.campaign_pnl:+.2f}")
        print(f"      Final balance: ${best_res.final_balance:.2f}")
        print(f"      Sessions run:  {best_res.sessions_run}")
        print(f"      Stop reason:   {best_res.stop_reason}")
    print()

    # CSV export
    if args.csv_out:
        try:
            with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                header = ["rank", "campaign_pnl", "final_balance", "sessions_run",
                          "total_rounds", "stop_reason", "elapsed_ms"] + keys
                writer.writerow(header)
                for rank, (overrides, res, elapsed) in enumerate(results, 1):
                    row = [rank, f"{res.campaign_pnl:.4f}", f"{res.final_balance:.4f}",
                           res.sessions_run, res.total_rounds, res.stop_reason,
                           f"{elapsed*1000:.0f}"]
                    row += [overrides.get(k, "") for k in keys]
                    writer.writerow(row)
            print(f"  CSV saved: {args.csv_out}")
        except Exception as exc:
            print(f"  Warning: failed to write CSV: {exc}", file=sys.stderr)

    # JSON export
    if args.json_out:
        try:
            payload = {
                "base_config": base,
                "sweep_keys": keys,
                "results": [
                    {
                        "overrides": overrides,
                        "elapsed_ms": int(elapsed * 1000),
                        "result": res.to_dict(),
                    }
                    for overrides, res, elapsed in results
                ],
            }
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            print(f"  JSON saved: {args.json_out}")
        except Exception as exc:
            print(f"  Warning: failed to write JSON: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
