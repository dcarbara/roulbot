#!/usr/bin/env python3
"""
Test script to verify database functions work correctly
"""

from core.utils.db_utils import save_session_stats, get_aggregate_stats, init_db

def test_database_functions():
    print("🧪 Testing database functions...")
    
    # Initialize database
    print("📊 Initializing database...")
    init_db()
    
    # Test saving session stats
    print("💾 Testing save_session_stats...")
    try:
        save_session_stats(
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T11:00:00", 
            strategy="test_strategy",
            rounds_played=10,
            wins=6,
            losses=4,
            profit=25.50
        )
        print("✅ save_session_stats works!")
    except Exception as e:
        print(f"❌ save_session_stats failed: {e}")
        return False
    
    # Test getting aggregate stats
    print("📈 Testing get_aggregate_stats...")
    try:
        stats = get_aggregate_stats()
        print(f"✅ get_aggregate_stats works!")
        print(f"   Total sessions: {stats.get('total_sessions', 0)}")
        print(f"   Total rounds: {stats.get('total_rounds', 0)}")
        print(f"   Total wins: {stats.get('total_wins', 0)}")
        print(f"   Total losses: {stats.get('total_losses', 0)}")
        print(f"   Total profit: ${stats.get('total_profit', 0):.2f}")
    except Exception as e:
        print(f"❌ get_aggregate_stats failed: {e}")
        return False
    
    print("🎉 All database functions work correctly!")
    return True

if __name__ == "__main__":
    test_database_functions() 