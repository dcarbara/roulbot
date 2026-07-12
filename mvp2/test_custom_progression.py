#!/usr/bin/env python3

from core.strategies.custom_progression import CustomProgressionStrategy

def test_custom_progression():
    """Test the custom progression strategy"""
    print("🧪 Testing Custom Progression Strategy")
    print("=" * 50)
    
    # Initialize with base bet of 10
    progression = CustomProgressionStrategy(base_bet=10.0)
    print(f"Initial bet: {progression.get_next_bet()}")
    
    # Test sequence: L, L, W, W, L, W, W, W
    test_sequence = [
        ("LOSS", False),
        ("LOSS", False), 
        ("WIN", True),
        ("WIN", True),
        ("LOSS", False),
        ("WIN", True),
        ("WIN", True),
        ("WIN", True)
    ]
    
    print("\n📊 Testing progression sequence:")
    print("Format: Result -> Bet Amount (consecutive_wins, consecutive_losses)")
    
    for i, (result, is_win) in enumerate(test_sequence, 1):
        current_bet = progression.get_next_bet()
        print(f"Round {i}: {result} -> ${current_bet} (wins: {progression.consecutive_wins}, losses: {progression.consecutive_losses})")
        
        progression.record_result(is_win)
    
    print("\n✅ Test completed!")
    print(f"Final state: bet=${progression.get_next_bet()}, wins={progression.consecutive_wins}, losses={progression.consecutive_losses}")

if __name__ == "__main__":
    test_custom_progression() 