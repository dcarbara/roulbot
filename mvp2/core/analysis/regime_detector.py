import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class RegimeDetector:
    """
    Analyzes historical roulette data to determine the current 'Regime' or 'Market State'.
    
    States:
    - TRENDING: High probability of repeating outcomes (Streaks).
    - CHOPPY: High probability of alternating outcomes (Ping-Pong).
    - NEUTRAL: No strong statistical edge detected (Random Walk).
    """

    def __init__(self):
        pass

    def detect_all_regimes(self, history: List[int]) -> dict:
        """
        Analyzes regimes for all major groupings.
        Returns dict: {'Colors': 'TRENDING', 'Dozens': 'CHOPPY', ...}
        """
        if not history or len(history) < 10:
            return {k: "NEUTRAL" for k in ["Colors", "Dozens", "Columns", "EvenOdd", "HighLow"]}
            
        return {
            "Colors": self._analyze_sequence([self._get_color(n) for n in history[-10:]]),
            "Dozens": self._analyze_sequence([self._get_dozen(n) for n in history[-10:]]),
            "Columns": self._analyze_sequence([self._get_column(n) for n in history[-10:]]),
            "EvenOdd": self._analyze_sequence([self._get_even_odd(n) for n in history[-10:]]),
            "HighLow": self._analyze_sequence([self._get_high_low(n) for n in history[-10:]]),
        }

    def detect_state(self, history: List[int]) -> str:
        """Legacy wrapper for Colors only (to maintain compatibility)"""
        return self.detect_all_regimes(history).get("Colors", "NEUTRAL")

    def _analyze_sequence(self, sequence: List[str]) -> str:
        """Generic analyzer for any sequence of labels"""
        if not sequence: return "NEUTRAL"
        
        # Streak Analysis
        max_streak = 1
        current_streak = 1
        for i in range(1, len(sequence)):
            if sequence[i] == sequence[i-1]:
                current_streak += 1
            else:
                max_streak = max(max_streak, current_streak)
                current_streak = 1
        max_streak = max(max_streak, current_streak)
        
        # Thresholds vary by probability? 
        # For Dozens (1/3), a streak of 4 is huge. For Colors (1/2), streak of 5.
        # Let's keep it simple: Streak >= 4 is TRENDING.
        if max_streak >= 4:
            return "TRENDING"
            
        # Chop Analysis (Flips)
        flips = 0
        for i in range(1, len(sequence)):
            if sequence[i] != sequence[i-1]:
                flips += 1
        
        # High flips = Choppy
        # For 10 items, 9 transitions. >6 flips is choppy.
        if flips >= 7:
            return "CHOPPY"
            
        return "NEUTRAL"

    # --- Helpers ---
    def _get_color(self, n):
        if n in {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}: return "RED"
        if n == 0: return "ZERO"
        return "BLACK"

    def _get_dozen(self, n):
        if 1 <= n <= 12: return "1st12"
        if 13 <= n <= 24: return "2nd12"
        if 25 <= n <= 36: return "3rd12"
        return "ZERO"

    def _get_column(self, n):
        if n == 0: return "ZERO"
        if n % 3 == 1: return "COL1"
        if n % 3 == 2: return "COL2"
        return "COL3"

    def _get_even_odd(self, n):
        if n == 0: return "ZERO"
        return "EVEN" if n % 2 == 0 else "ODD"

    def _get_high_low(self, n):
        if n == 0: return "ZERO"
        return "LOW" if n <= 18 else "HIGH"

    def _get_max_streak_color(self, colors): return 0 # Deprecated
    def _count_flips(self, colors): return 0 # Deprecated
