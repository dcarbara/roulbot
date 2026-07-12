"""ActionEditor — single action row with action-type-aware dynamic fields.

Used by CompositeEditor (PatternFollowerEditor uses a simpler inline action
since it never delegates and never bets explicit labels).

Action shapes:
    {"action": "follow",      "group": "color"}
    {"action": "contra",      "group": "color"}
    {"action": "target",      "group": "color", "target": "red"}
    {"action": "labels",      "labels": ["red", "1st12"]}
    {"action": "delegate",    "strategy": "<sub-strategy preset name>"}
    {"action": "follow_last", "group": "dozen", "skip_zero": true}
    {"action": "coldest",     "group": "dozen", "count": 1, "lookback": 18, "exclude_last": true}
    {"action": "hottest",     "group": "column", "count": 1, "lookback": 18}
    {"action": "combo",       "actions": [ ...sub-actions... ]}   # UNION their labels
"""
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from core.signals.base import GROUPS

GROUP_NAMES = list(GROUPS.keys())
# Top-level action choices (combo allowed). Sub-actions inside a combo exclude
# combo (no nesting) and delegate (keep combos to direct bets).
ACTION_NAMES = ["follow", "contra", "target", "labels", "delegate",
                "follow_last", "coldest", "hottest", "combo"]
SUBACTION_NAMES = ["follow", "contra", "target", "labels",
                   "follow_last", "coldest", "hottest"]


def _safe_int(s, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(s).strip())))
    except (ValueError, TypeError):
        return default


def _make_combo_searchable(combobox, get_master: Callable[[], List[str]]) -> None:
    """Make a CTkComboBox filter its dropdown as the user types: prefix →
    substring → fuzzy subsequence (initials, e.g. '6sb' → 6streetstratbundle).
    `get_master` returns the full option list live, so refreshes are picked up.
    """
    try:
        combobox.configure(state="normal")
    except Exception:
        return
    entry = getattr(combobox, "_entry", None)
    if entry is None or getattr(combobox, "_search_wired", False):
        return

    def _on_key(event=None):
        if event is not None and getattr(event, "keysym", "") in (
                "Up", "Down", "Return", "Escape", "Tab", "Left", "Right"):
            return
        query = (combobox.get() or "").strip().lower()
        master = list(get_master() or [])
        if not query:
            filtered = master
        else:
            prefix, contains, fuzzy = [], [], []
            for s in master:
                sl = str(s).lower()
                if sl.startswith(query):
                    prefix.append(s)
                elif query in sl:
                    contains.append(s)
                else:
                    it = iter(sl)
                    if all(ch in it for ch in query):
                        fuzzy.append(s)
            filtered = prefix + contains + fuzzy
        try:
            combobox.configure(values=filtered or master)
        except Exception:
            pass

    entry.bind("<KeyRelease>", _on_key)
    combobox._search_wired = True


