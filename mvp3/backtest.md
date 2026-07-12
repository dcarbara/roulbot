# SpinEdge Strategy Backtest Report

**Dataset**: 59,526 real roulette spins — Feb 14 to Jun 21 2026 (98 calendar days)
**Progression**: Fibonacci P&L-reset (advance on net loss; reset when session P&L ≥ 0)
**Base bet**: $0.50 per position per round
**Bet sequence**: $0.50 → $0.50 → $1.00 → $1.50 → $2.50 → $4.00 → $6.50 → $10.50 (max level 7)
**Session structure**: One independent session per calendar day (balance resets daily)
**Data source**: `winning_numbers.db` (SQLite, 59,526 rows)

---

## Results — 11 Profitable Strategies (ranked by Campaign PnL)

| Rank | Strategy | Description | Bets | Init | TP | SL | Days | Rounds | TP% | SL% | RD | Camp PnL | Avg/Day |
|------|----------|-------------|------|------|----|----|------|--------|-----|-----|-----|----------|---------|
| 1 | **S1-Aggressive** | Col1+Col3+1st12+Red+DS1 | 5 | $200 | $64 | $55 | 98 | 4,102 | 57.1% | 39.8% | 3 | +$1368 | +$13.96 |
| 2 | **Col1+1st12+DS1** | Col1+1st12+DS1 | 3 | $200 | $40 | $55 | 98 | 3,452 | 67.3% | 29.6% | 3 | +$1059 | +$10.81 |
| 3 | **DS1+Col1** | DS1+Col1 | 2 | $150 | $40 | $40 | 98 | 3,287 | 58.2% | 39.8% | 2 | +$813 | +$8.30 |
| 4 | **DS1+DS6** | DS 1-6 + DS 31-36 | 2 | $150 | $40 | $40 | 98 | 3,825 | 58.2% | 38.8% | 3 | +$601 | +$6.13 |
| 5 | **DS1+DS5+Col1** | DS1+DS5+Col1 | 3 | $200 | $40 | $55 | 98 | 4,316 | 62.2% | 35.7% | 2 | +$488 | +$4.97 |
| 6 | **S3-Conservative** | Red+Odd+1-18+19-36+DS1+DS25 | 6 | $200 | $31 | $51 | 98 | 2,955 | 66.3% | 30.6% | 3 | +$410 | +$4.18 |
| 7 | **DS1+Red** | DS1+Red | 2 | $150 | $40 | $40 | 98 | 4,308 | 51.0% | 44.9% | 4 | +$128 | +$1.31 |
| 8 | **DS2+DS5** | DS 4-9 + DS 25-30 | 2 | $150 | $40 | $40 | 98 | 3,696 | 55.1% | 42.9% | 2 | +$122 | +$1.24 |
| 9 | **DS5** | DS 25-30 | 1 | $100 | $20 | $20 | 98 | 1,076 | 48.0% | 50.0% | 2 | +$108 | +$1.11 |
| 10 | **S2-Moderate** | Col1+1st12+3rd12+Odd+DS1 | 5 | $300 | $64 | $119 | 98 | 5,562 | 62.2% | 32.7% | 5 | +$82 | +$0.84 |
| 11 | **Col3+1st12** | Col3+1st12 | 2 | $150 | $40 | $40 | 98 | 5,003 | 50.0% | 45.9% | 4 | +$44 | +$0.45 |

---

## Column Glossary

| Column | Meaning |
|--------|---------|
| **Bets** | Number of bet positions placed each round |
| **Init** | Session starting balance (resets each day) |
| **TP** | Take-profit threshold per session |
| **SL** | Stop-loss threshold per session (soft — Fibonacci can overshoot) |
| **Days** | Total calendar days played (= 98 for all strategies) |
| **Rounds** | Total spin rounds consumed across all days |
| **TP%** | % of days that ended with take-profit |
| **SL%** | % of days that ended with stop-loss |
| **RD** | Days that exhausted all available spins without hitting TP or SL |
| **Camp PnL** | Net profit across all 98 days (positive only in this table) |
| **Avg/Day** | Average daily P&L |

---

## Key Findings

### Top 5 Strategies by Campaign P&L
- **S1-Aggressive** (Col1+Col3+1st12+Red+DS1): +$1368 over 98 days, TP rate 57.1%, avg +$13.96/day
- **Col1+1st12+DS1** (Col1+1st12+DS1): +$1059 over 98 days, TP rate 67.3%, avg +$10.81/day
- **DS1+Col1** (DS1+Col1): +$813 over 98 days, TP rate 58.2%, avg +$8.30/day
- **DS1+DS6** (DS 1-6 + DS 31-36): +$601 over 98 days, TP rate 58.2%, avg +$6.13/day
- **DS1+DS5+Col1** (DS1+DS5+Col1): +$488 over 98 days, TP rate 62.2%, avg +$4.97/day

### strat.md Bot Strategies

- **S1-Aggressive**: Rank #1 | +$1368 total | TP 57.1% | avg +$13.96/day
- **S3-Conservative**: Rank #6 | +$410 total | TP 66.3% | avg +$4.18/day
- **S2-Moderate**: Rank #10 | +$82 total | TP 62.2% | avg +$0.84/day

### Best Single Double Streets
- **DS5** (DS 25-30): Rank #9 | +$108 total | TP 48.0%

---

## Progression Note

The Fibonacci P&L-reset rule used here differs from a simple win-reset:

| Rule | Reset condition | Effect |
|------|----------------|--------|
| **Win-reset** | Any round with net > 0 | Resets early, less recovery per sequence |
| **P&L-reset** (this report) | Cumulative session P&L ≥ 0 | Holds higher level until fully recovered |

Break-even rounds (net P&L = $0) do **not** advance Fibonacci per strat.md rules.

---

*Generated: 2026-07-07 | Engine: mvp3 | Dataset: 59,526 spins | Strategies tested: 55 | Profitable: 11*