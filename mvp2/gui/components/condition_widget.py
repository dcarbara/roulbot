"""ConditionEditor — single condition row with detector-aware dynamic fields.

Layout (left-to-right):
    [IF] [detector ▾] [group ▾] {detector-specific fields} [match ▾ if regime] [✕ if removable]

Used by:
    - PatternFollowerEditor (one condition per rule)
    - CompositeEditor    (one or more conditions per rule, ANDed)

The widget reads/writes a JSON-shaped condition spec, e.g.:
    {"detect": "streak",     "group": "color", "min_length": 3}
    {"detect": "dominance",  "group": "color", "window": 20, "threshold": 0.65}
    {"detect": "alternation","group": "color", "window": 10, "threshold": 0.7}
    {"detect": "regime",     "group": "color", "window": 20, "match": "TRENDING"}

Match key is exposed only for regime (since 'TRENDING' vs 'CHOPPY' is a real
choice). For other detectors the engine derives the match from the detector
type (streak->active, dominance->trending, alternation->choppy).
"""
import tkinter as tk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.signals.base import GROUPS

DETECTOR_NAMES = ["always", "streak", "dominance", "alternation", "regime", "last_number_in"]
GROUP_NAMES = list(GROUPS.keys())
REGIME_MATCHES = ["TRENDING", "CHOPPY", "any-active"]  # any-active = TRENDING|CHOPPY

# Detectors that don't use the group concept — group dropdown is hidden.
_NO_GROUP_DETECTORS = {"last_number_in", "always"}


