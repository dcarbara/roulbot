# core/strategy_engine.py

from core.strategies.martingale import MartingaleStrategy
from core.strategies.flat import FlatStrategy
from core.strategies.custom import CustomStrategy
from core.strategies.fibonacci import FibonacciStrategy
from core.strategies.custom_sequence import CustomSequenceStrategy
from core.strategies.dalembert import DAlembertStrategy
from core.strategies.dynamic_progression import DynamicProgressionStrategy
from core.strategies.dynamic_9street import DynamicNineStreetStrategy
from core.strategies.dynamic_neighbors import DynamicNeighborsStrategy
from core.strategies.bias_adaptive import BiasAdaptiveStrategy
from core.strategies.dirichlet_bayes import DirichletBayesStrategy
from core.strategies.custom_progression import CustomProgressionStrategy
from core.strategies.pattern_follower import PatternFollowerStrategy
from core.strategies.composite import CompositeStrategy
from core.decision.rules import extract_delegate_names, parse_rule
from collections import OrderedDict
import logging
import os
import glob
import time
from core.encryption import decrypt_strategy_data
from core.security.license_manager import get_license_manager

logger = logging.getLogger(__name__)

# Roulette number mappings for different bet types
ROULETTE_NUMBER_MAPPINGS = OrderedDict({
    # --- Single Numbers (Straight Bets) ---
    **{str(n): [n] for n in range(0, 37)},
    "00": [0],

    # --- Dozens ---
    "1st12": list(range(1, 13)),
    "2nd12": list(range(13, 25)),
    "3rd12": list(range(25, 37)),

    # --- Columns ---
    "col1": [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34],
    "col2": [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35],
    "col3": [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36],

    # --- Colors ---
    "red":   [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36],
    "black": [2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35],

    # --- Even/Odd ---
    "even": [n for n in range(2, 37, 2)],
    "odd":  [n for n in range(1, 36, 2)],

    # --- High/Low ---
    "1to18": list(range(1, 19)),
    "19to36": list(range(19, 37)),

    # --- Streets (Rows of 3) ---
    **{f"{i}-{i+2}strt": list(range(i, i+3)) for i in range(1, 35, 3)},

    # --- Corners (Blocks of 4) ---
    # Format: "start-endcorner": [n1, n2, n3, n4]
    "1-5corner": [1, 2, 4, 5], "2-6corner": [2, 3, 5, 6], "4-8corner": [4, 5, 7, 8], "5-9corner": [5, 6, 8, 9],
    "7-11corner": [7, 8, 10, 11], "8-12corner": [8, 9, 11, 12], "10-14corner": [10, 11, 13, 14], "11-15corner": [11, 12, 14, 15],
    "13-17corner": [13, 14, 16, 17], "14-18corner": [14, 15, 17, 18], "16-20corner": [16, 17, 19, 20], "17-21corner": [17, 18, 20, 21],
    "19-23corner": [19, 20, 22, 23], "20-24corner": [20, 21, 23, 24], "22-26corner": [22, 23, 25, 26], "23-27corner": [23, 24, 26, 27],
    "25-29corner": [25, 26, 28, 29], "26-30corner": [26, 27, 29, 30], "28-32corner": [28, 29, 31, 32], "29-33corner": [29, 30, 32, 33],
    "31-35corner": [31, 32, 34, 35], "32-36corner": [32, 33, 35, 36],

    # --- Splits (Pairs of 2) ---
    "0-00split": [0, 0], "0-1split": [0, 1], "0-2split": [0, 2], "0-3split": [0, 3],
    "1-2split": [1, 2], "1-4split": [1, 4], "2-3split": [2, 3], "2-5split": [2, 5],
    "3-6split": [3, 6], "4-5split": [4, 5], "4-7split": [4, 7], "5-6split": [5, 6],
    "5-8split": [5, 8], "6-9split": [6, 9], "7-8split": [7, 8], "7-10split": [7, 10],
    "8-9split": [8, 9], "8-11split": [8, 11], "9-12split": [9, 12], "10-11split": [10, 11],
    "10-13split": [10, 13], "11-12split": [11, 12], "11-14split": [11, 14], "12-15split": [12, 15],
    "13-14split": [13, 14], "13-16split": [13, 16], "14-15split": [14, 15], "14-17split": [14, 17],
    "15-18split": [15, 18], "16-17split": [16, 17], "16-19split": [16, 19], "17-18split": [17, 18],
    "17-20split": [17, 20], "18-21split": [18, 21], "19-20split": [19, 20], "19-22split": [19, 22],
    "20-21split": [20, 21], "20-23split": [20, 23], "21-24split": [21, 24], "22-23split": [22, 23],
    "22-25split": [22, 25], "23-24split": [23, 24], "23-26split": [23, 26], "24-27split": [24, 27],
    "25-26split": [25, 26], "25-28split": [25, 28], "26-27split": [26, 27], "26-29split": [26, 29],
    "27-30split": [27, 30], "28-29split": [28, 29], "28-31split": [28, 31], "29-30split": [29, 30],
    "29-32split": [29, 32], "30-33split": [30, 33], "31-32split": [31, 32], "31-34split": [31, 34],
    "32-33split": [32, 33], "32-35split": [32, 35], "33-36split": [33, 36], "34-35split": [34, 35],
    "35-36split": [35, 36],

    # --- Double Streets (6 numbers) ---
    "0-3dblstrt": [0, 1, 2, 3],
    "1-6dblstrt": [1, 2, 3, 4, 5, 6],
    "4-9dblstrt": [4, 5, 6, 7, 8, 9],
    "7-12dblstrt": [7, 8, 9, 10, 11, 12],
    "10-15dblstrt": [10, 11, 12, 13, 14, 15],
    "13-18dblstrt": [13, 14, 15, 16, 17, 18],
    "16-21dblstrt": [16, 17, 18, 19, 20, 21],
    "19-24dblstrt": [19, 20, 21, 22, 23, 24],
    "22-27dblstrt": [22, 23, 24, 25, 26, 27],
    "25-30dblstrt": [25, 26, 27, 28, 29, 30],
    "28-33dblstrt": [28, 29, 30, 31, 32, 33],
    "31-36dblstrt": [31, 32, 33, 34, 35, 36],
})

