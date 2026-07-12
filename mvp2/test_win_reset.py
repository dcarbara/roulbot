#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategies.dynamic_9street import DynamicNineStreetStrategy

def test_win_reset_behavior():
    """Test that the strategy resets after wins"""
    print("🧪 Testing Win Reset Behavior")
    print("=" * 40)
    
    strategy = DynamicNineStreetStrategy(base_bet=10.0, k=2)
    
    # Test sequence: [1, 3] -> WIN -> [22] -> [22, 15] -> WIN -> [7]
    test_sequence = [
        ("Add 1", 1, False),
        ("Add 3", 3, False),
        ("WIN with 3", 3, True),  # This should reset
        ("Add 22", 22, False),
        ("Add 15", 15, False),
        ("WIN with 15", 15, True),  # This should reset
        ("Add 7", 7, False),
    ]
    
    print("📊 Testing sequence with wins:")
    print("Format: Action -> Numbers -> Labels -> Pattern Detected")
    
    for action, number, is_win in test_sequence:
        strategy.record_result(is_win, last_number=number)
        labels = strategy.get_labels()
        
        # Check if pattern is detected
        if len(strategy.last_numbers) >= strategy.k:
            street_indices = [strategy.number_to_street_index(n) for n in strategy.last_numbers]
            pattern_detected = len(set(street_indices)) == 1
        else:
            pattern_detected = False
        
        print(f"{action}: numbers={strategy.last_numbers} -> labels={labels} -> pattern={'YES' if pattern_detected else 'NO'}")

if __name__ == "__main__":
    test_win_reset_behavior() 