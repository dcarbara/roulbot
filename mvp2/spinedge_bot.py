"""
spinedge_bot.py  —  SpinEdge automated betting CLI

Starts the overlay, prompts for session settings (strategy / base bet /
take-profit / stop-loss), then watches the game and places bets
automatically.  Tracks every round: balance, P&L, W/L ratio, streaks.

Usage:
    python spinedge_bot.py              # interactive prompts (recommended)
    python spinedge_bot.py --dry-run    # log only, no real clicks

Emergency stop: Ctrl+C  OR  move mouse to top-left corner.
"""

import sys, io, os, re, time, json, subprocess
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import cv2, numpy as np, mss, pyautogui
from PIL import Image
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.0

_HERE       = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(_HERE, "coords.json")
CONFIG_FILE = os.path.join(_HERE, "bot_config.json")
PYTHON      = sys.executable

# ─────────────────────────────────────────────────────────────────────────────
# Roulette knowledge
# ─────────────────────────────────────────────────────────────────────────────
RED_NUMS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
COL1     = {1,4,7,10,13,16,19,22,25,28,31,34}
COL2     = {2,5,8,11,14,17,20,23,26,29,32,35}
COL3     = {3,6,9,12,15,18,21,24,27,30,33,36}

BET_COVERS = {
    "col1_btn": lambda n: n in COL1,
    "col2_btn": lambda n: n in COL2,
    "col3_btn": lambda n: n in COL3,
    "1st12":    lambda n: 1  <= n <= 12,
    "2nd12":    lambda n: 13 <= n <= 24,
    "3rd12":    lambda n: 25 <= n <= 36,
    "red":      lambda n: n in RED_NUMS,
    "black":    lambda n: n not in RED_NUMS and n != 0,
    "even":     lambda n: n != 0 and n % 2 == 0,
    "odd":      lambda n: n % 2 == 1,
    "1-18":     lambda n: 1  <= n <= 18,
    "19-36":    lambda n: 19 <= n <= 36,
    "ds1":      lambda n: 1  <= n <= 6,
    "ds7":      lambda n: 7  <= n <= 12,
    "ds13":     lambda n: 13 <= n <= 18,
    "ds19":     lambda n: 19 <= n <= 24,
    "ds25":     lambda n: 25 <= n <= 30,
    "ds31":     lambda n: 31 <= n <= 36,
}

BET_PAYOUT = {            # net payout per $1 bet (excluding stake)
    "col1_btn":2, "col2_btn":2, "col3_btn":2,
    "1st12":2, "2nd12":2, "3rd12":2,
    "ds1":5, "ds7":5, "ds13":5, "ds19":5, "ds25":5, "ds31":5,
    "red":1, "black":1, "even":1, "odd":1, "1-18":1, "19-36":1,
}