class ActionEditor(ctk.CTkFrame):
    """Single action picker. Renders different fields per action type.

    `allow_combo=False` is used for sub-actions inside a combo (prevents
    nesting). `header_text` labels the row ("THEN" at top level, "•" for a
    combo sub-bet)."""

    def __init__(self, master, action: Optional[Dict[str, Any]] = None,
                 available_strategies: Optional[List[str]] = None,
                 strategies_provider: Optional[Callable[[], List[str]]] = None,
                 allow_combo: bool = True, header_text: str = "THEN",
                 **kwargs):
        super().__init__(master, fg_color=("#FAFAFA", "#1F1F23"),
                         corner_radius=6, **kwargs)

        self._available_strategies = list(available_strategies or [])
        self._strategies_provider = strategies_provider
        self._allow_combo = allow_combo
        self._action_choices = ACTION_NAMES if allow_combo else SUBACTION_NAMES

        action = action or {}
        a = action.get("action", "follow")
        if a not in self._action_choices:
            a = "follow"
        group = action.get("group", "color")
        if group not in GROUP_NAMES:
            group = "color"

        self.action_var = tk.StringVar(value=a)
        self.group_var = tk.StringVar(value=group)
        self.target_var = tk.StringVar(value=action.get("target", "")
                                       or GROUPS[group]["members"][0])
        labels = action.get("labels", [])
        labels_str = ", ".join(labels) if isinstance(labels, list) else str(labels)
        self.labels_var = tk.StringVar(value=labels_str)
        self.strategy_var = tk.StringVar(
            value=action.get("strategy") or action.get("strategy_name") or "")
        # History-aware action params
        self.skip_zero_var = tk.BooleanVar(value=bool(action.get("skip_zero", True)))
        self.count_var = tk.StringVar(value=str(action.get("count", 1)))
        self.lookback_var = tk.StringVar(value=str(action.get("lookback", 18)))
        self.exclude_last_var = tk.BooleanVar(value=bool(action.get("exclude_last", False)))
        self.min_labels_var = tk.StringVar(value=str(action.get("min_labels", 0)))
        # Combo sub-rows: list of (row_frame, ActionEditor). Seeded lazily when
        # the combo fields are rendered.
        self._combo_rows: List[tuple] = []
        self._combo_rows_frame = None
        self._initial_combo_actions = action.get("actions") if a == "combo" else None

        # Header
        ctk.CTkLabel(self, text=header_text, width=44,
                     font=("Arial", 11, "bold")).pack(side="left", padx=(8, 4), pady=6)
        ctk.CTkComboBox(
            self, variable=self.action_var, values=self._action_choices,
            state="readonly", width=110, command=self._on_action_change,
        ).pack(side="left", padx=2)

        self._fields_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._fields_frame.pack(side="left", padx=4, fill="x", expand=True)

        self._render_fields()

    # ----- public API -----

    def get_spec(self) -> Dict[str, Any]:
        a = self.action_var.get()
        spec: Dict[str, Any] = {"action": a}
        if a in ("follow", "contra"):
            spec["group"] = self.group_var.get()
        elif a == "target":
            spec["group"] = self.group_var.get()
            spec["target"] = self.target_var.get()
        elif a == "labels":
            raw = self.labels_var.get().strip()
            spec["labels"] = [s.strip() for s in raw.split(",") if s.strip()]
        elif a == "delegate":
            spec["strategy"] = self.strategy_var.get().strip()
        elif a == "follow_last":
            spec["group"] = self.group_var.get()
            spec["skip_zero"] = bool(self.skip_zero_var.get())
        elif a in ("coldest", "hottest"):
            spec["group"] = self.group_var.get()
            spec["count"] = _safe_int(self.count_var.get(), 1, 1, 12)
            spec["lookback"] = _safe_int(self.lookback_var.get(), 18, 1, 500)
            spec["exclude_last"] = bool(self.exclude_last_var.get())
        elif a == "combo":
            actions = [ed.get_spec() for (_f, ed) in self._combo_rows]
            spec["actions"] = actions or [{"action": "follow_last", "group": "dozen"}]
            spec["min_labels"] = _safe_int(self.min_labels_var.get(), 0, 0, 24)
        return spec

    def set_spec(self, action: Dict[str, Any]):
        if not isinstance(action, dict):
            return
        a = action.get("action", "follow")
        if a in self._action_choices:
            self.action_var.set(a)
        group = action.get("group")
        if group in GROUP_NAMES:
            self.group_var.set(group)
        if "target" in action:
            self.target_var.set(action["target"])
        if "labels" in action and isinstance(action["labels"], list):
            self.labels_var.set(", ".join(action["labels"]))
        strat = action.get("strategy") or action.get("strategy_name")
        if strat:
            self.strategy_var.set(strat)
        if "skip_zero" in action:
            self.skip_zero_var.set(bool(action["skip_zero"]))
        if "count" in action:
            self.count_var.set(str(action["count"]))
        if "lookback" in action:
            self.lookback_var.set(str(action["lookback"]))
        if "exclude_last" in action:
            self.exclude_last_var.set(bool(action["exclude_last"]))
        if "min_labels" in action:
            self.min_labels_var.set(str(action["min_labels"]))
        self._initial_combo_actions = action.get("actions") if a == "combo" else None
        self._render_fields()

    def refresh_strategies(self):
        if self._strategies_provider:
            try:
                self._available_strategies = list(self._strategies_provider())
            except Exception:
                pass
            self._render_fields()

    # ----- internals -----

    def _on_action_change(self, _value: str):
        self._render_fields()

    def _group_combo(self, on_change=None):
        return ctk.CTkComboBox(
            self._fields_frame, variable=self.group_var, values=GROUP_NAMES,
            state="readonly", width=85,
            command=on_change if on_change else (lambda _v: None),
        )

    def _render_fields(self):
        # Drop any combo sub-editors before wiping the frame so we don't keep
        # stale references to destroyed widgets.
        self._combo_rows = []
        self._combo_rows_frame = None
        for child in self._fields_frame.winfo_children():
            child.destroy()

        a = self.action_var.get()

        def _label(text):
            ctk.CTkLabel(self._fields_frame, text=text,
                         font=("Arial", 10)).pack(side="left", padx=(4, 2))

        def _entry(var, width, ph=None):
            ctk.CTkEntry(self._fields_frame, textvariable=var, width=width,
                         placeholder_text=ph or "").pack(side="left", padx=2)

        if a in ("follow", "contra"):
            _label("group:")
            self._group_combo().pack(side="left", padx=2)

        elif a == "target":
            _label("group:")

            def _on_group_change(group_value):
                members = GROUPS[group_value]["members"]
                target_dd.configure(values=members)
                if self.target_var.get() not in members:
                    self.target_var.set(members[0])

            self._group_combo(on_change=_on_group_change).pack(side="left", padx=2)
            _label("target:")
            target_dd = ctk.CTkComboBox(
                self._fields_frame, variable=self.target_var,
                values=GROUPS[self.group_var.get()]["members"],
                state="readonly", width=85,
            )
            target_dd.pack(side="left", padx=2)

        elif a == "labels":
            _label("labels (comma-separated):")
            ctk.CTkEntry(
                self._fields_frame, textvariable=self.labels_var, width=240,
                placeholder_text="red, 1st12, col2",
            ).pack(side="left", padx=2)

        elif a == "delegate":
            _label("strategy:")
            strategies = list(self._available_strategies) if self._available_strategies else []
            if not strategies:
                strategies = ["(no other strategies saved yet)"]
            delegate_combo = ctk.CTkComboBox(
                self._fields_frame, variable=self.strategy_var, values=strategies,
                state="normal", width=200,
            )
            delegate_combo.pack(side="left", padx=2)
            _make_combo_searchable(
                delegate_combo,
                lambda: (list(self._available_strategies) if self._available_strategies else strategies),
            )
            ctk.CTkButton(
                self._fields_frame, text="↻", width=24, height=24,
                command=self.refresh_strategies,
            ).pack(side="left", padx=2)

        elif a == "follow_last":
            _label("group:")
            self._group_combo().pack(side="left", padx=2)
            ctk.CTkCheckBox(self._fields_frame, text="skip 0", variable=self.skip_zero_var,
                            width=20, checkbox_width=18, checkbox_height=18,
                            font=("Arial", 10)).pack(side="left", padx=(8, 2))

        elif a in ("coldest", "hottest"):
            _label("group:")
            self._group_combo().pack(side="left", padx=2)
            _label("count:")
            _entry(self.count_var, 40)
            _label("lookback:")
            _entry(self.lookback_var, 50)
            ctk.CTkCheckBox(self._fields_frame, text="exclude last", variable=self.exclude_last_var,
                            width=20, checkbox_width=18, checkbox_height=18,
                            font=("Arial", 10)).pack(side="left", padx=(8, 2))

        elif a == "combo":
            container = ctk.CTkFrame(self._fields_frame, fg_color="transparent")
            container.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(container, text="bet ALL of (union of these):",
                         font=("Arial", 10, "italic"),
                         text_color="#71717A").pack(anchor="w", padx=2)
            self._combo_rows_frame = ctk.CTkFrame(container, fg_color="transparent")
            self._combo_rows_frame.pack(fill="x")
            seed = self._initial_combo_actions
            self._initial_combo_actions = None
            if isinstance(seed, list) and seed:
                for sub in seed:
                    self._add_combo_row(sub)
            else:
                self._add_combo_row({"action": "follow_last", "group": "dozen"})
            ctrls = ctk.CTkFrame(container, fg_color="transparent")
            ctrls.pack(anchor="w", pady=(3, 2), fill="x")
            ctk.CTkButton(ctrls, text="+ add bet", width=90, height=24,
                          fg_color="#475569", hover_color="#334155",
                          command=lambda: self._add_combo_row(
                              {"action": "follow_last", "group": "color"})
                          ).pack(side="left")
            ctk.CTkLabel(ctrls, text="min labels (0=any):",
                         font=("Arial", 10)).pack(side="left", padx=(12, 2))
            ctk.CTkEntry(ctrls, textvariable=self.min_labels_var, width=44).pack(side="left")

    # ----- combo sub-row management -----

    def _add_combo_row(self, sub_spec: Dict[str, Any]):
        if self._combo_rows_frame is None:
            return
        rowf = ctk.CTkFrame(self._combo_rows_frame, fg_color="transparent")
        rowf.pack(fill="x", pady=1)
        sub = ActionEditor(
            rowf, action=sub_spec, available_strategies=self._available_strategies,
            strategies_provider=self._strategies_provider,
            allow_combo=False, header_text="•",
        )
        sub.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(rowf, text="✕", width=24, height=24,
                      fg_color="#dc2626", hover_color="#b91c1c",
                      command=lambda: self._remove_combo_row(rowf, sub)).pack(side="left", padx=2)
        self._combo_rows.append((rowf, sub))

    def _remove_combo_row(self, rowf, sub):
        if len(self._combo_rows) <= 1:
            return  # keep at least one bet in a combo
        self._combo_rows = [(f, e) for (f, e) in self._combo_rows if e is not sub]
        try:
            rowf.destroy()
        except tk.TclError:
            pass
