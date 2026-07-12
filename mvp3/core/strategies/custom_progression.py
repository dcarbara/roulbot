class CustomProgressionStrategy:
    def __init__(self, base_bet: float):
        self.base_bet = base_bet
        self.current_bet = base_bet
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.total_loss = 0.0
        
    def record_result(self, win: bool):
        if win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
            self.total_loss -= self.current_bet  # Reduce total loss on win
            
            # After 2 consecutive wins, reset to base bet
            if self.consecutive_wins >= 2:
                self.current_bet = self.base_bet
                self.consecutive_wins = 0  # Reset win counter
            # For first win, keep same bet amount
            # (current_bet stays the same)
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
            self.total_loss += self.current_bet
            
            # Double the bet on loss
            self.current_bet *= 2
    
    def get_next_bet(self):
        return self.current_bet
    
    def get_current_bet(self):
        return self.current_bet
    
    def reset(self):
        self.current_bet = self.base_bet
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.total_loss = 0.0 