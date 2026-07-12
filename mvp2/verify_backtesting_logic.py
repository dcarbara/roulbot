
import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.backtesting import RouletteBacktester
from core.strategy_engine import StrategyEngine

def test_backtest_outcome_data():
    backtester = RouletteBacktester()
    
    # Define a simple strategy config
    config = {
        'strategy_name': 'red',
        'base_bet': 1.0,
        'initial_balance': 100.0,
        'num_rounds': 10,
        'progression_type': 'martingale'
    }
    
    try:
        print("Running backtest...")
        result = backtester.backtest_strategy(
            strategy_name=config['strategy_name'],
            base_bet=config['base_bet'],
            initial_balance=config['initial_balance'],
            num_rounds=config['num_rounds'],
            progression_type=config['progression_type']
        )
        print(f"Backtest successful. ROI: {result.roi:.2f}%")
        print(f"Final Balance: {result.final_balance:.2f}")
        
        # Check if bet_history has data
        if result.bet_history:
            print(f"First round result: {result.bet_history[0]['result']}")
            print(f"Outcome: {result.bet_history[0]['spin_result']}")
        else:
            print("Error: bet_history is empty")
            
    except Exception as e:
        print(f"Error caught: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_backtest_outcome_data()
