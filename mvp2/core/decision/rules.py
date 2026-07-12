"""Rule grammar: Condition + Action + Rule, plus parsers and evaluator.

JSON shape (composite mode):
    {
      "when": [
        {"detect": "<signal>", "group": "<group>", "match": "<state>", ...signal_params},
        ...
      ],
      "then": {"action": "<follow|contra|target|labels|delegate>", ...action_params}
    }

The 'match' key on a condition specifies which signal state(s) cause the
condition to fire. If omitted, the condition matches on `reading.matched`
(state != 'inactive' / 'neutral'). Examples:
    "match": "TRENDING"            — exact match
    "match": ["TRENDING", "CHOPPY"] — any of these
    "match": "active"              — exact match
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from core.signals.base import GROUPS, Signal, SignalReading, group_members, group_member_of
from core.signals.registry import make_signal


# ---------- Conditions ----------

@dataclass
class Condition:
    """A condition: a signal config + a match predicate on the reading's state."""
    signal: Signal
    match: Union[str, List[str], None]  # None = use reading.matched property

    def evaluate(self, history: List[int]) -> Tuple[bool, SignalReading]:
        reading = self.signal.update(history)
        if self.match is None:
            return reading.matched, reading
        if isinstance(self.match, str):
            return reading.state == self.match, reading
        if isinstance(self.match, (list, tuple, set)):
            return reading.state in self.match, reading
        return False, reading


def parse_condition(spec: Dict[str, Any]) -> Condition:
    """Build a Condition from a JSON-shaped dict. Pulls the signal spec (everything
    except the 'match' key) through make_signal()."""
    if not isinstance(spec, dict):
        raise ValueError(f"condition must be a dict, got {type(spec).__name__}")
    match = spec.get("match")
    # Pass everything except 'match' to the signal factory
    signal_spec = {k: v for k, v in spec.items() if k != "match"}
    signal = make_signal(signal_spec)
    return Condition(signal=signal, match=match)


# ---------- Actions ----------

class Action:
    """Marker base class for actions."""
    type: str = "abstract"


@dataclass
class BetLabelsAction(Action):
    """Bet a fixed list of labels."""
    labels: List[str]
    type: str = field(default="labels", init=False)


@dataclass
class BetGroupAction(Action):
    """Bet derived from the primary signal reading's member.

    mode = 'follow':  bet [reading.member]
    mode = 'contra':  bet all other members of `group`
    mode = 'target':  bet [target] (group's role is just to validate target)
    """
    group: str
    mode: str  # 'follow' | 'contra' | 'target'
    target: Optional[str] = None
    type: str = field(default="group", init=False)


@dataclass
class DelegateAction(Action):
    """Hand off label generation (and bet amount calc) to a named sub-strategy."""
    strategy_name: str
    type: str = field(default="delegate", init=False)


# ── History-aware actions (resolved from the number history, not a signal) ──

@dataclass
class FollowLastAction(Action):
    """Bet the group member that the LAST number landed in.

    e.g. group='dozen' → bet the dozen the last spin was in. skip_zero=True
    uses the most recent NON-zero number (0 has no dozen/column/etc.)."""
    group: str
    skip_zero: bool = True
    type: str = field(default="follow_last", init=False)


@dataclass
class ColdestAction(Action):
    """Bet the N coldest (or hottest) members of a group over a window.

    mode='cold' → fewest appearances first (ties: longest absence).
    mode='hot'  → most appearances first.
    count       → how many members to bet.
    lookback    → window of recent spins to rank over.
    exclude_last→ drop the member the last number landed in (so a cold pick
                  never collides with a follow_last on the same group).
    """
    group: str
    count: int = 1
    lookback: int = 18
    exclude_last: bool = False
    mode: str = "cold"  # 'cold' | 'hot'
    type: str = field(default="coldest", init=False)


