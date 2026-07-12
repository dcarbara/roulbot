import logging
import sys

# Configure logging immediately
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", mode='a', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ],
    force=True
)

# --- CRITICAL: Set High DPI Awareness Early ---
try:
    import ctypes
    # 2 = Per-Monitor DPI Aware (V2)
    # This prevents Windows from "stretching" the window on different monitors,
    # ensuring that 1 pixel = 1 pixel on all screens.
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception as e:
    logging.warning(f"Could not set DPI awareness: {e}")
# -----------------------------------------------

import customtkinter as ctk

# --- Monkeypatch for CTkScrollableFrame MouseWheel Error ---
# Fixes AttributeError: 'str' object has no attribute 'master'
from customtkinter.windows.widgets.ctk_scrollable_frame import CTkScrollableFrame

def safe_check_if_master_is_canvas(self, widget):
    if widget == self._parent_canvas:
        return True
    elif hasattr(widget, 'master') and widget.master is not None:
        return self.check_if_master_is_canvas(widget.master)
    else:
        # If widget is string or doesn't have master, fallback
        return False

CTkScrollableFrame.check_if_master_is_canvas = safe_check_if_master_is_canvas
# -----------------------------------------------------------

from gui.main_gui import RouletteBotGUI

APP_VERSION = "1.0.0"
# Override with env var for local testing:
# set SPINEDGE_VERSION_URL=http://localhost:3000/version.json
import os as _os
VERSION_CHECK_URL = _os.environ.get("SPINEDGE_VERSION_URL", "https://spinedge.pro/version.json")

def check_for_update(root):
    """Checks for a newer version in the background and shows a banner if found."""
    import threading, urllib.request, json, webbrowser
    from tkinter import messagebox

    def _check():
        try:
            with urllib.request.urlopen(VERSION_CHECK_URL, timeout=5) as r:
                data = json.loads(r.read())
            latest = data.get("version", APP_VERSION)
            if latest != APP_VERSION:
                download_url = data.get("download_url", "https://spinedge.pro/download")
                root.after(0, lambda: _prompt(latest, download_url))
        except Exception:
            pass  # Silent fail — no internet or server down

    def _prompt(latest, url):
        if messagebox.askyesno(
            "Update Available",
            f"SpinEdge v{latest} is available (you have v{APP_VERSION}).\n\nDownload the latest version now?",
        ):
            webbrowser.open(url)

    threading.Thread(target=_check, daemon=True).start()

if __name__ == "__main__":
    import os

    # ── Headless mode: run engine without GUI (used by start_webapp.py) ──
    if os.environ.get("SPINEDGE_HEADLESS") == "1":
        logging.info("Starting SpinEdge engine in HEADLESS mode (no GUI)...")
        # In headless mode we just keep the process alive so the React frontend
        # can communicate with it via the FastAPI bridge.  The engine automation
        # is controlled via /api/bot/control → /api/internal/command polling.
        import time
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Headless engine shutting down.")
        sys.exit(0)

    # ── Normal GUI mode ──────────────────────────────────────────────────
    # License validation is handled inside RouletteBotGUI via AuthScreen + Supabase.

    # Set AppUserModelID for Windows Taskbar Icon
    try:
        import ctypes
        myappid = 'spinedge.roulette.bot.1.0' # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception as e:
        logging.error(f"Failed to set AppUserModelID: {e}")

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()

    # Raise Tk's default font sizes so widgets that don't explicitly set a
    # font (most ttk widgets + many CTk widgets) become legible. Bumps body
    # text from ~9pt to 13pt, sets Treeview row height to 26px, etc.
    try:
        from gui.theme import apply_global_font_defaults
        apply_global_font_defaults(root)
    except Exception as _font_err:
        logging.warning(f"Could not apply global font defaults: {_font_err}")

    # Force Icon (Absolute Path)
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        ico_path = os.path.join(base_dir, "assets", "logo_new.ico")
        if os.path.exists(ico_path):
            root.iconbitmap(ico_path)
    except Exception as e:
        logging.error(f"Failed to set taskbar icon: {e}")

    app = RouletteBotGUI(root)
    root.after(3000, lambda: check_for_update(root))  # Check 3s after launch
    root.mainloop()
