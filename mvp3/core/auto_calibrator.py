"""
Auto-Calibrator: Detects roulette table layout from a screenshot and generates
a complete coordinate preset (numbers, splits, corners, streets, chips, etc.).

Usage:
    calibrator = AutoCalibrator()
    screenshot = calibrator.capture_window(hwnd)
    result = calibrator.detect_table(screenshot)
    if result:
        preset = calibrator.generate_preset(result)
"""

import cv2
import numpy as np
from PIL import Image
import mss
import logging
from typing import Dict, List, Optional, Tuple
import pytesseract

logger = logging.getLogger(__name__)


# Standard roulette layout: column index -> (row0=bottom, row1=mid, row2=top)
# Column 0 has numbers 1(bottom), 2(mid), 3(top)
# Column 11 has numbers 34(bottom), 35(mid), 36(top)
def _number_at(col: int, row: int) -> int:
    """row 0=bottom(row1 on table), 1=mid(row2), 2=top(row3). col 0-11."""
    return col * 3 + (row + 1)


RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


class GridDetectionResult:
    """Holds the detected grid parameters."""
    def __init__(self):
        self.grid_origin_x: float = 0.0   # x_pct of column 0 center
        self.grid_origin_y: float = 0.0   # y_pct of row 0 (bottom row) center
        self.cell_width: float = 0.0      # width of one cell in pct
        self.cell_height: float = 0.0     # height of one cell in pct
        self.zero_x: float = 0.0          # x_pct of zero cell center
        self.zero_y: float = 0.0          # y_pct of zero cell center
        self.num_cols: int = 12
        self.num_rows: int = 3
        self.detected_cells: List[dict] = []  # raw detected cell info
        self.chip_positions: List[dict] = []  # detected chip tray positions
        self.confidence: float = 0.0


