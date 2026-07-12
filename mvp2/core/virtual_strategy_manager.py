import logging
from typing import Dict, Any, List
from core.strategy_engine import StrategyEngine

logger = logging.getLogger(__name__)

class VirtualStrategyManager:
    """
    Manages the background simulation of betting strategies.
    Tracks their virtual performance (PnL, streaks) to be used as triggers.
    """
    def __init__(self):
        self.strategies: Dict[str, StrategyEngine] = {}
        self.strategy_states: Dict[str, Dict[str, Any]] = {}

    def register_strategy(self, name: str, config: Dict[str, Any]):
        """
        Register a strategy for simulation.
        config should contain:
        - strategy_name (e.g., "martingale")
        - progression_type (e.g., "martingale")
        - base_bet
        - etc.
        """
        try:
            # Instantiate the strategy engine
            # Note: We need to handle how custom strategies are passed if needed.
            # For standard strategies, we can just init.
            engine = StrategyEngine(
                strategy_name=config.get("strategy_name"),
                base_bet=config.get("base_bet", 1.0),
                progression_type=config.get("progression_type", "flat"),
                # Add other params as needed
            )
            self.strategies[name] = engine
            self.strategy_states[name] = {
                "virtual_balance": 0.0,
                "current_win_streak": 0,
                "current_loss_streak": 0,
                "total_wins": 0,
                "total_losses": 0,
                "last_result": None
            }
            logger.info(f"Registered virtual strategy: {name}")
        except Exception as e:
            logger.error(f"Failed to register virtual strategy {name}: {e}")

    def update_all(self, winning_number: int):
        """
        Update all registered strategies with the latest winning number.
        """
        for name, engine in self.strategies.items():
            try:
                # 1. Determine what the strategy WOULD have bet
                # current_bet = engine.get_current_bet() # Amount
                # covered_numbers = engine.get_covered_numbers() # Set of numbers
                
                # Check for win/loss
                is_win = engine.is_winning_number(winning_number)
                bet_amount = engine.get_current_bet()
                
                # Update Engine State (Progression)
                engine.record_result(is_win)
                
                # Update Virtual Stats
                state = self.strategy_states[name]
                
                if is_win:
                    # Calculate win amount based on bet type logic?
                    # For simplicity, StrategyEngine's record_result handles internal progression state.
                    # We just track the generic PnL here.
                    # Ideally we need the payout ratio. 
                    # engine.get_total_bet_amount() ?
                    
                    # Approximating PnL:
                    # We need exact payouts. StrategyEngine has calculate_win_amount but it takes a list of bets.
                    # Let's use engine.record_result which is what matters for progression.
                    
                    state["current_win_streak"] += 1
                    state["current_loss_streak"] = 0
                    state["total_wins"] += 1
                    state["last_result"] = "WIN"
                    # rough PnL tracking if needed, otherwise streaks are main use case
                    
                else:
                    if bet_amount > 0: # Only count as loss if it actually bet
                        state["current_loss_streak"] += 1
                        state["current_win_streak"] = 0
                        state["total_losses"] += 1
                        state["last_result"] = "LOSS"
                
            except Exception as e:
                logger.error(f"Error updating virtual strategy {name}: {e}")

    def get_state(self, name: str) -> Dict[str, Any]:
        return self.strategy_states.get(name, {})

    def get_metric(self, name: str, metric: str) -> Any:
        state = self.get_state(name)
        if metric == "loss_streak":
            return state.get("current_loss_streak", 0)
        elif metric == "win_streak":
            return state.get("current_win_streak", 0)
        elif metric == "virtual_balance":
            return state.get("virtual_balance", 0.0)
        return 0
