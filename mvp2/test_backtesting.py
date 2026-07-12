#!/usr/bin/env python3
"""
Test script for the roulette backtesting system
"""

from core.backtesting import RouletteBacktester

def test_single_strategy():
    """Test a single strategy backtest"""
    print("🎰 Testing Single Strategy Backtest")
    print("=" * 50)
    
    backtester = RouletteBacktester()
    
    # Test Martingale strategy
    result = backtester.backtest_strategy(
        strategy_name="martingale",
        base_bet=0.1,
        initial_balance=100.0,
        num_rounds=50,
        progression_type="martingale",
        max_loss=20.0,
        seed=42
    )
    
    print(f"Strategy: {result.strategy_name}")
    print(f"Base Bet: ${result.base_bet}")
    print(f"Total Rounds: {result.total_rounds}")
    print(f"Wins: {result.total_wins}, Losses: {result.total_losses}")
    print(f"Win Rate: {result.win_rate:.1f}%")
    print(f"Total Profit: ${result.total_profit:.2f}")
    print(f"ROI: {result.roi:.1f}%")
    print(f"Final Balance: ${result.final_balance:.2f}")
    print(f"Max Drawdown: ${result.max_drawdown:.2f}")
    print(f"Sharpe Ratio: {result.sharpe_ratio:.3f}")
    print()

def test_multiple_strategies():
    """Test multiple strategies comparison"""
    print("🎰 Testing Multiple Strategies Comparison")
    print("=" * 50)
    
    backtester = RouletteBacktester()
    
    # Define strategies to test
    strategies = [
        {
            'strategy_name': 'martingale',
            'base_bet': 0.1,
            'progression_type': 'martingale',
            'max_loss': 20.0
        },
        {
            'strategy_name': 'fibonacci',
            'base_bet': 0.1,
            'progression_type': 'fibonacci',
            'max_loss': 20.0
        },
        {
            'strategy_name': 'flat',
            'base_bet': 0.1,
            'progression_type': 'flat',
            'max_loss': 20.0
        }
    ]
    
    # Run multiple backtests
    results = backtester.run_multiple_backtests(
        strategy_configs=strategies,
        num_simulations=5,
        rounds_per_simulation=30
    )
    
    # Analyze results
    analysis = backtester.analyze_results(results)
    
    # Print comparison
    print("📊 Strategy Comparison:")
    print("-" * 60)
    print(f"{'Strategy':<15} {'Avg Profit':<12} {'Win Rate':<10} {'ROI %':<8} {'Success %':<10}")
    print("-" * 60)
    
    for strategy_name, stats in analysis.items():
        print(f"{strategy_name:<15} "
              f"${stats['avg_profit']:<11.2f} "
              f"{stats['avg_win_rate']:<9.1f}% "
              f"{stats['avg_roi']:<7.1f}% "
              f"{stats['success_rate']:<9.1f}%")
    
    print()

def test_report_generation():
    """Test report generation"""
    print("🎰 Testing Report Generation")
    print("=" * 50)
    
    backtester = RouletteBacktester()
    
    # Run a quick test
    strategies = [
        {
            'strategy_name': 'martingale',
            'base_bet': 0.1,
            'progression_type': 'martingale',
            'max_loss': 10.0
        }
    ]
    
    results = backtester.run_multiple_backtests(
        strategy_configs=strategies,
        num_simulations=3,
        rounds_per_simulation=20
    )
    
    analysis = backtester.analyze_results(results)
    
    # Generate report
    report = backtester.generate_report(results, analysis)
    print("Generated Report Preview (first 500 chars):")
    print("-" * 40)
    print(report[:500] + "..." if len(report) > 500 else report)
    print()

def main():
    """Run all tests"""
    print("🚀 Starting Roulette Backtesting Tests")
    print("=" * 60)
    
    try:
        test_single_strategy()
        test_multiple_strategies()
        test_report_generation()
        
        print("✅ All tests completed successfully!")
        print("\n💡 To use the full backtesting system:")
        print("   1. Run the main GUI: python main.py")
        print("   2. Go to the 'Strategy Backtesting' section")
        print("   3. Configure your strategy and run backtests")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main() 