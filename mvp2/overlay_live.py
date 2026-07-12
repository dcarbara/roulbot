"""
overlay_live.py — Live transparent overlay with self-verification.

Draws directly ON TOP of the game (transparent fullscreen window).
Background detection thread runs OCR/CV every 0.5 s and annotates:
  - Number strip: each detected number with OCR confidence badge
  - Bet buttons: crosshairs (green = active strategy, orange = others)
  - Status region: live OCR text
  - Verification panel: per-component PASS/FAIL + overall health %

Usage:
  python overlay_live.py [S1|S2|S3]   strategy to highlight (default S1)
  Ctrl+C in terminal to quit.

Click-through: the overlay window passes mouse clicks to the game underneath.
"""

import sys, os, io, time, json, threading, ctypes, ctypes.wintypes, re
import numpy as np
import cv2
import mss
from PIL import Image, ImageTk
import tkinter as tk
import pytesseract
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE       = os.path.dirname(os.path.abspath(__file__))
SCRATCHPAD  = (r"C:\Users\hihi\AppData\Local\Temp\claude"
               r"\C--Users-hihi-Documents-spinedge-engine-main1-spinedge-engine-main"
               r"\a8ded6e1-2276-413c-ae5e-e1f43ba183ea\scratchpad")
_COORD_CANDIDATES = [
    os.path.join(_HERE, "coords.json"),
    os.path.join(SCRATCHPAD, "coords.json"),
]
COORDS_FILE = next((p for p in _COORD_CANDIDATES if os.path.exists(p)), None)

# ── Strategy config ────────────────────────────────────────────────────────────
STRATEGIES = {
    "S1": {"name":"Aggressive",   "positions":["col1_btn","col3_btn","1st12","red","ds1"]},
    "S2": {"name":"Moderate",     "positions":["col1_btn","1st12","3rd12","odd","ds1"]},
    "S3": {"name":"Conservative", "positions":["red","odd","1-18","19-36","ds1","ds25"]},
}
STRAT_KEY = next((a for a in sys.argv[1:] if a in STRATEGIES), "S1")
STRAT_POSITIONS = set(STRATEGIES[STRAT_KEY]["positions"])

# ── Calibrated strip constants ─────────────────────────────────────────────────
STRIP_SX   = 1310
STRIP_SY   = 254
STRIP_SW   = 600
STRIP_SH   = 24
BOX_IX1    = 43
BOX_IX2    = 71
BOX_IY1    = 7
BOX_IY2    = 22
BOX_MASK_X = 95
SCALE      = 8

# ── Roulette colours ───────────────────────────────────────────────────────────
RED_NUMS   = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
GREEN_NUMS = {0}

def pocket_bgr(n):
    if n is None:        return (100,100,100)
    if n in GREEN_NUMS:  return (0,200,0)
    if n in RED_NUMS:    return (60,60,220)
    return (180,180,180)

# ── OCR helpers ────────────────────────────────────────────────────────────────
def _ocr_one(ch_img):
    """Majority-vote OCR of a single-number crop. Returns (number, confidence 0-8)."""
    if ch_img is None or ch_img.ndim != 2 or ch_img.shape[1] < 8:
        return None, 0
    cfg  = "--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    H    = ch_img.shape[0]
    pad  = np.zeros((H, 30), dtype=np.uint8)
    votes = []
    for thr in [80, 110, 140, 170]:
        for inv in [False, True]:
            flag = cv2.THRESH_BINARY_INV if inv else cv2.THRESH_BINARY
            _, bw = cv2.threshold(ch_img, thr, 255, flag)
            padded = np.hstack([pad, bw, pad])
            txt = pytesseract.image_to_string(Image.fromarray(padded), config=cfg).strip()
            txt = re.sub(r"\D", "", txt)
            if txt and len(txt) <= 2:
                n = int(txt)
                if 0 <= n <= 36:
                    votes.append(n)
    if not votes:
        return None, 0
    best, cnt = Counter(votes).most_common(1)[0]
    return best, cnt  # confidence = vote count (max 8)

