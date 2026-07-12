class FibonacciStrategy:
    def __init__(self, base_bet):
        self.base_bet = base_bet
        self.sequence = [1, 1]
        self.index = 0
        self.current_bet = base_bet

    def get_next_bet(self):
        if self.index >= len(self.sequence):
            self.sequence.append(self.sequence[-1] + self.sequence[-2])
        self.current_bet = self.base_bet * self.sequence[self.index]
        return self.current_bet

    def record_result(self, win: bool):
        if win:
            self.index = 0
        else:
            self.index += 1

    def get_current_bet(self):
        return self.current_bet

    def reset(self):
        self.index = 0
        self.current_bet = self.base_bet 