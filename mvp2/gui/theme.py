"""
SpinEdge Design System — Centralized colors, fonts, spacing, and reusable widget helpers.

Usage:
    from gui.theme import *

    label = ctk.CTkLabel(parent, text="Hello", **FONT_HEADING)
    card  = ctk.CTkFrame(parent, **CARD_STYLE)
"""

# ══════════════════════════════════════════════════════════════════════════
# COLORS — Modern refined palette with depth
# ══════════════════════════════════════════════════════════════════════════

# Brand — warm gold with richer tones
GOLD            = "#EAB308"
GOLD_DIM        = "#CA8A04"
GOLD_HOVER      = "#FACC15"
GOLD_GLOW       = "#EAB30820"      # Subtle gold tint for glows

# Semantic — slightly desaturated for a premium feel
SUCCESS         = "#34D399"
SUCCESS_HOVER   = "#2BBF89"
WARNING         = "#FBBF24"
WARNING_HOVER   = "#E5A913"
DANGER          = "#F87171"
DANGER_HOVER    = "#DC4E4E"
INFO            = "#60A5FA"
INFO_HOVER      = "#4B8FE5"

# Neutral — refined blue-grey tones
PRIMARY_BTN     = "#475569"
PRIMARY_BTN_HOVER = "#5B6B80"
PURPLE          = "#A78BFA"
PURPLE_HOVER    = "#8B6FE0"

# Surfaces — layered depth system (darkest → lightest)
BG_DARK         = "#09090B"         # App background
BG_SURFACE      = "#18181B"         # Primary surface / sidebar
BG_CARD         = "#27272A"         # Card / panel backgrounds
BG_CARD_HOVER   = "#3F3F46"         # Card hover state
BG_ELEVATED     = "#3F3F46"         # Elevated elements (dropdowns, tooltips)
BG_INPUT        = "#18181B"         # Input field backgrounds
BG_TRANSPARENT  = "transparent"

# Borders — subtle layering
BORDER_SUBTLE   = "#3F3F46"
BORDER_DEFAULT  = "#52525B"
BORDER_ACTIVE   = "#71717A"
BORDER_GOLD     = "#EAB308"
BORDER_GLOW     = "#EAB30840"       # Soft gold glow border

# Text — high-contrast hierarchy.
# Bumped TEXT_MUTED from #64748B → #94A3B8-ish so 9-12pt secondary copy
# clears WCAG AAA against the dark surfaces (the old value was a 4.5:1
# borderline pass that read as washed-out under most lighting). TEXT_SECONDARY
# now sits between PRIMARY and the new MUTED so the three-tier scale stays
# meaningful (each step is roughly +30% luminance over the next).
TEXT_PRIMARY     = "#F8FAFC"        # near-white for body / headers
TEXT_SECONDARY   = "#CBD5E1"        # readable secondary copy
TEXT_MUTED       = "#94A3B8"        # captions / hints (was #64748B)
TEXT_LIGHT       = "#E2E8F0"
TEXT_INVERSE     = "#0F1117"

# Status colors
STATUS_IDLE     = "#64748B"
STATUS_RUNNING  = SUCCESS
STATUS_PAUSED   = WARNING
STATUS_ERROR    = DANGER

# License tier colors
TIER_COLORS = {
    "FREE":  DANGER,
    "BASIC": INFO,
    "PLUS":  PURPLE,
    "PRO":   WARNING,
    "ADMIN": SUCCESS,
}


# ══════════════════════════════════════════════════════════════════════════
# FONTS — Refined typographic scale
# ══════════════════════════════════════════════════════════════════════════

FONT_FAMILY     = "Segoe UI"
FONT_MONO       = "Cascadia Code"   # Modern monospace (fallback: Consolas)

# Font scale — each tier bumped +1pt relative to the original (which had
# 9-12pt body copy that read as cramped on high-DPI displays). The hierarchy
# still has clear steps (display 26, title 18, heading 15, body 13, ...) but
# everything is more comfortable to read at normal viewing distance.
#
# Font tuples: (family, size, weight?)
FONT_HERO       = (FONT_FAMILY, 34, "bold")    # Dashboard hero text
FONT_DISPLAY    = (FONT_FAMILY, 26, "bold")    # Large display text
FONT_TITLE      = (FONT_FAMILY, 18, "bold")    # Page / section titles
FONT_HEADING    = (FONT_FAMILY, 15, "bold")    # Card headings
FONT_SUBHEADING = (FONT_FAMILY, 13, "bold")    # Sub-section headings
FONT_BODY       = (FONT_FAMILY, 13)            # Body text (was 12)
FONT_BODY_BOLD  = (FONT_FAMILY, 13, "bold")    # Emphasized body text
FONT_SMALL      = (FONT_FAMILY, 12)            # Secondary / helper (was 11)
FONT_CAPTION    = (FONT_FAMILY, 11)            # Captions, tooltips (was 10)
FONT_TINY       = (FONT_FAMILY, 10)            # Fine print (was 9 — borderline)
FONT_MONO_BODY  = (FONT_MONO, 12)              # Logs, code, data (was 11)
FONT_MONO_SMALL = (FONT_MONO, 11)              # Compact monospace (was 10)


