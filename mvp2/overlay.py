"""
overlay.py — Visual verification of all detected UI elements.

Draws on a full screenshot:
  - Number history strip (each number box + label)
  - All bet button positions from coords.json
  - Status region
  - Calibration constants from ocr_strip.py

Run:
  python overlay.py [strategy]   (default S1)
  python overlay.py --loop       (refresh every 3s, press Q to quit)
"""

import sys, io, os, json, time, subprocess
import mss
import cv2
import numpy as np
from collections import Counter
import pytesseract
import re
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Paths ──────────────────────────────────────────────────────────────────────
MVp2_DIR    = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(MVp2_DIR, "..", "scratchpad", "coords.json")
# Try local coords.json too
if not os.path.exists(COORDS_FILE):
    COORDS_FILE = os.path.join(MVp2_DIR, "coords.json")
SCRATCHPAD  = r"C:\Users\hihi\AppData\Local\Temp\claude\C--Users-hihi-Documents-spinedge-engine-main1-spinedge-engine-main\a8ded6e1-2276-413c-ae5e-e1f43ba183ea\scratchpad"
COORDS_FILE_ALT = os.path.join(SCRATCHPAD, "coords.json")
if not os.path.exists(COORDS_FILE):
    COORDS_FILE = COORDS_FILE_ALT
OUT_FILE    = os.path.join(MVp2_DIR, "overlay.png")

# ── Calibrated strip constants (from ocr_strip.py) ────────────────────────────
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

# ── Roulette color helpers ────────────────────────────────────────────────────
RED_NUMS   = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
GREEN_NUMS = {0}

def num_color(n):
    """Return BGR color representing the roulette pocket color."""
    if n in GREEN_NUMS: return (0, 180, 0)
    if n in RED_NUMS:   return (60, 60, 220)
    return (80, 80, 80)   # black number → dark gray in BGR

# ── Strip detection (returns numbers + screen positions) ──────────────────────
def _ocr_one(ch_img):
    if ch_img is None or ch_img.ndim != 2 or ch_img.shape[1] < 8:
        return None
    cfg = "--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    H   = ch_img.shape[0]
    pad = np.zeros((H, 30), dtype=np.uint8)
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
    return Counter(votes).most_common(1)[0][0] if votes else None


def detect_strip():
    """
    Returns list of dicts:
      { 'number': int, 'sx1': int, 'sy1': int, 'sx2': int, 'sy2': int,
        'is_recent': bool }
    sx/sy are screen coordinates (absolute pixels).
    """
    with mss.mss() as sct:
        shot = sct.grab({"left": STRIP_SX, "top": STRIP_SY,
                         "width": STRIP_SW, "height": STRIP_SH})
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    results = []

    # --- Highlighted box (most recent number) ---
    inner = img[BOX_IY1:BOX_IY2, BOX_IX1:BOX_IX2]
    inner_mc = cv2.max(inner[:, :, 0], cv2.max(inner[:, :, 1], inner[:, :, 2]))
    inner_8x = cv2.resize(inner_mc, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    n = _ocr_one(inner_8x)
    results.append({
        "number":    n,
        "sx1":       STRIP_SX + BOX_IX1 - 2,
        "sy1":       STRIP_SY,
        "sx2":       STRIP_SX + BOX_IX2 + 2,
        "sy2":       STRIP_SY + STRIP_SH,
        "is_recent": True,
    })

    # --- Remaining numbers via blob detection ---
    s8 = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    H8 = s8.shape[0]
    maxch8 = cv2.max(s8[:, :, 0], cv2.max(s8[:, :, 1], s8[:, :, 2]))
    _, bw8 = cv2.threshold(maxch8, 130, 255, cv2.THRESH_BINARY)
    bw8[:, : BOX_MASK_X * SCALE] = 0

    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(bw8)
    blobs = [
        (stats[i][0], stats[i][0] + stats[i][2])
        for i in range(1, n_labels)
        if stats[i][3] >= 35 and stats[i][4] >= 80
        and stats[i][3] < H8 * 0.85 and stats[i][2] <= 100
    ]
    blobs.sort()

    groups = []
    for x1, x2 in blobs:
        if groups and x1 - groups[-1][1] <= 20:
            groups[-1] = (groups[-1][0], max(x2, groups[-1][1]))
        else:
            groups.append([x1, x2])

    for x1, x2 in groups[:14]:
        crop = maxch8[:, max(0, x1 - 15): x2 + 15]
        n = _ocr_one(crop)
        # Convert 8x coords back to screen
        sx1 = STRIP_SX + (x1 - 15) // SCALE
        sx2 = STRIP_SX + (x2 + 15) // SCALE
        results.append({
            "number":    n,
            "sx1":       max(STRIP_SX, sx1),
            "sy1":       STRIP_SY,
            "sx2":       min(STRIP_SX + STRIP_SW, sx2),
            "sy2":       STRIP_SY + STRIP_SH,
            "is_recent": False,
        })

    return results


# ── Drawing helpers ────────────────────────────────────────────────────────────
FONT       = cv2.FONT_HERSHEY_SIMPLEX
FONT_SMALL = 0.45
FONT_MED   = 0.55
THICK      = 1

def draw_labeled_box(img, x1, y1, x2, y2, label, color, thickness=2, text_above=True):
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness)
    (tw, th), _ = cv2.getTextSize(label, FONT, FONT_SMALL, THICK)
    tx = x1
    ty = y1 - 4 if text_above else y2 + th + 4
    # background pill for readability
    cv2.rectangle(img, (tx - 1, ty - th - 2), (tx + tw + 2, ty + 2), (0, 0, 0), -1)
    cv2.putText(img, label, (tx, ty), FONT, FONT_SMALL, color, THICK, cv2.LINE_AA)

