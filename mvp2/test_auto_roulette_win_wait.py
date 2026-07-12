#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategy_engine import StrategyEngine

def test_auto_roulette_win_wait():
    """Test that auto roulette waits for new pattern after wins"""
    print("🧪 Testing Auto Roulette Win Wait Behavior")
    print("=" * 50)
    
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
    
    # Test sequence: [1, 3] -> WIN -> [22] -> [22, 15] -> WIN -> [7]
    test_sequence = [
        (1, False),   # Loss
        (3, False),   # Loss  
        (15, True),   # WIN! (15 is in street 1, same as 3)
        (22, False),  # Loss (should wait for new pattern)
        (13, False),  # Loss (should wait for new pattern)
        (33, True),   # WIN! (33 is in street 3, same as 13)
        (7, False),   # Loss (should wait for new pattern)
    ]
    
    print(f"\n📊 Testing sequence: {[f'{n}({"W" if w else "L"})' for n, w in test_sequence]}")
    print("Format: Number -> Street -> Bet Labels -> Win/Loss -> Should Wait")
    
    waiting_for_new_pattern = False
    
    for i, (number, is_win) in enumerate(test_sequence):
        # Check if we should wait for new pattern
        if waiting_for_new_pattern:
            print(f"Round {i+1}: {number} -> WAITING for new k=2 pattern...")
            # Process the number in the strategy
            if hasattr(strategy.strategy, 'record_result'):
                strategy.strategy.record_result(is_win, last_number=number)
            
            # Check if we now have enough numbers to form a pattern
            if len(strategy.strategy.last_numbers) >= strategy.strategy.k:
                waiting_for_new_pattern = False
                print(f"  -> New pattern search completed! Ready to place bets.")
            
            # Record the result in progression
            strategy.record_result(is_win)
            continue
        
        # Get current bet info before processing
        current_bet = strategy.get_next_bet()
        bet_labels = strategy.get_bet_labels()
        
        # Process the number in the strategy
        if hasattr(strategy.strategy, 'record_result'):
            strategy.strategy.record_result(is_win, last_number=number)
        
        # Record the result in progression
        strategy.record_result(is_win)
        
        # Get progression state
        if hasattr(strategy.progression, 'consecutive_wins'):
            prog_state = f"wins={strategy.progression.consecutive_wins}, losses={strategy.progression.consecutive_losses}"
        else:
            prog_state = f"losses={strategy.progression.consecutive_losses}"
        
        # Get street info
        if hasattr(strategy.strategy, 'number_to_street_index'):
            street = strategy.strategy.number_to_street_index(number)
        else:
            street = "N/A"
        
        print(f"Round {i+1}: {number} -> Street {street} -> {bet_labels} -> {'WIN' if is_win else 'LOSS'} -> {prog_state} -> Next bet: ${strategy.get_next_bet()}")
        
        # If this was a win, set flag to wait for new pattern
        if is_win:
            waiting_for_new_pattern = True
            print(f"  -> WIN detected! Will wait for new k=2 pattern...")
    
    print(f"\n✅ Test completed!")
    print(f"Final waiting state: {waiting_for_new_pattern}")

if __name__ == "__main__":
    test_auto_roulette_win_wait() 