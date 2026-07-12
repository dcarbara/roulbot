import os
import sys

# Add the root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from engine.mvp2.core.strategy_engine import StrategyEngine

def test_observation_mode():
    print("🧪 Running Observation Mode Test...")
    
    # 1. Initialize Engine with observation_trigger = 3
    # We will bet on "red" (from single_red strategy or similar, let's just use "flat" and "red" as a label)
    # Actually "red" isn't a built-in strategy string by default, let's use a standard strategy name like "single_red" if it exists, 
    # or just use random string and mock the 'get_covered_numbers'
    
    # Let's use a simple mock strategy string or just use 'red' which may fail gracefully to fallback mode
    engine = StrategyEngine(
        strategy_name="red", # This will just treat 'red' as the custom label if strategy not found
        base_bet=1.0,
        observation_trigger=3
    )
    
    # Override get_covered_numbers just for test if needed
    engine.get_covered_numbers = lambda: {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    
    print(f"Initial State - is_observing: {engine.is_observing}, trigger: {engine.observation_trigger}")
    assert engine.is_observing == True, "Engine should be observing initially"
    
    # Simulate spins
    # Spin 1: Black (2) - Miss 1
    engine.record_result(win=engine.is_winning_number(2), winning_number=2)
    print(f"Spin 1 (Miss 1) - misses: {engine.consecutive_misses}, is_observing: {engine.is_observing}")
    assert engine.consecutive_misses == 1
    
    # Spin 2: Red (3) - Hit (Reset)
    engine.record_result(win=engine.is_winning_number(3), winning_number=3)
    print(f"Spin 2 (Hit) - misses: {engine.consecutive_misses}, is_observing: {engine.is_observing}")
    assert engine.consecutive_misses == 0
    assert engine.is_observing == True, "Engine should still be observing after a premature hit"
    
    # Spin 3: Black (4) - Miss 1
    engine.record_result(win=engine.is_winning_number(4), winning_number=4)
    print(f"Spin 3 (Miss 1) - misses: {engine.consecutive_misses}, is_observing: {engine.is_observing}")
    
    # Spin 4: Black (6) - Miss 2
    engine.record_result(win=engine.is_winning_number(6), winning_number=6)
    print(f"Spin 4 (Miss 2) - misses: {engine.consecutive_misses}, is_observing: {engine.is_observing}")
    
    # Spin 5: Black (8) - Miss 3 (TRIGGER)
    engine.record_result(win=engine.is_winning_number(8), winning_number=8)
    print(f"Spin 5 (Miss 3) - misses: {engine.consecutive_misses}, is_observing: {engine.is_observing}")
    
    assert engine.is_observing == False, "Engine should stop observing and start betting after 3 consecutive misses!"
    print("✅ test_observation_mode passed!")

if __name__ == "__main__":
    test_observation_mode()
