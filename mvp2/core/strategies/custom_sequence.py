class CustomSequenceStrategy:
    def __init__(self, base_bet, sequence):
        self.base_bet = base_bet
        self.sequence = sequence
        self.index = 0

    def get_next_bet(self):
        if self.index >= len(self.sequence):
            self.index = len(self.sequence) - 1
        return self.base_bet * self.sequence[self.index]

    def record_result(self, win: bool):
        if win:
            self.index = 0
        else:
            self.index += 1

    def get_current_bet(self):
        return self.get_next_bet()

    def reset(self):
        self.index = 0 