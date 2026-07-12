"""core.profit — honest, +EV money tooling for the Spinedge ecosystem.

The casino-game bet itself is unbeatable (fixed negative EV on a provably-fair
RNG). These modules implement the avenues that ACTUALLY net positive, per the
Spinedge profit-plan research:

  - sports.py   : the only genuine edge — devig fair prices, +EV detection,
                  arbitrage, Kelly sizing, CLV. Beats a real, human-priced
                  market (vig ~2-3%), not a fair RNG.
  - edges.py    : the "minimum-cost value harvester" math — game house-edge
                  table, clearing-cost, rakeback, a bonus EV-gate that auto-
                  rejects trap wagering-requirement bonuses, and the session
                  EV ledger (harvest only when external value > edge cost).
  - affiliate.py: referral lifetime-wager -> commission tracking. As an
                  affiliate the house edge pays YOU (zero bankroll at risk).

Deliberately NOT here: anything for multi-accounting / fingerprint evasion /
bonus abuse. The research showed that path is ToS-catastrophic (total fund
confiscation) and net-negative once the enforcement tail is priced in.
"""
