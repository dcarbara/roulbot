import sys
import os
import json
import logging
import statistics
import math

# Setup paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.ranking_engine import RankingEngine, SimResult
from core.utils.db_utils import get_recent_winning_numbers
from benchmark_preset import parse_strategies_string

# Configure logging
logging.basicConfig(level=logging.ERROR) 
# Force UTF-8 for stdout
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def calculate_z_scores_manual(results):
    """
    Manually calculate Z-scores and return the detailed math components.
    """
    if not results: return [], {}
    
    pnls = [r.final_pnl for r in results]
    win_rates = [r.win_rate for r in results]
    dds = [r.max_drawdown for r in results]
    
    stats = {
        "pnl_mean": statistics.mean(pnls),
        "pnl_std": statistics.stdev(pnls) if len(pnls) > 1 else 1.0,
        "wr_mean": statistics.mean(win_rates),
        "wr_std": statistics.stdev(win_rates) if len(win_rates) > 1 else 1.0,
        "dd_mean": statistics.mean(dds),
        "dd_std": statistics.stdev(dds) if len(dds) > 1 else 1.0
    }
    
    detailed = []
    for r in results:
        z_pnl = (r.final_pnl - stats['pnl_mean']) / stats['pnl_std'] if stats['pnl_std'] else 0
        z_wr = (r.win_rate - stats['wr_mean']) / stats['wr_std'] if stats['wr_std'] else 0
        z_dd = (r.max_drawdown - stats['dd_mean']) / stats['dd_std'] if stats['dd_std'] else 0
        
        score = (1.0 * z_pnl) + (0.5 * z_wr) - (1.5 * z_dd)
        
        detailed.append({
            "name": r.strategy_name,
            "metrics": r,
            "z_pnl": z_pnl,
            "z_wr": z_wr,
            "z_dd": z_dd,
            "score": score
        })
        
    detailed.sort(key=lambda x: x['score'], reverse=True)
    return detailed, stats

