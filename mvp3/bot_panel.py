"""
bot_panel.py  —  SpinEdge Bot Controller Panel
Narrow sidebar (272 × 680 px, always on top, frameless).

Controls the bot process via subprocess + bot_cmd.json.
Reads bot_state.json for live stats (refreshed every 500 ms).

Usage:
    python bot_panel.py
    python bot_panel.py --x 1648   # pin to right of a 1920-wide display
"""
import sys, os, json, time, subprocess, argparse
import tkinter as tk
from tkinter import ttk

_HERE        = os.path.dirname(os.path.abspath(__file__))
STATE_FILE   = os.path.join(_HERE, "bot_state.json")
CMD_FILE     = os.path.join(_HERE, "bot_cmd.json")
CFG_FILE     = os.path.join(_HERE, "bot_config.json")
HISTORY_FILE = os.path.join(_HERE, "bot_history.json")

# Interpreter used to launch the bot/overlay subprocesses.
# Prefer the shared venv (..\venv) if present, else the running interpreter.
PYTHON       = sys.executable
_VENV_PY     = os.path.normpath(os.path.join(_HERE, "..", "venv", "Scripts", "python.exe"))
if os.path.exists(_VENV_PY):
    PYTHON = _VENV_PY

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#0d1117"
BG2    = "#161b22"
BG3    = "#21262d"
BORDER = "#30363d"
FG     = "#e6edf3"
DIM    = "#8b949e"
MUT    = "#3d444d"
GREEN  = "#3fb950"
RED    = "#f85149"
YELLOW = "#d29922"
BLUE   = "#58a6ff"
PURPLE = "#bc8cff"
ORANGE = "#f0883e"
WHITE  = "#ffffff"

WIN_W   = 272
WIN_H   = 820   # tall enough for config + stats + >=6 history rows + equity + footer
REFRESH = 500  # ms

PHASE_FG = {
    "WAITING": YELLOW, "PLACED":  BLUE,   "RESULT":  ORANGE,
    "PAUSED":  DIM,    "TP_HIT":  GREEN,  "SL_HIT":  RED,
    "STOPPED": MUT,
}

STRATEGIES_LIST = ["CORNER_HOT", "CORNER_DOZEN1", "CORNER_DOZEN2", "CORNER_DOZEN3",
                   "CORNERTOP", "S1", "S2", "S3"]

# Selectable base bets — multiples of $0.10. TP/SL scale off the validated
# $0.10 -> TP$30 / SL$45 ratios (TP = bet x 300, SL = bet x 450). The bot's
# chip decomposer figures out how to build any multiple-of-0.10 amount.
BET_PRESETS = [f"{i/10:.2f}" for i in range(1, 21)]   # 0.10 .. 2.00
TP_PER_UNIT    = 300.0   # TP     dollars per $1 of base bet   ($0.10 -> $30)
SL_PER_UNIT    = 450.0   # SL     dollars per $1 of base bet   ($0.10 -> $45)
CUMSL_PER_UNIT = 3000.0  # cum_sl dollars per $1 of base bet   ($0.10 -> $300)

DEFAULT_CFG = {"strategy": "CORNER_HOT", "base_bet": 0.1, "tp": 40.0, "sl": 48.80,
               "max_sessions": 50, "cum_sl": 0.0,
               "dynamic_base": False, "safe_bankroll": 1000.0,
               "book_pct": 0.2, "book_mult": 1.2,
               "auto_base": False, "auto_base_per_unit": 900.0}


def _f(size, bold=False):
    return ("Consolas", size, "bold" if bold else "normal")


def _hsep(parent, pady=(4, 0), side="top"):
    tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=pady, side=side)


def _load_cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in DEFAULT_CFG.items():
            d.setdefault(k, v)
        return d
    except Exception:
        return dict(DEFAULT_CFG)


