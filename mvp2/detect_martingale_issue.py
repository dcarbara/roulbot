
from core.strategy_engine import StrategyEngine, ROULETTE_NUMBER_MAPPINGS
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)

def test_martingale_defaults():
    print("--- Testing StrategyEngine with strategy_name='martingale' ---")
    # Initialize engine with just strategy name 'martingale'
    # logical expectation: it should use martingale progression and bet on something valid (e.g. Red)
    engine = StrategyEngine(strategy_name="martingale", base_bet=10.0)
    
    # 1. Check Progression Type
    print(f"Progression Class: {type(engine.progression).__name__}")
    if type(engine.progression).__name__ != "MartingaleStrategy":
        print("FAIL: Progression is not MartingaleStrategy (likely FlatStrategy default)")
    else:
        print("PASS: Progression is MartingaleStrategy")

    # 2. Check Bet Labels
    labels = engine.get_bet_labels()
    print(f"Bet Labels: {labels}")
    if "red" not in labels and "black" not in labels and "even" not in labels:
        # If labels are just ['martingale'], it's invalid unless 'martingale' is in MAPPINGS
        if labels[0] not in ROULETTE_NUMBER_MAPPINGS:
             print(f"FAIL: Label '{labels[0]}' is not a valid roulette bet mapping.")
        else:
             print(f"PASS: Label '{labels[0]}' is valid.")
    else:
        print("PASS: Valid labels found.")

    # 3. Check Progression on Loss
    print("\n--- Simulating Loss ---")
    current_bet = engine.get_current_bet()
    print(f"Initial Bet: {current_bet}")
    
    # Record a loss
    engine.record_result(win=False)
    
    next_bet = engine.get_current_bet()
    print(f"Next Bet after loss: {next_bet}")
    
    if next_bet == current_bet * 2:
        print("PASS: Bet doubled.")
    else:
        print(f"FAIL: Bet did not double. Expected {current_bet*2}, got {next_bet}")

if __name__ == "__main__":
    test_martingale_defaults()
