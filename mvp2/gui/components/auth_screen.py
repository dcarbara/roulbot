import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import threading
from core.security.license_manager import LicenseManager
from gui.theme import (
    FONT_DISPLAY, FONT_SMALL, FONT_CAPTION,
    GOLD, TEXT_SECONDARY, TEXT_MUTED,
    BG_DARK, BG_CARD,
    SUCCESS, DANGER,
    INPUT_STYLE, BUTTON_GOLD, fade_in,
)


class AuthScreen(ctk.CTkToplevel):
    def __init__(self, parent, on_success_callback, license_manager: LicenseManager):
        super().__init__(parent)
        self.parent = parent
        self.on_success_callback = on_success_callback
        self.license_manager = license_manager

        self.title("SpinEdge - Authentication")
        self.geometry("440x520")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(fg_color=BG_DARK)

        # Make modal
        self.transient(parent)
        self.grab_set()

        # Center on screen
        self.update_idletasks()
        x = (self.winfo_screenwidth() - 440) // 2
        y = (self.winfo_screenheight() - 520) // 2
        self.geometry(f"+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.setup_ui()
        fade_in(self, target_alpha=1.0, duration_ms=250)

        # Start auto-login check
        self.after(200, self.attempt_auto_login)

    def setup_ui(self):
        # Outer wrapper
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=44, pady=44)

        # Brand mark
        self.logo_label = ctk.CTkLabel(
            self.main_frame, text="SpinEdge",
            font=FONT_DISPLAY, text_color=GOLD,
        )
        self.logo_label.pack(pady=(0, 4))

        self.subtitle = ctk.CTkLabel(
            self.main_frame, text="Pro Roulette Automation",
            font=FONT_SMALL, text_color=TEXT_MUTED,
        )
        self.subtitle.pack(pady=(0, 36))

        # Divider accent
        ctk.CTkFrame(
            self.main_frame, fg_color=GOLD, height=2, corner_radius=1
        ).pack(fill="x", padx=60, pady=(0, 32))

        # Email field
        ctk.CTkLabel(
            self.main_frame, text="Email",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self.email_entry = ctk.CTkEntry(
            self.main_frame, placeholder_text="you@example.com",
            width=320, **INPUT_STYLE,
        )
        self.email_entry.pack(fill="x", pady=(0, 16))

        # Password field
        ctk.CTkLabel(
            self.main_frame, text="Password",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", pady=(0, 4))

        self.password_entry = ctk.CTkEntry(
            self.main_frame, placeholder_text="Enter your password",
            show="\u2022", width=320, **INPUT_STYLE,
        )
        self.password_entry.pack(fill="x", pady=(0, 28))
        self.password_entry.bind("<Return>", lambda e: self.do_login())

        # Login Button
        self.login_btn = ctk.CTkButton(
            self.main_frame, text="Sign In",
            width=320, command=self.do_login, **BUTTON_GOLD,
        )
        self.login_btn.pack(fill="x", pady=(0, 20))

        # Status Label
        self.status_label = ctk.CTkLabel(
            self.main_frame, text="",
            font=FONT_SMALL, text_color=TEXT_MUTED,
        )
        self.status_label.pack()

        # Loading indicator (hidden by default)
        self.loading_bar = ctk.CTkProgressBar(
            self.main_frame, mode="indeterminate",
            progress_color=GOLD, fg_color=BG_CARD,
            height=3, width=200, corner_radius=2,
        )

        # Footer
        ctk.CTkLabel(
            self.main_frame, text="spinedge.pro",
            font=FONT_CAPTION, text_color=TEXT_MUTED,
        ).pack(side="bottom", pady=(16, 0))

    def attempt_auto_login(self):
        self._set_loading(True, "Checking saved session...")

        def run_check():
            success = self.license_manager.try_auto_login()
            self.after(0, self._handle_auto_login_result, success)

        threading.Thread(target=run_check, daemon=True).start()

    def _handle_auto_login_result(self, success):
        if success:
            self._set_loading(False)
            self._success_close()
        else:
            self._set_loading(False)

    def do_login(self):
        email = self.email_entry.get().strip()
        password = self.password_entry.get()

        if not email or not password:
            self.update_status("Please enter email and password.", "error")
            return

        self._set_loading(True, "Authenticating...")

        def run_login():
            success, msg = self.license_manager.login(email, password)
            self.after(0, self._handle_login_result, success, msg)

        threading.Thread(target=run_login, daemon=True).start()

    def _handle_login_result(self, success, msg):
        self._set_loading(False)
        if success:
            self.update_status(msg, "success")
            self.after(500, self._success_close)
        else:
            self.update_status(msg, "error")

    def _set_loading(self, is_loading, text=""):
        if is_loading:
            self.login_btn.configure(state="disabled", text="Please Wait...")
            self.email_entry.configure(state="disabled")
            self.password_entry.configure(state="disabled")
            self.loading_bar.pack(pady=(8, 0))
            self.loading_bar.start()
            self.update_status(text, "info")
        else:
            self.login_btn.configure(state="normal", text="Sign In")
            self.email_entry.configure(state="normal")
            self.password_entry.configure(state="normal")
            self.loading_bar.stop()
            self.loading_bar.pack_forget()
            self.update_status("", "info")

    def update_status(self, text, type="error"):
        colors = {"error": DANGER, "success": SUCCESS, "info": TEXT_MUTED}
        self.status_label.configure(text=text, text_color=colors.get(type, TEXT_MUTED))

    def _success_close(self):
        self.grab_release()
        self.destroy()
        if self.on_success_callback:
            self.on_success_callback()

    def _on_close(self):
        """If user closes auth window, the app should likely exit."""
        if messagebox.askokcancel("Quit", "Authentication is required. Do you want to exit SpinEdge?"):
            self.grab_release()
            self.parent.destroy()