# Supported chip denominations for betting (label -> value)
CHIP_DENOMINATIONS = OrderedDict([
    ("chip_.1", 0.1),
    ("chip_.2", 0.2),
    ("chip_.5", 0.5),
    ("chip_1", 1),
    ("chip_2", 2),
    ("chip_5", 5),
    ("chip_25", 25),
    ("chip_100", 100),
])

# --- Payout table for roulette bet types ---
PAYOUT_TABLE = {
    'straight': 35,      # single number
    'split': 17,
    'street': 11,
    'corner': 8,
    'sixline': 5,        # double street
    'dozen': 2,
    'column': 2,
    'red': 1, 'black': 1,
    'even': 1, 'odd': 1,
    '1to18': 1, '19to36': 1,
}

def get_bet_type_and_numbers(label):
    """
    Given a bet label, return (bet_type, numbers_covered).
    """
    label = label.lower().strip()
    if label in ROULETTE_NUMBER_MAPPINGS:
        numbers = ROULETTE_NUMBER_MAPPINGS[label]
        # Determine bet type by label pattern
        if label.isdigit() or label in ("0", "00"):
            return 'straight', numbers
        elif 'split' in label:
            return 'split', numbers
        elif 'strt' in label:
            if 'dblstrt' in label:
                return 'sixline', numbers
            return 'street', numbers
        elif 'corner' in label:
            return 'corner', numbers
        elif '12' in label:
            return 'dozen', numbers
        elif 'col' in label:
            return 'column', numbers
        elif label in ('red', 'black', 'even', 'odd', '1to18', '19to36'):
            return label, numbers
        else:
            return 'unknown', numbers
    return 'unknown', []

def calculate_win_amount(bets, winning_number):
    """
    bets: list of dicts: [{ 'label': str, 'amount': float }]
    winning_number: int or str
    Returns: (total_win_amount, details)
    """
    total_win = 0.0
    details = []
    for bet in bets:
        bet_type, numbers = get_bet_type_and_numbers(bet['label'])
        payout = PAYOUT_TABLE.get(bet_type, 0)
        if str(winning_number) in [str(n) for n in numbers]:
            # Payout excludes stake, so total return is amount * (payout + 1)
            win_amt = bet['amount'] * payout
            # total_return = bet['amount'] + win_amt
            
            total_win += win_amt
            details.append({
                'label': bet['label'], 
                'amount': bet['amount'], 
                'bet_type': bet_type, 
                'payout': payout, 
                'win_amt': win_amt, 
                # 'total_return': total_return,
                'win': True
            })
        else:
            details.append({
                'label': bet['label'], 
                'amount': bet['amount'], 
                'bet_type': bet_type, 
                'payout': payout, 
                'win_amt': 0, 
                # 'total_return': 0,
                'win': False
            })
    return total_win, details

