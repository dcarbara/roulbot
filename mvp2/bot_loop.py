"""
bot_loop.py — SpinEdge automated betting loop.

Launches the overlay, then watches the game state and places bets
according to the selected strategy when PLACE YOUR BETS is active.

Usage:
    python bot_loop.py              # interactive: prompts for strategy
    python bot_loop.py S1           # use S1 directly
    python bot_loop.py S1 --dry-run # log actions without clicking

Emergency stop: Ctrl+C in terminal, OR move mouse to top-left corner.
"""
import sys, io, os, time, json, subprocess, threading
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import cv2, numpy as np, mss, pyautogui
from PIL import Image
import pytesseract

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
pyautogui.FAILSAFE  = True   # move mouse to top-left corner to emergency-stop
pyautogui.PAUSE     = 0.0    # we handle our own delays

_HERE       = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(_HERE, "coords.json")
PYTHON      = sys.executable

# ── Strategies ─────────────────────────────────────────────────────────────────
STRATEGIES = {
    "S1": {
        "name":      "Aggressive",
        "chip":      "chip_1",
        "positions": ["col1_btn", "col3_btn", "1st12", "red", "ds1"],
        "desc":      "Col1 + Col3 + 1st Dozen + Red + DS1-6",
    },
    "S2": {
        "name":      "Moderate",
        "chip":      "chip_1",
        "positions": ["col1_btn", "1st12", "3rd12", "odd", "ds1"],
        "desc":      "Col1 + 1st Dozen + 3rd Dozen + Odd + DS1-6",
    },
    "S3": {
        "name":      "Conservative",
        "chip":      "chip_0.50",
        "positions": ["red", "odd", "1-18", "19-36", "ds1", "ds25"],
        "desc":      "Red + Odd + 1-18 + 19-36 + DS1-6 + DS25-30",
    },
}

# ── Coords helpers ─────────────────────────────────────────────────────────────
_cache = {"data": None, "mtime": 0.0}

