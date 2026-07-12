import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import os
import json
from gui.theme import (
    FONT_DISPLAY, FONT_HEADING, FONT_BODY, FONT_SMALL, FONT_CAPTION,
    GOLD, GOLD_DIM, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    BG_DARK, BG_CARD, BG_ELEVATED,
    BORDER_SUBTLE, BORDER_DEFAULT,
    DANGER, CORNER_RADIUS, CORNER_LARGE,
    PAD_SECTION, BUTTON_PRIMARY, BUTTON_SUCCESS, BUTTON_GHOST, fade_in,
)


class SetupWizard(ctk.CTkToplevel):
    def __init__(self, parent, on_complete_callback):
        super().__init__(parent)
        self.parent = parent
        self.on_complete_callback = on_complete_callback

        self.title("SpinEdge - First Time Setup")
        self.geometry("640x520")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_DARK)
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Center on screen
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 640) // 2
        y = (self.winfo_screenheight() - 520) // 2
        self.geometry(f"640x520+{x}+{y}")

        self.current_step = 0
        self.presets = self.load_presets()

        self.create_widgets()
        self.show_step(0)
        fade_in(self, target_alpha=1.0, duration_ms=250)

    def load_presets(self):
        """Load available JSON presets from the config directory."""
        presets = {}
        presets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "custom_presets")

        if os.path.exists(presets_dir):
            for filename in os.listdir(presets_dir):
                if filename.endswith(".json"):
                    filepath = os.path.join(presets_dir, filename)
                    try:
                        with open(filepath, "r") as f:
                            data = json.load(f)
                            display_name = data.get("name", filename.replace('.json', ''))
                            presets[display_name] = filepath
                    except Exception as e:
                        print(f"Failed to load preset {filename}: {e}")
        return presets

    def create_widgets(self):
        # Step indicator at top
        self.step_indicator = ctk.CTkFrame(self, fg_color="transparent", height=40)
        self.step_indicator.pack(fill="x", padx=40, pady=(24, 0))

        self.step_dots = []
        dot_frame = ctk.CTkFrame(self.step_indicator, fg_color="transparent")
        dot_frame.pack(anchor="center")

        step_labels = ["Display Setup", "Layout Config"]
        for i, label in enumerate(step_labels):
            dot_container = ctk.CTkFrame(dot_frame, fg_color="transparent")
            dot_container.pack(side="left", padx=16)

            dot = ctk.CTkFrame(dot_container, width=10, height=10,
                               corner_radius=5, fg_color=GOLD if i == 0 else BORDER_DEFAULT)
            dot.pack()
            step_lbl = ctk.CTkLabel(dot_container, text=label,
                                    font=FONT_CAPTION,
                                    text_color=GOLD if i == 0 else TEXT_MUTED)
            step_lbl.pack(pady=(4, 0))
            self.step_dots.append((dot, step_lbl))

            if i < len(step_labels) - 1:
                line = ctk.CTkFrame(dot_frame, width=60, height=2,
                                    fg_color=BORDER_DEFAULT, corner_radius=1)
                line.pack(side="left", pady=(0, 16))

        # Main content area
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=40, pady=(20, 30))

        # --- Step 0: Welcome & Warning ---
        self.frame_step_0 = ctk.CTkFrame(self.main_frame, fg_color="transparent")

        title_0 = ctk.CTkLabel(self.frame_step_0, text="Welcome to SpinEdge",
                                font=FONT_DISPLAY, text_color=TEXT_PRIMARY)
        title_0.pack(pady=(10, 16))

        desc_0 = ctk.CTkLabel(self.frame_step_0, text=(
            "To ensure SpinEdge's optical character recognition (OCR)\n"
            "works perfectly, your display settings must be configured."
        ), justify="center", wraplength=500, font=FONT_BODY, text_color=TEXT_SECONDARY)
        desc_0.pack(pady=(0, 20))

        # Warning card
        warning_card = ctk.CTkFrame(self.frame_step_0, fg_color=BG_CARD,
                                     corner_radius=CORNER_LARGE, border_width=1,
                                     border_color="#5B2020")
        warning_card.pack(fill="x", padx=10, pady=(0, 24))

        warning_header = ctk.CTkFrame(warning_card, fg_color="transparent")
        warning_header.pack(fill="x", padx=PAD_SECTION, pady=(PAD_SECTION, 8))

        ctk.CTkLabel(warning_header, text="Critical Requirement",
                     font=FONT_HEADING, text_color=DANGER).pack(side="left")

        ctk.CTkLabel(warning_card, text=(
            "Your Windows 'Scale and layout' setting must be set to\n"
            "exactly 100%. If scaling is >100%, the bot will misclick\n"
            "and fail to read numbers correctly."
        ), font=FONT_SMALL, text_color="#F0A0A0", wraplength=480, justify="left"
        ).pack(padx=PAD_SECTION, pady=(0, PAD_SECTION))

        ctk.CTkButton(self.frame_step_0, text="I have set scaling to 100%  \u2192",
                       command=self.next_step, **BUTTON_PRIMARY).pack(pady=(0, 10))

        # --- Step 1: Preset Selection ---
        self.frame_step_1 = ctk.CTkFrame(self.main_frame, fg_color="transparent")

        title_1 = ctk.CTkLabel(self.frame_step_1, text="Quick Start",
                                font=FONT_DISPLAY, text_color=TEXT_PRIMARY)
        title_1.pack(pady=(10, 6))

        ctk.CTkLabel(self.frame_step_1, text="Recommended",
                     font=FONT_CAPTION, text_color=GOLD).pack(pady=(0, 16))

        desc_1 = ctk.CTkLabel(self.frame_step_1, text=(
            "Select your Casino/Table provider to instantly load a\n"
            "pre-configured layout, bypassing manual setup."
        ), justify="center", wraplength=500, font=FONT_BODY, text_color=TEXT_SECONDARY)
        desc_1.pack(pady=(0, 20))

        preset_names = list(self.presets.keys())
        self.preset_var = tk.StringVar(value="Select a Layout...")

        if preset_names:
            combo = ctk.CTkComboBox(self.frame_step_1, values=preset_names,
                                     variable=self.preset_var, width=340,
                                     font=FONT_BODY, dropdown_font=FONT_BODY,
                                     fg_color=BG_ELEVATED, border_color=BORDER_DEFAULT,
                                     button_color=GOLD_DIM, button_hover_color=GOLD,
                                     corner_radius=CORNER_RADIUS)
            combo.pack(pady=(0, 16))

            ctk.CTkButton(self.frame_step_1, text="Load Selected Layout & Finish",
                           command=self.load_preset_and_finish, **BUTTON_SUCCESS
                           ).pack(fill="x", padx=40, pady=(0, 16))
        else:
            ctk.CTkLabel(self.frame_step_1, text="No presets found. Proceed to manual setup.",
                         text_color=TEXT_MUTED, font=FONT_SMALL).pack(pady=20)

        # Divider
        div_frame = ctk.CTkFrame(self.frame_step_1, fg_color="transparent")
        div_frame.pack(fill="x", padx=40, pady=8)
        ctk.CTkFrame(div_frame, height=1, fg_color=BORDER_SUBTLE).pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(div_frame, text="  or  ", font=FONT_CAPTION, text_color=TEXT_MUTED).pack(side="left")
        ctk.CTkFrame(div_frame, height=1, fg_color=BORDER_SUBTLE).pack(side="left", fill="x", expand=True)

        ctk.CTkButton(self.frame_step_1, text="Auto-Detect from Screenshot",
                       command=self.auto_detect_from_screenshot,
                       fg_color="#27ae60", hover_color="#2ecc71",
                       ).pack(fill="x", padx=40, pady=(8, 4))

        ctk.CTkButton(self.frame_step_1, text="Run Manual Setup",
                       command=self.finish_manual, **BUTTON_GHOST
                       ).pack(fill="x", padx=40, pady=(4, 0))

        self.frames = [self.frame_step_0, self.frame_step_1]

    def show_step(self, step_idx):
        for frame in self.frames:
            if frame.winfo_ismapped():
                frame.pack_forget()

        self.frames[step_idx].pack(fill="both", expand=True)

        # Update step indicator
        for i, (dot, lbl) in enumerate(self.step_dots):
            if i <= step_idx:
                dot.configure(fg_color=GOLD)
                lbl.configure(text_color=GOLD)
            else:
                dot.configure(fg_color=BORDER_DEFAULT)
                lbl.configure(text_color=TEXT_MUTED)

    def next_step(self):
        if self.current_step < len(self.frames) - 1:
            self.current_step += 1
            self.show_step(self.current_step)

    def load_preset_and_finish(self):
        selection = self.preset_var.get()
        if selection == "Select a Layout..." or selection not in self.presets:
            messagebox.showwarning("Selection Required", "Please select a layout.", parent=self)
            return

        filepath = self.presets[selection]
        try:
            with open(filepath, "r") as f:
                preset_data = json.load(f)

            self.on_complete_callback(preset_data)
            self.destroy()

        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load preset: {e}", parent=self)

    def auto_detect_from_screenshot(self):
        """Auto-detect table layout from a screenshot file."""
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Select a screenshot of the roulette table",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )
        if not filepath:
            return

        try:
            from core.auto_calibrator import AutoCalibrator
            calibrator = AutoCalibrator()
            screenshot = calibrator.capture_from_image(filepath)
            if screenshot is None:
                messagebox.showerror("Error", "Failed to load image.", parent=self)
                return

            result = calibrator.detect_table(screenshot)
            if result is None:
                messagebox.showerror("Detection Failed",
                                     "Could not detect the roulette table in this image.\n\n"
                                     "Make sure the betting grid is clearly visible.",
                                     parent=self)
                return

            result = calibrator.refine_with_ocr(screenshot, result)
            preset = calibrator.generate_preset(result, name="Auto-Detected Layout")
            validation = calibrator.validate_preset(preset)

            msg = (f"Detected {validation['total_coordinates']} coordinates "
                   f"(confidence: {result.confidence:.0%})")
            if validation["issues"]:
                msg += f"\n\nWarnings:\n" + "\n".join(f"  - {i}" for i in validation["issues"][:3])
            msg += "\n\nApply this layout?"

            if messagebox.askyesno("Auto-Detection Complete", msg, parent=self):
                self.on_complete_callback(preset)
                self.destroy()

        except Exception as e:
            messagebox.showerror("Error", f"Auto-detection failed:\n{e}", parent=self)

    def finish_manual(self):
        """User chose to do manual setup. Return empty dict."""
        self.on_complete_callback({})
        self.destroy()

    def on_closing(self):
        """If user closes window without completing, treat as manual setup."""
        self.on_complete_callback({})
        self.destroy()