class StrategyEngine:
    """
    Manages the separation of betting strategy (labels/locations) and progression (bet amount logic).
    - strategy: decides where to bet (labels)
    - progression: decides how much to bet (amount)
    """
    def __init__(self, strategy_name: str, base_bet: float, max_loss: float = 100.0,
                 custom_strategies: dict = None, progression_type: str = "flat",
                 max_bet: float = None, max_consec_losses: int = None,
                 dynamic_rules=None, session_start_balance=0.0, custom_sequence=None, dalembert_step=1,
                 observation_trigger: int = 0):
        
        # --- Parsing Logic for Rotation Strings (e.g. "name:dynamic|rules=...") ---
        self.strategy_name = strategy_name
        self.dynamic_rules = dynamic_rules or []

        # Per-leg ("session") stop conditions, parsed from the entry suffix
        # (Bundle Builder per-strategy "Stops"). 0 = disabled. These are
        # per-strategy ONLY — each leg of a parallel/rotation bundle enforces
        # its own. Counters are tracked in record_result and reset on reset().
        stop_wins = 0          # stop this leg after N total wins
        stop_losses = 0        # stop this leg after N total losses
        stop_profit = 0.0      # stop this leg at +$X cumulative profit
        stop_loss_limit = 0.0  # stop this leg at -$X cumulative loss
        stop_time = 0.0        # stop this leg after X minutes
        session_length = 0.0   # per-leg time budget in minutes (same unit as stop_time)

        # Check if strategy_name contains configuration overrides
        if isinstance(strategy_name, str) and (":" in strategy_name or "|" in strategy_name):
            try:
                # 1. Extract Base Name
                parts = strategy_name.split(":", 1)
                base_name = parts[0].strip()
                self.strategy_name = base_name  # Clean name for display and lookup
                
                # 2. Extract Progression Configuration
                if len(parts) > 1:
                    config_str = parts[1]
                    
                    # Check for progression type by splitting at rules pipe
                    prog_type_val = config_str.split("|")[0].strip().lower()
                    if prog_type_val in ["flat", "martingale", "fibonacci", "dalembert", "custom_sequence", "dynamic", "custom"]:
                        progression_type = prog_type_val
                    
                    # 3. Parse Rules
                    # Format: rules=event:action|param=val;event:action|param=val
                    if "rules=" in config_str:
                        rules_section = config_str.split("rules=")[1].split(",")[0] # Assume rules are until next comma if any? Usually likely end of string or pipe? 
                        # Actually 'rules=' seems to be the main payload. 
                        # Let's split by delimiter if multiple sections exist?
                        # Based on example: "dynamic|rules=..."
                        
                        dynamic_rules = []
                        rules_list = rules_section.split(";")
                        for rule_str in rules_list:
                            rule_parts = rule_str.split("|")
                            main_def = rule_parts[0] # "event:action"
                            
                            if ":" in main_def:
                                event, action = main_def.split(":", 1)
                                rule = {'on': event.strip(), 'action': action.strip()}
                                
                                # Process params (condition, etc)
                                for param in rule_parts[1:]:
                                    if "=" in param:
                                        k, v = param.split("=", 1)
                                        rule[k.strip()] = v.strip()
                                
                                dynamic_rules.append(rule)
                                
                        logger.info(f"Parsed dynamic rules for {base_name}: {dynamic_rules}")
                        # Merge: per-entry rules (just parsed) take priority by
                        # being earlier in the iteration order. Bundle-level
                        # dynamic_rules (passed via kwarg, currently in
                        # self.dynamic_rules) act as a FALLBACK for events the
                        # per-entry rules don't cover — e.g. rotation entry
                        # has `rules=loss:martingale` and the bundle has
                        # `[{on:win, action:reset_to_base}]`. Without the
                        # merge, win-reset would be lost in rotation mode.
                        bundle_fallback = list(self.dynamic_rules or [])
                        # Drop any bundle fallback whose event is already
                        # handled by a per-entry rule, since per-entry wins
                        # anyway — keeps the list compact for debugging.
                        per_entry_events = {r.get('on') for r in dynamic_rules}
                        bundle_fallback = [r for r in bundle_fallback
                                           if r.get('on') not in per_entry_events]
                        self.dynamic_rules = dynamic_rules + bundle_fallback
                        if bundle_fallback:
                            logger.info(f"  + merged {len(bundle_fallback)} bundle-level fallback rule(s): {bundle_fallback}")

                    # Parse other global progression parameters
                    config_items = config_str.split("|")[1:]
                    for item in config_items:
                        if item.startswith("step="):
                            val = item.split("=", 1)[1]
                            if val == "base_bet":
                                dalembert_step = "base_bet"
                            else:
                                try:
                                    dalembert_step = float(val)
                                except ValueError:
                                    pass
                        elif item.startswith("seq="):
                            seq_str = item.split("=", 1)[1]
                            try:
                                custom_sequence = [float(x.strip()) for x in seq_str.split(",") if x.strip()]
                            except ValueError:
                                pass
                        elif item.startswith("max_consec_losses="):
                            # Per-strategy consec-loss cap from the bundle entry.
                            # Overrides the constructor arg; 0 ⇒ disabled (see
                            # get_next_bet). Lets each strategy in a parallel
                            # bundle carry its own cap independently.
                            try:
                                max_consec_losses = int(item.split("=", 1)[1])
                            except ValueError:
                                pass
                        elif item.startswith("base_bet="):
                            # Per-strategy base bet from the bundle entry. Must
                            # reassign the LOCAL base_bet (not just self.base_bet)
                            # because base_bet flows into _load_strategy and
                            # _load_progression below, and self.base_bet = base_bet
                            # at the bottom of __init__ picks it up too. Lets each
                            # leg of a parallel/rotation bundle size its own bet.
                            try:
                                base_bet = float(item.split("=", 1)[1])
                            except ValueError:
                                pass
                        elif item.startswith("stop_wins="):
                            try: stop_wins = int(item.split("=", 1)[1])
                            except ValueError: pass
                        elif item.startswith("stop_losses="):
                            try: stop_losses = int(item.split("=", 1)[1])
                            except ValueError: pass
                        elif item.startswith("stop_profit="):
                            try: stop_profit = float(item.split("=", 1)[1])
                            except ValueError: pass
                        elif item.startswith("stop_loss_limit="):
                            try: stop_loss_limit = float(item.split("=", 1)[1])
                            except ValueError: pass
                        elif item.startswith("stop_time="):
                            try: stop_time = float(item.split("=", 1)[1])
                            except ValueError: pass
                        elif item.startswith("session_length="):
                            try: session_length = float(item.split("=", 1)[1])
                            except ValueError: pass

            except Exception as e:
                logger.error(f"Error parsing strategy string '{strategy_name}': {e}")
                # Fallback to raw name if parsing fails, but self.strategy_name is likely base_name if step 1 succeeded
                
        self.base_bet = base_bet
        self.max_loss = max_loss
        self.max_bet = max_bet
        self.max_consec_losses = max_consec_losses
        self.total_loss = 0.0
        self.consecutive_losses = 0
        self.custom_labels = []
        self.custom_strategies = custom_strategies or {}

        # --- Per-leg stop conditions + counters (see check_session_stop) ---
        self.stop_wins = stop_wins
        self.stop_losses = stop_losses
        self.stop_profit = stop_profit
        self.stop_loss_limit = stop_loss_limit
        self.stop_time = stop_time
        self.session_length = session_length
        # Counters tracked over the leg's life (NOT reset on win, unlike
        # consecutive_losses / total_loss). leg_net_profit is the cumulative
        # P&L of this leg; leg_start_time is stamped on the first recorded result.
        self.leg_wins = 0
        self.leg_losses = 0
        self.leg_rounds = 0
        self.leg_net_profit = 0.0
        self.leg_start_balance = None
        self.leg_start_time = None

        # --- Strategy: where to bet ---
        self.strategy = self._load_strategy(self.strategy_name, base_bet, self.custom_strategies, custom_sequence, dalembert_step)

        # --- Progression: how much to bet ---
        self.progression = self._load_progression(progression_type, base_bet, self.dynamic_rules, session_start_balance, custom_sequence, dalembert_step)

        # --- Observation Phase (Sleeper Hunter) ---
        self.observation_trigger = observation_trigger
        self.is_observing = self.observation_trigger > 0
        self.consecutive_misses = 0

    def load_encrypted_strategies(self, directory, user_tier="FREE"):
        """
        Scans directory for .spine files, decypts, checks tier access, and loads them.
        """
        if not os.path.exists(directory):
            logger.warning(f"Strategy directory not found: {directory}")
            return 0

        # Tier Hierarchy: Higher number = better
        TIER_LEVELS = {
            "FREE": 0, # Legacy fallback
            "BASIC": 1,
            "PLUS": 2,
            "PRO": 3,
            "ADMIN": 99
        }
        user_level = TIER_LEVELS.get(user_tier.upper(), 0)

        spine_files = glob.glob(os.path.join(directory, "*.spine"))
        count = 0
        
        for filepath in spine_files:
            try:
                with open(filepath, "rb") as f:
                    encrypted_bytes = f.read()
                
                strategy_data = decrypt_strategy_data(encrypted_bytes)
                if strategy_data and isinstance(strategy_data, dict):
                    # Check DLC Bundle Entitlements first
                    bundle_id = strategy_data.get("bundle_id")
                    if bundle_id and user_level < TIER_LEVELS.get("ADMIN", 99):
                        from core.security.license_manager import get_license_manager
                        lm = get_license_manager()
                        if bundle_id not in lm.entitlements:
                            logger.info(f"Skipping {os.path.basename(filepath)} - User lacks entitlement: {bundle_id}")
                            continue
                            
                    # Check Tier
                    # If strategy doesn't specify tier, assume BASIC
                    strat_tier_name = strategy_data.get("tier", "BASIC").upper()
                    strat_level = TIER_LEVELS.get(strat_tier_name, 0)
                    
                    strategy_name = os.path.splitext(os.path.basename(filepath))[0]
                    
                    if user_level >= strat_level:
                        # Unlocked
                        self.custom_strategies[strategy_name] = strategy_data
                        count += 1
                        logger.info(f"Loaded encrypted strategy: {strategy_name} (Tier: {strat_tier_name})")
                    else:
                        # Locked - maybe add a placeholder to show it's locked?
                        # For now, let's just NOT load it, so it doesn't appear in the list.
                        # Or better: Add it with a LOCKED prefix and empty logic so user sees what they are missing.
                        # But that might crash the engine if selected.
                        # Let's simple skip for now to be safe, or log it.
                        logger.info(f"Skipped strategy {strategy_name}: User Tier {user_tier} < {strat_tier_name}")
                        
            except Exception as e:
                logger.error(f"Failed to load encrypted strategy {filepath}: {e}")
        
        return count

    def _load_strategy(self, name, base_bet, custom_strategies, custom_sequence, dalembert_step,
                       _visited=None):
        """Resolve a strategy name to an instantiated strategy object.

        _visited: set of lowercased custom-strategy names already on the resolution
                  stack — used by composite mode to detect delegation cycles.
        """
        if _visited is None:
            _visited = set()
        name = name.lower()
        if name in _visited:
            raise ValueError(
                f"Composite delegation cycle detected: {' -> '.join(list(_visited) + [name])}"
            )
        # Case-insensitive lookup for custom strategies
        custom_strategies_lower = {k.lower(): v for k, v in custom_strategies.items()} if custom_strategies else {}
        if name in custom_strategies_lower:
            strategy_data = custom_strategies_lower[name]
            custom_bet_units = None

            # Check for dynamic mode strategies
            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'neighbors':
                neighbors = strategy_data.get('neighbors', 2)
                anchor_offsets = strategy_data.get('anchor_offsets', [1])
                hot_count = strategy_data.get('hot_count', 0)
                cold_count = strategy_data.get('cold_count', 0)
                lookback = strategy_data.get('lookback', 30)
                logger.info(f"Loading dynamic neighbors strategy '{name}': ±{neighbors}, "
                            f"anchors={anchor_offsets}, hot={hot_count}, cold={cold_count}, lookback={lookback}")
                return DynamicNeighborsStrategy(
                    base_bet, neighbors=neighbors, anchor_offsets=anchor_offsets,
                    hot_count=hot_count, cold_count=cold_count, lookback=lookback
                )

            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'bias_adaptive':
                # Statistical bias-hunter. Bets the most over-represented
                # member of `group` when the last `window` spins show a
                # chi-square > `chi2_threshold` (uniform distribution test).
                # See core/strategies/bias_adaptive.py for the math.
                group = strategy_data.get('group', 'dozen')
                window = int(strategy_data.get('window', 30))
                chi2_threshold = strategy_data.get('chi2_threshold')  # None → class default
                min_samples = int(strategy_data.get('min_samples', 20))
                contra = bool(strategy_data.get('contra', False))
                logger.info(f"Loading bias_adaptive strategy '{name}': group={group}, "
                            f"window={window}, threshold={chi2_threshold}, contra={contra}")
                return BiasAdaptiveStrategy(
                    base_bet=base_bet, group=group, window=window,
                    chi2_threshold=chi2_threshold, min_samples=min_samples,
                    contra=contra,
                )

            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'dirichlet_bayes':
                # Self-evolving Bayesian bias hunter: one discounted Dirichlet
                # posterior over the 37 pockets; bets a label only when its lower
                # credible bound provably clears the payout break-even (otherwise
                # sits out). See core/strategies/dirichlet_bayes.py.
                params = {k: strategy_data[k] for k in (
                    "gamma", "gamma_fast", "alpha_prior", "delta", "min_neff",
                    "min_label_hits", "top_k", "margin", "targets",
                    "changepoint_z", "changepoint_run", "flush_spins", "bonferroni",
                ) if k in strategy_data}
                logger.info(f"Loading dirichlet_bayes strategy '{name}': {params}")
                return DirichletBayesStrategy(base_bet=base_bet, **params)

            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'pattern_follower':
                rules = strategy_data.get('rules', [])
                history_size = strategy_data.get('history_size', 50)
                logger.info(f"Loading pattern follower strategy '{name}' with {len(rules)} rules, "
                            f"history_size={history_size}")
                return PatternFollowerStrategy(base_bet, rules=rules, history_size=history_size)

            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'composite':
                rules = strategy_data.get('rules', [])
                history_size = strategy_data.get('history_size', 50)
                # Pre-parse rules to discover sub-strategy references, then recursively
                # resolve each. Cycle detection uses the _visited set.
                try:
                    parsed_rules = [parse_rule(r) for r in rules]
                except ValueError as e:
                    raise ValueError(f"Composite strategy '{name}': {e}") from e

                delegate_names = extract_delegate_names(parsed_rules)
                sub_strategies = {}
                next_visited = _visited | {name}
                for sub_name in delegate_names:
                    sub_lower = sub_name.lower()
                    if sub_lower not in custom_strategies_lower:
                        raise ValueError(
                            f"Composite strategy '{name}' references unknown sub-strategy "
                            f"'{sub_name}'. Available: {sorted(custom_strategies_lower.keys())}"
                        )
                    sub_strategies[sub_name] = self._load_strategy(
                        sub_name, base_bet, custom_strategies, custom_sequence, dalembert_step,
                        _visited=next_visited,
                    )

                logger.info(f"Loading composite strategy '{name}' with {len(rules)} rules, "
                            f"{len(delegate_names)} sub-strategies, history_size={history_size}")
                return CompositeStrategy(
                    base_bet=base_bet, rules=rules, history_size=history_size,
                    sub_strategies=sub_strategies,
                )

            if isinstance(strategy_data, dict) and 'labels' in strategy_data:
                self.custom_labels = strategy_data['labels']

                # Prioritize the new 'bet_units' format
                if 'bet_units' in strategy_data:
                    custom_bet_units = strategy_data['bet_units']
                # Fallback for backward compatibility with 'bet_amounts'
                elif 'bet_amounts' in strategy_data:
                    logger.warning("Legacy 'bet_amounts' format detected. Converting to units.")
                    custom_bet_units = {}
                    bet_amounts = strategy_data.get('bet_amounts', {})
                    for label, amount in bet_amounts.items():
                        if base_bet > 0:
                            custom_bet_units[label] = int(amount / base_bet)
                        else:
                            custom_bet_units[label] = 1

            elif isinstance(strategy_data, list):
                # Old format - just a list of labels
                self.custom_labels = strategy_data

            return CustomStrategy(self.custom_labels, base_bet, self.max_loss, custom_bet_units=custom_bet_units)

        elif name == "fibonacci":
            return FibonacciStrategy(base_bet)
        elif name == "custom_sequence":
            sequence = custom_sequence or [1]
            return CustomSequenceStrategy(base_bet, sequence)
        elif name == "dalembert":
            step = dalembert_step or 1
            return DAlembertStrategy(base_bet, step)
        elif name == "martingale":
            return MartingaleStrategy(base_bet)
        elif name == "flat":
            return FlatStrategy(base_bet)
        elif name == "dynamic_9street":
            return DynamicNineStreetStrategy(base_bet)
        elif name.startswith("dynamic_neighbors"):
            # Support "dynamic_neighbors" (default 2) or "dynamic_neighbors_N"
            parts = name.split("_")
            neighbors = 2  # default: 2 per side = 5 total numbers
            if len(parts) == 3:
                try:
                    neighbors = int(parts[2])
                except ValueError:
                    pass
            return DynamicNeighborsStrategy(base_bet, neighbors=neighbors)
        elif name.startswith("bias_adaptive"):
            # Names: "bias_adaptive" (default group=dozen), or "bias_adaptive_<group>"
            # where <group> is color/parity/hilo/dozen/column. Also accepts
            # "bias_adaptive_<group>_contra" for the gambler's-fallacy mode
            # (bet AGAINST the dominant member).
            parts = name.split("_")
            group = "dozen"
            contra = False
            if len(parts) >= 3:
                _grp = parts[2].lower()
                if _grp in ("color", "parity", "hilo", "dozen", "column"):
                    group = _grp
            if len(parts) >= 4 and parts[3].lower() == "contra":
                contra = True
            # Customizable via the custom_strategies registry too — entry can
            # override window / chi2_threshold / min_samples per-strategy.
            cfg = (custom_strategies or {}).get(name) or {}
            return BiasAdaptiveStrategy(
                base_bet=base_bet,
                group=group,
                window=int(cfg.get("window", 30)),
                chi2_threshold=cfg.get("chi2_threshold"),  # None → use class default
                min_samples=int(cfg.get("min_samples", 20)),
                contra=bool(cfg.get("contra", contra)),
            )
        else:
            # Default to a single-label strategy
            return CustomStrategy([name], base_bet, self.max_loss)

    def _load_progression(self, progression_type, base_bet, dynamic_rules, session_start_balance, custom_sequence, dalembert_step):
        if progression_type == "dynamic":
            return DynamicProgressionStrategy(base_bet, dynamic_rules or [], session_start_balance, custom_sequence, dalembert_step)
        elif progression_type == "fibonacci":
            return FibonacciStrategy(base_bet)
        elif progression_type == "custom_sequence":
            sequence = custom_sequence or [1]
            return CustomSequenceStrategy(base_bet, sequence)
        elif progression_type == "dalembert":
            step = dalembert_step or 1
            return DAlembertStrategy(base_bet, step)
        elif progression_type == "martingale":
            return MartingaleStrategy(base_bet)
        elif progression_type == "flat":
            return FlatStrategy(base_bet)
        elif progression_type == "custom":
            return CustomProgressionStrategy(base_bet)
        else:
            return FlatStrategy(base_bet)

    def get_next_bet(self):
        # Allow ranking simulation to bypass license check (no real bets placed)
        if not getattr(self, '_ranking_simulation', False):
            lm = get_license_manager()
            # Deep security: prevent bot from placing bets if unlicensed
            if not lm.is_licensed and not getattr(lm, "DEBUG_BYPASS", False):
                logger.error("🛑 Unlicensed execution prevented in StrategyEngine.")
                return 0.0
            
        # max_consec_losses: a per-strategy safety cap. 0 (or None / negative)
        # means DISABLED — without this guard, 0 would make `consecutive_losses
        # >= 0` true on the very first bet and stop the strategy immediately.
        if (self.max_consec_losses or 0) > 0 and self.consecutive_losses >= self.max_consec_losses:
            logger.info(f"🛑 Max consecutive losses ({self.max_consec_losses}) reached. Stopping bet.")
            return 0.0
        next_bet = self.progression.get_next_bet()
        if self.max_bet is not None and next_bet > self.max_bet:
            logger.warning(f"⚠️ Bet capped at max_bet ({self.max_bet}) instead of {next_bet}")
            next_bet = self.max_bet
        return next_bet

    def get_current_bet(self):
        return self.progression.get_current_bet()

    def get_bet_labels(self):
        if hasattr(self.strategy, "get_labels"):
            return self.strategy.get_labels()
        return [self.strategy_name]

    def get_bet_amounts(self):
        """
        Returns a dictionary mapping labels to their bet amounts.
        For strategies that support custom bet amounts, this will return the custom amounts.
        For other strategies, it will return the current bet amount for all labels.
        """
        if hasattr(self.strategy, "get_bet_amounts"):
            # Pass the current progression bet to the strategy
            current_progression_bet = self.get_current_bet()
            return self.strategy.get_bet_amounts(current_progression_bet)
        else:
            # Fallback for strategies that don't support custom amounts
            labels = self.get_bet_labels()
            current_bet = self.get_current_bet()
            return {label: current_bet for label in labels}

    def get_total_bet_amount(self):
        """
        Returns the total amount that will be bet across all labels.
        """
        if hasattr(self.strategy, "get_total_bet_amount"):
            # Pass the current progression bet to the strategy
            current_progression_bet = self.get_current_bet()
            return self.strategy.get_total_bet_amount(current_progression_bet)
        else:
            # Fallback for strategies that don't support custom amounts
            labels = self.get_bet_labels()
            current_bet = self.get_current_bet()
            return current_bet * len(labels)

    def get_covered_numbers(self):
        """
        Get all roulette numbers covered by the current strategy.
        Returns a set of numbers that would result in a win.
        """
        covered_numbers = set()
        labels = self.get_bet_labels()
        
        for label in labels:
            label_lower = label.lower().strip()
            if label_lower in ROULETTE_NUMBER_MAPPINGS:
                covered_numbers.update(ROULETTE_NUMBER_MAPPINGS[label_lower])
            else:
                # Try to parse as a single number
                try:
                    number = int(label_lower)
                    if 0 <= number <= 36:
                        covered_numbers.add(number)
                except ValueError:
                    logger.warning(f"⚠️ Unknown bet label: {label}")
        
        return covered_numbers

    def is_winning_number(self, winning_number):
        """
        Check if the given winning number results in a win for this strategy.
        """
        if winning_number is None:
            return False
        
        covered_numbers = self.get_covered_numbers()
        return winning_number in covered_numbers

    def record_result(self, win: bool, current_balance: float = None, winning_number=None,
                      round_pnl: float = None):
        if getattr(self, 'is_observing', False):
            if not win:
                self.consecutive_misses += 1
                logger.debug(f"👀 Observation Miss {self.consecutive_misses}/{self.observation_trigger}")
                if self.consecutive_misses >= self.observation_trigger:
                    self.is_observing = False
                    logger.info("🔥 Observation condition met! Activating real betting.")
            else:
                self.consecutive_misses = 0 # Target hit prematurely, wait for new sequence of misses
                logger.debug("👀 Observation Interrupted: Target hit prematurely. Resetting miss counter.")
            return # Skip progression updates while observing

        amount = self.get_current_bet()
        # Compute session profit once — needed by the dynamic progression AND by
        # any strategy using persist_until="session_high" (pattern follower).
        current_profit = None
        ss_bal = getattr(self.progression, 'session_start_balance', None)
        if current_balance is not None and ss_bal is not None:
            current_profit = current_balance - ss_bal
        # Update progression (may need current_balance for dynamic)
        if isinstance(self.progression, DynamicProgressionStrategy):
            if current_profit is None:
                # Fallback: estimate profit from tracked state
                current_profit = getattr(self.progression, 'total_profit', 0.0)
                logger.warning("[StrategyEngine] record_result called without current_balance for DynamicProgression — using last known profit")
            self.progression.record_result(win, current_profit)
        else:
            self.progression.record_result(win)
        # Optionally update strategy (if it tracks state). Pass current_profit
        # when the strategy accepts it (composite/pattern_follower use it for
        # persist_until="session_high").
        if hasattr(self.strategy, "record_result"):
            import inspect
            sig = inspect.signature(self.strategy.record_result)
            kwargs = {}
            if 'last_number' in sig.parameters and winning_number is not None:
                kwargs['last_number'] = winning_number
            if 'current_profit' in sig.parameters:
                kwargs['current_profit'] = current_profit
            self.strategy.record_result(win, **kwargs)
        if win:
            self.total_loss = 0.0
            self.consecutive_losses = 0
        else:
            self.total_loss += amount
            self.consecutive_losses += 1

        # --- Per-leg stop tracking (independent of the progression resets
        # above). Counters accumulate over the whole leg so check_session_stop
        # can enforce the per-strategy "Stops" from the bundle entry. ---
        if self.leg_start_time is None:
            self.leg_start_time = time.time()
        self.leg_rounds += 1
        if win:
            self.leg_wins += 1
        else:
            self.leg_losses += 1
        if round_pnl is not None:
            # Exact per-round P&L supplied by the caller (preferred path).
            self.leg_net_profit += float(round_pnl)
        elif current_balance is not None:
            # Fallback: derive cumulative leg P&L from the balance delta since
            # the leg's first recorded result.
            if self.leg_start_balance is None:
                self.leg_start_balance = current_balance
            self.leg_net_profit = current_balance - self.leg_start_balance

    def check_session_stop(self):
        """Per-leg stop conditions parsed from the bundle entry suffix
        (Bundle Builder per-strategy "Stops"). Returns a human-readable reason
        string when a limit is hit, else None. Every limit is 0 = disabled.

        "Leg" = this engine's life since its first recorded result; counters
        reset on reset(). Used by the bot loop: in parallel mode the offending
        leg is disarmed (others keep running); in single/rotation mode the bot
        halts (or rotates) when the active leg's limit fires."""
        if (self.stop_wins or 0) > 0 and self.leg_wins >= self.stop_wins:
            return f"reached {self.leg_wins} wins (cap {self.stop_wins})"
        if (self.stop_losses or 0) > 0 and self.leg_losses >= self.stop_losses:
            return f"reached {self.leg_losses} losses (cap {self.stop_losses})"
        if (self.stop_profit or 0) > 0 and self.leg_net_profit >= self.stop_profit:
            return f"profit ${self.leg_net_profit:.2f} >= target ${self.stop_profit:.2f}"
        if (self.stop_loss_limit or 0) > 0 and self.leg_net_profit <= -abs(self.stop_loss_limit):
            return f"loss ${-self.leg_net_profit:.2f} >= limit ${self.stop_loss_limit:.2f}"
        if self.leg_start_time is not None:
            elapsed_min = (time.time() - self.leg_start_time) / 60.0
            for lim, label in ((self.stop_time, "stop_time"),
                               (self.session_length, "session_length")):
                if (lim or 0) > 0 and elapsed_min >= lim:
                    return f"{label} {lim:g} min elapsed ({elapsed_min:.1f} min)"
        return None

    def reset(self):
        if hasattr(self.strategy, "reset"):
            self.strategy.reset()
        if hasattr(self.progression, "reset"):
            self.progression.reset()
        self.total_loss = 0.0
        self.consecutive_losses = 0
        self.consecutive_misses = 0
        self.is_observing = self.observation_trigger > 0
        # Per-leg stop counters restart so a rotated/reused leg gets a fresh budget.
        self.leg_wins = 0
        self.leg_losses = 0
        self.leg_rounds = 0
        self.leg_net_profit = 0.0
        self.leg_start_balance = None
        self.leg_start_time = None
