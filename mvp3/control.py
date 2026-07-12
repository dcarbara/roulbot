"""
control.py — SpinEdge Control Panel

Usage:
    python control.py

Manages overlay and bot as background processes.
Runs backtests inline.
"""

import sys, io, os, time, json, subprocess, glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE   = os.path.dirname(os.path.abspath(__file__))
PYTHON  = sys.executable

# Prefer the shared venv if it exists
_VENV_PY = os.path.normpath(os.path.join(_HERE, "..", "venv", "Scripts", "python.exe"))
if os.path.exists(_VENV_PY):
    PYTHON = _VENV_PY

CONFIG_FILE = os.path.join(_HERE, "bot_config.json")

DEFAULT_CFG = {
    "strategy": "S2",
    "base_bet": 0.50,
    "tp":       64.0,
    "sl":       55.0,
}

STRATEGIES = {
    "S1": {"name": "Aggressive",   "desc": "col1+col3+1st12+red+ds1",        "tp": 64.0,  "sl": 55.0},
    "S2": {"name": "Moderate",     "desc": "col1+1st12+3rd12+odd+ds1",        "tp": 64.0,  "sl": 119.0},
    "S3": {"name": "Conservative", "desc": "red+odd+1-18+19-36+ds1+ds25",     "tp": 31.0,  "sl": 51.0},
}

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

def load_cfg():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            c = json.load(f)
        for k, v in DEFAULT_CFG.items():
            c.setdefault(k, v)
        return c
    except Exception:
        return dict(DEFAULT_CFG)

