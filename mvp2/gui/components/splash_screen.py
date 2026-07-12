"""Startup / loading splash shown after login while the main UI builds.

`create_widgets()` in main_gui takes a few seconds (it's a large UI), during
which the main window is hidden. This borderless splash fills that gap with the
logo, an indeterminate progress bar, and a status line that the caller advances
through milestones via `set_status()`.

Usage (all on the Tk main thread):
    splash = SplashScreen(root)
    splash.set_status("Loading interface…")
    ...                       # heavy build steps, calling set_status between them
    splash.close()

The splash drives its own `update()` so it stays painted/animated even while the
caller's build work blocks the main loop.
"""

import os
import logging

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Local copies of the theme tokens so the splash never fails to import if the
# theme module changes. Kept in sync with gui/theme.py.
_BG_DARK = "#09090B"
_BG_CARD = "#18181B"
_BORDER = "#3F3F46"
_GOLD = "#EAB308"
_TEXT_PRIMARY = "#F8FAFC"
_TEXT_MUTED = "#94A3B8"


class SplashScreen:
    def __init__(self, root, title="SpinEdge", subtitle="Starting up…"):
        self.root = root
        self._closed = False
        self._win = ctk.CTkToplevel(root)
        self._win.title(title)
        # Borderless, always-on-top, no taskbar entry.
        self._win.overrideredirect(True)
        try:
            self._win.attributes("-topmost", True)
        except Exception:
            pass

        w, h = 380, 240
        try:
            sw = self._win.winfo_screenwidth()
            sh = self._win.winfo_screenheight()
            x = int((sw / 2) - (w / 2))
            y = int((sh / 2) - (h / 2))
        except Exception:
            x, y = 200, 200
        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._win.configure(fg_color=_BG_DARK)

        card = ctk.CTkFrame(self._win, fg_color=_BG_CARD, corner_radius=16,
                            border_width=1, border_color=_BORDER)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        # Logo (optional — load the .ico via PIL if available).
        self._logo_img = self._load_logo()
        if self._logo_img is not None:
            ctk.CTkLabel(card, image=self._logo_img, text="").pack(pady=(28, 8))
        else:
            ctk.CTkLabel(card, text="🎯", font=("Segoe UI", 40)).pack(pady=(28, 8))

        ctk.CTkLabel(card, text=title, font=("Segoe UI", 22, "bold"),
                     text_color=_TEXT_PRIMARY).pack()

        self._status_var = ctk.StringVar(value=subtitle)
        ctk.CTkLabel(card, textvariable=self._status_var, font=("Segoe UI", 12),
                     text_color=_TEXT_MUTED).pack(pady=(6, 14))

        self._bar = ctk.CTkProgressBar(card, width=260, height=6,
                                       progress_color=_GOLD, corner_radius=3)
        self._bar.pack(pady=(0, 24))
        try:
            self._bar.configure(mode="indeterminate")
            self._bar.start()
        except Exception:
            self._bar.set(0.4)

        self._pump()

    def _load_logo(self):
        try:
            from PIL import Image
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            for name in ("logo_new.ico", "logo.ico"):
                path = os.path.join(base_dir, "assets", name)
                if os.path.exists(path):
                    img = Image.open(path)
                    return ctk.CTkImage(light_image=img, dark_image=img, size=(72, 72))
        except Exception as e:
            logger.debug(f"[Splash] logo load skipped: {e}")
        return None

    def _pump(self):
        """Force a paint so the splash renders/animates while the caller's
        synchronous build work blocks the Tk main loop."""
        if self._closed:
            return
        try:
            self._win.update_idletasks()
            self._win.update()
        except Exception:
            pass

    def set_status(self, text):
        """Update the status line and repaint. Safe to call repeatedly."""
        if self._closed:
            return
        try:
            self._status_var.set(text)
        except Exception:
            pass
        self._pump()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._bar.stop()
        except Exception:
            pass
        try:
            self._win.destroy()
        except Exception:
            pass
