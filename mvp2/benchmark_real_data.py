import sys
import os
import json
import logging

# Setup paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.ranking_engine import RankingEngine
from core.utils.db_utils import get_recent_winning_numbers

# Configure logging
logging.basicConfig(level=logging.ERROR) # Suppress debug logs for cleaner output
# Force UTF-8 for stdout
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def run_benchmark():
    print("==================================================")
    print("   STRATEGY ROTATION BENCHMARK (Real DB Data)     ")
    print("==================================================")

    # 1. Fetch Real History from DB
    try:
        # Fetch last 100 numbers for a solid sample size
        history_limit = 100
        history_rows = get_recent_winning_numbers(limit=history_limit)
        
        # db_utils returns dicts, we need list of numbers
        # Note: get_recent_winning_numbers returns DESC order (newest first). 
        # For simulation, we usually want chronological order (oldest to newest)?
        # The RankingEngine just iterates. If we iterate [Newest, ..., Oldest], 
        # it means we simulate betting on the NEWEST number first using state derived from... nothing?
        # WAIT. RankingEngine `_simulate_strategy` iterates `for i, number in enumerate(history)`.
        # If history is [Newest -> Oldest], then we simulate betting on the Newest number first.
        # This is backwards! A strategy needs past numbers to predict future ones.
        # We MUST reverse the list to be Chronological [Oldest -> Newest].
        
        history_data = [row['number'] for row in history_rows]
        history_data.reverse() # Critical: Chronological order
        
        print(f"Loaded {len(history_data)} spins from 'winning_numbers.db'")
        if len(history_data) < 10:
            print("!  Not enough data in DB (need at least 10 spins). Play more spins first!")
            return

        print(f"Sample: {history_data[:10]} ...")

    except Exception as e:
        print(f"Error reading DB: {e}")
        return

    # 2. Load Strategies from Config
    try:
        with open('config/config.json', 'r') as f:
            config = json.load(f)
        
        custom_strategies = config.get('custom_strategies', {})
        
        # Candidate List: All Custom Strategies + some Standard ones
        candidates = list(custom_strategies.keys())
        # detailed comparison against specific standard ones like martingale
        candidates.append("martingale")
        candidates.append("dalembert")
        
        print(f"Benchmarking {len(candidates)} strategies...")
        
    except Exception as e:
        print(f"Error reading config: {e}")
        return

    # 3. Run Ranking Engine
    print("\nRunning simulations... (this may take a moment)")
    engine = RankingEngine(custom_strategies=custom_strategies)
    
    # We simulate starting with $1000
    results = engine.rank_strategies(candidates, history_data, start_balance=1000)

    # 4. Output Results to File
    output_path = "benchmark_results_direct.txt"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"STRATEGY ROTATION BENCHMARK (Real DB Data)\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"{'RANK':<5} | {'STRATEGY':<20} | {'SCORE':<8} | {'PnL ($)':<10} | {'WIN RATE':<8} | {'MAX DD':<8}\n")
            f.write("-" * 75 + "\n")
            
            for i, res in enumerate(results):
                prefix = ""
                if i == 0: prefix = "*"
                elif res['pnl'] > 0: prefix = "+"
                else: prefix = "-"
                
                line = f"{prefix:<2}{i+1:<3} | {res['name']:<20} | {res['score']:<8.2f} | {res['pnl']:<10.2f} | {res['win_rate']*100:<7.1f}% | {res['max_dd']*100:<7.1f}%"
                f.write(line + "\n")
            
            f.write("=" * 50 + "\n")
            if results:
                top = results[0]
                f.write(f"\nCONCLUSION: Based on the last {history_limit} spins,\n")
                f.write(f"The best strategy to use RIGHT NOW is: '{top['name']}'\n")
                f.write("Enable 'smart_ranking' in Bot Control to automate this selection.\n")
                
        print(f"Benchmark complete. Results saved to {output_path}")

    except Exception as e:
        print(f"Error writing output file: {e}")

if __name__ == "__main__":
    run_benchmark()
