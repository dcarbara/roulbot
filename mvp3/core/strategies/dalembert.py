class DAlembertStrategy:
    def __init__(self, base_bet, step=1):
        self.base_bet = base_bet
        if str(step) == "base_bet":
            self.step = 1.0
        elif str(step).startswith("base_bet_") and str(step).endswith("x"):
            try:
                self.step = float(str(step).split("_")[2][:-1])
            except (ValueError, IndexError):
                self.step = 1.0
        else:
            try:
                self.step = float(step)
            except (ValueError, TypeError):
                self.step = 1.0
        self.current_bet = base_bet
        self.level = 0

    def get_next_bet(self):
        step_value = self.step * self.base_bet
        self.current_bet = max(self.base_bet + self.level * step_value, self.base_bet)
        print(f"[DAlembertStrategy] base_bet={self.base_bet}, step={self.step}, step_value={step_value}, level={self.level}, current_bet={self.current_bet}")
        return self.current_bet

    def record_result(self, win: bool):
        if win:
            self.level = max(0, self.level - 1)
        else:
            self.level += 1

    def get_current_bet(self):
        return self.current_bet

    def reset(self):
        self.level = 0
        self.current_bet = self.base_bet 