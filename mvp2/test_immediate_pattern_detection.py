#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategy_engine import StrategyEngine

def test_immediate_pattern_detection():
    """Test immediate pattern detection after adding numbers"""
    print("🧪 Testing Immediate Pattern Detection")
    print("=" * 45)
    
    # Create strategy engine with dynamic 9-street and custom progression
    strategy = StrategyEngine(
        strategy_name="dynamic_9street",
        base_bet=10.0,
        max_loss=100.0,
        progression_type="custom",
        max_bet=100.0,
        max_consec_losses=10
    )
    
    # Set k value for dynamic 9-street
    if hasattr(strategy.strategy, 'k'):
        strategy.strategy.k = 2
        print(f"Set k value to: {strategy.strategy.k}")
    
    # Test sequence: [24, 10] -> Pattern detected! Both in Street 4
    test_sequence = [
        (24, False),  # Street 4
        (10, False),  # Street 4 - Pattern detected!
    ]
    
    print(f"\n📊 Testing sequence: {[f'{n}(Street {strategy.strategy.number_to_street_index(n)})' for n, _ in test_sequence]}")
    print("Format: Number -> Street -> Pattern Detected -> Bet Labels")
    
    waiting_for_new_pattern = False
    
    for i, (number, is_win) in enumerate(test_sequence):
        # Process the number in the strategy
        if hasattr(strategy.strategy, 'record_result'):
            strategy.strategy.record_result(is_win, last_number=number)
        
        # Get street info
        if hasattr(strategy.strategy, 'number_to_street_index'):
            street = strategy.strategy.number_to_street_index(number)
        else:
            street = "N/A"
        
        print(f"Round {i+1}: {number} -> Street {street}")
        
        # Immediately check if pattern is detected
        if not waiting_for_new_pattern:
            bet_labels = strategy.get_bet_labels()
            if bet_labels:
                print(f"  -> PATTERN DETECTED! Bet labels: {bet_labels}")
                print(f"  -> Strategy numbers: {strategy.strategy.last_numbers}")
                street_indices = [strategy.strategy.number_to_street_index(n) for n in strategy.strategy.last_numbers]
                print(f"  -> Street indices: {street_indices}")
                break
            else:
                print(f"  -> No pattern yet. Strategy numbers: {strategy.strategy.last_numbers}")
    
    print(f"\n✅ Test completed!")

if __name__ == "__main__":
    test_immediate_pattern_detection() 