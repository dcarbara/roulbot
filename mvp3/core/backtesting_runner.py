"""Shared backtest campaign runner.

Single source of truth for executing a backtest campaign. Used by:

  - gui/backtesting_gui.py            (Backtesting tab)
  - backtest_cli.py                   (top-level command-line script)

Both call `run_campaign(config, on_log=...)` with an identical config dict
and receive an identical CampaignResult. This is what makes "GUI matches
CLI" achievable — there is no second implementation to drift away from.

Config schema is documented in `default_config()`. Use `validate_config()`
to coerce types and fill defaults before calling `run_campaign()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from core.backtesting import RouletteBacktester, BacktestResult


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class CampaignResult:
    """End-of-campaign summary. Aggregates per-session BacktestResult objects."""
    sessions: list[BacktestResult] = field(default_factory=list)
    initial_balance: float = 0.0
    final_balance: float = 0.0
    campaign_pnl: float = 0.0
    total_rounds: int = 0
    sessions_run: int = 0
    stop_reason: str = "completed"          # completed | global_profit | global_loss | error
    error: str = ""
    escalation_log: list[str] = field(default_factory=list)
    # Final escalation state at end of campaign
    final_escalation_step: int = 0
    final_base_bet: float = 0.0
    final_max_loss: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        # BacktestResult objects need explicit conversion; asdict handles
        # dataclasses but not arbitrary attribute objects. We serialise the
        # fields we care about for cross-process parity checks.
        d["sessions"] = [
            {
                "initial_balance":   s.initial_balance,
                "final_balance":     s.final_balance,
                "total_rounds":      s.total_rounds,
                "total_wins":        s.total_wins,
                "total_losses":      s.total_losses,
                "max_drawdown":      s.max_drawdown,
                "max_consec_wins":   getattr(s, "max_consecutive_wins", 0),
                "max_consec_losses": getattr(s, "max_consecutive_losses", 0),
            }
            for s in self.sessions
        ]
        return d


# ── Config helpers ────────────────────────────────────────────────────────────

def default_config() -> dict:
    """Canonical config schema. Every key here is consumed by run_campaign().
    Users can omit keys; validate_config() fills defaults."""
    return {
        # Strategy
        "strategy_name":            "martingale",
        "base_bet":                 1.0,
        "progression_type":         "flat",
        "dynamic_rules":            [],
        "max_consec_losses":        0,
        "custom_sequence":          None,
        "dalembert_step":           1,

        # Session guardrails
        "initial_balance":          100.0,
        "max_loss":                 50.0,
        "max_bet":                  0.0,           # 0 = no cap
        "profit_target":            0.0,
        "enable_profit_target":     False,
        "trailing_stop_amount":     0.0,
        "enable_trailing_stop":     False,
        "max_session_wins_streak":  0,
        "max_session_losses_streak":0,
        "session_ext_after_win":    False,
        "session_ext_at_high":      False,
        "max_extension_rounds":     20,
        "extension_give_up_amount": 50.0,

        # Simulation
        "sim_mode":                 "sequential",   # | "independent"
        "rounds":                   100,
        "sims":                     10,
        "seed":                     None,           # int → deterministic generated data

        # Data source
        "historical_data_source":   "db",           # | "generated"
        "db_limit":                 1000,
        "db_offset":                0,              # skip the most-recent N rows
        "historical_data":          None,           # optional inline override

        # Global / Campaign
        "enable_global_limits":     False,
        "global_profit_target":     0.0,
        "global_stop_loss":         0.0,

        # Rotation
        "rotation_config":          None,           # {"strategies": [...], "mode": "..."}

        # Custom strategies registry
        "custom_strategies":        {},

        # Escalation-on-loss (mirrors live Bot Control settings)
        "enable_escalation_on_loss":False,
        "escalation_multiplier":    2.0,
        "escalation_max_steps":     4,
        "escalation_per_step":      "",             # CSV, takes precedence over multiplier
    }


def validate_config(user_cfg: dict) -> dict:
    """Merge user_cfg over defaults and coerce types. Raises ValueError on
    obviously bad input (negative balance, etc.)."""
    cfg = default_config()
    cfg.update(user_cfg or {})

    def _f(k, default):
        try:
            cfg[k] = float(cfg.get(k, default) or 0.0)
        except (TypeError, ValueError):
            cfg[k] = float(default)
    def _i(k, default):
        try:
            cfg[k] = int(cfg.get(k, default) or 0)
        except (TypeError, ValueError):
            cfg[k] = int(default)
    def _b(k, default):
        cfg[k] = bool(cfg.get(k, default))
    def _s(k, default):
        cfg[k] = str(cfg.get(k, default) or default)

    # Numerics
    for k, d in [("base_bet", 1.0), ("initial_balance", 100.0), ("max_loss", 50.0),
                 ("max_bet", 0.0),
                 ("profit_target", 0.0), ("trailing_stop_amount", 0.0),
                 ("extension_give_up_amount", 50.0), ("global_profit_target", 0.0),
                 ("global_stop_loss", 0.0), ("escalation_multiplier", 2.0)]:
        _f(k, d)
    for k, d in [("max_consec_losses", 0), ("max_session_wins_streak", 0),
                 ("max_session_losses_streak", 0), ("max_extension_rounds", 20),
                 ("rounds", 100), ("sims", 10), ("db_limit", 1000),
                 ("db_offset", 0),
                 ("escalation_max_steps", 4), ("dalembert_step", 1)]:
        _i(k, d)
    for k, d in [("enable_profit_target", False), ("enable_trailing_stop", False),
                 ("session_ext_after_win", False), ("session_ext_at_high", False),
                 ("enable_global_limits", False), ("enable_escalation_on_loss", False)]:
        _b(k, d)
    for k, d in [("strategy_name", "martingale"), ("progression_type", "flat"),
                 ("sim_mode", "sequential"), ("historical_data_source", "db"),
                 ("escalation_per_step", "")]:
        _s(k, d)

    if cfg["initial_balance"] <= 0:
        raise ValueError("initial_balance must be > 0")
    if cfg["base_bet"] <= 0:
        raise ValueError("base_bet must be > 0")
    if cfg["sims"] <= 0:
        raise ValueError("sims must be >= 1")
    if cfg["rounds"] <= 0:
        raise ValueError("rounds must be >= 1")

    return cfg


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_per_step(csv: str) -> list[float]:
    out: list[float] = []
    for tok in str(csv or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = float(tok)
            if v > 0:
                out.append(v)
        except ValueError:
            continue
    return out


def _build_session_config(cfg: dict, effective_max_loss: float) -> dict:
    """The dict the SessionManager consumes. Build it once per session so the
    escalated stop-loss is reflected each iteration.

    `session_duration_minutes` + `spins_per_minute` are required for the
    backtest's simulated-time TIME_LIMIT check: without them, SessionManager
    defaults to a 60-minute window that never elapses in fast backtest mode
    → soft_stop never triggers → session_ext_at_high never activates →
    sessions always end at num_rounds even when extension should have fired.
    """
    return {
        "max_loss":                  effective_max_loss,
        "profit_target":             cfg["profit_target"],
        "trailing_stop_amount":      cfg["trailing_stop_amount"],
        "enable_profit_target":      cfg["enable_profit_target"] and cfg["profit_target"] > 0,
        "enable_trailing_stop":      cfg["enable_trailing_stop"] and cfg["trailing_stop_amount"] > 0,
        "max_session_wins_streak":   cfg["max_session_wins_streak"],
        "max_session_losses_streak": cfg["max_session_losses_streak"],
        "session_ext_after_win":     cfg["session_ext_after_win"],
        "session_ext_at_high":       cfg["session_ext_at_high"],
        "max_extension_rounds":      cfg["max_extension_rounds"],
        "extension_give_up_amount":  cfg["extension_give_up_amount"],
        # Needed for simulated-time TIME_LIMIT in backtest. Falls back to
        # the bundle's session_duration (minutes) when set; otherwise the
        # SessionManager's own 60-min default applies.
        "session_duration_minutes":  cfg.get("session_duration_minutes", 0)
                                     or cfg.get("session_duration", 0) or 0,
        "spins_per_minute":          cfg.get("spins_per_minute", 30) or 30,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def filter_session_extension_conflicts(rules: list, session_ext_at_high: bool) -> list:
    """Reconcile bundle-level win-reset rules with the 'End only at session high'
    policy.

    When `session_ext_at_high` is true, the session extends past its time/round
    limit until profit RECOVERS to the session high — the whole point is to
    keep escalating the bet to claw back a drawdown. An unconditional
    `on:win, action:reset_to_base` (or `action:flat`) fired on every small
    win during recovery cancels exactly that intent: the bet snaps back to
    base and the strategy can never re-escalate enough to recover.

    Rather than dropping such rules (which would leave the bet escalated
    forever, even after the session high IS recovered), we TRANSFORM them by
    attaching `condition=profit_at_or_above_session_high`. Net effect:
      - Below the session high: the rule doesn't match → bet stays escalated ✓
      - At/above the session high: the rule fires → bet resets to base ✓

    Bundles like `dsSlutWEscBundle` that carry an unconditional reset alongside
    `session_ext_at_high=true` now get the right behavior automatically — no
    bundle edits, no manual rule conditions required. Rules that already
    carry an explicit condition pass through untouched.
    """
    if not session_ext_at_high or not rules:
        return rules
    out = []
    transformed = []
    for r in rules:
        is_unconditional_win_reset = (
            r.get('on') == 'win'
            and r.get('action') in ('reset_to_base', 'flat')
            and not r.get('condition')
        )
        if is_unconditional_win_reset:
            new_r = dict(r)
            new_r['condition'] = 'profit_at_or_above_session_high'
            transformed.append((r, new_r))
            out.append(new_r)
        else:
            out.append(r)
    if transformed:
        # Surface this so users see WHY their unconditional rule started
        # behaving conditionally — the policy implies the constraint.
        import logging as _logging
        _logging.getLogger(__name__).info(
            f"[Rule filter] session_ext_at_high=True → attached "
            f"`condition=profit_at_or_above_session_high` to {len(transformed)} "
            f"unconditional win-reset rule(s) so the bet stays escalated "
            f"during recovery and only resets when session high is reached. "
            f"Pre/post: {[(o, n) for o, n in transformed]}")
    return out


def run_campaign(user_cfg: dict,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_progress: Optional[Callable[[float], None]] = None) -> CampaignResult:
    """Run a backtest campaign. Single shared implementation; GUI + CLI both call this.

    Args:
        user_cfg: config dict (any subset of default_config() keys)
        on_log:   optional progress message callback (must be thread-safe in the caller)
        on_progress: optional 0..1 progress callback

    Returns:
        CampaignResult with per-session breakdown + aggregates.
    """
    cfg = validate_config(user_cfg)
    log = on_log or (lambda _msg: None)

    # Reconcile bundle rules with `session_ext_at_high`: an unconditional
    # `win:reset_to_base` would snap the bet back to base on every small win
    # during a drawdown, canceling the recovery intent of the extension
    # policy. The filter attaches `condition=profit_at_or_above_session_high`
    # to such rules so they fire only at session-high recovery.
    if cfg.get("session_ext_at_high"):
        _original = cfg.get("dynamic_rules") or []
        _filtered = filter_session_extension_conflicts(_original, True)
        if _filtered != _original:
            _n_changed = sum(
                1 for o, f in zip(_original, _filtered)
                if o != f
            )
            log(f"ℹ session_ext_at_high=True → attached "
                f"`condition=profit_at_or_above_session_high` to "
                f"{_n_changed} unconditional win-reset rule(s) so bet stays "
                f"escalated during recovery, resets only at session high.")
            cfg["dynamic_rules"] = _filtered

    bt = RouletteBacktester()

    # Resolve historical data
    historical = cfg.get("historical_data")
    if historical is None and cfg["historical_data_source"] == "db":
        # Each session inside a sequential campaign re-uses the same fetched
        # slice (run_campaign passes `historical` as an override every call).
        # That means: total spins consumed across the campaign is at most
        # rounds × sims. If db_limit is smaller than that, every session
        # silently clamps to db_limit. Auto-bump to whichever is larger so
        # the user gets the full `rounds` they asked for. They can still
        # cap explicitly via db_limit in independent mode if desired.
        offset = max(0, int(cfg.get("db_offset", 0) or 0))
        configured_limit = int(cfg["db_limit"])
        # Sequential mode now ADVANCES through historical data session by
        # session (session N uses historical[N*rounds : (N+1)*rounds]) so
        # 20 sessions = 20 distinct slices of history, not 20 replays of
        # the same window. We need `rounds × sims` rows for that. The old
        # behaviour was reusing the same slice every session, which made
        # multi-session campaigns on DB data produce identical-looking
        # results. Independent Monte Carlo still uses the same slice for
        # each sim (with possibly different RNG seeds elsewhere).
        if cfg["sim_mode"] == "sequential":
            per_session_need = cfg["rounds"]
            campaign_need = cfg["rounds"] * cfg["sims"]
        else:
            per_session_need = cfg["rounds"]
            campaign_need = cfg["rounds"]
        limit = max(configured_limit, campaign_need)
        if limit > configured_limit:
            log(f"⚠ Bumping db_limit {configured_limit} → {limit} so each session gets the {per_session_need} rounds you asked for.")
        # db_anchor_id: when set, every fetch is bounded by `WHERE id <=
        # anchor` so the slice is reproducible across runs even while the
        # GUI's spin-watcher keeps appending new rows in the background.
        # Without an anchor, two runs seconds apart can replay different
        # windows because "latest K" shifts every time a spin is appended.
        anchor_id = cfg.get("db_anchor_id")
        try:
            anchor_id = int(anchor_id) if anchor_id is not None else None
        except (TypeError, ValueError):
            anchor_id = None
        anchor_note = f" (anchored at id ≤ {anchor_id})" if anchor_id else ""
        if offset > 0:
            raw = bt.fetch_historical_data_from_db(limit=limit + offset,
                                                   max_id=anchor_id)
            historical = raw[:-offset] if len(raw) > offset else []
            historical = historical[-limit:] if len(historical) > limit else historical
            log(f"Loaded {len(historical)} spins from DB "
                f"(limit {limit}, skipped most-recent {offset}){anchor_note}.")
        else:
            historical = bt.fetch_historical_data_from_db(limit=limit,
                                                          max_id=anchor_id)
            log(f"Loaded {len(historical)} spins from DB (limit {limit}){anchor_note}.")

        # ── Pre-run sanity readout ──
        # Tell the user upfront how many sessions and rounds will ACTUALLY
        # run given (a) available data, (b) per-session round budget.
        # Without this users configure sims=1000, rounds=10000 against a
        # 43k-row DB, wonder why total rounds = 700, and have to dig
        # through Detailed Log to find "Ran out of historical data".
        if len(historical) < per_session_need:
            log(f"⚠ Only {len(historical)} spins available — sessions will be capped to this length "
                f"(requested {per_session_need} per session).")
        if cfg["sim_mode"] == "sequential":
            max_sessions_by_data = max(1, len(historical) // per_session_need)
            if max_sessions_by_data < cfg["sims"]:
                log(f"📊 Data budget: {len(historical)} spins ÷ {per_session_need} rounds/session "
                    f"= at most {max_sessions_by_data} session(s) runnable before data runs out "
                    f"(you configured {cfg['sims']}).")
            else:
                log(f"📊 Data budget OK: {cfg['sims']} sessions × {per_session_need} rounds "
                    f"= {cfg['sims'] * per_session_need} rows needed, {len(historical)} available.")
        if cfg["max_consec_losses"] and cfg["max_consec_losses"] <= 5:
            log(f"⚠ max_consec_losses={cfg['max_consec_losses']} is very tight — sessions will "
                f"terminate fast once that streak hits, so 'rounds per sim' will rarely be reached.")

    # ── Escalation snapshot ──
    per_step = _parse_per_step(cfg["escalation_per_step"])
    max_steps = len(per_step) if per_step else cfg["escalation_max_steps"]
    initial_base_bet = cfg["base_bet"]
    initial_max_loss = cfg["max_loss"]
    effective_base_bet = initial_base_bet
    effective_max_loss = initial_max_loss
    escalation_step = 0
    peak_global_pnl = 0.0

    if cfg["enable_escalation_on_loss"]:
        if per_step:
            log(f"🔼 Escalation per-step [{','.join(str(v) for v in per_step)}]")
        else:
            log(f"🔼 Escalation ×{cfg['escalation_multiplier']:g} (cap {max_steps} steps)")

    result = CampaignResult(initial_balance=cfg["initial_balance"])
    current_balance = cfg["initial_balance"]
    global_pnl = 0.0

    sims = cfg["sims"]
    mode = cfg["sim_mode"]

    log(f"🚀 Starting campaign: mode={mode}, {sims} sessions, init=${current_balance:.2f}")
    if mode == "independent" and historical and sims > 1:
        log(f"⚠ Independent Monte Carlo + DB data → all {sims} sims share the same data slice. "
            f"They'll produce identical results. Use sequential mode (advances through DB) or generated data (RNG-seeded) for variation.")
    # Seed is only effective with generated data — DB-sourced runs are already
    # deterministic from the stored rows. Tell the user when their seed has
    # no effect so they don't expect reproducibility-via-seed it can't deliver.
    if cfg["seed"] is not None and historical:
        log(f"⚠ Seed={cfg['seed']} is ignored when data source is DB — DB rows are already deterministic. "
            f"Seed only varies generated data.")

    # Sequential mode walks through the historical slice with a moving
    # cursor instead of pre-allocating N rows per session. Each session
    # consumes only as many spins as it actually plays (rounds + however
    # many extension rounds session_ext_at_high fires). Without this, a
    # bundle with `rounds=2 + max_extension_rounds=20000` would still get
    # only 2 spins per session — extension couldn't extend anywhere.
    historical_cursor = 0
    # Cap per-session data at rounds + extension allowance so a runaway
    # extension can't consume the whole campaign's data in one session.
    per_session_cap = cfg["rounds"] + int(cfg.get("max_extension_rounds", 0) or 0)

    try:
        for i in range(sims):
            seed = (cfg["seed"] + i) if (cfg["seed"] is not None and historical is None) else (i if historical is None else None)

            # ── Per-session historical slice ──
            # Sequential + DB: walk forward with a cursor that advances by
            # ACTUAL rounds consumed each session (not a fixed pre-allocation).
            # This lets session_ext_at_high actually extend — the session has
            # `rounds + max_extension_rounds` spins available to play with.
            # Independent + DB still reuses the same slice each sim (it's a
            # Monte Carlo over a fixed data window).
            if historical and mode == "sequential":
                remaining = historical[historical_cursor:]
                iter_historical = remaining[:per_session_cap]
                if not iter_historical:
                    log(f"⚠ Ran out of historical data at session {i+1} "
                        f"(cursor at {historical_cursor}/{len(historical)}). Stopping campaign.")
                    break
                if len(iter_historical) < cfg["rounds"]:
                    log(f"⚠ Session {i+1}: only {len(iter_historical)} rows left "
                        f"(asked for {cfg['rounds']}). Running shorter session.")
            else:
                iter_historical = historical

            # ── Build per-session args ──
            iter_bet = effective_base_bet if mode == "sequential" else initial_base_bet
            iter_max_loss = effective_max_loss if mode == "sequential" else initial_max_loss
            iter_balance = current_balance if mode == "sequential" else cfg["initial_balance"]
            iter_session_cfg = _build_session_config(cfg, iter_max_loss)

            session_res = bt.backtest_strategy(
                strategy_name=cfg["strategy_name"],
                base_bet=iter_bet,
                initial_balance=iter_balance,
                num_rounds=cfg["rounds"],
                progression_type=cfg["progression_type"],
                max_loss=iter_max_loss,
                max_bet=(cfg.get("max_bet") or None) or None,
                max_consec_losses=cfg["max_consec_losses"] or None,
                custom_strategies=cfg["custom_strategies"],
                historical_data_override=iter_historical,
                seed=seed,
                rotation_config=cfg["rotation_config"],
                session_config=iter_session_cfg,
                dynamic_rules=cfg["dynamic_rules"] or None,
                custom_sequence=cfg["custom_sequence"],
                dalembert_step=cfg["dalembert_step"],
            )

            # Audit: stamp the escalation state + effective values that this
            # session actually ran with. backtest_strategy initializes these
            # to (base_bet, max_loss, 0); we overwrite with the escalated
            # values + step from THIS iteration so the GUI / CSV / Audit tab
            # can show "Session N ran with base $0.20 / SL $50 / esc step 1".
            try:
                session_res.effective_base_bet = float(iter_bet)
                session_res.effective_max_loss = float(iter_max_loss)
                session_res.escalation_step = int(escalation_step)
            except Exception:
                pass

            result.sessions.append(session_res)
            session_pnl = session_res.final_balance - session_res.initial_balance

            if mode == "sequential":
                global_pnl += session_pnl
                current_balance = session_res.final_balance
                result.total_rounds += session_res.total_rounds
                # Advance the historical cursor by ACTUAL rounds consumed so
                # the next session resumes from where this one left off.
                # Without this, a session that played 50 rounds (extension)
                # would be followed by one starting at index +rounds (= 2 or
                # whatever), repeating spins already used.
                if historical:
                    historical_cursor += session_res.total_rounds

                if global_pnl > peak_global_pnl:
                    peak_global_pnl = global_pnl

                esc_tag = (f"  [esc step {escalation_step}, base ${effective_base_bet:.2f}, SL ${effective_max_loss:.2f}]"
                           if cfg["enable_escalation_on_loss"] else "")
                log(f"Session {i+1}: PnL=${session_pnl:+.2f}, Bal=${current_balance:.2f}, "
                    f"Global=${global_pnl:+.2f}{esc_tag}")

                # ── Global checks ──
                global_hit_profit = cfg["enable_global_limits"] and cfg["global_profit_target"] > 0 \
                                    and global_pnl >= cfg["global_profit_target"]
                global_hit_loss = cfg["enable_global_limits"] and cfg["global_stop_loss"] > 0 \
                                  and global_pnl <= -cfg["global_stop_loss"]

                # ── Escalation update ──
                # Audit-friendly logging: every transition mentions the session
                # that triggered it (which the user can then drill into via
                # Round Audit). Trigger logic uses both the BacktestResult's
                # stop_reason (when present) AND the PnL threshold so an
                # INSUFFICIENT_BALANCE session still escalates.
                if cfg["enable_escalation_on_loss"]:
                    recovered_to_peak = peak_global_pnl > 0 and round(global_pnl, 2) >= round(peak_global_pnl, 2)
                    if global_hit_profit or recovered_to_peak:
                        if escalation_step != 0 or effective_base_bet != initial_base_bet:
                            label = "global target" if global_hit_profit else "recovered to peak"
                            prev_step, prev_base, prev_sl = escalation_step, effective_base_bet, effective_max_loss
                            escalation_step = 0
                            effective_base_bet = initial_base_bet
                            effective_max_loss = initial_max_loss
                            msg = (f"🔁 Escalation reset after Session {i+1} ({label}): "
                                   f"step {prev_step}→0 · base ${prev_base:.2f}→${effective_base_bet:.2f} · "
                                   f"SL ${prev_sl:.2f}→${effective_max_loss:.2f}")
                            log(msg); result.escalation_log.append(msg)
                    else:
                        stop_reason_str = (getattr(session_res, "stop_reason", "") or "").upper()
                        session_hit_sl = (
                            session_pnl <= -(effective_max_loss - 0.01)
                            or stop_reason_str in ("STOP_LOSS", "INSUFFICIENT_BALANCE")
                        )
                        if session_hit_sl and escalation_step < max_steps:
                            prev_step, prev_base, prev_sl = escalation_step, effective_base_bet, effective_max_loss
                            escalation_step += 1
                            if per_step:
                                scale = per_step[min(escalation_step - 1, len(per_step) - 1)]
                            else:
                                scale = cfg["escalation_multiplier"] ** escalation_step
                            effective_base_bet = round(initial_base_bet * scale, 2)
                            effective_max_loss = round(initial_max_loss * scale, 2)
                            msg = (f"🔼 Escalation step {prev_step}→{escalation_step} "
                                   f"after Session {i+1} (×{scale:g}): "
                                   f"base ${prev_base:.2f}→${effective_base_bet:.2f} · "
                                   f"SL ${prev_sl:.2f}→${effective_max_loss:.2f} "
                                   f"(trigger: {stop_reason_str or 'PnL≤max_loss'})")
                            log(msg); result.escalation_log.append(msg)

                if global_hit_profit:
                    log(f"🏆 Global target reached (+${global_pnl:.2f}). Stopping campaign.")
                    result.stop_reason = "global_profit"
                    break
                if global_hit_loss:
                    log(f"🛑 Global stop-loss hit (-${abs(global_pnl):.2f}). Stopping campaign.")
                    result.stop_reason = "global_loss"
                    break
            else:
                # Independent Monte Carlo — sessions are isolated
                result.total_rounds += session_res.total_rounds
                log(f"Sim {i+1}: PnL=${session_pnl:+.2f}, rounds={session_res.total_rounds}, "
                    f"W={session_res.total_wins}, L={session_res.total_losses}, "
                    f"MaxDD=${session_res.max_drawdown:.2f}")

            if on_progress:
                try:
                    on_progress((i + 1) / sims)
                except Exception:
                    pass

        result.sessions_run = len(result.sessions)
        if mode == "sequential":
            result.final_balance = current_balance
            result.campaign_pnl = global_pnl
        else:
            # In independent mode, "final balance" isn't well-defined. Report
            # the mean ending balance instead, and campaign_pnl = mean PnL.
            if result.sessions:
                ends = [s.final_balance for s in result.sessions]
                result.final_balance = sum(ends) / len(ends)
                result.campaign_pnl = result.final_balance - cfg["initial_balance"]
            else:
                result.final_balance = cfg["initial_balance"]

        result.final_escalation_step = escalation_step
        result.final_base_bet = effective_base_bet
        result.final_max_loss = effective_max_loss

    except Exception as exc:
        result.stop_reason = "error"
        result.error = str(exc)
        log(f"❌ Campaign error: {exc}")

    return result


# ── Convenience JSON I/O for the CLI ──────────────────────────────────────────

def load_config_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config_json(cfg: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ── Bundle → Campaign config ─────────────────────────────────────────────────
# A "bundle" is the same JSON shape the live dashboard loads (see
# RouletteBotGUI._build_bundle_data / on_dashboard_bundle_select). This block
# converts that shape into the flat run_campaign config so backtests apply
# the SAME risk/strategy/rotation/dynamic-rules settings as the live bot.
#
# Mapping table (bundle JSON → campaign cfg key):
#   strategy_config.strategy_name           → strategy_name
#   strategy_config.progression_type        → progression_type
#   strategy_config.rotation_list_str       → rotation_config.strategies (split on ',')
#   strategy_config.rotation_mode           → rotation_config.mode
#   betting_config.base_bet                 → base_bet
#   betting_config.max_loss                 → max_loss  (supports "X%")
#   betting_config.max_bet                  → max_bet
#   betting_config.profit_target            → profit_target  (supports "X%")
#   betting_config.enable_trailing_stop     → enable_trailing_stop
#   betting_config.trailing_stop_amount     → trailing_stop_amount  (supports "X%")
#   betting_config.session_ext_after_win    → session_ext_after_win
#   betting_config.session_ext_at_high      → session_ext_at_high
#   betting_config.max_extension_rounds     → max_extension_rounds
#   betting_config.extension_give_up_amount → extension_give_up_amount  (supports "X%")
#   betting_config.enable_global_stop       → enable_global_limits
#   betting_config.global_profit_stop       → global_profit_target  (supports "X%")
#   betting_config.global_stop_loss         → global_stop_loss  (supports "X%")
#   betting_config.max_consec_losses        → max_consec_losses
#   betting_config.max_session_wins_streak  → max_session_wins_streak
#   betting_config.max_session_losses_streak→ max_session_losses_streak
#   dynamic_rules                           → dynamic_rules
#
# Fields the live bot uses but backtest ignores (with reason):
#   session_duration / min_gap_minutes / max_gap_minutes — wall-clock pacing,
#       irrelevant for a virtual sim. Use the explicit `rounds` arg instead.
#   filter_by_regime / rotation_trigger / switch_after_n_losses
#       — live runtime triggers; rotation in backtest only honors `mode`.
#   k_value, observation_trigger — strategy-internal params, surface via
#       custom_strategies registry if your strategy needs them.

def _parse_pct_or_float(value, base, default=0.0):
    """Bundle numerics can be plain numbers (44.24) or percent strings ('5%').
    Percent strings are resolved against `base` (typically initial_balance,
    matching live's parse_hybrid_value)."""
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return float(default)
    if s.endswith("%"):
        try:
            return float(base) * (float(s[:-1]) / 100.0)
        except ValueError:
            return float(default)
    try:
        return float(s)
    except ValueError:
        return float(default)


def bundle_to_campaign_config(bundle: dict,
                              *,
                              initial_balance: float = 100.0,
                              rounds: Optional[int] = None,
                              sims: Optional[int] = None,
                              sim_mode: str = "sequential",
                              custom_strategies: Optional[dict] = None,
                              historical_data_source: str = "db",
                              db_limit: int = 1000,
                              spins_per_minute: float = 30.0,
                              min_rounds_per_session: int = 100,
                              extra_overrides: Optional[dict] = None) -> dict:
    """Convert a bundle JSON dict → flat run_campaign config.

    Bundle fields drive everything that comes from the bundle. The kwargs are
    the user-facing inputs that the bundle doesn't (sensibly) define for a
    backtest: rounds/sims/initial_balance/sim_mode/data source.

    `extra_overrides` is applied last, so a caller can force-override any
    specific field after the bundle mapping completes.
    """
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a dict (parsed JSON)")

    sc = bundle.get("strategy_config", {}) or {}
    bc = bundle.get("betting_config", {}) or {}
    bundle_dynamic_rules = bundle.get("dynamic_rules", []) or []

    base_bet = float(bc.get("base_bet", 1.0) or 1.0)

    # Percent-aware limits — match live's parse_hybrid_value semantics.
    max_loss            = _parse_pct_or_float(bc.get("max_loss"), initial_balance, default=50.0)
    profit_target       = _parse_pct_or_float(bc.get("profit_target"), initial_balance, default=0.0)
    trailing_stop_amt   = _parse_pct_or_float(bc.get("trailing_stop_amount"), initial_balance, default=0.0)
    extension_give_up   = _parse_pct_or_float(bc.get("extension_give_up_amount"), initial_balance, default=50.0)
    global_profit       = _parse_pct_or_float(bc.get("global_profit_stop"), initial_balance, default=0.0)
    global_loss         = _parse_pct_or_float(bc.get("global_stop_loss"), initial_balance, default=0.0)

    # ── Derive rounds/sims from bundle when caller didn't override ──
    # Bundles store session_duration in MINUTES (a wall-clock concept for the
    # live bot — "let each session run for X minutes then start over"). For
    # backtest we need rounds-per-session.
    #
    # The conversion uses spins_per_minute (default 30 — i.e. a spin every
    # 2 seconds, matching backtest's "give the strategy enough rope" mindset
    # rather than real-time pacing). A bundle that ships with session_duration=1
    # would otherwise produce 1.5 rounds at the old default, way too short
    # for stop_loss to ever fire and giving misleading-looking backtests.
    #
    # Floor at min_rounds_per_session (default 100). Below ~50 rounds, most
    # strategies' stop_loss never gets a chance to fire — the session just
    # exhausts its rounds at a small trickle loss. The floor guarantees
    # backtest sessions are long enough to exercise the bundle's risk logic.
    # Caller can pass rounds=N to bypass the floor entirely.
    bundle_session_minutes = float(bc.get("session_duration", 0) or 0)
    bundle_num_sessions = int(bc.get("num_sessions", 0) or 0)
    if rounds is None:
        if bundle_session_minutes > 0:
            derived = max(1, int(round(bundle_session_minutes * spins_per_minute)))
            rounds = max(derived, int(min_rounds_per_session))
        else:
            rounds = max(100, int(min_rounds_per_session))
    if sims is None:
        # Bundles tend to set num_sessions huge (10000) for live looping. For
        # backtest that's overkill — cap at a sane default unless the caller
        # explicitly wants more.
        sims = min(max(1, bundle_num_sessions), 50) if bundle_num_sessions else 10

    # Rotation list. The encoded format (`name:prog|param=val,...`) is parsed
    # inside StrategyEngine, so we pass each entry through verbatim.
    # rotation_trigger / switch_after_n_losses / carry_progression_on_switch
    # are now honored by backtest_strategy's on-loss rotation block so bundles
    # with trigger='on_loss' rotate mid-session like the live bot.
    rot_str = (sc.get("rotation_list_str") or "").strip()
    rotation_config = None
    rotation_strategies: list[str] = []
    if rot_str:
        rotation_strategies = [s.strip() for s in rot_str.split(",") if s.strip()]
        if rotation_strategies:
            rotation_config = {
                "strategies":                  rotation_strategies,
                "mode":                        sc.get("rotation_mode", "sequential") or "sequential",
                "trigger":                     sc.get("rotation_trigger", "session_end") or "session_end",
                "switch_after_n_losses":       int(sc.get("switch_after_n_losses", 1) or 1),
                "carry_progression_on_switch": bool(sc.get("carry_progression_on_switch", False)),
                "reset_rotation_on_session":   bool(sc.get("reset_rotation_on_session", True)),
                # Conditional-trigger selection (see core/triggers.py). Bundles
                # opt in by setting selection_mode="conditional" and providing
                # per-strategy `triggers` and/or a single bundle-level
                # `global_trigger` that applies to every rotation entry without
                # an explicit override. Plain rotation behavior is unchanged
                # when these fields are absent.
                "selection_mode":              (sc.get("selection_mode") or "rotation").strip(),
                "triggers":                    dict(sc.get("triggers") or {}),
                "global_trigger":              sc.get("global_trigger") or None,
                "tiebreaker":                  (sc.get("tiebreaker") or "coldest").strip(),
                "fallback":                    (sc.get("fallback") or "stay").strip(),
            }

    # Active strategy name: prefer the explicit strategy_config field, but if
    # it's missing (or holds a comma-joined display label like "ds1, ds2, ds3")
    # fall back to the first rotation entry's base name so backtest_strategy
    # gets a single-name handle that StrategyEngine can resolve.
    raw_name = (sc.get("strategy_name") or "").strip()
    if "," in raw_name or not raw_name:
        if rotation_strategies:
            raw_name = rotation_strategies[0].split(":", 1)[0].strip()
        else:
            raw_name = "martingale"
    strategy_name = raw_name.split(":", 1)[0].strip()

    cfg = {
        # Strategy
        "strategy_name":             strategy_name,
        "base_bet":                  base_bet,
        "progression_type":          sc.get("progression_type", "flat") or "flat",
        "dynamic_rules":             list(bundle_dynamic_rules),
        # max_consec_losses is PER-STRATEGY ONLY — set in the Bundle Builder per
        # row as the entry suffix `|max_consec_losses=N`, which each StrategyEngine
        # parses for itself. The legacy bundle-level betting_config.max_consec_losses
        # is a stale global cap that silently stopped sessions after N losses even
        # when the user set none (old bundles baked in 5). Matching the live bot,
        # the backtest now ignores it (0 = disabled at the session level); genuine
        # per-strategy caps still fire via each engine's own gate.
        "max_consec_losses":         0,
        "custom_sequence":           None,
        "dalembert_step":            1,

        # Session guardrails
        "initial_balance":           float(initial_balance),
        "max_loss":                  max_loss,
        "max_bet":                   float(bc.get("max_bet", 0) or 0),
        "profit_target":             profit_target,
        "enable_profit_target":      bool(profit_target > 0),
        "trailing_stop_amount":      trailing_stop_amt,
        "enable_trailing_stop":      bool(bc.get("enable_trailing_stop", False)),
        "max_session_wins_streak":   int(bc.get("max_session_wins_streak", 0) or 0),
        "max_session_losses_streak": int(bc.get("max_session_losses_streak", 0) or 0),
        "session_ext_after_win":     bool(bc.get("session_ext_after_win", False)),
        "session_ext_at_high":       bool(bc.get("session_ext_at_high", False)),
        "max_extension_rounds":      int(bc.get("max_extension_rounds", 20) or 20),
        "extension_give_up_amount":  extension_give_up,
        # Bubbled through so SessionManager's simulated-time TIME_LIMIT check
        # uses the bundle's intended session length (not the 60-min default).
        "session_duration_minutes":  bundle_session_minutes,
        "spins_per_minute":          float(spins_per_minute),

        # Simulation
        "sim_mode":                  sim_mode,
        "rounds":                    int(rounds),
        "sims":                      int(sims),
        "seed":                      None,

        # Data source
        "historical_data_source":    historical_data_source,
        "db_limit":                  int(db_limit),
        "db_offset":                 0,
        "historical_data":           None,

        # Global / Campaign
        "enable_global_limits":      bool(bc.get("enable_global_stop", False)),
        "global_profit_target":      global_profit,
        "global_stop_loss":          global_loss,

        # Rotation
        "rotation_config":           rotation_config,

        # Custom strategy registry (passed through to StrategyEngine)
        "custom_strategies":         dict(custom_strategies or {}),

        # Escalation — bundles DO carry these in betting_config (added in
        # bundle version 1.2). Bot Control's "escalation on loss" multiplies
        # base_bet + max_loss after each session-level stop-loss event until
        # the campaign recovers to a new global peak.
        "enable_escalation_on_loss": bool(bc.get("enable_escalation_on_loss", False)),
        "escalation_multiplier":     float(bc.get("escalation_multiplier", 2.0) or 2.0),
        "escalation_max_steps":      int(bc.get("escalation_max_steps", 4) or 4),
        "escalation_per_step":       str(bc.get("escalation_per_step", "") or ""),
    }

    if extra_overrides:
        cfg.update(extra_overrides)

    return cfg


def backtest_bundle(bundle_or_path,
                    *,
                    initial_balance: float = 100.0,
                    rounds: int = 100,
                    sims: int = 10,
                    sim_mode: str = "sequential",
                    custom_strategies: Optional[dict] = None,
                    historical_data_source: str = "db",
                    db_limit: int = 1000,
                    on_log: Optional[Callable[[str], None]] = None,
                    on_progress: Optional[Callable[[float], None]] = None,
                    extra_overrides: Optional[dict] = None) -> CampaignResult:
    """Run a backtest from a bundle JSON file (path) or in-memory bundle dict.

    Wraps bundle_to_campaign_config → run_campaign so callers don't have to
    learn the campaign schema. Returns a CampaignResult identical to the one
    run_campaign produces for a hand-written config.
    """
    if isinstance(bundle_or_path, str):
        with open(bundle_or_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
    elif isinstance(bundle_or_path, dict):
        bundle = bundle_or_path
    else:
        raise ValueError("bundle_or_path must be a file path or dict")

    cfg = bundle_to_campaign_config(
        bundle,
        initial_balance=initial_balance,
        rounds=rounds,
        sims=sims,
        sim_mode=sim_mode,
        custom_strategies=custom_strategies,
        historical_data_source=historical_data_source,
        db_limit=db_limit,
        extra_overrides=extra_overrides,
    )
    if on_log:
        try:
            meta = bundle.get("meta", {}) or {}
            name = meta.get("name") or bundle.get("name") or "<unnamed>"
            on_log(f"📦 Backtesting bundle '{name}' "
                   f"({cfg['strategy_name']}, progression={cfg['progression_type']}, "
                   f"rotation={len(cfg.get('rotation_config', {}).get('strategies', []) if cfg.get('rotation_config') else [])} entries)")
            on_log(f"   rounds={cfg['rounds']}, sims={cfg['sims']}, init_balance=${cfg['initial_balance']:.2f}, "
                   f"max_loss=${cfg['max_loss']:.2f}, max_bet=${cfg['max_bet']:.2f}")
            if cfg.get("enable_escalation_on_loss"):
                on_log(f"   escalation: ×{cfg['escalation_multiplier']:g} (cap {cfg['escalation_max_steps']} steps)")
            # Warn about referenced-but-missing custom strategies. This is the
            # most common cause of "every round bets the same" — the names get
            # silently mapped to a fallback FlatStrategy with one default label.
            registry = set((cfg.get("custom_strategies") or {}).keys()) | {"martingale", "flat"}
            referenced = {cfg["strategy_name"]}
            for entry in (cfg.get("rotation_config") or {}).get("strategies", []):
                referenced.add(entry.split(":", 1)[0].strip())
            missing = sorted(n for n in referenced if n and n not in registry)
            if missing:
                on_log(f"   ⚠ Bundle references custom strategies NOT in this registry: {missing}")
                on_log(f"     These will fall back to a default placeholder — bets may look uniform "
                       f"across phases. Run from the GUI (where the app's custom_strategies are "
                       f"available) for accurate per-phase behavior.")
        except Exception:
            pass
    return run_campaign(cfg, on_log=on_log, on_progress=on_progress)
