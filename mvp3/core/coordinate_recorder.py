import pyautogui
import pygetwindow as gw
import time
import threading
from tkinter import messagebox, Tk
from core.utils.image_capture import capture_region_image
import mss
import numpy as np
from PIL import Image
import ctypes
import pytesseract
import re
import json
import logging

logger = logging.getLogger(__name__)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per monitor DPI awareness
except Exception:
    pass

class CoordinateRecorder:
    def __init__(self, on_capture_callback):
        """
        :param on_capture_callback: A function to call with (label, x_pct, y_pct) or (label, region_dict)
        """
        self.on_capture = on_capture_callback
        self._browser_hwnd = None       # Native window handle (stable across focus changes)
        self._browser_title = None      # Title at selection time (for display/logging)
        self.is_recording = False
        self._auto_bet_running = False

    @property
    def browser_win(self):
        """Always return a fresh window object from the stored HWND.
        This prevents stale references when other windows pop up."""
        if self._browser_hwnd is None:
            return None
        try:
            win = gw.Win32Window(self._browser_hwnd)
            # Verify the window still exists by reading a property
            _ = win.title
            return win
        except Exception:
            logger.warning(f"Selected window (HWND={self._browser_hwnd}) no longer exists.")
            return None

    @browser_win.setter
    def browser_win(self, value):
        """Store the HWND when a window is assigned."""
        if value is None:
            self._browser_hwnd = None
            self._browser_title = None
        else:
            self._browser_hwnd = value._hWnd
            self._browser_title = value.title

    def load_preset(self, preset_data):
        """
        Applies a preset config to the currently selected window.
        Converts relative percentages effectively for the current window size.
        """
        if not self.browser_win:
            messagebox.showerror("Error", "No window selected to apply preset to.")
            return False

        coordinates = preset_data.get("coordinates", {})
        count = 0

        for label, data in coordinates.items():
            # Check if it is a point or region
            if "x_pct" in data and "y_pct" in data:
                # Point
                self.on_capture(label, data["x_pct"], data["y_pct"])
                count += 1
            elif "x1_pct" in data:
                # Region
                self.on_capture(label, data)
                count += 1

        logger.info(f"Applied preset with {count} items.")
        return True


    def list_windows(self):
        return [w for w in gw.getAllWindows() if w.title.strip()]

    def select_window(self, index):
        windows = self.list_windows()
        if 0 <= index < len(windows):
            self.browser_win = windows[index]
            return True
        return False

    def select_window_by_hwnd(self, hwnd):
        """Select a window by its native handle — immune to list reordering."""
        try:
            win = gw.Win32Window(hwnd)
            _ = win.title  # verify it still exists
            self.browser_win = win
            return True
        except Exception:
            logger.warning(f"Window with HWND={hwnd} no longer exists.")
            return False

    def activate_window_with_click(self):
        """Bring window to front and click to ensure focus"""
        if not self.browser_win:
            return False
            
        try:
            # 1. Standard Activate (may flash taskbar)
            self.browser_win.activate()
            time.sleep(0.5)
            
            # 2. Force Click (to bypass SetForegroundWindow restrictions)
            # Click near bottom-left (safer than bottom-right which had chat/overlays)
            x = self.browser_win.left + 400
            y = self.browser_win.top + self.browser_win.height - 40
            
            # Move and Click
            pyautogui.moveTo(x, y)
            pyautogui.click()
            time.sleep(0.2)
            return True
        except Exception as e:
            logger.error(f"Failed to activate window: {e}")
            return False

    def capture_coordinate(self, label):
        if not self.browser_win:
            return

        self.is_recording = True
        messagebox.showinfo("Recording", f"Move your mouse over the target for '{label}' and press F8.")

        def record_loop():
            import keyboard
            while self.is_recording:
                if keyboard.is_pressed("f8"):
                    x, y = pyautogui.position()
                    left, top, width, height = self.browser_win.left, self.browser_win.top, self.browser_win.width, self.browser_win.height

                    if not (left <= x <= left + width and top <= y <= top + height):
                        logger.warning("❌ Outside browser bounds")
                        time.sleep(1)
                        continue

                    x_pct = round((x - left) / width, 4)
                    y_pct = round((y - top) / height, 4)

                    self.on_capture(label, x_pct, y_pct)
                    self.is_recording = False
                    break

                time.sleep(0.1)

        threading.Thread(target=record_loop, daemon=True).start()

    def capture_region(self, label):
        if not self.browser_win:
            messagebox.showerror("Error", "No browser window selected.")
            return

        def region_loop():
            import keyboard
            top_left = None
            bottom_right = None

            # Step 1: Prompt for top-left
            messagebox.showinfo("Record Region", f"Move mouse to TOP-LEFT of '{label}' and press F8.")
            while not top_left:
                if keyboard.is_pressed("f8"):
                    top_left = pyautogui.position()
                    print("📍 Top-left recorded at", top_left)
                    time.sleep(0.5)
                time.sleep(0.05)

            # Step 2: Prompt for bottom-right
            messagebox.showinfo("Record Region", f"Move mouse to BOTTOM-RIGHT of '{label}' and press F9.")
            while not bottom_right:
                if keyboard.is_pressed("f9"):
                    bottom_right = pyautogui.position()
                    print("📍 Bottom-right recorded at", bottom_right)
                    time.sleep(0.5)
                time.sleep(0.05)

            # Step 3: Validate and convert to percentages
            left, top, width, height = self.browser_win.left, self.browser_win.top, self.browser_win.width, self.browser_win.height
            x1, y1 = top_left
            x2, y2 = bottom_right

            # Ensure coordinates are within window bounds
            if not (left <= x1 <= left + width and top <= y1 <= top + height and
                    left <= x2 <= left + width and top <= y2 <= top + height):
                messagebox.showerror("Error", "Selected region is outside the browser window.")
                return

            x1_pct = round((x1 - left) / width, 4)
            y1_pct = round((y1 - top) / height, 4)
            x2_pct = round((x2 - left) / width, 4)
            y2_pct = round((y2 - top) / height, 4)

            region = {
                "x1_pct": min(x1_pct, x2_pct),
                "y1_pct": min(y1_pct, y2_pct),
                "x2_pct": max(x1_pct, x2_pct),
                "y2_pct": max(y1_pct, y2_pct),
            }

            # Step 4: Preview the region
            try:
                self.browser_win.activate()
                time.sleep(0.3)
                logger.debug(f"Capturing region: left={left}, top={top}, width={width}, height={height}")
                if width <= 0 or height <= 0:
                    logger.error("Invalid region size, cannot capture screenshot.")
                    return
                img = capture_region_image(self.browser_win, region)
                img.show()
                messagebox.showinfo("Preview", "Region captured and previewed. Click OK to save.")
                self.on_capture(label, region)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to capture or preview region: {e}")

        threading.Thread(target=region_loop, daemon=True).start()

    def flash_window_border(self, duration=2):
        if not self.browser_win:
            return

        left, top = self.browser_win.left, self.browser_win.top
        width, height = self.browser_win.width, self.browser_win.height

        def _flash():
            root = Tk()
            root.overrideredirect(True)
            root.attributes('-topmost', True)
            root.geometry(f"{width}x{height}+{left}+{top}")
            root.configure(bg='red')
            root.wm_attributes('-alpha', 0.3)
            root.after(int(duration * 1000), root.destroy)
            root.mainloop()

        threading.Thread(target=_flash, daemon=True).start()

    def calibrate_absolute_region(self, label):
        import pyautogui
        import keyboard
        import time
        import threading
        from PIL import Image
        import mss

        coords = {}

        def calibration_loop():
            # Step 1: Top-left
            pyautogui.alert(f"Move mouse to TOP-LEFT of '{label}' and press F8.")
            while True:
                if keyboard.is_pressed("f8"):
                    coords['left'], coords['top'] = pyautogui.position()
                    print("Top-left:", coords['left'], coords['top'])
                    time.sleep(0.5)
                    break
                time.sleep(0.05)

            # Step 2: Bottom-right
            pyautogui.alert(f"Move mouse to BOTTOM-RIGHT of '{label}' and press F9.")
            while True:
                if keyboard.is_pressed("f9"):
                    x2, y2 = pyautogui.position()
                    print("Bottom-right:", x2, y2)
                    time.sleep(0.5)
                    break
                time.sleep(0.05)

            coords['width'] = x2 - coords['left']
            coords['height'] = y2 - coords['top']

            print(f"Capturing region: {coords}")

            # Step 3: Capture and preview
            with mss.mss() as sct:
                monitor = {
                    "left": coords['left'],
                    "top": coords['top'],
                    "width": coords['width'],
                    "height": coords['height']
                }
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
                img.show()

        threading.Thread(target=calibration_loop, daemon=True).start()

    def is_table_open(self, bet_open_region):
        with mss.mss() as sct:
            sct_img = sct.grab(bet_open_region)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        text = pytesseract.image_to_string(img, config='--psm 7').lower()
        logger.debug(f"OCR Table State Text: {text}")
        if "open" in text:
            return True
        return False

    def auto_bet_when_open(self, bet_open_region, place_bet_callback, poll_interval=1):
        self._auto_bet_running = True
        def loop():
            while self._auto_bet_running:
                if self.is_table_open(bet_open_region):
                    logger.info("Table is open! Placing bet...")
                    place_bet_callback()
                    time.sleep(2)  # Wait to avoid multiple bets in one open period
                else:
                    logger.debug("Table is not open. Waiting...")
                time.sleep(poll_interval)
        threading.Thread(target=loop, daemon=True).start()

    def stop_auto_bet(self):
        self._auto_bet_running = False

    def extract_winning_number(self, winning_number_region):
        with mss.mss() as sct:
            sct_img = sct.grab(winning_number_region)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        text = pytesseract.image_to_string(img, config='--psm 7')
        logger.debug(f"OCR Winning Number Text: {text}")
        match = re.search(r'\d+', text)
        if match:
            return int(match.group())
        return None

    def extract_winning_number_from_config(self, config_path="config/config.json"):
        # Load config
        with open(config_path, "r") as f:
            config = json.load(f)
        coordinates = config.get("coordinates", {})
        region_pct = coordinates.get("winning_number_region")
        if not region_pct:
            logger.error("No winning_number_region found in config.")
            return None
        if not self.browser_win:
            logger.error("No browser window selected.")
            return None
        # Convert percentages to absolute screen coordinates
        left = self.browser_win.left + int(self.browser_win.width * region_pct["x1_pct"])
        top = self.browser_win.top + int(self.browser_win.height * region_pct["y1_pct"])
        right = self.browser_win.left + int(self.browser_win.width * region_pct["x2_pct"])
        bottom = self.browser_win.top + int(self.browser_win.height * region_pct["y2_pct"])
        width = right - left
        height = bottom - top
        region = {"left": left, "top": top, "width": width, "height": height}
        # OCR extraction
        with mss.mss() as sct:
            sct_img = sct.grab(region)
            img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        text = pytesseract.image_to_string(img, config='--psm 7')
        print("OCR Winning Number Text:", text)
        match = re.search(r'\d+', text)
        if match:
            return int(match.group())
        return None

def capture_region_image(window, region):
    """
    Capture a screenshot of the specified region within the given window using mss.
    """
    try:
        window.activate()
        time.sleep(0.3)
    except Exception as e:
        logger.warning(f"Could not activate window: {e}")

    left = window.left + int(window.width * region["x1_pct"])
    top = window.top + int(window.height * region["y1_pct"])
    right = window.left + int(window.width * region["x2_pct"])
    bottom = window.top + int(window.height * region["y2_pct"])
    width = right - left
    height = bottom - top

    logger.debug(f"Capturing region: left={left}, top={top}, width={width}, height={height}")

    if width <= 0 or height <= 0:
        logger.error("Invalid region size, cannot capture screenshot.")
        return None

    with mss.mss() as sct:
        monitor = {"left": left, "top": top, "width": width, "height": height}
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        return img
