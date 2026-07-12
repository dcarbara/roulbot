
import customtkinter as ctk
import tkinter as tk
from gui.backtesting_gui import BacktestingGUI
try:
    import matplotlib.pyplot as plt
    from core.backtesting import BacktestResult
except ImportError:
    plt = None

class MockApp:
    def get_all_strategy_names(self):
        return ["martingale"]

def verify_gui():
    ctk.set_appearance_mode("Dark")
    root = ctk.CTk() # Use CTk window
    root.geometry("800x600")
    
    frame = ctk.CTkFrame(root)
    frame.pack(fill="both", expand=True)
    
    app = MockApp()
    gui = BacktestingGUI(frame, app=app)
    
    # Create dummy bet history
    bet_history = [
        {
            'round': 1, 'strategy': 'martingale', 'spin_result': 22, 
            'bet_amount': 1.0, 'result': 'WIN', 'payout': 2.0, 
            'pnl': 1.0, 'balance_after': 101.0
        },
        {
            'round': 2, 'strategy': 'martingale', 'spin_result': 5, 
            'bet_amount': 1.0, 'result': 'LOSS', 'payout': 0.0, 
            'pnl': -1.0, 'balance_after': 100.0
        }
    ]

    dummy_result = BacktestResult(
        strategy_name="test_strat",
        initial_balance=100.0,
        base_bet=1.0,
        total_rounds=2,
        total_wins=1,
        total_losses=1,
        win_rate=50.0,
        total_profit=0.0,
        max_profit=1.0,
        max_loss=-1.0,
        max_drawdown=1.0,
        consecutive_wins=1,
        consecutive_losses=1,
        final_balance=100.0,
        roi=0.0,
        sharpe_ratio=0.0,
        bet_history=bet_history,
        balance_history=[],
        session_duration=1.0
    )
    
    gui.results = {"test_strat": [dummy_result]}
    gui.analysis = {"test_strat": {
        'num_simulations': 1, 'avg_pnl': 0.0, 'total_pnl_all_sims': 0.0, 'roi_pct': 0.0,
        'max_drawdown': 1.0, 'bankruptcies': 0, 'bankruptcy_rate': 0.0, 'win_rate': 50.0, 'avg_rounds': 2
    }}

    # Trigger display
    print("📋 Triggering _display_summary...")
    gui._display_summary("test_strat")
    
    # Check content of detailed_text
    # CTkTextbox.get() works like Tkinter Text widget
    content = gui.detailed_text.get("1.0", "end")
    print(f"📝 Detailed Log Content (First 200 chars):\n{content[:200]}")
    
    if "Round" in content and "Strategy" in content and "101.0" in content:
        print("✅ Detailed log populated successfully with table headers and data.")
    else:
        print("❌ Detailed log missing expected data.")
        
    root.after(1000, root.destroy)
    root.mainloop()

if __name__ == "__main__":
    verify_gui()
