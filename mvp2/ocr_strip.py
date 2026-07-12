"""
Strip OCR module — reads the number history strip from Stake.com live roulette.

Calibrated once. Coordinates are relative to SX,SY strip region:
  Strip:        screen x=1310, y=254, w=600, h=24
  Highlight box inner region: x_offset=43-71, y_rows=7-22 (within the 24-row strip)

Usage:
  from ocr_strip import read_strip
  nums = read_strip()   # [most_recent, ...older...]
"""

import mss
import cv2
import numpy as np
import pytesseract
import re
from PIL import Image
from collections import Counter

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Calibrated strip coordinates (screen pixels at 1x)
STRIP_SX   = 1310
STRIP_SY   = 254
STRIP_SW   = 600
STRIP_SH   = 24

# Highlighted box (most recent number) inner region, relative to strip
BOX_IX1    = 43    # x offset into strip
BOX_IX2    = 71
BOX_IY1    = 7     # y row offset into strip
BOX_IY2    = 22
BOX_MASK_X = 95    # mask first 95px of strip before blob detection (excludes box border)

SCALE      = 8     # upscale factor for OCR
MIN_BLOB_H = 35    # min blob height at 8x to be considered a digit


def _ocr_crop(ch_img):
    """OCR a single-number crop (max-channel or gray). Returns int 0-36 or None."""
    if ch_img is None or ch_img.ndim != 2 or ch_img.shape[1] < 8:
        return None
    cfg = "--psm 8 --oem 3 -c tessedit_char_whitelist=0123456789"
    H = ch_img.shape[0]
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


def read_strip(sx=STRIP_SX, sy=STRIP_SY):
    """
    Read the number history strip.
    sx, sy: top-left of the strip on screen (allows re-anchoring if game window moves).
    Returns list [most_recent, ..., oldest], filtering None values.
    """
    with mss.mss() as sct:
        shot = sct.grab({"left": sx, "top": sy, "width": STRIP_SW, "height": STRIP_SH})
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)

    # --- Most recent number: inner highlighted box at 1x, scale 8x ---
    inner = img[BOX_IY1:BOX_IY2, BOX_IX1:BOX_IX2]
    inner_mc = cv2.max(inner[:, :, 0], cv2.max(inner[:, :, 1], inner[:, :, 2]))
    inner_8x = cv2.resize(inner_mc, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    highlight_num = _ocr_crop(inner_8x)

    # --- Remaining numbers: blob detection on max-channel at 8x ---
    s8 = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    H8 = s8.shape[0]
    maxch8 = cv2.max(s8[:, :, 0], cv2.max(s8[:, :, 1], s8[:, :, 2]))

    _, bw8 = cv2.threshold(maxch8, 130, 255, cv2.THRESH_BINARY)
    bw8[:, : BOX_MASK_X * SCALE] = 0  # mask out highlighted box region

    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(bw8)
    blobs = [
        (stats[i][0], stats[i][0] + stats[i][2])
        for i in range(1, n_labels)
        if stats[i][3] >= MIN_BLOB_H
        and stats[i][4] >= 80
        and stats[i][3] < H8 * 0.85
        and stats[i][2] <= 100
    ]
    blobs.sort()

    # Merge digit blobs within 20px (intra-digit gap << inter-number gap)
    groups = []
    for x1, x2 in blobs:
        if groups and x1 - groups[-1][1] <= 20:
            groups[-1] = (groups[-1][0], max(x2, groups[-1][1]))
        else:
            groups.append([x1, x2])

    other_nums = [
        _ocr_crop(maxch8[:, max(0, x1 - 15) : x2 + 15])
        for x1, x2 in groups[:14]
    ]

    result = (
        ([highlight_num] if highlight_num is not None else [])
        + [n for n in other_nums if n is not None]
    )
    return result


if __name__ == "__main__":
    import sys, io, time
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    for i in range(3):
        nums = read_strip()
        print(f"[{i}] {nums}  most_recent={nums[0] if nums else None}")
        if i < 2:
            time.sleep(5)
