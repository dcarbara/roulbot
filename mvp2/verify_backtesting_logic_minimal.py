
import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.backtesting import RouletteBacktester

def test_minimal():
    backtester = RouletteBacktester()
    try:
        print("Running minimal backtest...")
        result = backtester.backtest_strategy(
            strategy_name='red',
            base_bet=1.0,
            num_rounds=1
        )
        print("Success!")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_minimal()
