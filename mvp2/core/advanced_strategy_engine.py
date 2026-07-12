import logging
import random
from typing import List, Dict, Any, Optional, Union
from core.utils.db_utils import (
    get_recent_winning_numbers, 
    get_number_frequency, 
    get_sector_stats,
    get_gap_stats
)
from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS

logger = logging.getLogger(__name__)

class AdvancedStrategyEngine:
    """
    Universal Logic Engine for executing user-defined strategies.
    Parses a JSON schema defining Variables, Rules, Conditions, and Actions.
    """
    def __init__(self, strategy_config: Dict[str, Any], base_bet: float, virtual_manager=None):
        self.config = strategy_config
        self.name = strategy_config.get("name", "Unnamed Strategy")
        self.variables_conf = strategy_config.get("variables", {})
        self.rules_conf = strategy_config.get("rules", [])
        self.base_bet = base_bet
        self.virtual_manager = virtual_manager
        
        # Runtime state
        self.variables = {} 
        self.current_bets = [] # List of bet dicts for the current round

    def update_state(self):
        """
        Refresh variable values based on latest DB data.
        This must be called before evaluating rules for a new round.
        """
        # Resolve all defined variables
        for var_name, var_def in self.variables_conf.items():
            self.variables[var_name] = self._resolve_variable(var_def)
            
        logger.debug(f"[{self.name}] Updated Variables: {self.variables}")

    def _resolve_variable(self, var_def: Dict[str, Any]) -> Any:
        v_type = var_def.get("type")
        
        if v_type == "strategy_metric":
            # { "type": "strategy_metric", "target": "Martingale", "metric": "loss_streak" }
            if not self.virtual_manager:
                return 0
            target_strategy = var_def.get("target")
            metric = var_def.get("metric")
            return self.virtual_manager.get_metric(target_strategy, metric)

        elif v_type == "gap_since_last":
            # { "type": "gap_since_last", "target": "red" }
            target = var_def.get("target")
            limit = var_def.get("limit", 200)
            gaps = get_gap_stats(limit=limit)
            
            # Search in all categories
            for category in gaps:
                if target in gaps[category]:
                    return gaps[category][target]
            return limit # Not found -> Max limit

        elif v_type == "last_outcome":
            # { "type": "last_outcome", "property": "color"|"number"|"dozen"|... }
            prop = var_def.get("property", "number")
            count = var_def.get("count", 1) # Support getting last N outcomes
            
            recent = get_recent_winning_numbers(limit=count)
            if not recent:
                return None
                
            if count == 1:
                item = recent[0]
                if prop == "number": return item['number']
                if prop == "color": return item['color']
                # Helper to derive dozen/column/etc from number
                return self._derive_property(item['number'], prop)
            else:
                # Return list of historical values
                return [self._derive_property(r['number'], prop) if prop != 'color' else r['color'] for r in recent]

        elif v_type == "streak_count":
            # { "type": "streak_count", "target": "red"|... }
            target = var_def.get("target")
            recent = get_recent_winning_numbers(limit=50) # Look back far enough
            count = 0
            for r in recent:
                # complex check: target can be 'red' (color) or '1st12' (derived)
                if self._check_hit(r, target):
                    count += 1
                else:
                    break
            return count

        elif v_type == "statistical_rank":
            # { "type": "statistical_rank", "target": "number"|"dozen", "metric": "coldest"|"hottest", "count": 1 }
            target_type = var_def.get("target", "number")
            metric = var_def.get("metric", "coldest")
            count = var_def.get("count", 1)
            
            if target_type == "number":
                stats = get_number_frequency(limit=100) # Last 100 spins
                # stats is sorted by count desc (Hot)
                if metric == "hottest":
                    res = [s['number'] for s in stats[:count]]
                else: # coldest
                    res = [s['number'] for s in stats[-count:]]
                return res[0] if count == 1 else res
                
        return None

    def _derive_property(self, number: int, prop: str) -> str:
        """Helper to get dozen/column/parity for a number"""
        if prop == "number": return number
        
        if prop == "dozen":
            if 1 <= number <= 12: return "1st12"
            if 13 <= number <= 24: return "2nd12"
            if 25 <= number <= 36: return "3rd12"
            return None
            
        if prop == "column":
            if number in ROULETTE_NUMBER_MAPPINGS["col1"]: return "col1"
            if number in ROULETTE_NUMBER_MAPPINGS["col2"]: return "col2"
            if number in ROULETTE_NUMBER_MAPPINGS["col3"]: return "col3"
            return None
            
        return None

    def _check_hit(self, record: Dict, label: str) -> bool:
        """Check if a number record matches a betting label"""
        number = record['number']
        color = record['color']
        label = str(label).lower()
        
        if label in ['red', 'black']:
            return color == label
            
        if label in ROULETTE_NUMBER_MAPPINGS:
            return number in ROULETTE_NUMBER_MAPPINGS[label]

        return False

    def get_next_bets(self) -> List[Dict[str, Any]]:
        """
        Evaluate all rules and return list of bets.
        Returns: [ { "label": "red", "amount": 10.0 }, ... ]
        """
        self.update_state()
        bets = []
        
        # Meta-action result
        active_strategy_override = None
        
        for rule in self.rules_conf:
            if self._evaluate_condition(rule.get("condition")):
                action = rule.get("action")
                result = self._execute_action(action)
                
                if result:
                    if result.get("type") == "bet":
                        bets.append(result["data"])
                    elif result.get("type") == "activate_strategy":
                        active_strategy_override = result["data"]
                        # If we switch strategy, maybe we stop processing other rules?
                        # For now, let's allow multiple actions but strategy switch is singular.
        
        if active_strategy_override:
             # If a rule says "Activate Martingale", we should return that.
             # But this engine returns BETS.
             # So we need a way to signal "Use this other strategy engine for bets".
             # Or we ask that other engine for bets right here.
             if self.virtual_manager:
                  target_strat = active_strategy_override
                  engine = self.virtual_manager.strategies.get(target_strat)
                  if engine:
                      # We want the REAL bet from that strategy
                      # But wait, that strategy is running virtually.
                      # We might need to "shadow" it or just take its suggestion.
                      # If we take its suggestion, we are betting AS IF we are that strategy.
                      
                      # IMPORTANT: If we are "activating" it, does it mean we adopt its progression?
                      # Yes. So we effectively proxy its 'get_next_bet'.
                      amount = engine.get_next_bet()
                      labels = engine.get_bet_labels()
                      for lbl in labels:
                          bets.append({
                              "label": lbl,
                              "amount": amount
                          })
        
        return bets

    def _evaluate_condition(self, condition: Dict[str, Any]) -> bool:
        if not condition:
            return True 
            
        op = condition.get("operator")
        left_raw = condition.get("left")
        right_raw = condition.get("right")
        
        left = self._resolve_val(left_raw)
        right = self._resolve_val(right_raw)
        
        try:
            if op == "eq": return str(left) == str(right)
            if op == "neq": return str(left) != str(right)
            if op == "gt": return float(left) > float(right)
            if op == "lt": return float(left) < float(right)
            if op == "gte": return float(left) >= float(right)
            if op == "lte": return float(left) <= float(right)
        except Exception:
            return False
        
        return False

    def _resolve_val(self, val):
        if isinstance(val, str) and val.startswith("@"):
            var_name = val[1:]
            return self.variables.get(var_name)
        return val

    def _execute_action(self, action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        act_type = action.get("type")
        
        if act_type == "bet":
            target_raw = action.get("target")
            target = self._resolve_val(target_raw)
            if not target: return None
                
            amount = action.get("amount", self.base_bet)
            return {
                "type": "bet",
                "data": {
                    "label": str(target),
                    "amount": amount
                }
            }
            
        elif act_type == "activate_strategy":
            target_strat = action.get("target")
            return {
                "type": "activate_strategy",
                "data": target_strat
            }
            
        return None
