import logging
import statistics
import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from core.strategy_engine import StrategyEngine
from core.analysis.regime_detector import RegimeDetector

logger = logging.getLogger(__name__)

@dataclass
class SimResult:
    """Stores the metrics of a virtual strategy simulation."""
    strategy_name: str
    final_pnl: float
    win_rate: float
    max_drawdown: float
    volatility: float
    bet_count: int
    
    def to_dict(self):
        return {
            "name": self.strategy_name,
            "pnl": self.final_pnl,
            "win_rate": self.win_rate,
            "max_dd": self.max_drawdown,
            "volatility": self.volatility,
            "bets": self.bet_count
        }

class RankingEngine:
    """
    Implements a Multi-Factor Probabilistic Scoring Model to rank strategies
    based on their virtual performance against recent table history.
    """
    
    def __init__(self, custom_strategies: Optional[dict] = None):
        """
        :param custom_strategies: Dictionary of custom strategy definitions (needed to load them for simulation)
        """
        self.custom_strategies = custom_strategies or {}
        self.regime_detector = RegimeDetector()

    def rank_strategies(self, strategy_names: List[str], history_data: List[int], start_balance: float = 1000.0, filter_by_regime: bool = False) -> List[Dict[str, Any]]:
        """
        Simulate and rank the provided strategies against the historical data.
        
        :param strategy_names: List of strategy names to evaluate.
        :param history_data: List of winning numbers (e.g., [23, 4, 0, 35...]).
        :param start_balance: Virtual starting balance for simulation.
        :return: List of results sorted by Score (descending).
        """
        if not strategy_names or not history_data:
            logger.warning("[RankingEngine] Insufficient data/strategies for ranking.")
            return []

        # --- REGIME FILTERING ---
        current_regimes = {} # Dict of "Dimension" -> "State"
        if filter_by_regime:
            try:
                # Use new Multi-Dimension Detection
                if hasattr(self.regime_detector, 'detect_all_regimes'):
                    current_regimes = self.regime_detector.detect_all_regimes(history_data)
                else:
                    # Fallback
                    current_regimes = {"Colors": self.regime_detector.detect_state(history_data)}
                
                # Filter candidates
                filtered_names = []
                for name in strategy_names:
                    # Look up strategy definition from self.custom_strategies
                    strat_def = self.custom_strategies.get(name)
                    if strat_def:
                        # 1. Check explicit tags first (User override)
                        tags = strat_def.get("regime_tags", [])
                        
                        # 2. INFER dimension from labels (ALWAYS, to find correct regime key)
                        labels = strat_def.get("labels", [])
                        target_dimension = self._infer_dimension(labels)
                        # Override inference if user specified a dimension tag? No, tags are TRENDING/CHOPPY.
                            
                        # 3. Get Relevant State
                        # If tags exist, they match against ANY regime state value (e.g. "TRENDING") finding a match in current_regimes values?
                        # Actually, explicit tags are ["TRENDING"]. We check if the strategy's target dimension is currently TRENDING.
                        # Wait, the tags are "What regime this strategy likes".
                        
                        relevant_state = "NEUTRAL"
                        if target_dimension in current_regimes:
                            relevant_state = current_regimes[target_dimension]
                        
                        # Logic:
                        # - If explicit tags exist (e.g. ["TRENDING"]), we check if relevant_state is in tags.
                        # - If NO tags exist, we assume the strategy is "Trend Following" by default? 
                        #   Or do we just skip filtering?
                        #   Better: If no tags, we can't safely filter. Allow it.
                        #   UNLESS we infer "Martingale" -> Likes CHOPPY. "Paroli" -> Likes TRENDING.
                        
                        # Revised Logic:
                        # If explicit tags: Check if relevant_state is in tags.
                        # If NO tags: Allow (Neutral).
                        
                        if tags:
                            if "NEUTRAL" in tags or relevant_state in tags:
                                filtered_names.append(name)
                        else:
                            # Auto-Inference for un-tagged strategies?
                            # For now, let's include them to be safe.
                            filtered_names.append(name)
                    else:
                        filtered_names.append(name)
                
                if filtered_names:
                    logger.info(f"[RankingEngine] Regimes: {current_regimes}. Filtering: {len(strategy_names)} -> {len(filtered_names)}")
                    strategy_names = filtered_names
                else:
                    logger.warning(f"[RankingEngine] Regimes: {current_regimes}. Filter removed ALL strategies! Falling back to full list.")
                    
            except Exception as e:
                logger.error(f"[RankingEngine] Regime Filtering Error: {e}")

        results = []
        
        # 1. Simulate Each Strategy
        for name in strategy_names:
            try:
                sim_res = self._simulate_strategy(name.strip(), history_data, start_balance)
                if sim_res:
                    results.append(sim_res)
            except Exception as e:
                logger.error(f"[RankingEngine] Failed to simulate {name}: {e}")

        if not results:
            return []

        # 2. Normalize and Score
        scored_results = self._calculate_scores(results)
        
        # 3. Sort by Score (Descending)
        scored_results.sort(key=lambda x: x['score'], reverse=True)
        
        return scored_results

    def _infer_dimension(self, labels: List[str]) -> str:
        """
        Infers the betting dimension (Colors, Dozens, etc.) based on the strategy labels.
        """
        if not labels: return "Colors"
        
        # Check first label sample
        sample = str(labels[0]).lower()
        
        if sample in ["red", "black"]: return "Colors"
        if "12" in sample: return "Dozens" # 1st12, 2nd12
        if "col" in sample: return "Columns"
        if sample in ["even", "odd"]: return "EvenOdd"
        if "to" in sample: return "HighLow" # 1to18
        
        return "Colors" # Default fallback

    def _simulate_strategy(self, name: str, history: List[int], start_balance: float) -> Optional[SimResult]:
        """
        Runs a lightweight virtual session for a single strategy.
        """
        # Create a FRESH instance of the strategy engine
        # We use a flat progression for ranking to test PURE STRATEGY POWER, 
        # or we could use the default progression. 
        # For now, let's use 'flat' to isolate strategy prediction power from martingale luck.
        # UPDATE: The prompt implies simulating the "strategy bundle" which might include progression.
        # But 'name' here is just the strategy name. 
        # Ideally, we should rank the "Strategy Logic" itself. 
        # A Flat progression is the scientific control.
        
        try:
            engine = StrategyEngine(
                strategy_name=name,
                base_bet=1.0,
                progression_type="flat", # Use flat to measure pure predictive power
                max_loss=10000,
                custom_strategies=self.custom_strategies
            )
            # Bypass license check for ranking simulation (read-only, no real bets)
            engine._ranking_simulation = True
            logger.info(f"[RankingEngine] Instantiated strategy '{name}' OK, labels={engine.get_bet_labels()}")
        except Exception as e:
            logger.warning(f"[RankingEngine] Could not instantiate strategy {name}: {e}")
            return None

        virtual_balance = start_balance
        peak_balance = start_balance
        equity_curve = [start_balance]
        
        wins = 0
        bets_placed = 0
        
        # Replay History
        # Note: Strategy needs to 'see' the number BEFORE betting if it's pattern based.
        # But for the *first* number, it can't bet. 
        # So loop is: Record Result -> Place Bet -> Next Number checks result.
        
        # Pre-feed data? 
        # If strategy needs K numbers to start steps, we feed them.
        # Let's iterate through history.
        
        for i, number in enumerate(history):
            # 1. Ask Engine for Bet (based on PREVIOUS state)
            # Check if engine has bet.
            
            # For the first few spins, the strategy might just be observing.
            bet_amount = engine.get_next_bet()
            bet_labels = engine.get_bet_labels()
            
            wagered = 0.0
            payout = 0.0
            
            if bet_amount > 0 and bet_labels:
                # Calculate virtual wager
                # Flat bet = 1.0 per unit. If multiple labels, bet_amount might be total or per label?
                # StrategyEngine.get_total_bet_amount() handles this.
                wagered = engine.get_total_bet_amount()
                
                # Check Win
                is_win = engine.is_winning_number(number)
                
                if is_win:
                    # Calculate payout
                    # Use helper from strategy_engine or approximation
                    # calculate_win_amount returns (total_win, details)
                    from core.strategy_engine import calculate_win_amount
                    
                    # Construct bet dict for calculation
                    # StrategyEngine usually gives per-label amount via get_bet_amounts()
                    bet_map = engine.get_bet_amounts() 
                    bets_for_calc = [{'label': l, 'amount': a} for l, a in bet_map.items()]
                    
                    win_amt, _ = calculate_win_amount(bets_for_calc, number)
                    payout = win_amt
                    wins += 1
                
                bets_placed += 1
                
            # Update Balance
            virtual_balance -= wagered
            virtual_balance += payout
            equity_curve.append(virtual_balance)
            
            if virtual_balance > peak_balance:
                peak_balance = virtual_balance
            
            # 2. Inform Engine of Result (so it updates for NEXT spin)
            # Note: We must update engine even if we didn't bet, so it tracks patterns.
            # Pass current_balance so DynamicProgressionStrategy can compute profit
            engine.record_result(payout > 0, current_balance=virtual_balance)
            # AND we must update the strategy internal state with the number
            if hasattr(engine.strategy, 'record_result'):
                # Some strategies take (win, last_number)
                # Inspecting code: StrategyBase often just takes win, 
                # but pattern strategies need the number. 
                # StrategyEngine.record_result does NOT pass number to internal strategy.winning...
                # Wait, StrategyEngine.record_result call:
                # self.strategy.record_result(win)
                # It does NOT pass number.
                # HOWEVER, pattern strategies usually need `last_numbers`.
                # Let's check `DynamicNineStreetStrategy` or similar.
                # If they need the number, `StrategyEngine` might be missing a param in `record_result`?
                # Or they rely on the user calling something else.
                # In `main_gui.py`, it does: `strategy.strategy.record_result(False, last_number=winning_number)`
                # AHA! The GUI bypasses the Engine to feed the number to the inner strategy.
                try:
                    engine.strategy.record_result(payout > 0, last_number=number)
                except TypeError:
                    # Fallback for strategies that don't accept last_number
                    engine.strategy.record_result(payout > 0)

        # Drawdown Calculation
        # Max % drop from Peak
        max_dd = 0.0
        current_peak = equity_curve[0]
        for bal in equity_curve:
            if bal > current_peak:
                current_peak = bal
            dd = (current_peak - bal) / current_peak
            if dd > max_dd:
                max_dd = dd
        
        # Volatility (StdDev of returns)
        # Returns = diff between equity steps
        diffs = [equity_curve[i] - equity_curve[i-1] for i in range(1, len(equity_curve))]
        if len(diffs) > 1:
            volatility = statistics.stdev(diffs)
        else:
            volatility = 0.0

        # Bayesian Win Rate (Smoothed)
        # (Wins + 1) / (Bets + 2)
        smoothed_win_rate = (wins + 1) / (bets_placed + 2)

        logger.info(f"[RankingEngine] Sim '{name}': bets={bets_placed}, wins={wins}, pnl={virtual_balance - start_balance:.2f}, wr={smoothed_win_rate:.3f}")
        return SimResult(
            strategy_name=name,
            final_pnl=virtual_balance - start_balance,
            win_rate=smoothed_win_rate,
            max_drawdown=max_dd,
            volatility=volatility,
            bet_count=bets_placed
        )

    def _calculate_scores(self, results: List[SimResult]) -> List[Dict[str, Any]]:
        """
        Normalize metrics and compute Alpha Score.
        Score = (1.0 * Z_PnL) + (0.5 * Z_WinRate) - (1.5 * Z_MaxDD)
        """
        # Extract vectors
        pnls = [r.final_pnl for r in results]
        win_rates = [r.win_rate for r in results]
        dds = [r.max_drawdown for r in results]
        
        # Helper for Z-Score
        def z_score(val, data):
            if len(data) < 2: return 0.0
            mean = statistics.mean(data)
            stdev = statistics.stdev(data)
            if stdev == 0: return 0.0
            return (val - mean) / stdev

        scored = []
        for r in results:
            z_pnl = z_score(r.final_pnl, pnls)
            z_wr = z_score(r.win_rate, win_rates)
            z_dd = z_score(r.max_drawdown, dds)
            
            # The Formula (as per PhD design)
            # We want High PnL (+), High WinRate (+), Low DD (-)
            # Since Z_DD is positive for high drawdown (bad), subtracting it works.
            alpha_score = (1.0 * z_pnl) + (0.5 * z_wr) - (1.5 * z_dd)
            
            # Volatility Penalty? 
            # Design doc said: Factor B: Risk-Adjusted Returns (Sharpe).
            # Effectively capturing PnL/Vol is better than raw PnL.
            # Let's add a small Volatility penalty directly if PnL is positive.
            
            item = r.to_dict()
            item['score'] = alpha_score
            scored.append(item)
            
        return scored
