import sys
import os
import json
import logging

# Setup paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.ranking_engine import RankingEngine
from core.utils.db_utils import get_recent_winning_numbers

# Configure logging
logging.basicConfig(level=logging.ERROR) 
# Force UTF-8 for stdout
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def parse_strategies_string(strategies_str):
    """
    Parses the strategy string into a list of dicts with metadata.
    Format: name:prog|meta,name2:prog...
    """
    entries = []
    items = [s.strip() for s in strategies_str.split(',') if s.strip()]
    
    for item in items:
        # Split main parts
        parts = item.split('|')
        main_def = parts[0]
        extra_meta = parts[1:] if len(parts) > 1 else []
        
        # Parse Name:Progression
        if ':' in main_def:
            name, progression = main_def.split(':', 1)
        else:
            name = main_def
            progression = "default"
            
        entries.append({
            "name": name.strip(),
            "progression": progression.strip(),
            "meta": extra_meta,
            "raw": item
        })
    return entries

def run_preset_benchmark(preset_path):
    print(f"Loading preset: {preset_path}")
    
    try:
        with open(preset_path, 'r') as f:
            preset = json.load(f)
            
        strategies_str = preset.get('strategies_string', '')
        parsed_entries = parse_strategies_string(strategies_str)
        
        if not parsed_entries:
            print("No strategies found in preset.")
            return

        # Get unique names for ranking
        # We rank the *Names*.
        unique_names = list(set([e['name'] for e in parsed_entries]))
        
        print(f"Found {len(parsed_entries)} entries ({len(unique_names)} unique strategies).")
        
        # Load custom definitions from main config
        with open('config/config.json', 'r') as f:
            main_config = json.load(f)
        custom_strategies = main_config.get('custom_strategies', {})
        
        # Fetch DB History
        history_limit = 100
        history_rows = get_recent_winning_numbers(limit=history_limit)
        history_data = [row['number'] for row in history_rows]
        history_data.reverse() # Chronological
        
        if len(history_data) < 10:
            print("Not enough history in DB.")
            return
            
        # Run Ranking
        print("Running Ranking Engine (Flat Betting Simulation)...")
        print("Note: Rankings are based on pure predictive power (Flat bets) to ensure fair comparison.")
        
        engine = RankingEngine(custom_strategies=custom_strategies)
        ranked_results = engine.rank_strategies(unique_names, history_data)
        
        # Map results back to the preset list? 
        # The user wants "ranking score of each of the strat used"
        # Since entries might duplicate names (unlikely in rotation list but possible), 
        # we can just show the ranked table.
        
        # Prepare Report
        output_path = "preset_benchmark_report.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"BENCHMARK REPORT: {preset.get('name', 'Unknown Preset').upper()}\n")
            f.write(f"Source: {preset_path}\n")
            f.write(f"History: Last {len(history_data)} real spins\n")
            f.write("=" * 80 + "\n")
            f.write(f"{'RANK':<5} | {'STRATEGY':<15} | {'SCORE':<8} | {'PnL (Flat)':<12} | {'WIN RATE':<10} | {'PRESET PROGRESSION'}\n")
            f.write("-" * 80 + "\n")
            
            # Create lookup for results
            res_map = {r['name']: r for r in ranked_results}
            
            # We want to list them in RANKING order? Or Preset Order?
            # User asked: "ranking score of each of the strat from the list"
            # Ranking order is usually more useful to start with.
            
            for i, res in enumerate(ranked_results):
                name = res['name']
                # precise progression from preset?
                # Find matching entry in preset
                # If a name appears multiple times in preset, we list it once here? 
                # Or list all variants?
                # Let's list the ranked strategies, and show which progression is mapped in the preset.
                
                matched_entries = [e for e in parsed_entries if e['name'] == name]
                progs = ", ".join([e['progression'] for e in matched_entries])
                
                prefix = "*" if i == 0 else " "
                
                f.write(f"{prefix:<2}{i+1:<3} | {name:<15} | {res['score']:<8.2f} | {res['pnl']:<12.2f} | {res['win_rate']*100:<9.1f}% | {progs}\n")
                
            f.write("=" * 80 + "\n")
            f.write("\nDETAILED PRESET METADATA:\n")
            for e in parsed_entries:
                f.write(f"- Strategy: {e['name']}\n")
                f.write(f"  Progression: {e['progression']}\n")
                if e['meta']:
                    f.write(f"  Metadata: {e['meta']}\n")
                f.write("\n")
                
        print(f"Report generated: {output_path}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Hardcoded path for this task
    path = r"c:\Users\Yash Thadani\Desktop\Engineering\spinedge\engine\mvp2\config\rotation_presets\conservative.json"
    run_preset_benchmark(path)