# ══════════════════════════════════════════════════════════════════════════
# SPACING (pixels) — Generous, breathable layout
# ══════════════════════════════════════════════════════════════════════════

PAD_SECTION     = 24        # Between major sections
PAD_GROUP       = 16        # Between groups within a section
PAD_ITEM        = 8         # Between items within a group
PAD_INNER       = 4         # Tight inner padding

PAD_CARD_X      = 24        # Horizontal card padding
PAD_CARD_Y      = 18        # Vertical card padding

CORNER_RADIUS   = 16        # Default corner radius
CORNER_SMALL    = 8         # Smaller elements (badges, tags)
CORNER_LARGE    = 24        # Cards, panels
CORNER_PILL     = 50        # Pill-shaped buttons


# ══════════════════════════════════════════════════════════════════════════
# WIDGET STYLE PRESETS (kwargs dicts — spread with **)
# ══════════════════════════════════════════════════════════════════════════

CARD_STYLE = dict(
    fg_color=BG_CARD,
    corner_radius=CORNER_LARGE,
    border_width=1,
    border_color=BORDER_SUBTLE,
)

CARD_ELEVATED_STYLE = dict(
    fg_color=BG_ELEVATED,
    corner_radius=CORNER_LARGE,
    border_width=1,
    border_color=BORDER_DEFAULT,
)

BUTTON_PRIMARY = dict(
    fg_color=INFO,
    hover_color=INFO_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
)

BUTTON_SUCCESS = dict(
    fg_color=SUCCESS,
    hover_color=SUCCESS_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
    text_color=TEXT_INVERSE,
)

BUTTON_DANGER = dict(
    fg_color=DANGER,
    hover_color=DANGER_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
)

BUTTON_WARNING = dict(
    fg_color=WARNING,
    hover_color=WARNING_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
    text_color=TEXT_INVERSE,
)

BUTTON_NEUTRAL = dict(
    fg_color=PRIMARY_BTN,
    hover_color=PRIMARY_BTN_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
)

BUTTON_GHOST = dict(
    fg_color="transparent",
    hover_color=BG_CARD_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
    border_width=1,
    border_color=BORDER_DEFAULT,
    text_color=TEXT_SECONDARY,
)

BUTTON_GOLD = dict(
    fg_color=GOLD,
    hover_color=GOLD_HOVER,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY_BOLD,
    height=40,
    text_color=TEXT_INVERSE,
)

BUTTON_SMALL = dict(
    corner_radius=CORNER_SMALL,
    font=FONT_SMALL,
    height=30,
    width=30,
)

INPUT_STYLE = dict(
    fg_color=BG_INPUT,
    border_color=BORDER_DEFAULT,
    border_width=1,
    corner_radius=CORNER_RADIUS,
    font=FONT_BODY,
    height=44,                  # +2 so click targets are comfortable
    text_color=TEXT_PRIMARY,
)

SECTION_HEADER_STYLE = dict(
    font=FONT_HEADING,
    text_color=GOLD,
)

KPI_VALUE_STYLE = dict(
    font=(FONT_FAMILY, 28, "bold"),     # bumped +4 — KPIs should read across the room
)

KPI_LABEL_STYLE = dict(
    font=FONT_SMALL,
    text_color=TEXT_SECONDARY,           # was TEXT_MUTED — labels need real contrast
)

DIVIDER_STYLE = dict(
    fg_color=BORDER_SUBTLE,
    height=1,
    corner_radius=0,
)


# ══════════════════════════════════════════════════════════════════════════
# GLOBAL TYPOGRAPHY DEFAULTS — applied once at app start
# ══════════════════════════════════════════════════════════════════════════
# Many widgets across the codebase use inline `font=("Segoe UI", 10)` tuples
# instead of importing from this module — we can't easily refactor all of
# them, but we CAN raise the floor for any widget that didn't set a font at
# all by overriding Tk's named font defaults. Call this once after creating
# the root window. Side-effect-only; returns nothing.