def draw_crosshair(img, x, y, label, color, size=10):
    cv2.line(img, (x - size, y), (x + size, y), color, 1)
    cv2.line(img, (x, y - size), (x, y + size), color, 1)
    cv2.circle(img, (x, y), 4, color, -1)
    (tw, th), _ = cv2.getTextSize(label, FONT, FONT_SMALL, THICK)
    bx, by = x + 6, y - 4
    cv2.rectangle(img, (bx - 1, by - th - 1), (bx + tw + 1, by + 2), (0, 0, 0), -1)
    cv2.putText(img, label, (bx, by), FONT, FONT_SMALL, color, THICK, cv2.LINE_AA)


# ── Main render function ───────────────────────────────────────────────────────
def render_overlay(strategy_key="S1"):
    print("Taking screenshot...", end=" ", flush=True)
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    print("done")

    # Semi-transparent dark tint on the whole frame (makes labels readable)
    overlay = frame.copy()

    # ── 1. Strip container ────────────────────────────────────────────────────
    draw_labeled_box(
        overlay,
        STRIP_SX - 2, STRIP_SY - 2,
        STRIP_SX + STRIP_SW + 2, STRIP_SY + STRIP_SH + 2,
        "NUMBER STRIP", (0, 220, 220), thickness=1, text_above=True,
    )

    # ── 2. Individual strip numbers ───────────────────────────────────────────
    print("Detecting strip numbers...", end=" ", flush=True)
    strip_entries = detect_strip()
    print(f"{len(strip_entries)} entries")

    for i, e in enumerate(strip_entries):
        n   = e["number"]
        x1, y1, x2, y2 = e["sx1"], e["sy1"], e["sx2"], e["sy2"]
        col = num_color(n) if n is not None else (128, 128, 128)
        border_thick = 2 if e["is_recent"] else 1

        cv2.rectangle(overlay, (x1, y1), (x2, y2), col, border_thick)

        label = f"#{i} {n if n is not None else '?'}"
        if e["is_recent"]:
            label = f"RECENT {n if n is not None else '?'}"
        (tw, th), _ = cv2.getTextSize(label, FONT, FONT_SMALL, THICK)
        tx = x1
        ty = y2 + th + 5  # draw BELOW the strip (strip is at top of screen)
        cv2.rectangle(overlay, (tx - 1, ty - th - 1), (tx + tw + 1, ty + 2), (0, 0, 0), -1)
        cv2.putText(overlay, label, (tx, ty), FONT, FONT_SMALL, col, THICK, cv2.LINE_AA)

    # ── 3. Coords-based elements ──────────────────────────────────────────────
    if os.path.exists(COORDS_FILE):
        with open(COORDS_FILE) as f:
            coords = json.load(f)

        meta    = coords.get("_meta", {})
        img_w   = meta.get("image_w", 1920)
        img_h   = meta.get("image_h", 1080)
        h, w    = frame.shape[:2]
        sx_mult = w / img_w
        sy_mult = h / img_h

        SKIP = {"_meta", "_status_region", "_last_number_region"}

        bet_colors = {
            "col1_btn": (255, 140, 0),  "col2_btn": (255, 140, 0),  "col3_btn": (255, 140, 0),
            "1st12":    (200, 80,  200), "2nd12":    (200, 80,  200), "3rd12":    (200, 80,  200),
            "red":      (80,  80,  220), "black":    (120, 120, 120),
            "odd":      (100, 200, 100), "even":     (100, 200, 100),
            "1-18":     (200, 200, 50),  "19-36":    (200, 200, 50),
            "ds1":      (0,   200, 200), "ds7":      (0,   200, 200),
            "ds13":     (0,   200, 200), "ds19":     (0,   200, 200),
            "ds25":     (0,   200, 200), "ds31":     (0,   200, 200),
        }

        # Strategy positions highlighted differently
        STRAT_POSITIONS = {
            "S1": ["col1_btn","col3_btn","1st12","red","ds1"],
            "S2": ["col1_btn","1st12","3rd12","odd","ds1"],
            "S3": ["red","odd","1-18","19-36","ds1","ds25"],
        }.get(strategy_key, [])

        for key, val in coords.items():
            if key in SKIP:
                continue
            if isinstance(val, (list, tuple)) and len(val) == 2:
                cx = int(val[0] * sx_mult)
                cy = int(val[1] * sy_mult)
                in_strat = key in STRAT_POSITIONS
                color = (0, 255, 0) if in_strat else bet_colors.get(key, (180, 180, 180))
                size  = 14 if in_strat else 9
                thick = 2  if in_strat else 1
                draw_crosshair(overlay, cx, cy, key, color, size=size)
                if in_strat:
                    cv2.circle(overlay, (cx, cy), size + 4, color, thick)

        # Status region
        sr = coords.get("_status_region")
        if sr:
            sx1 = int(sr["x"] * sx_mult)
            sy1 = int(sr["y"] * sy_mult)
            sx2 = int((sr["x"] + sr["w"]) * sx_mult)
            sy2 = int((sr["y"] + sr["h"]) * sy_mult)
            draw_labeled_box(overlay, sx1, sy1, sx2, sy2, "STATUS", (0, 140, 255), thickness=1)

    else:
        print(f"[WARN] coords.json not found at {COORDS_FILE}")

    # ── 4. Legend ─────────────────────────────────────────────────────────────
    legend_items = [
        ((0, 220, 220), "Strip region"),
        ((220, 80, 80),  "Red number"),
        ((80, 80, 80),   "Black number"),
        ((0, 180, 0),    "Green (0)"),
        ((0, 255, 0),    f"Strategy {strategy_key} bet positions"),
        ((255, 140, 0),  "Other bet positions"),
        ((0, 140, 255),  "Status region"),
    ]
    lx, ly = 10, frame.shape[0] - 10 - len(legend_items) * 22
    for color, text in legend_items:
        cv2.rectangle(overlay, (lx, ly - 12), (lx + 14, ly + 2), color, -1)
        cv2.putText(overlay, text, (lx + 18, ly), FONT, FONT_SMALL, (240, 240, 240), 1, cv2.LINE_AA)
        ly += 22

    # ── 5. Title bar ──────────────────────────────────────────────────────────
    ts = time.strftime("%H:%M:%S")
    nums_str = " | ".join(
        str(e["number"]) if e["number"] is not None else "?" for e in strip_entries[:7]
    )
    title = f"SpinEdge Overlay  [{ts}]  Strip: {nums_str} ..."
    cv2.putText(overlay, title, (10, 22), FONT, FONT_MED, (240, 240, 50), 1, cv2.LINE_AA)

    # Blend with original (0.75 original, 0.25 overlay for subtle tint)
    out = cv2.addWeighted(frame, 0.55, overlay, 0.45, 0)
    # But keep original brightness in the overlay boxes — just blend labels
    # Actually simpler: just use overlay directly
    out = overlay

    return out


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loop_mode = "--loop" in sys.argv
    strat = next((a for a in sys.argv[1:] if a in ("S1","S2","S3")), "S1")

    if loop_mode:
        print(f"Loop mode — refreshing every 3s. Press Q in the window to quit.")
        while True:
            img = render_overlay(strat)
            small = cv2.resize(img, (1280, 720))
            cv2.imshow("SpinEdge Overlay", small)
            key = cv2.waitKey(3000) & 0xFF
            if key == ord('q') or key == 27:
                break
        cv2.destroyAllWindows()
    else:
        img = render_overlay(strat)
        cv2.imwrite(OUT_FILE, img)
        print(f"Saved: {OUT_FILE}")
        # Open with default Windows viewer
        try:
            os.startfile(OUT_FILE)
        except Exception:
            subprocess.Popen(["explorer", OUT_FILE])