class ConditionEditor(ctk.CTkFrame):
    """Single editable condition row. Compact, dynamic per-detector fields."""

    def __init__(self, master, condition: Optional[Dict[str, Any]] = None,
                 on_remove=None, removable: bool = True, **kwargs):
        super().__init__(master, fg_color=("#F4F4F5", "#27272A"),
                         corner_radius=6, **kwargs)
        self.on_remove = on_remove
        self._removable = removable

        condition = condition or {}
        detect = condition.get("detect", "streak")
        if detect not in DETECTOR_NAMES:
            detect = "streak"
        group = condition.get("group", "color")
        if group not in GROUP_NAMES:
            group = "color"

        # Variables (StringVar throughout to tolerate empty entries during typing)
        self.detect_var = tk.StringVar(value=detect)
        self.group_var = tk.StringVar(value=group)
        self.min_length_var = tk.StringVar(value=str(condition.get("min_length", 3)))
        self.window_var = tk.StringVar(value=str(condition.get("window", 20)))
        self.threshold_var = tk.StringVar(value=str(condition.get("threshold", 0.6)))
        self.trend_thr_var = tk.StringVar(value=str(condition.get("trend_threshold", 0.6)))
        self.chop_thr_var = tk.StringVar(value=str(condition.get("chop_threshold", 0.7)))
        # Regime match: "TRENDING" / "CHOPPY" / "any-active"
        regime_match = condition.get("match") or condition.get("regime") or "TRENDING"
        if isinstance(regime_match, list):
            regime_match = "any-active"
        if regime_match not in REGIME_MATCHES:
            regime_match = "TRENDING"
        self.regime_match_var = tk.StringVar(value=regime_match)
        # last_number_in: numbers list as comma-separated text + offset
        nums = condition.get("numbers", [])
        nums_str = ", ".join(str(int(n)) for n in nums) if isinstance(nums, (list, tuple)) else str(nums)
        self.numbers_var = tk.StringVar(value=nums_str)
        self.offset_var = tk.StringVar(value=str(condition.get("offset", 1)))

        # ----- Header (always visible) -----
        ctk.CTkLabel(self, text="IF", width=20,
                     font=("Arial", 11, "bold")).pack(side="left", padx=(8, 4), pady=6)

        ctk.CTkComboBox(
            self, variable=self.detect_var, values=DETECTOR_NAMES,
            state="readonly", width=120, command=self._on_detect_change,
        ).pack(side="left", padx=2)

        # Group dropdown — stored so we can hide it for group-less detectors.
        self.group_dd = ctk.CTkComboBox(
            self, variable=self.group_var, values=GROUP_NAMES,
            state="readonly", width=85,
        )
        self.group_dd.pack(side="left", padx=2)

        # ----- Dynamic field area (rebuilt on detector change) -----
        self._fields_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._fields_frame.pack(side="left", padx=4, fill="x", expand=True)

        # ----- Remove button (right side, only if removable) -----
        if self._removable:
            ctk.CTkButton(
                self, text="✕", width=24, height=24,
                fg_color="#dc2626", hover_color="#b91c1c",
                command=self._handle_remove,
            ).pack(side="right", padx=(2, 8))

        self._render_fields()

    # ----- public API -----

    def get_spec(self) -> Dict[str, Any]:
        """Serialize to a JSON-shaped condition dict."""
        detect = self.detect_var.get()
        spec: Dict[str, Any] = {"detect": detect}
        # Group is only attached for group-aware detectors
        if detect not in _NO_GROUP_DETECTORS:
            spec["group"] = self.group_var.get()
        if detect == "streak":
            spec["min_length"] = _safe_int(self.min_length_var.get(), default=1, lo=1)
        elif detect == "dominance":
            spec["window"] = _safe_int(self.window_var.get(), default=20, lo=2)
            spec["threshold"] = _safe_float(self.threshold_var.get(), default=0.6, lo=0.01, hi=1.0)
        elif detect == "alternation":
            spec["window"] = _safe_int(self.window_var.get(), default=10, lo=3)
            spec["threshold"] = _safe_float(self.threshold_var.get(), default=0.7, lo=0.01, hi=1.0)
        elif detect == "regime":
            spec["window"] = _safe_int(self.window_var.get(), default=20, lo=3)
            spec["trend_threshold"] = _safe_float(
                self.trend_thr_var.get(), default=0.6, lo=0.01, hi=1.0)
            spec["chop_threshold"] = _safe_float(
                self.chop_thr_var.get(), default=0.7, lo=0.01, hi=1.0)
            match = self.regime_match_var.get()
            if match == "any-active":
                spec["match"] = ["TRENDING", "CHOPPY"]
            else:
                spec["match"] = match
        elif detect == "last_number_in":
            # Parse the numbers field: accept "17, 8, 12" or "17,8,12"
            raw = self.numbers_var.get().strip()
            nums = []
            for tok in raw.replace(";", ",").split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    v = int(tok)
                except ValueError:
                    continue
                if 0 <= v <= 36 and v not in nums:
                    nums.append(v)
            spec["numbers"] = nums
            spec["offset"] = _safe_int(self.offset_var.get(), default=1, lo=1, hi=200)
        return spec

    def set_spec(self, condition: Dict[str, Any]):
        """Populate from a JSON-shaped condition dict."""
        if not isinstance(condition, dict):
            return
        detect = condition.get("detect", "streak")
        if detect not in DETECTOR_NAMES:
            detect = "streak"
        self.detect_var.set(detect)
        group = condition.get("group", "color")
        if group in GROUP_NAMES:
            self.group_var.set(group)
        if "min_length" in condition:
            self.min_length_var.set(str(condition["min_length"]))
        if "window" in condition:
            self.window_var.set(str(condition["window"]))
        if "threshold" in condition:
            self.threshold_var.set(str(condition["threshold"]))
        if "trend_threshold" in condition:
            self.trend_thr_var.set(str(condition["trend_threshold"]))
        if "chop_threshold" in condition:
            self.chop_thr_var.set(str(condition["chop_threshold"]))
        regime_match = condition.get("match") or condition.get("regime")
        if isinstance(regime_match, list):
            regime_match = "any-active"
        if regime_match in REGIME_MATCHES:
            self.regime_match_var.set(regime_match)
        if "numbers" in condition and isinstance(condition["numbers"], (list, tuple)):
            self.numbers_var.set(", ".join(str(int(n)) for n in condition["numbers"]))
        if "offset" in condition:
            self.offset_var.set(str(condition["offset"]))
        self._render_fields()

    # ----- internals -----

    def _on_detect_change(self, _value: str):
        self._render_fields()

    def _render_fields(self):
        for child in self._fields_frame.winfo_children():
            child.destroy()

        detect = self.detect_var.get()

        # Hide/show the group dropdown depending on whether the detector uses it.
        if hasattr(self, 'group_dd'):
            try:
                if detect in _NO_GROUP_DETECTORS:
                    self.group_dd.pack_forget()
                else:
                    # Re-pack in the same slot (before _fields_frame). If already
                    # packed, pack() is a no-op for a packed widget — call only when
                    # currently hidden.
                    if not self.group_dd.winfo_ismapped():
                        self.group_dd.pack(side="left", padx=2, before=self._fields_frame)
            except tk.TclError:
                pass

        def _label(text):
            ctk.CTkLabel(self._fields_frame, text=text,
                         font=("Arial", 10)).pack(side="left", padx=(4, 2))

        def _entry(var, width=44):
            ctk.CTkEntry(self._fields_frame, textvariable=var,
                         width=width).pack(side="left", padx=2)

        if detect == "always":
            ctk.CTkLabel(self._fields_frame, text="(fires every spin — unconditional)",
                         font=("Arial", 10), text_color="#71717A").pack(side="left", padx=(4, 2))

        elif detect == "streak":
            _label("streak ≥")
            _entry(self.min_length_var, width=44)

        elif detect == "dominance":
            _label("≥")
            _entry(self.threshold_var, width=50)
            _label("share in last")
            _entry(self.window_var, width=44)

        elif detect == "alternation":
            _label("flip-rate ≥")
            _entry(self.threshold_var, width=50)
            _label("in last")
            _entry(self.window_var, width=44)

        elif detect == "regime":
            _label("=")
            ctk.CTkComboBox(
                self._fields_frame, variable=self.regime_match_var,
                values=REGIME_MATCHES, state="readonly", width=110,
            ).pack(side="left", padx=2)
            _label("window")
            _entry(self.window_var, width=44)
            _label("trend≥")
            _entry(self.trend_thr_var, width=44)
            _label("chop≥")
            _entry(self.chop_thr_var, width=44)

        elif detect == "last_number_in":
            _label("numbers (0-36):")
            ctk.CTkEntry(
                self._fields_frame, textvariable=self.numbers_var, width=200,
                placeholder_text="e.g. 17, 8, 12",
            ).pack(side="left", padx=2)
            _label("offset")
            _entry(self.offset_var, width=40)

    def _handle_remove(self):
        if callable(self.on_remove):
            self.on_remove(self)


def _safe_int(s: str, default: int, lo: int = 1, hi: int = 10000) -> int:
    try:
        v = int(str(s).strip())
        return max(lo, min(hi, v))
    except (ValueError, TypeError):
        return default


def _safe_float(s: str, default: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(str(s).strip())
        return max(lo, min(hi, v))
    except (ValueError, TypeError):
        return default
