"""Rule-list editor for pattern_follower mode.

Each rule is a single condition (any of streak / dominance / alternation /
regime) plus a simple action (follow / contra / target). The condition's group
also serves as the action's group — matching the existing flat-shape rule
where `group` is implicit.

For multi-condition rules and delegate actions, see CompositeEditor.
"""
import tkinter as tk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.signals.base import GROUPS
from gui.components.condition_widget import ConditionEditor

GROUP_NAMES = list(GROUPS.keys())
SIMPLE_ACTIONS = ["follow", "contra", "target", "labels"]

# Persistence presets shown in the UI. Maps display label -> the JSON value
# written to the rule's persist_until field. Power users can hand-edit
# max_losses / max_spins dicts via JSON.
PERSIST_PRESETS = {
    "never (default)": None,
    "until win":       "win",
    "until loss":      "loss",
}
# Reverse lookup for restoring from saved config
_PERSIST_FROM_VALUE = {repr(v): k for k, v in PERSIST_PRESETS.items()}


class _RuleRow:
    """One row = one ConditionEditor + an inline action picker.

    Renders as two stacked frames inside a single rounded container so visually
    each rule is one card.
    """

    def __init__(self, parent_frame, rule: Optional[Dict[str, Any]] = None,
                 on_remove=None, on_move_up=None, on_move_down=None):
        rule = rule or {}
        self.on_remove = on_remove
        self.on_move_up = on_move_up
        self.on_move_down = on_move_down

        # Outer container frame
        self.frame = ctk.CTkFrame(parent_frame, fg_color=("#FFFFFF", "#1F1F23"),
                                  corner_radius=6, border_width=1,
                                  border_color=("#E4E4E7", "#3F3F46"))
        self.frame.pack(fill="x", padx=2, pady=4)

        # Top row: ConditionEditor + reorder/remove buttons aligned right
        top = ctk.CTkFrame(self.frame, fg_color="transparent")
        top.pack(fill="x", padx=4, pady=(4, 0))

        # Reorder/remove cluster (right)
        btn_cluster = ctk.CTkFrame(top, fg_color="transparent")
        btn_cluster.pack(side="right", padx=4)
        ctk.CTkButton(btn_cluster, text="✕", width=28, height=24,
                      fg_color="#dc2626", hover_color="#b91c1c",
                      command=self._handle_remove).pack(side="right", padx=1)
        ctk.CTkButton(btn_cluster, text="▼", width=24, height=24,
                      command=self._handle_down).pack(side="right", padx=1)
        ctk.CTkButton(btn_cluster, text="▲", width=24, height=24,
                      command=self._handle_up).pack(side="right", padx=1)

        # Condition (left, takes remaining width)
        self.condition = ConditionEditor(top, condition=rule, removable=False)
        self.condition.pack(side="left", fill="x", expand=True, padx=(2, 0))

        # Bottom row: simple action picker
        bottom = ctk.CTkFrame(self.frame, fg_color=("#F4F4F5", "#27272A"),
                              corner_radius=4)
        bottom.pack(fill="x", padx=4, pady=(2, 4))

        ctk.CTkLabel(bottom, text="THEN", width=44,
                     font=("Arial", 11, "bold")).pack(side="left", padx=(8, 4), pady=4)

        action = rule.get("action", "follow")
        if action not in SIMPLE_ACTIONS:
            action = "follow"
        self.action_var = tk.StringVar(value=action)
        ctk.CTkComboBox(
            bottom, variable=self.action_var, values=SIMPLE_ACTIONS,
            state="readonly", width=100, command=self._on_action_change,
        ).pack(side="left", padx=2)

        # Persist-until selector — drops to the right edge so it doesn't crowd the action.
        existing_persist = rule.get("persist_until")
        # Map saved value -> display label; fall back to "never" for unknown
        # (e.g. {"max_losses": N} which we don't expose in the simple dropdown).
        persist_label = _PERSIST_FROM_VALUE.get(repr(existing_persist), "never (default)")
        self.persist_var = tk.StringVar(value=persist_label)
        # Preserve advanced dict-shape persist_until verbatim if loaded
        self._persist_advanced = existing_persist if isinstance(existing_persist, dict) else None
        ctk.CTkLabel(bottom, text="persist:", font=("Arial", 10)).pack(side="right", padx=(2, 2))
        ctk.CTkComboBox(
            bottom, variable=self.persist_var, values=list(PERSIST_PRESETS.keys()),
            state="readonly", width=130,
        ).pack(side="right", padx=(2, 8))

        # Target dropdown: depends on the condition's group; only visible when action==target.
        self.target_var = tk.StringVar(value=rule.get("target", ""))
        self.target_dd = ctk.CTkComboBox(
            bottom, variable=self.target_var,
            values=GROUPS[self.condition.group_var.get()]["members"],
            state="readonly", width=85,
        )

        # Labels entry: comma-separated explicit bet labels; visible when action==labels.
        existing_labels = rule.get("labels", [])
        labels_str = ", ".join(existing_labels) if isinstance(existing_labels, list) else str(existing_labels)
        self.labels_var = tk.StringVar(value=labels_str)
        self.labels_entry = ctk.CTkEntry(
            bottom, textvariable=self.labels_var, width=280,
            placeholder_text="e.g. 17, 8, 12, 29, 25  (straight-up numbers or any label)",
        )

        # Watch the condition's group so we can refresh target options when it changes
        self.condition.group_var.trace_add("write", self._on_condition_group_change)

        # Initial target sync + visibility based on current action
        self._refresh_target_options()
        if self.target_var.get() not in self.target_dd.cget("values"):
            members = GROUPS[self.condition.group_var.get()]["members"]
            self.target_var.set(members[0])
        self._apply_action_visibility()

    def _on_action_change(self, _value):
        self._apply_action_visibility()

    def _on_condition_group_change(self, *_args):
        # Refresh target options when the condition's group changes
        self._refresh_target_options()

    def _refresh_target_options(self):
        members = GROUPS[self.condition.group_var.get()]["members"]
        self.target_dd.configure(values=members)
        if self.target_var.get() not in members:
            self.target_var.set(members[0])

    def _apply_action_visibility(self):
        """Show/hide the target dropdown and labels entry based on the chosen action."""
        action = self.action_var.get()
        # Hide both, then show the relevant one
        try:
            self.target_dd.pack_forget()
        except tk.TclError:
            pass
        try:
            self.labels_entry.pack_forget()
        except tk.TclError:
            pass
        if action == "target":
            self.target_dd.pack(side="left", padx=4)
            self.target_dd.configure(state="readonly")
        elif action == "labels":
            self.labels_entry.pack(side="left", padx=4, fill="x", expand=True)

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
        """Emit a flat-shape pattern_follower rule."""
        spec = self.condition.get_spec()  # detect, group, and detector-specific fields
        spec["action"] = self.action_var.get()
        if spec["action"] == "target":
            spec["target"] = self.target_var.get()
        elif spec["action"] == "labels":
            raw = self.labels_var.get().strip()
            spec["labels"] = [s.strip() for s in raw.replace(";", ",").split(",") if s.strip()]
        # Persistence: write the value from the dropdown, or preserve an
        # advanced dict-shape persist_until that was loaded (JSON-only).
        persist_label = self.persist_var.get()
        persist_value = PERSIST_PRESETS.get(persist_label, None)
        if persist_value is None and self._persist_advanced is not None:
            # User didn't touch the dropdown and the loaded value was an advanced
            # dict like {"max_losses": N} — preserve it round-trip.
            spec["persist_until"] = self._persist_advanced
        elif persist_value is not None:
            spec["persist_until"] = persist_value
        return spec

    def destroy(self):
        try:
            self.frame.destroy()
        except tk.TclError:
            pass


