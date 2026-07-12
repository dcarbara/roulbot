#!/usr/bin/env python3

def test_auto_roulette_pattern_after_result():
    """
    Test that auto roulette correctly detects patterns immediately after recording results.
    This simulates the scenario where a pattern is detected after recording a win/loss result.
    """
    from core.strategy_engine import StrategyEngine
    
    print("🧪 Testing auto roulette pattern detection after result...")
    
    # Create strategy engine with dynamic_9street strategy
    strategy = StrategyEngine(
        strategy_name="dynamic_9street",
        base_bet=1.0,
        max_loss=100.0,
        progression_type="custom"
    )
    
    # Set k value to 2
    strategy.strategy.k = 2
    
    # Test sequence: [24, 10] -> WIN -> [10, 15] -> should detect pattern immediately
    test_sequence = [
        (24, False),  # First number
        (10, False),  # Second number - should trigger first pattern
        (15, True),   # Result - WIN, should reset and start new pattern
        (15, False),  # New number after win - should be added to new pattern
    ]
    
    waiting_for_new_pattern = False
    pattern_detected_after_result = False
    
    print(f"Initial strategy numbers: {strategy.strategy.last_numbers}")
    
    for i, (number, is_win) in enumerate(test_sequence):
        print(f"\n--- Round {i+1}: {number} -> {'WIN' if is_win else 'LOSS'} ---")
        
        # Check if we should wait for new pattern
        if waiting_for_new_pattern:
            print(f"  -> WAITING for new k=2 pattern...")
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
        
        print(f"  Before processing: bet_labels={bet_labels}, numbers={strategy.strategy.last_numbers}")
        
        # Process the number in the strategy
        if hasattr(strategy.strategy, 'record_result'):
            strategy.strategy.record_result(is_win, last_number=number)
        
        # Record the result in progression
        strategy.record_result(is_win)
        
        print(f"  After processing: numbers={strategy.strategy.last_numbers}")
        
        # Check for patterns immediately after recording result (unless waiting for new pattern)
        if not waiting_for_new_pattern:
            bet_labels = strategy.get_bet_labels()
            if bet_labels:
                print(f"  ✅ Pattern detected after result! Bet labels: {bet_labels}")
                pattern_detected_after_result = True
            else:
                print(f"  ❌ No pattern detected after result, waiting for more numbers...")
        
        # If this was a win, check if we need to wait for new pattern
        if is_win:
            # Check if this is the second consecutive win
            if hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins >= 2:
                waiting_for_new_pattern = True
                print(f"  🔄 2 consecutive wins detected! Waiting for new k=2 pattern...")
            elif hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins == 1:
                print(f"  ✅ First win detected! Continuing to place bets...")
            else:
                # This shouldn't happen, but just in case
                waiting_for_new_pattern = True
                print(f"  🔄 WIN detected! Waiting for new k=2 pattern...")
        
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
        
        print(f"  Summary: Street {street} -> {bet_labels} -> {'WIN' if is_win else 'LOSS'} -> {prog_state} -> Next bet: ${strategy.get_next_bet()}")
        print(f"  Pattern detected after result: {pattern_detected_after_result}")
    
    print(f"\n🎯 Final test results:")
    print(f"  Strategy numbers: {strategy.strategy.last_numbers}")
    print(f"  Pattern detected after result: {pattern_detected_after_result}")
    print(f"  Waiting for new pattern: {waiting_for_new_pattern}")
    
    # Verify the expected behavior
    expected_pattern_detected = True  # Should detect pattern after result
    expected_waiting = False  # Should not be waiting after processing all numbers
    
    success = (pattern_detected_after_result == expected_pattern_detected and 
               waiting_for_new_pattern == expected_waiting)
    
    if success:
        print("✅ Test PASSED: Pattern detection after result works correctly!")
    else:
        print("❌ Test FAILED: Pattern detection after result not working as expected!")
        print(f"  Expected pattern_detected_after_result={expected_pattern_detected}, got={pattern_detected_after_result}")
        print(f"  Expected waiting_for_new_pattern={expected_waiting}, got={waiting_for_new_pattern}")
    
    return success

if __name__ == "__main__":
    test_auto_roulette_pattern_after_result() 