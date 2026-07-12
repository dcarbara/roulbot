import sys
import os
import logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ranking_engine import RankingEngine

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_ranking_logic():
    print("--- Testing Ranking Engine Logic ---")
    
    # Mock History: 
    # Create a sequence where RED (e.g. 1, 3, 5) wins a lot.
    # 10 Red numbers, 2 Black numbers.
    # Red numbers: 1, 3, 5, 7, 9, 12, 14, 16, 18, 19
    # Black numbers: 2, 4
    history = [1, 3, 5, 7, 9, 2, 12, 14, 4, 16, 18, 19] 
    
    # We need strategies to test.
    # Assuming "martingale" places bets on Red/Black or similar?
    # Actually, standard martingale usually bets on Red by default or configurable.
    # Let's rely on standard strategies if available.
    # If not, we might fail if 'martingale' isn't in presets.
    
    # Let's check what built-in presets exist.
    # from config.presets import get_preset_names -> likely ["martingale", "dalembert", ...]
    
    # Load config to get custom strategies
    try:
        import json
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        custom_strategies = config.get('custom_strategies', {})
        
        # Use strategies from the config if available, otherwise fallback
        # Let's try to grab a mix of custom and standard
        strategies_to_test = ["martingale"] 
        if custom_strategies:
            # Add all custom strategies
            strategies_to_test.extend(list(custom_strategies.keys()))
            
        print(f"Loaded {len(custom_strategies)} custom strategies.")
    except Exception as e:
        print(f"Failed to load config: {e}")
        custom_strategies = {}
        strategies_to_test = ["martingale", "dalembert"]
    
    print(f"History: {history}")
    print(f"Strategies to Rank: {strategies_to_test}")
    
    engine = RankingEngine(custom_strategies=custom_strategies)
    results = engine.rank_strategies(strategies_to_test, history)
    
    print("\n--- Results ---")
    for i, res in enumerate(results):
        print(f"#{i+1} {res['name']} | Score: {res['score']:.4f} | PnL: {res['pnl']} | WinRate: {res['win_rate']:.2f}")

    if results:
        top_pick = results[0]
        print(f"\nTop Pick: {top_pick['name']}")
    else:
        print("No results returned - check StrategyEngine availability.")

if __name__ == "__main__":
    test_ranking_logic()