class PatternFollowerEditor(ctk.CTkFrame):
    """Rule-list editor for pattern_follower mode."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._rows: List[_RuleRow] = []

        ctk.CTkLabel(
            self, text="Pattern Follower — first matching rule fires each spin.",
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
            text="(buffer of past spins; auto-grows to deepest rule's window)",
            font=("Arial", 10), text_color="#71717A",
        ).pack(side="left")

        # Scrollable rules container
        self._rules_container = ctk.CTkScrollableFrame(
            self, fg_color=("#FAFAFA", "#18181B"),
            height=220, corner_radius=4,
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
            text="  Detectors: streak · dominance · alternation · regime    "
                 "Actions: follow · contra · target",
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
        default = {"detect": "streak", "group": "color", "min_length": 3, "action": "follow"}
        self._add_row_from_dict(default)

    def _add_row_from_dict(self, rule: Dict[str, Any]):
        row = _RuleRow(
            self._rules_container, rule=rule,
            on_remove=self._remove_row,
            on_move_up=self._move_row_up,
            on_move_down=self._move_row_down,
        )
        self._rows.append(row)
        self._refresh_empty_label()

    def _remove_row(self, row: _RuleRow):
        try:
            self._rows.remove(row)
        except ValueError:
            return
        row.destroy()
        self._refresh_empty_label()

    def _move_row_up(self, row: _RuleRow):
        i = self._rows.index(row)
        if i <= 0:
            return
        self._rows[i - 1], self._rows[i] = self._rows[i], self._rows[i - 1]
        self._repack_rows()

    def _move_row_down(self, row: _RuleRow):
        i = self._rows.index(row)
        if i >= len(self._rows) - 1:
            return
        self._rows[i + 1], self._rows[i] = self._rows[i], self._rows[i + 1]
        self._repack_rows()

    def _repack_rows(self):
        for row in self._rows:
            row.frame.pack_forget()
        for row in self._rows:
            row.frame.pack(fill="x", padx=2, pady=4)

    def _refresh_empty_label(self):
        if self._rows:
            self._empty_label.pack_forget()
        else:
            self._empty_label.pack(pady=20)
