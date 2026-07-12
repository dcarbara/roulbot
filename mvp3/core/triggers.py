"""Conditional strategy selection for SpinEdge bundles.

When a bundle is configured with `selection_mode == "conditional"`, the
TriggerEngine evaluates each rotation candidate's trigger condition against
the recent spin history and picks a winner via the configured tiebreaker.
If nothing matches, the configured `fallback` decides what happens:

    - "stay"        keep the current strategy active (default)
    - "skip_round"  sit this round out (no bet placed)
    - "rotation" / "first_in_list"  fall back to the first list entry

Wire-in points:
    - core.backtesting.backtest_strategy: build a TriggerEngine when
      rotation_config has triggers; call pick(...) at the top of each round
      before get_next_bet(); swap engines if winner != current; handle skip.
    - gui.main_gui.run_auto_roulette: same idea, using request_strategy_swap.

The condition and tiebreaker registries are open — register new entries via
register_condition() / register_tiebreaker() without modifying this module.

Composability: a strategy's trigger can be a single condition
    {"type": "labels_cold", "lookback": 3, "max_hits": 0}
or a compound tree
    {"op": "or", "conditions": [
        {"type": "labels_cold", "lookback": 5, "max_hits": 1},
        {"op": "and", "conditions": [
            {"type": "color_streak", "color": "red", "n": 3},
            {"type": "labels_cold", "lookback": 3, "max_hits": 0}
        ]}
    ]}
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from core.number_history import NumberHistory

logger = logging.getLogger(__name__)

ConditionFn = Callable[[NumberHistory, Iterable[str], dict], tuple[bool, float]]
TiebreakerFn = Callable[[list["Candidate"]], "Candidate"]


# ─── Registries ───────────────────────────────────────────────────────────────

CONDITION_REGISTRY: dict[str, ConditionFn] = {}
TIEBREAKER_REGISTRY: dict[str, TiebreakerFn] = {}


def register_condition(name: str):
    def deco(fn: ConditionFn) -> ConditionFn:
        CONDITION_REGISTRY[name] = fn
        return fn
    return deco


def register_tiebreaker(name: str):
    def deco(fn: TiebreakerFn) -> TiebreakerFn:
        TIEBREAKER_REGISTRY[name] = fn
        return fn
    return deco


# ─── Built-in conditions ──────────────────────────────────────────────────────
# Each condition returns (armed, score). Score is condition-specific but
# monotone — higher = "more strongly triggered" — so the generic coldest /
# hottest tiebreakers can rank across heterogeneous conditions.

@register_condition("always")
def _cond_always(history, labels, params):
    return True, 0.0


@register_condition("never")
def _cond_never(history, labels, params):
    return False, 0.0


@register_condition("labels_cold")
def _cond_labels_cold(history, labels, params):
    """Labels appeared at most `max_hits` times in the last `lookback` spins.
    Score = consecutive misses (how long since last hit) — bigger = colder."""
    lookback = int(params.get("lookback", 3))
    max_hits = int(params.get("max_hits", 0))
    if lookback <= 0 or not labels:
        return False, 0.0
    hits = history.hits_in_last(labels, lookback)
    armed = hits <= max_hits
    score = float(history.consecutive_misses(labels))
    return armed, score


@register_condition("labels_hot")
def _cond_labels_hot(history, labels, params):
    """Labels appeared at least `min_hits` times in the last `lookback` spins."""
    lookback = int(params.get("lookback", 5))
    min_hits = int(params.get("min_hits", 3))
    if lookback <= 0 or not labels:
        return False, 0.0
    hits = history.hits_in_last(labels, lookback)
    return hits >= min_hits, float(hits)


@register_condition("consecutive_misses")
def _cond_consecutive_misses(history, labels, params):
    n = int(params.get("n", 4))
    if not labels:
        return False, 0.0
    run = history.consecutive_misses(labels)
    return run >= n, float(run)


@register_condition("consecutive_hits")
def _cond_consecutive_hits(history, labels, params):
    n = int(params.get("n", 3))
    if not labels:
        return False, 0.0
    run = history.consecutive_hits(labels)
    return run >= n, float(run)


def _streak_condition(streak_getter, key: str):
    """Outside-bet streak condition (color/parity/dozen). Param `key` names the
    user-facing direction param (e.g. 'color': 'red'). If the direction is
    omitted or 'any', a streak of any direction satisfies."""
    def cond(history, labels, params):
        n = int(params.get("n", 3))
        target = params.get(key)
        cat, run = streak_getter(history)
        if cat is None or run < n:
            return False, 0.0
        if target and target != "any" and cat != target:
            return False, 0.0
        return True, float(run)
    return cond


CONDITION_REGISTRY["color_streak"] = _streak_condition(lambda h: h.color_streak(), "color")
CONDITION_REGISTRY["parity_streak"] = _streak_condition(lambda h: h.parity_streak(), "parity")
CONDITION_REGISTRY["dozen_streak"] = _streak_condition(lambda h: h.dozen_streak(), "dozen")


# ─── Composable evaluation ────────────────────────────────────────────────────

def evaluate_condition(condition: Optional[dict], history: NumberHistory, labels: Iterable[str]) -> tuple[bool, float]:
    if not condition:
        return True, 0.0  # missing = always armed (for "always" defaulting)
    op = condition.get("op")
    if op:
        children = condition.get("conditions") or []
        if not children:
            return True, 0.0
        results = [evaluate_condition(c, history, labels) for c in children]
        if op == "and":
            return all(r[0] for r in results), min((r[1] for r in results), default=0.0)
        if op == "or":
            return any(r[0] for r in results), max((r[1] for r in results), default=0.0)
        logger.warning(f"[Triggers] unknown op '{op}' — treating as always-armed")
        return True, 0.0
    type_ = condition.get("type", "always")
    fn = CONDITION_REGISTRY.get(type_)
    if fn is None:
        logger.warning(f"[Triggers] unknown condition type '{type_}' — treating as never-armed")
        return False, 0.0
    try:
        return fn(history, labels, condition)
    except Exception as exc:
        logger.exception(f"[Triggers] condition '{type_}' raised: {exc}")
        return False, 0.0


# ─── Candidate + Tiebreakers ──────────────────────────────────────────────────

@dataclass
class Candidate:
    name: str
    rank: int   # 0-based position in rotation list
    score: float


@register_tiebreaker("coldest")
def _tb_coldest(cands):
    # Tie on score → prefer earlier rank so behavior is deterministic.
    return max(cands, key=lambda c: (c.score, -c.rank))


@register_tiebreaker("hottest")
def _tb_hottest(cands):
    return max(cands, key=lambda c: (c.score, -c.rank))


@register_tiebreaker("user_rank")
def _tb_user_rank(cands):
    return min(cands, key=lambda c: c.rank)


@register_tiebreaker("first_in_list")
def _tb_first_in_list(cands):
    return min(cands, key=lambda c: c.rank)


@register_tiebreaker("reverse_rank")
def _tb_reverse_rank(cands):
    return max(cands, key=lambda c: c.rank)


@register_tiebreaker("random")
def _tb_random(cands):
    return random.choice(cands)


# ─── Engine ───────────────────────────────────────────────────────────────────

@dataclass
class TriggerDecision:
    action: str               # "use" | "stay" | "skip"
    strategy: Optional[str]   # base strategy name when action == "use"
    reason: str               # human-readable for logs / HUD
    fired: list[str] = field(default_factory=list)   # all armed candidate names


class TriggerEngine:
    """Picks the active strategy each round based on conditions over spin
    history. Holds its own NumberHistory so callers can either feed spins
    via .update(num) or share an external history.

    A strategy entry WITHOUT an explicit trigger is treated as un-armed in
    conditional mode (opt-in). To make a strategy always-eligible, give it
    `{"type": "always"}` so it shows up as a candidate with score 0 — useful
    as a safety-net entry the tiebreaker can fall to.
    """

    def __init__(
        self,
        rotation_entries: list[str],
        triggers: dict[str, dict],
        tiebreaker: str = "coldest",
        fallback: str = "stay",
        history: Optional[NumberHistory] = None,
        global_trigger: Optional[dict] = None,
    ):
        self.rotation_entries = list(rotation_entries or [])
        # Triggers key on base names (without the ":progression|param=..." tail).
        self.base_names = [e.split(":", 1)[0].strip() for e in self.rotation_entries]
        self.triggers = dict(triggers or {})
        # Bundle-level "global" trigger: applies to every rotation entry that
        # doesn't have an explicit per-strategy override. Lets users configure
        # one common condition (e.g. "labels_cold lookback=5") for all 12
        # strategies in a bundle without filling 12 identical rows.
        self.global_trigger = global_trigger or None
        self.tiebreaker_name = (tiebreaker or "coldest").strip()
        self.fallback = (fallback or "stay").strip().lower()
        self.history = history or NumberHistory()
        # Set by build_trigger_engine_from_rotation_config to "conditional"
        # or "parallel". Callers branch on this to decide whether to use
        # pick() (one winner via tiebreaker) or pick_all() (every armed
        # candidate bets concurrently). Defaults to "conditional" for the
        # legacy single-pick behavior.
        self.selection_mode: str = "conditional"

    def update(self, winning_number: int) -> None:
        self.history.append(winning_number)

    def pick_all(self, labels_by_name: dict[str, list[str]]) -> list[Candidate]:
        """Return ALL currently-armed candidates in rotation order.

        This is the parallel-mode primitive: instead of picking one winner
        via tiebreaker, the caller bets on every armed strategy in the same
        round (their label sets get merged into a single chip placement).
        Returns an empty list when nothing's armed — the caller decides the
        fallback (skip the round, stay on default, etc.).
        """
        out: list[Candidate] = []
        for rank, base in enumerate(self.base_names):
            cond = self.triggers.get(base) or self.global_trigger
            if cond is None:
                continue
            labels = labels_by_name.get(base, [])
            armed, score = evaluate_condition(cond, self.history, labels)
            if armed:
                out.append(Candidate(name=base, rank=rank, score=score))
        return out

    def pick(
        self,
        labels_by_name: dict[str, list[str]],
        current_strategy: Optional[str] = None,
    ) -> TriggerDecision:
        candidates: list[Candidate] = []
        fired_names: list[str] = []
        for rank, base in enumerate(self.base_names):
            # Per-strategy entry wins; global_trigger fills in for strategies
            # without an override. If neither is set, the strategy is un-armed
            # (opt-in semantics for conditional mode).
            cond = self.triggers.get(base) or self.global_trigger
            if cond is None:
                continue
            labels = labels_by_name.get(base, [])
            armed, score = evaluate_condition(cond, self.history, labels)
            if armed:
                candidates.append(Candidate(name=base, rank=rank, score=score))
                fired_names.append(base)

        if candidates:
            tb = TIEBREAKER_REGISTRY.get(self.tiebreaker_name, _tb_first_in_list)
            try:
                winner = tb(candidates)
            except Exception as exc:
                logger.exception(f"[Triggers] tiebreaker '{self.tiebreaker_name}' raised: {exc}")
                winner = candidates[0]
            return TriggerDecision(
                action="use",
                strategy=winner.name,
                reason=(f"{winner.name} via {self.tiebreaker_name} "
                        f"(score={winner.score:.1f}, {len(candidates)} armed)"),
                fired=fired_names,
            )

        # Nothing armed — fallback.
        if self.fallback == "skip_round":
            return TriggerDecision(action="skip", strategy=None,
                                   reason="no candidate armed → skip round",
                                   fired=[])
        if self.fallback in ("first_in_list", "rotation"):
            first = self.base_names[0] if self.base_names else None
            return TriggerDecision(
                action="use" if first else "skip",
                strategy=first,
                reason=f"no candidate armed → fallback '{self.fallback}' → {first}",
                fired=[],
            )
        # "stay" (default)
        if current_strategy:
            return TriggerDecision(action="use", strategy=current_strategy,
                                   reason="no candidate armed → stay on current strategy",
                                   fired=[])
        first = self.base_names[0] if self.base_names else None
        return TriggerDecision(
            action="use" if first else "skip",
            strategy=first,
            reason="no candidate armed, no current → first in list",
            fired=[],
        )


# ─── Bundle-config helpers ────────────────────────────────────────────────────

def normalize_trigger_spec(spec) -> Optional[dict]:
    """Accept user-friendly shorthand and normalize to the canonical condition
    dict shape. Returns None if the spec is empty / disabled."""
    if not spec:
        return None
    if isinstance(spec, str):
        s = spec.strip()
        if not s or s.lower() in ("none", "off", "disabled"):
            return None
        return {"type": s}
    if isinstance(spec, dict):
        # Already canonical (single condition or compound). Trust it.
        return spec
    if isinstance(spec, list):
        # List of leaf conditions → default to AND for backward intuition.
        return {"op": "and", "conditions": [normalize_trigger_spec(c) for c in spec if c]}
    return None


def build_trigger_engine_from_rotation_config(rotation_config: Optional[dict],
                                              history: Optional[NumberHistory] = None) -> Optional[TriggerEngine]:
    """Construct a TriggerEngine from a rotation_config dict (the same dict
    backtest_strategy / live runtime consume). Returns None when conditional
    selection isn't requested or no triggers are defined.

    Expected rotation_config keys (additive on the existing schema):
        strategies        list[str]  raw rotation_list_str entries
        selection_mode    "rotation" (default) | "conditional"
        triggers          dict[str, dict]  base_name -> condition spec
        tiebreaker        str (registry key, default "coldest")
        fallback          str ("stay" / "skip_round" / "rotation" / "first_in_list")
    """
    if not rotation_config:
        return None
    mode = (rotation_config.get("selection_mode") or "rotation").strip().lower()
    # Engine activates for both "conditional" (tiebreaker picks one winner)
    # and "parallel" (every armed candidate bets). Mode is stashed on the
    # engine so callers can ask which behavior to use without parsing
    # rotation_config again.
    if mode not in ("conditional", "parallel"):
        return None
    raw_triggers = rotation_config.get("triggers") or {}
    triggers = {k: normalize_trigger_spec(v) for k, v in raw_triggers.items()}
    triggers = {k: v for k, v in triggers.items() if v is not None}
    global_trigger = normalize_trigger_spec(rotation_config.get("global_trigger"))
    # Engine is active if EITHER global_trigger OR any per-strategy trigger
    # is configured — bundles using only a global condition shouldn't have
    # to populate per-strategy rows just to wake the engine up.
    if not triggers and global_trigger is None:
        return None
    eng = TriggerEngine(
        rotation_entries=rotation_config.get("strategies") or [],
        triggers=triggers,
        tiebreaker=rotation_config.get("tiebreaker", "coldest"),
        fallback=rotation_config.get("fallback", "stay"),
        history=history,
        global_trigger=global_trigger,
    )
    eng.selection_mode = mode  # "conditional" | "parallel"
    return eng
