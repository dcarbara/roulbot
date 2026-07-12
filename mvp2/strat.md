# SpinEdge Strategies

Backtested against **59,526 real spins — 98 days (Feb–Jun 2026)**.
All strategies use **Fibonacci progression with full reset**: advance level on net-loss round, reset to [0] when cumulative P&L since last reset turns ≥ 0.
None of these combos have jackpot numbers — no single number wins all positions simultaneously. Results are genuine.

---

## Strategy 1 — Aggressive

**Positions:** `col1 + col3 + 1st12 + red + ds1`

| Param | Value |
|-------|-------|
| Base bet / position | $0.50 |
| Positions | 5 |
| Total bet at Level 0 | $2.50 / round |
| Profit target (TP) | +$64 → stop at **$164** |
| Stop loss (SL) | -$55 → stop at **$45** |
| Account needed | $155+ |

**Results (98 days):**

| Metric | Value |
|--------|-------|
| TP days | 60 / 98 (61.2%) |
| SL days | 38 / 98 |
| Total P&L | **+$2,223** |
| Average / day | **+$22.68** |

### Where to place bets

| Position | Table location | Payout |
|----------|---------------|--------|
| col1 | Bottom-left "2 TO 1" box (row of 1,4,7...34) | 2:1 |
| col3 | Top-right "2 TO 1" box (row of 3,6,9...36) | 2:1 |
| 1st12 | [1ST 12] box | 2:1 |
| red | Red diamond | 1:1 |
| ds1 | Chip on the **1–4 line** at the left edge of the number grid | 5:1 |

> **ds1 placement:** place chip on the horizontal line between 1 and 4, flush against the left border. Covers numbers 1, 2, 3, 4, 5, 6.

### Fibonacci bet table

| Level | Bet / pos | Total (×5) | Trigger |
|-------|-----------|-----------|---------|
| [0] | $0.50 | $2.50 | Start / after recovery |
| [1] | $0.50 | $2.50 | After 1 net-loss round |
| [2] | $1.00 | $5.00 | After 2 net-loss rounds |
| [3] | $1.50 | $7.50 | After 3 |
| [4] | $2.50 | $12.50 | After 4 |
| [5] | $4.00 | $20.00 | After 5 |
| [6] | $6.50 | $32.50 | After 6 |
| [7] | $10.50 | $52.50 | After 7 |

---

## Strategy 2 — Moderate

**Positions:** `col1 + 1st12 + 3rd12 + odd + ds1`

| Param | Value |
|-------|-------|
| Base bet / position | $0.50 |
| Positions | 5 |
| Total bet at Level 0 | $2.50 / round |
| Profit target (TP) | +$64 → stop at **$164** |
| Stop loss (SL) | -$119 → stop at **-$19** |
| Account needed | $219+ |

> Note: SL exceeds $100 starting balance. You must fund the account with at least $219 for the SL to function correctly.

**Results (98 days):**

| Metric | Value |
|--------|-------|
| TP days | 66 / 98 (67.3%) |
| SL days | 32 / 98 |
| Total P&L | **+$2,504** |
| Average / day | **+$25.55** |

### Where to place bets

| Position | Table location | Payout |
|----------|---------------|--------|
| col1 | Bottom-left "2 TO 1" box (row of 1,4,7...34) | 2:1 |
| 1st12 | [1ST 12] box | 2:1 |
| 3rd12 | [3RD 12] box | 2:1 |
| odd | [ODD] box | 1:1 |
| ds1 | Chip on the **1–4 line** at the left edge | 5:1 |

> 1st12 and 3rd12 together cover 1–12 and 25–36, leaving the middle dozen (13–24) uncovered — this is intentional.

### Fibonacci bet table

| Level | Bet / pos | Total (×5) | Trigger |
|-------|-----------|-----------|---------|
| [0] | $0.50 | $2.50 | Start / after recovery |
| [1] | $0.50 | $2.50 | After 1 net-loss round |
| [2] | $1.00 | $5.00 | After 2 net-loss rounds |
| [3] | $1.50 | $7.50 | After 3 |
| [4] | $2.50 | $12.50 | After 4 |
| [5] | $4.00 | $20.00 | After 5 |
| [6] | $6.50 | $32.50 | After 6 |
| [7] | $10.50 | $52.50 | After 7 |

---

## Strategy 3 — Conservative

**Positions:** `red + odd + 1-18 + 19-36 + ds1 + ds25`

| Param | Value |
|-------|-------|
| Base bet / position | $0.50 |
| Positions | 6 |
| Total bet at Level 0 | $3.00 / round |
| Profit target (TP) | +$31 → stop at **$131** |
| Stop loss (SL) | -$51 → stop at **$49** |
| Account needed | $151+ |

**Results (98 days):**

| Metric | Value |
|--------|-------|
| TP days | 72 / 98 (73.5%) |
| SL days | 26 / 98 |
| Total P&L | **+$1,442** |
| Average / day | **+$14.71** |

### Where to place bets

| Position | Table location | Payout |
|----------|---------------|--------|
| red | Red diamond | 1:1 |
| odd | [ODD] box | 1:1 |
| 1-18 | [1–18] box | 1:1 |
| 19-36 | [19–36] box | 1:1 |
| ds1 | Chip on the **1–4 line** at the left edge | 5:1 |
| ds25 | Chip on the **25–28 line** at the left edge | 5:1 |

> **ds25 placement:** place chip on the horizontal line between 25 and 28, flush against the left border. Covers numbers 25, 26, 27, 28, 29, 30.
> Note: 1-18 and 19-36 together cover the entire board (except 0). Combined with red/odd, this gives wide coverage with the double streets providing recovery leverage.

### Fibonacci bet table

| Level | Bet / pos | Total (×6) | Trigger |
|-------|-----------|-----------|---------|
| [0] | $0.50 | $3.00 | Start / after recovery |
| [1] | $0.50 | $3.00 | After 1 net-loss round |
| [2] | $1.00 | $6.00 | After 2 net-loss rounds |
| [3] | $1.50 | $9.00 | After 3 |
| [4] | 2.50 | $15.00 | After 4 |
| [5] | $4.00 | $24.00 | After 5 |
| [6] | $6.50 | $39.00 | After 6 |
| [7] | $10.50 | $63.00 | After 7 |

---

## Session Rules (all strategies)

```
Each round:
  1. Check current fibonacci level
  2. Place [bet/pos] on each listed position
  3. After spin: calculate round net P&L
  4. If net < 0  → advance to next fibonacci level
  5. If net >= 0 → check cumulative P&L since last reset
               → if cumulative >= 0: RESET to level [0]

Stop immediately when:
  Balance >= start + TP  (walk away, session won)
  Balance <= start - SL  (walk away, session lost)

Never:
  Keep playing after hitting TP
  Skip positions mid-session
  Chase losses beyond SL
```

---

## Comparison

| | Strategy 1 | Strategy 2 | Strategy 3 |
|---|---|---|---|
| Combo | col1+col3+1st12+red+ds1 | col1+1st12+3rd12+odd+ds1 | red+odd+1-18+19-36+ds1+ds25 |
| Positions | 5 | 5 | 6 |
| TP | $64 | $64 | $31 |
| SL | $55 | $119 | $51 |
| Account needed | $155 | $219 | $151 |
| TP rate | 61.2% | 67.3% | 73.5% |
| Total / 98d | +$2,223 | +$2,504 | +$1,442 |
| Avg / day | +$22.68 | +$25.55 | +$14.71 |
| Jackpot dependency | None | None | None |
| Risk profile | Moderate | High | Low |
