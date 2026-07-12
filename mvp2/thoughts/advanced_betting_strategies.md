# Advanced Auto-Betting Strategies

This document outlines advanced auto-betting strategies that leverage historical winning numbers to inform betting decisions. The goal is to move beyond static strategies and use real-time data and pattern recognition for smarter, adaptive betting.

### Core Concept: The Adaptive Strategy Engine

The fundamental shift is to create new strategy classes that are no longer static but are fed a stream of historical data on every betting decision. The main bot loop in `gui/main_gui.py` will be responsible for querying the `winning_numbers.db` and passing the recent history to the strategy engine before asking for the next bet.

---

### Strategy 1: Hot & Cold Number/Property Chasing

This is the most fundamental data-driven strategy. The bot analyzes recent history to determine which numbers, or properties of numbers (like color, dozen, column), are "hot" (appearing frequently) or "cold" (overdue).

*   **Concept:**
    *   **Hot Chasing:** Bet on numbers/properties that have appeared more often than statistically expected in the recent past. The theory is to ride a streak or capitalize on a potential (real or perceived) bias.
    *   **Cold Chasing:** Bet on numbers/properties that have not appeared for a long time. This is based on the "law of averages," believing that these outcomes are "due" to hit. (This is a classic example of the Gambler's Fallacy, but it's a very common strategy to implement).

*   **Logic:**
    1.  Define a "lookback" period (e.g., the last 100 winning numbers).
    2.  Fetch these numbers from `winning_numbers.db`.
    3.  **For Hot:** Calculate the frequency of each number (or color, dozen, etc.). Select the top N most frequent outcomes to bet on.
    4.  **For Cold:** Identify all numbers that have *not* appeared in the lookback period. Bet on a selection of these.
    5.  The strategy returns a list of bet labels (e.g., `['15', '23', 'red']`) to the `StrategyEngine`.

*   **Implementation Plan:**
    1.  **Create `AdaptiveStrategy` classes:** In a new file like `core/strategies/adaptive.py`, create classes like `HotColdStrategy`.
    2.  The `__init__` method would accept parameters like `mode='hot'`, `property='number' | 'dozen' | 'color'`, `lookback=100`, `bet_count=5`.
    3.  The `get_bet_labels(self, history: list)` method would contain the logic to analyze the `history` and return the list of labels to bet on.
    4.  **Modify `StrategyEngine`:** It needs to be able to accept and use these new adaptive strategies. When `get_bet_labels()` is called, it must first fetch the history from the DB and pass it to the strategy object.
    5.  **Enhance GUI:** Add a new section in the "Strategy Builder" or "Bot Configuration" tab to select and configure these adaptive strategies (e.g., dropdowns for Hot/Cold, property type, and entry boxes for lookback period).

---

### Strategy 2: Streak Following & Anti-Streak (Pattern Recognition)

This strategy focuses on the sequence of outcomes, not just their frequency.

*   **Concept:**
    *   **Streak Following:** Identify a consecutive streak of a certain outcome (e.g., 4 reds in a row) and bet on that same outcome (bet on red again).
    *   **Anti-Streak (Fading):** Identify a streak and bet on the opposite outcome, predicting the streak will break (e.g., after 4 reds, bet on black).
    *   **Choppiness Detection:** Identify a pattern of frequent changes (e.g., Red, Black, Red, Black, Red). The strategy would then bet on Black, following the "choppy" pattern.

*   **Logic:**
    1.  Define a `trigger_length` for a streak (e.g., 3).
    2.  Analyze the last `N` numbers *in sequence*.
    3.  If the last `trigger_length` outcomes were all 'red', the strategy returns `['red']` (for following) or `['black']` (for fading).
    4.  **Crucially:** If no pattern is detected, the strategy can return an empty list `[]`, telling the bot to **sit out the round** and not place a bet.

*   **Implementation Plan:**
    1.  **Create a `PatternStrategy` class:** This class would be initialized with `mode='follow' | 'fade'`, `property='color'`, `trigger_length=3`.
    2.  Its `get_bet_labels(self, history: list)` method would analyze the sequence.
    3.  **Modify `StrategyEngine` and `run_bot` loop:** The system must now handle the case where the strategy returns no bets. The `run_bot` loop in `gui/main_gui.py` should simply skip the betting actions for that round if the list of labels is empty. This is a major enhancement that allows the bot to be patient.

---

### Strategy 3: Zone & Neighbor Betting (Wheel-Based)

This strategy treats the roulette wheel as a physical object and bets on sections of the wheel.

*   **Concept:** Based on the last winning number, bet on a "zone" of numbers that are physically adjacent to it on the wheel. This is popular in real casinos where players look for dealer signatures or wheel biases.
    *   **Neighbors:** Bet on the last winning number plus N numbers to its left and right on the wheel.
    *   **Classic Zones:** Implement bets on predefined zones like "Voisins du Zéro" (Neighbors of Zero), "Tiers du Cylindre" (Third of the Wheel), and "Orphelins" (Orphans).

*   **Logic:**
    1.  Define the European roulette wheel layout as a constant list: `WHEEL_LAYOUT = [0, 32, 15, 19, ...]`.
    2.  Get the last winning number.
    3.  Find the index of that number in `WHEEL_LAYOUT`.
    4.  The bet labels will be the numbers at `index-N` through `index+N` in the list (wrapping around for numbers near the start/end).
    5.  For classic zones, these lists of numbers are predefined.

*   **Implementation Plan:**
    1.  **Create a `ZoneStrategy` class.**
    2.  Add `WHEEL_LAYOUT` and the predefined zones as constants in `core/strategy_engine.py` or a new constants file.
    3.  The `get_bet_labels(self, history: list)` method will only need the most recent number from the history.
    4.  **Enhance GUI:** Add a "Zone Strategy" type with options to select the zone (Voisins, Tiers, etc.) or specify a number of neighbors to bet on.

---

### Implementation Roadmap Summary

To make these strategies a reality in your `mvp2` codebase, here is a high-level roadmap:

1.  **Evolve the Database Utilities (`core/utils/db_utils.py`):**
    *   Create new functions like `get_historical_stats(lookback: int)` that return not just the numbers, but pre-calculated frequencies of properties (colors, dozens, etc.) to avoid recalculating them constantly.

2.  **Create an Adaptive Strategy Base Class:**
    *   Create a new base class, e.g., `AdaptiveStrategyBase`, that inherits from `StrategyBase`.
    *   Its `get_bet_labels` method will be defined as `get_bet_labels(self, history: list) -> list[str]`, making the data requirement explicit.

3.  **Implement the New Strategy Classes:**
    *   Create the `HotColdStrategy`, `PatternStrategy`, and `ZoneStrategy` classes as described above, inheriting from `AdaptiveStrategyBase`.

4.  **Upgrade the `StrategyEngine`:**
    *   In the `__init__` method, detect if the chosen strategy is an "adaptive" one.
    *   In `get_bet_labels`, if the strategy is adaptive, it must first call a `db_utils` function to get the history and then pass that data to the strategy object.

5.  **Upgrade the Main Bot Loop (`gui/main_gui.py`):**
    *   The `run_bot` function is the orchestrator. Before calling the `strategy.get_next_bet()`, it should get the bet labels from the engine.
    *   If the returned list of labels is empty, it should `continue` its loop to the next table state check, effectively "sitting out" the round. This is a critical new piece of logic.

6.  **Build the GUI Controls:**
    *   In `gui/main_gui.py`, add new widgets to the configuration tabs to allow users to select and configure these advanced strategies. For example, a new "Adaptive Strategy" section that appears when the main strategy dropdown has "hot/cold" selected.