class AutoCalibrator:
    """Detects roulette table layout from screenshots using color detection + grid fitting."""

    # HSV ranges for roulette cell colors
    # Red has two ranges in HSV (wraps around 0/180)
    RED_LOWER_1 = np.array([0, 70, 70])
    RED_UPPER_1 = np.array([10, 255, 255])
    RED_LOWER_2 = np.array([160, 70, 70])
    RED_UPPER_2 = np.array([180, 255, 255])

    # Green (zero cell)
    GREEN_LOWER = np.array([35, 50, 50])
    GREEN_UPPER = np.array([85, 255, 255])

    def capture_window(self, hwnd) -> Optional[np.ndarray]:
        """Capture a screenshot of the specified window by HWND."""
        try:
            import pygetwindow as gw
            win = gw.Win32Window(hwnd)
            left, top = win.left, win.top
            width, height = win.width, win.height
            if width <= 0 or height <= 0:
                logger.error("Invalid window dimensions")
                return None

            with mss.mss() as sct:
                monitor = {"left": left, "top": top, "width": width, "height": height}
                sct_img = sct.grab(monitor)
                img = np.array(Image.frombytes("RGB", sct_img.size, sct_img.rgb))
                return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        except Exception as e:
            logger.error(f"Failed to capture window: {e}")
            return None

    def capture_from_image(self, image_path: str) -> Optional[np.ndarray]:
        """Load a screenshot from file."""
        img = cv2.imread(image_path)
        if img is None:
            logger.error(f"Failed to load image: {image_path}")
        return img

    def detect_table(self, screenshot: np.ndarray) -> Optional[GridDetectionResult]:
        """
        Main detection pipeline:
        1. Find red and green cells via color thresholding
        2. Extract rectangular contours
        3. Fit a regular 12x3 grid
        4. Return grid parameters
        """
        h, w = screenshot.shape[:2]
        hsv = cv2.cvtColor(screenshot, cv2.COLOR_BGR2HSV)

        # --- Step 1: Detect red cells ---
        red_mask1 = cv2.inRange(hsv, self.RED_LOWER_1, self.RED_UPPER_1)
        red_mask2 = cv2.inRange(hsv, self.RED_LOWER_2, self.RED_UPPER_2)
        red_mask = cv2.bitwise_or(red_mask1, red_mask2)

        # --- Step 2: Detect green cell (zero) ---
        green_mask = cv2.inRange(hsv, self.GREEN_LOWER, self.GREEN_UPPER)

        # Clean up masks
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_CLOSE, kernel)
        green_mask = cv2.morphologyEx(green_mask, cv2.MORPH_OPEN, kernel)

        # --- Step 3: Find red cell contours ---
        red_contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        green_contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter contours by area and aspect ratio (cells are roughly rectangular)
        min_cell_area = (w * h) * 0.0003  # minimum ~0.03% of image
        max_cell_area = (w * h) * 0.01    # maximum ~1% of image

        red_cells = self._filter_cell_contours(red_contours, min_cell_area, max_cell_area)
        green_cells = self._filter_cell_contours(green_contours, min_cell_area, max_cell_area * 3)

        logger.info(f"Detected {len(red_cells)} red cells, {len(green_cells)} green cells")

        if len(red_cells) < 10:
            logger.warning("Too few red cells detected — try adjusting color thresholds")
            return None

        # --- Step 4: Fit grid from red cells ---
        result = self._fit_grid(red_cells, green_cells, w, h)
        if result is None:
            logger.warning("Failed to fit grid to detected cells")
            return None

        # --- Step 5: Detect chip tray ---
        result.chip_positions = self._detect_chips(screenshot, result, w, h)

        return result

    def _filter_cell_contours(self, contours, min_area, max_area) -> List[dict]:
        """Filter contours to find rectangular cells. Returns list of {cx, cy, w, h, area}."""
        cells = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            # Aspect ratio filter: cells are roughly 1:1 to 1:2
            aspect = max(cw, ch) / (min(cw, ch) + 1e-6)
            if aspect > 3.0:
                continue
            cx = x + cw / 2
            cy = y + ch / 2
            cells.append({"cx": cx, "cy": cy, "w": cw, "h": ch, "area": area})
        return cells

    def _fit_grid(self, red_cells: List[dict], green_cells: List[dict],
                  img_w: int, img_h: int) -> Optional[GridDetectionResult]:
        """
        Fit a 12x3 grid to the detected red cells.
        Strategy: cluster cell centers by X (columns) and Y (rows).
        """
        if len(red_cells) < 10:
            return None

        # Get all cell centers
        xs = np.array([c["cx"] for c in red_cells])
        ys = np.array([c["cy"] for c in red_cells])
        widths = np.array([c["w"] for c in red_cells])
        heights = np.array([c["h"] for c in red_cells])

        # Estimate cell dimensions from median
        median_w = float(np.median(widths))
        median_h = float(np.median(heights))

        # --- Cluster Y values into 3 rows ---
        y_sorted = np.sort(np.unique(np.round(ys / (median_h * 0.5)) * (median_h * 0.5)))
        row_centers = self._cluster_1d(ys, expected_clusters=3, tolerance=median_h * 0.6)
        if row_centers is None or len(row_centers) != 3:
            logger.warning(f"Expected 3 row clusters, got {len(row_centers) if row_centers else 0}")
            # Try with more tolerance
            row_centers = self._cluster_1d(ys, expected_clusters=3, tolerance=median_h * 0.8)
            if row_centers is None or len(row_centers) != 3:
                return None

        row_centers = sorted(row_centers)  # top to bottom (ascending Y)

        # --- Cluster X values into columns ---
        col_centers = self._cluster_1d(xs, expected_clusters=12, tolerance=median_w * 0.6)
        if col_centers is None or len(col_centers) < 8:
            logger.warning(f"Expected ~12 column clusters, got {len(col_centers) if col_centers else 0}")
            # Try being more lenient
            col_centers = self._cluster_1d(xs, expected_clusters=12, tolerance=median_w * 0.8)
            if col_centers is None or len(col_centers) < 8:
                return None

        col_centers = sorted(col_centers)

        # If we got fewer than 12 columns, extrapolate
        if len(col_centers) < 12:
            col_step = np.median(np.diff(col_centers))
            while len(col_centers) < 12:
                # Extend right
                col_centers.append(col_centers[-1] + col_step)

        col_centers = col_centers[:12]  # take only 12

        # Compute grid parameters as percentages
        cell_w_pct = median_w / img_w
        cell_h_pct = median_h / img_h

        # Column step (in pct)
        col_step_pct = np.median(np.diff(col_centers)) / img_w

        # Row step (in pct)
        row_step_pct = (row_centers[1] - row_centers[0]) / img_h

        result = GridDetectionResult()
        result.cell_width = cell_w_pct
        result.cell_height = cell_h_pct

        # Grid origin: center of column 0, bottom row (row 0 = largest Y = row_centers[2])
        # In the preset, row order is: row3(top)=smallest y, row2(mid), row1(bottom)=largest y
        # Our row_centers[0]=top, row_centers[2]=bottom
        result.grid_origin_x = col_centers[0] / img_w
        result.grid_origin_y = row_centers[2] / img_h  # bottom row
        result.num_cols = len(col_centers)
        result.num_rows = 3

        # Store all column/row centers in pct for precise coordinate generation
        result._col_centers_pct = [c / img_w for c in col_centers]
        result._row_centers_pct = [r / img_h for r in row_centers]  # [top, mid, bottom]

        # Green cell (zero) — should be to the left of column 0
        if green_cells:
            # Pick the green cell closest to the grid's left edge
            grid_left_x = col_centers[0]
            best_green = min(green_cells, key=lambda c: abs(c["cx"] - grid_left_x + median_w))
            result.zero_x = best_green["cx"] / img_w
            result.zero_y = best_green["cy"] / img_h
        else:
            # Estimate zero position: one cell width to the left of column 0, vertically centered
            result.zero_x = (col_centers[0] - median_w) / img_w
            result.zero_y = row_centers[1] / img_h  # middle row

        # Store raw detections for debugging
        result.detected_cells = red_cells + green_cells
        result.confidence = min(1.0, len(red_cells) / 18.0)  # 18 red numbers = perfect

        logger.info(f"Grid fitted: {len(col_centers)} cols x 3 rows, "
                     f"cell={cell_w_pct:.4f}x{cell_h_pct:.4f}, "
                     f"confidence={result.confidence:.2f}")

        return result

    def _cluster_1d(self, values: np.ndarray, expected_clusters: int,
                    tolerance: float) -> Optional[List[float]]:
        """Simple 1D clustering: group values within tolerance, return cluster centers."""
        if len(values) == 0:
            return None

        sorted_vals = np.sort(values)
        clusters = []
        current_cluster = [sorted_vals[0]]

        for v in sorted_vals[1:]:
            if v - current_cluster[-1] < tolerance:
                current_cluster.append(v)
            else:
                clusters.append(current_cluster)
                current_cluster = [v]
        clusters.append(current_cluster)

        # Return cluster centers (mean of each cluster)
        centers = [float(np.mean(c)) for c in clusters]
        return centers

    def _detect_chips(self, screenshot: np.ndarray, grid: GridDetectionResult,
                      img_w: int, img_h: int) -> List[dict]:
        """
        Detect chip tray below/around the betting grid.
        Look for circular shapes in the lower portion of the screen.
        """
        chips = []
        # Chips are typically in the bottom 15% of the screen
        chip_region_top = int(img_h * 0.85)
        chip_region = screenshot[chip_region_top:, :]

        if chip_region.size == 0:
            return chips

        gray = cv2.cvtColor(chip_region, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (9, 9), 2)

        # Detect circles using Hough transform
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=30,
            param1=100, param2=40,
            minRadius=10, maxRadius=50
        )

        if circles is not None:
            circles = np.uint16(np.around(circles[0]))
            # Sort by X position (left to right)
            circles = sorted(circles, key=lambda c: c[0])

            for i, (cx, cy, r) in enumerate(circles):
                abs_cy = chip_region_top + cy
                chips.append({
                    "index": i,
                    "x_pct": round(float(cx) / img_w, 4),
                    "y_pct": round(float(abs_cy) / img_h, 4),
                    "radius": int(r),
                })

            logger.info(f"Detected {len(chips)} potential chip positions")

        return chips

    def generate_preset(self, result: GridDetectionResult,
                        name: str = "Auto-Detected Layout",
                        chip_values: Optional[List[str]] = None) -> dict:
        """
        Generate a full preset dictionary from detected grid parameters.
        Derives ALL coordinates: numbers, splits, corners, streets, dozens, etc.
        """
        coords = {}

        col_centers = result._col_centers_pct  # [col0 ... col11] in x_pct
        row_centers = result._row_centers_pct  # [top, mid, bottom] in y_pct

        # Cell step sizes (for computing boundary positions)
        col_step = np.median(np.diff(col_centers)) if len(col_centers) > 1 else result.cell_width
        row_step = (row_centers[2] - row_centers[0]) / 2 if len(row_centers) == 3 else result.cell_height

        # ── Zero ──
        coords["0"] = {"x_pct": round(result.zero_x, 4), "y_pct": round(result.zero_y, 4)}

        # ── Numbers 1-36 ──
        # Layout: column c has numbers (c*3+1, c*3+2, c*3+3)
        #   row_centers[2] = bottom = row1 numbers (1, 4, 7, ...)
        #   row_centers[1] = mid    = row2 numbers (2, 5, 8, ...)
        #   row_centers[0] = top    = row3 numbers (3, 6, 9, ...)
        for col in range(12):
            for row_idx, row_label in enumerate([2, 1, 0]):
                # row_idx 0 = bottom (numbers ending ...1, ...4), row_label=2 -> row_centers[2]
                num = col * 3 + (row_idx + 1)
                x = col_centers[col] if col < len(col_centers) else col_centers[-1] + col_step * (col - len(col_centers) + 1)
                y = row_centers[row_label]
                coords[str(num)] = {"x_pct": round(x, 4), "y_pct": round(y, 4)}

        # ── Horizontal Splits (adjacent rows, same column: n and n+1 where n%3 != 0) ──
        for n in range(1, 37):
            if n % 3 == 0:
                continue
            n2 = n + 1
            c1 = coords[str(n)]
            c2 = coords[str(n2)]
            mx = round((c1["x_pct"] + c2["x_pct"]) / 2, 4)
            my = round((c1["y_pct"] + c2["y_pct"]) / 2, 4)
            coords[f"{n}-{n2}split"] = {"x_pct": mx, "y_pct": my}

        # ── Vertical Splits (adjacent columns, same row: n and n+3) ──
        for n in range(1, 34):
            n2 = n + 3
            # Same row check: both in same row position
            r1 = (n - 1) % 3
            r2 = (n2 - 1) % 3
            if r1 != r2:
                continue
            c1 = coords[str(n)]
            c2 = coords[str(n2)]
            mx = round((c1["x_pct"] + c2["x_pct"]) / 2, 4)
            my = round((c1["y_pct"] + c2["y_pct"]) / 2, 4)
            coords[f"{n}-{n2}split"] = {"x_pct": mx, "y_pct": my}

        # ── Zero splits: 0-1, 0-2, 0-3 ──
        for n in [1, 2, 3]:
            c0 = coords["0"]
            cn = coords[str(n)]
            # Position at boundary between zero and number grid
            mx = round((c0["x_pct"] + cn["x_pct"]) / 2, 4)
            my = round(cn["y_pct"], 4)  # same row as the number
            coords[f"0-{n}split"] = {"x_pct": mx, "y_pct": my}

        # ── Corners (intersection of 4 numbers) ──
        for n in range(1, 34):
            if n % 3 == 0:
                continue
            n2, n3, n4 = n + 1, n + 3, n + 4
            if n4 > 36:
                continue
            # Corner at intersection of n, n+1, n+3, n+4
            lo = min(n, n2, n3, n4)
            hi = max(n, n2, n3, n4)
            c_n = coords[str(n)]
            c_hi = coords[str(n4)]
            cx = round((c_n["x_pct"] + c_hi["x_pct"]) / 2, 4)
            cy = round((c_n["y_pct"] + c_hi["y_pct"]) / 2, 4)
            coords[f"{lo}-{hi}corner"] = {"x_pct": cx, "y_pct": cy}

        # ── Streets (3-number rows: 1-3, 4-6, ... 34-36) ──
        # Click target is at the bottom edge of the column, below the bottom row
        bottom_edge_y = round(row_centers[2] + row_step * 0.55, 4)
        for start in range(1, 35, 3):
            col_idx = (start - 1) // 3
            cx = round(col_centers[col_idx], 4)
            coords[f"{start}-{start+2}strt"] = {"x_pct": cx, "y_pct": bottom_edge_y}

        # ── Double Streets (6-number blocks) ──
        for start in range(1, 34, 3):
            next_start = start + 3
            if next_start > 36:
                continue
            col1 = (start - 1) // 3
            col2 = (next_start - 1) // 3
            cx = round((col_centers[col1] + col_centers[col2]) / 2, 4)
            coords[f"{start}-{start+5}dblstrt"] = {"x_pct": cx, "y_pct": bottom_edge_y}

        # 0-3 double street
        cx = round((result.zero_x + col_centers[0]) / 2, 4)
        coords["0-3dblstrt"] = {"x_pct": cx, "y_pct": bottom_edge_y}

        # ── Dozens (below the number grid) ──
        dozens_y = round(row_centers[2] + row_step * 1.2, 4)
        # 1st12: center of columns 0-3
        coords["1st12"] = {
            "x_pct": round((col_centers[0] + col_centers[3]) / 2, 4),
            "y_pct": dozens_y
        }
        # 2nd12: center of columns 4-7
        coords["2nd12"] = {
            "x_pct": round((col_centers[4] + col_centers[7]) / 2, 4),
            "y_pct": dozens_y
        }
        # 3rd12: center of columns 8-11
        coords["3rd12"] = {
            "x_pct": round((col_centers[8] + col_centers[11]) / 2, 4),
            "y_pct": dozens_y
        }

        # ── Even chances (below dozens) ──
        chances_y = round(row_centers[2] + row_step * 2.0, 4)
        grid_left = col_centers[0]
        grid_right = col_centers[11]
        grid_span = grid_right - grid_left
        even_chance_labels = ["1to18", "even", "red", "black", "odd", "19to36"]
        for i, label in enumerate(even_chance_labels):
            x = grid_left + grid_span * (i + 0.5) / 6
            coords[label] = {"x_pct": round(x, 4), "y_pct": chances_y}

        # ── Columns (right of the grid) ──
        col_bet_x = round(col_centers[11] + col_step * 1.2, 4)
        coords["col1"] = {"x_pct": col_bet_x, "y_pct": round(row_centers[2], 4)}  # bottom row
        coords["col2"] = {"x_pct": col_bet_x, "y_pct": round(row_centers[1], 4)}  # mid row
        coords["col3"] = {"x_pct": col_bet_x, "y_pct": round(row_centers[0], 4)}  # top row

        # ── Chips ──
        default_chip_labels = chip_values or ["chip_.1", "chip_.5", "chip_1", "chip_5", "chip_25", "chip_100"]
        if result.chip_positions and len(result.chip_positions) >= len(default_chip_labels):
            # Map detected chip circles to chip labels (left to right)
            for i, label in enumerate(default_chip_labels):
                if i < len(result.chip_positions):
                    cp = result.chip_positions[i]
                    coords[label] = {"x_pct": cp["x_pct"], "y_pct": cp["y_pct"]}
        else:
            # No chips detected — leave chip coordinates empty for manual calibration
            logger.warning("Chip tray not fully detected — chip coordinates need manual calibration")

        preset = {
            "name": name,
            "description": f"Auto-detected layout (confidence: {result.confidence:.0%})",
            "coordinates": coords,
            "detection_info": {
                "red_cells_found": sum(1 for c in result.detected_cells if True),
                "confidence": result.confidence,
                "grid_cols": result.num_cols,
                "grid_rows": result.num_rows,
                "cell_size_pct": [round(result.cell_width, 4), round(result.cell_height, 4)],
                "chips_detected": len(result.chip_positions),
            }
        }

        total_coords = len(coords)
        logger.info(f"Generated preset with {total_coords} coordinates")
        return preset

    def generate_debug_image(self, screenshot: np.ndarray,
                             result: GridDetectionResult) -> np.ndarray:
        """
        Draw detected grid overlay on the screenshot for visual verification.
        Returns annotated image (BGR).
        """
        debug = screenshot.copy()
        h, w = debug.shape[:2]

        if not hasattr(result, '_col_centers_pct') or not hasattr(result, '_row_centers_pct'):
            return debug

        col_centers = result._col_centers_pct
        row_centers = result._row_centers_pct

        # Draw detected number positions
        for col in range(min(12, len(col_centers))):
            for row_idx in range(3):
                num = col * 3 + (row_idx + 1)
                row_label = 2 - row_idx  # map to row_centers index
                cx = int(col_centers[col] * w)
                cy = int(row_centers[row_label] * h)

                color = (0, 0, 255) if num in RED_NUMBERS else (100, 100, 100)
                cv2.circle(debug, (cx, cy), 8, color, 2)
                cv2.putText(debug, str(num), (cx - 8, cy + 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)

        # Draw zero
        zx = int(result.zero_x * w)
        zy = int(result.zero_y * h)
        cv2.circle(debug, (zx, zy), 10, (0, 200, 0), 2)
        cv2.putText(debug, "0", (zx - 4, zy + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

        # Draw chip positions
        for cp in result.chip_positions:
            cx = int(cp["x_pct"] * w)
            cy = int(cp["y_pct"] * h)
            cv2.circle(debug, (cx, cy), cp["radius"], (0, 255, 255), 2)

        # Add info text
        cv2.putText(debug, f"Confidence: {result.confidence:.0%}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        return debug

    def validate_preset(self, preset: dict) -> dict:
        """
        Validate a generated preset by checking coordinate consistency.
        Returns dict with validation results.
        """
        coords = preset.get("coordinates", {})
        issues = []

        # Check all 37 numbers present
        for n in range(0, 37):
            if str(n) not in coords:
                issues.append(f"Missing number {n}")

        # Check splits exist for adjacent numbers
        expected_splits = 0
        for n in range(1, 37):
            if n % 3 != 0:
                key = f"{n}-{n+1}split"
                if key not in coords:
                    issues.append(f"Missing horizontal split {key}")
                expected_splits += 1

        # Check columns are ordered correctly (col1 y > col2 y > col3 y or vice versa)
        if "col1" in coords and "col3" in coords:
            if abs(coords["col1"]["x_pct"] - coords["col3"]["x_pct"]) > 0.01:
                issues.append("Column bets not vertically aligned")

        # Check dozens horizontal ordering
        if all(k in coords for k in ["1st12", "2nd12", "3rd12"]):
            if not (coords["1st12"]["x_pct"] < coords["2nd12"]["x_pct"] < coords["3rd12"]["x_pct"]):
                issues.append("Dozens not in left-to-right order")

        return {
            "valid": len(issues) == 0,
            "total_coordinates": len(coords),
            "issues": issues,
        }

    def refine_with_ocr(self, screenshot: np.ndarray, result: GridDetectionResult) -> GridDetectionResult:
        """
        Optional: Use OCR to verify number positions by reading text in each detected cell.
        This refines the grid if the initial color-based detection was slightly off.
        """
        h, w = screenshot.shape[:2]
        col_centers = result._col_centers_pct
        row_centers = result._row_centers_pct
        cell_w = result.cell_width * w
        cell_h = result.cell_height * h

        verified = 0
        mismatches = []

        for col in range(min(12, len(col_centers))):
            for row_idx in range(3):
                expected_num = col * 3 + (row_idx + 1)
                row_label = 2 - row_idx
                cx = int(col_centers[col] * w)
                cy = int(row_centers[row_label] * h)

                # Extract cell region
                x1 = max(0, int(cx - cell_w * 0.4))
                y1 = max(0, int(cy - cell_h * 0.4))
                x2 = min(w, int(cx + cell_w * 0.4))
                y2 = min(h, int(cy + cell_h * 0.4))

                cell_img = screenshot[y1:y2, x1:x2]
                if cell_img.size == 0:
                    continue

                # OCR the cell
                try:
                    gray = cv2.cvtColor(cell_img, cv2.COLOR_BGR2GRAY)
                    # Upscale for better OCR
                    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    text = pytesseract.image_to_string(thresh, config='--psm 10 -c tessedit_char_whitelist=0123456789').strip()
                    if text.isdigit():
                        detected_num = int(text)
                        if detected_num == expected_num:
                            verified += 1
                        else:
                            mismatches.append((expected_num, detected_num, col, row_idx))
                except Exception:
                    pass

        logger.info(f"OCR verification: {verified}/36 numbers confirmed, {len(mismatches)} mismatches")
        if mismatches:
            logger.warning(f"OCR mismatches: {mismatches[:5]}...")

        # Update confidence based on OCR
        if verified > 0:
            result.confidence = min(1.0, (result.confidence + verified / 36) / 2)

        return result
