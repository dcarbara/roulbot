import pyautogui
import pygetwindow as gw
import pygetwindow as gw
import time
import logging

logger = logging.getLogger(__name__)

class RouletteBrowserAutomation:
    def __init__(self, coordinates: dict, window_title: str):
        self.coordinates = coordinates
        self.window_title = window_title
        self.last_window_state = None  # Cache window state
        self.last_focus_time = 0
        self.focus_cooldown = 1.0  # Seconds between focus attempts

    def _get_window(self):
        # Cache window lookup to reduce overhead
        current_time = time.time()
        if (self.last_window_state is None or 
            current_time - self.last_focus_time > self.focus_cooldown):
            
            win = next((w for w in gw.getAllWindows() if self.window_title in w.title), None)
            if win:
                self.last_window_state = {
                    "left": win.left,
                    "top": win.top,
                    "width": win.width,
                    "height": win.height,
                    "handle": win._hWnd
                }
                self.last_focus_time = current_time
            else:
                self.last_window_state = None
        
        return self.last_window_state

    def place_bet(self, label: str):
        coord = self.coordinates.get(label)
        if not coord:
            logger.warning(f"⚠️ Missing coordinate: {label}")
            return

        win = self._get_window()
        if not win:
            logger.error("❌ Could not find window.")
            return

        x = int(win["left"] + win["width"] * coord["x_pct"])
        y = int(win["top"] + win["height"] * coord["y_pct"])
        pyautogui.moveTo(x, y)
        pyautogui.click()
        logger.info(f"🧿 Placed chip on '{label}' at ({x}, {y})")

    def select_chip(self, chip_label: str):
        self.place_bet(chip_label)  # Same logic

    def mock_detect_win(self):
        while True:
            ans = input("✅ Win (w) or ❌ Loss (l)? ").lower().strip()
            if ans == "w":
                return True
            elif ans == "l":
                return False
            
    def focus_window(self):
        current_time = time.time()
        if current_time - self.last_focus_time < self.focus_cooldown:
            return True  # Skip if we focused recently
        
        win = next((w for w in gw.getAllWindows() if self.window_title in w.title), None)
        if win:
            try:
                win.activate()
                time.sleep(0.2)  # Reduced wait time
                self.last_focus_time = current_time
                return True
            except Exception as e:
                logger.warning(f"⚠️ Window activation failed: {e}")
                return False
        else:
            logger.error("❌ Could not find the window to activate.")
            return False
    
    def reset_scroll_keyboard(self):
        if not self.focus_window():
            return

        # Optional: Click somewhere safe first (only if coordinate exists)
        if "reset_scroll" in self.coordinates:
            self.place_bet("reset_scroll")
            time.sleep(0.1)  # Reduced wait time

        # More efficient keyboard handling
        pyautogui.hotkey('ctrl', 'home')
        logger.info("🔄 Sent Ctrl + Home")
        time.sleep(0.1)  # Reduced wait time
        
        # Scroll up a little after resetting
        pyautogui.scroll(-60)  # Increased scroll amount (positive value scrolls up)
        logger.info("📜 Scrolled up")
        time.sleep(0.01)  # Reduced wait time

    def close(self):
        pass  # Nothing to close
