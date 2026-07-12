"""Modal editor for a bundle's conditional-trigger configuration.

Edits the in-memory `triggers_config` dict on the parent app:
    {
        "selection_mode": "rotation" | "conditional",
        "triggers":       {base_strategy_name: condition_spec, ...},
        "tiebreaker":     <registry key>,
        "fallback":       "stay" | "skip_round" | "rotation" | "first_in_list",
    }

The leaf-condition row UI covers the common types (cold, hot, misses, hits,
color/parity/dozen streaks). For compound (AND/OR / nested) conditions, users
edit the raw JSON via a per-row "Custom…" button — the data layer is fully
composable; this just keeps the leaf UI uncluttered.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Optional

import customtkinter as ctk

from core.triggers import CONDITION_REGISTRY, TIEBREAKER_REGISTRY


# ── Param schemas ─────────────────────────────────────────────────────────────
# Each entry: ordered list of (param_key, label, kind, default, choices).
#   kind: "int" | "str_choice"
#   choices: list of valid string values (only for str_choice)

LEAF_TYPES: list[str] = [
    "always", "never",
    "labels_cold", "labels_hot",
    "consecutive_misses", "consecutive_hits",
    "color_streak", "parity_streak", "dozen_streak",
]

PARAM_SCHEMAS: dict[str, list[tuple]] = {
    "always": [],
    "never": [],
    "labels_cold": [
        ("lookback", "Lookback (spins)", "int", 3, None),
        ("max_hits", "Max hits allowed",  "int", 0, None),
    ],
    "labels_hot": [
        ("lookback", "Lookback (spins)", "int", 5, None),
        ("min_hits", "Min hits required", "int", 3, None),
    ],
    "consecutive_misses": [("n", "Consecutive misses ≥", "int", 4, None)],
    "consecutive_hits":   [("n", "Consecutive hits ≥",   "int", 3, None)],
    "color_streak": [
        ("color", "Color", "str_choice", "any", ["any", "red", "black"]),
        ("n",     "Run length ≥", "int", 3, None),
    ],
    "parity_streak": [
        ("parity", "Parity", "str_choice", "any", ["any", "even", "odd"]),
        ("n",      "Run length ≥", "int", 3, None),
    ],
    "dozen_streak": [
        ("dozen", "Dozen", "str_choice", "any", ["any", "1st12", "2nd12", "3rd12"]),
        ("n",     "Run length ≥", "int", 3, None),
    ],
}

TIEBREAKER_OPTIONS = ["coldest", "hottest", "user_rank", "reverse_rank", "first_in_list", "random"]
FALLBACK_OPTIONS = ["stay", "skip_round", "rotation", "first_in_list"]


# Plain-English hint shown under the condition dropdown so users can see what
# each condition actually measures without leaving the editor.
CONDITION_HINTS: dict[str, str] = {
    "always":              "Always armed — useful as a safety-net entry (score 0).",
    "never":               "Never armed — temporarily disables this strategy.",
    "labels_cold":         "Strategy's labels appeared ≤ max_hits in the last `lookback` spins. Cold = good.",
    "labels_hot":          "Strategy's labels appeared ≥ min_hits in the last `lookback` spins. Hot = good.",
    "consecutive_misses":  "Strategy's labels missed N spins in a row.",
    "consecutive_hits":    "Strategy's labels hit N spins in a row.",
    "color_streak":        "Red or black has appeared N+ spins in a row. Useful for reversal bets.",
    "parity_streak":       "Even or odd has appeared N+ spins in a row.",
    "dozen_streak":        "Same dozen (1st12 / 2nd12 / 3rd12) has appeared N+ spins in a row.",
    "(custom)":            "Compound condition (AND/OR). Edit via the Custom… button.",
}

TIEBREAKER_HINTS: dict[str, str] = {
    "coldest":       "Pick the strategy with the longest dry spell on its labels.",
    "hottest":       "Pick the strategy with the most recent matches / longest run.",
    "user_rank":     "Pick the first entry in rotation list order that's armed.",
    "reverse_rank":  "Pick the LAST entry in rotation list order that's armed.",
    "first_in_list": "Same as user_rank — earliest entry wins.",
    "random":        "Randomly pick among all armed strategies.",
}

FALLBACK_HINTS: dict[str, str] = {
    "stay":          "Keep the current strategy active when nothing is armed.",
    "skip_round":    "Sit out the round — no bet placed when nothing is armed.",
    "rotation":      "Play the first list entry as a fallback.",
    "first_in_list": "Same as rotation.",
}


# Built-in example presets the user can one-click load. Each preset uses one
# of three `apply` modes:
#   "global" — set bundle-level global_trigger (one condition covers every
#              rotation entry; no per-strategy rows touched). The common case.
#   "pair"   — set per-strategy triggers on the first two rotation entries
#              (asymmetric scenarios like color reversal).
#   "clear"  — disable conditional mode entirely (back to plain rotation).
EXAMPLES: list[dict] = [
    {
        "name": "Cold Hunter",
        "summary": "Every strategy fires only when its own labels haven't hit in the last 5 spins. Coldest wins. Sits out when nothing is cold.",
        "best_for": "Bundles where every strategy targets a different sector and you want to bet only what's been quiet.",
        "apply": "global",
        "trigger": {"type": "labels_cold", "lookback": 5, "max_hits": 0},
        "tiebreaker": "coldest",
        "fallback": "skip_round",
    },
    {
        "name": "Hot Streak Rider",
        "summary": "Every strategy fires only when its labels are hot (≥3 hits in last 5 spins). Hottest wins.",
        "best_for": "Trend-following — bet what's been winning recently.",
        "apply": "global",
        "trigger": {"type": "labels_hot", "lookback": 5, "min_hits": 3},
        "tiebreaker": "hottest",
        "fallback": "stay",
    },
    {
        "name": "Color Reversal",
        "summary": "First strategy fires on red streaks ≥3; second fires on black streaks ≥3. Tip: have first strategy bet BLACK and second bet RED.",
        "best_for": "Outside-bet recovery plays on color streaks.",
        "apply": "pair",
        "first_trigger":  {"type": "color_streak", "color": "red",   "n": 3},
        "second_trigger": {"type": "color_streak", "color": "black", "n": 3},
        "tiebreaker": "hottest",
        "fallback": "skip_round",
    },
    {
        "name": "Strict (Cold AND Streak)",
        "summary": "Compound trigger applied to every strategy: fires only when its labels are cold (lookback 3) AND a red streak ≥3 is active. Stricter = higher conviction.",
        "best_for": "Filtering for high-confidence entries — fewer trades, better setups.",
        "apply": "global",
        "trigger": {
            "op": "and",
            "conditions": [
                {"type": "labels_cold", "lookback": 3, "max_hits": 0},
                {"type": "color_streak", "color": "red", "n": 3},
            ],
        },
        "tiebreaker": "coldest",
        "fallback": "skip_round",
    },
    {
        "name": "Sequential (Plain Rotation)",
        "summary": "Disables conditional selection — bot uses plain rotation logic (the original behavior).",
        "best_for": "Baseline / reset to plain rotation.",
        "apply": "clear",
        "trigger": None,
        "tiebreaker": "coldest",
        "fallback": "stay",
    },
]


def _is_compound(spec: Any) -> bool:
    return isinstance(spec, dict) and spec.get("op") in ("and", "or")


class _StrategyRow:
    """One editable trigger row for a single rotation strategy."""

    def __init__(self, parent: tk.Widget, base_name: str, initial_spec: Optional[dict]):
        self.base_name = base_name
        self.compound_spec: Optional[dict] = None  # set when user opens Custom… with a compound

        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.grid_columnconfigure(2, weight=1)

        self._enabled_var = tk.BooleanVar(value=initial_spec is not None)
        ctk.CTkCheckBox(self.frame, text="", variable=self._enabled_var, width=20,
                        command=self._on_enable_toggle).grid(row=0, column=0, padx=(4, 6), sticky="w")

        ctk.CTkLabel(self.frame, text=base_name, anchor="w", width=180).grid(
            row=0, column=1, padx=(0, 8), sticky="w")

        # Type dropdown
        initial_type = "always"
        if isinstance(initial_spec, dict):
            if _is_compound(initial_spec):
                initial_type = "(custom)"
                self.compound_spec = initial_spec
            else:
                initial_type = initial_spec.get("type", "always")
        self._type_var = tk.StringVar(value=initial_type)
        self._type_menu = ctk.CTkOptionMenu(
            self.frame, variable=self._type_var,
            values=LEAF_TYPES + ["(custom)"], width=170,
            command=lambda _v: (self._rebuild_params(), self._update_hint()),
        )
        self._type_menu.grid(row=0, column=2, padx=(0, 8), sticky="w")

        # Params subframe
        self._params_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        self._params_frame.grid(row=0, column=3, padx=(0, 8), sticky="ew")
        self._param_vars: dict[str, tk.Variable] = {}

        # Custom JSON button
        ctk.CTkButton(self.frame, text="Custom…", width=80,
                      command=self._edit_custom_json).grid(row=0, column=4, padx=(0, 4))

        # Plain-English hint under the row — updates whenever the type changes
        # so users learn what each condition means without leaving the editor.
        self._hint_label = ctk.CTkLabel(
            self.frame, text="", anchor="w", justify="left",
            font=("Segoe UI", 10), text_color="#94a3b8", wraplength=560,
        )
        self._hint_label.grid(row=1, column=1, columnspan=4, padx=(0, 8), pady=(0, 4), sticky="w")

        self._rebuild_params(seed=initial_spec if not _is_compound(initial_spec) else None)
        self._update_hint()
        self._apply_enable_state()

    def _update_hint(self) -> None:
        try:
            self._hint_label.configure(text="💡 " + CONDITION_HINTS.get(self._type_var.get(), ""))
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────

    def _on_enable_toggle(self) -> None:
        self._apply_enable_state()

    def _apply_enable_state(self) -> None:
        state = "normal" if self._enabled_var.get() else "disabled"
        try:
            self._type_menu.configure(state=state)
            for w in self._params_frame.winfo_children():
                if hasattr(w, "configure"):
                    try:
                        w.configure(state=state)
                    except Exception:
                        pass
        except Exception:
            pass

    def _rebuild_params(self, seed: Optional[dict] = None) -> None:
        for child in self._params_frame.winfo_children():
            child.destroy()
        self._param_vars.clear()
        type_ = self._type_var.get()
        if type_ == "(custom)":
            label = ctk.CTkLabel(self._params_frame, text="(edit via Custom… button)",
                                 text_color="#94a3b8")
            label.pack(side="left")
            return
        schema = PARAM_SCHEMAS.get(type_, [])
        col = 0
        for key, label_txt, kind, default, choices in schema:
            ctk.CTkLabel(self._params_frame, text=label_txt).grid(row=0, column=col, padx=(0, 4))
            col += 1
            initial = (seed or {}).get(key, default)
            if kind == "int":
                var = tk.StringVar(value=str(int(initial) if initial is not None else default))
                entry = ctk.CTkEntry(self._params_frame, textvariable=var, width=60)
                entry.grid(row=0, column=col, padx=(0, 10))
            else:  # str_choice
                var = tk.StringVar(value=str(initial or default))
                entry = ctk.CTkOptionMenu(self._params_frame, variable=var, values=list(choices),
                                          width=90)
                entry.grid(row=0, column=col, padx=(0, 10))
            self._param_vars[key] = var
            col += 1

    def _edit_custom_json(self) -> None:
        """Open a small JSON editor for compound / nested conditions."""
        current = self.collect_spec(allow_compound=True) or {"op": "and", "conditions": [
            {"type": "labels_cold", "lookback": 3, "max_hits": 0}
        ]}
        try:
            text = json.dumps(current, indent=2)
        except Exception:
            text = '{"op": "and", "conditions": []}'

        top = ctk.CTkToplevel(self.frame)
        top.title(f"Custom condition — {self.base_name}")
        top.geometry("520x420")
        top.transient(self.frame.winfo_toplevel())
        try:
            top.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(top, text=(
            "Edit raw JSON. Use {\"op\": \"and\"/\"or\", \"conditions\": [...]} for compounds. "
            "Leaf example: {\"type\": \"labels_cold\", \"lookback\": 3, \"max_hits\": 0}"
        ), wraplength=480, justify="left").pack(padx=12, pady=(12, 4), anchor="w")

        tb = ctk.CTkTextbox(top, font=("Consolas", 11))
        tb.pack(fill="both", expand=True, padx=12, pady=8)
        tb.insert("1.0", text)

        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def _save():
            raw = tb.get("1.0", "end").strip()
            try:
                spec = json.loads(raw)
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"{e}", parent=top)
                return
            ok, err = _validate_condition(spec)
            if not ok:
                messagebox.showerror("Invalid condition", err, parent=top)
                return
            if _is_compound(spec):
                self.compound_spec = spec
                self._type_var.set("(custom)")
                self._rebuild_params()
            else:
                self.compound_spec = None
                self._type_var.set(spec.get("type", "always"))
                self._rebuild_params(seed=spec)
            self._enabled_var.set(True)
            self._apply_enable_state()
            top.destroy()

        ctk.CTkButton(btns, text="Cancel", fg_color="#374151", hover_color="#1f2937",
                      command=top.destroy).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Apply", command=_save).pack(side="right", padx=4)

    # ──────────────────────────────────────────────────────────────────────

    def collect_spec(self, allow_compound: bool = True) -> Optional[dict]:
        """Read the current row into a condition spec dict, or None if disabled."""
        if not self._enabled_var.get():
            return None
        type_ = self._type_var.get()
        if type_ == "(custom)":
            return dict(self.compound_spec) if (allow_compound and self.compound_spec) else None
        schema = PARAM_SCHEMAS.get(type_, [])
        spec: dict[str, Any] = {"type": type_}
        for key, _label, kind, default, _choices in schema:
            var = self._param_vars.get(key)
            if var is None:
                continue
            raw = var.get()
            if kind == "int":
                try:
                    spec[key] = int(raw)
                except (ValueError, TypeError):
                    spec[key] = int(default)
            else:
                spec[key] = str(raw) if raw else str(default)
        return spec


class InlineTriggerEditor:
    """Compact single-strategy trigger editor for embedding inside the Bundle
    Creator dialog. No enable checkbox, no name label — the parent dialog
    already knows which strategy is being edited.

    The 'type' dropdown's first option is ``(none)`` which serializes to
    ``None`` — i.e. the strategy has no trigger configured. ``(custom)`` opens
    the JSON editor for compound (AND/OR) conditions.

    `on_change` is called (no args) whenever the user changes the type
    dropdown or applies a Custom-JSON edit — wire this up to auto-save the
    spec into the bundle's triggers_config so the user doesn't have to
    remember to click "Update Selected" just to persist a trigger change.
    The callback is NOT fired by programmatic `set_spec()` (which uses
    `_type_var.set(...)` rather than user-driven menu commands) so it's safe
    to call set_spec during row-select without spurious saves.
    """

    NONE_LABEL = "(none)"

    def __init__(self, parent: tk.Widget, label_text: str = "🎯 Trigger:",
                 on_change: Optional[Callable[[], None]] = None):
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.columnconfigure(2, weight=1)

        self.compound_spec: Optional[dict] = None
        self._on_change = on_change

        ctk.CTkLabel(self.frame, text=label_text, width=80, anchor="w",
                     font=("Segoe UI", 11, "bold")).grid(row=0, column=0, padx=(0, 4), sticky="w")

        self._type_var = tk.StringVar(value=self.NONE_LABEL)
        self._type_menu = ctk.CTkOptionMenu(
            self.frame, variable=self._type_var,
            values=[self.NONE_LABEL] + LEAF_TYPES + ["(custom)"],
            width=170,
            command=lambda _v: self._on_type_changed(),
        )
        self._type_menu.grid(row=0, column=1, padx=(0, 8), sticky="w")

        self._params_frame = ctk.CTkFrame(self.frame, fg_color="transparent")
        self._params_frame.grid(row=0, column=2, padx=(0, 8), sticky="ew")
        self._param_vars: dict[str, tk.Variable] = {}

        ctk.CTkButton(self.frame, text="Custom…", width=80,
                      command=self._edit_custom_json).grid(row=0, column=3, padx=(0, 4))

        self._hint_label = ctk.CTkLabel(
            self.frame, text="", anchor="w", justify="left",
            font=("Segoe UI", 10), text_color="#94a3b8", wraplength=560,
        )
        self._hint_label.grid(row=1, column=1, columnspan=3, padx=(0, 8), pady=(2, 4), sticky="w")

        self._rebuild_params()
        self._update_hint()

    # ── Param rendering ───────────────────────────────────────────────────

    def _rebuild_params(self, seed: Optional[dict] = None) -> None:
        for child in self._params_frame.winfo_children():
            child.destroy()
        self._param_vars.clear()
        type_ = self._type_var.get()
        if type_ in (self.NONE_LABEL, "(custom)"):
            return
        schema = PARAM_SCHEMAS.get(type_, [])
        col = 0
        # Auto-save on every committed param edit (FocusOut for entries, menu
        # command for str_choice). Without this, users could tune a lookback
        # value but it would never reach triggers_config unless they also
        # changed the type dropdown or clicked Update Selected.
        def _fire_change(*_a):
            if self._on_change is not None:
                try:
                    self._on_change()
                except Exception:
                    pass
        for key, label_txt, kind, default, choices in schema:
            ctk.CTkLabel(self._params_frame, text=label_txt).grid(row=0, column=col, padx=(0, 4))
            col += 1
            initial = (seed or {}).get(key, default)
            if kind == "int":
                var = tk.StringVar(value=str(int(initial) if initial is not None else default))
                entry = ctk.CTkEntry(self._params_frame, textvariable=var, width=60)
                entry.grid(row=0, column=col, padx=(0, 10))
                entry.bind("<FocusOut>", _fire_change)
                entry.bind("<Return>",   _fire_change)
            else:
                var = tk.StringVar(value=str(initial or default))
                ctk.CTkOptionMenu(self._params_frame, variable=var, values=list(choices),
                                  width=90, command=lambda _v: _fire_change()
                                  ).grid(row=0, column=col, padx=(0, 10))
            self._param_vars[key] = var
            col += 1

    def _update_hint(self) -> None:
        text = ("No trigger — this strategy won't participate in conditional selection."
                if self._type_var.get() == self.NONE_LABEL
                else CONDITION_HINTS.get(self._type_var.get(), ""))
        try:
            self._hint_label.configure(text="💡 " + text)
        except Exception:
            pass

    def _on_type_changed(self) -> None:
        """Type dropdown command: rebuild params, refresh hint, auto-save.

        Auto-save is the whole point of `on_change` — users were configuring
        triggers in Step 4 and not realizing they had to click "Update
        Selected" to persist. Wiring auto-save here means the moment they
        pick a type, the bundle's triggers_config sees it."""
        self._rebuild_params()
        self._update_hint()
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    # ── Custom JSON ──────────────────────────────────────────────────────

    def _edit_custom_json(self) -> None:
        current = self.get_spec(allow_compound=True) or {"op": "and", "conditions": [
            {"type": "labels_cold", "lookback": 3, "max_hits": 0}
        ]}
        try:
            text = json.dumps(current, indent=2)
        except Exception:
            text = '{"op": "and", "conditions": []}'

        top = ctk.CTkToplevel(self.frame)
        top.title("Custom condition")
        top.geometry("520x420")
        top.transient(self.frame.winfo_toplevel())
        try:
            top.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(top, text=(
            "Edit raw JSON. Use {\"op\": \"and\"/\"or\", \"conditions\": [...]} for compounds. "
            "Leaf example: {\"type\": \"labels_cold\", \"lookback\": 3, \"max_hits\": 0}"
        ), wraplength=480, justify="left").pack(padx=12, pady=(12, 4), anchor="w")

        tb = ctk.CTkTextbox(top, font=("Consolas", 11))
        tb.pack(fill="both", expand=True, padx=12, pady=8)
        tb.insert("1.0", text)

        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def _save():
            raw = tb.get("1.0", "end").strip()
            try:
                spec = json.loads(raw)
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"{e}", parent=top)
                return
            ok, err = _validate_condition(spec)
            if not ok:
                messagebox.showerror("Invalid condition", err, parent=top)
                return
            self.set_spec(spec)
            # Apply IS a user action — auto-save through to triggers_config so
            # the user doesn't have to remember a separate "Update Selected"
            # click after editing the JSON.
            if self._on_change is not None:
                try:
                    self._on_change()
                except Exception:
                    pass
            top.destroy()

        ctk.CTkButton(btns, text="Cancel", fg_color="#374151", hover_color="#1f2937",
                      command=top.destroy).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Apply", command=_save).pack(side="right", padx=4)

    # ── Public API ────────────────────────────────────────────────────────

    def get_spec(self, allow_compound: bool = True) -> Optional[dict]:
        """Return the current condition spec, or None when type is '(none)'."""
        type_ = self._type_var.get()
        if type_ == self.NONE_LABEL:
            return None
        if type_ == "(custom)":
            return dict(self.compound_spec) if (allow_compound and self.compound_spec) else None
        schema = PARAM_SCHEMAS.get(type_, [])
        spec: dict[str, Any] = {"type": type_}
        for key, _label, kind, default, _choices in schema:
            var = self._param_vars.get(key)
            if var is None:
                continue
            raw = var.get()
            if kind == "int":
                try:
                    spec[key] = int(raw)
                except (ValueError, TypeError):
                    spec[key] = int(default)
            else:
                spec[key] = str(raw) if raw else str(default)
        return spec

    def set_spec(self, spec: Optional[dict]) -> None:
        """Populate the editor from a condition spec (or None to clear)."""
        if spec is None:
            self.compound_spec = None
            self._type_var.set(self.NONE_LABEL)
            self._rebuild_params()
            self._update_hint()
            return
        if _is_compound(spec):
            self.compound_spec = spec
            self._type_var.set("(custom)")
            self._rebuild_params()
        else:
            self.compound_spec = None
            self._type_var.set(spec.get("type", "always"))
            self._rebuild_params(seed=spec)
        self._update_hint()

    def reset(self) -> None:
        self.set_spec(None)


