"""
CollapsibleFrame — A reusable accordion/collapsible section widget for CustomTkinter.

Usage:
    from gui.components.collapsible_frame import CollapsibleFrame

    section = CollapsibleFrame(parent, title="Bot Configuration", expanded=True)
    section.pack(fill="x", padx=10, pady=5)

    # Add widgets to section.content_frame
    ctk.CTkLabel(section.content_frame, text="Hello").pack()
"""

import customtkinter as ctk
from gui.theme import (
    FONT_HEADING, FONT_BODY, GOLD, TEXT_PRIMARY,
    BORDER_SUBTLE, BG_CARD, BG_CARD_HOVER, CORNER_LARGE,
)


class CollapsibleFrame(ctk.CTkFrame):
    """
    A collapsible section with a clickable header bar.

    Parameters
    ----------
    parent : widget
        Parent container.
    title : str
        Section heading text.
    expanded : bool
        Whether the section starts open (default True).
    accent_color : str
        Color for the toggle indicator and optional left accent.
    """

    def __init__(self, parent, title="Section", expanded=True, accent_color=GOLD, **kwargs):
        kwargs.setdefault("corner_radius", CORNER_LARGE)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", BORDER_SUBTLE)
        kwargs.setdefault("fg_color", BG_CARD)
        super().__init__(parent, **kwargs)

        self._expanded = expanded
        self._accent = accent_color

        # ── Header bar (always visible) ──────────────────────────────
        self._header = ctk.CTkFrame(self, fg_color="transparent", cursor="hand2")
        self._header.pack(fill="x", padx=12, pady=(10, 0))

        self._toggle_label = ctk.CTkLabel(
            self._header, text="\u25BC" if expanded else "\u25B6",
            font=FONT_BODY, text_color=accent_color, width=20,
        )
        self._toggle_label.pack(side="left", padx=(4, 8))

        self._title_label = ctk.CTkLabel(
            self._header, text=title,
            font=FONT_HEADING, text_color=TEXT_PRIMARY,
        )
        self._title_label.pack(side="left")

        # Subtle right-side indicator
        self._status_indicator = ctk.CTkFrame(
            self._header, width=6, height=6,
            corner_radius=3, fg_color=accent_color,
        )
        self._status_indicator.pack(side="right", padx=(0, 4))

        # Make entire header clickable
        for widget in (self._header, self._toggle_label, self._title_label):
            widget.bind("<Button-1>", self._on_toggle)

        # Hover effect on header
        for widget in (self._header, self._toggle_label, self._title_label):
            widget.bind("<Enter>", self._on_header_enter)
            widget.bind("<Leave>", self._on_header_leave)

        # ── Content area (collapsible) ───────────────────────────────
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        if expanded:
            self.content_frame.pack(fill="x", padx=12, pady=(6, 10))

    # ── Public API ───────────────────────────────────────────────────

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def expand(self):
        if not self._expanded:
            self._toggle()

    def collapse(self):
        if self._expanded:
            self._toggle()

    def toggle(self):
        self._toggle()

    # ── Internals ────────────────────────────────────────────────────

    def _on_header_enter(self, event=None):
        self._title_label.configure(text_color=self._accent)
        self._header.configure(fg_color=BG_CARD_HOVER)

    def _on_header_leave(self, event=None):
        self._title_label.configure(text_color=TEXT_PRIMARY)
        self._header.configure(fg_color="transparent")

    def _on_toggle(self, event=None):
        self._toggle()

    def _toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._toggle_label.configure(text="\u25BC")
            self.content_frame.pack(fill="x", padx=12, pady=(6, 10))
        else:
            self._toggle_label.configure(text="\u25B6")
            self.content_frame.pack_forget()
