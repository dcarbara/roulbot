#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategies.custom_progression import CustomProgressionStrategy
from core.strategy_engine import StrategyEngine

def test_auto_roulette_progression():
    """Test the auto roulette progression with recent winning numbers"""
    print("🧪 Testing Auto Roulette Progression with Recent Numbers")
    print("=" * 60)
    
    # Recent winning numbers from the database
    recent_numbers = [1, 22, 3, 15, 17, 22, 13, 33, 11, 16]
    
    # Create strategy engine with dynamic 9-street and custom progression
    strategy = StrategyEngine(
        strategy_name="dynamic_9street",
        base_bet=10.0,
        max_loss=100.0,
        progression_type="custom",
        max_bet=100.0,
        max_consec_losses=5
    )
    
    # Set k value for dynamic 9-street
    if hasattr(strategy.strategy, 'k'):
        strategy.strategy.k = 2
        print(f"Set k value to: {strategy.strategy.k}")
    
    print(f"\n📊 Testing with recent numbers: {recent_numbers}")
    print("Format: Number -> Street -> Bet Labels -> Win/Loss -> Progression State")
    
    for i, number in enumerate(recent_numbers):
        # Get current bet info before processing
        current_bet = strategy.get_next_bet()
        bet_labels = strategy.get_bet_labels()
        
        # Process the number in the strategy
        if hasattr(strategy.strategy, 'record_result'):
            strategy.strategy.record_result(False, last_number=number)
        
        # Determine if this would be a win
        covered_numbers = strategy.get_covered_numbers()
        is_win = number in covered_numbers
        
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
    
    print(f"\n✅ Test completed!")
    print(f"Final progression state: {prog_state}")
    print(f"Next bet amount: ${strategy.get_next_bet()}")

def check_database_stats():
    """Check database statistics"""
    print("\n📊 Database Statistics")
    print("=" * 30)
    
    import sqlite3
    conn = sqlite3.connect('winning_numbers.db')
    cursor = conn.cursor()
    
    # Total records
    cursor.execute("SELECT COUNT(*) FROM winning_numbers")
    total = cursor.fetchone()[0]
    print(f"Total winning numbers recorded: {total}")
    
    # Recent activity
    cursor.execute("SELECT COUNT(*) FROM winning_numbers WHERE timestamp > datetime('now', '-1 hour')")
    recent = cursor.fetchone()[0]
    print(f"Numbers in last hour: {recent}")
    
    # Most common numbers
    cursor.execute("SELECT number, COUNT(*) as count FROM winning_numbers GROUP BY number ORDER BY count DESC LIMIT 5")
    common = cursor.fetchall()
    print(f"Most common numbers: {common}")
    
    conn.close()

if __name__ == "__main__":
    test_auto_roulette_progression()
    check_database_stats() 