def _validate_condition(spec: Any) -> tuple[bool, str]:
    if not isinstance(spec, dict):
        return False, "condition must be a JSON object"
    if "op" in spec:
        if spec["op"] not in ("and", "or"):
            return False, f"unknown op '{spec['op']}' (use 'and' or 'or')"
        children = spec.get("conditions")
        if not isinstance(children, list) or not children:
            return False, "compound conditions need a non-empty 'conditions' list"
        for c in children:
            ok, err = _validate_condition(c)
            if not ok:
                return False, err
        return True, ""
    if "type" not in spec:
        return False, "leaf conditions need a 'type' field"
    if spec["type"] not in CONDITION_REGISTRY:
        return (False,
                f"unknown type '{spec['type']}' — valid types: {sorted(CONDITION_REGISTRY)}")
    return True, ""


class TriggerEditorDialog:
    """Modal editor for `triggers_config` on the parent app.

    Usage:
        TriggerEditorDialog(app, rotation_entries=..., on_save=cb).open()
    """

    def __init__(self, app, rotation_entries: list[str],
                 on_save: Optional[Callable[[dict], None]] = None):
        self.app = app
        self.rotation_entries = list(rotation_entries or [])
        self.on_save = on_save
        self._win: Optional[ctk.CTkToplevel] = None
        self._rows: list[_StrategyRow] = []
        self._tiebreaker_var = tk.StringVar()
        self._fallback_var = tk.StringVar()
        self._enabled_var = tk.BooleanVar()

    # ──────────────────────────────────────────────────────────────────────

    def open(self) -> None:
        cfg = dict(getattr(self.app, "triggers_config", {}) or {})
        _mode = (cfg.get("selection_mode") or "rotation").lower()
        if _mode not in ("rotation", "conditional", "parallel"):
            _mode = "rotation"
        # Held as StringVar so the 3-way mode selector below can bind to it.
        self._mode_var = tk.StringVar(value=_mode)
        # Legacy enabled flag kept in sync for _apply_example etc.
        self._enabled_var.set(_mode != "rotation")
        self._tiebreaker_var.set(cfg.get("tiebreaker", "coldest"))
        self._fallback_var.set(cfg.get("fallback", "stay"))
        triggers = cfg.get("triggers") or {}
        global_trigger = cfg.get("global_trigger") or None

        root = self.app if hasattr(self.app, "winfo_toplevel") else getattr(self.app, "root", None)
        self._win = ctk.CTkToplevel(root) if root else ctk.CTkToplevel()
        self._win.title("Conditional Strategy Triggers")
        self._win.geometry("820x720")
        try:
            self._win.transient(root)
            self._win.grab_set()
        except Exception:
            pass

        # ── Header ───────────────────────────────────────────────────────
        header = ctk.CTkFrame(self._win, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 4))
        ctk.CTkLabel(header, text="🎯  Conditional Strategy Triggers",
                     font=("Segoe UI", 16, "bold")).pack(side="left")
        ctk.CTkButton(header, text="📚 Examples", width=110,
                      fg_color="#0ea5e9", hover_color="#0284c7",
                      command=self._open_examples).pack(side="right", padx=(0, 8))

        # ── Mode selector (3-way) ───────────────────────────────────────
        # Replaces the old enable checkbox so users can pick between the
        # original tiebreaker behavior and the new parallel mode without
        # having to know about a second hidden config field.
        mode_row = ctk.CTkFrame(self._win, fg_color="transparent")
        mode_row.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(mode_row, text="Selection Mode:",
                     font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 8))
        for label, val, color in [
            ("Off (plain rotation)",            "rotation",    "#475569"),
            ("Conditional (tiebreaker picks 1)","conditional", "#7c3aed"),
            ("Parallel (all armed bet)",        "parallel",    "#0ea5e9"),
        ]:
            ctk.CTkRadioButton(
                mode_row, text=label, variable=self._mode_var, value=val,
                command=lambda: self._enabled_var.set(self._mode_var.get() != "rotation"),
                fg_color=color, font=("Segoe UI", 11),
            ).pack(side="left", padx=(0, 12))

        # ── Visual flow diagram (canvas) ─────────────────────────────────
        self._draw_flow_diagram()

        ctk.CTkLabel(self._win, wraplength=780, justify="left", text=(
            "Each round: arm the strategies whose condition matches recent spins → "
            "tiebreaker picks the winner → if nothing's armed, fallback decides. Click "
            "📚 Examples for ready-to-use presets."
        ), text_color="#94a3b8").pack(padx=16, pady=(2, 4), anchor="w")

        # ── Global Trigger (applies to every rotation strategy) ──────────
        # The common case is one condition for the whole bundle. Configuring
        # it here means users don't have to fill 12 identical per-strategy
        # rows. Per-strategy rows below still win when set.
        _gt_frame = ctk.CTkFrame(self._win, fg_color="#1f2937", corner_radius=8)
        _gt_frame.pack(fill="x", padx=16, pady=(2, 4))
        ctk.CTkLabel(_gt_frame,
                     text="Global Trigger (applies to ALL strategies; per-strategy rows below override):",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#a78bfa").pack(anchor="w", padx=10, pady=(8, 2))
        self._global_trigger_editor = InlineTriggerEditor(_gt_frame, label_text="Global:")
        self._global_trigger_editor.frame.pack(fill="x", padx=10, pady=(0, 8))
        try:
            self._global_trigger_editor.set_spec(global_trigger)
        except Exception:
            pass

        # ── Per-strategy rows ────────────────────────────────────────────
        rows_outer = ctk.CTkFrame(self._win)
        rows_outer.pack(fill="both", expand=True, padx=16, pady=4)
        scroll = ctk.CTkScrollableFrame(rows_outer, label_text="Per-Strategy Triggers")
        scroll.pack(fill="both", expand=True, padx=4, pady=4)

        base_names: list[str] = []
        for entry in self.rotation_entries:
            base = entry.split(":", 1)[0].strip()
            if base and base not in base_names:
                base_names.append(base)
        if not base_names:
            ctk.CTkLabel(scroll, text=(
                "No rotation strategies configured. Add strategies to the rotation list first."
            ), text_color="#f59e0b").pack(padx=8, pady=12)

        for base in base_names:
            row = _StrategyRow(scroll, base, triggers.get(base))
            row.frame.pack(fill="x", padx=4, pady=3)
            self._rows.append(row)

        # ── Tiebreaker + Fallback ────────────────────────────────────────
        tb_frame = ctk.CTkFrame(self._win, fg_color="transparent")
        tb_frame.pack(fill="x", padx=16, pady=(8, 0))
        ctk.CTkLabel(tb_frame, text="Tiebreaker:", width=90, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(tb_frame, variable=self._tiebreaker_var,
                          values=[t for t in TIEBREAKER_OPTIONS if t in TIEBREAKER_REGISTRY],
                          width=160,
                          command=lambda _v: self._update_combo_hints()
                          ).pack(side="left", padx=(0, 24))
        ctk.CTkLabel(tb_frame, text="Fallback:", width=80, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(tb_frame, variable=self._fallback_var,
                          values=FALLBACK_OPTIONS, width=160,
                          command=lambda _v: self._update_combo_hints()
                          ).pack(side="left")

        # Static caption clarifying the condition/tiebreaker split. Without
        # this, users see "coldest" and look for a threshold on this dropdown
        # instead of in the per-strategy condition rows above.
        ctk.CTkLabel(self._win,
                     text="ℹ Thresholds (cold ≥ N spins, streak ≥ N, etc.) are set per-strategy "
                          "in the rows above. Tiebreaker only ranks strategies that already qualify.",
                     font=("Segoe UI", 10, "italic"), text_color="#a78bfa",
                     wraplength=760, justify="left").pack(padx=16, pady=(4, 0), anchor="w")

        # Inline hints for the two selectors — update as the user changes them.
        hint_frame = ctk.CTkFrame(self._win, fg_color="transparent")
        hint_frame.pack(fill="x", padx=16, pady=(2, 4))
        self._tb_hint = ctk.CTkLabel(hint_frame, text="", anchor="w", justify="left",
                                     wraplength=760, font=("Segoe UI", 10),
                                     text_color="#94a3b8")
        self._tb_hint.pack(anchor="w")
        self._fb_hint = ctk.CTkLabel(hint_frame, text="", anchor="w", justify="left",
                                     wraplength=760, font=("Segoe UI", 10),
                                     text_color="#94a3b8")
        self._fb_hint.pack(anchor="w")
        self._update_combo_hints()

        # ── Buttons ──────────────────────────────────────────────────────
        btns = ctk.CTkFrame(self._win, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(4, 16))
        ctk.CTkButton(btns, text="Save Triggers", command=self._on_save).pack(side="right", padx=4)
        ctk.CTkButton(btns, text="Cancel", fg_color="#374151", hover_color="#1f2937",
                      command=self._win.destroy).pack(side="right", padx=4)

    # ──────────────────────────────────────────────────────────────────────

    # ── Visual flow diagram ───────────────────────────────────────────────

    def _draw_flow_diagram(self) -> None:
        """Render a one-look flow diagram explaining round-to-round behavior."""
        wrap = ctk.CTkFrame(self._win, fg_color="#0f172a", corner_radius=8)
        wrap.pack(fill="x", padx=16, pady=(6, 4))
        # Plain tk.Canvas inside the CTk frame — CTk has no canvas widget but
        # this nests cleanly and inherits the dark frame as a bezel.
        canvas = tk.Canvas(wrap, height=140, bg="#0f172a", highlightthickness=0)
        canvas.pack(fill="x", padx=10, pady=10)
        # Defer the actual drawing until the widget knows its width.
        canvas.bind("<Configure>", lambda e: self._render_flow_diagram(canvas, e.width))

    def _render_flow_diagram(self, canvas: tk.Canvas, w: int) -> None:
        canvas.delete("all")
        if w < 100:
            return
        # 4 main boxes across the top row, 1 fallback box centered below.
        boxes_top = [
            ("🎲  Spin lands",       "#1e3a8a"),
            ("🎯  Arm triggers",     "#7c3aed"),
            ("⚖️  Tiebreaker picks", "#0ea5e9"),
            ("▶  Play strategy",     "#16a34a"),
        ]
        n = len(boxes_top)
        margin = 14
        gap = 16
        box_w = (w - 2 * margin - gap * (n - 1)) // n
        box_h = 44
        top_y = 14
        bot_y = top_y + box_h + 38  # second row Y

        centers_top: list[int] = []
        for i, (text, color) in enumerate(boxes_top):
            x0 = margin + i * (box_w + gap)
            x1 = x0 + box_w
            canvas.create_rectangle(x0, top_y, x1, top_y + box_h,
                                    fill=color, outline=color, width=0)
            canvas.create_text((x0 + x1) // 2, top_y + box_h // 2,
                               text=text, fill="white", font=("Segoe UI", 10, "bold"))
            centers_top.append((x0 + x1) // 2)
            if i < n - 1:
                arrow_y = top_y + box_h // 2
                canvas.create_line(x1 + 2, arrow_y, x1 + gap - 2, arrow_y,
                                   fill="#94a3b8", arrow=tk.LAST, width=2)

        # Fallback branch under box 2 ("Arm triggers") → "no candidate armed?"
        fb_text = "⏸  None armed → Fallback (stay / skip / first-in-list)"
        fb_w = max(360, w // 2)
        fb_x0 = centers_top[1] - 30
        fb_x1 = min(w - margin, fb_x0 + fb_w)
        canvas.create_rectangle(fb_x0, bot_y, fb_x1, bot_y + box_h - 8,
                                fill="#374151", outline="#374151", width=0)
        canvas.create_text((fb_x0 + fb_x1) // 2, bot_y + (box_h - 8) // 2,
                           text=fb_text, fill="#e5e7eb", font=("Segoe UI", 10))
        # Arrow from "Arm triggers" down to fallback box
        canvas.create_line(centers_top[1], top_y + box_h + 2,
                           centers_top[1], bot_y - 2,
                           fill="#94a3b8", arrow=tk.LAST, width=2)

    # ── Examples gallery ──────────────────────────────────────────────────

    def _open_examples(self) -> None:
        """Show a gallery of preset trigger configurations the user can load."""
        if not self._win:
            return
        top = ctk.CTkToplevel(self._win)
        top.title("Trigger Examples")
        top.geometry("680x540")
        top.transient(self._win)
        try:
            top.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(top, text="📚  Pick a preset to load",
                     font=("Segoe UI", 15, "bold")).pack(padx=16, pady=(14, 4), anchor="w")
        ctk.CTkLabel(top, wraplength=620, justify="left", text=(
            "Each preset applies its trigger to your current rotation list. "
            "'Cold Hunter' applies the same trigger to every strategy; "
            "'Color Reversal' configures the first two; 'Plain Rotation' clears triggers."
        ), text_color="#94a3b8").pack(padx=16, pady=(0, 8), anchor="w")

        scroll = ctk.CTkScrollableFrame(top)
        scroll.pack(fill="both", expand=True, padx=16, pady=8)

        for ex in EXAMPLES:
            card = ctk.CTkFrame(scroll, fg_color="#1f2937", corner_radius=8)
            card.pack(fill="x", padx=4, pady=6)

            head = ctk.CTkFrame(card, fg_color="transparent")
            head.pack(fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(head, text=ex["name"], font=("Segoe UI", 13, "bold"),
                         text_color="#f8fafc").pack(side="left")
            ctk.CTkButton(head, text="Load this preset", width=140,
                          fg_color="#16a34a", hover_color="#15803d",
                          command=lambda e=ex, t=top: self._apply_example(e, t)
                          ).pack(side="right")

            ctk.CTkLabel(card, text=ex["summary"], wraplength=600,
                         justify="left", text_color="#cbd5e1").pack(
                padx=12, pady=(0, 2), anchor="w")
            ctk.CTkLabel(card, text=f"Best for: {ex['best_for']}", wraplength=600,
                         justify="left", text_color="#64748b",
                         font=("Segoe UI", 10, "italic")).pack(
                padx=12, pady=(0, 10), anchor="w")

        ctk.CTkButton(top, text="Close", command=top.destroy).pack(pady=(0, 14))

    def _apply_example(self, ex: dict, popup: Optional[tk.Toplevel]) -> None:
        """Populate the editor's rows + global trigger + tiebreaker + fallback
        from a preset.

        Templates use `apply` to decide where the trigger goes:
          - "global": set the bundle-wide global_trigger, leave per-strategy
                      rows empty (the common case — one condition covers all)
          - "pair":   set per-strategy triggers on rows 0 and 1
                      (asymmetric scenarios like color reversal)
          - "clear":  disable conditional mode entirely (plain rotation)
        """
        mode = ex.get("apply", "global")
        # Disable every per-strategy row first; presets opt back in selectively.
        for row in self._rows:
            row._enabled_var.set(False)
            row.compound_spec = None
        # Clear the Global Trigger by default; "global"-mode presets re-set it.
        try:
            self._global_trigger_editor.set_spec(None)
        except Exception:
            pass

        def _apply_to(row: _StrategyRow, spec: dict) -> None:
            row._enabled_var.set(True)
            row.compound_spec = None
            if _is_compound(spec):
                row.compound_spec = spec
                row._type_var.set("(custom)")
                row._rebuild_params()
            else:
                row._type_var.set(spec.get("type", "always"))
                row._rebuild_params(seed=spec)
            row._update_hint()
            row._apply_enable_state()

        if mode == "global" and ex.get("trigger") is not None:
            try:
                self._global_trigger_editor.set_spec(ex["trigger"])
            except Exception:
                pass
        elif mode == "pair":
            if self._rows and ex.get("first_trigger") is not None:
                _apply_to(self._rows[0], ex["first_trigger"])
            if len(self._rows) > 1 and ex.get("second_trigger") is not None:
                _apply_to(self._rows[1], ex["second_trigger"])
        elif mode == "clear":
            pass  # all rows + global already cleared above

        # Tiebreaker / fallback / enable flag
        self._tiebreaker_var.set(ex.get("tiebreaker", "coldest"))
        self._fallback_var.set(ex.get("fallback", "stay"))
        self._enabled_var.set(mode != "clear")
        # All built-in presets are tiebreaker-based, not parallel — they have
        # asymmetric per-strategy conditions where one winner is the natural
        # semantic. So Examples set selection_mode to "conditional" on enable.
        if hasattr(self, '_mode_var'):
            self._mode_var.set("conditional" if mode != "clear" else "rotation")
        try:
            self._update_combo_hints()
        except Exception:
            pass

        if popup is not None:
            popup.destroy()
        messagebox.showinfo("Preset loaded",
                            f"Loaded '{ex['name']}'. Review the rows below and click "
                            "Save Triggers when ready.", parent=self._win)

    def _update_combo_hints(self) -> None:
        try:
            self._tb_hint.configure(text="💡 " + TIEBREAKER_HINTS.get(self._tiebreaker_var.get(), ""))
            self._fb_hint.configure(text="💡 " + FALLBACK_HINTS.get(self._fallback_var.get(), ""))
        except Exception:
            pass

    def _on_save(self) -> None:
        triggers: dict[str, dict] = {}
        for row in self._rows:
            spec = row.collect_spec(allow_compound=True)
            if spec is None:
                continue
            ok, err = _validate_condition(spec)
            if not ok:
                messagebox.showerror("Invalid trigger", f"{row.base_name}: {err}",
                                     parent=self._win)
                return
            triggers[row.base_name] = spec

        # Read the Global Trigger (None when type=(none))
        try:
            global_trigger = self._global_trigger_editor.get_spec()
        except Exception:
            global_trigger = None
        if global_trigger is not None:
            ok, err = _validate_condition(global_trigger)
            if not ok:
                messagebox.showerror("Invalid global trigger", err, parent=self._win)
                return

        # selection_mode now has 3 valid values: rotation / conditional / parallel.
        _mode = self._mode_var.get() if hasattr(self, '_mode_var') else (
            "conditional" if self._enabled_var.get() else "rotation"
        )
        if _mode not in ("rotation", "conditional", "parallel"):
            _mode = "rotation"
        new_cfg = {
            "selection_mode": _mode,
            "triggers":       triggers,
            "global_trigger": global_trigger,
            "tiebreaker":     self._tiebreaker_var.get() or "coldest",
            "fallback":       self._fallback_var.get() or "stay",
        }
        self.app.triggers_config = new_cfg

        # Re-init the runtime trigger engine so a swap takes effect for the
        # next round even if the bot is currently running.
        try:
            if hasattr(self.app, "rotation_strategies") and self.app.rotation_strategies:
                self.app._init_trigger_engine(list(self.app.rotation_strategies))
        except Exception:
            pass

        if self.on_save:
            try:
                self.on_save(new_cfg)
            except Exception:
                pass

        if self._win:
            self._win.destroy()
        messagebox.showinfo("Triggers saved",
                            f"Saved {len(triggers)} trigger(s). "
                            f"Mode: {new_cfg['selection_mode']}, "
                            f"tiebreaker: {new_cfg['tiebreaker']}, "
                            f"fallback: {new_cfg['fallback']}.")
