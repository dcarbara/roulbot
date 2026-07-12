# automation/playwright_driver.py

from playwright.sync_api import sync_playwright
import pyautogui
import time
import json
import urllib.request

class RouletteBrowserAutomation:
    def __init__(self, coordinates: dict, window_title: str, cdp_url="http://localhost:9222"):
        self.coordinates = coordinates
        self.window_title = window_title
        self.cdp_url = cdp_url
        self.page = None
        self.playwright = None
        self.browser = None

    def launch_browser(self):
        """
        Connect to existing Chrome via CDP.
        """
        self.playwright = sync_playwright().start()
        ws_endpoint = self._get_cdp_ws()
        if not ws_endpoint:
            raise Exception("❌ Could not get WebSocket endpoint from CDP.")

        self.browser = self.playwright.chromium.connect_over_cdp(ws_endpoint)

        pages = self.browser.contexts[0].pages
        self.page = None
        for p in pages:
            if "https://stake.bet/casino/games/evolution-roulette-lobby" in p.url:
                self.page = p
                break

        if not self.page:
            print("⚠️ Stake roulette page not found. Opening it...")
            self.page = self.browser.contexts[0].new_page()
            self.page.goto("https://stake.bet/casino/games/evolution-roulette-lobby")

        print(f"🌐 Connected to: {self.page.url}")

    def _get_cdp_ws(self):
        try:
            with urllib.request.urlopen(f"{self.cdp_url}/json/version") as response:
                data = json.loads(response.read())
                return data["webSocketDebuggerUrl"]
        except Exception as e:
            print("⚠️ Failed to fetch CDP endpoint:", e)
            return None

    def place_bet(self, label: str):
        if label not in self.coordinates:
            print(f"⚠️ Missing coordinates for {label}")
            return

        window = self._get_window()
        if not window:
            print("❌ Could not find window.")
            return

        x_pct = self.coordinates[label]["x_pct"]
        y_pct = self.coordinates[label]["y_pct"]
        x = int(window["left"] + window["width"] * x_pct)
        y = int(window["top"] + window["height"] * y_pct)
        pyautogui.moveTo(x, y)
        pyautogui.click()
        print(f"🧿 Placed chip on '{label}' at ({x}, {y})")

    def select_chip(self, chip_label: str):
        if chip_label not in self.coordinates:
            print(f"⚠️ Missing coordinates for chip: {chip_label}")
            return

        window = self._get_window()
        if not window:
            print("❌ Could not find window.")
            return

        x_pct = self.coordinates[chip_label]["x_pct"]
        y_pct = self.coordinates[chip_label]["y_pct"]
        x = int(window["left"] + window["width"] * x_pct)
        y = int(window["top"] + window["height"] * y_pct)
        pyautogui.moveTo(x, y)
        pyautogui.click()
        print(f"🔘 Selected chip '{chip_label}' at ({x}, {y})")

    def _get_window(self):
        import pygetwindow as gw
        win = next((w for w in gw.getAllWindows() if self.window_title in w.title), None)
        if win:
            return {
                "left": win.left,
                "top": win.top,
                "width": win.width,
                "height": win.height
            }
        return None

    def mock_detect_win(self):
        while True:
            ans = input("✅ Win (w) or ❌ Loss (l)? ").lower().strip()
            if ans == "w":
                return True
            elif ans == "l":
                return False

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
