class FlatStrategy:
    def __init__(self, base_bet):
        self.base_bet = base_bet
        self.total_loss = 0

    def get_next_bet(self):
        return self.base_bet

    def record_result(self, win: bool):
        if not win:
            self.total_loss += self.base_bet

    def get_current_bet(self):
        return self.base_bet

    def reset(self):
        self.total_loss = 0
