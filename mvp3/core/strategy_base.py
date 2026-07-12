# core/strategy_base.py

class StrategyBase:
    def __init__(self, base_bet):
        """
        Initialize the strategy.
        :param base_bet: The initial bet amount.
        """
        self.base_bet = base_bet

    def get_next_bet(self):
        """
        Return the next bet amount.
        """
        raise NotImplementedError

    def record_result(self, win: bool):
        """
        Update internal state based on whether the last bet won or lost.
        :param win: True if last bet won, False if lost.
        """
        raise NotImplementedError

    def reset(self):
        """
        Reset internal state (e.g., between sessions).
        """
        raise NotImplementedError
