class FibonacciStrategy:
    def __init__(self, base_bet, session_start_balance=0.0, max_level=7):
        self.base_bet = base_bet
        self.sequence = [1, 1]
        self.index = 0
        self.current_bet = base_bet
        # session_start_balance: lets strategy_engine compute current_profit for us
        self.session_start_balance = float(session_start_balance or 0.0)
        # _reset_baseline: absolute balance at last Fibonacci reset (strat.md rule:
        # "cumulative P&L since last reset" means P&L relative to this point, not
        # relative to session start — they diverge after the first reset within a session)
        self._reset_baseline = float(session_start_balance or 0.0)
        # max_level: cap Fibonacci index so bets don't grow unboundedly.
        # strat.md shows levels 0-7 ($0.50→$10.50/pos) and no higher — this matches
        # the real-world constraint that level-8+ bets ($17+/pos) would exceed the
        # session SL cushion before the player could realistically escalate there.
        self.max_level = int(max_level) if max_level is not None else None

    def get_next_bet(self):
        idx = min(self.index, self.max_level) if self.max_level is not None else self.index
        if idx >= len(self.sequence):
            self.sequence.append(self.sequence[-1] + self.sequence[-2])
        self.current_bet = self.base_bet * self.sequence[idx]
        return self.current_bet

    def record_result(self, win: bool, current_profit: float = None, round_pnl: float = None):
        if current_profit is not None:
            # current_profit = current_balance - session_start_balance (supplied by engine)
            # profit_since_reset = current_balance - _reset_baseline
            #                    = (session_start_balance + current_profit) - _reset_baseline
            profit_since_reset = (self.session_start_balance + current_profit) - self._reset_baseline

            # strat.md rule: advance ONLY when net < 0 (not when net == 0).
            # When round_pnl is available use it directly; fall back to win flag.
            is_net_loss = (round_pnl < 0) if round_pnl is not None else (not win)

            if profit_since_reset >= 0:
                # Back in profit relative to last reset point → reset Fibonacci
                self.index = 0
                self._reset_baseline = self.session_start_balance + current_profit
            elif is_net_loss:
                # Net-loss round while still below reset point → escalate
                self.index += 1
            # else: break-even or winning round but below reset point → hold level
        else:
            # Fallback: original win-based reset (no current_profit available)
            if win:
                self.index = 0
            else:
                self.index += 1

    def get_current_bet(self):
        return self.current_bet

    def reset(self):
        self.index = 0
        self.current_bet = self.base_bet
        self._reset_baseline = self.session_start_balance
