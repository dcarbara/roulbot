import sys
import os
import logging

# Add 'mvp2' to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from core.analysis.regime_detector import RegimeDetector
from core.ranking_engine import RankingEngine

def test_multi_regime_filtering():
    print("\n--- Testing Context-Aware Regime Filtering ---")
    
    detector = RegimeDetector()
    
    # 1. Create a history that is:
    # Colors: CHOPPY (R, B, R, B...)
    # Dozens: TRENDING (1st, 1st, 1st...)
    
    # Numbers: 1 (Red, 1st12), 2 (Black, 1st12), 3 (Red, 1st12), 4 (Black, 1st12), 5 (Red, 1st12)
    # Colors: R, B, R, B, R -> Chop
    # Dozens: 1st, 1st, 1st, 1st, 1st -> Trend
    
    mixed_history = [1, 2, 3, 4, 1, 2, 3, 4, 1, 2] 
    
    regimes = detector.detect_all_regimes(mixed_history)
    print(f"Detected Regimes: {regimes}")
    
    assert regimes["Colors"] == "CHOPPY"
    assert regimes["Dozens"] == "TRENDING"
    
    # 2. Define Strategies
    custom_strategies = {
        "ColorChalupa": {
            "labels": ["red"], # Infers "Colors" -> CHOPPY
            "regime_tags": ["TRENDING"] # Explicitly wants TRENDING
        },
        "DozenDominator": {
            "labels": ["1st12"], # Infers "Dozens" -> TRENDING
            "regime_tags": ["TRENDING"] # Wants TRENDING
        },
        "NeutralNancy": {
            "labels": ["red"],
            "regime_tags": ["NEUTRAL"]
        }
    }
    
    engine = RankingEngine(custom_strategies=custom_strategies)
    # Mock simulation
    engine._simulate_strategy = lambda n, h, b: {'name': n, 'score': 10} 
    engine._calculate_scores = lambda r: r
    
    print("Ranking with mixed history...")
    candidates = ["ColorChalupa", "DozenDominator", "NeutralNancy"]
    results = engine.rank_strategies(candidates, mixed_history, filter_by_regime=True)
    result_names = [r['name'] for r in results]
    
    print(f"Accepted Strategies: {result_names}")
    
    # Expectations:
    # ColorChalupa: Targets Colors (CHOPPY). Wants TRENDING. Mismatch -> Reject.
    # DozenDominator: Targets Dozens (TRENDING). Wants TRENDING. Match -> Accept.
    # NeutralNancy: Neutral -> Accept.
    
    assert "ColorChalupa" not in result_names
    assert "DozenDominator" in result_names
    assert "NeutralNancy" in result_names
    
    print("✅ Context-Aware Logic Verified!")

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    test_multi_regime_filtering()
