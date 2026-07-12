"""
spinedge_bot.py  —  SpinEdge automated betting CLI

Usage:
    python spinedge_bot.py                          # interactive prompts
    python spinedge_bot.py --auto                   # use saved config, no prompts
    python spinedge_bot.py --auto --strategy S2 --base-bet 0.50 --tp 64 --sl 55
    python spinedge_bot.py --dry-run                # log only, no clicks

Emergency stop: Ctrl+C  OR  move mouse to top-left corner.
"""

import sys, io, os, re, time, json, subprocess, argparse, random, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import cv2, numpy as np, mss, pyautogui
from PIL import Image
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.0

_HERE            = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE      = os.path.join(_HERE, "coords.json")
CONFIG_FILE      = os.path.join(_HERE, "bot_config.json")
BOT_STATE_FILE   = os.path.join(_HERE, "bot_state.json")
BOT_CMD_FILE     = os.path.join(_HERE, "bot_cmd.json")
BOT_HISTORY_FILE = os.path.join(_HERE, "bot_history.json")
BET_DB_FILE      = os.path.join(_HERE, "bet_history.db")

# Interpreter used to launch any child subprocesses.
# Prefer the shared venv (..\venv) if present, else the running interpreter.
PYTHON           = sys.executable
_VENV_PY         = os.path.normpath(os.path.join(_HERE, "..", "venv", "Scripts", "python.exe"))
if os.path.exists(_VENV_PY):
    PYTHON = _VENV_PY

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
    # corners (8:1) — key matches coords.json cr_<numbers> entries
    # 1st dozen (CORNER_HOT / corner_dozen1)
    "cr_1_2_4_5":   lambda n: n in {1,2,4,5},
    "cr_2_3_5_6":   lambda n: n in {2,3,5,6},
    "cr_5_6_8_9":   lambda n: n in {5,6,8,9},
    "cr_8_9_11_12": lambda n: n in {8,9,11,12},
    # 2nd dozen (corner_dozen2) — same shape +12
    "cr_13_14_16_17": lambda n: n in {13,14,16,17},
    "cr_14_15_17_18": lambda n: n in {14,15,17,18},
    "cr_17_18_20_21": lambda n: n in {17,18,20,21},
    "cr_20_21_23_24": lambda n: n in {20,21,23,24},
    # 3rd dozen (corner_dozen3) — same shape +24
    "cr_25_26_28_29": lambda n: n in {25,26,28,29},
    "cr_26_27_29_30": lambda n: n in {26,27,29,30},
    "cr_29_30_32_33": lambda n: n in {29,30,32,33},
    "cr_32_33_35_36": lambda n: n in {32,33,35,36},
}

BET_PAYOUT = {            # net payout per $1 bet (excluding stake)
    "col1_btn":2, "col2_btn":2, "col3_btn":2,
    "1st12":2, "2nd12":2, "3rd12":2,
    "ds1":5, "ds7":5, "ds13":5, "ds19":5, "ds25":5, "ds31":5,
    "red":1, "black":1, "even":1, "odd":1, "1-18":1, "19-36":1,
    "cr_1_2_4_5":8, "cr_2_3_5_6":8, "cr_5_6_8_9":8, "cr_8_9_11_12":8,
    "cr_13_14_16_17":8, "cr_14_15_17_18":8, "cr_17_18_20_21":8, "cr_20_21_23_24":8,
    "cr_25_26_28_29":8, "cr_26_27_29_30":8, "cr_29_30_32_33":8, "cr_32_33_35_36":8,
}

CHIP_DENOMS = [0.10, 0.50, 1.0, 5.0, 25.0, 100.0]
CHIP_KEYS   = {0.10:"chip_0.10", 0.50:"chip_0.50", 1.0:"chip_1",
               5.0:"chip_5", 25.0:"chip_25", 100.0:"chip_100"}

