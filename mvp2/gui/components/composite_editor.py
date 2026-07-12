"""Rule-list editor for composite mode.

Each rule is a list of conditions (all ANDed) plus a single action. Action
types include 'delegate' which routes to another saved preset — that's the
regime-router pattern.

Rule shape emitted (composite shape):
    {"when": [<condition>, ...], "then": <action>}
"""
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from gui.components.action_widget import ActionEditor
from gui.components.condition_widget import ConditionEditor


class _CompoundRuleRow:
    """One rule = stack of ConditionEditors (ANDed) + one ActionEditor."""

    def __init__(self, parent_frame, rule: Optional[Dict[str, Any]] = None,
                 strategies_provider: Optional[Callable[[], List[str]]] = None,
                 on_remove=None, on_move_up=None, on_move_down=None):
        self._strategies_provider = strategies_provider
        self.on_remove = on_remove
        self.on_move_up = on_move_up
        self.on_move_down = on_move_down

        # Outer card
        self.frame = ctk.CTkFrame(parent_frame, fg_color=("#FFFFFF", "#1F1F23"),
                                  corner_radius=8, border_width=1,
                                  border_color=("#E4E4E7", "#3F3F46"))
        self.frame.pack(fill="x", padx=2, pady=6)

        # Header bar with reorder/remove
        header = ctk.CTkFrame(self.frame, fg_color="transparent")
        header.pack(fill="x", padx=4, pady=(4, 0))

        # Right-aligned button cluster
        btn_cluster = ctk.CTkFrame(header, fg_color="transparent")
        btn_cluster.pack(side="right")
        ctk.CTkButton(btn_cluster, text="✕ Remove rule", width=110, height=24,
                      fg_color="#dc2626", hover_color="#b91c1c",
                      command=self._handle_remove).pack(side="right", padx=2)
        ctk.CTkButton(btn_cluster, text="▼", width=24, height=24,
                      command=self._handle_down).pack(side="right", padx=1)
        ctk.CTkButton(btn_cluster, text="▲", width=24, height=24,
                      command=self._handle_up).pack(side="right", padx=1)

        ctk.CTkLabel(header, text="WHEN ALL OF:",
                     font=("Arial", 10, "bold"),
                     text_color="#2563eb").pack(side="left", padx=(2, 6))

        # Conditions container (each ANDed)
        self._conditions_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        self._conditions_frame.pack(fill="x", padx=4, pady=(2, 4))
        self._conditions: List[ConditionEditor] = []

        # Initial conditions from input rule (handle both shapes)
        rule = rule or {}
        if "when" in rule and "then" in rule:
            cond_specs = rule.get("when", []) or [{}]
            action_spec = rule.get("then", {}) or {}
        elif "detect" in rule and "action" in rule:
            # Flat shape — single condition + flat action
            cond_specs = [{k: v for k, v in rule.items()
                           if k not in ("action", "target", "strategy", "labels")}]
            action_spec = {
                "action": rule.get("action"),
                "group": rule.get("group"),
                "target": rule.get("target"),
                "strategy": rule.get("strategy"),
                "labels": rule.get("labels"),
            }
        else:
            cond_specs = [{}]
            action_spec = {}

        for cs in cond_specs:
            self._add_condition(cs)

        # Add condition button
        add_cond_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        add_cond_frame.pack(fill="x", padx=4, pady=(0, 4))
        ctk.CTkButton(
            add_cond_frame, text="+ AND another condition",
            command=lambda: self._add_condition({}),
            width=200, height=24,
            fg_color="#475569", hover_color="#334155",
        ).pack(side="left", padx=2)

        # Action editor (single)
        self.action = ActionEditor(
            self.frame, action=action_spec,
            strategies_provider=self._strategies_provider,
            available_strategies=(self._strategies_provider() if self._strategies_provider else []),
        )
        self.action.pack(fill="x", padx=4, pady=(2, 6))

    def _add_condition(self, cond_spec: Dict[str, Any]):
        # First condition is non-removable (every rule needs at least one)
        is_first = len(self._conditions) == 0
        cond = ConditionEditor(
            self._conditions_frame, condition=cond_spec,
            removable=not is_first, on_remove=self._remove_condition,
        )
        cond.pack(fill="x", pady=2)
        self._conditions.append(cond)

    def _remove_condition(self, cond: ConditionEditor):
        if len(self._conditions) <= 1:
            return  # always keep at least one
        try:
            self._conditions.remove(cond)
        except ValueError:
            return
        try:
            cond.destroy()
        except tk.TclError:
            pass

    def _handle_remove(self):
        if callable(self.on_remove):
            self.on_remove(self)

    def _handle_up(self):
        if callable(self.on_move_up):
            self.on_move_up(self)

    def _handle_down(self):
        if callable(self.on_move_down):
            self.on_move_down(self)

    def to_dict(self) -> Dict[str, Any]:
        when = [c.get_spec() for c in self._conditions]
        return {"when": when, "then": self.action.get_spec()}

    def destroy(self):
        try:
            self.frame.destroy()
        except tk.TclError:
            pass


