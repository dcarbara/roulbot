import customtkinter as ctk
import tkinter as tk
from gui.theme import (
    GOLD, TEXT_PRIMARY,
    FONT_CAPTION,
)

# Board-specific colors
_BOARD_BG = "#0D0F14"
_CELL_RED = "#C0392B"
_CELL_BLACK = "#1C1C2E"
_CELL_GREEN = "#1E8449"
_CELL_OUTLINE = "#2A2F45"
_CHIP_FILL = GOLD
_CHIP_OUTLINE = "#D4A017"
_CHIP_INNER = "#FFF8DC"
_SELECTED_OUTLINE = "#F1C40F"  # Gold highlight for selected cells
_HOVER_OUTLINE = "#5DADE2"     # Blue highlight on hover


class RouletteBoard(ctk.CTkFrame):
    """
    A visual representation of a European Roulette board using a Tkinter Canvas.
    Cells are clickable to toggle bet label selection for the strategy builder.
    """

    NUMBERS = [
        0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26
    ]

    RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

    def __init__(self, master, width=800, height=250, **kwargs):
        kwargs.setdefault("fg_color", _BOARD_BG)
        kwargs.setdefault("corner_radius", 12)
        super().__init__(master, **kwargs)

        self.canvas_width = width
        self.canvas_height = height

        self.hit_areas = {}       # label -> (cx, cy) center point
        self.cell_rects = {}      # label -> (x, y, x2, y2) bounding box
        self.number_grid = {}
        self.active_chips = []
        self.selected_labels = set()  # Currently selected labels
        self._on_click_callback = None  # Callback when cell is clicked
        self._on_unit_edit_callback = None  # Callback when unit is edited on board
        self._hover_rect = None   # Canvas item for hover highlight
        self._unit_edit_mode = False  # When True, clicking selected cell edits units
        self._unit_popup = None   # Active unit edit popup widget
        self._label_units = {}    # label -> int units (for chip display)
        self._pending_click_id = None  # Deferred single-click (so double-click can cancel)
        self._pending_click_label = None
        self._mult_frame = None       # 2x / 1/2 button frame
        self._mult_window = None      # Canvas window id for multiplier buttons

        self.canvas = tk.Canvas(
            self, width=self.canvas_width, height=self.canvas_height,
            bg=_BOARD_BG, highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)

        self.bind("<Configure>", self.on_resize)
        self.draw_board()

    def set_click_callback(self, callback):
        """Set callback for cell clicks. callback(label: str, selected: bool)"""
        self._on_click_callback = callback

    def set_unit_edit_callback(self, callback):
        """Set callback for unit edits on board. callback(label: str, units: int)"""
        self._on_unit_edit_callback = callback

    def set_unit_edit_mode(self, enabled):
        """Enable/disable unit editing on click of selected cells."""
        self._unit_edit_mode = enabled
        self._update_multiplier_buttons()

    def _update_multiplier_buttons(self):
        """Show/hide 2x and 1/2 pill buttons based on unit edit mode."""
        if self._unit_edit_mode and self.selected_labels:
            if self._mult_frame is None:
                self._mult_frame = tk.Frame(self.canvas, bg=_BOARD_BG, highlightthickness=0)
                # Shared style: small pill-shaped buttons matching board palette
                common = {"font": ("Arial", 8), "relief": "flat", "cursor": "hand2",
                          "bd": 0, "highlightthickness": 0, "padx": 6, "pady": 1}
                self._btn_half = tk.Button(
                    self._mult_frame, text="\u00f7 2", bg=_CELL_OUTLINE, fg="#AAB0C0",
                    activebackground="#3A4060", activeforeground="#CDD0DA",
                    command=self._halve_all_units, **common)
                self._btn_half.pack(side="left", padx=(0, 2))
                self._btn_2x = tk.Button(
                    self._mult_frame, text="\u00d7 2", bg=_CELL_OUTLINE, fg="#AAB0C0",
                    activebackground="#3A4060", activeforeground="#CDD0DA",
                    command=self._double_all_units, **common)
                self._btn_2x.pack(side="left")
                self._mult_window = self.canvas.create_window(
                    self.canvas_width - 8, 4, window=self._mult_frame, anchor="ne")
        else:
            if self._mult_window:
                self.canvas.delete(self._mult_window)
                self._mult_window = None
                self._mult_frame = None

    def _double_all_units(self):
        """Double all selected label units."""
        self._scale_all_units(2)

    def _halve_all_units(self):
        """Halve all selected label units (min 1)."""
        self._scale_all_units(0.5)

    def _scale_all_units(self, factor):
        """Multiply all selected label units by factor."""
        if not self.selected_labels:
            return
        for label in self.selected_labels:
            key = label.lower()
            current = self._label_units.get(key, 1)
            new_val = max(1, int(round(current * factor)))
            self._label_units[key] = new_val
            if self._on_unit_edit_callback:
                self._on_unit_edit_callback(label, new_val)
        self._refresh_unit_chips()

    def set_label_units(self, units_dict):
        """Set units for labels and refresh chip display. units_dict: {label: int}"""
        self._label_units = {k.lower(): v for k, v in units_dict.items()}

    def update_label_unit(self, label, units):
        """Update a single label's unit value and refresh its chip."""
        self._label_units[label.lower()] = units

    def on_resize(self, event):
        if self.canvas_width == event.width and self.canvas_height == event.height:
            return
        self.canvas_width = event.width
        self.canvas_height = event.height
        self.draw_board()
        self._redraw_selections()
        # Reposition multiplier buttons
        if hasattr(self, '_mult_window') and self._mult_window:
            self.canvas.coords(self._mult_window, self.canvas_width - 10, 5)

    def _num_pos(self, num):
        """Return (col, row) grid position for number 1-36.
        col: 0-11, row: 0(top/row3) 1(mid/row2) 2(bot/row1)"""
        col = (num - 1) // 3
        row = 2 - ((num - 1) % 3)
        return col, row

    def draw_board(self):
        """Draws the full roulette betting layout with split/corner/street hit zones."""
        self.canvas.delete("all")
        self.hit_areas = {}
        self.cell_rects = {}
        self.number_grid = {}

        w = self.canvas_width
        h = self.canvas_height

        margin_left = 60
        margin_right = 50
        margin_top = 20
        grid_width = w - margin_left - margin_right
        cw = grid_width / 12   # cell width
        ch = (h * 0.5) / 3     # cell height

        # ── Zero ──
        zero_x, zero_y = 10, margin_top
        zero_w, zero_h = margin_left - 10, ch * 3
        self.draw_cell("0", zero_x, zero_y, zero_w, zero_h, color=_CELL_GREEN)
        self.number_grid[0] = (zero_x, zero_y, zero_w, zero_h)

        # ── Numbers 1-36 ──
        for num in range(1, 37):
            col, row = self._num_pos(num)
            x = margin_left + col * cw
            y = margin_top + row * ch
            color = _CELL_RED if num in self.RED_NUMBERS else _CELL_BLACK
            self.draw_cell(str(num), x, y, cw, ch, color=color, text_color=TEXT_PRIMARY)
            self.number_grid[num] = (x, y, cw, ch)

        # ── Column Bets (2to1) ──
        for row, label in enumerate(["col3", "col2", "col1"]):
            x = margin_left + 12 * cw
            y = margin_top + row * ch
            self.draw_cell(label, x, y, margin_right - 5, ch, text="2to1", color="transparent")

        # ── Dozens ──
        dozens_y = margin_top + ch * 3 + 5
        dozens_h = ch * 0.8
        dw = 12 * cw / 3
        self.draw_cell("1st12", margin_left, dozens_y, dw, dozens_h, text="1st 12", color="transparent")
        self.draw_cell("2nd12", margin_left + dw, dozens_y, dw, dozens_h, text="2nd 12", color="transparent")
        self.draw_cell("3rd12", margin_left + dw * 2, dozens_y, dw, dozens_h, text="3rd 12", color="transparent")

        # ── Even Chances ──
        chances_y = dozens_y + dozens_h + 5
        chances_h = ch * 0.8
        ew = 12 * cw / 6
        for i, (display, key) in enumerate([
            ("1-18", "1to18"), ("Even", "even"), ("Red", "red"),
            ("Black", "black"), ("Odd", "odd"), ("19-36", "19to36")
        ]):
            bg = _CELL_RED if key == "red" else (_CELL_BLACK if key == "black" else "transparent")
            self.draw_cell(key, margin_left + i * ew, chances_y, ew, chances_h, text=display, color=bg)

        # ── Invisible hit zones for splits, corners, streets, double streets ──
        self._draw_inside_bet_zones(margin_left, margin_top, cw, ch)

    def _draw_inside_bet_zones(self, ml, mt, cw, ch):
        """Register invisible hit areas for splits, corners, streets, and double streets."""
        hit_pad = max(10, min(cw, ch) * 0.22)  # hit zone half-size (generous for clickability)

        def _center(num):
            """Center coords for number 1-36."""
            col, row = self._num_pos(num)
            return ml + col * cw + cw / 2, mt + row * ch + ch / 2

        def _register(label, cx, cy, pad_override=None):
            """Register an invisible hit area (no drawn rectangle)."""
            key = label.lower()
            p = pad_override if pad_override is not None else hit_pad
            self.hit_areas[key] = (cx, cy)
            self.cell_rects[key] = (cx - p, cy - p, cx + p, cy + p)

        # --- Horizontal Splits (same column, adjacent rows: n and n+1 where n%3!=0) ---
        for n in range(1, 37):
            if n % 3 == 0:
                continue  # top of column group, no horizontal neighbor above
            n2 = n + 1
            cx1, cy1 = _center(n)
            cx2, cy2 = _center(n2)
            mx, my = (cx1 + cx2) / 2, (cy1 + cy2) / 2
            _register(f"{n}-{n2}split", mx, my)

        # --- Vertical Splits (same row, adjacent columns: n and n+3) ---
        for n in range(1, 34):
            n2 = n + 3
            col1, row1 = self._num_pos(n)
            col2, row2 = self._num_pos(n2)
            if row1 != row2:
                continue  # not same row
            cx1, cy1 = _center(n)
            cx2, cy2 = _center(n2)
            mx, my = (cx1 + cx2) / 2, (cy1 + cy2) / 2
            _register(f"{n}-{n2}split", mx, my)

        # --- Zero splits: 0-1, 0-2, 0-3 ---
        # Positioned at the boundary between zero cell and number grid
        # Use a larger hit zone since these are at the board edge
        zero_pad = max(hit_pad * 1.3, 12)
        for n in [1, 2, 3]:
            cx, cy = _center(n)
            mx = ml  # right edge of zero / left edge of number grid
            my = cy
            _register(f"0-{n}split", mx, my, pad_override=zero_pad)

        # --- Corners (intersection of 4 numbers) ---
        # Corner between n, n+1, n+3, n+4 where n%3 != 0 and n+4 <= 36
        for n in range(1, 34):
            if n % 3 == 0:
                continue
            n2, n3, n4 = n + 1, n + 3, n + 3 + 1
            if n4 > 36:
                continue
            col_a, row_a = self._num_pos(n)
            col_b, row_b = self._num_pos(n4)
            # Corner is at the intersection of the four cells
            cx = ml + (col_a + 1) * cw  # right edge of left column
            cy = mt + (min(row_a, row_b) + 1) * ch  # bottom of top row
            # Use the canonical naming from ROULETTE_NUMBER_MAPPINGS
            lo = min(n, n2, n3, n4)
            hi = max(n, n2, n3, n4)
            _register(f"{lo}-{hi}corner", cx, cy)

        # --- Streets (bottom edge of row, covers 3 numbers: n, n+1, n+2) ---
        for start in range(1, 35, 3):
            col, _ = self._num_pos(start)
            # Street hit zone at the bottom edge of the column
            cx = ml + col * cw + cw / 2
            cy = mt + ch * 3  # bottom of number grid
            _register(f"{start}-{start+2}strt", cx, cy)

        # --- Double Streets (between two adjacent 3-number columns) ---
        # Placed at the boundary between columns, at the bottom edge of the grid
        for start in range(1, 34, 3):
            next_start = start + 3
            if next_start > 36:
                continue
            col2, _ = self._num_pos(next_start)
            # cx = left edge of the second column = boundary between the two groups
            cx = ml + col2 * cw
            cy = mt + ch * 3  # bottom edge of number grid
            _register(f"{start}-{start+5}dblstrt", cx, cy)

        # 0-3 double street (boundary between zero and column 0)
        _register("0-3dblstrt", ml, mt + ch * 3)

    def draw_cell(self, label, x, y, w, h, text=None, color="transparent", text_color=TEXT_PRIMARY, outline=_CELL_OUTLINE):
        """Draws a single betting cell with subtle rounded feel"""
        if text is None:
            text = label
        fill_color = "" if color == "transparent" else color
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=fill_color, outline=outline, width=1)
        self.canvas.create_text(x + w / 2, y + h / 2, text=text, fill=text_color, font=FONT_CAPTION)
        key = str(label).lower()
        self.hit_areas[key] = (x + w / 2, y + h / 2)
        self.cell_rects[key] = (x, y, x + w, y + h)

    # ── Click-to-select ──────────────────────────────────────────────

    def _label_at(self, cx, cy):
        """Return the bet label at canvas coordinates, or None.
        Prioritizes smaller hit zones (splits/corners) over larger cells."""
        best = None
        best_area = float('inf')
        for label, (x1, y1, x2, y2) in self.cell_rects.items():
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                area = (x2 - x1) * (y2 - y1)
                if area < best_area:
                    best = label
                    best_area = area
        return best

    def _on_canvas_click(self, event):
        """Left-click: select cell (1 unit) or schedule increment (deferred so double-click can cancel)."""
        # Dismiss any open unit popup first
        if self._unit_popup:
            # Check if clicking on a different cell — if so, dismiss but continue processing
            clicked_label = self._label_at(event.x, event.y)
            popup_label = getattr(self, '_unit_popup_label', None)
            self._dismiss_unit_popup()
            if clicked_label == popup_label or clicked_label is None:
                return  # Clicked same cell or empty area — just dismiss

        label = self._label_at(event.x, event.y)
        if label is None:
            return

        if self._unit_edit_mode and label in self.selected_labels:
            # Already selected in unit mode → defer increment (double-click may cancel it)
            self._pending_click_label = label
            self._pending_click_id = self.after(250, self._execute_pending_click)
            return

        # Not selected → select with 1 unit
        if label in self.selected_labels:
            self.selected_labels.discard(label)
            selected = False
        else:
            self.selected_labels.add(label)
            if self._unit_edit_mode:
                self._label_units[label.lower()] = 1
            selected = True

        self._redraw_selections()
        if self._unit_edit_mode:
            self._refresh_unit_chips()

        if self._on_click_callback:
            self._on_click_callback(label, selected)

    def _execute_pending_click(self):
        """Execute the deferred single-click increment."""
        label = getattr(self, '_pending_click_label', None)
        self._pending_click_id = None
        if label is None or label not in self.selected_labels:
            return
        key = label.lower()
        current = self._label_units.get(key, 1)
        self._label_units[key] = current + 1
        self._refresh_unit_chips()
        if self._on_unit_edit_callback:
            self._on_unit_edit_callback(label, current + 1)

    def _on_canvas_double_click(self, event):
        """Double-click: cancel pending increment and open unit editor popup."""
        # Cancel pending single-click increment
        if getattr(self, '_pending_click_id', None):
            self.after_cancel(self._pending_click_id)
            self._pending_click_id = None

        if not self._unit_edit_mode:
            return
        label = self._label_at(event.x, event.y)
        if label is None or label not in self.selected_labels:
            return
        self._show_unit_popup(label, event.x, event.y)

    def _on_canvas_right_click(self, event):
        """Right-click: deselect and clear units for a cell."""
        self._dismiss_unit_popup()
        label = self._label_at(event.x, event.y)
        if label is None or label not in self.selected_labels:
            return
        self.selected_labels.discard(label)
        self._label_units.pop(label.lower(), None)
        self._redraw_selections()
        if self._unit_edit_mode:
            self._refresh_unit_chips()
        if self._on_click_callback:
            self._on_click_callback(label, False)

    def _show_unit_popup(self, label, x, y):
        """Show a small entry popup on the canvas to edit units for a label."""
        self._dismiss_unit_popup()

        current_units = self._label_units.get(label.lower(), 1)

        # Unique ID for this popup instance — used to prevent stale FocusOut from killing a newer popup
        import random
        popup_id = random.randint(0, 2**31)
        self._unit_popup_id = popup_id

        # Create a small frame with entry + hint
        popup = tk.Frame(self.canvas, bg="#1E1E2E", bd=2, relief="solid",
                         highlightbackground=_SELECTED_OUTLINE, highlightthickness=1)

        hint = tk.Label(popup, text=f"{label} units:", bg="#1E1E2E", fg="#E0E0E0",
                        font=("Arial", 8))
        hint.pack(side="left", padx=(4, 2))

        var = tk.StringVar(value=str(current_units))
        entry = tk.Entry(popup, textvariable=var, width=4, font=("Arial", 10, "bold"),
                         bg="#2A2F45", fg="#FFFFFF", insertbackground="#FFFFFF",
                         justify="center", relief="flat")
        entry.pack(side="left", padx=(0, 4), pady=2)
        entry.select_range(0, tk.END)
        entry.focus_set()

        self._unit_popup_label = label
        self._unit_popup_var = var
        self._unit_popup_committed = False

        def commit(*_args):
            # If a newer popup has replaced us, this FocusOut is stale — ignore it
            if getattr(self, '_unit_popup_id', None) != popup_id:
                return
            # Prevent double commit (FocusOut + Return, or FocusOut + click dismiss)
            if self._unit_popup_committed:
                return
            self._unit_popup_committed = True
            try:
                val = int(var.get())
                if val < 1:
                    val = 1
            except (ValueError, tk.TclError):
                val = 1
            self._label_units[label.lower()] = val
            self._dismiss_unit_popup()
            # Refresh chip display
            self._refresh_unit_chips()
            if self._on_unit_edit_callback:
                self._on_unit_edit_callback(label, val)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda _: self._dismiss_unit_popup())

        # Position popup near the click, clamped to canvas bounds
        pw, ph = 120, 28
        px = min(x, self.canvas_width - pw - 5)
        py = max(5, y - ph - 5)
        self._unit_popup = self.canvas.create_window(px, py, window=popup, anchor="nw")

    def _dismiss_unit_popup(self):
        """Remove the unit editing popup."""
        if self._unit_popup:
            self.canvas.delete(self._unit_popup)
            self._unit_popup = None

    def _refresh_unit_chips(self):
        """Redraw chips for all selected labels showing their unit counts."""
        self.clear_chips()
        for label in self.selected_labels:
            coords = self.get_bet_coordinates(label)
            if coords:
                units = self._label_units.get(label.lower(), 1)
                small = self._is_inside_bet(label)
                self.draw_chip(coords[0], coords[1], str(units), small=small)

    def _is_inside_bet(self, label):
        """Check if label is a split, corner, street, or double street."""
        return any(k in label for k in ('split', 'corner', 'strt'))

    def _on_canvas_motion(self, event):
        """Show hover highlight on cells"""
        if self._hover_rect:
            self.canvas.delete(self._hover_rect)
            self._hover_rect = None

        label = self._label_at(event.x, event.y)
        if label and label not in self.selected_labels:
            if self._is_inside_bet(label):
                # Small circle for inside bets (splits/corners/streets)
                cx, cy = self.hit_areas[label]
                r = 8
                self._hover_rect = self.canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    outline=_HOVER_OUTLINE, width=2, fill=""
                )
            else:
                x1, y1, x2, y2 = self.cell_rects[label]
                self._hover_rect = self.canvas.create_rectangle(
                    x1, y1, x2, y2,
                    outline=_HOVER_OUTLINE, width=2, fill=""
                )
            self.canvas.configure(cursor="hand2")
        else:
            self.canvas.configure(cursor="")

    def _on_canvas_leave(self, _event):
        if self._hover_rect:
            self.canvas.delete(self._hover_rect)
            self._hover_rect = None
        self.canvas.configure(cursor="")

    def _redraw_selections(self):
        """Redraw gold outlines on all selected cells"""
        self.canvas.delete("selection_highlight")
        for label in self.selected_labels:
            if label in self.cell_rects:
                if self._is_inside_bet(label):
                    cx, cy = self.hit_areas[label]
                    r = 10
                    self.canvas.create_oval(
                        cx - r, cy - r, cx + r, cy + r,
                        outline=_SELECTED_OUTLINE, width=2, fill="",
                        tags="selection_highlight"
                    )
                else:
                    x1, y1, x2, y2 = self.cell_rects[label]
                    self.canvas.create_rectangle(
                        x1, y1, x2, y2,
                        outline=_SELECTED_OUTLINE, width=3, fill="",
                        tags="selection_highlight"
                    )
        # Show/hide multiplier buttons based on selection state
        self._update_multiplier_buttons()

    def set_selected_labels(self, labels):
        """Programmatically set the selected labels (e.g., from listbox sync)"""
        self.selected_labels = set(l.lower() for l in labels)
        self._redraw_selections()

    # ── Chip display (unchanged) ─────────────────────────────────────

    def clear_chips(self):
        """Remove all chips"""
        for chip in self.active_chips:
            self.canvas.delete(chip)
        self.active_chips = []

    def get_bet_coordinates(self, label):
        """Calculate (x, y) for a given bet label including Splits/Corners"""
        label = str(label).lower().strip()

        if label in self.hit_areas:
            return self.hit_areas[label]

        if label.isdigit() and int(label) in self.number_grid:
            x, y, w, h = self.number_grid[int(label)]
            return x + w / 2, y + h / 2

        # Splits
        if "split" in label:
            parts = label.replace("split", "").split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                n1, n2 = int(parts[0]), int(parts[1])
                if n1 in self.number_grid and n2 in self.number_grid:
                    x1, y1, w1, h1 = self.number_grid[n1]
                    x2, y2, w2, h2 = self.number_grid[n2]
                    return (x1 + w1 / 2 + x2 + w2 / 2) / 2, (y1 + h1 / 2 + y2 + h2 / 2) / 2

        # Corners
        if "corner" in label:
            parts = label.replace("corner", "").split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                n1, n2 = int(parts[0]), int(parts[1])
                if n1 in self.number_grid and n2 in self.number_grid:
                    x1, y1, w1, h1 = self.number_grid[n1]
                    x2, y2, w2, h2 = self.number_grid[n2]
                    return (x1 + w1 / 2 + x2 + w2 / 2) / 2, (y1 + h1 / 2 + y2 + h2 / 2) / 2

        # Streets
        if "strt" in label and "dbl" not in label:
            parts = label.replace("strt", "").split("-")
            if len(parts) == 2 and parts[0].isdigit():
                n1 = int(parts[0])
                if n1 in self.number_grid:
                    x, y, w, h = self.number_grid[n1]
                    return x + w / 2, y + h

        # Six Line / Double Street
        if "dblstrt" in label:
            parts = label.replace("dblstrt", "").split("-")
            try:
                n1 = int(parts[0])
                if n1 in self.number_grid:
                    x, y, w, h = self.number_grid[n1]
                    return x + w, y + h
            except Exception:
                pass

        return None

    def highlight_bets(self, bets_data):
        """
        Draw chips.
        bets_data: can be list of strings OR dict {label: value}
        """
        self.clear_chips()

        if not bets_data:
            return

        if isinstance(bets_data, list):
            bets = {label: "" for label in bets_data}
        else:
            bets = bets_data

        for label, value in bets.items():
            coords = self.get_bet_coordinates(label)
            if coords:
                small = self._is_inside_bet(label.lower())
                self.draw_chip(coords[0], coords[1], str(value), small=small)

    def draw_chip(self, x, y, value="", small=False):
        """Draw a chip at x,y with optional text. small=True for inside bets."""
        r = 9 if small else 13
        # Outer ring
        self.active_chips.append(
            self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                     fill=_CHIP_FILL, outline=_CHIP_OUTLINE, width=2))
        # Inner ring
        self.active_chips.append(
            self.canvas.create_oval(x - r + 3, y - r + 3, x + r - 3, y + r - 3,
                                     outline=_CHIP_INNER, width=1))
        if value:
            self.active_chips.append(
                self.canvas.create_text(x, y, text=str(value), fill="#1a1a1a",
                                         font=FONT_CAPTION))
