import random
import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
try:
    import matplotlib.pyplot as plt
    import pandas as pd
except ImportError:
    plt = None
    pd = None
from core.strategy_engine import StrategyEngine

@dataclass
class BacktestResult:
    """Results from a backtesting session"""
    strategy_name: str
    initial_balance: float
    base_bet: float
    total_rounds: int
    total_wins: int
    total_losses: int
    win_rate: float
    total_profit: float
    max_profit: float
    max_loss: float
    max_drawdown: float
    consecutive_wins: int
    consecutive_losses: int
    final_balance: float
    roi: float
    sharpe_ratio: float
    bet_history: List[Dict]
    balance_history: List[Dict]
    session_duration: float
    # ── Audit fields (added for backtest auditability) ─────────────────────
    # All optional with sane defaults so legacy callers still work. These let
    # the GUI explain exactly why each session ended and which escalated
    # base_bet/max_loss were active at session start.
    stop_reason: str = ""           # STOP_LOSS / MAX_CONSEC_LOSSES / INSUFFICIENT_BALANCE / ROUNDS_EXHAUSTED / PROFIT_TARGET / TRAILING_STOP / STREAK_LIMIT / TIME_LIMIT / EXTENSION_LIMIT
    stop_message: str = ""          # human-readable detail of the stop
    effective_base_bet: float = 0.0 # base_bet active at session start (post-escalation)
    effective_max_loss: float = 0.0 # max_loss active at session start (post-escalation)
    escalation_step: int = 0        # escalation step active at session start (0 = none)