CHIP_DENOMS = [0.10, 0.50, 1.0, 5.0, 25.0, 100.0]
CHIP_KEYS   = {0.10:"chip_0.10", 0.50:"chip_0.50", 1.0:"chip_1",
               5.0:"chip_5", 25.0:"chip_25", 100.0:"chip_100"}

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────
STRATEGIES = {
    "S1": {
        "name":      "Aggressive",
        "positions": ["col1_btn","col3_btn","1st12","red","ds1"],
        "desc":      "Col1 + Col3 + 1st Dozen + Red + DS1-6",
    },
    "S2": {
        "name":      "Moderate",
        "positions": ["col1_btn","1st12","3rd12","odd","ds1"],
        "desc":      "Col1 + 1st Dozen + 3rd Dozen + Odd + DS1-6",
    },
    "S3": {
        "name":      "Conservative",
        "positions": ["red","odd","1-18","19-36","ds1","ds25"],
        "desc":      "Red + Odd + 1-18 + 19-36 + DS1-6 + DS25-30",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Config persistence  (last-used values become next-run defaults)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "strategy":  "S1",
    "base_bet":  1.0,
    "tp":        50.0,
    "sl":        20.0,
}

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            c = json.load(f)
        # Fill missing keys with defaults
        for k, v in DEFAULT_CONFIG.items():
            c.setdefault(k, v)
        return c
    except Exception:
        return dict(DEFAULT_CONFIG)

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"  [WARN] Could not save config: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Coords helpers
# ─────────────────────────────────────────────────────────────────────────────
_coords_cache = {"data": None, "mtime": 0.0}

def load_coords():
    try:
        mt = os.path.getmtime(COORDS_FILE)
        if mt != _coords_cache["mtime"]:
            with open(COORDS_FILE, encoding="utf-8") as f:
                _coords_cache["data"] = json.load(f)
            _coords_cache["mtime"] = mt
    except Exception as e:
        print(f"  [WARN] coords: {e}")
    return _coords_cache.get("data") or {}

def get_scale(coords):
    meta = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    with mss.mss() as sct:
        mon = sct.monitors[1]
    return mon["width"] / sw, mon["height"] / sh

def validate_strategy(key, base_bet, coords):
    """Return list of missing coord keys (empty = all OK)."""
    chip_key = best_chip_key(base_bet)
    needed   = STRATEGIES[key]["positions"] + [chip_key]
    return [k for k in needed
            if k not in coords
            or not isinstance(coords[k], (list, tuple))
            or len(coords[k]) != 2]

# ─────────────────────────────────────────────────────────────────────────────
# Chip selection
# ─────────────────────────────────────────────────────────────────────────────
def best_chip_key(base_bet):
    """Pick the chip denomination that exactly matches or is nearest below."""
    exact = CHIP_KEYS.get(float(base_bet))
    if exact:
        return exact
    # nearest denomination <= base_bet
    below = [d for d in CHIP_DENOMS if d <= base_bet]
    if below:
        return CHIP_KEYS[max(below)]
    return CHIP_KEYS[CHIP_DENOMS[0]]   # fallback to smallest

# ─────────────────────────────────────────────────────────────────────────────
# Status detection  (bg-cached, OCR throttled)
# ─────────────────────────────────────────────────────────────────────────────
def _bg_color(img):
    H, W = img.shape[:2]
    s = img[:, :max(1, W//2)]
    b, g, r = float(s[:,:,0].mean()), float(s[:,:,1].mean()), float(s[:,:,2].mean())
    if   g > 80 and g > r*1.3 and g > b*1.3: return "GREEN"
    elif r > 80 and r > g*1.3 and r > b*1.3: return "RED"
    elif r > 80 and g > 60    and r > b*1.5: return "YELLOW"
    else:                                      return "DARK"

def _run_ocr(img, bg):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big  = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    if bg in ("GREEN", "RED", "YELLOW"):
        _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, bw = cv2.threshold(big, 100, 255, cv2.THRESH_BINARY)
    txt = pytesseract.image_to_string(
        Image.fromarray(bw), config="--psm 7 --oem 3"
    ).strip().upper()
    return "" if bg == "DARK" and len(txt) < 4 else txt

class StatusReader:
    def __init__(self):
        self._bg, self._txt, self._t = None, "", 0.0

    def read(self, coords):
        sr = coords.get("_status_region")
        if not sr:
            return self._bg or "DARK", self._txt
        meta = coords.get("_meta", {})
        sw, sh = meta.get("image_w",1920), meta.get("image_h",1080)
        with mss.mss() as sct:
            mon  = sct.monitors[1]
            reg  = {
                "left":   int(sr["x"] * mon["width"]  / sw),
                "top":    int(sr["y"] * mon["height"] / sh),
                "width":  int(sr["w"] * mon["width"]  / sw),
                "height": int(sr["h"] * mon["height"] / sh),
            }
            shot = sct.grab(reg)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        bg  = _bg_color(img)
        now = time.time()
        if bg != self._bg or (now - self._t) >= 0.9:
            self._txt = _run_ocr(img, bg)
            self._t   = now
        self._bg = bg
        return bg, self._txt

# ─────────────────────────────────────────────────────────────────────────────
# Balance OCR
# ─────────────────────────────────────────────────────────────────────────────
def read_balance(coords):
    """Return balance as float, or None on failure."""
    r = coords.get("_balance_region")
    if not r:
        return None
    meta = coords.get("_meta", {})
    sw, sh = meta.get("image_w",1920), meta.get("image_h",1080)
    with mss.mss() as sct:
        mon  = sct.monitors[1]
        reg  = {
            "left":   int(r["x"] * mon["width"]  / sw),
            "top":    int(r["y"] * mon["height"] / sh),
            "width":  int(r["w"] * mon["width"]  / sw),
            "height": int(r["h"] * mon["height"] / sh),
        }
        shot = sct.grab(reg)
    img  = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big  = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(big, 120, 255, cv2.THRESH_BINARY)
    txt = pytesseract.image_to_string(
        Image.fromarray(bw),
        config="--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.$,"
    ).strip()
    m = re.search(r'\$?([\d,]+\.?\d*)', txt)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Result parsing and win-check
# ─────────────────────────────────────────────────────────────────────────────
def parse_result(txt):
    """'22 BLACK' → (22, 'black').  Returns (None, None) if no match."""
    m = re.search(r'\b(\d{1,2})\s+(BLACK|RED|GREEN)\b', txt)
    if m:
        return int(m.group(1)), m.group(2).lower()
    return None, None

def eval_round(number, positions, base_bet):
    """Return (won_positions, lost_positions, estimated_net)."""
    if number is None:
        return [], [], 0.0
    won, lost = [], []
    net = 0.0
    for pos in positions:
        checker = BET_COVERS.get(pos)
        if checker and checker(number):
            won.append(pos)
            net += base_bet * BET_PAYOUT.get(pos, 1)
        else:
            lost.append(pos)
            net -= base_bet
    return won, lost, net

# ─────────────────────────────────────────────────────────────────────────────
# Bet placement
# ─────────────────────────────────────────────────────────────────────────────
def _click(sx, sy, label="", dry_run=False):
    if dry_run:
        print(f"    [DRY] {label:16s} ({sx},{sy})")
        time.sleep(0.04)
        return
    pyautogui.moveTo(sx, sy, duration=0.10)
    time.sleep(0.04)
    pyautogui.click()
    print(f"    >> {label:16s} ({sx},{sy})")
    time.sleep(0.08)

def place_bets(strat_key, base_bet, coords, sx_m, sy_m, dry_run=False):
    chip_key  = best_chip_key(base_bet)
    positions = STRATEGIES[strat_key]["positions"]
    cx, cy    = coords[chip_key]
    _click(int(cx*sx_m), int(cy*sy_m), label=chip_key, dry_run=dry_run)
    time.sleep(0.12)
    placed = []
    for pos in positions:
        val = coords.get(pos)
        if isinstance(val, (list, tuple)) and len(val) == 2:
            _click(int(val[0]*sx_m), int(val[1]*sy_m), label=pos, dry_run=dry_run)
            placed.append(pos)
        else:
            print(f"    [SKIP] {pos}")
    return placed

# ─────────────────────────────────────────────────────────────────────────────
# Session stats
# ─────────────────────────────────────────────────────────────────────────────
class Session:
    def __init__(self, initial_balance, tp, sl, strat_key, base_bet):
        self.initial    = initial_balance
        self.balance    = initial_balance
        self.tp         = tp
        self.sl         = sl
        self.strat_key  = strat_key
        self.base_bet   = base_bet
        self.rounds     = 0
        self.wins       = 0
        self.losses     = 0
        self.net        = 0.0
        self.win_streak = 0
        self.loss_streak= 0
        self.best_win   = 0
        self.best_loss  = 0
        self.history    = []   # list of round dicts

    @property
    def win_ratio(self):
        total = self.wins + self.losses
        return self.wins / total * 100 if total else 0.0

    def record(self, won, number, positions, bal_after, est_net):
        self.rounds  += 1
        self.balance  = bal_after if bal_after is not None else self.balance + est_net
        self.net      = self.balance - self.initial
        if won:
            self.wins       += 1
            self.win_streak += 1
            self.loss_streak = 0
            self.best_win    = max(self.best_win, self.win_streak)
        else:
            self.losses      += 1
            self.loss_streak += 1
            self.win_streak   = 0
            self.best_loss    = max(self.best_loss, self.loss_streak)
        self.history.append({
            "round": self.rounds, "number": number,
            "won": won, "net": round(est_net, 2),
            "balance": round(self.balance, 2),
        })

    def tp_hit(self):
        return self.net >= self.tp

    def sl_hit(self):
        return self.net <= -self.sl

    def summary_lines(self):
        sign  = "+" if self.net >= 0 else ""
        ratio = f"{self.win_ratio:.1f}%"
        lines = [
            f"  Balance  : ${self.balance:.2f}  (start ${self.initial:.2f})",
            f"  Net P&L  : {sign}${self.net:.2f}",
            f"  Rounds   : {self.rounds}  |  W {self.wins} / L {self.losses}  ({ratio})",
            f"  Streak   : Win {self.win_streak} cur / {self.best_win} best   "
            f"Loss {self.loss_streak} cur / {self.best_loss} best",
            f"  TP/SL    : +${self.tp:.2f} / -${self.sl:.2f}",
        ]
        return lines

# ─────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────────────
def ask(prompt, default, cast=str):
    """Prompt with default; Enter = keep default."""
    raw = input(f"  {prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except Exception:
        print(f"    Invalid — using default: {default}")
        return default

def hr(char="-", n=62):
    print("  " + char * n)

def clear_line():
    print("\r" + " " * 80 + "\r", end="", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv

    print()
    hr("=")
    print("  SpinEdge Bot" + ("  [DRY-RUN]" if dry_run else ""))
    hr("=")
    print()

    # ── Load last-used config as defaults ─────────────────────────────────────
    cfg = load_config()

    # ── Load + validate coords ────────────────────────────────────────────────
    coords = load_coords()
    if not coords:
        print(f"  [ERROR] Cannot load {COORDS_FILE}")
        sys.exit(1)

    # ── Show strategies with coord check ─────────────────────────────────────
    print("  Strategies:")
    print()
    for key, s in STRATEGIES.items():
        missing = validate_strategy(key, cfg["base_bet"], coords)
        mark    = "[OK]  " if not missing else "[MISS]"
        print(f"  {mark} {key}: {s['name']:15s}  —  {s['desc']}")
        if missing:
            print(f"         Missing coords: {', '.join(missing)}")
    print()

    # ── Strategy ──────────────────────────────────────────────────────────────
    strat_key = None
    while strat_key not in STRATEGIES:
        strat_key = ask(f"Strategy (S1/S2/S3)", cfg["strategy"]).upper()
        if strat_key not in STRATEGIES:
            print(f"    Choose S1, S2, or S3.")
            strat_key = None

    # ── Base bet ──────────────────────────────────────────────────────────────
    print()
    print(f"  Available chips: {', '.join(f'${d}' for d in CHIP_DENOMS)}")
    base_bet = ask("Base bet per position ($)", cfg["base_bet"], float)
    while base_bet <= 0:
        print("    Must be > 0.")
        base_bet = ask("Base bet per position ($)", cfg["base_bet"], float)

    chip_key = best_chip_key(base_bet)
    n_pos    = len(STRATEGIES[strat_key]["positions"])
    print(f"    -> Using chip: {chip_key}  |  Total per round: ${base_bet * n_pos:.2f} ({n_pos} positions)")

    # ── TP / SL ───────────────────────────────────────────────────────────────
    print()
    tp = ask("Take Profit — stop when net gain reaches ($)", cfg["tp"], float)
    sl = ask("Stop Loss  — stop when net loss reaches  ($)", cfg["sl"], float)

    # ── Final coord validation for chosen strategy + chip ────────────────────
    missing = validate_strategy(strat_key, base_bet, coords)
    if missing:
        print(f"\n  [ERROR] Missing coords for {strat_key} with chip {chip_key}:")
        for m in missing:
            print(f"    - {m}")
        sys.exit(1)

    # ── Save config ───────────────────────────────────────────────────────────
    cfg.update({"strategy": strat_key, "base_bet": base_bet, "tp": tp, "sl": sl})
    save_config(cfg)

    # ── Confirm ───────────────────────────────────────────────────────────────
    print()
    hr()
    print(f"  Strategy : {strat_key}  ({STRATEGIES[strat_key]['name']})")
    print(f"  Chip     : {chip_key}  x{n_pos} positions  = ${base_bet*n_pos:.2f}/round")
    print(f"  Positions: {', '.join(STRATEGIES[strat_key]['positions'])}")
    print(f"  TP       : +${tp:.2f}")
    print(f"  SL       : -${sl:.2f}")
    hr()
    print()
    go = input("  Press Enter to start  (or 'q' to quit): ").strip().lower()
    if go == "q":
        print("  Aborted.")
        return

    # ── Launch overlay ────────────────────────────────────────────────────────
    overlay_path = os.path.join(_HERE, "overlay_live.py")
    overlay_proc = None
    if os.path.exists(overlay_path):
        print()
        print("  [*] Starting overlay...")
        overlay_proc = subprocess.Popen(
            [PYTHON, overlay_path, strat_key],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        time.sleep(3)
        print("  [*] Overlay running.")
    else:
        print("  [WARN] overlay_live.py not found — running without overlay")

    # ── Read initial balance ──────────────────────────────────────────────────
    print()
    print("  Reading initial balance...")
    coords       = load_coords()
    init_balance = None
    for _ in range(5):
        init_balance = read_balance(coords)
        if init_balance is not None:
            break
        time.sleep(1)
    if init_balance is None:
        print("  [WARN] Could not read balance — starting with $0.00")
        init_balance = 0.0
    print(f"  Initial balance: ${init_balance:.2f}")

    # ── Session + state machine ───────────────────────────────────────────────
    session  = Session(init_balance, tp, sl, strat_key, base_bet)
    reader   = StatusReader()
    sx_m, sy_m = get_scale(coords)

    WAITING  = "WAITING"
    PLACED   = "PLACED"
    RESULT   = "RESULT"
    state    = WAITING

    bal_before   = init_balance
    placed_pos   = []
    result_wait  = 0.0   # timestamp when we first saw DARK after PLACED

    print()
    hr("=")
    print(f"  Bot running  —  Ctrl+C or mouse to top-left to stop")
    hr("=")
    print()
    print(f"  {'Time':8s}  {'Rnd':4s}  {'State':8s}  {'BG':6s}  Status")
    hr()

    try:
        while True:
            coords     = load_coords()
            sx_m, sy_m = get_scale(coords)
            bg, txt    = reader.read(coords)
            ts         = time.strftime("%H:%M:%S")

            # ── WAITING: look for green betting phase ────────────────────────
            if state == WAITING:
                print(f"\r  {ts}  {'---':4s}  {state:8s}  {bg:6s}  {txt[:35]:35s}",
                      end="", flush=True)

                if bg == "GREEN" and "PLACE YOUR BETS" in txt:
                    clear_line()
                    print()
                    hr()
                    print(f"  ROUND {session.rounds+1}  —  {txt}")
                    hr()
                    print(f"  Placing bets: {', '.join(STRATEGIES[strat_key]['positions'])}")
                    bal_before  = read_balance(coords) or session.balance
                    placed_pos  = place_bets(strat_key, base_bet, coords, sx_m, sy_m, dry_run)
                    state       = PLACED
                    result_wait = 0.0
                    print(f"  Bets placed ({len(placed_pos)}).")
                    print()

            # ── PLACED: wait for result ──────────────────────────────────────
            elif state == PLACED:
                print(f"\r  {ts}  {str(session.rounds+1):4s}  {state:8s}  {bg:6s}  {txt[:35]:35s}",
                      end="", flush=True)

                # Detect result from winner text
                num, color = parse_result(txt)
                if num is not None:
                    state = RESULT

                # Failsafe: dark + quiet for 2s → assume round ended
                elif bg == "DARK" and txt == "":
                    if result_wait == 0.0:
                        result_wait = time.time()
                    elif time.time() - result_wait >= 2.0:
                        state = RESULT
                else:
                    result_wait = 0.0

            # ── RESULT: read balance, record stats, check TP/SL ─────────────
            elif state == RESULT:
                clear_line()

                # Give game 1.5s for balance to update
                time.sleep(1.5)
                coords   = load_coords()
                bal_after = read_balance(coords)

                num, color = parse_result(txt)
                won_pos, lost_pos, est_net = eval_round(num, placed_pos, base_bet)

                # Determine win/loss: prefer real balance diff, fall back to logic
                if bal_after is not None and bal_before is not None:
                    actual_net = bal_after - bal_before
                    won        = actual_net > 0
                else:
                    bal_after  = session.balance + est_net
                    actual_net = est_net
                    won        = len(won_pos) > 0

                session.record(won, num, placed_pos, bal_after, actual_net)

                # ── Print round summary ──────────────────────────────────────
                print()
                hr("=")
                result_str = f"{num} {color.upper()}" if num is not None else txt[:20]
                outcome    = "WIN " if won else "LOSS"
                net_sign   = "+" if actual_net >= 0 else ""
                print(f"  ROUND {session.rounds}  |  {result_str}  ->  {outcome}  "
                      f"({net_sign}${actual_net:.2f})")
                if won_pos:
                    print(f"  Hit : {', '.join(won_pos)}")
                if lost_pos:
                    print(f"  Miss: {', '.join(lost_pos)}")
                hr()
                for line in session.summary_lines():
                    print(line)
                hr("=")
                print()

                # ── TP / SL check ────────────────────────────────────────────
                if session.tp_hit():
                    print(f"  *** TAKE PROFIT reached (+${session.net:.2f}) — stopping. ***")
                    break
                if session.sl_hit():
                    print(f"  *** STOP LOSS reached (-${abs(session.net):.2f}) — stopping. ***")
                    break

                state      = WAITING
                placed_pos = []

            time.sleep(0.15)

    except KeyboardInterrupt:
        print("\n\n  Stopped by user (Ctrl+C).")
    except pyautogui.FailSafeException:
        print("\n\n  Stopped by failsafe (mouse corner).")
    finally:
        # Final summary
        print()
        hr("=")
        print("  SESSION COMPLETE")
        hr()
        for line in session.summary_lines():
            print(line)
        hr("=")
        if overlay_proc:
            overlay_proc.terminate()
            print("  Overlay stopped.")
        print()

if __name__ == "__main__":
    main()