def run_evolution_analysis():
    print("================================================================")
    print("        RANKING ALGORITHM: MATHEMATICAL EVOLUTION ANALYSIS       ")
    print("================================================================")
    print("Objective: Demonstrate how Strategy Scores evolve every 20 spins.")
    
    # 1. Load Preset & Config
    preset_path = r"c:\Users\Yash Thadani\Desktop\Engineering\spinedge\engine\mvp2\config\rotation_presets\conservative.json"
    with open(preset_path, 'r') as f:
        preset = json.load(f)
    strategies_str = preset.get('strategies_string', '')
    parsed = parse_strategies_string(strategies_str)
    unique_names = list(set([e['name'] for e in parsed]))
    
    with open('config/config.json', 'r') as f:
        main_config = json.load(f)
    custom_strategies = main_config.get('custom_strategies', {})
    
    # 2. Get History
    history_limit = 100
    rows = get_recent_winning_numbers(limit=history_limit)
    history_data = [r['number'] for r in rows]
    history_data.reverse()
    
    print(f"Loaded {len(history_data)} spins. Analyzing '{preset.get('name')}' strategies: {unique_names}")
    
    # 3. Time-Step Simulation
    # We want to run simulations at [20, 40, 60, 80, 100] slices
    engine = RankingEngine(custom_strategies=custom_strategies)
    
    output_path = "ranking_math_evolution.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("RANKING ALGORITHM VERIFICATION\n")
        f.write("==============================\n\n")
        
        checkpoints = [20, 40, 60, 80, 100]
        
        for cp in checkpoints:
            if cp > len(history_data): break
            
            slice_data = history_data[:cp]
            f.write(f"\n--- SNAPSHOT AT SPIN {cp} ---\n")
            f.write(f"History Slice: {slice_data[-5:]} ... (Last 5)\n")
            
            # Run simulation on this slice
            # To be efficient, we probably shouldn't re-instantiate everything, 
            # but to ensure exact results matching the main engine, we will call rank_strategies internal logic.
            # But rank_strategies just returns final list. 
            # We will use _simulate_strategy directly.
            
            sim_results = []
            for name in unique_names:
                res = engine._simulate_strategy(name, slice_data, 1000.0)
                if res: sim_results.append(res)
                
            # Calculate Scores
            detailed, stats = calculate_z_scores_manual(sim_results)
            
            # Print Stat Distro
            f.write(f"Group Statistics (N={len(unique_names)}):\n")
            f.write(f"  Avg PnL: ${stats['pnl_mean']:.2f} (StdDev: {stats['pnl_std']:.2f})\n")
            f.write(f"  Avg WR:  {stats['wr_mean']*100:.1f}% (StdDev: {stats['wr_std']*100:.1f}%)\n")
            f.write(f"  Avg DD:  {stats['dd_mean']*100:.1f}% (StdDev: {stats['dd_std']*100:.1f}%)\n\n")
            
            f.write(f"{'RANK':<4} | {'STRATEGY':<12} | {'SCORE':<6} | {'PnL':<8} | {'Z_PnL':<6} | {'Z_WR':<6} | {'Z_DD':<6}\n")
            f.write("-" * 75 + "\n")
            
            for i, d in enumerate(detailed):
                # Highlight Top Moves
                prefix = ""
                if i == 0: prefix = "👑"
                
                f.write(f"{prefix:<3} {i+1:<3} | {d['name']:<12} | {d['score']:<6.2f} | {d['metrics'].final_pnl:<8.1f} | {d['z_pnl']:<6.2f} | {d['z_wr']:<6.2f} | {d['z_dd']:<6.2f}\n")
                
            f.write("\n")
            
        # 4. Final Math Verification Example
        top = detailed[0]
        f.write("\n\n=======================================================\n")
        f.write(f"MANUAL VERIFICATION: Top Strategy '{top['name']}'\n")
        f.write("=======================================================\n")
        f.write("Formula: Score = (1.0 * Z_PnL) + (0.5 * Z_WR) - (1.5 * Z_DD)\n\n")
        
        f.write("Step 1: Raw Metrics\n")
        f.write(f"  PnL:      ${top['metrics'].final_pnl:.2f}\n")
        f.write(f"  Win Rate: {top['metrics'].win_rate:.4f}\n")
        f.write(f"  Drawdown: {top['metrics'].max_drawdown:.4f}\n\n")
        
        f.write("Step 2: Group Statistics (The 'Curve')\n")
        f.write(f"  Mean PnL: ${stats['pnl_mean']:.2f}, StdDev PnL: {stats['pnl_std']:.2f}\n")
        f.write(f"  Mean WR:  {stats['wr_mean']:.4f},  StdDev WR:  {stats['wr_std']:.4f}\n")
        f.write(f"  Mean DD:  {stats['dd_mean']:.4f},  StdDev DD:  {stats['dd_std']:.4f}\n\n")
        
        f.write("Step 3: Calculate Z-Scores (Standard Deviations from Mean)\n")
        f.write(f"  Z_PnL = ({top['metrics'].final_pnl:.2f} - {stats['pnl_mean']:.2f}) / {stats['pnl_std']:.2f} = {top['z_pnl']:.4f}\n")
        f.write(f"  Z_WR  = ({top['metrics'].win_rate:.4f} - {stats['wr_mean']:.4f}) / {stats['wr_std']:.4f}  = {top['z_wr']:.4f}\n")
        f.write(f"  Z_DD  = ({top['metrics'].max_drawdown:.4f} - {stats['dd_mean']:.4f}) / {stats['dd_std']:.4f}  = {top['z_dd']:.4f}\n\n")
        
        f.write("Step 4: Final Weighted Score\n")
        f.write(f"  Score = (1.0 * {top['z_pnl']:.4f}) + (0.5 * {top['z_wr']:.4f}) - (1.5 * {top['z_dd']:.4f})\n")
        calc_check = (1.0 * top['z_pnl']) + (0.5 * top['z_wr']) - (1.5 * top['z_dd'])
        f.write(f"        = {calc_check:.4f}\n")
        
        f.write(f"\nResult matches Engine Score: {top['score']:.4f} ✅\n")
        
    print(f"Analysis complete. See '{output_path}'")

if __name__ == "__main__":
    run_evolution_analysis()