@dataclass
class ComboAction(Action):
    """Union the labels produced by several sub-actions in ONE rule.

    This is what lets a single rule bet across MULTIPLE groups / sources in the
    same spin (e.g. follow_last dozen + coldest dozen + follow_last column +
    coldest column). Labels are de-duplicated, first-seen order preserved.

    min_labels: sit out (bet nothing) unless the combo yields at least this many
    distinct labels. 0 = no minimum. Use it to enforce "all components present"
    — e.g. min_labels=4 makes the dozen+column hot/cold combo skip the warmup
    spin where there's no last number yet to follow."""
    actions: List[Action]
    min_labels: int = 0
    type: str = field(default="combo", init=False)


_VALID_GROUP_MODES = {"follow", "contra", "target"}


def parse_action(spec: Dict[str, Any]) -> Action:
    """Build an Action from a JSON-shaped dict."""
    if not isinstance(spec, dict):
        raise ValueError(f"action must be a dict, got {type(spec).__name__}")
    a = spec.get("action")
    if a == "labels":
        labels = spec.get("labels", [])
        if not isinstance(labels, list) or not labels:
            raise ValueError("action='labels' requires non-empty 'labels' list")
        return BetLabelsAction(labels=list(labels))
    if a in _VALID_GROUP_MODES:
        group = spec.get("group")
        if group not in GROUPS:
            raise ValueError(
                f"action={a!r} requires 'group' in {list(GROUPS)}, got {group!r}"
            )
        target = spec.get("target")
        if a == "target":
            if target not in group_members(group):
                raise ValueError(
                    f"action='target' for group={group!r} requires 'target' in "
                    f"{group_members(group)}, got {target!r}"
                )
        return BetGroupAction(group=group, mode=a, target=target)
    if a == "delegate":
        name = spec.get("strategy") or spec.get("strategy_name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("action='delegate' requires 'strategy' (sub-strategy name)")
        return DelegateAction(strategy_name=name.strip())
    if a == "follow_last":
        group = spec.get("group")
        if group not in GROUPS:
            raise ValueError(
                f"action='follow_last' requires 'group' in {list(GROUPS)}, got {group!r}"
            )
        return FollowLastAction(group=group, skip_zero=bool(spec.get("skip_zero", True)))
    if a in ("coldest", "hottest"):
        group = spec.get("group")
        if group not in GROUPS:
            raise ValueError(
                f"action={a!r} requires 'group' in {list(GROUPS)}, got {group!r}"
            )
        return ColdestAction(
            group=group,
            count=max(1, int(spec.get("count", 1))),
            lookback=max(1, int(spec.get("lookback", 18))),
            exclude_last=bool(spec.get("exclude_last", False)),
            mode=("cold" if a == "coldest" else "hot"),
        )
    if a == "combo":
        subs = spec.get("actions")
        if not isinstance(subs, list) or not subs:
            raise ValueError("action='combo' requires a non-empty 'actions' list")
        return ComboAction(actions=[parse_action(s) for s in subs],
                           min_labels=max(0, int(spec.get("min_labels", 0))))
    raise ValueError(
        f"unknown action {a!r}, expected one of "
        f"{sorted(_VALID_GROUP_MODES | {'labels', 'delegate', 'follow_last', 'coldest', 'hottest', 'combo'})}"
    )


# ---------- Rules ----------

@dataclass
class Rule:
    when: List[Condition]
    then: Action
    # Rule persistence: once a rule fires, lock its labels (and the rule itself)
    # until a release condition is met. Supported:
    #   None / omitted        : no persistence (default; re-evaluate each spin)
    #   "win"                 : release after the next winning spin
    #   "loss"                : release after the next losing spin
    #   "session_high"        : hold until profit recovers to the prior session
    #                           high-water mark (needs current_profit on
    #                           record_result) — keeps a triggered pattern locked
    #                           through wins AND losses until fully recovered
    #   {"max_losses": N}     : release after N consecutive losses
    #   {"max_spins":  N}     : release after N spins regardless of result
    persist_until: Any = None

    def evaluate(self, history: List[int]) -> Tuple[bool, List[SignalReading]]:
        """Evaluate all conditions against history. Returns (all_matched, readings).
        Conditions are ANDed; readings are returned in input order regardless of match."""
        readings = []
        all_matched = True
        for cond in self.when:
            matched, reading = cond.evaluate(history)
            readings.append(reading)
            if not matched:
                all_matched = False
        return all_matched, readings


def parse_rule(spec: Dict[str, Any]) -> Rule:
    """Build a Rule from a JSON-shaped dict.

    Supports two input shapes:

    Composite shape (preferred):
        {"when": [{...condition...}, ...], "then": {...action...}}

    Flat shape (pattern_follower legacy, single condition + flat action keys):
        {"detect": "...", "group": "...", "min_length": ..., "action": "follow"}
        — the action is whatever 'action' key holds, with 'group'/'target' inherited
        from the condition's group.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"rule must be a dict, got {type(spec).__name__}")

    # Validate persist_until value if present (shared between both rule shapes below)
    persist = spec.get("persist_until")
    if persist is not None:
        if isinstance(persist, str):
            if persist not in ("win", "loss", "session_high"):
                raise ValueError(
                    f"persist_until string must be 'win', 'loss', or 'session_high', got {persist!r}"
                )
        elif isinstance(persist, dict):
            if not ("max_losses" in persist or "max_spins" in persist):
                raise ValueError(
                    f"persist_until dict must contain 'max_losses' or 'max_spins', "
                    f"got {persist!r}"
                )
        else:
            raise ValueError(
                f"persist_until must be a string ('win'/'loss') or a dict "
                f"({{max_losses: N}} or {{max_spins: N}}), got {type(persist).__name__}"
            )

    # Detect shape. Composite shape is identified by 'then'. `when` is OPTIONAL:
    # a rule with no (or empty) `when` ALWAYS fires — useful for always-on
    # strategies (e.g. always bet follow_last + coldest). Put an unconditional
    # rule LAST in the list since first-match-wins would otherwise shadow the
    # rules below it.
    if "then" in spec:
        when_specs = spec.get("when", [])
        if when_specs and not isinstance(when_specs, list):
            raise ValueError("rule.when must be a list of conditions (or omitted)")
        conditions = [parse_condition(c) for c in (when_specs or [])]
        action = parse_action(spec["then"])
        return Rule(when=conditions, then=action, persist_until=persist)

    # Flat / legacy shape
    if "detect" in spec and "action" in spec:
        # Build the condition (everything except action/target keys)
        cond_spec = {k: v for k, v in spec.items()
                     if k not in ("action", "target", "strategy", "strategy_name", "labels")}
        # If no explicit 'match' was provided, derive a sensible default per detector:
        if "match" not in cond_spec:
            cond_spec["match"] = _default_match_for(spec)
        condition = parse_condition(cond_spec)
        # Build the action from the flat keys
        action_spec = {"action": spec["action"]}
        for k in ("group", "target", "strategy", "labels",
                  "count", "lookback", "exclude_last", "skip_zero", "actions"):
            if k in spec:
                action_spec[k] = spec[k]
        action = parse_action(action_spec)
        return Rule(when=[condition], then=action, persist_until=persist)

    raise ValueError(
        "rule must use either composite shape ({'when': [...], 'then': {...}}) "
        "or flat shape ({'detect': ..., 'action': ...})"
    )


def _default_match_for(spec: Dict[str, Any]) -> Union[str, List[str], None]:
    """Pick a sensible default 'match' for a flat-shape rule based on its detect type."""
    detect = spec.get("detect")
    if detect == "streak":
        return "active"
    if detect == "dominance":
        return "trending"
    if detect == "alternation":
        return "choppy"
    if detect == "regime":
        # If user provided 'regime': "TRENDING" (or list), match on that exactly.
        if "regime" in spec:
            return spec["regime"]
        # Otherwise match any non-NEUTRAL state
        return ["TRENDING", "CHOPPY"]
    if detect == "last_number_in":
        return "active"
    return None  # falls back to reading.matched property


# ---------- Evaluation ----------

def evaluate_rules(rules: List[Rule], history: List[int]
                   ) -> Tuple[Optional[Rule], Optional[List[SignalReading]]]:
    """First-match-wins evaluation. Returns (rule, readings) of the first matching
    rule, or (None, None) if no rule matches."""
    for rule in rules:
        matched, readings = rule.evaluate(history)
        if matched:
            return rule, readings
    return None, None


def _last_member(group: str, history: List[int], skip_zero: bool = True) -> Optional[str]:
    """Group member of the last number (or most recent non-zero if skip_zero)."""
    if not history:
        return None
    if skip_zero:
        n = next((x for x in reversed(history) if 1 <= x <= 36), None)
    else:
        n = history[-1]
    if n is None:
        return None
    return group_member_of(group, n)


def _rank_members(group: str, history: List[int], lookback: int, mode: str) -> List[str]:
    """Rank a group's members coldest- or hottest-first over the last `lookback`
    spins. Cold = fewest hits (ties → longest absence). Hot = most hits."""
    members = group_members(group)
    window = history[-lookback:] if lookback else list(history)
    counts = {m: 0 for m in members}
    last_seen = {m: None for m in members}  # index within window (later = recent)
    for idx, n in enumerate(window):
        mem = group_member_of(group, n)
        if mem in counts:
            counts[mem] += 1
            last_seen[mem] = idx

    def gap(m: str) -> int:
        ls = last_seen[m]
        return (len(window) + 1) if ls is None else (len(window) - 1 - ls)

    if mode == "hot":
        key = lambda m: (-counts[m], gap(m), members.index(m))
    else:  # cold
        key = lambda m: (counts[m], -gap(m), members.index(m))
    return sorted(members, key=key)


def labels_for_action(action: Action, primary_reading: Optional[SignalReading],
                      history: Optional[List[int]] = None) -> List[str]:
    """Resolve an Action to a list of bet labels.

    `history` (recent winning numbers, oldest→newest) is required by the
    history-aware actions (follow_last / coldest / hottest / combo); signal-
    based actions (follow/contra/target/labels) ignore it.

    Delegate actions return [] — caller routes to the sub-strategy instead.
    """
    history = history or []
    if isinstance(action, BetLabelsAction):
        return list(action.labels)
    if isinstance(action, BetGroupAction):
        if action.mode == "target":
            return [action.target]
        if primary_reading is None:
            return []
        member = primary_reading.member
        if member is None:
            return []
        if action.mode == "follow":
            # Sanity: member should belong to the action's group, but signals don't
            # enforce cross-group references. We trust the rule writer.
            return [member]
        if action.mode == "contra":
            others = [m for m in group_members(action.group) if m != member]
            return others
    if isinstance(action, FollowLastAction):
        lab = _last_member(action.group, history, action.skip_zero)
        return [lab] if lab else []
    if isinstance(action, ColdestAction):
        ranked = _rank_members(action.group, history, action.lookback, action.mode)
        if action.exclude_last:
            last_mem = _last_member(action.group, history, skip_zero=True)
            ranked = [m for m in ranked if m != last_mem]
        return ranked[:action.count]
    if isinstance(action, ComboAction):
        out: List[str] = []
        for sub in action.actions:
            for lab in labels_for_action(sub, primary_reading, history):
                if lab not in out:
                    out.append(lab)
        # Sit out unless we have enough distinct labels (e.g. all components
        # present). Avoids a partial spread on the warmup spin.
        if len(out) < action.min_labels:
            return []
        return out
    if isinstance(action, DelegateAction):
        return []
    return []


def _delegate_names_in(action: Action) -> List[str]:
    """Delegate names referenced by an action, recursing into combos."""
    if isinstance(action, DelegateAction):
        return [action.strategy_name]
    if isinstance(action, ComboAction):
        names = []
        for sub in action.actions:
            names.extend(_delegate_names_in(sub))
        return names
    return []


def extract_delegate_names(rules: List[Rule]) -> List[str]:
    """List the unique sub-strategy names referenced by any Delegate action
    (including inside combos), preserving first-seen order."""
    seen = []
    for rule in rules:
        for name in _delegate_names_in(rule.then):
            if name not in seen:
                seen.append(name)
    return seen
