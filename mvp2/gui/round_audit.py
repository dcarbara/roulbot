"""Per-round audit subsystem.

Captures every spin's bets, outcome, strategy state, and post-round PnL into
a compact `RoundRecord`. Persists to SQLite (`winning_numbers.db`, table
`round_audit`) so history survives bot restarts. Surfaces the data through a
list view with a click-through detail dialog that visualises chip placement
on a synthetic European roulette board.

Public surface used by main_gui.py:
  - record_round(app, **fields)         # call once per round, after net_profit
  - RoundHistoryView(parent, app)        # the tab widget
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk


# ── Storage path ──────────────────────────────────────────────────────────────

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "winning_numbers.db")
_DB_LOCK = threading.Lock()


# ── Roulette colour map ───────────────────────────────────────────────────────

_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
def _color_of(n: int) -> str:
    if n == 0:
        return "GREEN"
    return "RED" if n in _RED else "BLACK"


# ── Data record ───────────────────────────────────────────────────────────────

@dataclass
class RoundRecord:
    # Identity
    timestamp: float
    session_num: int
    round_index: int

    # Result
    winning_number: Optional[int] = None
    winning_color: str = ""
    result: str = ""                     # "WIN" | "LOSS" | "BREAK_EVEN"

    # Bets — list[{label,amount,win,payout}]
    bets: list[dict] = field(default_factory=list)
    total_bet: float = 0.0
    total_return: float = 0.0
    net_profit: float = 0.0

    # Strategy / progression
    strategy_name: str = ""
    progression_type: str = ""
    base_bet: float = 0.0
    current_bet: float = 0.0
    martingale_level: int = 0

    # Run state after this round
    session_pnl_after: float = 0.0
    global_pnl_after: float = 0.0
    balance_after: float = 0.0
    win_streak: int = 0
    loss_streak: int = 0
    escalation_step: int = 0


# ── SQLite helpers ────────────────────────────────────────────────────────────

def _ensure_table() -> None:
    with _DB_LOCK, sqlite3.connect(_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS round_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_round_audit_ts ON round_audit(ts)")


def _persist(record: RoundRecord) -> None:
    try:
        _ensure_table()
        with _DB_LOCK, sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO round_audit (ts, payload) VALUES (?, ?)",
                (record.timestamp, json.dumps(asdict(record))),
            )
    except Exception as exc:
        print(f"[RoundAudit] Persist failed: {exc}")


def _load_recent(limit: int = 500, filters: Optional[dict] = None) -> list[RoundRecord]:
    """Load up to `limit` records from SQLite, newest-first by id, optionally
    filtered. Filters applied in Python after SQL date-range narrowing — JSON
    is small enough that this is fast for thousands of rows.

    Recognised filter keys (all optional):
      ts_from         float | None     unix-time lower bound (inclusive)
      ts_to           float | None     unix-time upper bound (inclusive)
      session_num     int | None       exact session number
      strategy_name   str | None       exact match on the strategy field
      result          str | None       "WIN" | "LOSS" | "BREAK_EVEN"
      winning_number  int | None       exact match on the spin
      min_net         float | None     net_profit >= min_net
      max_net         float | None     net_profit <= max_net
    """
    f = filters or {}
    where_sql = []
    params: list = []
    if f.get("ts_from") is not None:
        where_sql.append("ts >= ?"); params.append(float(f["ts_from"]))
    if f.get("ts_to") is not None:
        where_sql.append("ts <= ?"); params.append(float(f["ts_to"]))
    where_clause = (" WHERE " + " AND ".join(where_sql)) if where_sql else ""

    sql = f"SELECT payload FROM round_audit{where_clause} ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    try:
        _ensure_table()
        with _DB_LOCK, sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(sql, tuple(params))
            rows = cur.fetchall()
    except Exception as exc:
        print(f"[RoundAudit] Load failed: {exc}")
        return []

    out: list[RoundRecord] = []
    for (payload,) in rows:
        try:
            d = json.loads(payload)
            rec = RoundRecord(**d)
        except Exception:
            continue

        # Secondary filters in Python
        if f.get("session_num") is not None and rec.session_num != int(f["session_num"]):
            continue
        if f.get("strategy_name") and str(f["strategy_name"]).strip() != rec.strategy_name:
            continue
        if f.get("result") and str(f["result"]).upper() != (rec.result or "").upper():
            continue
        if f.get("winning_number") is not None and rec.winning_number != int(f["winning_number"]):
            continue
        if f.get("min_net") is not None and rec.net_profit < float(f["min_net"]):
            continue
        if f.get("max_net") is not None and rec.net_profit > float(f["max_net"]):
            continue
        out.append(rec)

    out.reverse()  # chronological
    return out


def _unique_strategies(limit_rows: int = 5000) -> list[str]:
    """Distinct strategy names seen in the audit log — drives the dropdown."""
    try:
        _ensure_table()
        with _DB_LOCK, sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute(
                "SELECT payload FROM round_audit ORDER BY id DESC LIMIT ?", (int(limit_rows),)
            )
            rows = cur.fetchall()
    except Exception:
        return []
    seen: set[str] = set()
    for (payload,) in rows:
        try:
            d = json.loads(payload)
            name = str(d.get("strategy_name") or "").strip()
            if name:
                seen.add(name)
        except Exception:
            continue
    return sorted(seen)


# ── Capture entry point used by main_gui.py ───────────────────────────────────

def record_round(app: Any, **fields) -> None:
    """Build a RoundRecord, append to the in-memory deque, persist to SQLite,
    and notify any open RoundHistoryView. Safe to call from a worker thread.
    """
    rec = RoundRecord(timestamp=time.time(), **fields)
    if not hasattr(app, "_round_history"):
        from collections import deque
        app._round_history = deque(maxlen=500)
    app._round_history.append(rec)
    _persist(rec)
    # Notify any open views — the Operations tab list AND the compact mini
    # card embedded in the Dashboard. Both routes go via root.after so the
    # Tk write happens on the main thread.
    for attr in ("_round_history_view", "_round_audit_mini"):
        view = getattr(app, attr, None)
        if view is None or getattr(app, "root", None) is None:
            continue
        try:
            app.root.after(0, lambda r=rec, v=view: v.append_row(r))
        except Exception:
            pass


# ── Roulette board canvas ─────────────────────────────────────────────────────

# Layout: 0 on left, then 12 columns × 3 rows. European single-zero.
# Top row in real layout is 3,6,...,36 (third dozen multiples). We render
# top-to-bottom mirroring a casino felt: row 0 = top = 3rd row; row 2 = bottom = 1st row.
_GRID_ROWS = [
    [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36],
    [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35],
    [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34],
]

def _color_hex(c: str) -> str:
    return {"RED": "#b91c1c", "BLACK": "#1f2937", "GREEN": "#15803d"}.get(c, "#374151")


class RouletteBoardCanvas(ctk.CTkFrame):
    """Synthetic board view that draws chip placements for a RoundRecord."""

    CELL_W = 44
    CELL_H = 44
    PAD = 8
    LEFT_W = 44                 # zero column width
    OUTSIDE_H = 36

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        rows, cols = 3, 12
        # +outside columns/rows
        width = self.PAD * 2 + self.LEFT_W + cols * self.CELL_W + 60  # +60 for column 2:1 cells
        height = self.PAD * 2 + rows * self.CELL_H + 3 * self.OUTSIDE_H
        self.canvas = tk.Canvas(self, width=width, height=height,
                                bg="#0b3d22", highlightthickness=0)
        self.canvas.pack(padx=4, pady=4)
        self._render_layout()

    # ── geometry helpers ──────────────────────────────────────────────────────

    def _cell_xy(self, row: int, col: int) -> tuple[int, int, int, int]:
        x0 = self.PAD + self.LEFT_W + col * self.CELL_W
        y0 = self.PAD + row * self.CELL_H
        return x0, y0, x0 + self.CELL_W, y0 + self.CELL_H

    def _zero_xy(self) -> tuple[int, int, int, int]:
        x0 = self.PAD
        y0 = self.PAD
        return x0, y0, x0 + self.LEFT_W, y0 + 3 * self.CELL_H

    def _outside_band(self, idx: int) -> tuple[int, int]:
        """Return (y_top, y_bottom) for the idx-th outside row (0=dozens, 1=halves, 2=color/parity)."""
        y_top = self.PAD + 3 * self.CELL_H + idx * self.OUTSIDE_H
        return y_top, y_top + self.OUTSIDE_H

    # ── layout drawing (no chips) ─────────────────────────────────────────────

    def _render_layout(self) -> None:
        c = self.canvas
        # 0 cell
        x0, y0, x1, y1 = self._zero_xy()
        c.create_rectangle(x0, y0, x1, y1, fill=_color_hex("GREEN"), outline="white")
        c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text="0",
                      fill="white", font=("Segoe UI", 12, "bold"))

        # 1..36 grid
        for r, row in enumerate(_GRID_ROWS):
            for col, n in enumerate(row):
                x0, y0, x1, y1 = self._cell_xy(r, col)
                c.create_rectangle(x0, y0, x1, y1,
                                   fill=_color_hex(_color_of(n)), outline="white")
                c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=str(n),
                              fill="white", font=("Segoe UI", 11, "bold"))

        # Column 2:1 cells on the right
        col_x0 = self.PAD + self.LEFT_W + 12 * self.CELL_W
        for r in range(3):
            y0 = self.PAD + r * self.CELL_H
            c.create_rectangle(col_x0, y0, col_x0 + 60, y0 + self.CELL_H,
                               fill="#374151", outline="white")
            c.create_text(col_x0 + 30, y0 + self.CELL_H // 2, text="2 to 1",
                          fill="#cbd5e1", font=("Segoe UI", 9))

        # Outside row 0: 1st 12 / 2nd 12 / 3rd 12
        labels = ["1st 12", "2nd 12", "3rd 12"]
        y0, y1 = self._outside_band(0)
        for i, lbl in enumerate(labels):
            x0 = self.PAD + self.LEFT_W + i * 4 * self.CELL_W
            x1 = x0 + 4 * self.CELL_W
            c.create_rectangle(x0, y0, x1, y1, fill="#1e3a8a", outline="white")
            c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=lbl,
                          fill="white", font=("Segoe UI", 10, "bold"))

        # Outside row 1: 1-18, EVEN, RED, BLACK, ODD, 19-36
        y0, y1 = self._outside_band(1)
        cell_w = (12 * self.CELL_W) / 6
        for i, lbl in enumerate(["1-18", "EVEN", "RED", "BLACK", "ODD", "19-36"]):
            x0 = self.PAD + self.LEFT_W + i * cell_w
            x1 = x0 + cell_w
            fill = "#1e3a8a"
            if lbl == "RED": fill = _color_hex("RED")
            if lbl == "BLACK": fill = _color_hex("BLACK")
            c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="white")
            c.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=lbl,
                          fill="white", font=("Segoe UI", 10, "bold"))

    # ── chip placement ────────────────────────────────────────────────────────

    def _bet_anchor(self, label: str) -> Optional[tuple[int, int]]:
        """Return the (x, y) on the canvas where the chip for `label` sits.
        Returns None for labels we can't position; the caller falls back to a
        side-list rendering.

        Approach:
          1. Special-case OUTSIDE bets (red/black/even/odd/halves/dozens/columns)
             so the chip lands on the outside-band cell, not the centroid of all
             numbers covered (which would float in the middle of the inside grid).
          2. For everything else, look the label up in ROULETTE_NUMBER_MAPPINGS
             (the canonical table from strategy_engine.py) and place the chip
             at the centroid of the numbers it covers. This handles straights,
             splits, streets, corners, double streets / six-lines automatically.
        """
        s = label.strip().lower()
        if not s:
            return None

        # Straight number bets get an exact-cell anchor (faster + cleaner than
        # centroid). "00" is American roulette only — treat as the zero cell.
        if s == "00":
            x0, y0, x1, y1 = self._zero_xy()
            return (x0 + x1) // 2, (y0 + y1) // 2
        if s.isdigit():
            n = int(s)
            if n == 0:
                x0, y0, x1, y1 = self._zero_xy()
                return (x0 + x1) // 2, (y0 + y1) // 2
            for r, row in enumerate(_GRID_ROWS):
                if n in row:
                    col = row.index(n)
                    x0, y0, x1, y1 = self._cell_xy(r, col)
                    return (x0 + x1) // 2, (y0 + y1) // 2

        # Dozens — chip on the corresponding 1st12/2nd12/3rd12 outside cell.
        if s in ("1st12", "1st_12", "1-12"):
            x0 = self.PAD + self.LEFT_W + 0 * 4 * self.CELL_W
            y0, y1 = self._outside_band(0)
            return x0 + 2 * self.CELL_W, (y0 + y1) // 2
        if s in ("2nd12", "2nd_12", "13-24"):
            x0 = self.PAD + self.LEFT_W + 1 * 4 * self.CELL_W
            y0, y1 = self._outside_band(0)
            return x0 + 2 * self.CELL_W, (y0 + y1) // 2
        if s in ("3rd12", "3rd_12", "25-36"):
            x0 = self.PAD + self.LEFT_W + 2 * 4 * self.CELL_W
            y0, y1 = self._outside_band(0)
            return x0 + 2 * self.CELL_W, (y0 + y1) // 2

        # Halves / parity / colour — outside band 1. Accept the "1to18"/"19to36"
        # aliases in addition to the dash form ("1-18" / "19-36").
        cell_w = (12 * self.CELL_W) / 6
        outside_idx_map = {
            "1-18": 0, "1to18": 0,
            "even": 1,
            "red": 2,
            "black": 3,
            "odd": 4,
            "19-36": 5, "19to36": 5,
        }
        if s in outside_idx_map:
            i = outside_idx_map[s]
            x0 = self.PAD + self.LEFT_W + i * cell_w
            y0, y1 = self._outside_band(1)
            return int(x0 + cell_w / 2), (y0 + y1) // 2

        # Columns — chip on the 2:1 cells to the right of each row.
        # col1 = bottom row (1,4,7,...), col2 = mid, col3 = top (3,6,9,...).
        col_map = {"col1": 2, "col2": 1, "col3": 0}
        if s in col_map:
            row = col_map[s]
            col_x0 = self.PAD + self.LEFT_W + 12 * self.CELL_W
            y0 = self.PAD + row * self.CELL_H
            return col_x0 + 30, y0 + self.CELL_H // 2

        # Generic fallback: look up the label's number list and centroid-place.
        # ROULETTE_NUMBER_MAPPINGS has every label the engine knows about
        # (splits, streets, corners, double streets, etc.), so this single
        # path replaces what used to be a half-dozen hard-coded suffix matches
        # and now handles labels like "1-2split", "4-6strt", "1-5corner",
        # "0-3dblstrt", etc. without special-casing each suffix.
        try:
            from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS
            # Mapping keys preserve original case ("1st12" not "1ST12") but
            # the comparison set in this method is lower-cased. Match against
            # the original label first, then fall back to a case-insensitive
            # scan.
            nums = ROULETTE_NUMBER_MAPPINGS.get(label) or ROULETTE_NUMBER_MAPPINGS.get(s)
            if nums is None:
                for k, v in ROULETTE_NUMBER_MAPPINGS.items():
                    if k.lower() == s:
                        nums = v
                        break
            if nums:
                return self._numbers_centroid(list(nums))
        except Exception:
            pass

        return None

    def _numbers_centroid(self, nums: list[int]) -> Optional[tuple[int, int]]:
        coords: list[tuple[int, int]] = []
        for n in nums:
            if n == 0:
                x0, y0, x1, y1 = self._zero_xy()
            else:
                pos = None
                for r, row in enumerate(_GRID_ROWS):
                    if n in row:
                        col = row.index(n)
                        pos = self._cell_xy(r, col)
                        break
                if pos is None:
                    continue
                x0, y0, x1, y1 = pos
            coords.append(((x0 + x1) // 2, (y0 + y1) // 2))
        if not coords:
            return None
        cx = sum(p[0] for p in coords) // len(coords)
        cy = sum(p[1] for p in coords) // len(coords)
        return cx, cy

    def render_record(self, rec: RoundRecord) -> list[str]:
        """Draw chips for the record. Returns labels we couldn't position
        (caller can show those in a side panel)."""
        # Clear any prior chips by tag
        self.canvas.delete("chip")

        # Highlight the winning number cell
        if rec.winning_number is not None:
            n = rec.winning_number
            if n == 0:
                x0, y0, x1, y1 = self._zero_xy()
            else:
                for r, row in enumerate(_GRID_ROWS):
                    if n in row:
                        col = row.index(n)
                        x0, y0, x1, y1 = self._cell_xy(r, col)
                        break
                else:
                    x0 = y0 = x1 = y1 = 0
            self.canvas.create_rectangle(
                x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                outline="#facc15", width=3, tags=("chip",)
            )

        unplaced: list[str] = []
        # Group bets by label so multiple chips on the same spot stack visually.
        grouped: dict[str, float] = {}
        for b in rec.bets:
            grouped[b.get("label", "?")] = grouped.get(b.get("label", "?"), 0.0) + float(b.get("amount", 0.0))

        for label, total in grouped.items():
            xy = self._bet_anchor(label)
            if xy is None:
                unplaced.append(f"{label}  ${total:.2f}")
                continue
            x, y = xy
            r = 14
            # Did this label win? — sum 'win' flag across this label's bet rows
            won = any(d.get("win") for d in rec.bets if d.get("label") == label)
            fill = "#facc15" if won else "#94a3b8"
            outline = "#1f2937"
            self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                    fill=fill, outline=outline, width=2,
                                    tags=("chip",))
            self.canvas.create_text(x, y, text=f"${total:g}",
                                    fill="#0f172a", font=("Segoe UI", 9, "bold"),
                                    tags=("chip",))
        return unplaced


# ── List view + detail dialog ─────────────────────────────────────────────────

class RoundHistoryView(ctk.CTkFrame):
    """Embedded panel showing a treeview of rounds; double-click → detail."""

    COLUMNS = ("idx", "time", "num", "color", "result", "bet", "net", "global")

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="#09090b")
        self.app = app
        app._round_history_view = self  # so record_round can notify

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(header, text="📋  Round Audit",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#facc15").pack(side="left")
        ctk.CTkLabel(header,
                     text="Double-click a row to view chip placement and full metadata.",
                     font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(side="left", padx=10)
        ctk.CTkButton(header, text="Refresh", width=90,
                      command=self._reload_from_db).pack(side="right", padx=(4, 0))

        # ── Filter bar ─────────────────────────────────────────────────────
        # Date / time range, session #, strategy, result, winning number, net
        # PnL bounds. Quick presets for the common ranges. All filters are
        # AND-combined; any blank field = "no constraint".
        self._row_count_var = tk.StringVar(value="0 rows")
        self._filter_state: dict = {}

        filter_card = ctk.CTkFrame(self, fg_color="#0f172a", corner_radius=8)
        filter_card.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(filter_card, text="🔎 Filters",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#facc15").pack(anchor="w", padx=10, pady=(8, 0))

        row1 = ctk.CTkFrame(filter_card, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=(4, 2))

        def _lbl(parent, text, w=80):
            return ctk.CTkLabel(parent, text=text, width=w,
                                font=ctk.CTkFont(size=10), anchor="w",
                                text_color="#cbd5e1")

        # Date range
        _lbl(row1, "From (YYYY-MM-DD):", w=130).pack(side="left")
        self._flt_from = tk.StringVar()
        ctk.CTkEntry(row1, textvariable=self._flt_from, width=110,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="2026-04-28").pack(side="left", padx=(2, 10))

        _lbl(row1, "To (YYYY-MM-DD):", w=120).pack(side="left")
        self._flt_to = tk.StringVar()
        ctk.CTkEntry(row1, textvariable=self._flt_to, width=110,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="2026-04-28").pack(side="left", padx=(2, 10))

        # Quick presets — compact buttons that fill the from/to vars
        for label, span_h in [("Today", 24), ("24h", 24), ("7d", 24*7), ("30d", 24*30), ("All", None)]:
            ctk.CTkButton(
                row1, text=label, width=46, height=22,
                font=ctk.CTkFont(size=9),
                fg_color="#1f2937", hover_color="#374151",
                command=lambda h=span_h, lbl=label: self._apply_preset(h, lbl),
            ).pack(side="left", padx=2)

        row2 = ctk.CTkFrame(filter_card, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=(2, 8))

        _lbl(row2, "Session #:", w=70).pack(side="left")
        self._flt_session = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self._flt_session, width=60,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="any").pack(side="left", padx=(2, 10))

        _lbl(row2, "Strategy:", w=60).pack(side="left")
        self._flt_strategy = tk.StringVar(value="(any)")
        self._strategy_combo = ctk.CTkComboBox(
            row2, variable=self._flt_strategy, width=180, height=24,
            font=ctk.CTkFont(size=10), values=["(any)"],
        )
        self._strategy_combo.pack(side="left", padx=(2, 10))

        _lbl(row2, "Result:", w=50).pack(side="left")
        self._flt_result = tk.StringVar(value="(any)")
        ctk.CTkComboBox(row2, variable=self._flt_result, width=100, height=24,
                        font=ctk.CTkFont(size=10),
                        values=["(any)", "WIN", "LOSS", "BREAK_EVEN"]).pack(side="left", padx=(2, 10))

        _lbl(row2, "Spin #:", w=50).pack(side="left")
        self._flt_spin = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self._flt_spin, width=50,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="any").pack(side="left", padx=(2, 10))

        _lbl(row2, "Net min:", w=55).pack(side="left")
        self._flt_min_net = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self._flt_min_net, width=60,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="—").pack(side="left", padx=(2, 4))
        _lbl(row2, "max:", w=30).pack(side="left")
        self._flt_max_net = tk.StringVar()
        ctk.CTkEntry(row2, textvariable=self._flt_max_net, width=60,
                     font=ctk.CTkFont(size=10),
                     placeholder_text="—").pack(side="left", padx=(2, 10))

        ctk.CTkButton(row2, text="Apply", width=70, height=24,
                      font=ctk.CTkFont(size=10, weight="bold"),
                      fg_color="#1d4ed8", hover_color="#2563eb",
                      command=self._apply_filters).pack(side="left", padx=(6, 2))
        ctk.CTkButton(row2, text="Clear", width=60, height=24,
                      font=ctk.CTkFont(size=10),
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._clear_filters).pack(side="left", padx=2)
        ctk.CTkLabel(row2, textvariable=self._row_count_var,
                     font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(side="right", padx=4)

        # Treeview
        tv_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        tv_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        style = ttk.Style()
        try:
            style.theme_use("default")
        except tk.TclError:
            pass
        style.configure("RoundAudit.Treeview",
                        background="#0f172a", fieldbackground="#0f172a",
                        foreground="#e5e7eb", rowheight=24,
                        bordercolor="#1f2937", borderwidth=0)
        style.configure("RoundAudit.Treeview.Heading",
                        background="#1f2937", foreground="#e5e7eb",
                        font=("Segoe UI", 9, "bold"))
        style.map("RoundAudit.Treeview",
                  background=[("selected", "#1d4ed8")],
                  foreground=[("selected", "white")])

        self.tree = ttk.Treeview(tv_frame, columns=self.COLUMNS, show="headings",
                                 style="RoundAudit.Treeview")
        widths = {"idx": 70, "time": 90, "num": 50, "color": 70,
                  "result": 100, "bet": 80, "net": 90, "global": 90}
        labels = {"idx": "Round", "time": "Time", "num": "#", "color": "Colour",
                  "result": "Result", "bet": "Bet $", "net": "Net $", "global": "Global $"}
        for col in self.COLUMNS:
            self.tree.heading(col, text=labels[col])
            self.tree.column(col, width=widths[col],
                             anchor="center" if col != "result" else "w")

        sb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)

        # Initial population from DB
        self._reload_from_db()

    # ── data ──────────────────────────────────────────────────────────────────

    def _reload_from_db(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        records = _load_recent(500, filters=self._filter_state or None)
        # Keep them in the app's deque too so detail dialog can find them
        from collections import deque
        if not hasattr(self.app, "_round_history"):
            self.app._round_history = deque(maxlen=500)
        self.app._round_history.clear()
        for r in records:
            self.app._round_history.append(r)
            self._insert_row(r)
        # Refresh strategy combobox values whenever we reload
        try:
            strategies = ["(any)"] + _unique_strategies()
            self._strategy_combo.configure(values=strategies)
        except Exception:
            pass
        try:
            self._row_count_var.set(f"{len(records)} row{'s' if len(records) != 1 else ''}")
        except Exception:
            pass

    # ── Filter helpers ────────────────────────────────────────────────────────

    def _parse_date(self, s: str, end_of_day: bool = False) -> Optional[float]:
        """Parse a YYYY-MM-DD string into a unix timestamp. End-of-day adds
        86399 seconds so the upper bound is inclusive of the same day."""
        s = (s or "").strip()
        if not s:
            return None
        try:
            t = time.strptime(s, "%Y-%m-%d")
            ts = time.mktime(t)
            return ts + (86400 - 1) if end_of_day else ts
        except ValueError:
            return None

    def _apply_filters(self) -> None:
        f: dict = {}
        ts_from = self._parse_date(self._flt_from.get(), end_of_day=False)
        ts_to = self._parse_date(self._flt_to.get(), end_of_day=True)
        if ts_from is not None: f["ts_from"] = ts_from
        if ts_to is not None:   f["ts_to"] = ts_to
        try:
            sess = (self._flt_session.get() or "").strip()
            if sess: f["session_num"] = int(sess)
        except ValueError:
            pass
        strat = (self._flt_strategy.get() or "").strip()
        if strat and strat != "(any)":
            f["strategy_name"] = strat
        result = (self._flt_result.get() or "").strip()
        if result and result != "(any)":
            f["result"] = result
        try:
            spin = (self._flt_spin.get() or "").strip()
            if spin: f["winning_number"] = int(spin)
        except ValueError:
            pass
        try:
            min_net = (self._flt_min_net.get() or "").strip()
            if min_net: f["min_net"] = float(min_net)
        except ValueError:
            pass
        try:
            max_net = (self._flt_max_net.get() or "").strip()
            if max_net: f["max_net"] = float(max_net)
        except ValueError:
            pass
        self._filter_state = f
        self._reload_from_db()

    def _clear_filters(self) -> None:
        self._flt_from.set("")
        self._flt_to.set("")
        self._flt_session.set("")
        self._flt_strategy.set("(any)")
        self._flt_result.set("(any)")
        self._flt_spin.set("")
        self._flt_min_net.set("")
        self._flt_max_net.set("")
        self._filter_state = {}
        self._reload_from_db()

    def _apply_preset(self, span_hours: Optional[int], label: str) -> None:
        """Quick date presets — set the from/to fields and re-apply."""
        if span_hours is None or label == "All":
            self._flt_from.set("")
            self._flt_to.set("")
        elif label == "Today":
            today = time.strftime("%Y-%m-%d", time.localtime())
            self._flt_from.set(today)
            self._flt_to.set(today)
        else:
            now = time.time()
            self._flt_from.set(time.strftime("%Y-%m-%d", time.localtime(now - span_hours * 3600)))
            self._flt_to.set(time.strftime("%Y-%m-%d", time.localtime(now)))
        self._apply_filters()

    def append_row(self, rec: RoundRecord) -> None:
        # Respect any active filter so a filtered view stays consistent when
        # new rounds land. Bump the row-count label either way.
        if self._record_matches_filter(rec):
            self._insert_row(rec)
            try:
                kids = self.tree.get_children()
                if kids:
                    self.tree.see(kids[-1])
                self._row_count_var.set(f"{len(kids)} row{'s' if len(kids) != 1 else ''}")
            except Exception:
                pass

    def _record_matches_filter(self, rec: RoundRecord) -> bool:
        f = self._filter_state or {}
        if f.get("ts_from") is not None and rec.timestamp < float(f["ts_from"]):
            return False
        if f.get("ts_to") is not None and rec.timestamp > float(f["ts_to"]):
            return False
        if f.get("session_num") is not None and rec.session_num != int(f["session_num"]):
            return False
        if f.get("strategy_name") and str(f["strategy_name"]).strip() != rec.strategy_name:
            return False
        if f.get("result") and str(f["result"]).upper() != (rec.result or "").upper():
            return False
        if f.get("winning_number") is not None and rec.winning_number != int(f["winning_number"]):
            return False
        if f.get("min_net") is not None and rec.net_profit < float(f["min_net"]):
            return False
        if f.get("max_net") is not None and rec.net_profit > float(f["max_net"]):
            return False
        return True

    def _insert_row(self, rec: RoundRecord) -> None:
        time_str = time.strftime("%H:%M:%S", time.localtime(rec.timestamp))
        num_str = "—" if rec.winning_number is None else str(rec.winning_number)
        result_emoji = {"WIN": "✅ WIN", "LOSS": "❌ LOSS",
                        "BREAK_EVEN": "➖ EVEN"}.get(rec.result, rec.result or "—")
        net_str = f"{rec.net_profit:+.2f}"
        global_str = f"{rec.global_pnl_after:+.2f}"
        bet_str = f"{rec.total_bet:.2f}"
        iid = f"row-{rec.timestamp:.6f}-{rec.round_index}"
        self.tree.insert(
            "", "end", iid=iid,
            values=(
                f"S{rec.session_num} · #{rec.round_index}",
                time_str, num_str, rec.winning_color,
                result_emoji, bet_str, net_str, global_str,
            ),
        )

    def _on_double_click(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        # Find by iid in the deque (iid encodes timestamp+round)
        recs = list(getattr(self.app, "_round_history", []))
        target = None
        for r in recs:
            if f"row-{r.timestamp:.6f}-{r.round_index}" == iid:
                target = r
                break
        if target is None:
            return
        RoundDetailDialog(self, target)


class RoundAuditMini(ctk.CTkFrame):
    """Compact card embedded in the Dashboard. Shows the last N rounds as
    clickable one-liners (round / time / number / result / bet / net),
    each opening the full RoundDetailDialog with chip-placement playback.

    Wires itself to `app._round_audit_mini` so record_round can call
    `append_row` live, and re-uses the same SQLite store as the Operations
    tab — so it shows immediately on Dashboard load even before a session
    has started in this run.
    """

    LIMIT = 8  # rows shown at a time

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="#0f172a", corner_radius=8)
        self.app = app
        app._round_audit_mini = self

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(header, text="🎰  Recent Round Audit",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#facc15").pack(side="left")
        ctk.CTkLabel(header, text="(click any row to open chip-placement view)",
                     font=ctk.CTkFont(size=9), text_color="#64748b").pack(side="left", padx=8)
        ctk.CTkButton(header, text="Refresh", width=80, height=22,
                      font=ctk.CTkFont(size=10),
                      command=self._reload_from_db).pack(side="right")

        # Treeview
        tv_frame = ctk.CTkFrame(self, fg_color="#0b1220")
        tv_frame.pack(fill="x", padx=12, pady=(0, 12))

        try:
            _style = ttk.Style()
            _style.configure(
                "AuditMini.Treeview",
                background="#0b1220", fieldbackground="#0b1220",
                foreground="#e5e7eb", rowheight=22, borderwidth=0,
            )
            _style.configure(
                "AuditMini.Treeview.Heading",
                background="#1f2937", foreground="#cbd5e1",
                font=("Segoe UI", 9, "bold"),
            )
            _style.map("AuditMini.Treeview",
                       background=[("selected", "#1d4ed8")],
                       foreground=[("selected", "white")])
        except Exception:
            pass

        cols = ("idx", "time", "num", "result", "bet", "net")
        self.tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                 height=self.LIMIT, style="AuditMini.Treeview")
        widths = {"idx": 80, "time": 80, "num": 60, "result": 90, "bet": 70, "net": 80}
        labels = {"idx": "Round", "time": "Time", "num": "Spin",
                  "result": "Result", "bet": "Bet", "net": "Net"}
        for c in cols:
            self.tree.heading(c, text=labels[c])
            self.tree.column(c, width=widths[c],
                             anchor="center" if c != "result" else "w")
        self.tree.pack(fill="x", padx=4, pady=4)
        self.tree.bind("<Double-1>", self._on_double_click)
        # Single-click also opens detail — feels snappier on the dashboard
        self.tree.bind("<Return>", self._on_double_click)

        self._reload_from_db()

    # ── data ──────────────────────────────────────────────────────────────────

    def _reload_from_db(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        recs = _load_recent(self.LIMIT)
        # Keep them in the app's deque so the detail dialog finds them
        from collections import deque
        if not hasattr(self.app, "_round_history"):
            self.app._round_history = deque(maxlen=500)
        for r in recs:
            if r not in self.app._round_history:
                self.app._round_history.append(r)
            self._insert_row(r)

    def append_row(self, rec: RoundRecord) -> None:
        self._insert_row(rec)
        # Trim to keep only LIMIT rows visible
        kids = self.tree.get_children()
        if len(kids) > self.LIMIT:
            for iid in kids[: len(kids) - self.LIMIT]:
                try: self.tree.delete(iid)
                except Exception: pass
        try:
            kids = self.tree.get_children()
            if kids:
                self.tree.see(kids[-1])
        except Exception:
            pass

    def _insert_row(self, rec: RoundRecord) -> None:
        time_str = time.strftime("%H:%M:%S", time.localtime(rec.timestamp))
        num_str = "—" if rec.winning_number is None else (
            f"{rec.winning_number} {rec.winning_color[:1] if rec.winning_color else ''}".strip()
        )
        result_emoji = {"WIN": "✅ Win", "LOSS": "❌ Loss",
                        "BREAK_EVEN": "➖ Even"}.get(rec.result, rec.result or "—")
        net_str = f"{rec.net_profit:+.2f}"
        bet_str = f"{rec.total_bet:.2f}"
        iid = f"row-{rec.timestamp:.6f}-{rec.round_index}"
        self.tree.insert(
            "", "end", iid=iid,
            values=(f"S{rec.session_num}·#{rec.round_index}",
                    time_str, num_str, result_emoji, bet_str, net_str),
        )

    def _on_double_click(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        recs = list(getattr(self.app, "_round_history", []))
        target = None
        for r in recs:
            if f"row-{r.timestamp:.6f}-{r.round_index}" == iid:
                target = r
                break
        if target is None:
            return
        RoundDetailDialog(self, target)


class RoundDetailDialog(ctk.CTkToplevel):
    def __init__(self, parent, rec: RoundRecord):
        super().__init__(parent)
        self.title(f"Round S{rec.session_num} · #{rec.round_index}")
        self.configure(fg_color="#09090b")
        self.geometry("780x720")
        self.transient(parent.winfo_toplevel())

        # Top: roulette board
        board_frame = ctk.CTkFrame(self, fg_color="#0f172a")
        board_frame.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(board_frame, text="🎰  Chip placement",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#facc15").pack(anchor="w", padx=10, pady=(8, 0))
        board = RouletteBoardCanvas(board_frame, fg_color="#0f172a")
        board.pack(padx=10, pady=10)
        unplaced = board.render_record(rec)
        if unplaced:
            ctk.CTkLabel(
                board_frame,
                text="Unmapped bets (no canvas position):  " + " · ".join(unplaced),
                font=ctk.CTkFont(size=10), text_color="#94a3b8",
                wraplength=720, justify="left",
            ).pack(anchor="w", padx=10, pady=(0, 8))

        # Bottom: metadata
        meta = ctk.CTkScrollableFrame(self, fg_color="#0f172a")
        meta.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def row(label, value, color="#e5e7eb"):
            r = ctk.CTkFrame(meta, fg_color="transparent")
            r.pack(fill="x", pady=2, padx=8)
            ctk.CTkLabel(r, text=label, width=180, anchor="w",
                         text_color="#94a3b8",
                         font=ctk.CTkFont(size=11)).pack(side="left")
            ctk.CTkLabel(r, text=str(value), anchor="w",
                         text_color=color,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")

        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.timestamp))
        net_color = "#4ade80" if rec.net_profit > 0 else "#f87171" if rec.net_profit < 0 else "#cbd5e1"

        row("Timestamp", ts_str)
        row("Session / Round", f"Session {rec.session_num}, round #{rec.round_index}")
        row("Winning number",
            f"{rec.winning_number} {rec.winning_color}" if rec.winning_number is not None else "—")
        row("Result", rec.result or "—",
            color={"WIN": "#4ade80", "LOSS": "#f87171"}.get(rec.result, "#cbd5e1"))
        row("Bets total", f"${rec.total_bet:.2f}")
        row("Return", f"${rec.total_return:.2f}")
        row("Net profit", f"{rec.net_profit:+.2f}", color=net_color)
        row("Strategy", rec.strategy_name or "—")
        row("Progression", rec.progression_type or "—")
        row("Base bet", f"${rec.base_bet:.2f}")
        row("Current bet (unit)", f"${rec.current_bet:.2f}")
        row("Martingale level", str(rec.martingale_level))
        row("Escalation step", str(rec.escalation_step))
        row("Session PnL after", f"{rec.session_pnl_after:+.2f}")
        row("Global PnL after", f"{rec.global_pnl_after:+.2f}")
        row("Balance after", f"${rec.balance_after:.2f}")
        row("Win streak after", str(rec.win_streak))
        row("Loss streak after", str(rec.loss_streak))

        # Bet breakdown
        ctk.CTkLabel(meta, text="Bet breakdown",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#facc15").pack(anchor="w", pady=(10, 4), padx=8)
        for b in rec.bets:
            label = b.get("label", "?")
            amt = float(b.get("amount", 0.0))
            won = b.get("win")
            payout = float(b.get("payout", 0.0))
            line = f"  {label:<20} ${amt:>6.2f}   "
            line += "✅" if won else "❌"
            if won:
                line += f"  payout +${payout:.2f}"
            ctk.CTkLabel(meta, text=line, anchor="w",
                         text_color="#cbd5e1",
                         font=ctk.CTkFont(family="Consolas", size=11)).pack(anchor="w", padx=12)

        # Close button
        ctk.CTkButton(self, text="Close", command=self.destroy,
                      fg_color="#1f2937", hover_color="#374151",
                      width=120, height=32).pack(pady=(0, 10))
