class CustomStrategy:
    def __init__(self, labels, base_bet, max_loss, progression_type="flat", custom_bet_units=None, regime_tags=None):
        self.labels = labels
        self.base_bet = base_bet
        self.max_loss = max_loss
        self.progression_type = progression_type
        
        # Support for custom bet units per label
        self.custom_bet_units = custom_bet_units or {}
        
        # Regime tags for "Smart Ranking" filtering
        # e.g., ["TRENDING"] or ["CHOPPY"]. Default is ["NEUTRAL"] (always valid).
        self.regime_tags = regime_tags or ["NEUTRAL"]
        
        print(f"[CustomStrategy] Init with base_bet={base_bet}, progression={progression_type}, regimes={self.regime_tags}")
        if self.custom_bet_units:
            print(f"[CustomStrategy] Custom bet units: {self.custom_bet_units}")

    def get_next_bet(self):
        # This should not be used - progression is handled by StrategyEngine
        return self.base_bet

    # ✅ Alias for compatibility with other code
    def get_current_bet(self):
        # This should not be used - progression is handled by StrategyEngine
        return self.base_bet

    def record_result(self, won: bool):
        # This should not be used - progression is handled by StrategyEngine
        pass

    def get_labels(self):
        return self.labels
    
    def get_bet_amounts(self, current_progression_bet=None):
        """
        Returns a dictionary mapping labels to their bet amounts.
        If no custom units are set, uses the current_progression_bet for all labels.
        Otherwise, multiplies custom units by the current progression bet.
        """
        if current_progression_bet is None:
            current_progression_bet = self.base_bet
            
        if not self.custom_bet_units:
            return {label: current_progression_bet for label in self.labels}
        
        bet_amounts = {}
        for label in self.labels:
            if label in self.custom_bet_units:
                # Apply progression to units: units * current_progression_bet
                units = self.custom_bet_units[label]
                bet_amounts[label] = units * current_progression_bet
            else:
                bet_amounts[label] = current_progression_bet
        
        return bet_amounts
    
    def get_total_bet_amount(self, current_progression_bet=None):
        """
        Returns the total amount that will be bet across all labels.
        """
        bet_amounts = self.get_bet_amounts(current_progression_bet)
        return sum(bet_amounts.values())