def _save_cfg(cfg):
    try:
        with open(CFG_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def _write_cmd(cmd):
    try:
        with open(CMD_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cmd": cmd}, f)
    except Exception:
        pass


class Panel(tk.Tk):
    def __init__(self, x=20, y=60):
        super().__init__()
        self._bot_proc     = None   # spinedge_bot.py subprocess
        self._overlay_proc = None   # overlay_live.py subprocess
        self._paused       = False
        self._hist_len     = -1     # round-count of last-rendered history list
        self._setup(x, y)
        self._build()
        self._load_defaults()
        self._refresh()

    # ── Window chrome ────────────────────────────────────────────────────────
    def _setup(self, x, y):
        self.title("SpinEdge")
        self.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")
        self.resizable(False, True)
        self.minsize(WIN_W, 560)   # keep >=6 history rows visible when resized
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.overrideredirect(True)
        self._dx = self._dy = 0
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._stop_bot()
        self.destroy()

    def _press(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag(self, e):
        self.geometry(f"+{e.x_root-self._dx}+{e.y_root-self._dy}")

    def _bind_drag(self, w):
        w.bind("<ButtonPress-1>", self._press)
        w.bind("<B1-Motion>",     self._drag)

    # ── Build layout ─────────────────────────────────────────────────────────
    def _build(self):
        R = self

        # ── Title bar ─────────────────────────────────────────────────────────
        tb = tk.Frame(R, bg=BG2, height=30)
        tb.pack(fill="x")
        tb.pack_propagate(False)
        self._bind_drag(tb)

        tk.Label(tb, text="  ◈ SpinEdge Bot", fg=BLUE, bg=BG2,
                 font=_f(9, True), anchor="w").pack(side="left", fill="y")
        tk.Button(tb, text=" × ", fg=DIM, bg=BG2, bd=0, relief="flat",
                  font=_f(11), command=self._on_close, cursor="hand2",
                  activebackground="#2d1010", activeforeground=RED,
                  padx=4).pack(side="right")
        tk.Button(tb, text=" — ", fg=DIM, bg=BG2, bd=0, relief="flat",
                  font=_f(9), command=R.iconify, cursor="hand2",
                  activebackground=BG3, activeforeground=FG,
                  padx=2).pack(side="right")

        self.badge = tk.Label(tb, text="OFFLINE", fg=MUT, bg=BG2, font=_f(8, True))
        self.badge.pack(side="right", padx=8)

        _hsep(R, (0, 0))

        # ── Config section ─────────────────────────────────────────────────────
        cf = tk.Frame(R, bg=BG2, padx=10, pady=8)
        cf.pack(fill="x")
        self._bind_drag(cf)

        # Row 1: Strategy + Base Bet
        r1 = tk.Frame(cf, bg=BG2)
        r1.pack(fill="x", pady=(0, 4))

        tk.Label(r1, text="Strat", fg=DIM, bg=BG2, font=_f(8), width=5, anchor="w").pack(side="left")
        self.cfg_strat = ttk.Combobox(r1, values=STRATEGIES_LIST, width=10,
                                       font=_f(9), state="readonly")
        self.cfg_strat.pack(side="left", padx=(2, 8))

        tk.Label(r1, text="Bet $", fg=DIM, bg=BG2, font=_f(8), anchor="w").pack(side="left")
        self.cfg_bet = ttk.Combobox(r1, values=BET_PRESETS, width=5,
                                    font=_f(9), state="readonly")
        self.cfg_bet.pack(side="left", padx=2)
        self.cfg_bet.bind("<<ComboboxSelected>>", self._on_bet_change)

        # Row 2: TP + SL
        r2 = tk.Frame(cf, bg=BG2)
        r2.pack(fill="x", pady=(0, 6))

        tk.Label(r2, text="TP  $", fg=GREEN, bg=BG2, font=_f(8), width=5, anchor="w").pack(side="left")
        self.cfg_tp = tk.Entry(r2, width=7, bg=BG3, fg=FG, insertbackground=FG,
                               font=_f(9), relief="flat", bd=2)
        self.cfg_tp.pack(side="left", padx=(2, 8))

        tk.Label(r2, text="SL $", fg=RED, bg=BG2, font=_f(8), anchor="w").pack(side="left")
        self.cfg_sl = tk.Entry(r2, width=7, bg=BG3, fg=FG, insertbackground=FG,
                               font=_f(9), relief="flat", bd=2)
        self.cfg_sl.pack(side="left", padx=2)

        # Row 2b: Max Sessions + Cumulative Stop-Loss (safety limits)
        r2b = tk.Frame(cf, bg=BG2)
        r2b.pack(fill="x", pady=(0, 6))

        tk.Label(r2b, text="Sess", fg=DIM, bg=BG2, font=_f(8), width=5, anchor="w").pack(side="left")
        self.cfg_maxsess = tk.Entry(r2b, width=7, bg=BG3, fg=FG, insertbackground=FG,
                                    font=_f(9), relief="flat", bd=2)
        self.cfg_maxsess.pack(side="left", padx=(2, 8))

        tk.Label(r2b, text="CumSL", fg=RED, bg=BG2, font=_f(8), anchor="w").pack(side="left")
        self.cfg_cumsl = tk.Entry(r2b, width=7, bg=BG3, fg=FG, insertbackground=FG,
                                  font=_f(9), relief="flat", bd=2)
        self.cfg_cumsl.pack(side="left", padx=2)

        # Row 2c: Dynamic base bet (auto-scale with bankroll)
        r2c = tk.Frame(cf, bg=BG2)
        r2c.pack(fill="x", pady=(0, 6))

        self.cfg_dyn = tk.BooleanVar(value=False)
        tk.Checkbutton(r2c, text="Dyn base", variable=self.cfg_dyn,
                       fg=BLUE, bg=BG2, font=_f(8), anchor="w",
                       selectcolor=BG3, activebackground=BG2, activeforeground=BLUE,
                       bd=0, highlightthickness=0).pack(side="left")

        tk.Label(r2c, text="Bank$", fg=DIM, bg=BG2, font=_f(8), anchor="w").pack(side="left", padx=(8, 0))
        self.cfg_bank = tk.Entry(r2c, width=7, bg=BG3, fg=FG, insertbackground=FG,
                                 font=_f(9), relief="flat", bd=2)
        self.cfg_bank.pack(side="left", padx=2)

        # Row 2d: Auto base bet (simple balance-tier, checked each new fib sequence)
        r2d = tk.Frame(cf, bg=BG2)
        r2d.pack(fill="x", pady=(0, 6))

        self.cfg_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(r2d, text="Auto bet", variable=self.cfg_auto,
                       fg=GREEN, bg=BG2, font=_f(8), anchor="w",
                       selectcolor=BG3, activebackground=BG2, activeforeground=GREEN,
                       bd=0, highlightthickness=0).pack(side="left")

        tk.Label(r2d, text="per$", fg=DIM, bg=BG2, font=_f(8), anchor="w").pack(side="left", padx=(8, 0))
        self.cfg_autounit = tk.Entry(r2d, width=7, bg=BG3, fg=FG, insertbackground=FG,
                                     font=_f(9), relief="flat", bd=2)
        self.cfg_autounit.pack(side="left", padx=2)
        tk.Label(r2d, text="=+$0.10", fg=MUT, bg=BG2, font=_f(7), anchor="w").pack(side="left")

        # Row 3: START / PAUSE / STOP
        r3 = tk.Frame(cf, bg=BG2)
        r3.pack(fill="x", pady=(0, 4))

        btn_kw = dict(font=_f(9, True), relief="flat", bd=0, cursor="hand2", pady=5)

        self.btn_start = tk.Button(r3, text="▶  START", bg="#1a3a1a", fg=GREEN,
                                   command=self._start_bot,
                                   activebackground="#224422", activeforeground=GREEN,
                                   **btn_kw)
        self.btn_pause = tk.Button(r3, text="⏸  PAUSE", bg="#3a3010", fg=YELLOW,
                                   command=self._toggle_pause,
                                   activebackground="#4a4010", activeforeground=YELLOW,
                                   **btn_kw)
        self.btn_stop  = tk.Button(r3, text="■  STOP",  bg="#3a1010", fg=RED,
                                   command=self._stop_bot,
                                   activebackground="#4a1010", activeforeground=RED,
                                   **btn_kw)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            b.pack(side="left", expand=True, fill="x", padx=2)

        # Row 4: Overlay toggle
        r4 = tk.Frame(cf, bg=BG2)
        r4.pack(fill="x")
        self.btn_overlay = tk.Button(r4, text="⬚  Overlay: OFF",
                                     bg=BG3, fg=DIM,
                                     font=_f(8, True), relief="flat", bd=0,
                                     cursor="hand2", pady=4,
                                     command=self._toggle_overlay,
                                     activebackground=BG3, activeforeground=BLUE)
        self.btn_overlay.pack(fill="x", padx=2)

        _hsep(R, (0, 0))

        # ── Balance block ─────────────────────────────────────────────────────
        bf = tk.Frame(R, bg=BG, padx=10, pady=6)
        bf.pack(fill="x")

        tk.Label(bf, text="BALANCE", fg=MUT, bg=BG, font=_f(7), anchor="w").pack(fill="x")

        self.b_val = tk.Label(bf, text="$—", fg=WHITE, bg=BG, font=_f(22, True), anchor="w")
        self.b_val.pack(fill="x")

        nr = tk.Frame(bf, bg=BG)
        nr.pack(fill="x")
        self.b_net = tk.Label(nr, text="Net  —", fg=DIM, bg=BG, font=_f(10, True), anchor="w")
        self.b_pct = tk.Label(nr, text="",        fg=DIM, bg=BG, font=_f(9),      anchor="w", padx=4)
        self.b_net.pack(side="left")
        self.b_pct.pack(side="left")

        pb_wrap = tk.Frame(bf, bg=BG, pady=3)
        pb_wrap.pack(fill="x")
        self.pb = tk.Canvas(pb_wrap, bg=MUT, height=8, highlightthickness=0, bd=0)
        self.pb.pack(fill="x")

        tr = tk.Frame(bf, bg=BG)
        tr.pack(fill="x")
        self.b_tp = tk.Label(tr, text="TP +$—", fg=GREEN, bg=BG, font=_f(8), anchor="w")
        self.b_sl = tk.Label(tr, text="SL -$—", fg=RED,   bg=BG, font=_f(8), anchor="w", padx=8)
        self.b_tp.pack(side="left")
        self.b_sl.pack(side="left")

        _hsep(R, (4, 0))

        # ── Round block ───────────────────────────────────────────────────────
        rf = tk.Frame(R, bg=BG, padx=10, pady=5)
        rf.pack(fill="x")

        rr = tk.Frame(rf, bg=BG)
        rr.pack(fill="x")
        self.r_num   = tk.Label(rr, text="ROUND —", fg=FG,     bg=BG, font=_f(9, True), anchor="w")
        self.r_phase = tk.Label(rr, text="OFFLINE", fg=MUT,    bg=BG, font=_f(9, True), anchor="e")
        self.r_num.pack(side="left")
        self.r_phase.pack(side="right")

        self.r_last = tk.Label(rf, text="Last: —",   fg=DIM, bg=BG, font=_f(9), anchor="w")
        self.r_bet  = tk.Label(rf, text="",           fg=DIM, bg=BG, font=_f(8), anchor="w")
        self.r_fib  = tk.Label(rf, text="Fib: L0 ×1", fg=DIM, bg=BG, font=_f(8), anchor="w")
        self.r_last.pack(fill="x")
        self.r_bet.pack(fill="x")
        self.r_fib.pack(fill="x")

        _hsep(R, (4, 0))

        # ── Stats block ───────────────────────────────────────────────────────
        stf = tk.Frame(R, bg=BG, padx=10, pady=5)
        stf.pack(fill="x")

        tk.Label(stf, text="STATS", fg=MUT, bg=BG, font=_f(7), anchor="w").pack(fill="x")

        self.st_wl   = tk.Label(stf, text="W: 0  L: 0  (—%)", fg=FG,  bg=BG, font=_f(9), anchor="w")
        self.st_ws   = tk.Label(stf, text="Win   streak  —",   fg=DIM, bg=BG, font=_f(8), anchor="w")
        self.st_ls   = tk.Label(stf, text="Loss  streak  —",   fg=DIM, bg=BG, font=_f(8), anchor="w")
        self.st_sess = tk.Label(stf, text="Sessions: —",       fg=DIM, bg=BG, font=_f(8), anchor="w")
        for w in (self.st_wl, self.st_ws, self.st_ls, self.st_sess):
            w.pack(fill="x")

        _hsep(R, (4, 0))

        # ── Footer (pinned to very bottom) ─────────────────────────────────────
        foot = tk.Frame(R, bg=BG2, padx=10, pady=3)
        foot.pack(side="bottom", fill="x")
        self.ft_ts   = tk.Label(foot, text="—", fg=MUT, bg=BG2, font=_f(8), anchor="w")
        self.ft_info = tk.Label(foot, text="",  fg=MUT, bg=BG2, font=_f(8), anchor="e")
        self.ft_ts.pack(side="left")
        self.ft_info.pack(side="right")
        _hsep(R, (0, 0), side="bottom")

        # ── Equity Curve (pinned just above footer, below history) ─────────────
        ef = tk.Frame(R, bg=BG, padx=10, pady=4)
        ef.pack(side="bottom", fill="x")
        tk.Label(ef, text="EQUITY CURVE", fg=MUT, bg=BG, font=_f(7), anchor="w").pack(fill="x")
        self.eq_canvas = tk.Canvas(ef, bg=BG3, height=80,
                                   bd=0, highlightthickness=0)
        self.eq_canvas.pack(fill="x", pady=(2, 0))
        _hsep(R, (4, 0), side="bottom")

        # ── History (scrollable, fills remaining space above equity) ───────────
        hf = tk.Frame(R, bg=BG, padx=10, pady=4)
        hf.pack(side="top", fill="both", expand=True)

        tk.Label(hf, text="ROUND HISTORY", fg=MUT, bg=BG, font=_f(7), anchor="w").pack(fill="x")

        htext = tk.Frame(hf, bg=BG3)
        htext.pack(fill="both", expand=True, pady=(2, 0))

        hscroll = tk.Scrollbar(htext, orient="vertical", width=9,
                               bg=BG3, troughcolor=BG, bd=0,
                               highlightthickness=0, activebackground=MUT,
                               relief="flat")
        hscroll.pack(side="right", fill="y")

        self.hist = tk.Text(htext, bg=BG3, fg=DIM, font=_f(8),
                            bd=0, highlightthickness=0,
                            state="disabled", wrap="none",
                            selectbackground=BG3, cursor="arrow",
                            height=6,                # minimum 6 rows always visible
                            yscrollcommand=hscroll.set)
        self.hist.pack(side="left", fill="both", expand=True)
        hscroll.config(command=self.hist.yview)

        self.hist.tag_config("win",  foreground=GREEN)
        self.hist.tag_config("loss", foreground=RED)
        self.hist.tag_config("red",  foreground="#f85149")
        self.hist.tag_config("blk",  foreground="#8b949e")
        self.hist.tag_config("zer",  foreground=GREEN)
        self.hist.tag_config("dim",  foreground=MUT)

        self._update_btn_states()

    # ── Bet change → rescale dependent params ─────────────────────────────────
    def _on_bet_change(self, event=None):
        """When the base bet changes, auto-scale TP, SL and cum_sl to match.

        cum_sl = the equity level: (bet/$0.10) x per-unit (e.g. $0.10->$250,
        $0.20->$500 when the auto-base tier is $250).
        """
        try:
            bet = float(self.cfg_bet.get())
        except ValueError:
            return
        try:
            per_unit = float(self.cfg_autounit.get() or 250)
        except ValueError:
            per_unit = 250.0
        def _fmt(v): return f"{v:.2f}".rstrip("0").rstrip(".")
        self.cfg_tp.delete(0, "end");    self.cfg_tp.insert(0,    _fmt(bet * TP_PER_UNIT))
        self.cfg_sl.delete(0, "end");    self.cfg_sl.insert(0,    _fmt(bet * SL_PER_UNIT))
        self.cfg_cumsl.delete(0, "end"); self.cfg_cumsl.insert(0, _fmt((bet / 0.10) * per_unit))

    # ── Defaults ─────────────────────────────────────────────────────────────
    def _load_defaults(self):
        cfg = _load_cfg()
        strat = cfg.get("strategy", "CORNERTOP")
        if strat in STRATEGIES_LIST:
            self.cfg_strat.set(strat)
        else:
            self.cfg_strat.set(STRATEGIES_LIST[0])
        bet_str = f"{float(cfg.get('base_bet', 0.1)):.2f}"
        if bet_str not in BET_PRESETS:
            self.cfg_bet["values"] = BET_PRESETS + [bet_str]
        self.cfg_bet.set(bet_str)
        self.cfg_tp.insert(0,  str(cfg.get("tp",       20.0)))
        self.cfg_sl.insert(0,  str(cfg.get("sl",       24.40)))
        self.cfg_maxsess.insert(0, str(cfg.get("max_sessions", 0)))
        self.cfg_cumsl.insert(0,   str(cfg.get("cum_sl",       0.0)))
        self.cfg_dyn.set(bool(cfg.get("dynamic_base", False)))
        self.cfg_bank.insert(0,    str(cfg.get("safe_bankroll", 1000.0)))
        self.cfg_auto.set(bool(cfg.get("auto_base", False)))
        self.cfg_autounit.insert(0, str(cfg.get("auto_base_per_unit", 900.0)))

    # ── Bot control ───────────────────────────────────────────────────────────
    def _bot_running(self):
        return self._bot_proc is not None and self._bot_proc.poll() is None

    def _kill_stray_bots(self):
        """Kill any spinedge_bot process (incl. untracked/leftover) to avoid dupes."""
        try:
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
                  "Where-Object { $_.CommandLine -match 'spinedge_bot' } | ForEach-Object { $_.ProcessId }")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            for x in out.stdout.split():
                if x.strip().isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", x.strip()],
                                   capture_output=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass

    def _start_bot(self):
        if self._bot_running():
            return
        try:
            strat     = self.cfg_strat.get()
            base_bet  = float(self.cfg_bet.get())
            tp        = float(self.cfg_tp.get())
            sl        = float(self.cfg_sl.get())
            max_sess  = int(float(self.cfg_maxsess.get() or 0))
            cum_sl    = float(self.cfg_cumsl.get() or 0)
            dyn_base  = bool(self.cfg_dyn.get())
            safe_bank = float(self.cfg_bank.get() or 1000)
            auto_base = bool(self.cfg_auto.get())
            auto_unit = float(self.cfg_autounit.get() or 200)
        except ValueError:
            self.badge.config(text="BAD CONFIG", fg=RED)
            return

        # Persist the safety limits so they survive restarts
        cfg = _load_cfg()
        cfg.update({"strategy": strat, "base_bet": base_bet, "tp": tp, "sl": sl,
                    "max_sessions": max_sess, "cum_sl": cum_sl,
                    "dynamic_base": dyn_base, "safe_bankroll": safe_bank,
                    "auto_base": auto_base, "auto_base_per_unit": auto_unit})
        _save_cfg(cfg)

        # Kill any leftover/duplicate bot before launching a fresh one
        self._kill_stray_bots()

        _write_cmd("run")
        self._paused = False

        self._bot_proc = subprocess.Popen(
            [PYTHON, os.path.join(_HERE, "spinedge_bot.py"),
             "--auto", "--no-overlay",
             "--strategy", strat,
             "--base-bet", str(base_bet),
             "--tp",  str(tp),
             "--sl",  str(sl),
             "--max-sessions", str(max_sess),
             "--cum-sl", str(cum_sl)],
            cwd=_HERE,
        )
        self._update_btn_states()

    def _toggle_pause(self):
        if not self._bot_running():
            return
        self._paused = not self._paused
        _write_cmd("pause" if self._paused else "run")
        self.btn_pause.config(
            text="▶  RESUME" if self._paused else "⏸  PAUSE",
            fg=GREEN if self._paused else YELLOW,
            bg="#1a3a1a" if self._paused else "#3a3010",
        )

    def _stop_bot(self):
        _write_cmd("stop")
        if self._bot_running():
            self._bot_proc.terminate()
            try:
                self._bot_proc.wait(timeout=3)
            except Exception:
                self._bot_proc.kill()
        self._bot_proc = None
        self._paused   = False
        self.btn_pause.config(text="⏸  PAUSE", fg=YELLOW, bg="#3a3010")
        self._update_btn_states()

    def _update_btn_states(self):
        running = self._bot_running()
        self.btn_start.config(state="disabled" if running else "normal",
                              fg=DIM if running else GREEN)
        self.btn_pause.config(state="normal" if running else "disabled",
                              fg=(GREEN if self._paused else YELLOW) if running else MUT)
        self.btn_stop.config(state="normal" if running else "disabled",
                             fg=RED if running else MUT)

    # ── Overlay control ───────────────────────────────────────────────────────
    def _overlay_running(self):
        return self._overlay_proc is not None and self._overlay_proc.poll() is None

    def _toggle_overlay(self):
        if self._overlay_running():
            self._overlay_proc.terminate()
            try:
                self._overlay_proc.wait(timeout=2)
            except Exception:
                self._overlay_proc.kill()
            self._overlay_proc = None
            self.btn_overlay.config(text="⬚  Overlay: OFF", fg=DIM,  bg=BG3)
        else:
            overlay_path = os.path.join(_HERE, "overlay_live.py")
            if not os.path.exists(overlay_path):
                self.btn_overlay.config(text="⬚  overlay_live.py not found", fg=RED)
                return
            strat = self.cfg_strat.get() or "S1"
            self._overlay_proc = subprocess.Popen(
                [PYTHON, overlay_path, strat],
                cwd=_HERE,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.btn_overlay.config(text="⬚  Overlay: ON", fg=BLUE, bg="#0d1f2d")

    # ── Refresh loop ─────────────────────────────────────────────────────────
    def _refresh(self):
        # Poll subprocess state
        if self._bot_proc and self._bot_proc.poll() is not None:
            self._bot_proc = None
            self._paused   = False
            self.btn_pause.config(text="⏸  PAUSE", fg=YELLOW, bg="#3a3010")

        self._update_btn_states()

        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                self._update_stats(json.load(f))
        except Exception:
            pass

        self.after(REFRESH, self._refresh)

    # ── Stats display ─────────────────────────────────────────────────────────
    def _draw_equity(self, history, initial_balance, tp, sl):
        c = self.eq_canvas
        c.update_idletasks()
        W = c.winfo_width()
        W = W if W > 20 else 252
        H = 80
        c.config(width=W, height=H)
        c.delete("all")
        PAD_L, PAD_R, PAD_T, PAD_B = 4, 4, 6, 6

        # Build balance series: start at initial, then each round's balance
        balances = [initial_balance] + [h.get("balance", initial_balance) for h in history]
        if len(balances) < 2:
            c.create_text(W // 2, H // 2, text="no data", fill=MUT, font=_f(7))
            return

        b_min = min(balances)
        b_max = max(balances)
        tp_line = initial_balance + tp
        sl_line = initial_balance - sl
        b_min = min(b_min, sl_line)
        b_max = max(b_max, tp_line)
        span = b_max - b_min or 1.0

        def xt(i):
            return PAD_L + (i / (len(balances) - 1)) * (W - PAD_L - PAD_R)

        def yt(b):
            return PAD_T + (1 - (b - b_min) / span) * (H - PAD_T - PAD_B)

        # TP / SL / baseline dashed lines
        for level, color in ((tp_line, "#1a4a2a"), (sl_line, "#4a1a1a"), (initial_balance, "#2a2f3a")):
            y = yt(level)
            for x0 in range(0, W, 6):
                c.create_line(x0, y, min(x0 + 3, W), y, fill=color, width=1)

        # Equity line — segment color: green if above initial, red if below
        for i in range(len(balances) - 1):
            x0, y0 = xt(i),     yt(balances[i])
            x1, y1 = xt(i + 1), yt(balances[i + 1])
            above = (balances[i] + balances[i + 1]) / 2 >= initial_balance
            c.create_line(x0, y0, x1, y1, fill=GREEN if above else RED, width=1)

        # Current balance dot
        xi = xt(len(balances) - 1)
        yi = yt(balances[-1])
        cur_col = GREEN if balances[-1] >= initial_balance else RED
        c.create_oval(xi - 3, yi - 3, xi + 3, yi + 3, fill=cur_col, outline="")

    @staticmethod
    def _fmt_num(v):
        return f"{float(v):.2f}".rstrip("0").rstrip(".")

    def _sync_entry(self, entry, value):
        """Set an Entry to `value` only if different (avoids flicker/cursor jump)."""
        if value is None:
            return
        want = self._fmt_num(value)
        if entry.get() != want:
            entry.delete(0, "end")
            entry.insert(0, want)

    def _sync_combo(self, combo, value):
        if value is None:
            return
        want = f"{float(value):.2f}"
        if combo.get() != want:
            if want not in combo["values"]:
                combo["values"] = list(combo["values"]) + [want]
            combo.set(want)

    def _update_stats(self, s):
        phase = s.get("state", "WAITING")
        pc    = PHASE_FG.get(phase, DIM)

        # If bot process not running, show last known phase as dim
        if not self._bot_running() and phase not in ("TP_HIT", "SL_HIT"):
            phase = "STOPPED"
            pc    = MUT

        self.badge.config(text=phase, fg=pc)

        # Strategy name
        strat_name = s.get("strategy", "—")
        if phase == "TP_HIT":   strat_name += "  ★ TP"
        elif phase == "SL_HIT": strat_name += "  ✗ SL"

        # Balance
        bal  = s.get("balance", 0.0)
        init = s.get("initial_balance", bal)
        net  = s.get("net", 0.0)
        tp   = s.get("tp",  20.0)
        sl   = s.get("sl",  24.40)

        self.b_val.config(text=f"${bal:,.2f}")
        nc  = GREEN if net >= 0 else RED
        sgn = "+" if net >= 0 else ""
        pct = (net / init * 100) if init else 0.0
        self.b_net.config(text=f"Net  {sgn}${net:.2f}", fg=nc)
        self.b_pct.config(text=f"({sgn}{pct:.1f}%)",    fg=nc)
        self.b_tp.config(text=f"TP +${tp:.2f}")
        self.b_sl.config(text=f"SL -${sl:.2f}")

        # While running, mirror the LIVE params into the config fields so the UI
        # shows exactly what the bot is using (updates when auto-base rescales).
        if self._bot_running():
            self._sync_combo(self.cfg_bet,   s.get("base_bet"))
            self._sync_entry(self.cfg_tp,    tp)
            self._sync_entry(self.cfg_sl,    sl)
            self._sync_entry(self.cfg_cumsl, s.get("cum_sl"))

        # Progress bar
        total   = tp + sl
        frac    = max(0.0, min(1.0, (net + sl) / total))
        bw      = self.pb.winfo_width() or (WIN_W - 20)
        fill_px = int(bw * frac)
        bar_c   = GREEN if frac > 0.55 else (YELLOW if frac > 0.3 else RED)
        self.pb.delete("all")
        self.pb.create_rectangle(0, 0, bw,      8, fill="#2d1515", outline="")
        self.pb.create_rectangle(0, 0, fill_px, 8, fill=bar_c,     outline="")
        zero_x = int(bw * sl / total)
        self.pb.create_line(zero_x, 0, zero_x, 8, fill=WHITE, width=1)
        self.pb.create_line(bw-1,   0, bw-1,   8, fill=GREEN, width=1)

        # Round / phase
        rounds = s.get("rounds", 0)
        self.r_num.config(text=f"ROUND {rounds}")
        self.r_phase.config(text=phase, fg=pc)

        num   = s.get("last_number")
        color = s.get("last_color", "")
        lpnl  = s.get("last_pnl", 0.0)
        if num is not None:
            sgn2 = "+" if lpnl >= 0 else ""
            self.r_last.config(
                text=f"Last: {num} {color.upper():<5}   {sgn2}${lpnl:.2f}",
                fg=GREEN if lpnl >= 0 else RED,
            )
        pos      = s.get("positions", [])
        base_bet = s.get("base_bet", 0.10)
        n_pos    = len(pos) or 1
        self.r_bet.config(
            text=f"Bet: ${base_bet:.2f} × {n_pos} = ${base_bet * n_pos:.2f}/round"
        )
        fib_idx  = s.get("fib_idx",  0)
        fib_mult = s.get("fib_mult", 1)
        fib_bet  = s.get("fib_bet",  base_bet)
        self.r_fib.config(
            text=f"Fib: L{fib_idx} ×{fib_mult}  (${fib_bet:.2f}/pos)",
            fg=RED if fib_idx > 0 else DIM,
        )

        # Stats
        wins   = s.get("wins", 0)
        losses = s.get("losses", 0)
        total_wl = wins + losses
        wr = wins / total_wl * 100 if total_wl else 0.0
        self.st_wl.config(
            text=f"W: {wins:<4}  L: {losses:<4}  ({wr:.1f}%)",
            fg=GREEN if wins > losses else (RED if losses > wins else FG),
        )
        ws  = s.get("win_streak",  0)
        ls  = s.get("loss_streak", 0)
        bw_ = s.get("best_win",    0)
        bl_ = s.get("best_loss",   0)
        self.st_ws.config(
            text=f"Win   streak  {ws} cur  /  {bw_} best",
            fg=GREEN if ws > 1 else DIM,
        )
        self.st_ls.config(
            text=f"Loss  streak  {ls} cur  /  {bl_} best",
            fg=RED if ls > 2 else DIM,
        )
        tp_c = s.get("tp_count", 0)
        sl_c = s.get("sl_count", 0)
        self.st_sess.config(
            text=f"Sessions: {tp_c + sl_c}  (TP: {tp_c}  SL: {sl_c})"
        )

        # Full persistent history — used for both the scrollable list and equity
        try:
            with open(HISTORY_FILE, encoding="utf-8") as _hf:
                full_history = json.load(_hf)
        except Exception:
            full_history = s.get("history", [])

        # History list (most recent on top, all rounds, scrollable).
        # Only re-render when the row count changes so the user can scroll
        # freely without the 500 ms refresh yanking the view back.
        if len(full_history) != self._hist_len:
            self._hist_len = len(full_history)
            yv = self.hist.yview()          # preserve scroll position
            self.hist.config(state="normal")
            self.hist.delete("1.0", "end")
            for h in reversed(full_history):
                rnd  = h.get("round",  "?")
                n    = h.get("number", "?")
                c    = h.get("color",  "")
                won  = h.get("won",    False)
                pnl  = h.get("pnl",   0.0)
                sgn3 = "+" if pnl >= 0 else ""
                ctag = "red" if c == "red" else ("zer" if c == "green" else "blk")
                otag = "win" if won else "loss"
                self.hist.insert("end", f"  #{rnd:<3} ", "dim")
                self.hist.insert("end", f"{str(n):>2}", ctag)
                self.hist.insert("end", f" {c[:3].upper():<3}", ctag)
                self.hist.insert("end", f"  {'WIN ' if won else 'LOSS'}", otag)
                self.hist.insert("end", f"  {sgn3}${pnl:.2f}\n", otag)
            self.hist.config(state="disabled")
            self.hist.yview_moveto(yv[0])

        # Equity curve — full persistent history for all-time curve
        init_bal = s.get("initial_balance", s.get("balance", 0.0))
        self._draw_equity(full_history,
                          init_bal,
                          s.get("tp", 20.0),
                          s.get("sl", 24.4))

        # Footer
        self.ft_ts.config(text=s.get("timestamp", time.strftime("%H:%M:%S")))
        self.ft_info.config(text=f"${base_bet:.2f}×{n_pos}  |  rnd {rounds}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--x", type=int, default=20)
    ap.add_argument("--y", type=int, default=60)
    args, _ = ap.parse_known_args()
    Panel(x=args.x, y=args.y).mainloop()
