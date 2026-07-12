class MartingaleStrategy:
    def __init__(self, base_bet):
        self.base_bet = base_bet
        self.current_bet = base_bet
        self.total_loss = 0
        print(f"[Martingale] Initialized with base_bet: {base_bet}")

    def get_next_bet(self):
        print(f"[Martingale] get_next_bet() called, returning: {self.current_bet}")
        return self.current_bet

    def record_result(self, win: bool):
        print(f"[Martingale] record_result(win={win}) called")
        print(f"[Martingale] Before: current_bet={self.current_bet}, base_bet={self.base_bet}")
        if win:
            self.current_bet = self.base_bet
            print(f"[Martingale] WIN: Reset to base_bet={self.base_bet}")
        else:
            self.total_loss += self.current_bet
            self.current_bet *= 2
            print(f"[Martingale] LOSS: Doubled to {self.current_bet}, total_loss={self.total_loss}")
        print(f"[Martingale] After: current_bet={self.current_bet}")

    def get_current_bet(self):
        return self.current_bet

    def reset(self):
        self.current_bet = self.base_bet
        self.total_loss = 0
        print(f"[Martingale] Reset to base_bet={self.base_bet}")