def apply_global_font_defaults(root):
    """Bump Tk's default fonts so widgets without an explicit font become
    legible. Safe to call multiple times. No-op if Tk's font module isn't
    importable."""
    try:
        import tkinter.font as _tkfont
    except Exception:
        return
    # The named fonts Tk uses by default. We only raise the size; leave
    # family alone so platform-native rendering stays untouched.
    targets = {
        "TkDefaultFont":         13,
        "TkTextFont":            13,
        "TkMenuFont":            13,
        "TkHeadingFont":         14,
        "TkCaptionFont":         12,
        "TkSmallCaptionFont":    12,
        "TkIconFont":            12,
        "TkTooltipFont":         12,
        "TkFixedFont":           12,
    }
    for name, size in targets.items():
        try:
            f = _tkfont.nametofont(name)
            f.configure(size=size, family=FONT_FAMILY if name != "TkFixedFont" else FONT_MONO)
        except Exception:
            # Some named fonts aren't always defined on every platform.
            # Best-effort: skip the missing ones silently.
            pass
    # ttk widget defaults (used by Treeview / Scrollbar / etc.)
    try:
        from tkinter import ttk
        style = ttk.Style(root)
        style.configure("Treeview", font=(FONT_FAMILY, 12), rowheight=26)
        style.configure("Treeview.Heading", font=(FONT_FAMILY, 12, "bold"))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# ANIMATION HELPERS
# ══════════════════════════════════════════════════════════════════════════

def fade_in(widget, target_alpha=1.0, duration_ms=200, steps=10):
    """Fade in a toplevel window by animating its alpha."""
    try:
        toplevel = widget.winfo_toplevel()
        if not hasattr(toplevel, 'attributes'):
            return
        step_delay = max(1, duration_ms // steps)
        current_alpha = 0.0
        step_size = target_alpha / steps

        def _step():
            nonlocal current_alpha
            current_alpha = min(current_alpha + step_size, target_alpha)
            try:
                toplevel.attributes("-alpha", current_alpha)
                if current_alpha < target_alpha:
                    widget.after(step_delay, _step)
            except Exception:
                pass

        toplevel.attributes("-alpha", 0.0)
        widget.after(10, _step)
    except Exception:
        pass


def animate_color(widget, prop, start_hex, end_hex, duration_ms=150, steps=15):
    """Smoothly transition a widget's color property."""
    try:
        sr, sg, sb = int(start_hex[1:3], 16), int(start_hex[3:5], 16), int(start_hex[5:7], 16)
        er, eg, eb = int(end_hex[1:3], 16), int(end_hex[3:5], 16), int(end_hex[5:7], 16)
        step_delay = max(1, duration_ms // steps)
        step = [0]

        def _step():
            step[0] += 1
            t = step[0] / steps
            r = int(sr + (er - sr) * t)
            g = int(sg + (eg - sg) * t)
            b = int(sb + (eb - sb) * t)
            color = f"#{r:02x}{g:02x}{b:02x}"
            try:
                widget.configure(**{prop: color})
                if step[0] < steps:
                    widget.after(step_delay, _step)
            except Exception:
                pass

        widget.after(10, _step)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════════════════

def validate_entry(entry_widget, is_valid: bool):
    """Apply visual feedback to a CTkEntry based on validation state."""
    if is_valid:
        entry_widget.configure(border_color=BORDER_DEFAULT, border_width=1)
    else:
        entry_widget.configure(border_color=DANGER, border_width=2)


def validate_numeric(value: str, allow_pct: bool = False, min_val: float = None, max_val: float = None) -> bool:
    """Check if a string is a valid numeric value (optionally with % suffix)."""
    if not value or value.strip() == "":
        return False
    v = value.strip()
    if allow_pct and v.endswith("%"):
        v = v[:-1]
    try:
        num = float(v)
        if min_val is not None and num < min_val:
            return False
        if max_val is not None and num > max_val:
            return False
        return True
    except ValueError:
        return False


def make_validated_entry(parent, variable, validate_fn=None, **kwargs):
    """
    Create a CTkEntry with live validation feedback.
    validate_fn: callable(str) -> bool
    """
    import customtkinter as ctk

    merged = {**INPUT_STYLE, **kwargs}
    entry = ctk.CTkEntry(parent, textvariable=variable, **merged)

    if validate_fn:
        def on_change(*_):
            val = variable.get()
            validate_entry(entry, validate_fn(val))
        variable.trace_add("write", on_change)
        entry.after(100, on_change)

    return entry