def _status_bg(img_bgr):
    """Classify status bar background color: GREEN, RED, YELLOW, or DARK."""
    # Sample left half (avoids right-aligned text area)
    H, W = img_bgr.shape[:2]
    s  = img_bgr[:, :W//2]
    b  = float(s[:,:,0].mean())
    g  = float(s[:,:,1].mean())
    r  = float(s[:,:,2].mean())
    if   g > 80  and g > r * 1.3 and g > b * 1.3:  return "GREEN"
    elif r > 80  and r > g * 1.3 and r > b * 1.3:  return "RED"
    elif r > 80  and g > 60 and r > b * 1.5:        return "YELLOW"
    else:                                             return "DARK"

def ocr_status_text(img_bgr):
    """Status OCR: adapts to background color for best text extraction."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    big  = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    bg   = _status_bg(img_bgr)

    if bg in ("GREEN", "RED", "YELLOW"):
        # White text on bright background → invert so text becomes black
        _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        # White/yellow text on dark background → extract bright pixels directly
        _, bw = cv2.threshold(big, 100, 255, cv2.THRESH_BINARY)

    txt = pytesseract.image_to_string(
        Image.fromarray(bw), config="--psm 7 --oem 3"
    ).strip().upper()

    # Strip OCR noise from dark-bg reads (single chars, symbols)
    if bg == "DARK" and len(txt) < 4:
        txt = ""
    return txt, bg

def ocr_dollar(img_bgr):
    """OCR a dollar amount region. Returns string like '$127.35' or ''."""
    gray   = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    _, bw  = cv2.threshold(scaled, 120, 255, cv2.THRESH_BINARY)
    txt = pytesseract.image_to_string(
        Image.fromarray(bw),
        config="--psm 6 --oem 3 -c tessedit_char_whitelist=0123456789.$,"
    ).strip()
    m = re.search(r'\$[\d\.]+', txt)
    return m.group(0) if m else ""

# ── Coords cache (avoid re-reading JSON every loop) ────────────────────────────
_coords_cache = {"data": None, "mtime": 0.0}

def _load_coords():
    if not COORDS_FILE:
        return {}
    try:
        mtime = os.path.getmtime(COORDS_FILE)
        if mtime != _coords_cache["mtime"]:
            with open(COORDS_FILE, encoding="utf-8") as f:
                _coords_cache["data"] = json.load(f)
            _coords_cache["mtime"] = mtime
    except Exception:
        pass
    return _coords_cache.get("data") or {}

# ── Fast status detection (runs in its own thread at 150 ms) ───────────────────
KNOWN_STATUS = ["PLACE YOUR BETS","BETS CLOSING","BETS CLOSED","NO MORE BETS",
                "SPINNING","WINNER","NEXT GAME","BLACK","RED","GREEN"]

_sts = {"bg": None, "text": "", "known": False, "region": None, "ocr_time": 0.0}

def detect_status_fast():
    global _sts
    coords = _load_coords()
    sr     = coords.get("_status_region")
    if not sr:
        return _sts

    meta = coords.get("_meta", {})
    sw   = meta.get("image_w", 1920)
    sh   = meta.get("image_h", 1080)

    with mss.mss() as sct:
        mon  = sct.monitors[1]
        sx_m = mon["width"] / sw
        sy_m = mon["height"] / sh
        reg  = {
            "left":   int(sr["x"] * sx_m), "top":    int(sr["y"] * sy_m),
            "width":  int(sr["w"] * sx_m), "height": int(sr["h"] * sy_m),
        }
        shot = sct.grab(reg)

    simg      = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    bg        = _status_bg(simg)
    now       = time.time()
    scrn_reg  = {
        "x1": reg["left"],  "y1": reg["top"],
        "x2": reg["left"] + reg["width"],
        "y2": reg["top"]  + reg["height"],
    }

    # Run OCR on bg-color change (instant state transition) OR every 1 s (countdown)
    if bg != _sts["bg"] or (now - _sts["ocr_time"]) >= 1.0:
        txt, _  = ocr_status_text(simg)
        known   = any(k in txt for k in KNOWN_STATUS)
        _sts    = {"bg": bg, "text": txt, "known": known,
                   "region": scrn_reg, "ocr_time": now}
    else:
        _sts["region"] = scrn_reg
    return _sts

# ── Heavy detection (strip + bets + balance, 500 ms) ──────────────────────────
def detect_all():
    """Strip + bets + balance detection (500 ms loop). Status is handled separately."""
    result = {"strip": [], "status": {}, "bets": {}, "health": {},
              "balance": {"text": "", "region": None},
              "total_bet": {"text": "", "region": None}}

    coords = _load_coords()
    meta   = coords.get("_meta", {})
    sw     = meta.get("image_w", 1920)
    sh     = meta.get("image_h", 1080)

    with mss.mss() as sct:
        mon  = sct.monitors[1]
        sx_m = mon["width"] / sw
        sy_m = mon["height"] / sh

        # ── Strip ────────────────────────────────────────────────────────────────
        try:
            shot = sct.grab({"left":STRIP_SX,"top":STRIP_SY,
                             "width":STRIP_SW,"height":STRIP_SH})
            img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

            inner    = img[BOX_IY1:BOX_IY2, BOX_IX1:BOX_IX2]
            inner_mc = cv2.max(inner[:,:,0], cv2.max(inner[:,:,1], inner[:,:,2]))
            inner_8x = cv2.resize(inner_mc, None, fx=SCALE, fy=SCALE,
                                  interpolation=cv2.INTER_CUBIC)
            n, conf  = _ocr_one(inner_8x)
            result["strip"].append({
                "number":n,"confidence":conf,"is_recent":True,
                "sx1":STRIP_SX+BOX_IX1-2,"sy1":STRIP_SY,
                "sx2":STRIP_SX+BOX_IX2+2,"sy2":STRIP_SY+STRIP_SH,
            })

            s8    = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
            H8    = s8.shape[0]
            mc8   = cv2.max(s8[:,:,0], cv2.max(s8[:,:,1], s8[:,:,2]))
            _, bw = cv2.threshold(mc8, 130, 255, cv2.THRESH_BINARY)
            bw[:, :BOX_MASK_X*SCALE] = 0
            _, _, stats, _ = cv2.connectedComponentsWithStats(bw)
            blobs = sorted(
                [(stats[i][0], stats[i][0]+stats[i][2])
                 for i in range(1, len(stats))
                 if stats[i][3]>=35 and stats[i][4]>=80
                 and stats[i][3]<H8*0.85 and stats[i][2]<=100]
            )
            groups = []
            for x1,x2 in blobs:
                if groups and x1-groups[-1][1]<=20:
                    groups[-1] = (groups[-1][0], max(x2,groups[-1][1]))
                else:
                    groups.append([x1,x2])
            for x1,x2 in groups[:14]:
                crop   = mc8[:, max(0,x1-15):x2+15]
                n,conf = _ocr_one(crop)
                result["strip"].append({
                    "number":n,"confidence":conf,"is_recent":False,
                    "sx1":max(STRIP_SX, STRIP_SX+(x1-15)//SCALE),
                    "sy1":STRIP_SY,
                    "sx2":min(STRIP_SX+STRIP_SW, STRIP_SX+(x2+15)//SCALE),
                    "sy2":STRIP_SY+STRIP_SH,
                })
        except Exception:
            result["strip"] = []

        # ── Balance / Total Bet OCR ───────────────────────────────────────────────
        for rkey, dkey in [("_balance_region","balance"),
                           ("_total_bet_region","total_bet")]:
            r = coords.get(rkey)
            if not r:
                continue
            reg = {
                "left":   int(r["x"] * sx_m), "top":    int(r["y"] * sy_m),
                "width":  int(r["w"] * sx_m), "height": int(r["h"] * sy_m),
            }
            try:
                shot = sct.grab(reg)
                simg = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
                txt  = ocr_dollar(simg)
                result[dkey] = {
                    "text": txt,
                    "region": {
                        "x1": reg["left"],  "y1": reg["top"],
                        "x2": reg["left"] + reg["width"],
                        "y2": reg["top"]  + reg["height"],
                    },
                }
            except Exception:
                pass

    # ── Bet positions (no screenshot needed) ─────────────────────────────────────
    skip = {"_meta","_status_region","_last_number_region"}
    for key, val in coords.items():
        if key in skip:
            continue
        if isinstance(val, (list,tuple)) and len(val) == 2:
            result["bets"][key] = {
                "cx": int(val[0] * sx_m),
                "cy": int(val[1] * sy_m),
                "in_strategy": key in STRAT_POSITIONS,
            }

    # ── Health ───────────────────────────────────────────────────────────────────
    valid_strip = [e for e in result["strip"] if e["number"] is not None
                   and 0 <= e["number"] <= 36]
    strip_ok = len(valid_strip) >= 8
    bets_ok  = len(result["bets"]) >= 5
    score    = int(((1 if strip_ok else 0) + (1 if bets_ok else 0)) / 2 * 100)
    result["health"] = {
        "strip_ok": strip_ok, "strip_n": len(valid_strip),
        "status_ok": True,    "bets_ok": bets_ok, "score": score,
    }
    return result

# ── Render frame onto a black canvas (black = transparent chroma key) ──────────
CHROMA = (0, 0, 0)          # pure black → transparent
FONT   = cv2.FONT_HERSHEY_SIMPLEX

def _box(img, x1,y1,x2,y2, color, thick=2):
    cv2.rectangle(img,(x1,y1),(x2,y2),color,thick)

def _label(img, x,y, text, color, scale=0.42, thick=1, bg=True):
    (tw,th),_ = cv2.getTextSize(text, FONT, scale, thick)
    if bg:
        cv2.rectangle(img,(x-1,y-th-2),(x+tw+2,y+2),(10,10,10),-1)
    cv2.putText(img, text,(x,y), FONT, scale, color, thick, cv2.LINE_AA)

def _cross(img, cx,cy, color, size=12, thick=2):
    cv2.line(img,(cx-size,cy),(cx+size,cy),color,thick)
    cv2.line(img,(cx,cy-size),(cx,cy+size),color,thick)

def render_frame(data, W, H):
    frame = np.zeros((H, W, 3), dtype=np.uint8)  # all black = transparent

    # ── Title bar ─────────────────────────────────────────────────────────────
    h = data.get("health",{})
    score = h.get("score",0)
    score_col = (0,220,0) if score==100 else (0,200,200) if score>=66 else (0,80,220)
    ts  = time.strftime("%H:%M:%S")
    nums = " ".join(str(e["number"]) if e["number"] is not None else "?"
                    for e in data.get("strip",[])[:7])
    title = f"SpinEdge Live [{ts}]  Health:{score}%  |  Strip: {nums} ..."
    cv2.rectangle(frame,(0,0),(W,26),(15,15,15),-1)
    _label(frame,6,18,title,(200,200,200),scale=0.44,bg=False)

    # ── Number strip region ────────────────────────────────────────────────────
    _box(frame, STRIP_SX-3,STRIP_SY-3, STRIP_SX+STRIP_SW+3,STRIP_SY+STRIP_SH+3,
         (0,220,220), thick=1)
    _label(frame, STRIP_SX, STRIP_SY-16, "STRIP", (0,220,220), scale=0.38)

    for i, e in enumerate(data.get("strip",[])):
        n     = e["number"]
        x1,y1 = e["sx1"], e["sy1"]
        x2,y2 = e["sx2"], e["sy2"]
        conf  = e.get("confidence",0)
        col   = pocket_bgr(n)
        thick = 2 if e["is_recent"] else 1

        _box(frame, x1,y1,x2,y2, col, thick)

        # Confidence bar below box (green = high, red = low)
        bar_w = max(0, int((x2-x1) * conf/8))
        bar_col = (0,200,0) if conf>=5 else (0,180,220) if conf>=3 else (0,80,220)
        cv2.rectangle(frame,(x1,y2+2),(x1+bar_w,y2+5),bar_col,-1)

        lbl = f"{n if n is not None else '?'}"
        if e["is_recent"]:
            lbl = f">{n}<"
        _label(frame, x1, y2+18, lbl, col, scale=0.4)

    # ── Bet positions ──────────────────────────────────────────────────────────
    for key, b in data.get("bets",{}).items():
        cx,cy = b["cx"], b["cy"]
        in_s  = b["in_strategy"]

        if key.startswith("num_"):
            pass  # hidden
        elif key.startswith("cr_"):
            # Corner bets: small cyan diamond
            col  = (0,220,220)
            size = 6
            cv2.line(frame,(cx-size,cy),(cx+size,cy),col,1)
            cv2.line(frame,(cx,cy-size),(cx,cy+size),col,1)
            pts = np.array([[cx,cy-size],[cx+size,cy],[cx,cy+size],[cx-size,cy]])
            cv2.polylines(frame,[pts],True,col,1)
        elif key.startswith("chip_") or key.startswith("btn_"):
            # Chip tray: circle + label
            col  = (0,220,255) if key.startswith("chip_") else (180,180,180)
            r    = 18
            cv2.circle(frame,(cx,cy),r,col,1)
            _cross(frame,cx,cy,col,size=6,thick=1)
            _label(frame,cx-14,cy+r+12, key.replace("chip_","").replace("btn_",""),
                   col, scale=0.38)
        else:
            col   = (0,255,0) if in_s else (0,140,255)
            size  = 10
            thick = 1
            _cross(frame, cx,cy, col, size=size, thick=thick)

    # ── Status region ──────────────────────────────────────────────────────────
    sr  = data.get("status",{})
    reg = sr.get("region")
    if reg:
        bg_type = sr.get("bg", "DARK")
        if   bg_type == "GREEN":  box_col = (0, 220, 0)
        elif bg_type == "RED":    box_col = (0, 60, 220)
        elif bg_type == "YELLOW": box_col = (0, 200, 220)
        else:                     box_col = (160, 160, 160)
        _box(frame, reg["x1"],reg["y1"],reg["x2"],reg["y2"], box_col, thick=2)
        status_txt = sr.get("text","")
        if status_txt:
            _label(frame, reg["x1"], reg["y1"]-6,
                   status_txt[:35], box_col, scale=0.45)

    # ── Balance / Total Bet overlay ───────────────────────────────────────────
    bal  = data.get("balance",  {})
    tbet = data.get("total_bet", {})
    for entry, col, label in [
        (bal,  (0, 210, 120), "BAL"),
        (tbet, (0, 220, 255), "BET"),
    ]:
        r = entry.get("region")
        txt = entry.get("text", "")
        if r:
            _box(frame, r["x1"], r["y1"], r["x2"], r["y2"], col, thick=1)
        if txt:
            rx = r["x1"] if r else 10
            ry = r["y1"] if r else H - 200
            _label(frame, rx, ry - 5, f"{label}: {txt}", col, scale=0.44)

    # Mini info panel (top of chip tray area, left side)
    if bal.get("text") or tbet.get("text"):
        px, py = 470, 800
        bw = 200
        cv2.rectangle(frame, (px-4, py-18), (px+bw, py+26), (20,20,20), -1)
        cv2.rectangle(frame, (px-4, py-18), (px+bw, py+26), (50,50,50), 1)
        _label(frame, px, py,
               f"BALANCE  {bal.get('text','--')}",
               (0, 210, 120), scale=0.45, bg=False)
        _label(frame, px, py+20,
               f"TOTAL BET {tbet.get('text','--')}",
               (0, 220, 255), scale=0.45, bg=False)

    # ── Verification panel (bottom-right corner) ───────────────────────────────
    px,py = W-260, H-130
    cv2.rectangle(frame,(px-6,py-20),(W-4,H-6),(20,20,20),-1)
    cv2.rectangle(frame,(px-6,py-20),(W-4,H-6),(60,60,60),1)

    def check_line(label, ok, detail=""):
        nonlocal py
        col = (0,200,0) if ok else (0,80,220)
        sym = "OK" if ok else "!!"
        _label(frame, px,py, f"[{sym}] {label} {detail}", col, scale=0.42, bg=False)
        py += 20

    check_line("Strip numbers",  h.get("strip_ok"),  f"{h.get('strip_n',0)}/13")
    check_line("Status OCR",     h.get("status_ok"), sr.get("text","")[:15])
    check_line("Bet positions",  h.get("bets_ok"),   f"{len(data.get('bets',{}))} loaded")
    check_line(f"Strategy {STRAT_KEY}", True,
               " ".join(STRATEGIES[STRAT_KEY]["positions"][:3])+"...")

    overall = h.get("score",0)
    col = (0,200,0) if overall==100 else (0,200,200) if overall>=66 else (0,80,220)
    py += 4
    _label(frame, px,py, f"Overall health: {overall}%", col, scale=0.48, bg=False)

    # ── Legend (bottom-left) ───────────────────────────────────────────────────
    items = [
        ((0,220,220),  "Strip region"),
        ((60,60,220),  "Red number"),
        ((180,180,180),"Black number"),
        ((0,200,0),    "Green (0) / PASS"),
        ((0,255,0),    f"Active S{STRAT_KEY} bet"),
        ((0,140,255),  "Other bet / WARN"),
    ]
    lx,ly = 8, H-10 - len(items)*20
    cv2.rectangle(frame,(lx-4,ly-16),(180,H-2),(20,20,20),-1)
    for col,txt in items:
        cv2.rectangle(frame,(lx,ly-11),(lx+12,ly+2),col,-1)
        _label(frame, lx+16,ly, txt, (200,200,200), scale=0.38, bg=False)
        ly += 20

    return frame

# ── Win32 click-through helper ─────────────────────────────────────────────────
def set_click_through(hwnd):
    GWL_EXSTYLE     = -20
    WS_EX_LAYERED   = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
    )

# ── Main overlay class ─────────────────────────────────────────────────────────
class LiveOverlay:
    def __init__(self):
        with mss.mss() as sct:
            m = sct.monitors[1]
            self.W, self.H = m["width"], m["height"]

        self._data   = {}
        self._lock   = threading.Lock()
        self._run    = True

        # Slow detection thread (strip, bets, balance — 500 ms)
        t = threading.Thread(target=self._detect_loop, daemon=True)
        t.start()
        # Fast status thread (150 ms, bg-color cached)
        ts = threading.Thread(target=self._status_loop, daemon=True)
        ts.start()

        # Build window
        self.root = tk.Tk()
        self.root.title("SpinEdge Live Overlay")
        self.root.geometry(f"{self.W}x{self.H}+0+0")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", "black")

        self.canvas = tk.Canvas(self.root, bg="black",
                                highlightthickness=0,
                                width=self.W, height=self.H)
        self.canvas.pack()
        self._photo = None

        # Make click-through after window is mapped
        self.root.after(200, self._apply_click_through)
        self.root.after(300, self._update_ui)
        print(f"Overlay started ({self.W}x{self.H}). Ctrl+C in terminal to quit.")
        self.root.mainloop()

    def _apply_click_through(self):
        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, "SpinEdge Live Overlay")
            if hwnd:
                set_click_through(hwnd)
        except Exception as e:
            print(f"[WARN] click-through failed: {e}")

    def _detect_loop(self):
        while self._run:
            try:
                d = detect_all()
            except Exception:
                d = {"strip":[],"bets":{},"health":{"score":0},
                     "balance":{"text":"","region":None},
                     "total_bet":{"text":"","region":None}}
            with self._lock:
                # Preserve status — updated by the faster _status_loop
                d["status"] = self._data.get("status", {})
                self._data = d
            time.sleep(0.5)

    def _status_loop(self):
        while self._run:
            try:
                s = detect_status_fast()
            except Exception:
                s = {"bg":"DARK","text":"","known":False,"region":None,"ocr_time":0.0}
            with self._lock:
                self._data["status"] = s
            time.sleep(0.15)

    def _update_ui(self):
        if not self._run:
            return
        with self._lock:
            data = dict(self._data)

        frame = render_frame(data, self.W, self.H)

        # Convert BGR → RGB → PIL → PhotoImage
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil   = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(pil)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self._photo = photo  # prevent GC

        self.root.after(500, self._update_ui)

    def stop(self):
        self._run = False
        self.root.quit()


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        LiveOverlay()
    except KeyboardInterrupt:
        print("\nOverlay stopped.")