def load_coords():
    try:
        mt = os.path.getmtime(COORDS_FILE)
        if mt != _cache["mtime"]:
            with open(COORDS_FILE, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = mt
    except Exception as e:
        print(f"[WARN] coords load: {e}")
    return _cache.get("data") or {}

def get_scale(coords):
    meta = coords.get("_meta", {})
    sw = meta.get("image_w", 1920)
    sh = meta.get("image_h", 1080)
    with mss.mss() as sct:
        mon = sct.monitors[1]
    return mon["width"] / sw, mon["height"] / sh

def validate_strategy(key, coords):
    """Return list of missing coord keys for a strategy (empty = all OK)."""
    s      = STRATEGIES[key]
    needed = s["positions"] + [s["chip"]]
    return [k for k in needed
            if k not in coords
            or not isinstance(coords[k], (list, tuple))
            or len(coords[k]) != 2]

# ── Status detection ───────────────────────────────────────────────────────────
def _bg_color(img_bgr):
    H, W = img_bgr.shape[:2]
    s = img_bgr[:, :max(1, W//2)]
    b = float(s[:,:,0].mean())
    g = float(s[:,:,1].mean())
    r = float(s[:,:,2].mean())
    if   g > 80 and g > r*1.3 and g > b*1.3:  return "GREEN"
    elif r > 80 and r > g*1.3 and r > b*1.3:  return "RED"
    elif r > 80 and g > 60    and r > b*1.5:  return "YELLOW"
    else:                                       return "DARK"

def _ocr_status(img_bgr, bg):
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    big  = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    if bg in ("GREEN", "RED", "YELLOW"):
        _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        _, bw = cv2.threshold(big, 100, 255, cv2.THRESH_BINARY)
    txt = pytesseract.image_to_string(
        Image.fromarray(bw), config="--psm 7 --oem 3"
    ).strip().upper()
    if bg == "DARK" and len(txt) < 4:
        txt = ""
    return txt

# ── Bet placement ──────────────────────────────────────────────────────────────
def _click(sx, sy, label="", dry_run=False):
    if dry_run:
        print(f"    [DRY] click {label:16s} @ ({sx},{sy})")
        time.sleep(0.05)
        return
    pyautogui.moveTo(sx, sy, duration=0.10)
    time.sleep(0.05)
    pyautogui.click()
    print(f"    click {label:16s} @ ({sx},{sy})")
    time.sleep(0.08)

def place_bets(strat_key, coords, sx_m, sy_m, dry_run=False):
    s        = STRATEGIES[strat_key]
    chip_key = s["chip"]
    positions = s["positions"]

    # 1. Select chip denomination
    cx, cy = coords[chip_key]
    _click(int(cx*sx_m), int(cy*sy_m), label=chip_key, dry_run=dry_run)
    time.sleep(0.12)

    # 2. Click each bet position
    placed, skipped = [], []
    for pos in positions:
        val = coords.get(pos)
        if isinstance(val, (list, tuple)) and len(val) == 2:
            bx, by = val
            _click(int(bx*sx_m), int(by*sy_m), label=pos, dry_run=dry_run)
            placed.append(pos)
        else:
            skipped.append(pos)
            print(f"    [SKIP] {pos} not found in coords")

    return placed, skipped

# ── Status reader (bg-cached, OCR throttled) ───────────────────────────────────
class StatusReader:
    def __init__(self):
        self._bg       = None
        self._txt      = ""
        self._ocr_t    = 0.0
        self._ocr_ivl  = 0.9   # seconds between OCR calls

    def read(self, coords):
        sr = coords.get("_status_region")
        if not sr:
            return self._bg or "DARK", self._txt

        meta = coords.get("_meta", {})
        sw, sh = meta.get("image_w", 1920), meta.get("image_h", 1080)
        with mss.mss() as sct:
            mon  = sct.monitors[1]
            sx_m = mon["width"] / sw
            sy_m = mon["height"] / sh
            reg  = {
                "left":   int(sr["x"]*sx_m), "top":    int(sr["y"]*sy_m),
                "width":  int(sr["w"]*sx_m), "height": int(sr["h"]*sy_m),
            }
            shot = sct.grab(reg)

        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        bg  = _bg_color(img)
        now = time.time()

        # OCR on bg-color change or after interval
        if bg != self._bg or (now - self._ocr_t) >= self._ocr_ivl:
            self._txt   = _ocr_status(img, bg)
            self._ocr_t = now

        self._bg = bg
        return bg, self._txt

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv
    args    = [a for a in sys.argv[1:] if not a.startswith("-")]

    # ── Show header ────────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("  SpinEdge Bot Loop")
    if dry_run:
        print("  *** DRY-RUN MODE — no actual clicks ***")
    print("=" * 62)
    print()

    # ── Load coords and validate all strategies ─────────────────────────────────
    coords = load_coords()
    if not coords:
        print(f"[ERROR] Cannot load {COORDS_FILE}")
        sys.exit(1)

    print("  Available strategies:")
    print()
    all_valid = {}
    for key, s in STRATEGIES.items():
        missing = validate_strategy(key, coords)
        ok      = not missing
        all_valid[key] = ok
        status  = "OK" if ok else f"MISSING: {', '.join(missing)}"
        mark    = "[OK]  " if ok else "[WARN]"
        print(f"  {mark} {key}: {s['name']:15s}  chip={s['chip']}")
        print(f"         Bets : {', '.join(s['positions'])}")
        print(f"         Check: {status}")
        print()

    # ── Strategy selection ──────────────────────────────────────────────────────
    strat_key = args[0].upper() if args and args[0].upper() in STRATEGIES else None

    if strat_key and not all_valid[strat_key]:
        print(f"[ERROR] Strategy {strat_key} has missing coords. Fix coords.json first.")
        sys.exit(1)

    while strat_key not in STRATEGIES:
        valid_opts = [k for k,ok in all_valid.items() if ok]
        prompt = f"Select strategy [{'/'.join(valid_opts)}]: "
        strat_key = input(prompt).strip().upper()
        if strat_key not in STRATEGIES:
            print(f"  Invalid. Choose from: {', '.join(STRATEGIES.keys())}")
        elif not all_valid[strat_key]:
            print(f"  {strat_key} has missing coords — choose another or fix coords.json.")
            strat_key = None

    s = STRATEGIES[strat_key]
    print(f"[OK] Running {strat_key}: {s['name']}")
    print(f"     Chip: {s['chip']}  |  Bets: {', '.join(s['positions'])}")
    print()

    # ── Launch overlay ──────────────────────────────────────────────────────────
    overlay_path = os.path.join(_HERE, "overlay_live.py")
    if os.path.exists(overlay_path):
        print("[*] Starting overlay...")
        overlay_proc = subprocess.Popen(
            [PYTHON, overlay_path, strat_key],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        time.sleep(3)  # let overlay initialize
        print("[*] Overlay started.")
    else:
        overlay_proc = None
        print("[WARN] overlay_live.py not found — running without overlay")

    print()
    print("Watching game state. Ctrl+C or move mouse to top-left to stop.")
    print()
    print(f"  {'Time':8s}  {'Round':5s}  {'State':10s}  {'BG':6s}  {'Status text'}")
    print("  " + "-" * 60)

    # ── State machine ───────────────────────────────────────────────────────────
    WAITING = "WAITING"
    PLACED  = "PLACED"

    state     = WAITING
    round_num = 0
    reader    = StatusReader()

    try:
        while True:
            coords     = load_coords()
            sx_m, sy_m = get_scale(coords)
            bg, txt    = reader.read(coords)
            ts         = time.strftime("%H:%M:%S")

            if state == WAITING:
                print(f"\r  {ts}  {'---':5s}  {state:10s}  {bg:6s}  {txt[:35]:35s}",
                      end="", flush=True)

                if bg == "GREEN" and "PLACE YOUR BETS" in txt:
                    round_num += 1
                    print()  # newline after the \r
                    print()
                    print(f"  {'='*58}")
                    print(f"  ROUND {round_num} — {txt}")
                    print(f"  Placing bets for {strat_key} ({s['name']})...")
                    placed, skipped = place_bets(strat_key, coords, sx_m, sy_m,
                                                 dry_run=dry_run)
                    print(f"  Placed : {placed}")
                    if skipped:
                        print(f"  Skipped: {skipped}")
                    state = PLACED
                    print()

            elif state == PLACED:
                print(f"\r  {ts}  {str(round_num):5s}  {state:10s}  {bg:6s}  {txt[:35]:35s}",
                      end="", flush=True)

                # Return to WAITING after result phase ends
                if bg == "DARK" and txt == "":
                    # No status shown — between rounds
                    state = WAITING
                elif bg == "DARK" and any(w in txt for w in
                        ("NEXT GAME", "BLACK", "RED", "GREEN", "WHITE")):
                    print()
                    print(f"  Result: {txt}")
                    state = WAITING
                elif bg == "GREEN" and "PLACE YOUR BETS" in txt:
                    # New round started before we caught the result — reset
                    print()
                    print(f"  [WARN] New round detected while in PLACED state — resetting")
                    state = WAITING

            time.sleep(0.15)

    except KeyboardInterrupt:
        print("\n\n  Bot stopped (Ctrl+C).")
    except pyautogui.FailSafeException:
        print("\n\n  Bot stopped (mouse moved to corner — failsafe).")
    finally:
        if overlay_proc:
            print("  Stopping overlay...")
            overlay_proc.terminate()
        print("  Done.")

if __name__ == "__main__":
    main()
