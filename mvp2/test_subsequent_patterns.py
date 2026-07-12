#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategy_engine import StrategyEngine

def test_subsequent_patterns():
    """Test subsequent pattern detection after first bet"""
    print("🧪 Testing Subsequent Pattern Detection")
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
    
    # Test sequence: [12, 36] -> WIN -> [7, 19] -> Pattern detected!
    test_sequence = [
        (12, False),  # Street 4
        (36, False),  # Street 4
        (25, True),   # WIN! (25 is in street 1, same as 12)
        (7, False),   # Street 3
        (19, False),  # Street 3 - Should detect pattern!
    ]
    
    print(f"\n📊 Testing sequence: {[f'{n}(Street {strategy.strategy.number_to_street_index(n)})' for n, _ in test_sequence]}")
    print("Format: Number -> Street -> Pattern Detected -> Bet Labels")
    
    waiting_for_new_pattern = False
    last_processed_numbers = []
    
    for i, (number, is_win) in enumerate(test_sequence):
        # Check if this is a new number
        if number not in last_processed_numbers:
            # Process the number in the strategy
            if hasattr(strategy.strategy, 'record_result'):
                strategy.strategy.record_result(is_win, last_number=number)
            
            # Track this number to avoid reprocessing
            last_processed_numbers.append(number)
            if len(last_processed_numbers) > strategy.strategy.k:
                last_processed_numbers.pop(0)
            
            # Get street info
            if hasattr(strategy.strategy, 'number_to_street_index'):
                street = strategy.strategy.number_to_street_index(number)
            else:
                street = "N/A"
            
            print(f"Round {i+1}: {number} -> Street {street}")
            print(f"  -> Strategy numbers: {strategy.strategy.last_numbers}")
            print(f"  -> Last processed: {last_processed_numbers}")
            
            # Immediately check if pattern is detected
            if not waiting_for_new_pattern:
                bet_labels = strategy.get_bet_labels()
                if bet_labels:
                    print(f"  -> PATTERN DETECTED! Bet labels: {bet_labels}")
                    street_indices = [strategy.strategy.number_to_street_index(n) for n in strategy.strategy.last_numbers]
                    print(f"  -> Street indices: {street_indices}")
                    # Don't break, continue to see what happens next
                else:
                    print(f"  -> No pattern yet.")
            
            # If this was a win, check if we need to wait for new pattern
            if is_win:
                if hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins >= 2:
                    waiting_for_new_pattern = True
                    print(f"  -> 2 consecutive wins detected! Will wait for new k=2 pattern...")
                elif hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins == 1:
                    print(f"  -> First win detected! Continuing to place bets...")
            
            # Record the result in progression
            strategy.record_result(is_win)
        else:
            print(f"Round {i+1}: {number} -> Already processed, skipping...")
    
    print(f"\n✅ Test completed!")

if __name__ == "__main__":
    test_subsequent_patterns() 