class CompositeEditor(ctk.CTkFrame):
    """Full compound-rule editor for composite mode."""

    def __init__(self, master,
                 strategies_provider: Optional[Callable[[], List[str]]] = None,
                 **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._strategies_provider = strategies_provider
        self._rows: List[_CompoundRuleRow] = []

        # Header
        ctk.CTkLabel(
            self,
            text="Composite — compound conditions, delegate to other presets, regime routing.",
            font=("Arial", 11, "bold"),
        ).pack(anchor="w", pady=(2, 6))

        # History size row
        size_frame = ctk.CTkFrame(self, fg_color="transparent")
        size_frame.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(size_frame, text="History size:").pack(side="left")
        self.history_size_var = tk.StringVar(value="50")
        ctk.CTkEntry(
            size_frame, textvariable=self.history_size_var, width=60,
        ).pack(side="left", padx=(5, 8))
        ctk.CTkLabel(
            size_frame,
            text="(buffer of past spins; rules see this much history)",
            font=("Arial", 10), text_color="#71717A",
        ).pack(side="left")

        # Rules container (taller than pattern_follower since rules are bigger)
        self._rules_container = ctk.CTkScrollableFrame(
            self, fg_color=("#FAFAFA", "#18181B"),
            height=320, corner_radius=4,
        )
        self._rules_container.pack(fill="both", expand=True, pady=(0, 6))

        self._empty_label = ctk.CTkLabel(
            self._rules_container,
            text="No rules yet. Click '+ Add Rule' below.",
            text_color="#71717A", font=("Arial", 11, "italic"),
        )
        self._empty_label.pack(pady=20)

        # Footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x")
        ctk.CTkButton(
            footer, text="+ Add Rule", command=self._handle_add_rule,
            width=110, fg_color="#2563eb", hover_color="#1d4ed8",
        ).pack(side="left")
        ctk.CTkLabel(
            footer,
            text="  First-match-wins per spin · sub-strategies stay 'warm' (history-current)",
            font=("Arial", 9), text_color="#71717A",
        ).pack(side="left", padx=(8, 0))

    # ----- public API -----

    def get_rules(self) -> List[Dict[str, Any]]:
        return [row.to_dict() for row in self._rows]

    def get_history_size(self) -> int:
        raw = self.history_size_var.get().strip()
        try:
            return max(1, int(raw))
        except (ValueError, tk.TclError):
            return 50

    def set_rules(self, rules: List[Dict[str, Any]]):
        self.clear()
        for r in rules or []:
            self._add_row_from_dict(r)
        self._refresh_empty_label()

    def set_history_size(self, n: int):
        try:
            self.history_size_var.set(str(int(n)))
        except (TypeError, ValueError):
            self.history_size_var.set("50")

    def clear(self):
        for row in list(self._rows):
            row.destroy()
        self._rows.clear()
        self._refresh_empty_label()

    # ----- internals -----

    def _handle_add_rule(self):
        default = {
            "when": [{"detect": "regime", "group": "color", "match": "TRENDING"}],
            "then": {"action": "follow", "group": "color"},
        }
        self._add_row_from_dict(default)

    def _add_row_from_dict(self, rule: Dict[str, Any]):
        row = _CompoundRuleRow(
            self._rules_container, rule=rule,
            strategies_provider=self._strategies_provider,
            on_remove=self._remove_row,
            on_move_up=self._move_row_up,
            on_move_down=self._move_row_down,
        )
        self._rows.append(row)
        self._refresh_empty_label()

    def _remove_row(self, row: _CompoundRuleRow):
        try:
            self._rows.remove(row)
        except ValueError:
            return
        row.destroy()
        self._refresh_empty_label()

    def _move_row_up(self, row: _CompoundRuleRow):
        i = self._rows.index(row)
        if i <= 0:
            return
        self._rows[i - 1], self._rows[i] = self._rows[i], self._rows[i - 1]
        self._repack_rows()

    def _move_row_down(self, row: _CompoundRuleRow):
        i = self._rows.index(row)
        if i >= len(self._rows) - 1:
            return
        self._rows[i + 1], self._rows[i] = self._rows[i], self._rows[i + 1]
        self._repack_rows()

    def _repack_rows(self):
        for row in self._rows:
            row.frame.pack_forget()
        for row in self._rows:
            row.frame.pack(fill="x", padx=2, pady=6)

    def _refresh_empty_label(self):
        if self._rows:
            self._empty_label.pack_forget()
        else:
            self._empty_label.pack(pady=20)
