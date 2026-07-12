#!/usr/bin/env python3

def test_auto_roulette_comprehensive():
    """
    Comprehensive test that simulates the full auto roulette flow.
    Tests the scenario where the bot places the first bet correctly but then
    needs to wait for k numbers to hit the same street before placing the next bet.
    """
    from core.strategy_engine import StrategyEngine
    
    print("🧪 Testing comprehensive auto roulette flow...")
    
    # Create strategy engine with dynamic_9street strategy
    strategy = StrategyEngine(
        strategy_name="dynamic_9street",
        base_bet=1.0,
        max_loss=100.0,
        progression_type="custom"
    )
    
    # Set k value to 2
    strategy.strategy.k = 2
    
    # Test sequence that simulates the reported issue:
    # 1. [24, 10] -> should trigger first bet (both in street 4)
    # 2. [15] -> WIN -> should reset and start new pattern
    # 3. [15, 16] -> should trigger second bet (both in street 5)
    test_sequence = [
        (24, False),  # First number - street 4
        (10, False),  # Second number - street 4 -> should trigger first pattern
        (15, True),   # Result - WIN, should reset and start new pattern
        (15, False),  # New number after win - street 5
        (16, False),  # Second number - street 5 -> should trigger second pattern
    ]
    
    waiting_for_new_pattern = False
    pattern_detected_after_result = False
    bets_placed = 0
    
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
                street_indices = [strategy.strategy.number_to_street_index(n) for n in strategy.strategy.last_numbers]
                if len(set(street_indices)) == 1:
                    waiting_for_new_pattern = False
                    print(f"  -> New pattern detected! Ready to place bets.")
                else:
                    print(f"  -> Have {len(strategy.strategy.last_numbers)} numbers but no pattern yet (street indices: {street_indices})")
            
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
                bets_placed += 1
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
        print(f"  Bets placed so far: {bets_placed}")
    
    print(f"\n🎯 Final test results:")
    print(f"  Strategy numbers: {strategy.strategy.last_numbers}")
    print(f"  Pattern detected after result: {pattern_detected_after_result}")
    print(f"  Waiting for new pattern: {waiting_for_new_pattern}")
    print(f"  Total bets placed: {bets_placed}")
    
    # Verify the expected behavior
    expected_bets_placed = 2  # Should place 2 bets: one for [24,10] and one for [15,16]
    expected_pattern_detected = True  # Should detect pattern after result
    expected_waiting = False  # Should not be waiting after processing all numbers
    
    success = (bets_placed == expected_bets_placed and 
               pattern_detected_after_result == expected_pattern_detected and 
               waiting_for_new_pattern == expected_waiting)
    
    if success:
        print("✅ Test PASSED: Comprehensive auto roulette flow works correctly!")
        print(f"  ✅ Placed {bets_placed} bets as expected")
        print(f"  ✅ Pattern detection after result works")
        print(f"  ✅ Waiting logic works correctly")
    else:
        print("❌ Test FAILED: Comprehensive auto roulette flow not working as expected!")
        print(f"  Expected bets_placed={expected_bets_placed}, got={bets_placed}")
        print(f"  Expected pattern_detected_after_result={expected_pattern_detected}, got={pattern_detected_after_result}")
        print(f"  Expected waiting_for_new_pattern={expected_waiting}, got={waiting_for_new_pattern}")
    
    return success

if __name__ == "__main__":
    test_auto_roulette_comprehensive() 