def save_cfg(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"  [WARN] Could not save config: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Process management
# ─────────────────────────────────────────────────────────────────────────────

_procs = {"overlay": None, "bot": None}

def is_running(key):
    p = _procs[key]
    return p is not None and p.poll() is None

def _pid(key):
    p = _procs[key]
    return p.pid if p and p.poll() is None else None

def start_overlay(strat_key="S1"):
    if is_running("overlay"):
        return False, "already running"
    path = os.path.join(_HERE, "overlay_live.py")
    if not os.path.exists(path):
        return False, "overlay_live.py not found"
    _procs["overlay"] = subprocess.Popen(
        [PYTHON, path, strat_key],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True, f"PID {_procs['overlay'].pid}"

def stop_overlay():
    if not is_running("overlay"):
        return False, "not running"
    _procs["overlay"].terminate()
    _procs["overlay"] = None
    return True, "stopped"

def start_bot(cfg, dry_run=False):
    if is_running("bot"):
        return False, "already running"
    path = os.path.join(_HERE, "spinedge_bot.py")
    cmd  = [
        PYTHON, path,
        "--auto", "--no-overlay",
        "--strategy", cfg["strategy"],
        "--base-bet", str(cfg["base_bet"]),
        "--tp",        str(cfg["tp"]),
        "--sl",        str(cfg["sl"]),
    ]
    if dry_run:
        cmd.append("--dry-run")
    _procs["bot"] = subprocess.Popen(
        cmd, creationflags=subprocess.CREATE_NEW_CONSOLE
    )
    return True, f"PID {_procs['bot'].pid}"

def stop_bot():
    if not is_running("bot"):
        return False, "not running"
    _procs["bot"].terminate()
    _procs["bot"] = None
    return True, "stopped"

# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _status_tag(key):
    if is_running(key):
        return f"RUNNING  (PID {_pid(key)})"
    return "STOPPED"

def _clr():
    os.system("cls" if os.name == "nt" else "clear")

def hr(c="─", n=60):
    print("  " + c * n)

def ask(prompt, default, cast=str):
    raw = input(f"  {prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except Exception:
        print(f"    Invalid — keeping: {default}")
        return default

def pause(msg="  Press Enter to continue..."):
    input(msg)

# ─────────────────────────────────────────────────────────────────────────────
# Backtest presets helper
# ─────────────────────────────────────────────────────────────────────────────

def list_presets():
    presets = []
    for f in glob.glob(os.path.join(_HERE, "config", "**", "*.json"), recursive=True):
        try:
            d = json.load(open(f, encoding="utf-8"))
            name = d.get("name") or os.path.splitext(os.path.basename(f))[0]
            presets.append(name)
        except Exception:
            pass
    return sorted(set(presets))

# ─────────────────────────────────────────────────────────────────────────────
# Menus
# ─────────────────────────────────────────────────────────────────────────────

def menu_main():
    while True:
        cfg = load_cfg()
        s   = STRATEGIES.get(cfg["strategy"], {})
        _clr()
        print()
        hr("=")
        print("  SpinEdge Control Panel")
        hr("=")
        print(f"  Overlay  :  {_status_tag('overlay')}")
        print(f"  Bot      :  {_status_tag('bot')}")
        hr()
        print(f"  Config   :  {cfg['strategy']} ({s.get('name','?')}) | "
              f"${cfg['base_bet']:.2f}/pos | TP +${cfg['tp']:.2f} | SL -${cfg['sl']:.2f}")
        hr("=")
        print()
        print("  [1]  Overlay     —  toggle on/off")
        print("  [2]  Bot         —  configure & start/stop")
        print("  [3]  Status      —  process details")
        print("  [4]  Backtest    —  run strategy simulation")
        print()
        print("  [Q]  Quit")
        print()
        hr("=")
        ch = input("  > ").strip().lower()

        if   ch == "1": menu_overlay(cfg)
        elif ch == "2": menu_bot(cfg)
        elif ch == "3": menu_status()
        elif ch == "4": menu_backtest()
        elif ch == "q":
            _shutdown()
            break


def menu_overlay(cfg):
    _clr()
    print()
    hr("=")
    print("  OVERLAY")
    hr("=")
    print(f"  Status : {_status_tag('overlay')}")
    print()

    if is_running("overlay"):
        print("  [1]  Stop overlay")
        print("  [B]  Back")
        print()
        ch = input("  > ").strip().lower()
        if ch == "1":
            ok, msg = stop_overlay()
            print(f"  {'OK' if ok else 'ERR'}: {msg}")
            pause()
    else:
        strat = cfg["strategy"]
        print(f"  Will launch with strategy: {strat}")
        print()
        print("  [1]  Start overlay")
        print("  [B]  Back")
        print()
        ch = input("  > ").strip().lower()
        if ch == "1":
            ok, msg = start_overlay(strat)
            print(f"  {'OK' if ok else 'ERR'}: {msg}")
            if ok:
                time.sleep(2)
            pause()


def menu_bot(cfg):
    while True:
        _clr()
        s = STRATEGIES.get(cfg["strategy"], {})
        print()
        hr("=")
        print("  BOT CONFIGURATION")
        hr("=")
        print(f"  Status   : {_status_tag('bot')}")
        hr()
        print(f"  [1]  Strategy   : {cfg['strategy']} — {s.get('name','')}  ({s.get('desc','')})")
        print(f"  [2]  Base bet   : ${cfg['base_bet']:.2f} / position")
        print(f"  [3]  Take Profit: +${cfg['tp']:.2f}")
        print(f"  [4]  Stop Loss  : -${cfg['sl']:.2f}")
        hr()

        if is_running("bot"):
            print("  [S]  STOP bot")
        else:
            print("  [S]  START bot")
            print("  [D]  START bot (dry-run)")

        print("  [B]  Back")
        hr("=")
        print()
        ch = input("  > ").strip().lower()

        if ch == "1":
            cfg = _change_strategy(cfg)
        elif ch == "2":
            cfg = _change_base_bet(cfg)
        elif ch == "3":
            cfg = _change_tp(cfg)
        elif ch == "4":
            cfg = _change_sl(cfg)
        elif ch == "s":
            if is_running("bot"):
                ok, msg = stop_bot()
                print(f"  {'OK' if ok else 'ERR'}: {msg}")
            else:
                ok, msg = start_bot(cfg, dry_run=False)
                print(f"  {'OK' if ok else 'ERR'}: {msg}")
                if ok:
                    print("  Bot started in separate window.")
            pause()
        elif ch == "d" and not is_running("bot"):
            ok, msg = start_bot(cfg, dry_run=True)
            print(f"  {'OK' if ok else 'ERR'}: {msg} [DRY-RUN]")
            pause()
        elif ch == "b":
            break


def _change_strategy(cfg):
    print()
    print("  Available strategies:")
    for k, s in STRATEGIES.items():
        print(f"    {k} — {s['name']:13s}  {s['desc']}")
        print(f"         strat.md defaults: TP +${s['tp']:.0f}  SL -${s['sl']:.0f}  base $0.50/pos")
    print()
    val = ask("Strategy (S1/S2/S3)", cfg["strategy"]).upper()
    if val in STRATEGIES:
        cfg["strategy"] = val
        # Suggest strat.md defaults
        s = STRATEGIES[val]
        print(f"  Suggested TP/SL from strat.md: TP +${s['tp']:.0f} / SL -${s['sl']:.0f}")
        apply = input("  Apply strat.md TP/SL defaults? [y/N]: ").strip().lower()
        if apply == "y":
            cfg["tp"] = s["tp"]
            cfg["sl"] = s["sl"]
    else:
        print("  Invalid — keeping current.")
    save_cfg(cfg)
    return cfg

def _change_base_bet(cfg):
    print()
    chips = [0.10, 0.50, 1.0, 5.0, 25.0, 100.0]
    print(f"  Available chips: {', '.join(f'${d}' for d in chips)}")
    val = ask("Base bet per position ($)", cfg["base_bet"], float)
    if val > 0:
        cfg["base_bet"] = val
        save_cfg(cfg)
    else:
        print("  Must be > 0 — keeping current.")
    return cfg

def _change_tp(cfg):
    print()
    val = ask("Take Profit — stop when net gain >= ($)", cfg["tp"], float)
    if val > 0:
        cfg["tp"] = val
        save_cfg(cfg)
    return cfg

def _change_sl(cfg):
    print()
    val = ask("Stop Loss — stop when net loss >= ($)", cfg["sl"], float)
    if val > 0:
        cfg["sl"] = val
        save_cfg(cfg)
    return cfg


def menu_status():
    _clr()
    print()
    hr("=")
    print("  STATUS")
    hr("=")

    cfg = load_cfg()
    s   = STRATEGIES.get(cfg["strategy"], {})

    print(f"  Overlay  : {_status_tag('overlay')}")
    print(f"  Bot      : {_status_tag('bot')}")
    hr()
    print(f"  Strategy : {cfg['strategy']} — {s.get('name','?')}")
    print(f"  Positions: {s.get('desc','?')}")
    print(f"  Base bet : ${cfg['base_bet']:.2f} / position")
    print(f"  TP       : +${cfg['tp']:.2f}")
    print(f"  SL       : -${cfg['sl']:.2f}")
    hr()
    print(f"  Config   : {CONFIG_FILE}")
    hr("=")
    print()
    pause()


def menu_backtest():
    _clr()
    print()
    hr("=")
    print("  BACKTEST")
    hr("=")

    # Show available preset names
    presets = list_presets()
    if presets:
        print(f"  Available presets: {', '.join(presets[:12])}")
        if len(presets) > 12:
            print(f"                     ... and {len(presets)-12} more")
    print()

    # ── Inputs ────────────────────────────────────────────────────────────────
    strategy   = ask("Strategy name", "conservative")
    prog_opts  = "flat / fibonacci / martingale / dalembert"
    progression = ask(f"Progression ({prog_opts})", "flat")
    base_bet   = ask("Base bet ($)", 1.0, float)
    sessions   = ask("Sessions", 10, int)
    rounds     = ask("Rounds per session", 100, int)
    balance    = ask("Initial balance ($)", 100.0, float)
    tp_val     = ask("Take Profit ($, 0=disabled)", 50.0, float)
    sl_val     = ask("Stop Loss ($)", 50.0, float)
    max_bet    = ask("Max bet cap ($, 0=none)", 0.0, float)

    print()
    hr()
    print(f"  Running: {strategy} | {progression} | ${base_bet}/bet | "
          f"{sessions} sessions × {rounds} rounds")
    hr()
    print()

    try:
        from core.backtesting_runner import run_campaign, default_config, validate_config
    except ImportError as e:
        print(f"  [ERROR] Cannot import backtest engine: {e}")
        pause()
        return

    raw_cfg = default_config()
    raw_cfg.update({
        "strategy_name":          strategy,
        "progression_type":       progression,
        "base_bet":               base_bet,
        "sims":                   sessions,
        "rounds":                 rounds,
        "initial_balance":        balance,
        "max_loss":               sl_val,
        "max_bet":                max_bet,
        "enable_profit_target":   tp_val > 0,
        "profit_target":          tp_val,
        "sim_mode":               "sequential",
        "historical_data_source": "db",
        "session_duration_minutes": 9999,
    })

    try:
        cfg_v = validate_config(raw_cfg)
    except ValueError as e:
        print(f"  [ERROR] Invalid config: {e}")
        pause()
        return

    t0 = time.monotonic()
    logs = []
    def _log(msg):
        logs.append(msg)
        print(f"  {msg}")

    try:
        res = run_campaign(cfg_v, on_log=_log)
    except Exception as e:
        print(f"\n  [ERROR] Backtest failed: {e}")
        pause()
        return

    elapsed = time.monotonic() - t0

    # ── Results ───────────────────────────────────────────────────────────────
    print()
    hr("=")
    print("  RESULTS")
    hr("=")
    print(f"  Strategy     : {strategy}  ({progression})")
    print(f"  Sessions run : {res.sessions_run}  |  Total rounds: {res.total_rounds}")
    print(f"  Balance      : ${res.initial_balance:.2f} -> ${res.final_balance:.2f}")
    sign = "+" if res.campaign_pnl >= 0 else ""
    print(f"  Campaign P&L : {sign}${res.campaign_pnl:.2f}")
    print(f"  Stop reason  : {res.stop_reason}")

    if res.sessions:
        print()
        print(f"  {'Sess':>4}  {'Start':>8}  {'End':>8}  {'P&L':>8}  "
              f"{'Rounds':>6}  {'Wins':>5}  {'Losses':>6}")
        hr()
        for i, s in enumerate(res.sessions, 1):
            pnl = s.final_balance - s.initial_balance
            sign = "+" if pnl >= 0 else ""
            print(f"  {i:>4}  ${s.initial_balance:>7.2f}  ${s.final_balance:>7.2f}  "
                  f"{sign}${abs(pnl):>6.2f}  {s.total_rounds:>6}  "
                  f"{s.total_wins:>5}  {s.total_losses:>6}")

    hr("=")
    print(f"  Elapsed: {elapsed:.2f}s")
    print()

    # Offer to save results
    save = input("  Save results to JSON? [y/N]: ").strip().lower()
    if save == "y":
        out = os.path.join(_HERE, f"backtest_{strategy}_{int(time.time())}.json")
        try:
            with open(out, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"config": cfg_v, "result": {
                    "sessions_run":  res.sessions_run,
                    "total_rounds":  res.total_rounds,
                    "campaign_pnl":  res.campaign_pnl,
                    "initial_balance": res.initial_balance,
                    "final_balance": res.final_balance,
                    "stop_reason":   res.stop_reason,
                }}, f, indent=2)
            print(f"  Saved: {out}")
        except Exception as e:
            print(f"  [WARN] Save failed: {e}")

    pause()


def _shutdown():
    print()
    if is_running("overlay"):
        print("  Stopping overlay...")
        stop_overlay()
    if is_running("bot"):
        print("  Stopping bot...")
        stop_bot()
    print("  Bye.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        menu_main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
        _shutdown()
