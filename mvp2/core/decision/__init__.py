"""Decision layer — turns SignalReadings into bet actions via composable rules.

A Rule has:
    when: [Condition, ...]  — conditions on signal readings, all must match (AND)
    then: Action            — what to do when the rule fires

Action types:
    BetLabelsAction(labels)                     — bet explicit labels
    BetGroupAction(group, mode, target?)        — bet derived from primary reading
                                                  mode in {follow, contra, target}
    DelegateAction(strategy_name)               — hand off to a sub-strategy

Public API:
    parse_rule(spec)            — JSON dict -> Rule
    evaluate_rules(rules, ...)  — first-match-wins; returns (rule, readings_by_index)
    labels_for_action(...)      — convert an Action + primary reading into labels
    extract_delegate_names(...) — list strategy names referenced by Delegate actions
"""
from core.decision.rules import (
    Action,
    BetGroupAction,
    BetLabelsAction,
    Condition,
    DelegateAction,
    Rule,
    evaluate_rules,
    extract_delegate_names,
    labels_for_action,
    parse_rule,
)

__all__ = [
    "Action", "BetGroupAction", "BetLabelsAction", "DelegateAction",
    "Condition", "Rule",
    "evaluate_rules", "extract_delegate_names", "labels_for_action", "parse_rule",
]
