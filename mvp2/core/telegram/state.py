"""
Bot state machine — tracks menu navigation, input modes, and transient state.
All mutable bot state lives here so bot.py stays clean.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List

# ── Menu identifiers ──────────────────────────────────────────────────────────

MENU_DASHBOARD    = "DASHBOARD"
MENU_SETTINGS     = "SETTINGS"
MENU_GUARDRAILS   = "GUARDRAILS"
MENU_SESSION      = "SESSION"
MENU_STRATEGY     = "STRATEGY"
MENU_STATS        = "STATS"
# ── Mission-control additions ──────────────────────────────────────────────────
MENU_CONTROL      = "CONTROL"        # the hub
MENU_BETTING      = "BETTING"        # base/max bet, observation, progression
MENU_RISK         = "RISK"           # risk-profile picker
MENU_ROTATION     = "ROTATION"       # strategy rotation
MENU_ESCALATION   = "ESCALATION"     # escalation-on-loss
MENU_BUNDLES      = "BUNDLES"        # browse ALL bundles
MENU_PROGRESSION  = "PROGRESSION"    # progression picker
MENU_QUICK        = "QUICK"          # quick actions

# ── Input mode identifiers ────────────────────────────────────────────────────

INPUT_NONE         = "NONE"
INPUT_BASE_BET     = "BASE_BET"
INPUT_SESS_PROFIT  = "SESS_PROFIT"
INPUT_SESS_LOSS    = "SESS_LOSS"
INPUT_TRAILING     = "TRAILING"
INPUT_GLOB_PROFIT  = "GLOB_PROFIT"
INPUT_GLOB_LOSS    = "GLOB_LOSS"
INPUT_DURATION     = "DURATION"
INPUT_NUM_SESSIONS = "NUM_SESSIONS"
# ── Mission-control numeric inputs ─────────────────────────────────────────────
INPUT_MAX_BET      = "MAX_BET"
INPUT_OBSERVATION  = "OBSERVATION"
INPUT_WIN_STREAK   = "WIN_STREAK"
INPUT_LOSS_STREAK  = "LOSS_STREAK"
INPUT_ESC_MULT     = "ESC_MULT"
INPUT_ESC_STEPS    = "ESC_STEPS"
INPUT_MAX_EXT      = "MAX_EXT"
INPUT_EXT_GIVEUP   = "EXT_GIVEUP"
INPUT_BALANCE      = "BALANCE"
INPUT_MIN_GAP      = "MIN_GAP"
INPUT_MAX_GAP      = "MAX_GAP"
# Free-text (non-numeric) input — handled specially in bot._handle_message.
INPUT_ROTATION_LIST = "ROTATION_LIST"

# Map input mode → (config_key, var_name, cast_type, display_unit)
INPUT_CONFIG_MAP = {
    INPUT_BASE_BET:     ("base_bet",                 "base_bet_var",                 float, "$"),
    INPUT_SESS_PROFIT:  ("profit_target",             "profit_target_var",             float, "$"),
    INPUT_SESS_LOSS:    ("max_loss",                  "max_loss_var",                  float, "$"),
    INPUT_TRAILING:     ("trailing_stop_amount",      "trailing_stop_amount_var",      float, "$"),
    INPUT_DURATION:     ("session_duration_minutes",  "session_duration_var",          int,   "min"),
    INPUT_NUM_SESSIONS: ("num_sessions",              "num_sessions_var",              int,   ""),
    INPUT_MAX_BET:      ("max_bet",                   "max_bet_var",                   float, "$"),
    INPUT_OBSERVATION:  ("observation_trigger",       "observation_trigger_var",       int,   "rounds"),
    INPUT_WIN_STREAK:   ("max_session_wins_streak",   "max_session_wins_streak_var",   int,   "wins"),
    INPUT_LOSS_STREAK:  ("max_session_losses_streak", "max_session_losses_streak_var", int,   "losses"),
    INPUT_ESC_MULT:     ("escalation_multiplier",     "escalation_multiplier_var",     float, "×"),
    INPUT_ESC_STEPS:    ("escalation_max_steps",      "escalation_max_steps_var",      int,   "steps"),
    INPUT_MAX_EXT:      ("max_extension_rounds",      "max_ext_rounds_var",            int,   "rounds"),
    INPUT_EXT_GIVEUP:   ("extension_give_up_amount",  "ext_give_up_var",               float, "$"),
    INPUT_BALANCE:      ("current_balance",           "balance_var",                   float, "$"),
    INPUT_MIN_GAP:      ("min_gap_minutes",           "min_gap_var",                   int,   "min"),
    INPUT_MAX_GAP:      ("max_gap_minutes",           "max_gap_var",                   int,   "min"),
}


@dataclass
class BotState:
    # Navigation
    menu: str = MENU_DASHBOARD
    prev_menu: str = MENU_DASHBOARD

    # Dashboard message (pinned)
    dashboard_msg_id: Optional[int] = None
    # Live PnL graph message (pinned, auto-refreshed by state watcher)
    graph_msg_id: Optional[int] = None

    # Input collection
    input_mode: str = INPUT_NONE
    input_back_data: str = "menu_dashboard"   # callback_data to return to after input

    # Confirmation/acknowledgment (blocking flow from GUI thread)
    expecting_confirmation: bool = False
    confirmation_value: Optional[bool] = None
    confirmation_event: threading.Event = field(default_factory=threading.Event)

    # Input from Telegram (blocking flow from GUI thread)
    expecting_input: bool = False
    input_value: Optional[str] = None
    input_event: threading.Event = field(default_factory=threading.Event)

    # Live round history (last 20 results)
    recent_results: deque = field(default_factory=lambda: deque(maxlen=20))

    # Cached strategy list (for indexed callback data)
    strategy_list_cache: List[str] = field(default_factory=list)

    # /swap — favorites snapshot frozen at menu render time so a click
    # always resolves to what the user saw, even if favorites change.
    swap_strat_snapshot: List[str] = field(default_factory=list)
    swap_bundle_snapshot: List[str] = field(default_factory=list)

    # Bundle browser (ALL bundles, paginated). Snapshot + page index so an
    # indexed callback always resolves to the name the user saw.
    bundle_browse_cache: List[str] = field(default_factory=list)
    bundle_browse_page: int = 0