# Fibonacci progression (P&L-based, same as backtest)
FIB_SEQ = [1, 1, 2, 3, 5, 8, 13, 21, 34]
MAX_FIB = 8   # index cap; overflow resets to 0

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
    "CORNERTOP": {
        "name":      "CornerTop",
        "positions": ["cr_1_2_4_5","cr_2_3_5_6"],
        "desc":      "Corner 1-2-4-5 + Corner 2-3-5-6  (Fib TP=$20 SL=$24.40)",
    },
    "CORNER_HOT": {
        "name":      "CornerHot",
        "positions": ["cr_1_2_4_5","cr_2_3_5_6","cr_5_6_8_9","cr_8_9_11_12"],
        "desc":      "4-corner diagonal on hot 5 & 9 — covers 1-6,8-9,11-12  (Fib TP=$40 SL=$48.80, start >=$90)",
    },
    "CORNER_DOZEN1": {
        "name":      "CornerDozen1",
        "positions": ["cr_1_2_4_5","cr_2_3_5_6","cr_5_6_8_9","cr_8_9_11_12"],
        "desc":      "4-corner chain in 1st dozen — covers 1-6,8-9,11-12  (= CornerHot)",
    },
    "CORNER_DOZEN2": {
        "name":      "CornerDozen2",
        "positions": ["cr_13_14_16_17","cr_14_15_17_18","cr_17_18_20_21","cr_20_21_23_24"],
        "desc":      "4-corner chain in 2nd dozen — covers 13-18,20-21,23-24",
    },
    "CORNER_DOZEN3": {
        "name":      "CornerDozen3",
        "positions": ["cr_25_26_28_29","cr_26_27_29_30","cr_29_30_32_33","cr_32_33_35_36"],
        "desc":      "4-corner chain in 3rd dozen — covers 25-30,32-33,35-36",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Config persistence  (last-used values become next-run defaults)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "strategy":     "CORNERTOP",
    "base_bet":     0.1,
    "tp":           20.0,
    "sl":           24.40,
    "max_sessions": 50,     # 0 = unlimited
    "cum_sl":       0.0,    # 0 = disabled (cumulative stop-loss in $)
    "dynamic_base": False,  # advanced: auto-scale base + compound-then-skim withdrawal
    "safe_bankroll": 1000.0,# equity required per one base-bet unit ($0.10) = one tier
    "book_pct":     0.20,   # (retained for reference; skim = excess above the tier)
    "book_mult":    1.2,    # skim when working equity >= book_mult x current tier
    "auto_base":    False,  # simple: set base by balance tiers at each new fib sequence
    "auto_base_per_unit": 900.0,  # balance per $0.10 of base (~max-drawdown safe)
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
    below = [d for d in CHIP_DENOMS if d <= base_bet]
    if below:
        return CHIP_KEYS[max(below)]
    return CHIP_KEYS[CHIP_DENOMS[0]]

def _chips_for_amount(amount, coords):
    """Greedy decomposition of amount into available chip denominations (largest first).

    Returns list of (chip_key, click_count) pairs that sum to amount.
    Only includes chips present in coords.
    """
    plan      = []
    remaining = round(amount, 4)
    for denom in sorted(CHIP_DENOMS, reverse=True):
        key = CHIP_KEYS[denom]
        if key not in coords:
            continue
        count = int(remaining / denom + 1e-9)
        if count > 0:
            plan.append((key, count))
            remaining = round(remaining - count * denom, 4)
    if remaining > 1e-6:
        # fallback: use smallest available chip for any leftover
        smallest = next((CHIP_KEYS[d] for d in CHIP_DENOMS if CHIP_KEYS[d] in coords), None)
        if smallest:
            extra = int(remaining / next(d for d in CHIP_DENOMS if CHIP_KEYS[d] == smallest) + 0.999)
            plan.append((smallest, extra))
    return plan

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

def read_status(coords):
    """Fresh (no-cache) read of the status region text. Returns str or ''."""
    sr = coords.get("_status_region")
    if not sr:
        return ""
    meta = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    with mss.mss() as sct:
        mon = sct.monitors[1]
        reg = {
            "left":   int(sr["x"] * mon["width"]  / sw),
            "top":    int(sr["y"] * mon["height"] / sh),
            "width":  int(sr["w"] * mon["width"]  / sw),
            "height": int(sr["h"] * mon["height"] / sh),
        }
        shot = sct.grab(reg)
    img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    bg  = _bg_color(img)
    return _run_ocr(img, bg)

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


_last_good_balance = {"v": None}   # last balance that passed the sanity check

def read_balance_safe(coords):
    """read_balance() with a sanity guard against OCR misreads.

    The balance cannot legitimately change by a huge factor in one round, so a
    reading that jumps more than max(50% of prev, $200) is rejected and the last
    good value is kept. This prevents a garbage read (e.g. '424.92' -> '42412')
    from creating phantom wins or blowing up the auto-scaled base bet.
    """
    raw  = read_balance(coords)
    prev = _last_good_balance["v"]
    if raw is None:
        return prev
    if prev is not None and prev > 0:
        cap = max(prev * 0.5, 200.0)
        if abs(raw - prev) > cap:
            # 1) Try to recover a misread by shifting a missing decimal point
            #    (e.g. '42412' from '424.12' -> divide by 100).
            for div in (10.0, 100.0, 1000.0, 10000.0):
                cand = round(raw / div, 2)
                if abs(cand - prev) <= cap:
                    print(f"  [FIX] Balance misread ${raw:,.2f} -> recovered "
                          f"${cand:,.2f} (prev ${prev:,.2f})")
                    _last_good_balance["v"] = cand
                    return cand
            # 2) Sanity gate: no plausible recovery -> reject, keep last good.
            print(f"  [WARN] Rejected implausible balance ${raw:,.2f} "
                  f"(prev ${prev:,.2f}, cap ${cap:,.2f}) — keeping ${prev:,.2f}")
            return prev
    _last_good_balance["v"] = raw
    return raw

# ─────────────────────────────────────────────────────────────────────────────
# Game state parsing
# ─────────────────────────────────────────────────────────────────────────────
# Known game phases:
#   PLACE YOUR BETS xx  — betting window open, xx = seconds remaining
#   BET CLOSED          — bets no longer accepted, result pending
#   NEXT GAME SOON      — round ended, new round imminent

def _parse_game_phase(txt):
    """Return (phase, seconds) from status OCR text.

    Phases: 'PLACE_BETS', 'BET_CLOSED', 'NEXT_GAME_SOON', 'UNKNOWN'
    """
    t = txt.upper()
    if "PLACE YOUR BETS" in t:
        m = re.search(r'\b(\d+)\b', t.replace("PLACE YOUR BETS", ""))
        secs = int(m.group(1)) if m else 0
        return "PLACE_BETS", secs
    if "BETS CLOSED" in t or "BET CLOSED" in t or "BETS CLOSING" in t:
        return "BET_CLOSED", 0
    if "BETS ACCEPTED" in t or "BET ACCEPTED" in t:
        return "BET_CLOSED", 0
    if "NEXT GAME" in t:
        return "NEXT_GAME_SOON", 0
    return "UNKNOWN", 0

def number_color(n):
    if n == 0:   return "green"
    return "red" if n in RED_NUMS else "black"

# ─────────────────────────────────────────────────────────────────────────────
# Last winning number — read from number strip (first = most recent)
# ─────────────────────────────────────────────────────────────────────────────
def read_last_number(coords):
    """OCR the _last_number_region (first slot of number strip = last win)."""
    r = coords.get("_last_number_region")
    if not r:
        return None
    meta = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    with mss.mss() as sct:
        mon = sct.monitors[1]
        reg = {
            "left":   int(r["x"] * mon["width"]  / sw),
            "top":    int(r["y"] * mon["height"] / sh),
            "width":  int(r["w"] * mon["width"]  / sw),
            "height": int(r["h"] * mon["height"] / sh),
        }
        shot = sct.grab(reg)
    img  = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    big  = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    raw = pytesseract.image_to_string(
        Image.fromarray(bw),
        config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789"
    ).strip()
    m = re.search(r'\b(\d{1,2})\b', raw)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 36:
            return n
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Total-bet / last-win area  (same screen position, changes per phase)
# ─────────────────────────────────────────────────────────────────────────────
def read_total_bet_area(coords):
    """Return ('LAST_WIN', amount) or ('TOTAL_BET', amount) or (None, None).

    During PLACE YOUR BETS the area shows:
      - 'LAST WIN $x.xx' if the previous round was a win
      - 'TOTAL BET $x.xx' (current bets placed so far this round)
    """
    r = coords.get("_total_bet_region")
    if not r:
        return None, None
    meta = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    with mss.mss() as sct:
        mon = sct.monitors[1]
        reg = {
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
    raw = pytesseract.image_to_string(
        Image.fromarray(bw),
        config="--psm 6 --oem 3"
    ).strip().upper()
    # require at least one digit; tolerate stray commas / OCR noise
    amt_m = re.search(r'\$?(\d[\d,]*\.?\d*)', raw)
    amt = None
    if amt_m:
        try:
            amt = float(amt_m.group(1).replace(",", ""))
        except ValueError:
            amt = None
    if "LAST WIN" in raw or "LASTWIN" in raw:
        return "LAST_WIN", amt
    return "TOTAL_BET", amt

# ─────────────────────────────────────────────────────────────────────────────
# Result parsing (fallback — used when _last_number_region OCR fails)
# ─────────────────────────────────────────────────────────────────────────────
def parse_result(txt):
    """'22 BLACK' or 'BLACK 22' → (22, 'black').  Returns (None, None) if no match."""
    t = txt.upper()
    # "22 BLACK", "22 RED", "0 GREEN"
    m = re.search(r'\b(\d{1,2})\s+(BLACK|RED|GREEN)\b', t)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 36:
            return n, m.group(2).lower()
    # "BLACK 22", "RED 22"
    m = re.search(r'\b(BLACK|RED|GREEN)\s+(\d{1,2})\b', t)
    if m:
        n = int(m.group(2))
        if 0 <= n <= 36:
            return n, m.group(1).lower()
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
# Dynamic base bet — bankroll-aware scaling
# ─────────────────────────────────────────────────────────────────────────────
def scale_for_equity(working, base0, tp0, sl0, cumsl0, safe_bankroll):
    """Return (base, tp, sl, cum_sl) sized to the current working equity.

    Requires `safe_bankroll` of equity per base0 unit (e.g. $1200 per $0.10),
    so a full max-drawdown at that bet size stays covered. TP/SL/cum_sl scale
    proportionally with the bet so session economics are preserved.
    """
    units = max(1, int(working // safe_bankroll))
    base  = round(units * base0, 2)
    scale = base / base0
    return base, round(tp0 * scale, 2), round(sl0 * scale, 2), round(cumsl0 * scale, 2)


def auto_base_scale(balance, per_unit):
    """Simple balance-tier base bet: +$0.10 per `per_unit` of balance.

    e.g. per_unit=$250 -> balance>=250:$0.10, >=500:$0.20, >=750:$0.30 ...
    TP/SL scale off the $0.10 -> $30/$45 ratios; cum_sl = the equity level
    (units x per_unit), i.e. $0.10->$250, $0.20->$500 when per_unit=$250.
    Checked/applied at the start of each new Fibonacci sequence (session).
    """
    units = max(1, int(balance // per_unit))
    base  = round(units * 0.10, 2)
    return base, round(base * 300, 2), round(base * 450, 2), round(units * per_unit, 2)


def cap_base_climb(prev_base, computed, max_climb=0.10):
    """Limit a base-bet INCREASE to +max_climb per step (one new Fib sequence).

    computed is (base, tp, sl, cum_sl); if the new base exceeds prev+max_climb,
    scale it (and tp/sl/cum_sl proportionally) down to the cap. Decreases pass
    through unchanged. This stops a single balance blip from jumping the bet.
    """
    base, tp, sl, cum = computed
    limit = round(prev_base + max_climb, 2)
    if base > limit and base > 0:
        r = limit / base
        return limit, round(tp * r, 2), round(sl * r, 2), round(cum * r, 2)
    return base, tp, sl, cum


# ─────────────────────────────────────────────────────────────────────────────
# Bet placement
# ─────────────────────────────────────────────────────────────────────────────
_last_click = {"pos": None}   # last clicked (sx, sy) — for same-spot fast-repeat

def _click(sx, sy, label="", dry_run=False):
    same = _last_click["pos"] == (sx, sy)
    if dry_run:
        tag = " (fast)" if same else ""
        print(f"    [DRY] {label:20s} ({sx},{sy}){tag}")
        time.sleep(0.02)
        _last_click["pos"] = (sx, sy)
        return
    if same:
        # Cursor already here (stacking chips on the same spot): click again
        # with a shorter randomized delay ~200ms (+/-20ms).
        pyautogui.click()
        if label:
            print(f"    >> {label:20s} ({sx},{sy})")
        time.sleep(random.uniform(0.18, 0.22))
    else:
        pyautogui.moveTo(sx, sy, duration=0.10)
        time.sleep(0.04)
        pyautogui.click()
        if label:
            print(f"    >> {label:20s} ({sx},{sy})")
        time.sleep(random.uniform(0.40, 0.60))   # 500ms +/- 100ms
    _last_click["pos"] = (sx, sy)

# ─────────────────────────────────────────────────────────────────────────────
# Live chip-tray detection  (robust to reload re-centering)
# ─────────────────────────────────────────────────────────────────────────────
# The betting felt (numbers/corners) is a statically-anchored grid, but the chip
# tray is a *centered, variable-width* toolbar: balance-gated denominations
# (500/1000) and undo/repeat buttons come and go, so its member positions drift
# on reload. Each denomination chip draws a bright yellow '+' (RGB~255,220,0) at
# its centre; undo/repeat draw a grey '+'. We detect the yellow '+' intersections
# live and index them left→right (0.10 is always the leftmost denomination, and
# the left side of the tray is stable), so re-centering can't throw us off.
CHIP_ORDER = ["chip_0.10","chip_0.50","chip_1","chip_5","chip_25","chip_100","chip_500","chip_1000"]

def _largest_even_run(xs, lo=36, hi=50):
    """Longest run of consecutive xs whose gaps fall within one chip pitch [lo,hi]."""
    if not xs:
        return xs
    best = cur = [xs[0]]
    for i in range(1, len(xs)):
        if lo <= xs[i] - xs[i-1] <= hi:
            cur = cur + [xs[i]]
        else:
            if len(cur) > len(best):
                best = cur
            cur = [xs[i]]
    return cur if len(cur) > len(best) else best

def detect_chip_centers(coords):
    """Return (ref_x_centers_sorted, ref_y) of the chip '+' markers, or (None, None).

    Keeps only columns that carry the '+' horizontal bar (the intersection),
    which rejects the tall yellow runs produced by the ring tangents *between*
    adjacent chips. Coords are returned in the same reference space as coords.json.
    """
    meta   = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    # The tray drifts vertically as well as horizontally by game state, so scan a
    # tall band and locate the '+' row (peak yellow density) rather than assume y.
    bx0, bx1 = 855, 1425
    by0, by1 = 800, 882
    try:
        with mss.mss() as sct:
            mon = sct.monitors[1]
            sx  = mon["width"]  / sw
            sy  = mon["height"] / sh
            reg = {"left": int(bx0*sx), "top": int(by0*sy),
                   "width": int((bx1-bx0)*sx), "height": int((by1-by0)*sy)}
            shot = sct.grab(reg)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    except Exception:
        return None, None
    Hh, Ww = img.shape[:2]
    B = img[:, :, 0].astype(np.int16)
    G = img[:, :, 1].astype(np.int16)
    R = img[:, :, 2].astype(np.int16)
    ymask  = (R > 200) & (G > 150) & (G < 240) & (B < 90)
    # locate the '+' intersection row (its horizontal bars spike yellow-per-row);
    # if there's essentially no yellow, the tray isn't in its interactive form.
    rowsum = ymask.sum(axis=1)
    if rowsum.max() < max(15, int(20 * sx)):
        return None, None
    prow = int(np.argmax(rowsum))
    rlo, rhi = max(0, prow - 13), min(Hh, prow + 13)
    colsum = ymask[rlo:rhi].sum(axis=0)
    th     = max(5, int(7 * sy))
    gap_px = max(6, int(10 * sx))
    hbar   = max(5, int(6 * sx))
    cand = []; s = None; last = None
    for i in range(Ww):
        if colsum[i] >= th:
            if s is None:
                s = i
            last = i
        elif s is not None and i - last > gap_px:
            cand.append((s + last) // 2); s = None
    if s is not None:
        cand.append((s + last) // 2)
    centers = []
    for cx in cand:
        lo = max(0, cx - 4); hi = min(Ww, cx + 5)
        if (ymask[rlo:rhi, lo:hi].sum(axis=1) >= hbar).any():   # '+' horizontal bar
            centers.append(cx)
    if len(centers) < 3:
        return None, None
    ref_centers = sorted(int(round(bx0 + cx / sx)) for cx in centers)
    ref_centers = _largest_even_run(ref_centers)
    if len(ref_centers) < 3:
        return None, None
    ref_y = int(round(by0 + prow / sy))
    return ref_centers, ref_y

# Last-good detection: the first betting window after a reload detects the true
# tray position; we cache it so later rounds stay aligned even if an individual
# frame is momentarily unreadable (spin animation, occlusion, colour re-skin).
# Cleared naturally when the bot process restarts; overwritten by any fresh detect.
_chip_cache = {"centers": None, "ref_y": None, "persisted_x0": None}

def _persist_chip_coords(coords):
    """Atomically write the chip-corrected coords back to COORDS_FILE.

    The overlay is a separate process that reloads coords.json whenever its mtime
    changes, so persisting a good detection is what makes the overlay's chip
    markers line up (and permanently fixes the stored miscalibration). os.replace
    is atomic, so the overlay never reads a half-written file."""
    try:
        tmp = COORDS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(coords, f, indent=2)
        os.replace(tmp, COORDS_FILE)
        return True
    except Exception as e:
        print(f"  [CHIP] could not persist coords: {e}")
        return False

def _apply_chip_centers(coords, centers, ref_y):
    """Write chip_*/btn_* into `coords` from left→right centres; return pitch."""
    for i, cx in enumerate(centers):
        if i < len(CHIP_ORDER):
            coords[CHIP_ORDER[i]] = [cx, ref_y]
    pitch = centers[1] - centers[0] if len(centers) >= 2 else 43
    coords["btn_undo"]   = [centers[0]  - pitch, ref_y]
    coords["btn_repeat"] = [centers[-1] + pitch, ref_y]
    return pitch

def reset_chip_cache():
    """Forget the cached detection (call on game reload if bot keeps running)."""
    _chip_cache["centers"] = None
    _chip_cache["ref_y"]   = None

# The interactive PLACE-YOUR-BETS tray (the state the bot actually clicks in)
# colour-codes its chips: 0.50 orange, 5 red, 25 green, 500 purple. We anchor on
# those saturated chips at their fixed indices and least-squares fit x0 + i*pitch
# to place every denomination — robust to re-centering AND to the dark chips
# (1/100) that brightness/edge methods miss.  (0.10 sits at index 0.)
_CHIP_ANCHORS = [   # (CHIP_ORDER index, colour test on int16 R,G,B planes)
    (1, lambda R, G, B: (R > 190) & (G > 85) & (G < 180) & (B < 80)),   # 0.50 orange
    (3, lambda R, G, B: (R > 150) & (G < 80) & (B < 80)),               # 5    red
    (4, lambda R, G, B: (G > 110) & (R < 120) & (B < 120)),             # 25   green
    (6, lambda R, G, B: (R > 105) & (B > 115) & (G < 95)),              # 500  purple
]

def detect_chip_centers_colored(coords):
    """Detect the colour-coded placement tray. Returns (ref_x[0.10..500], ref_y)
    or (None, None). Fits x0+pitch from the saturated anchor chips."""
    meta   = coords.get("_meta", {})
    sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
    bx0, bx1 = 855, 1425
    by0, by1 = 795, 890
    try:
        with mss.mss() as sct:
            mon = sct.monitors[1]
            sx  = mon["width"]  / sw
            sy  = mon["height"] / sh
            reg = {"left": int(bx0*sx), "top": int(by0*sy),
                   "width": int((bx1-bx0)*sx), "height": int((by1-by0)*sy)}
            shot = sct.grab(reg)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    except Exception:
        return None, None
    B = img[:, :, 0].astype(np.int16)
    G = img[:, :, 1].astype(np.int16)
    R = img[:, :, 2].astype(np.int16)
    idxs, xs, ys = [], [], []
    for idx, test in _CHIP_ANCHORS:
        mask = np.asarray(test(R, G, B), dtype=np.uint8) * 255
        n, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        if n <= 1:
            continue
        k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        if stats[k, cv2.CC_STAT_AREA] < 40:           # ignore stray colour specks
            continue
        idxs.append(idx)
        xs.append(bx0 + cent[k][0] / sx)
        ys.append(by0 + cent[k][1] / sy)
    if len(idxs) < 2:
        return None, None
    I = np.asarray(idxs, dtype=float); X = np.asarray(xs, dtype=float)
    x0, pitch = np.linalg.lstsq(np.vstack([np.ones_like(I), I]).T, X, rcond=None)[0]
    if not (34 <= pitch <= 48):
        return None, None
    ref_y = int(round(sum(ys) / len(ys)))
    ref_centers = [int(round(x0 + i * pitch)) for i in range(7)]   # 0.10 .. 500
    return ref_centers, ref_y

def refresh_chip_coords(coords, verbose=False):
    """Live-detect the chip tray and update chip_*/btn_* in `coords` in place.

    Priority: (1) the colour-coded placement tray (what the bot clicks in);
    (2) the yellow-'+' tray (other states); (3) the last-good cached detection;
    (4) the stored coords. Successful detections refresh the cache. Returns True
    if coords were set from a live/cached detection, False if it fell through."""
    for detector, tag in ((detect_chip_centers_colored, "colour"),
                          (detect_chip_centers,         "yellow+")):
        centers, ref_y = detector(coords)
        if centers and len(centers) >= 4:
            pitch = _apply_chip_centers(coords, centers, ref_y)
            _chip_cache["centers"] = list(centers)
            _chip_cache["ref_y"]   = ref_y
            if verbose:
                print(f"  [CHIP] tray detected ({tag}): {len(centers)} chips, "
                      f"0.10@x={centers[0]} y={ref_y} pitch={pitch}px")
            # Persist to disk when the tray has meaningfully moved, so the overlay
            # (which watches coords.json) redraws its chip markers to match.
            px0 = _chip_cache["persisted_x0"]
            if px0 is None or abs(centers[0] - px0) >= 3:
                if _persist_chip_coords(coords):
                    _chip_cache["persisted_x0"] = centers[0]
                    if verbose:
                        print(f"  [CHIP] wrote corrected tray to "
                              f"{os.path.basename(COORDS_FILE)} — overlay will refresh")
            return True
    if _chip_cache["centers"]:                       # fall back to last-good detect
        c, y = _chip_cache["centers"], _chip_cache["ref_y"]
        _apply_chip_centers(coords, c, y)
        if verbose:
            print(f"  [CHIP] tray not visible — using last-good detection "
                  f"(0.10@x={c[0]} y={y})")
        return True
    if verbose:
        print("  [CHIP] no detection yet, no cache — using stored coords")
    return False

def place_bets(strat_key, base_bet, coords, sx_m, sy_m, fib_mult=1, dry_run=False):
    refresh_chip_coords(coords, verbose=True)   # re-detect tray live (reload-proof)
    fib_bet   = round(base_bet * fib_mult, 4)
    positions = STRATEGIES[strat_key]["positions"]
    chip_plan = _chips_for_amount(fib_bet, coords)
    placed    = []

    print(f"  Chip plan for ${fib_bet:.2f}/pos: "
          + ", ".join(f"{n}×{k}" for k, n in chip_plan))

    for chip_key, n_clicks in chip_plan:
        cx, cy = coords[chip_key]
        _click(int(cx*sx_m), int(cy*sy_m), label=f"SELECT {chip_key}", dry_run=dry_run)
        time.sleep(0.10)

        for pos in positions:
            val = coords.get(pos)
            if not (isinstance(val, (list, tuple)) and len(val) == 2):
                if pos not in placed:
                    print(f"    [SKIP] {pos}")
                continue
            px, py = int(val[0]*sx_m), int(val[1]*sy_m)
            for i in range(n_clicks):
                lbl = pos if i == 0 else ""
                _click(px, py, label=lbl, dry_run=dry_run)
            if pos not in placed:
                placed.append(pos)

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

    def record(self, won, number, positions, bal_after, est_net, color=""):
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
            "won": won, "pnl": round(est_net, 2),
            "color": color or "",
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
# Bot command reader  (written by bot_panel.py)
# ─────────────────────────────────────────────────────────────────────────────
def read_bot_cmd():
    """Return 'run', 'pause', or 'stop'."""
    try:
        with open(BOT_CMD_FILE, encoding="utf-8") as f:
            return json.load(f).get("cmd", "run")
    except Exception:
        return "run"

# ─────────────────────────────────────────────────────────────────────────────
# Bot state writer  (read by bot_panel.py)
# ─────────────────────────────────────────────────────────────────────────────
_state_extra = {"cum_sl": 0.0}   # runtime params surfaced to bot_state.json

def write_bot_state(phase, session, strat_key, base_bet,
                    last_number=None, last_color=None, last_pnl=0.0,
                    tp_count=0, sl_count=0, fib_idx=0, fib_mult=1):
    state = {
        "state":           phase,
        "strategy":        strat_key,
        "positions":       STRATEGIES[strat_key]["positions"],
        "balance":         round(session.balance, 2),
        "initial_balance": round(session.initial, 2),
        "net":             round(session.net, 2),
        "tp":              session.tp,
        "sl":              session.sl,
        "cum_sl":          round(_state_extra.get("cum_sl", 0.0), 2),
        "base_bet":        base_bet,
        "rounds":          session.rounds,
        "wins":            session.wins,
        "losses":          session.losses,
        "win_streak":      session.win_streak,
        "loss_streak":     session.loss_streak,
        "best_win":        session.best_win,
        "best_loss":       session.best_loss,
        "last_number":     last_number,
        "last_color":      last_color,
        "last_pnl":        round(last_pnl, 2),
        "tp_count":        tp_count,
        "sl_count":        sl_count,
        "fib_idx":         fib_idx,
        "fib_mult":        fib_mult,
        "fib_bet":         round(base_bet * fib_mult, 4),
        "history":         session.history[-20:],
        "timestamp":       time.strftime("%H:%M:%S"),
    }
    try:
        with open(BOT_STATE_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"  [WARN] Could not write bot_state.json: {e}")


def append_bot_history(entry):
    """Append a single round entry to the persistent bot_history.json."""
    try:
        try:
            with open(BOT_HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
        data.append(entry)
        with open(BOT_HISTORY_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, separators=(",", ":"))
    except Exception as e:
        print(f"  [WARN] Could not write bot_history.json: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Bet history — full per-round record in SQLite (bet_history.db)
# ─────────────────────────────────────────────────────────────────────────────
_BET_COLS = ["timestamp", "session_num", "round", "strategy", "number", "color",
             "won", "pnl", "balance_before", "balance_after", "base_bet",
             "bet_amount", "fib_idx", "fib_mult", "win_streak", "loss_streak",
             "tp", "sl", "cum_sl", "source"]

def init_bet_db():
    try:
        conn = sqlite3.connect(BET_DB_FILE)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bet_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      TEXT,
                session_num    INTEGER,
                round          INTEGER,
                strategy       TEXT,
                number         INTEGER,
                color          TEXT,
                won            INTEGER,
                pnl            REAL,
                balance_before REAL,
                balance_after  REAL,
                base_bet       REAL,
                bet_amount     REAL,
                fib_idx        INTEGER,
                fib_mult       INTEGER,
                win_streak     INTEGER,
                loss_streak    INTEGER,
                tp             REAL,
                sl             REAL,
                cum_sl         REAL,
                source         TEXT
            )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"  [WARN] init_bet_db: {e}")

def save_bet_row(**row):
    """Insert one round's full record into bet_history.db."""
    try:
        cols = [c for c in _BET_COLS if c in row]
        conn = sqlite3.connect(BET_DB_FILE)
        conn.execute(
            f"INSERT INTO bet_history ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [row[c] for c in cols])
        conn.commit(); conn.close()
    except Exception as e:
        print(f"  [WARN] save_bet_row: {e}")

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
# Single-instance guard — kill any other running spinedge_bot before we start
# ─────────────────────────────────────────────────────────────────────────────
def kill_other_bot_instances():
    """Terminate any other spinedge_bot.py processes (keep only this one).

    Prevents duplicate bots fighting over the same screen/clicks. Windows-only
    (uses PowerShell + taskkill); no third-party deps.
    """
    own = os.getpid()
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
            "Where-Object { $_.CommandLine -match 'spinedge_bot' -and $_.ProcessId -ne "
            + str(own) + " } | ForEach-Object { $_.ProcessId }"
        )
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15)
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        for pid in pids:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10)
                print(f"  [GUARD] Killed existing bot instance PID {pid}")
            except Exception as e:
                print(f"  [GUARD] Could not kill PID {pid}: {e}")
        if not pids:
            print("  [GUARD] No other bot instance running.")
    except Exception as e:
        print(f"  [GUARD] Instance check failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def _parse_args():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--auto",       action="store_true", help="Skip all prompts, use config/args directly")
    ap.add_argument("--no-overlay", action="store_true", help="Do not launch overlay (control.py manages it)")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--strategy",     default=None)
    ap.add_argument("--base-bet",     type=float, default=None)
    ap.add_argument("--tp",           type=float, default=None)
    ap.add_argument("--sl",           type=float, default=None)
    ap.add_argument("--max-sessions", type=int,   default=None,
                    help="Stop after N completed sessions (0 = unlimited)")
    ap.add_argument("--cum-sl",       type=float, default=None,
                    help="Stop when cumulative loss from start reaches $X (0 = disabled)")
    return ap.parse_known_args()[0]


def main():
    args    = _parse_args()
    dry_run = args.dry_run

    print()
    hr("=")
    print("  SpinEdge Bot" + ("  [DRY-RUN]" if dry_run else "") + ("  [AUTO]" if args.auto else ""))
    hr("=")

    # Ensure only one bot runs — kill any other instances before we begin
    kill_other_bot_instances()
    print()

    # ── Load last-used config as defaults ─────────────────────────────────────
    cfg = load_config()

    # ── Load + validate coords ────────────────────────────────────────────────
    coords = load_coords()
    if not coords:
        print(f"  [ERROR] Cannot load {COORDS_FILE}")
        sys.exit(1)

    if args.auto:
        # ── AUTO mode: use args/config directly, skip all prompts ────────────
        strat_key    = (args.strategy or cfg["strategy"]).upper()
        base_bet     = args.base_bet  if args.base_bet is not None else cfg["base_bet"]
        tp           = args.tp        if args.tp        is not None else cfg["tp"]
        sl           = args.sl        if args.sl        is not None else cfg["sl"]
        max_sessions = args.max_sessions if args.max_sessions is not None else cfg.get("max_sessions", 0)
        cum_sl       = args.cum_sl       if args.cum_sl       is not None else cfg.get("cum_sl", 0.0)
        if strat_key not in STRATEGIES:
            print(f"  [ERROR] Unknown strategy: {strat_key}")
            sys.exit(1)
        missing = validate_strategy(strat_key, base_bet, coords)
        if missing:
            print(f"  [ERROR] Missing coords: {', '.join(missing)}")
            sys.exit(1)
    else:
        # ── INTERACTIVE mode ──────────────────────────────────────────────────
        print("  Strategies:")
        print()
        for key, s in STRATEGIES.items():
            missing = validate_strategy(key, cfg["base_bet"], coords)
            mark    = "[OK]  " if not missing else "[MISS]"
            print(f"  {mark} {key}: {s['name']:15s}  —  {s['desc']}")
            if missing:
                print(f"         Missing coords: {', '.join(missing)}")
        print()

        strat_key = None
        while strat_key not in STRATEGIES:
            strat_key = ask(f"Strategy ({'/'.join(STRATEGIES)})", cfg["strategy"]).upper()
            if strat_key not in STRATEGIES:
                print(f"    Choose one of: {', '.join(STRATEGIES)}")
                strat_key = None

        print()
        print(f"  Available chips: {', '.join(f'${d}' for d in CHIP_DENOMS)}")
        base_bet = ask("Base bet per position ($)", cfg["base_bet"], float)
        while base_bet <= 0:
            print("    Must be > 0.")
            base_bet = ask("Base bet per position ($)", cfg["base_bet"], float)

        print()
        tp = ask("Take Profit — stop when net gain reaches ($)", cfg["tp"], float)
        sl = ask("Stop Loss  — stop when net loss reaches  ($)", cfg["sl"], float)

        print()
        max_sessions = ask("Max sessions — stop after N sessions (0 = unlimited)",
                           cfg.get("max_sessions", 0), int)
        cum_sl       = ask("Cumulative SL — stop when total loss reaches ($, 0 = off)",
                           cfg.get("cum_sl", 0.0), float)

        missing = validate_strategy(strat_key, base_bet, coords)
        if missing:
            print(f"\n  [ERROR] Missing coords for {strat_key}:")
            for m in missing:
                print(f"    - {m}")
            sys.exit(1)

    chip_key = best_chip_key(base_bet)
    n_pos    = len(STRATEGIES[strat_key]["positions"])

    # ── Dynamic / auto base bet settings ──────────────────────────────────────
    dynamic_base  = bool(cfg.get("dynamic_base", False))
    safe_bankroll = float(cfg.get("safe_bankroll", 1200.0))
    book_pct      = float(cfg.get("book_pct", 0.20))
    book_mult     = float(cfg.get("book_mult", 2.2))
    auto_base     = bool(cfg.get("auto_base", False))
    auto_base_per_unit = float(cfg.get("auto_base_per_unit", 200.0))
    if auto_base:            # simple auto-base takes precedence over dynamic_base
        dynamic_base = False

    # ── Save config ───────────────────────────────────────────────────────────
    cfg.update({"strategy": strat_key, "base_bet": base_bet, "tp": tp, "sl": sl,
                "max_sessions": max_sessions, "cum_sl": cum_sl})
    save_config(cfg)

    # ── Confirm ───────────────────────────────────────────────────────────────
    print()
    hr()
    print(f"  Strategy : {strat_key}  ({STRATEGIES[strat_key]['name']})")
    print(f"  Chip     : {chip_key}  x{n_pos} positions  = ${base_bet*n_pos:.2f}/round")
    print(f"  Positions: {', '.join(STRATEGIES[strat_key]['positions'])}")
    print(f"  TP       : +${tp:.2f}  (per session)")
    print(f"  SL       : -${sl:.2f}  (per session)")
    print(f"  Max sess : {max_sessions if max_sessions > 0 else 'unlimited'}")
    print(f"  Cum SL   : {('-$' + format(cum_sl, '.2f')) if cum_sl > 0 else 'off'}")
    if auto_base:
        print(f"  Auto base: ON  (+$0.10 per ${auto_base_per_unit:.0f} balance, "
              f"at each new Fib sequence)")
    elif dynamic_base:
        print(f"  Dyn base : ON  (${safe_bankroll:.0f}/unit, book {book_pct*100:.0f}% "
              f"at {book_mult:.1f}x)")
    hr()
    print()

    if not args.auto:
        go = input("  Press Enter to start  (or 'q' to quit): ").strip().lower()
        if go == "q":
            print("  Aborted.")
            return

    # ── Launch stats panel (unless suppressed) ───────────────────────────────
    panel_path = os.path.join(_HERE, "bot_panel.py")
    overlay_proc = None
    if not args.no_overlay and os.path.exists(panel_path):
        print()
        print("  [*] Starting stats panel...")
        overlay_proc = subprocess.Popen(
            [PYTHON, panel_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        print("  [*] Stats panel running.")
    elif args.no_overlay:
        print("  [*] Panel managed externally.")
    else:
        print("  [WARN] bot_panel.py not found — running without panel")

    # ── Read initial balance ──────────────────────────────────────────────────
    print()
    print("  Reading initial balance...")
    coords       = load_coords()
    init_balance = None
    for _ in range(5):
        init_balance = read_balance_safe(coords)
        if init_balance is not None:
            break
        time.sleep(1)
    if init_balance is None:
        print("  [WARN] Could not read balance — starting with $0.00")
        init_balance = 0.0
    print(f"  Initial balance: ${init_balance:.2f}")

    # ── Dynamic / auto base bet: store unit values, size to starting bankroll ──
    base0, tp0, sl0, cumsl0 = base_bet, tp, sl, cum_sl
    banked   = 0.0                 # cumulative profit recommended for withdrawal
    book_ref = safe_bankroll       # current bankroll tier / skim floor
    if auto_base:
        base_bet, tp, sl, cum_sl = cap_base_climb(
            base_bet, auto_base_scale(init_balance, auto_base_per_unit))
        print(f"  [AUTO] Auto base ON — base ${base_bet:.2f} @ balance ${init_balance:.2f}  "
              f"(+$0.10 per ${auto_base_per_unit:.0f})  TP ${tp:.2f} / SL ${sl:.2f} / cumSL ${cum_sl:.2f}")
    elif dynamic_base:
        # advance the tier so we don't immediately skim a large starting balance
        while init_balance >= book_mult * book_ref:
            book_ref += safe_bankroll
        base_bet, tp, sl, cum_sl = cap_base_climb(base_bet, scale_for_equity(
            init_balance, base0, tp0, sl0, cumsl0, safe_bankroll))
        print(f"  [DYN] Dynamic base ON — base ${base_bet:.2f}  "
              f"(TP ${tp:.2f} / SL ${sl:.2f} / cumSL ${cum_sl:.2f}), "
              f"skim at ${book_mult*book_ref:.0f} (leave ${book_ref:.0f})")
    _state_extra["cum_sl"] = cum_sl   # surface current cum_sl to the panel

    # ── Session + state machine ───────────────────────────────────────────────
    init_bet_db()            # ensure bet_history.db + table exist
    session  = Session(init_balance, tp, sl, strat_key, base_bet)
    reader   = StatusReader()
    sx_m, sy_m = get_scale(coords)

    last_number = None
    last_color  = None
    last_pnl    = 0.0
    tp_count    = 0
    sl_count    = 0
    session_num = 1        # current session number (increments on TP/SL)
    start_bal   = init_balance   # balance at the very first session start

    # Fibonacci state (mirrors backtest logic exactly)
    fib_idx  = 0      # current level (0-8)
    fib_base = 0.0    # session.net at last Fibonacci reset point

    WAITING  = "WAITING"
    PLACED   = "PLACED"
    RESULT   = "RESULT"
    state    = WAITING

    bal_before           = init_balance
    placed_pos           = []
    result_wait          = 0.0   # failsafe: seconds since we last saw a known phase in PLACED
    placed_at_secs       = 0     # countdown value when we placed bets this round
    placed_time          = 0.0   # wall-clock time when we placed bets
    pending_result_num   = None  # number captured from status text during PLACED phase
    pending_result_color = None

    print()
    hr("=")
    print(f"  Bot running  —  Ctrl+C or mouse to top-left to stop")
    hr("=")
    print()
    print(f"  {'Time':8s}  {'Rnd':4s}  {'State':8s}  {'Phase':20s}  Status")
    hr()

    try:
        while True:
            coords     = load_coords()
            sx_m, sy_m = get_scale(coords)
            bg, txt    = reader.read(coords)
            ts         = time.strftime("%H:%M:%S")
            phase, secs = _parse_game_phase(txt)
            phase_lbl   = f"{phase}:{secs}s" if phase == "PLACE_BETS" and secs else phase

            # ── WAITING: watch for betting window ────────────────────────────
            if state == WAITING:
                print(f"\r  {ts}  {'---':4s}  {state:8s}  {phase_lbl:20s}  {txt[:25]:25s}",
                      end="", flush=True)

                if phase == "PLACE_BETS":
                    cmd = read_bot_cmd()
                    if cmd == "stop":
                        print("\n  [PANEL] Stop command received.")
                        break
                    if cmd == "pause":
                        write_bot_state("PAUSED", session, strat_key, base_bet,
                                        last_number, last_color, last_pnl,
                                        tp_count, sl_count, fib_idx,
                                        FIB_SEQ[min(fib_idx, MAX_FIB)])
                        print(f"\r  {ts}  {'---':4s}  {'PAUSED':8s}  {phase_lbl:20s}  waiting for resume...",
                              end="", flush=True)
                        time.sleep(0.5)
                        continue

                    clear_line()
                    print()
                    hr()
                    print(f"  ROUND {session.rounds+1}  —  {phase_lbl}  ({secs}s remaining)")
                    hr()
                    print(f"  Placing bets: {', '.join(STRATEGIES[strat_key]['positions'])}")
                    fib_mult = FIB_SEQ[min(fib_idx, MAX_FIB)]
                    fib_bet  = round(base_bet * fib_mult, 4)
                    print(f"  Fib L{fib_idx} (×{fib_mult})  bet ${fib_bet:.2f}/position")
                    _ocr_bal        = read_balance_safe(coords)
                    bal_before      = _ocr_bal if _ocr_bal is not None else session.balance
                    if _ocr_bal is not None:
                        session.balance = _ocr_bal
                        session.net     = round(_ocr_bal - session.initial, 2)
                    placed_pos      = place_bets(strat_key, base_bet, coords, sx_m, sy_m,
                                                 fib_mult=fib_mult, dry_run=dry_run)
                    state           = PLACED
                    result_wait     = 0.0
                    placed_at_secs  = secs
                    placed_time     = time.time()
                    print(f"  Bets placed ({len(placed_pos)}).")

                    # ── Verify total bet via OCR ──────────────────────────────
                    time.sleep(0.3)
                    _bet_type, _bet_amt = read_total_bet_area(coords)
                    expected_total = round(fib_bet * len(placed_pos), 4)
                    if _bet_amt is not None:
                        _ok = abs(_bet_amt - expected_total) < 0.02
                        _sym = "✓" if _ok else "✗ MISMATCH"
                        print(f"  Total bet check: OCR=${_bet_amt:.2f}  expected=${expected_total:.2f}  {_sym}")
                    else:
                        print(f"  Total bet check: OCR read failed  (expected ${expected_total:.2f})")
                    print()
                    write_bot_state("PLACED", session, strat_key, base_bet,
                                    last_number, last_color, last_pnl,
                                    tp_count, sl_count, fib_idx, fib_mult)

            # ── PLACED: bets are in — wait for round to end ──────────────────
            elif state == PLACED:
                if read_bot_cmd() == "stop":
                    print("\n  [PANEL] Stop command received (mid-round — completing).")
                    state = RESULT
                    time.sleep(1.0)

                # Capture result number from status text (e.g. "34 RED") while waiting
                if pending_result_num is None:
                    _pn, _pc = parse_result(txt)
                    if _pn is not None:
                        pending_result_num   = _pn
                        pending_result_color = _pc

                print(f"\r  {ts}  {str(session.rounds+1):4s}  {state:8s}  {phase_lbl:20s}  {txt[:25]:25s}",
                      end="", flush=True)

                if phase == "NEXT_GAME_SOON":
                    # Round ended — number strip updated, safe to read result
                    state = RESULT

                elif phase == "PLACE_BETS":
                    # Only treat as new round if countdown reset above where we placed
                    # (secs > placed_at_secs means counter restarted) OR enough time passed
                    elapsed = time.time() - placed_time
                    if secs > placed_at_secs or elapsed >= 30.0:
                        state = RESULT  # new round started — we missed the result window

                elif phase == "BET_CLOSED":
                    # Normal: bets locked, wheel spinning — keep waiting
                    result_wait = 0.0

                else:
                    # UNKNOWN phase — failsafe: if quiet for 3s, assume result came
                    if result_wait == 0.0:
                        result_wait = time.time()
                    elif time.time() - result_wait >= 3.0:
                        state = RESULT

            # ── RESULT: read number strip, record stats, check TP/SL ─────────
            elif state == RESULT:
                clear_line()

                # Re-read status immediately — may still show "34 RED" result
                coords     = load_coords()
                fresh_txt  = read_status(coords)
                _fn, _fc   = parse_result(fresh_txt) if fresh_txt else (None, None)
                if _fn is not None and pending_result_num is None:
                    pending_result_num   = _fn
                    pending_result_color = _fc

                # Give game 1s for balance to settle
                time.sleep(1.0)
                bal_after = read_balance_safe(coords)

                # 1st choice: number captured from status during spin (most reliable)
                # 2nd choice: re-read history strip (fallback)
                if pending_result_num is not None:
                    num   = pending_result_num
                    color = pending_result_color
                else:
                    num   = read_last_number(coords)
                    color = number_color(num) if num is not None else None

                # Last resort: parse fresh status text again
                if num is None:
                    num, color = parse_result(fresh_txt or txt)

                pending_result_num   = None
                pending_result_color = None

                won_pos, lost_pos, est_net = eval_round(num, placed_pos, fib_bet)

                # Determine win/loss: prefer real balance diff, fall back to logic
                if bal_after is not None and bal_before is not None:
                    actual_net = bal_after - bal_before
                    won        = actual_net > 0
                else:
                    bal_after  = session.balance + est_net
                    actual_net = est_net
                    won        = len(won_pos) > 0

                session.record(won, num, placed_pos, bal_after, actual_net,
                               color=color or "")
                last_number = num
                last_color  = color if color else ""
                last_pnl    = actual_net

                # Persist to cross-session history file
                append_bot_history(session.history[-1])

                # Full per-round record to SQLite (fib_idx here = level used for
                # THIS round's bet, before the progression update below).
                save_bet_row(
                    timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    session_num=session_num, round=session.rounds, strategy=strat_key,
                    number=num, color=(color or ""), won=1 if won else 0,
                    pnl=round(actual_net, 2),
                    balance_before=round(bal_before, 2) if bal_before is not None else None,
                    balance_after=round(bal_after, 2) if bal_after is not None else None,
                    base_bet=base_bet, bet_amount=round(fib_bet, 4),
                    fib_idx=fib_idx, fib_mult=FIB_SEQ[min(fib_idx, MAX_FIB)],
                    win_streak=session.win_streak, loss_streak=session.loss_streak,
                    tp=tp, sl=sl, cum_sl=cum_sl, source="live")

                # ── Fibonacci progression (P&L-based, same as backtest) ───────
                since = session.net - fib_base
                if since >= 0:
                    fib_idx  = 0
                    fib_base = session.net
                elif actual_net < -1e-9:
                    fib_idx += 1
                    if fib_idx > MAX_FIB:
                        fib_idx  = 0
                        fib_base = session.net
                next_mult = FIB_SEQ[min(fib_idx, MAX_FIB)]
                print(f"  Fib next: L{fib_idx} (×{next_mult})  next bet ${base_bet*next_mult:.2f}/position")

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

                # ── Cumulative stop-loss — global safety guard (every round) ──
                cum      = round(session.balance - start_bal, 2)
                cum_sign = "+" if cum >= 0 else "-"
                if cum_sl > 0 and cum <= -cum_sl:
                    write_bot_state("SL_HIT", session, strat_key, base_bet,
                                    last_number, last_color, last_pnl,
                                    tp_count, sl_count, fib_idx,
                                    FIB_SEQ[min(fib_idx, MAX_FIB)])
                    print(f"  *** CUMULATIVE STOP-LOSS reached "
                          f"(-${abs(cum):.2f} of -${cum_sl:.2f}) — stopping bot. ***")
                    break

                # ── TP / SL check — on hit, roll into a new session ───────────
                _fib_next = FIB_SEQ[min(fib_idx, MAX_FIB)]
                hit = "TP" if session.tp_hit() else ("SL" if session.sl_hit() else None)
                if hit:
                    if hit == "TP":
                        tp_count += 1
                        word, sign = "TAKE PROFIT", "+"
                    else:
                        sl_count += 1
                        word, sign = "STOP LOSS", "-"
                    write_bot_state("TP_HIT" if hit == "TP" else "SL_HIT",
                                    session, strat_key, base_bet,
                                    last_number, last_color, last_pnl,
                                    tp_count, sl_count, fib_idx, _fib_next)
                    print(f"  *** {word} reached ({sign}${abs(session.net):.2f}) "
                          f"— session {session_num} done. ***")

                    # Max-sessions guard — stop instead of opening a new session
                    if max_sessions > 0 and (tp_count + sl_count) >= max_sessions:
                        print(f"  Sessions: {tp_count} TP / {sl_count} SL   "
                              f"Cumulative: {cum_sign}${abs(cum):.2f}")
                        print(f"  *** MAX SESSIONS ({max_sessions}) reached "
                              f"— stopping bot. ***")
                        break

                    print(f"  Sessions: {tp_count} TP / {sl_count} SL   "
                          f"Cumulative: {cum_sign}${abs(cum):.2f}   "
                          f"→ starting session {session_num + 1}")
                    # brief pause so the panel shows the TP/SL badge
                    time.sleep(1.5)
                    # ── Base bet adjustment before the next Fibonacci sequence ──
                    if auto_base:
                        # simple: set base by balance tier, climb capped to +$0.10
                        base_bet, tp, sl, cum_sl = cap_base_climb(
                            base_bet, auto_base_scale(session.balance, auto_base_per_unit))
                        print(f"  [AUTO] base ${base_bet:.2f} @ balance ${session.balance:.2f}  "
                              f"(TP ${tp:.2f} / SL ${sl:.2f} / cumSL ${cum_sl:.2f})")
                    elif dynamic_base:
                        bal = session.balance   # real OCR balance = working equity
                        # compound-then-skim: skim excess above the tier, then
                        # advance the tier by one bankroll unit (base compounds
                        # as the balance climbs toward the next skim)
                        if bal >= book_mult * book_ref:
                            take     = round(bal - book_ref, 2)
                            banked   = round(banked + take, 2)
                            leave    = book_ref
                            book_ref = round(book_ref + safe_bankroll, 2)
                            print(f"  *** WITHDRAW ${take:.2f} NOW — leave ${leave:.0f} working "
                                  f"(withdrawn total ${banked:.2f}, next skim at ${book_mult*book_ref:.0f}) ***")
                        base_bet, tp, sl, cum_sl = cap_base_climb(base_bet, scale_for_equity(
                            bal, base0, tp0, sl0, cumsl0, safe_bankroll))
                        print(f"  [DYN] base ${base_bet:.2f}  "
                              f"(TP ${tp:.2f} / SL ${sl:.2f} / cumSL ${cum_sl:.2f})  "
                              f"balance ${bal:.2f}")
                    _state_extra["cum_sl"] = cum_sl   # surface current cum_sl
                    # Fresh session from the current balance; reset Fibonacci
                    session_num += 1
                    session   = Session(session.balance, tp, sl, strat_key, base_bet)
                    fib_idx   = 0
                    fib_base  = 0.0
                    _fib_next = FIB_SEQ[0]

                write_bot_state("WAITING", session, strat_key, base_bet,
                                last_number, last_color, last_pnl,
                                tp_count, sl_count, fib_idx, _fib_next)
                state                = WAITING
                placed_pos           = []
                pending_result_num   = None
                pending_result_color = None

            time.sleep(0.15)

    except KeyboardInterrupt:
        print("\n\n  Stopped by user (Ctrl+C).")
    except pyautogui.FailSafeException:
        print("\n\n  Stopped by failsafe (mouse corner).")
    finally:
        # Final summary
        cum      = session.balance - start_bal
        cum_sign = "+" if cum >= 0 else "-"
        print()
        hr("=")
        print(f"  BOT STOPPED  —  {tp_count + sl_count} completed sessions "
              f"({tp_count} TP / {sl_count} SL), now in session {session_num}")
        hr()
        print(f"  Start balance : ${start_bal:.2f}")
        print(f"  End balance   : ${session.balance:.2f}")
        print(f"  Cumulative P&L: {cum_sign}${abs(cum):.2f}")
        hr()
        print("  Current session:")
        for line in session.summary_lines():
            print(line)
        hr("=")
        write_bot_state("STOPPED", session, strat_key, base_bet,
                        last_number, last_color, last_pnl,
                        tp_count, sl_count, fib_idx,
                        FIB_SEQ[min(fib_idx, MAX_FIB)])
        if overlay_proc:
            overlay_proc.terminate()
            print("  Panel stopped.")
        print()

if __name__ == "__main__":
    main()
