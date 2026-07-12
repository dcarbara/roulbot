"""
AnnouncementDialog — shows one announcement at a time in a modal window.
Seen announcement IDs are persisted to ~/.spinedge/seen_announcements.json
so each announcement is shown only once per device.
"""

import json
import logging
import os
import customtkinter as ctk
from gui.theme import (
    FONT_HEADING, FONT_BODY,
    TEXT_PRIMARY, TEXT_SECONDARY,
    BG_DARK, BG_CARD,
    BORDER_SUBTLE, CORNER_LARGE,
    WARNING, DANGER, INFO,
    BUTTON_GHOST, fade_in,
)

logger = logging.getLogger(__name__)

_SEEN_FILE = os.path.join(os.path.expanduser("~"), ".spinedge", "seen_announcements.json")

_TYPE_COLORS = {
    "info":     INFO,
    "warning":  WARNING,
    "critical": DANGER,
}
_TYPE_ICONS = {
    "info":     "\u2139",
    "warning":  "\u26A0",
    "critical": "\uD83D\uDD34",
}


def _load_seen() -> set:
    try:
        if os.path.exists(_SEEN_FILE):
            with open(_SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def _save_seen(seen: set):
    try:
        os.makedirs(os.path.dirname(_SEEN_FILE), exist_ok=True)
        with open(_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f)
    except Exception as e:
        logger.warning(f"Could not save seen announcements: {e}")


def show_announcements(parent, announcements: list):
    """
    Filter out already-seen announcements, then show each one in sequence.
    Call this after auth completes.

    :param parent: The root CTk window.
    :param announcements: List of dicts from license_manager.get_announcements().
    """
    if not announcements:
        return

    seen = _load_seen()
    unseen = [a for a in announcements if a.get("id") not in seen]

    if not unseen:
        return

    def _show_next(remaining):
        if not remaining:
            return
        item = remaining[0]
        rest = remaining[1:]

        def _on_close():
            seen.add(item["id"])
            _save_seen(seen)
            _show_next(rest)

        _AnnouncementModal(parent, item, _on_close)

    _show_next(unseen)


class _AnnouncementModal(ctk.CTkToplevel):
    def __init__(self, parent, announcement: dict, on_close_cb):
        super().__init__(parent)

        a_type = announcement.get("type", "info")
        title = announcement.get("title", "Announcement")
        message = announcement.get("message", "")
        color = _TYPE_COLORS.get(a_type, _TYPE_COLORS["info"])
        icon = _TYPE_ICONS.get(a_type, "\u2139")

        self.on_close_cb = on_close_cb

        self.title(f"SpinEdge \u2014 {title}")
        self.geometry("500x300")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        self.grab_set()

        # Center on screen
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 500) // 2
        y = (self.winfo_screenheight() - 300) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._dismiss)

        # ── Accent bar at top ────────────────────────────────────────
        ctk.CTkFrame(self, height=3, fg_color=color, corner_radius=0).pack(fill="x", side="top")

        # ── Content card ─────────────────────────────────────────────
        card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=CORNER_LARGE,
                            border_width=1, border_color=BORDER_SUBTLE)
        card.pack(fill="both", expand=True, padx=24, pady=(16, 24))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)

        # Icon + Title row
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.pack(fill="x", pady=(0, 14))

        ctk.CTkLabel(
            header, text=icon, font=(None, 20), text_color=color, width=30
        ).pack(side="left")

        ctk.CTkLabel(
            header, text=title,
            font=FONT_HEADING, text_color=TEXT_PRIMARY, anchor="w"
        ).pack(side="left", padx=(10, 0))

        # Message
        ctk.CTkLabel(
            body, text=message,
            font=FONT_BODY, text_color=TEXT_SECONDARY,
            wraplength=430, justify="left", anchor="w"
        ).pack(fill="x")

        # Dismiss button
        btn_kwargs = {**BUTTON_GHOST}
        btn_kwargs["border_color"] = color
        btn_kwargs["text_color"] = color
        btn_kwargs["hover_color"] = BG_CARD

        ctk.CTkButton(
            body, text="Got it", width=120, height=36,
            command=self._dismiss, **btn_kwargs,
        ).pack(anchor="e", pady=(20, 0))

        fade_in(self, target_alpha=1.0, duration_ms=200)

    def _dismiss(self):
        self.grab_release()
        self.destroy()
        if self.on_close_cb:
            self.on_close_cb()
