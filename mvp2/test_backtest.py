import sys
import os
import json
import logging
sys.path.append(os.path.abspath('.'))

logging.basicConfig(level=logging.INFO)

from core.backtesting import RouletteBacktester
from config.schema import load_config
from core.security.license_manager import get_license_manager

config = load_config()
custom_strategies = config.get('custom_strategies', {})

lm = get_license_manager()
print("License Active:", lm.is_licensed)
lm.DEBUG_BYPASS = True  # Force bypass just in case

backtester = RouletteBacktester()
historical_data = backtester.fetch_historical_data_from_db(limit=5000)

with open('config/rotation_presets/quant_conservative_mix.json', 'r') as f:
    rotation_config = json.load(f)

if 'strategies_string' in rotation_config:
    rotation_config['strategies'] = rotation_config['strategies_string'].split(',')

session_config = {
    "max_loss": 100.0,
    "profit_target": 0.0,
    "session_duration_minutes": 60,
    "session_ext_after_win": False,
    "session_ext_at_high": True,  # User's extension policy overriden
    "max_extension_rounds": 500,  # Generous extension rounds for backtest
    "extension_give_up_amount": 50.0  
}

print("Starting Backtest...")
result = backtester.backtest_strategy(
    strategy_name='quant_conservative_mix',
    base_bet=0.10,
    initial_balance=250.0,
    num_rounds=1000,
    max_loss=100.0,
    custom_strategies=custom_strategies,
    historical_data_override=historical_data,
    rotation_config=rotation_config,
    session_config=session_config
)

print(f'\n--- BACKTEST RESULTS ---')
print(f'Total Rounds: {result.total_rounds}')
print(f'Win Rate: {result.win_rate:.2f}%')
print(f'Total Profit: ${result.total_profit:.2f}')
print(f'Max Drawdown: ${result.max_drawdown:.2f}')
print(f'Final Balance: ${result.final_balance:.2f}')

with open('backtest_output.json', 'w') as f:
    json.dump(result.bet_history[:30], f, indent=4)
print("Bet history dumped to backtest_output.json")
