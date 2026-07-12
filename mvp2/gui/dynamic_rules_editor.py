
import customtkinter as ctk
from tkinter import messagebox
from gui.theme import (
    FONT_HEADING, FONT_SMALL, FONT_CAPTION,
    GOLD, TEXT_MUTED,
    BG_DARK, BG_CARD, BG_ELEVATED, BG_INPUT,
    BORDER_SUBTLE, BORDER_DEFAULT,
    DANGER_HOVER, CORNER_SMALL,
    PAD_SECTION, PAD_GROUP,
    BUTTON_SUCCESS, BUTTON_PRIMARY, BUTTON_GHOST,
)


class DynamicRulesEditor(ctk.CTkToplevel):
    def __init__(self, parent, current_rules, on_save_callback):
        super().__init__(parent)
        self.on_save = on_save_callback
        self.rows = []

        self.title("Dynamic Progression Rules Editor")
        self.geometry("940x540")
        self.minsize(900, 400)
        self.configure(fg_color=BG_DARK)

        self.lift()
        self.focus_force()

        # Layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header
        header_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)

        header_inner = ctk.CTkFrame(header_frame, fg_color="transparent")
        header_inner.pack(fill="x", padx=PAD_SECTION, pady=PAD_GROUP)

        ctk.CTkLabel(header_inner, text="Rules Editor",
                     font=FONT_HEADING, text_color=GOLD).pack(side="left")

        # Column labels
        col_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        col_frame.pack(fill="x", padx=PAD_SECTION, pady=(0, 8))

        col_widths = [("Event", 120), ("Condition", 150), ("Action", 150), ("Value / Params", 180)]
        for label, width in col_widths:
            ctk.CTkLabel(col_frame, text=label, width=width, anchor="w",
                         font=FONT_CAPTION, text_color=TEXT_MUTED).pack(side="left", padx=5)

        # Scrollable Rule List
        self.scroll_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=BG_ELEVATED,
        )
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", padx=PAD_SECTION, pady=PAD_GROUP)

        # Footer
        footer_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0)
        footer_frame.grid(row=2, column=0, sticky="ew")

        footer_inner = ctk.CTkFrame(footer_frame, fg_color="transparent")
        footer_inner.pack(fill="x", padx=PAD_SECTION, pady=PAD_GROUP)

        ctk.CTkButton(footer_inner, text="+ Add Rule", command=self.add_rule_row,
                       width=110, **BUTTON_SUCCESS).pack(side="left", padx=(0, 10))

        ctk.CTkButton(footer_inner, text="Save & Close", command=self.save_rules,
                       width=130, **BUTTON_PRIMARY).pack(side="right", padx=(10, 0))

        ctk.CTkButton(footer_inner, text="Cancel", command=self.destroy,
                       width=100, **BUTTON_GHOST).pack(side="right")

        # Pre-populate
        if current_rules:
            for rule in current_rules:
                self.add_rule_row(rule)
        else:
            self.add_rule_row()

    def add_rule_row(self, rule_data=None):
        if rule_data is None:
            rule_data = {}

        row_frame = ctk.CTkFrame(
            self.scroll_frame, fg_color=BG_CARD,
            corner_radius=CORNER_SMALL, border_width=1, border_color=BORDER_SUBTLE,
        )
        row_frame.pack(fill="x", padx=2, pady=3)

        combo_kwargs = dict(
            font=FONT_SMALL, dropdown_font=FONT_SMALL,
            fg_color=BG_ELEVATED, border_color=BORDER_DEFAULT,
            button_color=BG_ELEVATED, corner_radius=CORNER_SMALL,
        )

        # Event
        event_var = ctk.StringVar(value=rule_data.get('on', 'win'))
        event_cb = ctk.CTkComboBox(row_frame, variable=event_var,
                                    values=["win", "loss", "session_high"], width=120,
                                    **combo_kwargs)
        event_cb.pack(side="left", padx=4, pady=6)

        # Condition
        cond_var = ctk.StringVar(value=rule_data.get('condition', 'None'))
        cond_cb = ctk.CTkComboBox(row_frame, variable=cond_var,
                                   values=["None", "profit_below_session_high", "profit_at_or_above_session_high"],
                                   width=150, **combo_kwargs)
        cond_cb.pack(side="left", padx=4, pady=6)

        # Action
        action_var = ctk.StringVar(value=rule_data.get('action', 'reset_to_base'))
        action_cb = ctk.CTkComboBox(row_frame, variable=action_var,
                                     values=["reset_to_base", "martingale", "dalembert", "step_up",
                                             "step_down", "flat", "custom_sequence", "keep"],
                                     width=150, **combo_kwargs)
        action_cb.pack(side="left", padx=4, pady=6)

        # Param Area
        param_area = ctk.CTkFrame(row_frame, fg_color="transparent")
        param_area.pack(side="left", fill="x", expand=True, padx=4)

        # Step Type Combobox (for dalembert)
        step_type_var = ctk.StringVar(value="Base Bet Multiplier")
        step_type_cb = ctk.CTkComboBox(param_area, variable=step_type_var,
                                        values=["Base Bet Multiplier", "Custom Unit"],
                                        width=150, **combo_kwargs)

        # Dynamic Param Entry
        init_val = ""
        if 'sequence' in rule_data:
            init_val = ",".join(map(str, rule_data['sequence']))
        elif 'step' in rule_data:
            if str(rule_data['step']).startswith("base_bet"):
                step_type_var.set("Base Bet Multiplier")
                if rule_data['step'] == "base_bet":
                    init_val = "1.0"
                else:
                    try:
                        mult = str(rule_data['step']).split("_")[2][:-1]
                        init_val = str(mult)
                    except (ValueError, IndexError):
                        init_val = "1.0"
            else:
                step_type_var.set("Custom Unit")
                init_val = str(rule_data['step'])

        param_var = ctk.StringVar(value=init_val)
        param_entry = ctk.CTkEntry(param_area, textvariable=param_var, width=150,
                                    placeholder_text="Seq: 1,2,3 or Step: 1",
                                    fg_color=BG_INPUT, border_color=BORDER_DEFAULT,
                                    corner_radius=CORNER_SMALL, font=FONT_SMALL)
        param_entry.pack(side="left", padx=2)

        def update_param_visibility(*args):
            act = action_var.get()
            if act == "custom_sequence":
                step_type_cb.pack_forget()
                param_entry.pack(side="left", padx=2)
                param_entry.configure(state="normal")
            elif act in ["dalembert", "step_up", "step_down"]:
                step_type_cb.pack(side="left", padx=2)
                param_entry.pack(side="left", padx=2)
                param_entry.configure(state="normal")
            else:
                step_type_cb.pack_forget()
                param_entry.pack(side="left", padx=2)
                param_entry.configure(state="disabled")

        action_cb.configure(command=update_param_visibility)
        step_type_cb.configure(command=update_param_visibility)
        update_param_visibility()

        # Delete Button
        def delete_row():
            row_frame.destroy()
            self.rows.remove(row_wrapper)

        del_btn = ctk.CTkButton(
            row_frame, text="\u2715", width=32, height=32,
            fg_color="transparent", hover_color=DANGER_HOVER,
            text_color=TEXT_MUTED, font=FONT_SMALL,
            corner_radius=CORNER_SMALL, command=delete_row,
        )
        del_btn.pack(side="right", padx=6)

        # Store refs
        row_wrapper = {
            "frame": row_frame,
            "event": event_var,
            "condition": cond_var,
            "action": action_var,
            "param": param_var,
            "step_type": step_type_var
        }
        self.rows.append(row_wrapper)

    def save_rules(self):
        rules = []
        for row in self.rows:
            r = {}
            r['on'] = row['event'].get()

            cond = row['condition'].get()
            if cond != "None":
                r['condition'] = cond

            act = row['action'].get()
            r['action'] = act

            val_str = row['param'].get().strip()
            if act == 'custom_sequence':
                try:
                    seq = [float(x.strip()) for x in val_str.split(',') if x.strip()]
                    r['sequence'] = seq
                except ValueError:
                    messagebox.showerror("Invalid Input",
                                         "Custom sequence must be comma-separated numbers (e.g., 1, 2, 3).")
                    return
            elif act in ['dalembert', 'step_up', 'step_down']:
                step_type = row.get('step_type')
                if step_type and step_type.get() == "Base Bet Multiplier":
                    try:
                        mult = float(val_str)
                        if mult == 1.0:
                            r['step'] = "base_bet"
                        else:
                            r['step'] = f"base_bet_{mult}x"
                    except ValueError:
                        r['step'] = "base_bet"
                else:
                    try:
                        step = float(val_str)
                        r['step'] = step
                    except ValueError:
                        r['step'] = 1.0

            rules.append(r)

        if self.on_save:
            self.on_save(rules)

        self.destroy()