class RouletteBacktester:
    """
    Comprehensive backtesting system for roulette strategies
    """
    
    def __init__(self):
        self.results_cache = {}
        self.historical_data = []
        
    def generate_historical_data(self, num_rounds: int = 1000, seed: int = None) -> List[Dict]:
        """
        Generate realistic roulette historical data
        """
        if seed:
            random.seed(seed)
            
        data = []
        for i in range(num_rounds):
            # Generate random roulette outcome (0-36)
            outcome = random.randint(0, 36)
            
            # Determine if it's a win for common bet types
            # This is a simplified model - you can make it more sophisticated
            is_win = self._determine_win(outcome)
            
            data.append({
                'round': i + 1,
                'outcome': outcome,
                'is_win': is_win,
                'timestamp': datetime.now() - timedelta(minutes=num_rounds-i)
            })
            
        return data

    def fetch_historical_data_from_db(self, limit: int = 1000,
                                      max_id: int = None) -> List[Dict]:
        """
        Fetch historical data from the database.

        max_id: optional anchor. When set, only rows with id <= max_id are
        returned. The GUI uses this to pin a backtest to the EXACT same
        snapshot across multiple runs — otherwise the watcher thread
        appending new spins between runs silently shifts the "latest K"
        window and breaks reproducibility.
        """
        from core.utils.db_utils import get_recent_winning_numbers

        # Get numbers from DB (returns list of dicts with 'number', 'timestamp', etc.)
        # Returns most recent first (timestamp DESC, id DESC for stable
        # ordering on tied timestamps).
        raw_data = get_recent_winning_numbers(limit=limit, max_id=max_id)
        
        # Reverse to have chronological order (oldest -> newest) for backtesting
        raw_data.reverse()
        
        data = []
        for i, record in enumerate(raw_data):
            outcome = record['number']
            
            # Determine win based on simple bet logic (Red/Black etc support needed for realistic test?)
            # The current _determine_win checks Dozens/Columns. 
            # Ideally we should support color checking too if strategy relies on it.
            # But for now we stick to what _determine_win supports or expand it.
            
            is_win = self._determine_win(outcome)
            
            data.append({
                'round': i + 1,
                'outcome': outcome,
                'is_win': is_win,
                'timestamp': datetime.fromisoformat(record['timestamp']) if isinstance(record['timestamp'], str) else record['timestamp'],
                'color': record.get('color') # Pass color if available
            })
            
        return data
    
    def _determine_win(self, outcome: int) -> bool:
        """
        Determine if outcome is a win for the bet types we're testing
        For now, testing on dozens and columns (1/3 probability each)
        """
        # First dozen: 1-12
        if 1 <= outcome <= 12:
            return True
        # Third dozen: 25-36  
        elif 25 <= outcome <= 36:
            return True
        # First column: 1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34
        elif outcome in [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34]:
            return True
        # Third column: 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36
        elif outcome in [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36]:
            return True
        else:
            return False
    
    def backtest_strategy(self, 
                         strategy_name: str,
                         base_bet: float,
                         initial_balance: float = 100.0,
                         num_rounds: int = 100,
                         progression_type: str = "martingale",
                         max_loss: float = 50.0,
                         max_bet: float = None,
                         max_consec_losses: int = None,
                         custom_strategies: dict = None,
                         dynamic_rules: List = None,
                         custom_sequence: List = None,
                         dalembert_step: int = 1,
                         seed: int = None,
                         historical_data_override: List[Dict] = None,
                         rotation_config: Dict = None,
                         session_config: Dict = None) -> BacktestResult:
        """
        Run a complete backtest of a strategy
        """
        
        # Generator or use historical data
        if historical_data_override:
            # Use provided override data (e.g. from DB)
            self.historical_data = historical_data_override
            # num_rounds should match data length if not explicitly limited?
            # Or just take min(num_rounds, len(data))
            if len(self.historical_data) < num_rounds:
                 print(f"⚠️ Warning: Requested {num_rounds} rounds but only {len(self.historical_data)} data points available. Using available data.")
                 num_rounds = len(self.historical_data)
        elif not self.historical_data or len(self.historical_data) < num_rounds:
             self.historical_data = self.generate_historical_data(num_rounds * 2, seed)
        
        # Initialize strategy engines
        engines = []
        active_engine_idx = 0
        
        if rotation_config and rotation_config.get('strategies'):
            # Rotation Mode
            strat_names = rotation_config['strategies']
            for name in strat_names:
                engines.append(StrategyEngine(
                    strategy_name=name.strip(),
                    base_bet=base_bet,
                    max_loss=max_loss,
                    custom_strategies=custom_strategies,
                    progression_type=progression_type, # Using same progression for all? Or custom?
                    max_bet=max_bet,
                    session_start_balance=initial_balance,
                    # Pass the bundle's top-level dynamic_rules so events the
                    # per-entry rules (parsed from `rules=...` in the rotation
                    # string) don't cover — typically `on:win` — fall through
                    # to the bundle-level fallback. Without this, bundles that
                    # specify `loss:martingale` per-entry + `win:reset_to_base`
                    # at the bundle level never reset the bet after a win.
                    dynamic_rules=dynamic_rules,
                ))
            if not engines:
                # Fallback
                engines.append(StrategyEngine(strategy_name, base_bet, max_loss=max_loss, custom_strategies=custom_strategies, progression_type=progression_type, session_start_balance=initial_balance))
        else:
            # Single Mode
            engines.append(StrategyEngine(
                strategy_name=strategy_name,
                base_bet=base_bet,
                max_loss=max_loss,
                custom_strategies=custom_strategies,
                progression_type=progression_type,
                max_bet=max_bet,
                max_consec_losses=max_consec_losses,
                dynamic_rules=dynamic_rules,
                session_start_balance=initial_balance,
                custom_sequence=custom_sequence,
                dalembert_step=dalembert_step
            ))

        # Backtest is a virtual simulation — no real bets placed. Bypass the
        # license check the same way ranking_engine does, otherwise
        # StrategyEngine.get_next_bet returns 0.0 and the run does nothing.
        for _e in engines:
            _e._ranking_simulation = True
            
            
        strategy_engine = engines[0]
        active_engine_idx = 0 # Ensure this is set
        print(f"DEBUG: Initialized {len(engines)} engines: {[e.strategy_name for e in engines]}")
        print(f"DEBUG: Active Engine Index: {active_engine_idx}, Strategy: {strategy_engine.strategy_name}")

        rotation_mode = rotation_config.get('mode', 'sequential') if rotation_config else None

        # ── Conditional-trigger selection (opt-in via rotation_config.selection_mode)
        # When active, the on-loss rotation block below is bypassed — the trigger
        # engine becomes the sole strategy-selection mechanism. Bundles without
        # `selection_mode: conditional` are unaffected.
        trigger_engine = None
        engines_by_base: Dict[str, Any] = {}
        _sel_mode = (rotation_config.get('selection_mode') or 'rotation').lower() if rotation_config else 'rotation'
        if rotation_config and _sel_mode in ('conditional', 'parallel'):
            try:
                from core.triggers import build_trigger_engine_from_rotation_config
                trigger_engine = build_trigger_engine_from_rotation_config(rotation_config)
                if trigger_engine:
                    for entry, eng in zip(rotation_config.get('strategies', []) or [], engines):
                        base = entry.split(':', 1)[0].strip()
                        engines_by_base[base] = eng
                    print(f"🎯 TriggerEngine active: triggers={list(trigger_engine.triggers.keys())}, "
                          f"tiebreaker={trigger_engine.tiebreaker_name}, fallback={trigger_engine.fallback}")
            except Exception as _trig_err:
                print(f"⚠️ TriggerEngine init failed ({_trig_err}) — falling back to plain rotation")
                trigger_engine = None
        
        # Initialize tracking variables
        current_balance = initial_balance
        total_wins = 0
        total_losses = 0
        session_max_profit = 0
        session_max_loss = 0
        max_drawdown = 0
        consecutive_wins = 0
        consecutive_losses = 0
        max_consecutive_wins = 0
        max_consecutive_losses = 0
        
        bet_history = []
        balance_history = [{'round': 0, 'balance': initial_balance, 'change': 0}]

        # Audit fields — track what ended the session so the GUI can explain it
        # without the user having to grep the runner log. Default ROUNDS_EXHAUSTED;
        # any stop path below overwrites this with the actual reason+message.
        session_stop_reason = "ROUNDS_EXHAUSTED"
        session_stop_message = ""

        start_time = time.time()
        
        from core.session_manager import SessionManager, StopReason
        
        # Initialize Session Manager if config provided
        session_manager = None
        if session_config:
            # Prepare config for manager (map backtest global params to session config if needed)
            # But usually session_config comes fully formed from GUI
            
            # Ensure critical params are in session_config
            if 'max_loss' not in session_config and max_loss:
                 session_config['max_loss'] = max_loss
                 # Note: max_loss argument to backtest_strategy might be redundant if session_config is used
            
            session_manager = SessionManager(session_config)

        # Run the backtest
        round_num = 0
        
        # We process rounds until done or stopped.
        # Main limit is num_rounds, but extensions can go beyond.
        # If historical data is limited, we stop when we run out.
        
        while True:
            round_num += 1
            
            # Check basic termination (Fixed Rounds)
            # Only terminate by round count if NOT extending. 
            # If extending, SessionManager controls the stop.
            
            # Current Extension State
            is_extending = False
            if session_manager and session_manager.extension_mode != "NONE":
                is_extending = True
                
            if not is_extending and round_num > num_rounds:
                 print(f"✅ Simulation complete: Reached {num_rounds} rounds.")
                 session_stop_reason = "ROUNDS_EXHAUSTED"
                 session_stop_message = f"Played all {num_rounds} configured rounds without hitting any stop condition"
                 break
            
            # Data Availability Check
            if round_num > len(self.historical_data):
                print(f"⚠️ Simulation stopped: Ran out of historical data at round {round_num-1}.")
                break

            # --- 0a. PARALLEL-mode round handler ---
            # When selection_mode == "parallel", EVERY armed candidate bets
            # together in the same round. Each candidate uses its OWN cached
            # engine's progression (per-strategy martingale, dynamic rules,
            # session_high, etc.) and its bets are merged into the placement.
            # This entire branch consumes the round and `continue`s — none of
            # the legacy single-strategy path runs for parallel rounds.
            if trigger_engine and getattr(trigger_engine, 'selection_mode', 'conditional') == 'parallel':
                labels_by_name: Dict[str, list] = {}
                for base, eng in engines_by_base.items():
                    try:
                        labels_by_name[base] = list(eng.get_bet_labels() or [])
                    except Exception:
                        labels_by_name[base] = []
                cands = trigger_engine.pick_all(labels_by_name)

                outcome_data = self.historical_data[round_num - 1]

                if not cands:
                    # No candidate armed — skip the round (parallel has no
                    # natural "stay on current" semantic since there's no
                    # active single strategy).
                    bet_history.append({
                        'round': round_num, 'strategy': '(parallel: none armed)',
                        'spin_result': outcome_data['outcome'],
                        'bet_amount': 0.0, 'total_bet': 0.0, 'bets': [],
                        'result': 'SKIP', 'payout': 0.0, 'pnl': 0.0,
                        'balance_after': current_balance,
                        'trigger_reason': 'no candidate armed (parallel)',
                        'parallel_strategies': [],
                    })
                    balance_history.append({
                        'round': round_num, 'strategy': '(parallel: none armed)',
                        'balance': current_balance, 'change': 0.0,
                    })
                    trigger_engine.update(outcome_data['outcome'])
                    # Feed history to every cached engine so labels stay current.
                    import inspect as _inspect
                    for _eng in engines_by_base.values():
                        try:
                            inner = getattr(_eng, 'strategy', None)
                            if inner and hasattr(inner, 'record_result'):
                                _sig = _inspect.signature(inner.record_result)
                                if 'last_number' in _sig.parameters:
                                    inner.record_result(False, last_number=outcome_data['outcome'])
                        except Exception:
                            pass
                    continue

                # Gather bet plans from each armed candidate via its OWN engine.
                # Per-engine bet_amount + bet_amounts come from that engine's
                # progression (so martingale state is per-strategy, not shared).
                from core.strategy_engine import calculate_win_amount
                per_strat = []
                merged_bets = []
                for cand in cands:
                    eng = engines_by_base[cand.name]
                    try:
                        bet_amount = eng.get_next_bet()
                    except Exception:
                        bet_amount = 0.0
                    if bet_amount is None or bet_amount <= 0:
                        continue  # this strategy refused — sit it out, others still bet
                    try:
                        bets_dict = eng.get_bet_amounts() or {}
                    except Exception:
                        bets_dict = {}
                    cand_bets = [{'label': l, 'amount': float(a)} for l, a in bets_dict.items() if float(a) > 0]
                    if not cand_bets:
                        continue
                    cand_total = sum(b['amount'] for b in cand_bets)
                    per_strat.append({'name': cand.name, 'eng': eng,
                                       'bets': cand_bets, 'total_bet': cand_total})
                    merged_bets.extend(cand_bets)

                if not merged_bets:
                    # Every candidate refused — treat as skip (same as no-armed).
                    # CRITICAL: also feed the spin into each cached engine's
                    # inner strategy. Without this, composite strategies
                    # (which sit out most rounds) never accumulate spin
                    # history — their dominance/regime filters can never
                    # align and they refuse forever. This was the cause of
                    # 0-bet runs against full historical data.
                    bet_history.append({
                        'round': round_num, 'strategy': '(parallel: all refused)',
                        'spin_result': outcome_data['outcome'],
                        'bet_amount': 0.0, 'total_bet': 0.0, 'bets': [],
                        'result': 'SKIP', 'payout': 0.0, 'pnl': 0.0,
                        'balance_after': current_balance,
                        'trigger_reason': 'all candidates refused (parallel)',
                        'parallel_strategies': [],
                    })
                    balance_history.append({
                        'round': round_num, 'strategy': '(parallel: all refused)',
                        'balance': current_balance, 'change': 0.0,
                    })
                    trigger_engine.update(outcome_data['outcome'])
                    import inspect as _inspect3
                    for _eng in engines_by_base.values():
                        try:
                            inner = getattr(_eng, 'strategy', None)
                            if inner and hasattr(inner, 'record_result'):
                                _sig = _inspect3.signature(inner.record_result)
                                if 'last_number' in _sig.parameters:
                                    inner.record_result(False, last_number=outcome_data['outcome'])
                        except Exception:
                            pass
                    continue

                total_bet = sum(b['amount'] for b in merged_bets)
                if total_bet > current_balance:
                    print(f"💰 Insufficient balance at round {round_num} (parallel total ${total_bet:.2f} > ${current_balance:.2f}).")
                    session_stop_reason = "INSUFFICIENT_BALANCE"
                    session_stop_message = (f"Round {round_num}: parallel total ${total_bet:.2f} "
                                            f"exceeds balance ${current_balance:.2f}")
                    break

                # Compute aggregate result for the whole merged placement.
                bundle_win_amt, bundle_details = calculate_win_amount(merged_bets, outcome_data['outcome'])
                bundle_return = bundle_win_amt + sum(b['amount'] for b, d in zip(merged_bets, bundle_details) if d['win'])
                net_pnl = bundle_return - total_bet
                TOL = 1e-6
                is_break_even = (-TOL <= net_pnl <= TOL)
                is_win = (not is_break_even) and (net_pnl > TOL)
                is_loss = (not is_break_even) and (net_pnl < -TOL)

                current_balance += net_pnl
                if is_break_even:
                    pass
                elif is_win:
                    total_wins += 1
                    consecutive_wins += 1
                    consecutive_losses = 0
                    max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
                else:
                    total_losses += 1
                    consecutive_losses += 1
                    consecutive_wins = 0
                    max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)

                # Per-strategy attribution: each strategy's progression advances
                # based on its OWN labels' win/loss, not the bundle's. This is
                # what makes parallel mode actually parallel — strategy A can
                # martingale on its loss while strategy B resets on its win.
                profit_from_start = current_balance - initial_balance
                session_max_profit = max(session_max_profit, profit_from_start)
                session_max_loss = min(session_max_loss, profit_from_start)
                if profit_from_start > 0:
                    drawdown = session_max_profit - profit_from_start
                    max_drawdown = max(max_drawdown, drawdown)

                per_strat_results = []
                import inspect as _inspect2
                for ps in per_strat:
                    ps_win_amt, ps_details = calculate_win_amount(ps['bets'], outcome_data['outcome'])
                    ps_return = ps_win_amt + sum(b['amount'] for b, d in zip(ps['bets'], ps_details) if d['win'])
                    ps_pnl = ps_return - ps['total_bet']
                    ps_is_win = ps_pnl > TOL
                    # Update this strategy's progression
                    try:
                        if hasattr(ps['eng'], 'progression') and hasattr(ps['eng'].progression, 'session_high'):
                            ps['eng'].progression.session_high = session_max_profit
                        ps['eng'].record_result(ps_is_win, current_balance=current_balance,
                                                 winning_number=outcome_data['outcome'])
                    except TypeError:
                        try:
                            ps['eng'].record_result(ps_is_win, winning_number=outcome_data['outcome'])
                        except Exception:
                            pass
                    except Exception:
                        pass
                    per_strat_results.append({
                        'name': ps['name'], 'pnl': ps_pnl,
                        'result': 'WIN' if ps_is_win else 'LOSS',
                        'total_bet': ps['total_bet'],
                        'bets': [{'label': b['label'], 'amount': b['amount'],
                                  'win': bool(d.get('win'))}
                                 for b, d in zip(ps['bets'], ps_details)],
                    })

                bet_history.append({
                    'round': round_num,
                    'strategy': '+'.join(ps['name'] for ps in per_strat),
                    'spin_result': outcome_data['outcome'],
                    'bet_amount': total_bet / max(1, len(per_strat)),
                    'total_bet': total_bet,
                    'bets': [{'label': b['label'], 'amount': b['amount'],
                              'win': bool(d.get('win'))}
                             for b, d in zip(merged_bets, bundle_details)],
                    'result': 'WIN' if is_win else ('LOSS' if is_loss else 'BREAKEVEN'),
                    'payout': bundle_return,
                    'pnl': net_pnl,
                    'balance_after': current_balance,
                    'trigger_reason': f"{len(per_strat)} strategies armed (parallel)",
                    'parallel_strategies': per_strat_results,
                })
                balance_history.append({
                    'round': round_num,
                    'strategy': '+'.join(ps['name'] for ps in per_strat),
                    'balance': current_balance,
                    'change': net_pnl,
                })

                # Feed history to trigger engine + still-warm engines that
                # didn't bet (so their dominance/regime detectors stay current).
                trigger_engine.update(outcome_data['outcome'])
                _bet_names = {ps['name'] for ps in per_strat}
                for _base, _eng in engines_by_base.items():
                    if _base in _bet_names:
                        continue  # already updated via record_result above
                    try:
                        inner = getattr(_eng, 'strategy', None)
                        if inner and hasattr(inner, 'record_result'):
                            _sig = _inspect2.signature(inner.record_result)
                            if 'last_number' in _sig.parameters:
                                inner.record_result(False, last_number=outcome_data['outcome'])
                    except Exception:
                        pass

                # Session-manager + escalation tracking (use bundle-level result)
                if session_manager:
                    streak = consecutive_wins if consecutive_wins > 0 else -consecutive_losses
                    session_manager.update_state(pnl=profit_from_start, wins=total_wins,
                                                  losses=total_losses, streak=streak)
                    should_stop, reason, msg = session_manager.check_stop_conditions(
                        bot_running=True, last_result='win' if is_win else 'loss')
                    if should_stop:
                        print(f"🛑 Session Stop: {reason} - {msg} (Round {round_num}, parallel)")
                        session_stop_reason = reason
                        session_stop_message = msg
                        break
                elif profit_from_start <= -max_loss:
                    session_stop_reason = "STOP_LOSS"
                    session_stop_message = (f"Round {round_num}: profit_from_start "
                                             f"${profit_from_start:.2f} ≤ -${max_loss:.2f}")
                    break

                continue  # skip the legacy single-strategy round below

            # --- 0b. Conditional (tiebreaker single-pick) trigger pre-check ---
            # Decide which strategy is active this round (or skip the round
            # entirely) based on triggers + tiebreaker. Triggers evaluate
            # against spins observed BEFORE this round, so we read history
            # without peeking at round_num's outcome.
            trigger_decision = None
            if trigger_engine:
                labels_by_name: Dict[str, list] = {}
                for base, eng in engines_by_base.items():
                    try:
                        labels_by_name[base] = list(eng.get_bet_labels() or [])
                    except Exception:
                        labels_by_name[base] = []
                current_base = strategy_engine.strategy_name
                trigger_decision = trigger_engine.pick(labels_by_name, current_strategy=current_base)

                # Skip path — reveal & record the outcome for history, no bet placed.
                if trigger_decision.action == 'skip':
                    outcome_data = self.historical_data[round_num - 1]
                    bet_history.append({
                        'round': round_num,
                        'strategy': strategy_engine.strategy_name,
                        'spin_result': outcome_data['outcome'],
                        'bet_amount': 0.0,
                        'total_bet': 0.0,
                        'bets': [],
                        'result': 'SKIP',
                        'payout': 0.0,
                        'pnl': 0.0,
                        'balance_after': current_balance,
                        'trigger_reason': trigger_decision.reason,
                    })
                    balance_history.append({
                        'round': round_num,
                        'strategy': strategy_engine.strategy_name,
                        'balance': current_balance,
                        'change': 0.0,
                    })
                    trigger_engine.update(outcome_data['outcome'])
                    # Keep adaptive strategies' internal history in sync even when sitting out.
                    import inspect as _inspect
                    for _eng in engines:
                        try:
                            inner = getattr(_eng, 'strategy', None)
                            if inner and hasattr(inner, 'record_result'):
                                _sig = _inspect.signature(inner.record_result)
                                if 'last_number' in _sig.parameters:
                                    inner.record_result(False, last_number=outcome_data['outcome'])
                        except Exception:
                            pass
                    continue

                # Swap path — minimal swap: ONLY change which numbers the bot
                # bets on (inner strategy). The StrategyEngine wrapper, its
                # progression, dynamic_rules, session_high, consecutive_losses,
                # and martingale_level all stay intact. Mirrors the live
                # behavior so backtest matches what the live bot actually
                # does on a trigger fire.
                if trigger_decision.action == 'use' and trigger_decision.strategy:
                    target_base = trigger_decision.strategy
                    if target_base != strategy_engine.strategy_name and target_base in engines_by_base:
                        _target_eng = engines_by_base[target_base]
                        _old_name = strategy_engine.strategy_name
                        if getattr(_target_eng, 'strategy', None) is not None:
                            strategy_engine.strategy = _target_eng.strategy
                            strategy_engine.strategy_name = target_base
                            print(f"🎯 Trigger swap (labels only): {_old_name} → "
                                  f"{target_base} ({trigger_decision.reason}, round {round_num})")
                        else:
                            print(f"⚠️ Trigger pick {target_base} has no inner strategy — skipping swap")

            # --- 1. Get Strategy Bet ---
            bet_amount = strategy_engine.get_next_bet()

            # bet_amount <= 0 happens when the strategy engine refuses to bet
            # (e.g. consecutive_losses >= max_consec_losses, or unlicensed
            # without _ranking_simulation). Treat it as a graceful end-of-
            # session: mark the session as stopped so the rotation / multi-
            # sim glue fires, instead of breaking out of the whole campaign
            # which left some users with zero-session, no-graph results.
            if bet_amount <= 0:
                # Strategy refused to place a bet (max_consec_losses hit,
                # unlicensed, etc.). End this session cleanly — the campaign
                # loop in run_campaign owns cross-session rotation and
                # escalation, so we MUST NOT silently rotate-and-continue
                # here (that was hiding stop_loss events as "ROUNDS_EXHAUSTED"
                # and bypassing escalation entirely).
                print(f"🛑 Strategy stopped at round {round_num} due to limits (0 bet)")
                balance_history.append({
                    'round': round_num,
                    'strategy': strategy_engine.strategy_name,
                    'balance': current_balance,
                    'change': 0.0,
                })
                session_stop_reason = "MAX_CONSEC_LOSSES"
                session_stop_message = (f"Round {round_num}: strategy returned bet=0 "
                                         f"(usually max_consec_losses hit or strategy refused)")
                break

            if bet_amount > current_balance:
                print(f"💰 Insufficient balance at round {round_num}. Stopping.")
                session_stop_reason = "INSUFFICIENT_BALANCE"
                session_stop_message = (f"Round {round_num}: bet ${bet_amount:.2f} "
                                        f"exceeds balance ${current_balance:.2f}")
                break
                
            # --- 2. Get Outcome ---
            outcome_data = self.historical_data[round_num - 1]
            
            # --- 3. Calculate Result ---
            bets_dict = strategy_engine.get_bet_amounts()
            bets_list = [{'label': l, 'amount': a} for l, a in bets_dict.items()]
            current_total_bet = sum(b['amount'] for b in bets_list)
            
            from core.strategy_engine import calculate_win_amount
            win_amt, win_details = calculate_win_amount(bets_list, outcome_data['outcome'])
            
            # Calculate Total Return (Stake + Profit) since calculate_win_amount now results only Profit
            total_return = win_amt + sum(b['amount'] for b, d in zip(bets_list, win_details) if d['win'])
            
            pnl = total_return - current_total_bet
            # Match the live engine's classification: gains/losses under TOL
            # are treated as break-even and DO NOT advance the win/loss/
            # streak counters. Previously `is_win = pnl > 0` flipped a
            # \$0.000001 floating-point gain into a full WIN, breaking the
            # streak guardrails.
            TOL = 1e-6
            is_break_even = (-TOL <= pnl <= TOL)
            is_win = (not is_break_even) and (pnl > TOL)

            current_balance += pnl

            if is_break_even:
                payout = total_return
                # Don't touch streaks — break-even is neutral.
            elif is_win:
                total_wins += 1
                consecutive_wins += 1
                consecutive_losses = 0
                max_consecutive_wins = max(max_consecutive_wins, consecutive_wins)
                payout = total_return
            else:
                total_losses += 1
                consecutive_losses += 1
                consecutive_wins = 0
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
                payout = total_return

            # --- 4. Record & Update ---
            # Per-label bet breakdown is preserved so the GUI's Round Audit
            # can render chip placement on the synthetic board. Each entry
            # also carries a `win` flag (from calculate_win_amount's details)
            # so the board can highlight winning chips in gold.
            bet_history.append({
                'round': round_num,
                'strategy': strategy_engine.strategy_name,
                'spin_result': outcome_data['outcome'],
                'bet_amount': bet_amount,
                'total_bet': current_total_bet,
                'bets': [
                    {'label': b['label'], 'amount': b['amount'],
                     'win': bool(d.get('win'))}
                    for b, d in zip(bets_list, win_details)
                ],
                'result': 'WIN' if is_win else 'LOSS',
                'payout': payout,
                'pnl': pnl,
                'balance_after': current_balance,
                'trigger_reason': (trigger_decision.reason if trigger_decision and trigger_decision.action == 'use' else None),
            })

            # Feed the spin into the trigger engine so the NEXT round's
            # condition checks see this outcome.
            if trigger_engine:
                trigger_engine.update(outcome_data['outcome'])

            # Metrics Update (Moved Up for Dynamic Sync)
            profit_from_start = current_balance - initial_balance
            session_max_profit = max(session_max_profit, profit_from_start)
            session_max_loss = min(session_max_loss, profit_from_start)

            if profit_from_start > 0:
                drawdown = session_max_profit - profit_from_start
                max_drawdown = max(max_drawdown, drawdown)

            # Sync Global High to Strategy (Crucial for Rotation)
            if hasattr(strategy_engine.progression, 'session_high'):
                 strategy_engine.progression.session_high = session_max_profit

            # (removed: a debug-print block that fired on round_num == 207
            # and accessed `.rules` without a hasattr guard — it threw
            # AttributeError for FlatStrategy / any non-dynamic progression
            # the moment a backtest exceeded 206 rounds, which is the
            # "graph stops working past 200" bug a user hit.)

            # Only update progression when the strategy actually placed a bet. Sit-outs
            # (empty bets_list) must not advance the progression — otherwise martingale
            # treats every NEUTRAL spin as a phantom loss and doubles.
            if bets_list:
                strategy_engine.record_result(is_win, current_balance, winning_number=outcome_data['outcome'])
            else:
                # Strategy still needs every spin's history (pattern detectors). Feed
                # winning number directly to the inner strategy if it accepts last_number.
                if hasattr(strategy_engine, 'strategy') and hasattr(strategy_engine.strategy, 'record_result'):
                    import inspect
                    sig = inspect.signature(strategy_engine.strategy.record_result)
                    if 'last_number' in sig.parameters:
                        try:
                            strategy_engine.strategy.record_result(False, last_number=outcome_data['outcome'])
                        except TypeError:
                            pass
            
            balance_change = current_balance - balance_history[-1]['balance']
            balance_history.append({
                'round': round_num,
                'strategy': strategy_engine.strategy_name,
                'balance': current_balance,
                'change': balance_change
            })

            # --- 4b. On-loss rotation (mirrors live's rebuild_strategy_on_loss) ---
            # The session-end rotation block further down only runs when the
            # session stops. Bundles with rotation_trigger='on_loss' need
            # mid-session switching — without this, strat1 keeps running
            # despite the bundle's intent.
            #
            # Skipped when TriggerEngine is in charge: conditional swaps are
            # the selection mechanism; layering on_loss rotation on top would
            # double-swap and defeat the trigger's tiebreaker logic.
            if (trigger_engine is None
                    and rotation_config
                    and rotation_config.get('trigger') == 'on_loss'
                    and not is_win
                    and not is_break_even
                    and len(engines) > 1):
                threshold = max(1, int(rotation_config.get('switch_after_n_losses', 1) or 1))
                if consecutive_losses >= threshold:
                    prev_idx = active_engine_idx
                    if rotation_mode == 'random':
                        import random as _r
                        active_engine_idx = _r.randint(0, len(engines) - 1)
                    else:
                        # sequential / smart_ranking* — advance one slot
                        active_engine_idx = (active_engine_idx + 1) % len(engines)
                    if prev_idx != active_engine_idx:
                        old_engine = strategy_engine
                        strategy_engine = engines[active_engine_idx]
                        carry = bool(rotation_config.get('carry_progression_on_switch', False))
                        if carry:
                            # Mirror main_gui.rebuild_strategy_on_loss exactly: the
                            # new engine inherits progression + streak so recovery
                            # bet sizing continues across the swap.
                            try:
                                strategy_engine.progression = old_engine.progression
                                strategy_engine.consecutive_losses = old_engine.consecutive_losses
                                strategy_engine.total_loss = old_engine.total_loss
                            except Exception:
                                pass
                        else:
                            # Fresh start on the new strategy. Also reset the
                            # local counter so max_consec_losses fires per-strategy
                            # rather than carrying across rotations.
                            try:
                                strategy_engine.consecutive_losses = 0
                                strategy_engine.total_loss = 0.0
                            except Exception:
                                pass
                            consecutive_losses = 0
                        print(f"🔀 On-loss rotation: {old_engine.strategy_name} → {strategy_engine.strategy_name} "
                              f"(carry={carry}, round {round_num})")

            # --- 5. Session Manager Check ---
            should_stop_session = False
            if session_manager:
                streak = consecutive_wins if consecutive_wins > 0 else -consecutive_losses
                session_manager.update_state(pnl=profit_from_start, wins=total_wins, losses=total_losses, streak=streak)
                # Drive synthetic wall-clock so TIME_LIMIT fires deterministically
                # in backtest. Each round consumes (60 / spins_per_minute) seconds
                # of bundle time — at 1.5 spins/min that's 40s per round, so a
                # 1-min bundle session reaches TIME_LIMIT after ~2 rounds and
                # session_ext_at_high gets a chance to defer/extend it.
                try:
                    spm = float(session_config.get("spins_per_minute", 30) or 30) if session_config else 30.0
                    if spm <= 0:
                        spm = 30.0
                    session_manager.simulated_elapsed_seconds = round_num * (60.0 / spm)
                except Exception:
                    pass
                should_stop, reason, msg = session_manager.check_stop_conditions(bot_running=True, last_result='win' if is_win else 'loss')
                if should_stop:
                    print(f"🛑 Session Stop: {reason} - {msg} (Round {round_num})")
                    session_stop_reason = reason
                    session_stop_message = msg
                    should_stop_session = True
            else:
                if profit_from_start <= -max_loss:
                    print(f"🛑 Max loss reached at round {round_num}.")
                    session_stop_reason = "STOP_LOSS"
                    session_stop_message = (f"Round {round_num}: profit_from_start "
                                             f"${profit_from_start:.2f} ≤ -${max_loss:.2f}")
                    should_stop_session = True
                elif max_consec_losses and consecutive_losses >= max_consec_losses:
                    print(f"🛑 Max consecutive losses reached.")
                    session_stop_reason = "MAX_CONSEC_LOSSES"
                    session_stop_message = (f"Round {round_num}: {consecutive_losses} "
                                             f"consecutive losses (cap {max_consec_losses})")
                    should_stop_session = True

            # Session is over — end it. The campaign loop (run_campaign) owns
            # cross-session rotation, escalation, and balance carry-over. The
            # old code here rotated internally and reset initial_balance,
            # which silently merged multiple sessions into one BacktestResult
            # — hiding stop_loss events as "ROUNDS_EXHAUSTED" and starving
            # the campaign-level escalation of its trigger signal. Bug fix:
            # always break out so each backtest_strategy call = exactly one
            # session, as every caller (run_campaign, run_multiple_backtests,
            # standalone tests) expects.
            if should_stop_session:
                break
        
        # Calculate final metrics
        session_duration = time.time() - start_time
        total_rounds = len(bet_history)
        win_rate = (total_wins / total_rounds * 100) if total_rounds > 0 else 0
        total_profit = current_balance - initial_balance
        roi = (total_profit / initial_balance * 100) if initial_balance > 0 else 0
        
        # Calculate Sharpe ratio (simplified)
        if balance_history:
            returns = [h['change'] for h in balance_history[1:]]
            if returns:
                avg_return = sum(returns) / len(returns)
                std_return = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
                sharpe_ratio = avg_return / std_return if std_return > 0 else 0
            else:
                sharpe_ratio = 0
        else:
            sharpe_ratio = 0
        
        # Create result object — including the new audit fields. effective_*
        # mirror what the session actually ran with (handy when escalation is
        # off; run_campaign overwrites these post-return when escalation is
        # on, since it owns the effective base_bet/max_loss state machine).
        result = BacktestResult(
            strategy_name=strategy_name,
            initial_balance=initial_balance,
            base_bet=base_bet,
            total_rounds=total_rounds,
            total_wins=total_wins,
            total_losses=total_losses,
            win_rate=win_rate,
            total_profit=total_profit,
            max_profit=session_max_profit,
            max_loss=session_max_loss,
            max_drawdown=max_drawdown,
            consecutive_wins=max_consecutive_wins,
            consecutive_losses=max_consecutive_losses,
            final_balance=current_balance,
            roi=roi,
            sharpe_ratio=sharpe_ratio,
            bet_history=bet_history,
            balance_history=balance_history,
            session_duration=session_duration,
            stop_reason=session_stop_reason,
            stop_message=session_stop_message,
            effective_base_bet=base_bet,
            effective_max_loss=max_loss,
            escalation_step=0,
        )

        return result
    
    def run_multiple_backtests(self, 
                              strategy_configs: List[Dict],
                              num_simulations: int = 10,
                              rounds_per_simulation: int = 100) -> Dict[str, List[BacktestResult]]:
        """
        Run multiple backtests with different configurations and seeds
        """
        results = {}
        
        for config in strategy_configs:
            strategy_name = config['strategy_name']
            results[strategy_name] = []
            
            print(f"🔄 Running {num_simulations} simulations for {strategy_name}...")
            
            for sim in range(num_simulations):
                seed = random.randint(1, 10000)  # Random seed for each simulation
                
                result = self.backtest_strategy(
                    strategy_name=config['strategy_name'],
                    base_bet=config['base_bet'],
                    initial_balance=config.get('initial_balance', 100.0),
                    num_rounds=rounds_per_simulation,
                    progression_type=config.get('progression_type', 'martingale'),
                    max_loss=config.get('max_loss', 50.0),
                    max_bet=config.get('max_bet'),
                    max_consec_losses=config.get('max_consec_losses'),
                    custom_strategies=config.get('custom_strategies'),
                    dynamic_rules=config.get('dynamic_rules'),
                    custom_sequence=config.get('custom_sequence'),
                    dalembert_step=config.get('dalembert_step', 1),
                    seed=seed
                )
                
                results[strategy_name].append(result)
                
                if (sim + 1) % 5 == 0:
                    print(f"   Completed {sim + 1}/{num_simulations} simulations")
        
        return results
    
    def analyze_results(self, results: Dict[str, List[BacktestResult]]) -> Dict[str, Dict]:
        """
        Analyze and compare multiple backtest results
        """
        analysis = {}
        
        for strategy_name, strategy_results in results.items():
            if not strategy_results:
                continue
                
            # Calculate aggregate statistics
            profits = [r.total_profit for r in strategy_results]
            win_rates = [r.win_rate for r in strategy_results]
            rois = [r.roi for r in strategy_results]
            max_drawdowns = [r.max_drawdown for r in strategy_results]
            sharpe_ratios = [r.sharpe_ratio for r in strategy_results]
            
            total_rounds_list = [r.total_rounds for r in strategy_results]
            bankruptcies = sum(1 for r in strategy_results if r.final_balance <= 0.1) # Assuming < min bet is bust

            analysis[strategy_name] = {
                'avg_profit': sum(profits) / len(profits),
                'avg_pnl': sum(profits) / len(profits), # Alias
                'total_pnl_all_sims': sum(profits),
                'min_profit': min(profits),
                'max_profit': max(profits),
                'profit_std': (sum((p - sum(profits)/len(profits))**2 for p in profits) / len(profits))**0.5,
                'avg_win_rate': sum(win_rates) / len(win_rates),
                'win_rate': sum(win_rates) / len(win_rates), # Alias
                'avg_roi': sum(rois) / len(rois),
                'roi_pct': sum(rois) / len(rois), # Alias
                'avg_max_drawdown': sum(max_drawdowns) / len(max_drawdowns),
                'max_drawdown': max(max_drawdowns) if max_drawdowns else 0.0, # Worst case
                'avg_sharpe_ratio': sum(sharpe_ratios) / len(sharpe_ratios),
                'profitable_simulations': sum(1 for p in profits if p > 0),
                'num_simulations': len(strategy_results),
                'success_rate': sum(1 for p in profits if p > 0) / len(strategy_results) * 100,
                'avg_rounds': sum(total_rounds_list) / len(total_rounds_list),
                'bankruptcies': bankruptcies,
                'bankruptcy_rate': (bankruptcies / len(strategy_results)) * 100
            }
        
        return analysis
    
    def generate_report(self, results: Dict[str, List[BacktestResult]], 
                       analysis: Dict[str, Dict]) -> str:
        """
        Generate a comprehensive backtesting report
        """
        report = []
        report.append("=" * 60)
        report.append("🎰 ROULETTE STRATEGY BACKTESTING REPORT")
        report.append("=" * 60)
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("")
        
        # Summary table
        report.append("📊 STRATEGY COMPARISON SUMMARY")
        report.append("-" * 60)
        report.append(f"{'Strategy':<15} {'Avg Profit':<12} {'Win Rate':<10} {'ROI %':<8} {'Success %':<10}")
        report.append("-" * 60)
        
        for strategy_name, stats in analysis.items():
            report.append(f"{strategy_name:<15} "
                         f"${stats['avg_profit']:<11.2f} "
                         f"{stats['avg_win_rate']:<9.1f}% "
                         f"{stats['avg_roi']:<7.1f}% "
                         f"{stats['success_rate']:<9.1f}%")
        
        report.append("")
        
        # Detailed analysis for each strategy
        for strategy_name, strategy_results in results.items():
            if not strategy_results:
                continue
                
            report.append(f"🎯 DETAILED ANALYSIS: {strategy_name.upper()}")
            report.append("-" * 40)
            
            # Sample result for detailed stats
            sample_result = strategy_results[0]
            report.append(f"Base Bet: ${sample_result.base_bet}")
            report.append(f"Rounds per Simulation: {sample_result.total_rounds}")
            report.append(f"Total Simulations: {len(strategy_results)}")
            report.append("")
            
            # Performance metrics
            stats = analysis[strategy_name]
            report.append("📈 PERFORMANCE METRICS:")
            report.append(f"  Average Profit: ${stats['avg_profit']:.2f}")
            report.append(f"  Profit Range: ${stats['min_profit']:.2f} to ${stats['max_profit']:.2f}")
            report.append(f"  Profit Std Dev: ${stats['profit_std']:.2f}")
            report.append(f"  Average Win Rate: {stats['avg_win_rate']:.1f}%")
            report.append(f"  Average ROI: {stats['avg_roi']:.1f}%")
            report.append(f"  Success Rate: {stats['success_rate']:.1f}%")
            report.append(f"  Average Max Drawdown: ${stats['avg_max_drawdown']:.2f}")
            report.append(f"  Average Sharpe Ratio: {stats['avg_sharpe_ratio']:.3f}")
            report.append("")
            
            # Risk analysis
            report.append("⚠️ RISK ANALYSIS:")
            profitable_sims = stats['profitable_simulations']
            total_sims = stats['total_simulations']
            report.append(f"  Profitable Simulations: {profitable_sims}/{total_sims}")
            report.append(f"  Risk of Loss: {((total_sims - profitable_sims) / total_sims * 100):.1f}%")
            report.append("")
        
        report.append("=" * 60)
        report.append("📝 NOTES:")
        report.append("- Results are based on simulated roulette outcomes")
        report.append("- Win/loss determination is simplified for testing")
        report.append("- Real-world results may vary significantly")
        report.append("- Always test with small amounts first")
        report.append("=" * 60)
        
        return "\n".join(report)
    
    def save_results(self, results: Dict[str, List[BacktestResult]], 
                    filename: str = None) -> str:
        """
        Save backtesting results to a JSON file
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_results_{timestamp}.json"
        
        # Convert results to serializable format
        serializable_results = {}
        for strategy_name, strategy_results in results.items():
            serializable_results[strategy_name] = []
            for result in strategy_results:
                serializable_results[strategy_name].append({
                    'strategy_name': result.strategy_name,
                    'base_bet': result.base_bet,
                    'total_rounds': result.total_rounds,
                    'total_wins': result.total_wins,
                    'total_losses': result.total_losses,
                    'win_rate': result.win_rate,
                    'total_profit': result.total_profit,
                    'max_profit': result.max_profit,
                    'max_loss': result.max_loss,
                    'max_drawdown': result.max_drawdown,
                    'consecutive_wins': result.consecutive_wins,
                    'consecutive_losses': result.consecutive_losses,
                    'final_balance': result.final_balance,
                    'roi': result.roi,
                    'sharpe_ratio': result.sharpe_ratio,
                    'session_duration': result.session_duration
                })
        
        with open(filename, 'w') as f:
            json.dump(serializable_results, f, indent=2)
        
        return filename 