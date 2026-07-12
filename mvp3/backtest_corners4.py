"""
backtest_corners4.py  —  Backtest 4-corner strategy variants.

Table layout (for reference):
    3  6  9  12 15 18 21 24 27 30 33 36
    2  5  8  11 14 17 20 23 26 29 32 35
    1  4  7  10 13 16 19 22 25 28 31 34

A corner bet covers a 2x2 block and pays 8:1 net.  The power of CORNERTOP is
OVERLAP: when a shared number hits, every corner covering it pays at once.
Number 5 is the hottest in the dataset (+160 over expected); number 9 is +99.

Each variant is defined by its list of corner number-sets.  We sweep TP/SL
(keeping SL/TP ~= 1.22, same as CORNERTOP) and report per-session economics
using the exact P&L-based Fibonacci progression from spinedge_bot.py.
"""

import sqlite3, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DB_PATH = os.path.join(os.path.dirname(__file__), "winning_numbers.db")

# ── Corner catalog: key -> covered number set (matches coords.json) ───────────
C = {
    "cr_1_2_4_5":   {1, 2, 4, 5},
    "cr_2_3_5_6":   {2, 3, 5, 6},
    "cr_4_5_7_8":   {4, 5, 7, 8},
    "cr_5_6_8_9":   {5, 6, 8, 9},
    "cr_7_8_10_11": {7, 8, 10, 11},
    "cr_8_9_11_12": {8, 9, 11, 12},
    "cr_10_11_13_14": {10, 11, 13, 14},
    "cr_11_12_14_15": {11, 12, 14, 15},
}

# ── Variants: name -> (list of corner keys, human description) ────────────────
VARIANTS = {
    "CORNERTOP (2c ref)": (
        ["cr_1_2_4_5", "cr_2_3_5_6"],
        "baseline — 6 nums, 5 covered 2x",
    ),
    "LEFT_CHAIN": (
        ["cr_1_2_4_5", "cr_2_3_5_6", "cr_4_5_7_8", "cr_7_8_10_11"],
        "known best — covers 1-11, 5 covered 3x",
    ),
    "SQUARE_1_9": (
        ["cr_1_2_4_5", "cr_2_3_5_6", "cr_4_5_7_8", "cr_5_6_8_9"],
        "3x3 block 1-9, 5 covered 4x (32:1 on 5)",
    ),
    "DIAG_HOT_5_9": (
        ["cr_1_2_4_5", "cr_2_3_5_6", "cr_5_6_8_9", "cr_8_9_11_12"],
        "diagonal on hot 5 & 9, both covered 2x",
    ),
    "TWIN_DOMINO": (
        ["cr_1_2_4_5", "cr_2_3_5_6", "cr_7_8_10_11", "cr_8_9_11_12"],
        "two CORNERTOP dominoes (1-6 & 7-12)",
    ),
    "RIGHT_STEP": (
        ["cr_2_3_5_6", "cr_5_6_8_9", "cr_8_9_11_12", "cr_11_12_14_15"],
        "top-row chain 2-15, 5/8/11 covered 2x",
    ),
}

FIB_SEQ = [1, 1, 2, 3, 5, 8, 13, 21, 34]
MAX_FIB = 8


def build_payout_map(corner_keys):
    """number -> total net multiplier (in units of fib_bet) if that number hits.

    Winning corners net +8 each (8:1, stake already returned); losing corners
    cost -1 each.  With `hits` winners out of n_corners:
        net = 8*hits - (n_corners - hits) = 9*hits - n_corners
    (matches spinedge_bot.eval_round / BET_PAYOUT semantics.)
    """
    n_corners = len(corner_keys)
    pay = {}
    for num in range(37):
        hits = sum(1 for k in corner_keys if num in C[k])
        pay[num] = 9 * hits - n_corners
    return pay


def simulate(pay, spins, base_bet, tp, sl):
    """Run sessions with P&L-based Fibonacci (exact bot logic)."""
    sessions = []
    s_net, fib_base, fib_idx = 0.0, 0.0, 0

    for result in spins:
        fib_mult = FIB_SEQ[min(fib_idx, MAX_FIB)]
        fib_bet  = base_bet * fib_mult
        net      = pay[result] * fib_bet
        s_net    = round(s_net + net, 8)

        since = s_net - fib_base
        if since >= 0:
            fib_idx, fib_base = 0, s_net
        elif net < -1e-9:
            fib_idx += 1
            if fib_idx > MAX_FIB:
                fib_idx, fib_base = 0, s_net

        if s_net >= tp or s_net <= -sl:
            sessions.append(("TP" if s_net >= tp else "SL", round(s_net, 4)))
            s_net, fib_base, fib_idx = 0.0, 0.0, 0

    return sessions


def stats(sessions):
    if not sessions:
        return (0, 0.0, 0.0, 0.0)
    tp_n  = sum(1 for s in sessions if s[0] == "TP")
    total = sum(s[1] for s in sessions)
    return (len(sessions), 100 * tp_n / len(sessions), total / len(sessions), total)


def main():
    conn = sqlite3.connect(DB_PATH)
    raw  = [r[0] for r in conn.execute(
        "SELECT number FROM winning_numbers ORDER BY rowid").fetchall()]
    conn.close()
    spins   = [n for n in raw if isinstance(n, int) and 0 <= n <= 36]
    dropped = len(raw) - len(spins)
    print(f"Loaded {len(spins):,} valid spins"
          + (f"  ({dropped} out-of-range dropped)" if dropped else "") + "\n")

    # TP/SL sweep — SL = TP * 1.22 (same ratio as CORNERTOP $20/$24.40)
    TP_LEVELS = [20.0, 30.0, 40.0, 60.0]
    BASE_BET  = 0.10

    sep = "  " + "-" * 78
    print(f"  {'Variant / config':40s}  {'Sess':>5}  {'TP%':>6}  {'Avg/S':>7}  {'Total':>10}")
    print(sep)

    summary = []   # (name, best_total, best_row)

    for name, (keys, desc) in VARIANTS.items():
        pay      = build_payout_map(keys)
        covered  = sorted(n for n in range(37) if pay[n] > 0)
        # peak payout = best multi-hit
        peak     = max(pay.values())
        peak_num = [n for n in range(37) if pay[n] == peak]
        print(f"\n  {name}  —  {desc}")
        print(f"    covers {len(covered)} nums {covered}  |  peak +{peak}×bet on {peak_num}")

        best = None
        for tp in TP_LEVELS:
            sl = round(tp * 1.22, 2)
            sess = simulate(pay, spins, BASE_BET, tp, sl)
            n, tp_pct, avg, total = stats(sess)
            row = f"    b={BASE_BET:.2f} TP={tp:.0f}/SL={sl:.0f}"
            print(f"  {row:40s}  {n:>5}  {tp_pct:>5.1f}%  {avg:>+7.2f}  {total:>+10.1f}")
            if best is None or total > best[-1]:
                best = (name, tp, sl, n, tp_pct, avg, total)
        summary.append(best)

    # ── Ranked summary ────────────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  {'RANKED — best config per variant':40s}  {'Sess':>5}  {'TP%':>6}  {'Avg/S':>7}  {'Total':>10}")
    print(sep)
    for name, tp, sl, n, tp_pct, avg, total in sorted(summary, key=lambda x: -x[-1]):
        lbl = f"{name}  TP{tp:.0f}/SL{sl:.0f}"
        print(f"  {lbl:40s}  {n:>5}  {tp_pct:>5.1f}%  {avg:>+7.2f}  {total:>+10.1f}")
    print(sep)


if __name__ == "__main__":
    main()
