
import sys
import os

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from core.backtesting import RouletteBacktester
from core.strategy_engine import StrategyEngine
import io

def verify_win_logic():
    # Capture stdout
    old_stdout = sys.stdout
    sys.stdout = mystdout = io.StringIO()
    
    print("Verifying Backtest Win/Loss Logic...")
    
    # Setup Backtester
    backtester = RouletteBacktester()
    
    # Create explicit historical data
    # Round 1: Red (1), Win for Martingale (bets Red usually or depends on default)
    # Round 2: Black (2), Loss for Martingale (assuming it bets Red)
    # Note: StrategyEngine default 'martingale' typically bets on 'Red'? 
    # Let's check or configure it. Standard Martingale usually has a target.
    # If strategy defaults to Red, 1 is Win, 2 is Loss.
    
    historical_data = [
        {'round': 1, 'outcome': 1, 'is_win': True, 'timestamp': None}, # 1 is Red
        {'round': 2, 'outcome': 2, 'is_win': False, 'timestamp': None}, # 2 is Black
    ]
    
    # Create a custom strategy config if needed to ensure it bets on Red
    # But standard 'martingale' preset usually targets Red or Black.
    # Let's assume default behavior or inspect StrategyEngine.
    
    print("Running backtest with hardcoded outcomes [1 (Red), 2 (Black)]...")
    # Use "Red" as strategy name so it maps to Red numbers. 
    # progression_type defaults to "martingale" if not specified? 
    # Check default in backtest_strategy: progression_type="martingale"
    result = backtester.backtest_strategy(
        strategy_name="Red",
        base_bet=10.0,
        initial_balance=1000.0,
        num_rounds=2,
        historical_data_override=historical_data
    )
    
    print(f"\nResult Summary:")
    print(f"Total Rounds: {result.total_rounds}")
    print(f"Total Wins: {result.total_wins}")
    print(f"Total Losses: {result.total_losses}")
    print(f"Final Balance: {result.final_balance}")
    
    print("\nDetailed Bet History:")
    for bet in result.bet_history:
        print(f"Round {bet['round']}: Bet=${bet['bet_amount']} on {bet['strategy']} | Spin={bet['spin_result']} -> Result={bet['result']} (PnL={bet['pnl']})")
        
    # Validation
    # Round 1: Bet 10 on Red. Spin 1 (Red). Win. Payout 20 (stake+win). PnL +10. Bal 1010.
    # Round 2: Bet 10 on Red. Spin 2 (Black). Loss. Payout 0. PnL -10. Bal 1000.
    # Note: Martingale resets on Win. So Round 2 is base bet.
    
    # Let's test a Loss then Win sequence to check progression and payout
    # Round 1: Black (Loss). Bet 10. Bal 990. 
    # Round 2: Red (Win). Bet 20 (Doubled). Win 40. PnL +20. Bal 1010.
    
    # Adjust test data for progression check
    historical_data_2 = [
        {'round': 1, 'outcome': 2, 'is_win': False, 'timestamp': None}, # Black (Loss)
        {'round': 2, 'outcome': 1, 'is_win': True, 'timestamp': None}, # Red (Win)
    ]
    
    print("\nRunning Sequence 2 (Loss -> Win)...")
    result2 = backtester.backtest_strategy(
        strategy_name="Red",
        base_bet=10.0,
        initial_balance=1000.0,
        num_rounds=2,
        historical_data_override=historical_data_2
    )

    for bet in result2.bet_history:
        print(f"Round {bet['round']}: Bet=${bet['bet_amount']} | Spin={bet['spin_result']} -> PnL={bet['pnl']} | Bal={bet['balance_after']}")

    if result2.final_balance == 1010.0:
        print("\nVerification SUCCESS: PnL and Balance calculated correctly with progression.")
    else:
        print(f"\nVerification FAILED: Expected Final Balance 1010.0, got {result2.final_balance}")

    output = mystdout.getvalue()
    sys.stdout = old_stdout
    
    with open("validation_debug.log", "w", encoding="utf-8") as f:
        f.write(output)
    
    print(output)

if __name__ == "__main__":
    verify_win_logic()
