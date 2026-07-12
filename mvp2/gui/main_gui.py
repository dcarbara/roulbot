import customtkinter as ctk
import asyncio
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import ttk, messagebox, filedialog
from gui.backtesting_gui import BacktestingGUI
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib
matplotlib.use("TkAgg") # Ensure TkAgg backend is used
from core.coordinate_recorder import CoordinateRecorder
from config.schema import load_config, save_config, default_config, CONFIG_PATH
from core.session_manager import SessionManager, StopReason
import json
import threading
import time
from gui.components.roulette_board import RouletteBoard
from gui.components.collapsible_frame import CollapsibleFrame
from gui.components.pattern_follower_editor import PatternFollowerEditor
from gui.components.composite_editor import CompositeEditor
from gui.theme import (
    GOLD, GOLD_HOVER, SUCCESS, SUCCESS_HOVER, WARNING, WARNING_HOVER,
    DANGER, DANGER_HOVER, INFO, INFO_HOVER, PRIMARY_BTN, PRIMARY_BTN_HOVER,
    PURPLE, PURPLE_HOVER, BG_DARK, BG_CARD, BG_CARD_HOVER, BG_ELEVATED, BG_INPUT, BG_TRANSPARENT,
    BORDER_SUBTLE, BORDER_ACTIVE, BORDER_GOLD, TEXT_PRIMARY, TEXT_SECONDARY,
    TEXT_MUTED, TEXT_LIGHT, STATUS_IDLE, STATUS_RUNNING, STATUS_PAUSED, STATUS_ERROR,
    TIER_COLORS, FONT_FAMILY, FONT_MONO, FONT_HERO, FONT_TITLE, FONT_HEADING,
    FONT_SUBHEADING, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL, FONT_CAPTION,
    FONT_TINY, FONT_MONO_BODY, FONT_MONO_SMALL, PAD_SECTION, PAD_GROUP,
    PAD_ITEM, PAD_INNER, PAD_CARD_X, PAD_CARD_Y, CORNER_RADIUS, CORNER_SMALL,
    CORNER_LARGE, CARD_STYLE, BUTTON_PRIMARY, BUTTON_SUCCESS, BUTTON_DANGER,
    BUTTON_WARNING, BUTTON_NEUTRAL, BUTTON_SMALL, SECTION_HEADER_STYLE,
    KPI_VALUE_STYLE, KPI_LABEL_STYLE, validate_entry, validate_numeric,
)
import random
from core.strategy_engine import StrategyEngine, ROULETTE_NUMBER_MAPPINGS, CHIP_DENOMINATIONS
from automation.roulette_browser import RouletteBrowserAutomation
from core.ocr_utils import extract_recent_numbers, extract_table_state, extract_balance, extract_winning_number_from_table_state, extract_number_and_color, clean_ocr_text
from core.utils.db_utils import save_winning_number, get_recent_winning_numbers, save_session_stats, get_aggregate_stats
from core.utils.telemetry import track
from datetime import datetime
import difflib
import logging

# NEW: Import the Setup Wizard
from gui.components.setup_wizard import SetupWizard
from core.ocr_utils import initialize_ocr
from core.encryption import decrypt_strategy_data
try:
    from core.telegram_bot import RouletteTelegramBot
except ImportError:
    RouletteTelegramBot = None
import glob
import os
from config.presets import get_preset_names, get_preset
from core.auto_calibrator import AutoCalibrator
from core.advanced_strategy_engine import AdvancedStrategyEngine
from core.ranking_engine import RankingEngine
from core.virtual_strategy_manager import VirtualStrategyManager


# Setup logging (already configured in main.py, but getting logger here)
logger = logging.getLogger(__name__)


CHIP_MAP = {
    100: "chip_100",
    25: "chip_25",
    5: "chip_5",
    1: "chip_1",
    0.5: "chip_.5",
    0.1: "chip_.1",
}
CHIP_VALUES = sorted(CHIP_MAP.keys(), reverse=True)

VALID_BET_TYPES = sorted(ROULETTE_NUMBER_MAPPINGS.keys())

def get_chip_breakdown(amount):
    breakdown = []
    epsilon = 1e-6  # Small tolerance for floating point errors
    for value in CHIP_VALUES:
        count = int((amount + epsilon) // value)
        if count > 0:
            breakdown.append((CHIP_MAP[value], count))
            amount = round(amount - count * value, 6)
    return breakdown

def group_bet_types(bet_types):
    groups = {
        "Splits": [],
        "Double Streets": [],
        "Streets": [],
        "Corners": [],
        "Dozens": [],
        "Columns": [],
        "Colors": [],
        "Even/Odd": [],
        "High/Low": [],
        "Straight Numbers": [],
        "Other": [],
    }
    for bt in bet_types:
        if "split" in bt:
            groups["Splits"].append(bt)
        elif "dblstrt" in bt:
            groups["Double Streets"].append(bt)
        elif "strt" in bt:
            groups["Streets"].append(bt)
        elif "corner" in bt:
            groups["Corners"].append(bt)
        elif "12" in bt:
            groups["Dozens"].append(bt)
        elif "col" in bt:
            groups["Columns"].append(bt)
        elif bt in ("red", "black"):
            groups["Colors"].append(bt)
        elif bt in ("even", "odd"):
            groups["Even/Odd"].append(bt)
        elif "to" in bt:
            groups["High/Low"].append(bt)
        elif bt.isdigit() or bt in ("0", "00"):
            groups["Straight Numbers"].append(bt)
        else:
            groups["Other"].append(bt)
    return groups

class RouletteBotGUI:

    def parse_hybrid_value(self, value_str, base_amount=0.0):
        """
        Parses a string that can be an absolute number (e.g. "100") or a percentage (e.g. "10%").
        Returns the absolute float value.
        """
        try:
            if not value_str:
                return 0.0
            
            s = str(value_str).strip()
            if s.endswith("%"):
                pct = float(s.rstrip("%"))
                return base_amount * (pct / 100.0)
            else:
                return float(s)
        except Exception as e:
            print(f"Error parsing hybrid value '{value_str}': {e}")
            return 0.0

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(f"SpinEdge v10.5 - Roulette Automation Engine")
        print(f"DEBUG: Active Configuration Path: {CONFIG_PATH}")
        screen_width  = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        target_width  = int(screen_width  / 3)
        target_height = int(screen_height / 2)
        self.root.geometry(f"{target_width}x{target_height}+0+0")
        # Minimum size — below this the scrollable tabs keep content accessible
        self.root.minsize(960, 620)

        self.config = load_config()
        self.coordinates = {}
        self.custom_strategies = {}
        self.bot_running = False

        # Strategy-source selection (strict XOR: a bundle OR a single manual
        # strategy drives a run, never both). Set by _select_strategy_source().
        # pending_engine_rearm is consumed at the run loop's round boundary to
        # apply a mid-session bundle/strategy switch ("apply immediately").
        self.active_strategy_source = None   # 'bundle' | 'manual' | None
        self.pending_engine_rearm = False
        # Bot Control betting/session config captured the moment a bundle is
        # first loaded (i.e. when leaving manual mode), so it can be restored
        # when the user switches back to a single strategy — the bundle's values
        # shouldn't linger in manual mode. See _snapshot/_restore_manual_config.
        self._manual_config_snapshot = None

        # Thread synchronization locks
        self._state_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self.dynamic_rules = []
        self.dynamic_rules_frame = None
        self.selected_window_title = None
        self._window_watermark = None
        
        self.init_variables() # Initialize all tracking vars BEFORE widget creation
        
        # License tier is determined after Supabase auth completes (_on_auth_complete).
        # Default to FREE until then so any tier-gated code is safely locked.
        self.license_tier = "FREE"

        # Initialize Regime Detector
        from core.analysis.regime_detector import RegimeDetector
        self.regime_detector = RegimeDetector()
        
        # Load Assets
        self.assets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
        self.logo_image = None
        try:
            # Load for UI (PNG)
            logo_path = os.path.join(self.assets_dir, "logo_new.png")
            if os.path.exists(logo_path):
                pil_image = Image.open(logo_path)
                self.logo_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(40, 40))

            # Set Window Icon (ICO for Windows Taskbar)
            ico_path = os.path.join(self.assets_dir, "logo_new.ico")
            if os.path.exists(ico_path):
                self.root.iconbitmap(default=ico_path)
            elif os.path.exists(logo_path):
                 # Fallback to PNG if ICO fails (via iconphoto)
                pil_image = Image.open(logo_path)
                self.icon_photo = ImageTk.PhotoImage(pil_image)
                self.root.iconphoto(False, self.icon_photo)
                
        except Exception as e:
            logger.error(f"Failed to load assets: {e}")

        self.dynamic_rules_widgets = []
        self.add_rule_btn = None
        self.dynamic_rules_listbox = None
        self.remove_rule_btn = None
        self.dynamic_rules_widgets = []
        self.add_rule_btn = None
        self.dynamic_rules_listbox = None
        self.remove_rule_btn = None
        
        # --- LOAD CONFIG AND DYNAMIC RULES BEFORE WIDGETS ---
        # Note: self.config is ALREADY loaded by load_config() at class level or passed in?
        # Wait, main.py calls RouletteBotGUI(root). 
        # Inside __init__, we need to ensure self.config is populated correctly.
        # It seems self.config was initialized earlier in __init__? 
        # Let's check lines 100-150 where self.config should be.
        
        # Actually, looking at previous context, self.config = load_config() starts at line 125 (approx).
        # This block at 183 is REDUNDANT and DANGEROUS because it uses relative path "config/config.json".
        
        # Just ensure derived variables are set from self.config:
        self.coordinates = self.config.get("coordinates", {})
        self.dynamic_rules = self.config.get("dynamic_rules", [])
        self.custom_regions = self.config.get("custom_regions", [])
        
        # Initialize OCR with configured path
        # Initialize OCR (Prioritizes bundled > config > default)
        tesseract_path = self.config.get("tesseract_path", "")
        # Always attempt initialization so bundled Tesseract is picked up
        if not initialize_ocr(tesseract_path):
             # Only warn if a specific custom path was provided and it failed
             if tesseract_path:
                messagebox.showwarning("OCR Warning", f"Could not initialize Tesseract at: {tesseract_path}\nPlease configure the correct path in OCR Settings.")
             else:
                # If no path set and default/bundled also failed
                # We might not want to annoy user yet, or maybe we do?
                # For now, just log it.
                logger.warning("OCR initialization failed (no bundled or default Tesseract found).")

        # Initialize Telegram Bot
        self.telegram_bot = None
        self.start_telegram_bot()
        
        # We will prompt for current balance AFTER checking for wizard
        self.simulation_running = False
        
        self.session_end_time = None
        
        # Stats tracking variables
        self.session_start_time = None
        self.session_start_balance = None
        self.session_start_timestamp = None  # Timestamp for filtering historical data
        # Risk-profile override state (see _on_bundle_textbox_write / _runtime_switch_risk)
        self._active_risk_profile = "Bundle"
        self._user_override_base_bet = None
        self._user_override_max_loss = None
        self._suppress_override_capture = False

        # Escalation-on-loss state (see _apply_session_escalation).
        # _initials are snapshotted in start_bot so escalation always knows the
        # values to scale from / restore to. _step counts how many session
        # stop-loss hits we've absorbed since the last reset.
        # _peak_global_pnl tracks the all-time-high global PnL during the run
        # so we can also reset escalation when we recover back to that peak,
        # not just on the configured global profit target.
        self._escalation_step = 0
        self._escalation_initial_base_bet = None
        self._escalation_initial_max_loss = None
        self._peak_global_pnl = 0.0
        self.pnl_history = [0.0] # Track PnL history for graph (starts at 0)
        self.total_wins = 0
        self.total_losses = 0
        self.current_session_num = 0
        self.total_sessions = 0
        self.session_end_time = None
        
        # Track if we've placed the first bet (for correct win/loss logic)
        self.has_placed_first_bet = False
        
        # Winning number tracking for bot logic
        self.latest_winning_number = None
        self.latest_winning_color = None
        self.latest_winning_timestamp = None  # Add timestamp tracking
        self.last_processed_winning_number = None
        
        self.winning_number_watcher_running = True
        self.custom_strategies = self.config.get("custom_strategies", {})
        self.recorder = CoordinateRecorder(self.save_coordinate)
        
        self.winning_number_watcher_running = True
        if getattr(self, '_watcher_thread', None) and self._watcher_thread.is_alive():
            logger.warning("Winning number watcher thread already running")
        else:
            self._watcher_thread = threading.Thread(target=self.winning_number_watcher, daemon=True)
            self._watcher_thread.start()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.simulation_running = False
        self.auto_roulette_paused = False
        self.bot_paused = False
        self.pause_start_time = None
        self.total_paused_duration = 0

        # Initialize Licensing & Security.
        # IMPORTANT: use get_license_manager() (the module-level singleton) instead
        # of LicenseManager() directly. StrategyEngine.get_next_bet() also calls
        # get_license_manager() to gate execution; if the GUI created its own
        # separate instance, the GUI auto-logs in to LM #1 while the engine sees
        # a fresh, unauthenticated LM #2 -> "Unlicensed execution prevented" on
        # every bet despite a perfectly valid login. Sharing the singleton fixes
        # that.
        from core.security.license_manager import get_license_manager
        self.license_manager = get_license_manager()

        # Hide main window until authenticated — widgets built AFTER auth so tier is known
        self.root.withdraw()

        # Launch Auth Screen
        from gui.components.auth_screen import AuthScreen
        self.auth_screen = AuthScreen(self.root, self._on_auth_complete, self.license_manager)

    # ── Tier required to access each tab ──────────────────────────────────────
    _TAB_TIER_REQUIRED = {
        "Strategy Builder":           "BASIC",
        "Advanced Strategy Builder":  "PLUS",
        "Backtesting":                "PLUS",
        "Bot Control":                "PRO",
        "Auto Roulette":              "PRO",
    }
    _TIER_RANK = {"FREE": 0, "BASIC": 1, "PLUS": 2, "PRO": 3, "ADMIN": 99}

    def _get_allowed_tabs(self, tier: str) -> list:
        rank = self._TIER_RANK.get(tier, 0)
        all_tabs = [
            "Dashboard", "Statistics", "Region/Coordinate Setup", "Activity Log",
            "Winning Numbers", "OCR Settings", "Settings",
            "Strategy Builder", "Advanced Strategy Builder", "Backtesting",
            "Bot Control", "Auto Roulette",
        ]
        return [t for t in all_tabs
                if self._TIER_RANK.get(self._TAB_TIER_REQUIRED.get(t, "FREE"), 0) <= rank]

    def _apply_tab_lock_overlays(self, allowed: list):
        """Place a lock frame over every tab the user cannot access."""
        if not hasattr(self, "_tab_parents"):
            return
        for tab_name, required_tier in self._TAB_TIER_REQUIRED.items():
            if tab_name not in allowed and tab_name in self._tab_parents:
                tabview = self._tab_parents[tab_name]
                tab_frame = tabview.tab(tab_name)
                overlay = ctk.CTkFrame(tab_frame, fg_color="#0d1117", corner_radius=0)
                overlay.place(x=0, y=0, relwidth=1, relheight=1)
                # Content
                ctk.CTkLabel(overlay, text="🔒", font=ctk.CTkFont(size=40)).pack(pady=(60, 8))
                ctk.CTkLabel(overlay,
                             text=f"Requires {required_tier} or higher",
                             font=ctk.CTkFont(size=15, weight="bold"),
                             text_color="#f4f4f5").pack()
                ctk.CTkLabel(overlay,
                             text=f"Upgrade your plan to unlock {tab_name}.",
                             font=ctk.CTkFont(size=12), text_color="#71717a").pack(pady=(4, 20))
                ctk.CTkButton(
                    overlay, text="⬆  Upgrade at spinedge.pro",
                    fg_color="#10b981", hover_color="#059669",
                    font=ctk.CTkFont(size=13, weight="bold"),
                    height=38,
                    command=lambda: __import__("webbrowser").open("https://spinedge.pro/shop")
                ).pack()
                self._tab_lock_overlays[tab_name] = overlay

    def _unlock_tabs(self, new_allowed: list):
        """Remove lock overlays for tabs newly accessible in new_allowed."""
        for tab_name in list(self._tab_lock_overlays.keys()):
            if tab_name in new_allowed:
                self._tab_lock_overlays[tab_name].destroy()
                del self._tab_lock_overlays[tab_name]

    def _apply_tier_change(self, new_tier: str):
        """Called on GUI thread when tier changes (upgrade or downgrade)."""
        old_tier = self.license_tier
        self.license_tier = new_tier
        self._refresh_tier_badges()

        old_allowed = set(self._get_allowed_tabs(old_tier))
        new_allowed  = set(self._get_allowed_tabs(new_tier))

        # Unlock tabs that are now accessible
        self._unlock_tabs(list(new_allowed))

        # Lock tabs that are no longer accessible
        if old_allowed - new_allowed:
            self._apply_tab_lock_overlays(list(new_allowed))

        self.refresh_dashboard_bundles()
        self._update_free_guide()
        self._update_expiry_banner()

        newly_unlocked = new_allowed - old_allowed
        if newly_unlocked:
            import tkinter.messagebox as mb
            mb.showinfo("Plan Upgraded",
                        f"Your plan has been upgraded to {new_tier}!\n"
                        f"Newly unlocked: {', '.join(sorted(newly_unlocked))}")

    def _start_license_heartbeat(self):
        """Background thread that re-validates license.
        Fast mode: polls every 30s for first 10 min (catches post-payment unlock quickly).
        Slow mode: polls every 5 min after that.
        """
        def _loop():
            import time as _time
            poll_count = 0
            FAST_INTERVAL  = 30    # seconds — first 20 polls (10 min)
            SLOW_INTERVAL  = 300   # seconds — after that
            FAST_POLLS     = 20
            while True:
                interval = FAST_INTERVAL if poll_count < FAST_POLLS else SLOW_INTERVAL
                _time.sleep(interval)
                poll_count += 1
                try:
                    if not self.license_manager.is_authenticated:
                        continue
                    valid, msg = self.license_manager.validate_license(force_refresh=True)
                    if valid:
                        new_tier = self.license_manager.license_data.get("subscription_tier", "FREE")
                        if new_tier != self.license_tier:
                            self.root.after(0, lambda t=new_tier: self._apply_tier_change(t))
                        # Also refresh expiry banner on every check
                        self.root.after(0, self._update_expiry_banner)
                    else:
                        # Don't silently swallow validation failures — surface them so
                        # the user can spot license issues (session-token mismatch from
                        # logging in elsewhere, expired row, etc.). Was previously `_`.
                        logger.warning(f"[License heartbeat] Validation failed: {msg}")
                except Exception as e:
                    logger.debug(f"License heartbeat error: {e}")
        threading.Thread(target=_loop, daemon=True, name="license-heartbeat").start()

    def _update_free_guide(self):
        """Show the Get Started guide when tier is FREE, hide it otherwise."""
        if not hasattr(self, "_dash_free_guide"):
            return
        if self.license_tier == "FREE":
            if not self._dash_free_guide.winfo_ismapped():
                self._dash_free_guide.pack(fill="x", padx=20, pady=(8, 0))
        else:
            self._dash_free_guide.pack_forget()

    def _update_expiry_banner(self):
        """Show/hide/update the expiry warning banner on the Dashboard."""
        if not hasattr(self, "_dash_expiry_banner"):
            return
        ld = getattr(self.license_manager, "license_data", None) or {}
        valid_until = ld.get("valid_until")
        duration = ld.get("subscription_duration", "")
        tier = self.license_tier

        if tier in ("FREE", "ADMIN"):
            self._dash_expiry_banner.pack_forget()
            return
        if duration == "lifetime" or not valid_until:
            self._dash_expiry_banner.pack_forget()
            return

        try:
            from datetime import datetime, timezone
            exp = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            days_left = (exp - datetime.now(timezone.utc)).days
        except Exception:
            self._dash_expiry_banner.pack_forget()
            return

        if days_left > 7:
            self._dash_expiry_banner.pack_forget()
            return

        if days_left <= 0:
            msg  = "⚠  Your subscription has EXPIRED. Renew now to keep access."
            color = "#7f1d1d"
            border = "#ef4444"
        elif days_left <= 3:
            msg  = f"⚠  Only {days_left} day{'s' if days_left != 1 else ''} left on your {tier} plan. Renew before you lose access."
            color = "#7c2d12"
            border = "#f97316"
        else:
            msg  = f"⏳  Your {tier} plan expires in {days_left} days. Renew now."
            color = "#713f12"
            border = "#f59e0b"

        self._dash_expiry_banner_label.configure(text=msg)
        self._dash_expiry_banner.configure(fg_color=color, border_color=border)
        # Re-pack at top of dash_frame if not visible
        if not self._dash_expiry_banner.winfo_ismapped():
            self._dash_expiry_banner.pack(fill="x", padx=20, pady=(8, 0), before=self._dash_expiry_banner_anchor)

    def _refresh_tier_badges(self):
        """Sync all tier badge widgets to the current self.license_tier value."""
        tier = self.license_tier
        color = TIER_COLORS.get(tier, "#e74c3c")
        label_text = f"  {tier}  "
        if hasattr(self, "dash_tier_badge"):
            self.dash_tier_badge.configure(text=label_text, fg_color=color)
        if hasattr(self, "license_tier_label"):
            self.license_tier_label.configure(text=label_text, fg_color=color)

    def _on_auth_complete(self):
        """Callback from Auth Screen after successful login."""
        track("app_start", {"version": "10.5"})

        # Fetch and show any unseen announcements (non-blocking — after UI is up)
        import threading
        from gui.components.announcement_dialog import show_announcements
        def _fetch_and_show():
            try:
                items = self.license_manager.get_announcements()
                if items:
                    self.root.after(1500, lambda: show_announcements(self.root, items))
            except Exception:
                pass
        threading.Thread(target=_fetch_and_show, daemon=True).start()

        # Set license tier from Supabase license_data now that auth is confirmed.
        if self.license_manager.license_data:
            self.license_tier = self.license_manager.license_data.get("subscription_tier", "FREE")
        elif self.license_manager.is_licensed:
            self.license_tier = "FREE"
        print(f"[License] Tier: {self.license_tier} | Entitlements: {self.license_manager.entitlements}")

        # Show a startup splash while the (heavy) UI builds — the main window is
        # still withdrawn at this point, so without this the user stares at
        # nothing for a few seconds after login. Best-effort: never let splash
        # issues block startup.
        splash = None
        try:
            from gui.components.splash_screen import SplashScreen
            splash = SplashScreen(self.root, title="SpinEdge",
                                  subtitle="Loading interface…")
        except Exception as _sp_err:
            logger.warning(f"[Splash] could not show startup splash: {_sp_err}")

        def _splash_status(msg):
            if splash is not None:
                splash.set_status(msg)

        # Build the entire UI now — tier is known so tabs/badges render correctly
        _splash_status("Building interface…")
        self.create_widgets()
        self.update_coordinate_display()
        self._start_license_heartbeat()

        # Load secure strategies & presets
        _splash_status("Loading strategies & bundles…")
        self.refresh_encrypted_strategies()
        self.update_strategy_dropdown()
        self.update_strategy_selector()
        if hasattr(self, 'refresh_rotation_presets_dropdown'):
            self.refresh_rotation_presets_dropdown()
        if hasattr(self, 'refresh_dashboard_bundles'):
            self.refresh_dashboard_bundles()

        # Register global hotkeys (Ctrl+1..9 to favorites, Ctrl+` to toggle).
        # Safe to call before the setup wizard — hotkeys just no-op until the
        # user adds favorites.
        _splash_status("Finishing up…")
        self._register_global_hotkeys()

        # UI is ready — tear down the splash before the main window / wizard shows.
        if splash is not None:
            try:
                splash.close()
            except Exception:
                pass

        # Check if we need to run Setup Wizard
        if not self.coordinates:
            self._run_setup_wizard()
        else:
            self.root.deiconify()
            self._finalize_init()

    def _run_setup_wizard(self):
        """Launches the Setup Wizard on first boot."""
        # Hide main window temporarily
        self.root.withdraw()
        # Launch wizard
        self.wizard = SetupWizard(self.root, self._on_wizard_complete)

    def _on_wizard_complete(self, preset_data):
        """Callback from Setup Wizard."""
        self.root.deiconify() # Show main window again
        
        preset_name = preset_data.get('name', 'Custom') if preset_data else 'Custom'
        track("wizard_completed", {"preset": preset_name})
        
        if preset_data:
            # Merge preset coordinates into existing (don't overwrite manually set ones)
            new_coords = preset_data.get("coordinates", {})
            if not self.coordinates:
                self.coordinates = {}
            for k, v in new_coords.items():
                self.coordinates[k] = v  # Wizard is first-time setup, safe to set all

            new_regions = preset_data.get("custom_regions", {})
            if not self.custom_regions:
                self.custom_regions = {}
            if isinstance(new_regions, dict):
                self.custom_regions.update(new_regions)
            elif isinstance(new_regions, list):
                self.custom_regions = new_regions

            self.config["coordinates"] = self.coordinates
            self.config["custom_regions"] = self.custom_regions
            save_config(self.config)
            
            self.update_coord_list_display()
            if hasattr(self, 'region_label_dropdown'):
                region_labels = ["balance*", "table_state*", "recent_winning_numbers*"] + self.custom_regions
                self.region_label_dropdown["values"] = region_labels
                
            messagebox.showinfo("Setup Complete", f"Loaded preset: {preset_data.get('name', 'Custom')}\nReady to play!")
        
        self._finalize_init()

    def _finalize_init(self):
        """Runs the final initialization steps that depend on setup being done."""
        # Prompt for current balance if not set
        if "current_balance" not in self.config or not isinstance(self.config["current_balance"], (int, float)):
            while True:
                bal = self.simple_input("Enter your current balance:", "Current Balance Required")
                try:
                    bal_val = float(bal)
                    self.config["current_balance"] = bal_val
                    save_config(self.config)
                    break
                except Exception:
                    messagebox.showerror("Invalid Input", "Please enter a valid number for balance.")
                    
        # Set initial current balance in stats display
        self.update_stats_display(starting_balance=self.config.get("current_balance", 0.0), projected_balance=self.config.get("current_balance", 0.0))

    def load_advanced_strategies(self):
        """Scan advanced_strategies directory for JSON files"""
        strategies = {}
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "advanced_strategies")
        if os.path.exists(strategies_dir):
            for file in os.listdir(strategies_dir):
                if file.endswith(".json"):
                    try:
                        with open(os.path.join(strategies_dir, file), "r") as f:
                            data = json.load(f)
                            name = data.get("name")
                            if name:
                                strategies[name] = data
                    except Exception as e:
                        print(f"Error loading advanced strategy {file}: {e}")
        return strategies

    def get_all_strategy_names(self):
        """Get list of all available strategies (Built-in + Custom + Advanced)"""
        # Built-in (from StrategyEngine)
        try:
             from config.presets import get_preset_names
             builtin = get_preset_names()
        except Exception:
             builtin = ["martingale", "dalembert", "fibonacci"] # Fallback

        custom = list(self.custom_strategies.keys()) if hasattr(self, 'custom_strategies') else []
        advanced = list(self.load_advanced_strategies().keys())
        
        # Deduplicate and sort
        all_names = sorted(list(set(builtin + custom + advanced)))
        return all_names

    def _attach_window_watermark(self):
        """Show a gold border watermark overlay on the selected gameplay window."""
        # Detach any existing watermark
        if self._window_watermark:
            try:
                self._window_watermark.detach()
                self._window_watermark.destroy()
            except Exception:
                pass
            self._window_watermark = None

        if not self.recorder.browser_win:
            return

        try:
            from gui.components.window_watermark import WindowWatermark
            self._window_watermark = WindowWatermark(self.root)
            self._window_watermark.attach(self.recorder.browser_win)
        except Exception as e:
            print(f"[Watermark] Failed to create: {e}")

    def _detach_window_watermark(self):
        """Remove the window watermark overlay."""
        if self._window_watermark:
            try:
                self._window_watermark.detach()
                self._window_watermark.destroy()
            except Exception:
                pass
            self._window_watermark = None

    def highlight_selected_window(self):
        if not self.recorder.browser_win:
            messagebox.showerror("Error", "Please select a window first.")
            return
        self.recorder.flash_window_border()
        self.recorder.flash_window_border()

    def refresh_encrypted_strategies(self):
        """Scans strategies/ directory for .spine files and adds them to custom_strategies."""
        
        # 1. Get current license tier from database
        user_tier = "FREE"
        if hasattr(self, "license_manager") and self.license_manager.license_data:
            user_tier = self.license_manager.license_data.get("subscription_tier", "FREE")
        
        logger.info(f"Refreshing strategies for Tier: {user_tier}")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        # Handle frozen state
        import sys
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.getcwd() 

        strategies_dir = os.path.join(base_dir, "strategies")
        if not os.path.exists(strategies_dir):
            try:
                os.makedirs(strategies_dir)
            except OSError:
                pass
            return

        # Tier Hierarchy for UI filtering (Engine already does it, but we want to show LOCKED ones maybe?)
        # For now, duplicate logic or trust engine to return the list? 
        # Actually our StrategyEngine ALREADY has a method load_encrypted_strategies
        # But here we are manually decrypting to add to self.custom_strategies dictionary.
        # Ideally we should use shared logic.
        
        # Checking tiers locally to populate the dropdown
        TIER_LEVELS = {
            "FREE": 0, # Legacy fallback
            "BASIC": 1,
            "PLUS": 2,
            "PRO": 3,
            "ADMIN": 99
        }
        user_level = TIER_LEVELS.get(user_tier.upper(), 0)

        spine_files = glob.glob(os.path.join(strategies_dir, "*.spine"))
        count = 0
        for filepath in spine_files:
            try:
                with open(filepath, "rb") as f:
                    encrypted_bytes = f.read()
                
                strategy_data = decrypt_strategy_data(encrypted_bytes)
                if strategy_data and isinstance(strategy_data, dict):
                    name = os.path.splitext(os.path.basename(filepath))[0]
                    strat_tier = strategy_data.get("tier", "FREE").upper()
                    strat_level = TIER_LEVELS.get(strat_tier, 0)
                    
                    if user_level >= strat_level:
                        display_name = f"🔒 {name}"
                        self.custom_strategies[display_name] = strategy_data
                        count += 1
                    else:
                        # Show as locked?
                        display_name = f"🚫 {name} ({strat_tier})"
                        # We can add it but maybe disable selection or show a warning if selected
                        # For now, let's add it so user knows it exists, but we WON'T be able to run it
                        # because StrategyEngine also checks.
                        # Actually adding it to custom_strategies allows selection in dropdown.
                        # The "StrategyEngine" is usually instantiated LATER with "custom_strategies".
                        # If we put data here, the engine might run it unless the engine re-checks.
                        # Engine re-validates only if using its own load method. 
                        # But GUI passes self.custom_strategies directly to Engine.__init__.
                        # So GUI is the gatekeeper here.
                        
                        # Let's NOT add it to custom_strategies (so it can't be run)
                        # But MAYBE add to a separate list for "Shown but locked"?
                        # For MVp, just skipping it is safer and cleaner.
                        logger.info(f"Skipping {name}: Tier {strat_tier} > {user_tier}")
                        
            except Exception as e:
                logger.error(f"Failed to load encrypted strategy {filepath}: {e}")
        
        if count > 0:
            logger.info(f"Loaded {count} encrypted strategies for tier {user_tier}.")

    def refresh_encrypted_strategies_ui(self):
        self.refresh_encrypted_strategies()
        self.update_strategy_dropdown()
        self.update_strategy_selector()
        messagebox.showinfo("Refreshed", "Scanned for encrypted strategies.")

    def apply_preset_gui(self):
        name = self.preset_var.get()
        if not name:
            messagebox.showwarning("Warning", "Please select a preset first.")
            return
            
        preset = get_preset(name)
        if not preset:
            messagebox.showerror("Error", f"Could not find preset data for {name}")
            return
            
        if not self.recorder.browser_win:
            messagebox.showwarning("Window Required", "Please SELECT a window first using 'Select Window' in Bot Control.")
            return
            
        success = self.recorder.load_preset(preset)
        if success:
            messagebox.showinfo("Success", f"Applied preset '{name}'.\nCoordinates have been updated relative to the window size.")
            self.update_coordinate_display()
        else:
            messagebox.showerror("Error", "Failed to apply preset.")

    def auto_detect_table(self):
        """Auto-detect roulette table layout from a screenshot of the selected window."""
        if not self.recorder.browser_win:
            messagebox.showwarning("Window Required",
                                   "Please SELECT a browser window first using 'Select Window' in Bot Control.")
            return

        import threading

        def _detect():
            try:
                calibrator = AutoCalibrator()
                hwnd = self.recorder._browser_hwnd

                # Capture screenshot
                self.root.after(0, lambda: self._set_status("Capturing screenshot..."))
                screenshot = calibrator.capture_window(hwnd)
                if screenshot is None:
                    self.root.after(0, lambda: messagebox.showerror("Error", "Failed to capture window screenshot."))
                    return

                # Detect table
                self.root.after(0, lambda: self._set_status("Detecting roulette table layout..."))
                result = calibrator.detect_table(screenshot)
                if result is None:
                    self.root.after(0, lambda: messagebox.showerror(
                        "Detection Failed",
                        "Could not detect the roulette table.\n\n"
                        "Make sure the betting grid is fully visible in the browser window "
                        "and try again."))
                    return

                # Optional OCR refinement
                self.root.after(0, lambda: self._set_status("Verifying with OCR..."))
                result = calibrator.refine_with_ocr(screenshot, result)

                # Generate preset
                preset = calibrator.generate_preset(result, name="Auto-Detected Layout")
                validation = calibrator.validate_preset(preset)

                # Generate debug image for preview
                debug_img = calibrator.generate_debug_image(screenshot, result)

                # Show results on main thread
                def _show_results():
                    self._show_auto_detect_results(preset, validation, debug_img, result)
                self.root.after(0, _show_results)

            except Exception as e:
                logger.exception("Auto-detect failed")
                self.root.after(0, lambda: messagebox.showerror("Error", f"Auto-detection failed:\n{e}"))
            finally:
                self.root.after(0, lambda: self._set_status("Ready"))

        threading.Thread(target=_detect, daemon=True).start()

    def _show_auto_detect_results(self, preset, validation, debug_img, result):
        """Show auto-detection results in a preview dialog."""
        import cv2
        from PIL import Image, ImageTk

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Auto-Detect Results")
        dialog.geometry("800x620")
        dialog.attributes("-topmost", True)
        dialog.configure(fg_color="#0D0F14")

        # Header
        header = ctk.CTkFrame(dialog, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))

        confidence_pct = f"{result.confidence:.0%}"
        color = "#2ecc71" if result.confidence > 0.7 else "#f39c12" if result.confidence > 0.4 else "#e74c3c"
        ctk.CTkLabel(header, text=f"Detection Confidence: {confidence_pct}",
                     font=("Arial", 16, "bold"), text_color=color).pack(side="left")

        ctk.CTkLabel(header, text=f"{validation['total_coordinates']} coordinates generated",
                     font=("Arial", 12), text_color="#bdc3c7").pack(side="right")

        # Preview image
        preview_frame = ctk.CTkFrame(dialog)
        preview_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # Convert OpenCV BGR to PIL for display
        rgb_img = cv2.cvtColor(debug_img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb_img)
        # Scale to fit
        display_w, display_h = 760, 380
        pil_img.thumbnail((display_w, display_h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(pil_img)

        img_label = tk.Label(preview_frame, image=photo, bg="#0D0F14")
        img_label.image = photo  # keep reference
        img_label.pack(pady=10)

        # Validation info
        if validation["issues"]:
            issues_text = "\n".join(f"  - {issue}" for issue in validation["issues"][:5])
            ctk.CTkLabel(dialog, text=f"Issues found:\n{issues_text}",
                         font=("Arial", 10), text_color="#e74c3c",
                         justify="left").pack(padx=20, anchor="w")

        # Chip info
        chips_detected = len(result.chip_positions)
        chip_text = f"{chips_detected} chip positions detected" if chips_detected else "No chips detected — chip coordinates need manual setup"
        chip_color = "#2ecc71" if chips_detected >= 4 else "#f39c12"
        ctk.CTkLabel(dialog, text=chip_text, font=("Arial", 10),
                     text_color=chip_color).pack(padx=20, anchor="w", pady=(0, 5))

        # Buttons
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(5, 15))

        def apply_detected():
            coords = preset.get("coordinates", {})
            # Merge into existing coordinates — never overwrite manually-calibrated
            # regions (balance, table_state, etc.) or chips the user already set up
            existing = self.coordinates or {}
            protected_keys = {"balance", "table_state", "recent_winning_numbers",
                              "winning_number_region"}
            # Also protect any chip keys the user already has
            for k in list(existing.keys()):
                if k.startswith("chip_") and k in coords:
                    protected_keys.add(k)

            applied = 0
            skipped = 0
            for label, data in coords.items():
                if label in protected_keys and label in existing:
                    skipped += 1
                    continue
                if "x_pct" in data and "y_pct" in data:
                    self.recorder.on_capture(label, data["x_pct"], data["y_pct"])
                    applied += 1

            self.update_coord_list_display()
            msg = f"Applied {applied} auto-detected coordinates."
            if skipped:
                msg += f"\nSkipped {skipped} existing manual coordinates (regions, chips)."
            msg += "\nVerify accuracy by testing a few bets."
            messagebox.showinfo("Applied", msg)
            dialog.destroy()

        def save_as_preset():
            name = self.simple_input("Enter preset name:", "Save Auto-Detected Preset")
            if name:
                preset["name"] = name
                presets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "custom_presets")
                os.makedirs(presets_dir, exist_ok=True)
                filepath = os.path.join(presets_dir, f"{name.lower().replace(' ', '_')}.json")
                import json
                with open(filepath, "w") as f:
                    json.dump(preset, f, indent=4)
                messagebox.showinfo("Saved", f"Preset saved to:\n{filepath}")

        ctk.CTkButton(btn_frame, text="Apply Coordinates", command=apply_detected,
                       fg_color="#27ae60", hover_color="#2ecc71", width=180).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Save as Preset", command=save_as_preset,
                       fg_color="#8e44ad", hover_color="#9b59b6", width=140).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy,
                       fg_color="#7f8c8d", hover_color="#95a5a6", width=100).pack(side="right", padx=5)

    def _set_status(self, text):
        """Update status bar if available."""
        if hasattr(self, 'status_label'):
            try:
                self.status_label.configure(text=text)
            except Exception:
                pass

    def _augment_rotation_for_extend_at_high(self, rotation_str: str) -> tuple:
        """Ensure each rotation entry has the conditional win rules that make
        'End only at session high' actually work end-to-end.

        Returns (new_rotation_str, count_modified).

        Without these rules the bundle has the EXTENSION enabled (session keeps
        playing past its time/round limit until profit recovers to session
        high) but the bet RESETS to base on every win — which defeats the
        purpose because the strategy can never escalate enough to recover the
        drawdown. The fix mirrors conservativeV15's pattern: keep the bet on
        sub-high wins, reset only when profit hits/exceeds the session high.
        """
        if not rotation_str:
            return rotation_str, 0
        need_below = "win:keep|condition=profit_below_session_high"
        need_above = "win:reset_to_base|condition=profit_at_or_above_session_high"
        modified = 0
        new_entries = []
        for entry in rotation_str.split(","):
            entry = entry.strip()
            if not entry:
                continue
            # Entry shape: "name:progression|rules=...|other_params"
            # We only touch entries that have a `rules=` section AND lack
            # the two conditional win rules.
            if "rules=" not in entry:
                # No rules at all — only inject if the entry is a dynamic
                # progression (others like flat don't accept dynamic rules).
                if ":dynamic" in entry:
                    entry = f"{entry}|rules=loss:martingale;{need_below};{need_above}"
                    modified += 1
                new_entries.append(entry)
                continue
            head, _, tail = entry.partition("rules=")
            # rules section runs until the next pipe — but pipes inside
            # rule params (`condition=...`) are part of the rules. Per the
            # existing parser (strategy_engine.py:226), the rules section
            # is everything after `rules=` until end-of-entry. Multiple
            # rules are split by ';'.
            rules_section = tail
            existing = rules_section.split(";")
            has_below = any("profit_below_session_high" in r for r in existing)
            has_above = any("profit_at_or_above_session_high" in r for r in existing)
            additions = []
            if not has_below:
                additions.append(need_below)
            if not has_above:
                additions.append(need_above)
            if additions:
                merged = ";".join(existing + additions)
                entry = f"{head}rules={merged}"
                modified += 1
            new_entries.append(entry)
        return ",".join(new_entries), modified

    def _build_bundle_data(self):
        """Build the full bundle dict from current config state. Returns (data, errors)."""
        import uuid

        current_list = self.rotation_strategies_var.get().strip() if hasattr(self, 'rotation_strategies_var') else ""
        errors = []

        # ── Validation ────────────────────────────────────────────────
        if not current_list:
            errors.append("No strategies in the rotation list.")
        strat_list = [s.strip() for s in current_list.split(",") if s.strip()]
        base_bet = float(self.config.get("base_bet", 1.0))
        max_bet = float(self.config.get("max_bet", 100.0))
        if base_bet <= 0:
            errors.append("Base Bet must be greater than 0.")
        if max_bet < base_bet:
            errors.append(f"Max Bet (${max_bet}) must be >= Base Bet (${base_bet}).")
        session_dur = int(self.config.get("session_duration", 1))
        if session_dur <= 0:
            errors.append("Session duration must be > 0.")
        min_gap = int(self.config.get("min_gap_minutes", 30))
        max_gap = int(self.config.get("max_gap_minutes", 120))
        if min_gap > max_gap:
            errors.append(f"Min gap ({min_gap}) cannot be greater than max gap ({max_gap}).")

        if errors:
            return None, errors

        # ── Derive strategy info from rotation list ───────────────────
        strat_names = []
        prog_types = set()
        for entry in strat_list:
            head = entry.split("|")[0]
            if ":" in head:
                sname, prog = head.split(":", 1)
                strat_names.append(sname)
                prog_types.add(prog)
            else:
                strat_names.append(head)

        if len(prog_types) == 1:
            progression_type = next(iter(prog_types))
        elif prog_types:
            progression_type = "mixed"
        else:
            progression_type = self.auto_roulette_progression_var.get() if hasattr(self, 'auto_roulette_progression_var') else "custom"

        strategy_name = ", ".join(dict.fromkeys(strat_names)) if strat_names else "unknown"
        k_value = self.auto_roulette_k_var.get() if hasattr(self, 'auto_roulette_k_var') else "2"
        rotation_mode = self.rotation_mode_var.get() if hasattr(self, 'rotation_mode_var') else "random"

        raw_max_loss = self.config.get("max_loss", 100.0)
        try:
            max_loss = float(raw_max_loss)
        except (ValueError, TypeError):
            max_loss = 100.0

        dynamic_rules = getattr(self, 'dynamic_rules', [])

        data = {
            "bundle_id": str(uuid.uuid4()),
            "source": "local",
            "meta": {
                "created_at": datetime.now().isoformat(),
                "version": "1.2"
            },
            "strategy_config": {
                "strategy_name": strategy_name,
                "progression_type": progression_type,
                "k_value": k_value,
                "rotation_list_str": current_list,
                "rotation_mode": rotation_mode,
                "rotation_trigger": self.rotation_trigger_var.get() if hasattr(self, 'rotation_trigger_var') else "session_end",
                "switch_after_n_losses": self.switch_after_n_losses_var.get() if hasattr(self, 'switch_after_n_losses_var') else 1,
                "carry_progression_on_switch": self.carry_progression_var.get() if hasattr(self, 'carry_progression_var') else True,
                "reset_rotation_on_session": self.reset_rotation_on_session_var.get() if hasattr(self, 'reset_rotation_on_session_var') else False,
                "rotation_progression_override": self.rotation_progression_override_var.get() if hasattr(self, 'rotation_progression_override_var') else False,
                "filter_by_regime": self.filter_regime_var.get() if hasattr(self, 'filter_regime_var') else False,
                # Conditional-trigger selection (see core/triggers.py). When
                # selection_mode == "conditional", per-strategy `triggers` and
                # `tiebreaker` drive which rotation entry plays each round;
                # `fallback` decides behavior when no candidate is armed.
                # `triggers_config` is the single source of truth on the GUI
                # side — held on self.triggers_config and edited via the
                # Triggers dialog. Defaults preserve plain rotation behavior.
                **({
                    "selection_mode": (getattr(self, 'triggers_config', {}) or {}).get('selection_mode', 'rotation'),
                    "triggers":       dict((getattr(self, 'triggers_config', {}) or {}).get('triggers') or {}),
                    "global_trigger": (getattr(self, 'triggers_config', {}) or {}).get('global_trigger'),
                    "tiebreaker":     (getattr(self, 'triggers_config', {}) or {}).get('tiebreaker', 'coldest'),
                    "fallback":       (getattr(self, 'triggers_config', {}) or {}).get('fallback', 'stay'),
                }),
            },
            "betting_config": {
                "base_bet": base_bet,
                "max_loss": max_loss,
                "max_bet": max_bet,
                "session_duration": session_dur,
                "num_sessions": int(self.config.get("num_sessions", 1)),
                "min_gap_minutes": min_gap,
                "max_gap_minutes": max_gap,
                "profit_target": self.config.get("profit_target", 0),
                "enable_trailing_stop": bool(self.config.get("enable_trailing_stop", False)),
                "trailing_stop_amount": self.config.get("trailing_stop_amount", 0),
                "session_ext_after_win": bool(self.config.get("session_ext_after_win", False)),
                "session_ext_at_high": bool(self.config.get("session_ext_at_high", False)),
                "max_extension_rounds": int(self.config.get("max_extension_rounds", 20)),
                "extension_give_up_amount": float(self.config.get("extension_give_up_amount", 50.0)),
                "enable_global_stop": bool(self.config.get("enable_global_stop", False)),
                "global_profit_stop": self.config.get("global_profit_stop", 0),
                "global_stop_loss": self.config.get("global_stop_loss", 0),
                "observation_trigger": int(self.config.get("observation_trigger", 0)),
                # 0 = disabled (per-strategy only, via entry suffix). Was 5, which
                # silently planted a global per-leg cap that stopped legs after 5
                # losses — the "only playing 1 strategy" bug. Keep 0 here.
                "max_consec_losses": int(self.config.get("max_consec_losses", 0)),
                # Escalation on session stop-loss (multiply base bet & stop-loss
                # after each session SL hit, reset on global / session profit).
                "enable_escalation_on_loss": bool(self.config.get("enable_escalation_on_loss", False)),
                "escalation_multiplier": float(self.config.get("escalation_multiplier", 2.0)),
                "escalation_max_steps": int(self.config.get("escalation_max_steps", 4)),
                "escalation_per_step": str(self.config.get("escalation_per_step", "") or ""),
            },
            "dynamic_rules": dynamic_rules
        }
        return data, []

    def _bundle_preview_text(self, name, data):
        """Build a human-readable preview string for a bundle."""
        strat_list = [s.strip() for s in data["strategy_config"]["rotation_list_str"].split(",") if s.strip()]
        _names = [s.split(":")[0].split("|")[0] for s in strat_list[:5]]
        _suffix = f"... +{len(strat_list) - 5} more" if len(strat_list) > 5 else ""
        _bc = data["betting_config"]
        _sc = data["strategy_config"]

        lines = [
            f"Name:  {name}",
            f"Bundle ID:  {data['bundle_id'][:8]}...",
            f"",
            f"── Strategy ──",
            f"  Strategies ({len(strat_list)}):  {', '.join(_names)}{_suffix}",
            f"  Progression:  {_sc['progression_type']}",
            f"  Rotation Mode:  {_sc['rotation_mode']}",
            f"  Switch On:  {'Loss (after ' + str(_sc.get('switch_after_n_losses', 1)) + ')' if _sc.get('rotation_trigger') == 'on_loss' else 'Session End'}"
            + (f"  |  Carry Progression: {'Yes' if _sc.get('carry_progression_on_switch', True) else 'No'}" if _sc.get('rotation_trigger') == 'on_loss' else ""),
            *(([f"  Per-Strategy Progressions:  Yes"] if _sc.get('rotation_progression_override') else [])
            + ([f"  Smart Filter:  On"] if _sc.get('filter_by_regime') else [])),
            f"",
            f"── Betting ──",
            f"  Base Bet:  ${_bc['base_bet']:.2f}",
            f"  Max Loss:  ${_bc['max_loss']:.2f}",
            f"  Max Bet:  ${_bc['max_bet']:.2f}",
            f"  Session:  {_bc['session_duration']} min x {_bc['num_sessions']} sessions",
            f"  Gap:  {_bc['min_gap_minutes']}-{_bc['max_gap_minutes']} min",
        ]
        pt = _bc.get('profit_target', 0)
        if pt and float(pt) > 0:
            lines.append(f"  Profit Target:  ${float(pt):.2f}")
        if _bc.get('enable_trailing_stop'):
            lines.append(f"  Trailing Stop:  ${_bc['trailing_stop_amount']}")
        if _bc.get('enable_escalation_on_loss'):
            per_step = str(_bc.get('escalation_per_step', '') or '').strip()
            if per_step:
                lines.append(f"  Escalation:  per-step ×[{per_step}] on SL")
            else:
                lines.append(
                    f"  Escalation:  ×{_bc.get('escalation_multiplier', 2.0)} "
                    f"on SL (cap {_bc.get('escalation_max_steps', 4)} steps)"
                )
        ext_parts = []
        if _bc.get('session_ext_after_win'):
            ext_parts.append("after_win")
        if _bc.get('session_ext_at_high'):
            ext_parts.append("at_high")
        if ext_parts:
            lines.append(f"  Extension:  {', '.join(ext_parts)} (max {_bc['max_extension_rounds']} rounds)")
        if _bc.get('enable_global_stop'):
            lines += [f"", f"── Global Stops ──",
                       f"  Profit:  {_bc['global_profit_stop']}  |  Loss:  {_bc['global_stop_loss']}"]
        dr = data.get("dynamic_rules", [])
        if dr:
            lines += [f"", f"── Rules: {len(dr)} ──"]
        return "\n".join(lines)

    def save_bundle(self, overwrite_path=None, overwrite_name=None):
        """Saves the current configuration as a full Bundle.
        If overwrite_path is given, saves there without asking for name.
        Always saves .json locally. Use export_bundle_spine() for encrypted distribution."""
        import tkinter.simpledialog as simpledialog

        data, errors = self._build_bundle_data()
        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors))
            return None

        # ── Determine name ────────────────────────────────────────────
        if overwrite_name:
            name = overwrite_name
        elif overwrite_path:
            name = os.path.splitext(os.path.basename(overwrite_path))[0]
        else:
            name = simpledialog.askstring("Save Strategy Bundle",
                                          "Enter a name for this bundle (e.g. 'Aggressive_Martingale'):")
            if not name:
                return None

        data["name"] = name
        data["meta"]["name"] = name

        # ── Preview & confirm ─────────────────────────────────────────
        preview = self._bundle_preview_text(name, data)
        if not messagebox.askyesno("Confirm Bundle Save", f"{preview}\n\nSave as .json?"):
            return None

        # ── Write .json ───────────────────────────────────────────────
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        os.makedirs(bundles_dir, exist_ok=True)

        if overwrite_path:
            filename = overwrite_path
        else:
            safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
            filename = os.path.join(bundles_dir, f"{safe_name}.json")

        try:
            # Preserve bundle_id from existing file when overwriting
            if overwrite_path and os.path.exists(overwrite_path):
                try:
                    with open(overwrite_path, "r") as ef:
                        existing = json.load(ef)
                    if "bundle_id" in existing:
                        data["bundle_id"] = existing["bundle_id"]
                    if existing.get("meta", {}).get("created_at"):
                        data["meta"]["created_at"] = existing["meta"]["created_at"]
                except Exception:
                    pass

            with open(filename, "w") as f:
                json.dump(data, f, indent=4)

            # Remove stale .spine file if we saved a .json (dashboard prefers .spine)
            if filename.endswith(".json"):
                stale_spine = filename.rsplit(".json", 1)[0] + ".spine"
                if os.path.exists(stale_spine):
                    try:
                        os.remove(stale_spine)
                    except OSError:
                        pass

            messagebox.showinfo("Bundle Saved", f"Saved: {filename}\nBundle ID: {data['bundle_id'][:8]}...")
            # Point the dropdown selections at the just-saved bundle BEFORE refreshing —
            # the refresh functions preserve the current selection only if it appears
            # in the new list, so setting it here makes the saved bundle the active
            # pick in every dropdown instead of getting wiped to "Select Bundle...".
            saved_name = os.path.splitext(os.path.basename(filename))[0]
            if hasattr(self, 'dashboard_bundle_var'):
                self.dashboard_bundle_var.set(saved_name)
            if hasattr(self, 'botcontrol_bundle_var'):
                self.botcontrol_bundle_var.set(saved_name)
            self.refresh_dashboard_bundles()
            if hasattr(self, 'botcontrol_bundle_dropdown'):
                self._refresh_botcontrol_bundles()
            # rotation_preset_dropdown lists both presets AND bundles — without this
            # refresh, newly saved bundles only appear after an app restart (which
            # rebuilds the dropdown via init).
            if hasattr(self, 'refresh_rotation_presets_dropdown'):
                try:
                    self.refresh_rotation_presets_dropdown()
                except Exception:
                    pass
            return filename
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save bundle: {e}")
            return None

    def export_bundle_spine(self):
        """Export the current bundle as an encrypted .spine file for distribution."""
        data, errors = self._build_bundle_data()
        if errors:
            messagebox.showerror("Validation Error", "\n".join(errors))
            return

        import tkinter.simpledialog as simpledialog
        name = simpledialog.askstring("Export Encrypted Bundle",
                                      "Enter a name for the .spine file:")
        if not name:
            return

        data["name"] = name
        data["meta"]["name"] = name

        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        os.makedirs(bundles_dir, exist_ok=True)
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()

        try:
            from core.encryption import encrypt_strategy_data
            encrypted_bytes = encrypt_strategy_data(data)
            if not encrypted_bytes:
                messagebox.showerror("Encryption Error", "Failed to encrypt the bundle.")
                return
            filename = os.path.join(bundles_dir, f"{safe_name}.spine")
            with open(filename, "wb") as f:
                f.write(encrypted_bytes)
            messagebox.showinfo("Exported", f"Encrypted bundle saved:\n{filename}")
            # Refresh dropdowns so the new .spine appears without needing a restart.
            if hasattr(self, 'refresh_dashboard_bundles'):
                self.refresh_dashboard_bundles()
            if hasattr(self, '_refresh_botcontrol_bundles') and hasattr(self, 'botcontrol_bundle_dropdown'):
                self._refresh_botcontrol_bundles()
            if hasattr(self, 'refresh_rotation_presets_dropdown'):
                try:
                    self.refresh_rotation_presets_dropdown()
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export: {e}")

    def save_rotation_list_preset(self):
        """Saves ONLY the list of strategies to rotate."""
        import tkinter.simpledialog as simpledialog
        
        current_list = self.rotation_strategies_var.get().strip()
        if not current_list:
            messagebox.showwarning("Empty List", "No strategies in the rotation list to save.")
            return

        name = simpledialog.askstring("Save Rotation List", "Enter a name for this rotation sequence (e.g. 'RedBlackSequence'):")
        if not name:
            return
            
        data = {
            "name": name,
            "strategies_string": current_list,
            "created_at": datetime.now().isoformat()
        }
        
        presets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "rotation_presets")
        os.makedirs(presets_dir, exist_ok=True)
        
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        filename = os.path.join(presets_dir, f"{safe_name}.json")
        
        try:
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("List Saved", f"Saved rotation list '{name}'.")
            self.refresh_rotation_presets_dropdown() # Refresh dropdown
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save rotation list: {e}")

    def refresh_rotation_presets_dropdown(self):
        """Scans for saved list presets AND user bundles, updates the dropdown."""
        TIER_LEVELS = {"FREE": 0, "BASIC": 1, "PLUS": 2, "PRO": 3, "ADMIN": 99}
        user_tier = getattr(self, "license_tier", "FREE")
        if not user_tier:
            user_tier = self.license_manager.license_data.get("subscription_tier", "BASIC") if hasattr(self, "license_manager") and self.license_manager.license_data else "BASIC"
        user_level = TIER_LEVELS.get(user_tier.upper(), 0)

        # Track name -> file path so load_rotation_list_from_dropdown knows where to find each entry
        self._rotation_preset_paths = {}

        # --- 1. Scan static rotation presets (config/rotation_presets/) ---
        presets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "rotation_presets")
        if os.path.exists(presets_dir):
            for f in glob.glob(os.path.join(presets_dir, "*.json")):
                try:
                    with open(f, "r") as json_file:
                        data = json.load(json_file)
                        bundle_id = data.get("bundle_id")
                        if bundle_id and user_level < TIER_LEVELS.get("ADMIN", 99):
                            entitlements = getattr(self.license_manager, "entitlements", [])
                            if bundle_id not in entitlements:
                                logger.info(f"Skipping rotation preset {os.path.basename(f)} - User lacks entitlement: {bundle_id}")
                                continue
                    name = os.path.splitext(os.path.basename(f))[0]
                    self._rotation_preset_paths[name] = f
                except Exception as e:
                    logger.error(f"Failed to read preset {f}: {e}")

        # --- 2. Scan user bundles (~/.spinedge/bundles/) ---
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        if os.path.exists(bundles_dir):
            json_bundles = glob.glob(os.path.join(bundles_dir, "*.json"))
            spine_bundles = glob.glob(os.path.join(bundles_dir, "*.spine"))
            # Group paths by bundle name; try .spine first (preferred/encrypted),
            # fall back to .json if .spine is missing OR fails to decrypt.
            # Previously a stale/corrupt .spine would silently hide a valid .json
            # from this dropdown (while the Dashboard still showed it).
            paths_by_name = {}
            for b_path in json_bundles + spine_bundles:
                bname = os.path.splitext(os.path.basename(b_path))[0]
                paths_by_name.setdefault(bname, {})[os.path.splitext(b_path)[1]] = b_path

            for bname, paths in paths_by_name.items():
                if bname in self._rotation_preset_paths:
                    continue  # static preset already registered with this name

                # Try .spine first, then .json as fallback
                tried = []
                if ".spine" in paths:
                    tried.append(paths[".spine"])
                if ".json" in paths:
                    tried.append(paths[".json"])

                loaded = None
                used_path = None
                for b_path in tried:
                    try:
                        data = None
                        if b_path.endswith(".json"):
                            with open(b_path, "r") as f:
                                data = json.load(f)
                        elif b_path.endswith(".spine"):
                            from core.encryption import decrypt_strategy_data
                            with open(b_path, "rb") as f:
                                data = decrypt_strategy_data(f.read())
                        if data and isinstance(data, dict):
                            loaded = data
                            used_path = b_path
                            break
                        else:
                            logger.info(f"Bundle {os.path.basename(b_path)} load returned empty data — trying next format")
                    except Exception as e:
                        logger.error(f"Error reading bundle {b_path} for rotation dropdown: {e}")

                if not loaded:
                    continue

                # Check that this bundle actually has rotation strategies
                has_rotation = (
                    "strategies_string" in loaded
                    or loaded.get("strategy_config", {}).get("rotation_list_str")
                )
                if not has_rotation:
                    continue

                bundle_id = loaded.get("bundle_id")
                is_local = loaded.get("source") == "local"
                if not is_local and user_level < TIER_LEVELS["ADMIN"]:
                    entitlements = getattr(self.license_manager, "entitlements", [])
                    if not bundle_id or bundle_id not in entitlements:
                        logger.info(f"Skipping bundle {os.path.basename(used_path)} from rotation dropdown - not entitled")
                        continue

                self._rotation_preset_paths[bname] = used_path

        names = sorted(self._rotation_preset_paths.keys())
        if names:
            self.rotation_preset_dropdown.configure(values=names)
            self._rotation_preset_master = list(names)
        else:
            self.rotation_preset_dropdown.configure(values=["No Lists Found"])
            self._rotation_preset_master = ["No Lists Found"]

    def load_rotation_list_from_dropdown(self, choice):
        """Loads the selected preset or bundle from the dropdown."""
        if choice in ["No Lists Found", "Select List...", ""]:
            return

        # Use the path mapping built by refresh_rotation_presets_dropdown
        filepath = getattr(self, "_rotation_preset_paths", {}).get(choice)
        if not filepath or not os.path.exists(filepath):
            # Suppress error if file not found (e.g. cascaded from bundle selection)
            return

        try:
            data = None
            if filepath.endswith(".spine"):
                from core.encryption import decrypt_strategy_data
                with open(filepath, "rb") as f:
                    data = decrypt_strategy_data(f.read())
                if data is None:
                    messagebox.showerror("Error", f"Failed to decrypt '{choice}'.")
                    return
            else:
                with open(filepath, "r") as f:
                    data = json.load(f)

            # Extract rotation strategies: handle both rotation preset and full bundle formats
            rotation_str = None
            if "strategies_string" in data:
                rotation_str = data["strategies_string"]
            elif data.get("strategy_config", {}).get("rotation_list_str"):
                rotation_str = data["strategy_config"]["rotation_list_str"]

            if rotation_str:
                self.rotation_strategies_var.set(rotation_str)
            else:
                messagebox.showerror("Error", "No rotation strategies found in this bundle.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load preset: {e}")

    def load_rotation_list_preset(self):
        # Legacy method - kept just in case or can be removed if fully replaced.
        # Functionality is now covered by load_rotation_list_from_dropdown
        pass

    def _apply_betting_config(self, bet_conf):
        """Apply a bundle's betting_config to BOTH self.config AND the visible
        GUI vars, so widgets, config, and the live run loop never disagree.

        Single source of truth for every bundle load path (toolbar load_bundle
        and dashboard on_dashboard_bundle_select) — having one helper stops the
        two paths from drifting (the old toolbar path wrote self.config only and
        left the widgets showing pre-load values; the live stop-enforcement loop
        reads several of these vars directly, so a stale widget meant the run
        enforced the wrong profit target / global stop / trailing stop).

        Always applies a default for keys missing from older bundles so the
        loaded bundle fully defines the betting state (no stale carry-over).
        max_loss / base_bet are handled separately by each caller (hybrid-value
        parsing) and are deliberately NOT in this map.
        """
        # key -> (cast, gui_var_name_or_None, default)
        _bc_map = {
            "max_bet": (float, "max_bet_var", 100.0),
            "num_sessions": (int, "num_sessions_var", 1),
            "min_gap_minutes": (int, "min_gap_var", 30),
            "max_gap_minutes": (int, "max_gap_var", 120),
            "profit_target": (float, "profit_target_var", 0),
            "enable_trailing_stop": (bool, "enable_trailing_stop_var", False),
            "trailing_stop_amount": (float, "trailing_stop_amount_var", 0),
            "session_ext_after_win": (bool, "session_ext_after_win_var", False),
            "session_ext_at_high": (bool, "session_ext_at_high_var", False),
            "max_extension_rounds": (int, "max_ext_rounds_var", 20),
            "extension_give_up_amount": (float, "ext_give_up_var", 50.0),
            "enable_global_stop": (bool, "enable_global_stop_var", False),
            "global_profit_stop": (float, "global_profit_stop_var", 0),
            "global_stop_loss": (float, "global_stop_loss_var", 0),
            "observation_trigger": (int, "observation_trigger_var", 0),
            # No main-window widget — per-strategy only (entry suffix).
            # 0 = disabled; see strategy_engine get_next_bet.
            "max_consec_losses": (int, None, 0),
            "enable_escalation_on_loss": (bool, "enable_escalation_on_loss_var", False),
            "escalation_multiplier": (float, "escalation_multiplier_var", 2.0),
            "escalation_max_steps": (int, "escalation_max_steps_var", 4),
            "escalation_per_step": (str, "escalation_per_step_var", ""),
        }
        for key, (cast, var_name, default) in _bc_map.items():
            raw = bet_conf.get(key, default)
            try:
                val = cast(raw)
            except (ValueError, TypeError):
                val = default
            self.config[key] = val
            gui_var = getattr(self, var_name, None) if var_name else None
            if gui_var is not None:
                try:
                    gui_var.set(val)
                except Exception:
                    pass

    def load_bundle(self):
        """Loads a Strategy Bundle (.json or .spine) and applies settings to Bot Control."""
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        if not os.path.exists(bundles_dir):
            os.makedirs(bundles_dir, exist_ok=True)

        filename = filedialog.askopenfilename(
            initialdir=bundles_dir,
            title="Select Strategy Bundle",
            filetypes=[
                ("All Bundles", "*.spine *.json"),
                ("Encrypted Bundles", "*.spine"),
                ("JSON Bundles", "*.json"),
            ]
        )

        if not filename:
            return

        try:
            data = None

            if filename.endswith(".spine"):
                from core.encryption import decrypt_strategy_data
                with open(filename, "rb") as f:
                    encrypted_bytes = f.read()
                data = decrypt_strategy_data(encrypted_bytes)
                if data is None:
                    messagebox.showerror("Error", "Failed to decrypt bundle.\nFile may be corrupt or from a different version.")
                    return
            else:
                with open(filename, "r") as f:
                    data = json.load(f)

            # Resolve bundle name from normalized location
            bundle_name = data.get("name") or data.get("meta", {}).get("name") or os.path.splitext(os.path.basename(filename))[0]

            # Handle rotation preset format (strategies_string only, no strategy_config)
            if "strategies_string" in data and "strategy_config" not in data:
                if hasattr(self, 'rotation_strategies_var'):
                    self.rotation_strategies_var.set(data["strategies_string"])
                messagebox.showinfo("Preset Loaded", f"Loaded rotation list: {bundle_name}")
                return

            # --- Full bundle format ---
            # 1. Apply Dynamic Rules
            if "dynamic_rules" in data:
                self.dynamic_rules = data["dynamic_rules"]
                self.config["dynamic_rules"] = self.dynamic_rules
                if hasattr(self, 'refresh_dynamic_rules_listbox'):
                    self.refresh_dynamic_rules_listbox()

            # 2. Apply Strategy Settings
            strat_conf = data.get("strategy_config", {})
            if "strategy_name" in strat_conf and hasattr(self, 'auto_roulette_strategy_var'):
                self.auto_roulette_strategy_var.set(strat_conf["strategy_name"])
            if "progression_type" in strat_conf and hasattr(self, 'auto_roulette_progression_var'):
                self.auto_roulette_progression_var.set(strat_conf["progression_type"])
            if "k_value" in strat_conf and hasattr(self, 'auto_roulette_k_var'):
                self.auto_roulette_k_var.set(strat_conf["k_value"])

            if "rotation_list_str" in strat_conf and hasattr(self, 'rotation_strategies_var'):
                self.rotation_strategies_var.set(strat_conf["rotation_list_str"])
            # Always apply ALL rotation settings with proper defaults for missing keys
            if hasattr(self, 'rotation_mode_var'):
                self.rotation_mode_var.set(strat_conf.get("rotation_mode", "sequential"))
            if hasattr(self, 'rotation_trigger_var'):
                trigger = strat_conf.get("rotation_trigger", "session_end")
                self.rotation_trigger_var.set(trigger)
                if hasattr(self, 'switch_on_loss_var'):
                    self.switch_on_loss_var.set(trigger == "on_loss")
                    self._on_switch_on_loss_toggled()
            if hasattr(self, 'switch_after_n_losses_var'):
                self.switch_after_n_losses_var.set(int(strat_conf.get("switch_after_n_losses", 1)))
            if hasattr(self, 'carry_progression_var'):
                self.carry_progression_var.set(bool(strat_conf.get("carry_progression_on_switch", True)))
            if hasattr(self, 'reset_rotation_on_session_var'):
                self.reset_rotation_on_session_var.set(bool(strat_conf.get("reset_rotation_on_session", False)))
            if hasattr(self, 'rotation_progression_override_var'):
                self.rotation_progression_override_var.set(bool(strat_conf.get("rotation_progression_override", False)))
            if hasattr(self, 'filter_regime_var'):
                self.filter_regime_var.set(bool(strat_conf.get("filter_by_regime", False)))

            # Conditional-trigger config. Stored on the GUI as a single dict
            # (no per-field Tk vars yet — the Triggers dialog reads/writes
            # this directly). Older bundles without these keys load with the
            # safe defaults that preserve plain rotation behavior.
            self.triggers_config = {
                "selection_mode": (strat_conf.get("selection_mode") or "rotation"),
                "triggers":       dict(strat_conf.get("triggers") or {}),
                "global_trigger": strat_conf.get("global_trigger") or None,
                "tiebreaker":     (strat_conf.get("tiebreaker") or "coldest"),
                "fallback":       (strat_conf.get("fallback") or "stay"),
            }

            # 3. Apply Betting Config
            bet_conf = data.get("betting_config", {})
            if "base_bet" in bet_conf:
                self.config["base_bet"] = float(bet_conf["base_bet"])
            if "max_loss" in bet_conf:
                raw_ml = bet_conf["max_loss"]
                current_bal = float(self.config.get("current_balance", 0))
                parsed_ml = self.parse_hybrid_value(raw_ml, current_bal)
                if parsed_ml and parsed_ml > 0:
                    self.config["max_loss"] = parsed_ml
                elif isinstance(raw_ml, (int, float)):
                    self.config["max_loss"] = float(raw_ml)
                else:
                    self.config["max_loss"] = 100.0

            # 4. Apply extended betting config (v1.1+ bundles) to config AND the
            # visible GUI vars via the shared helper, so the toolbar load matches
            # the dashboard load and the widgets/live-run loop reflect the bundle.
            self._apply_betting_config(bet_conf)

            # Sync session_duration -> session_duration_minutes (run_bot reads _minutes key)
            if "session_duration" in bet_conf:
                self.config["session_duration_minutes"] = int(bet_conf["session_duration"])
                if hasattr(self, 'session_duration_var'):
                    self.session_duration_var.set(str(int(bet_conf["session_duration"])))

            save_config(self.config)

            # Show what was loaded
            bid = data.get("bundle_id", "N/A")
            bid_display = f"{bid[:8]}..." if bid != "N/A" else "N/A"
            messagebox.showinfo("Bundle Loaded", f"Loaded: {bundle_name}\nBundle ID: {bid_display}\nSettings have been updated.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load bundle: {e}")
            logger.error(f"Load Bundle Error: {e}")


    def save_current_as_preset(self):

        """Exports the current coordinates as a JSON preset file."""
        import tkinter.simpledialog as simpledialog
        
        if not self.coordinates:
            messagebox.showwarning("No Data", "No coordinates recorded to save.")
            return

        name = simpledialog.askstring("Save Preset", "Enter a name for this new preset (e.g. 'My Casino Layout'):")
        if not name:
            return

        preset_data = {
            "description": f"Custom preset created on {datetime.now().strftime('%Y-%m-%d')}",
            "coordinates": self.coordinates
        }
        
        # Ensure 'presets' folder exists
        presets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "custom_presets")
        os.makedirs(presets_dir, exist_ok=True)
        
        # Sanitize filename
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        filename = os.path.join(presets_dir, f"{safe_name}.json")
        
        try:
            with open(filename, "w") as f:
                json.dump(preset_data, f, indent=4)
            messagebox.showinfo("Preset Saved", f"Saved preset to:\n{filename}\n\nYou can share this file or load it in future updates.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save preset: {e}")

    def import_config(self):
        """Import a full configuration file (config.json) to restore settings/coordinates."""
        filename = filedialog.askopenfilename(
            title="Import Config JSON",
            filetypes=[("JSON Files", "*.json")]
        )
        if not filename:
             return
             
        try:
            with open(filename, "r") as f:
                new_config = json.load(f)
            
            # Basic Validation
            if not isinstance(new_config, dict):
                 messagebox.showerror("Error", "Invalid config file format.")
                 return
                 
            # Merge logic: Update current config
            self.config.update(new_config)
            
            # Save to persistent storage
            save_config(self.config)
            
            # Reload coordinates if present
            if "coordinates" in self.config:
                self.coordinates = self.config["coordinates"]
                self.update_coord_list_display()
                
            # Reload custom regions if present
            if "custom_regions" in self.config:
                self.custom_regions = self.config["custom_regions"]
                # Update region label selector dropdown
                if hasattr(self, 'region_label_dropdown'):
                    region_labels = ["balance*", "table_state*"] + self.custom_regions
                    self.region_label_dropdown["values"] = region_labels
                
            # Reload custom strategies if present
            if "custom_strategies" in self.config:
                self.custom_strategies = self.config["custom_strategies"]
                # Refresh strategy list in Strategy Builder tab
                if hasattr(self, 'update_strategy_listbox'): 
                    self.update_strategy_listbox()
                # Refresh main strategy dropdown
                if hasattr(self, 'refresh_strategies'):
                    self.refresh_strategies()
                
            # Reload Tesseract path if present
            if "tesseract_path" in self.config:
                self.tesseract_path_var.set(self.config["tesseract_path"])

            messagebox.showinfo("Success", "Configuration imported successfully.\n\nCoordinates, Strategies, and Settings have been updated.")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to import config: {e}")
            logger.error(f"Import Config Error: {e}")

    def export_public_config(self):
        """Export a safe configuration (Coordinates/Regions only) for sharing."""
        try:
            filename = filedialog.asksaveasfilename(
                title="Export Public Setup",
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")],
                initialfile="public_setup.json"
            )
            if not filename:
                return

            # Create safe subset of config
            safe_config = {
                "coordinates": self.config.get("coordinates", {}),
                "custom_regions": self.config.get("custom_regions", []),
                "tesseract_path": self.config.get("tesseract_path", ""),
                # Explicitly EXCLUDE strategies, license, balance, etc.
            }
            
            with open(filename, "w") as f:
                json.dump(safe_config, f, indent=2)
                
            messagebox.showinfo("Success", f"Public setup exported to:\n{filename}\n\nThis file contains ONLY coordinates and regions.\nStrategies and License Key were NOT included.")
            
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export config: {e}")




    def browse_tesseract_path(self):


        filename = filedialog.askopenfilename(
            title="Select Tesseract Executable",
            filetypes=[("Executables", "*.exe"), ("All Files", "*.*")]
        )
        if filename:
            self.tesseract_path_var.set(filename)

    def save_tesseract_path(self):
        path = self.tesseract_path_var.get().strip()
        if not path:
            messagebox.showerror("Error", "Please enter a path.")
            return

        if initialize_ocr(path):
            self.config["tesseract_path"] = path
            save_config(self.config)
            messagebox.showinfo("Success", f"Tesseract initialized and path saved to config.\nPath: {path}")
        else:
            messagebox.showerror("Error", "Could not initialize Tesseract at that path.\nPlease check the file exists.")

    # Merged into main create_widgets call below

    def init_variables(self):
        """Initialize all Tkinter variables used across tabs."""
        # Bot Control Vars
        self.num_sessions_var = tk.StringVar(value=str(self.config.get("num_sessions", 1)))
        self.min_gap_var = tk.DoubleVar(value=self.config.get("min_gap", 5))
        self.max_gap_var = tk.DoubleVar(value=self.config.get("max_gap", 10))
        self.session_timing_var = tk.StringVar(value=self.config.get("session_timing", "random"))
        self.start_time_var = tk.StringVar(value=self.config.get("start_time", "09:00"))
        self.end_time_var = tk.StringVar(value=self.config.get("end_time", "17:00"))
        
        # Auto Roulette Vars
        self.auto_roulette_strategy_var = tk.StringVar(value=self.config.get("auto_roulette_strategy", "martingale"))
        self.auto_roulette_progression_var = tk.StringVar(value=self.config.get("auto_roulette_progression", "martingale"))
        self.auto_roulette_k_var = tk.DoubleVar(value=self.config.get("auto_roulette_k", 2.0))
        self.auto_roulette_status_var = tk.StringVar(value="Stopped")
        self.rotation_strategies_var = tk.StringVar(value="")
        self.rotation_mode_var = tk.StringVar(value="sequential")
        self.rotation_trigger_var = tk.StringVar(value="session_end")
        
        # Dashboard Vars
        self.dashboard_bundle_var = tk.StringVar(value="Select Bundle...")
        
        # Other Vars
        self.preset_var = tk.StringVar(value="")

    def select_navigation_tab(self, tab_name):
        # Hide all nav panels
        for panel in self.nav_panels.values():
            panel.grid_remove()
        
        # Show selected
        if tab_name in self.nav_panels:
            self.nav_panels[tab_name].grid(row=0, column=0, sticky="nsew")
        
        # Update button colors
        for name, btn in self.nav_buttons.items():
            if name == tab_name:
                btn.configure(fg_color=BG_ELEVATED, text_color=TEXT_PRIMARY)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_SECONDARY)

    def create_menu_bar(self):
        """Create the application menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Exit", command=self.root.quit)

        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", "SpinEdge Roulette Automation\nVersion 10.5"))

    def create_widgets(self):
        # 0. Initialize Variables
        self.init_variables()

        # 1. Main Layout & Header
        # Main container with padding (using CTkFrame)
        main_container = ctk.CTkFrame(self.root, corner_radius=0)
        main_container.pack(fill="both", expand=True)

        # ===== HEADER SECTION =====
        header_frame = ctk.CTkFrame(main_container, height=90, fg_color="transparent")
        header_frame.pack(fill="x", padx=30, pady=(25, 15))
        
        # Logo & Title Container
        branding_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        branding_frame.pack(side="left", anchor="center")

        if self.logo_image:
            self.logo_image.configure(size=(64, 64)) 
            ctk.CTkLabel(branding_frame, image=self.logo_image, text="").pack(side="left", padx=(0, 24))
            
        title_stack = ctk.CTkFrame(branding_frame, fg_color="transparent")
        title_stack.pack(side="left", anchor="center")
        
        ctk.CTkLabel(title_stack, text="SPINEDGE", font=("Segoe UI", 36, "bold"), text_color=GOLD).pack(anchor="w", pady=(0, 0))
        ctk.CTkLabel(title_stack, text="Roulette Automation Engine", font=FONT_BODY, text_color=TEXT_MUTED).pack(anchor="w", pady=(2, 0))

        controls_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
        controls_frame.pack(side="right", anchor="center")
        
        # NEW: OCR Status Indicator
        self.dash_ocr_status_dot = ctk.CTkLabel(controls_frame, text="●", font=("Segoe UI", 16), text_color="gray50")
        self.dash_ocr_status_dot.pack(side="left", padx=(0, 5))
        self.dash_ocr_status_text = ctk.CTkLabel(controls_frame, text="Scanner Idle", font=("Segoe UI", 12), text_color="gray50")
        self.dash_ocr_status_text.pack(side="left", padx=(0, 20))
        
        self.dark_mode_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(controls_frame, text="Dark Mode", font=("Segoe UI", 12), variable=self.dark_mode_var, command=self.toggle_dark_mode).pack(side="left", pady=10)

        # Create layout for main content
        self.body_container = ctk.CTkFrame(main_container, fg_color="transparent")
        self.body_container.pack(fill="both", expand=True)
        
        # 2. Menu Bar
        self.create_menu_bar()

        # 3. Sidebar Layout
        self.sidebar_frame = ctk.CTkFrame(self.body_container, width=200, corner_radius=0)
        self.sidebar_frame.pack(side="left", fill="y")
        self.sidebar_frame.pack_propagate(False)  # keep fixed width while window resizes

        self.content_frame = ctk.CTkFrame(self.body_container, corner_radius=0, fg_color="transparent")
        self.content_frame.pack(side="right", fill="both", expand=True, padx=8, pady=8)
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        # 4. Navigation setup
        ctk.CTkLabel(self.sidebar_frame, text="NAVIGATION", font=FONT_HEADING, text_color=TEXT_MUTED).pack(pady=(PAD_SECTION, PAD_GROUP), padx=PAD_SECTION, anchor="w")

        nav_items = ["⚡ Dashboard", "🎯 Operations", "🧠 Strategy Lab", "⚙️ Settings & Setup"]
        self.nav_buttons = {}
        for btn_name in nav_items:
            btn = ctk.CTkButton(
                self.sidebar_frame, text=f"  {btn_name}", font=FONT_TITLE, height=48, corner_radius=CORNER_RADIUS,
                fg_color="transparent", text_color=TEXT_SECONDARY, hover_color=BG_CARD_HOVER,
                anchor="w", command=lambda name=btn_name: self.select_navigation_tab(name)
            )
            btn.pack(fill="x", padx=12, pady=4)
            self.nav_buttons[btn_name] = btn

        # Top-Level Content Panels
        self.nav_panels = {
            "⚡ Dashboard": ctk.CTkScrollableFrame(self.content_frame, fg_color="transparent"),
            "🎯 Operations": ctk.CTkTabview(self.content_frame),
            "🧠 Strategy Lab": ctk.CTkTabview(self.content_frame),
            "⚙️ Settings & Setup": ctk.CTkTabview(self.content_frame)
        }

        # Layout for panels so they overlay (only 1 visible at a time)
        for panel in self.nav_panels.values():
            panel.grid(row=0, column=0, sticky="nsew")

        # --- TIERED TAB LOGIC ---
        tier = self.license_tier
        # Use the canonical helper so PRO/ADMIN etc. are never mis-computed
        allowed = self._get_allowed_tabs(tier)

        logger.info(f"Creating nested views for Tier {tier}: {allowed}")

        # Always add ALL tabs — access is controlled via lock overlays after content is built
        ops_tabview = self.nav_panels["🎯 Operations"]
        ops_tabview.add("Bot Control")
        ops_tabview.add("Auto Roulette")
        ops_tabview.add("Activity Log")
        ops_tabview.add("Round Audit")
        ops_tabview.add("Winning Numbers")

        lab_tabview = self.nav_panels["🧠 Strategy Lab"]
        lab_tabview.add("Strategy Builder")
        lab_tabview.add("Advanced Strategy Builder")
        lab_tabview.add("Backtesting")
        lab_tabview.add("Statistics")

        settings_tabview = self.nav_panels["⚙️ Settings & Setup"]
        settings_tabview.add("Region/Coordinate Setup")
        settings_tabview.add("OCR Settings")
        settings_tabview.add("Settings")

        # Track which tabview owns each tab (needed for overlay management)
        self._tab_parents = {
            "Bot Control": ops_tabview, "Auto Roulette": ops_tabview,
            "Activity Log": ops_tabview, "Winning Numbers": ops_tabview,
            "Strategy Builder": lab_tabview, "Advanced Strategy Builder": lab_tabview,
            "Backtesting": lab_tabview, "Statistics": lab_tabview,
            "Region/Coordinate Setup": settings_tabview,
            "OCR Settings": settings_tabview, "Settings": settings_tabview,
        }

        # Helper to get the target frame — wraps tab content in a scrollable frame
        # so nothing gets clipped regardless of window size
        self._tab_scroll_wrappers = {}
        def get_frame(parent_panel, tab_name):
            if isinstance(parent_panel, ctk.CTkTabview):
                raw = parent_panel.tab(tab_name)
                scroll = ctk.CTkScrollableFrame(raw, fg_color="transparent")
                scroll.pack(fill="both", expand=True)
                scroll.columnconfigure(0, weight=1)
                self._tab_scroll_wrappers[tab_name] = scroll
                return scroll
            elif tab_name == "Dashboard":
                return parent_panel   # Dashboard panel is already a CTkScrollableFrame
            return ctk.CTkFrame(self.root)

        # Default Tab Selection
        self.select_navigation_tab("⚡ Dashboard")

        # =================================================================
        # DASHBOARD TAB IMPLEMENTATION (Premium UX)
        # =================================================================
        dash_frame = get_frame(self.nav_panels["⚡ Dashboard"], "Dashboard")
        dash_frame.columnconfigure(0, weight=1)

        # --- HEADER ROW: Title + Tier Badge ---
        dash_header = ctk.CTkFrame(dash_frame, fg_color="transparent")
        dash_header.pack(fill="x", padx=20, pady=(15, 5))

        ctk.CTkLabel(dash_header, text="⚡ COMMAND CENTER", font=FONT_HERO, text_color=GOLD).pack(side="left")

        # Tier badge (right-aligned)
        tier_color = TIER_COLORS.get(self.license_tier, DANGER)
        self.dash_tier_badge = ctk.CTkLabel(
            dash_header, text=f"  {self.license_tier}  ",
            font=FONT_BODY_BOLD, text_color="white",
            fg_color=tier_color, corner_radius=CORNER_SMALL
        )
        self.dash_tier_badge.pack(side="right", padx=(PAD_GROUP, 0))
        ctk.CTkLabel(dash_header, text="License:", font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="right")

        # --- EXPIRY WARNING BANNER (hidden by default, shown by _update_expiry_banner) ---
        # Anchor widget so we can pack the banner before the KPI strip
        self._dash_expiry_banner_anchor = ctk.CTkFrame(dash_frame, fg_color="transparent", height=0)
        self._dash_expiry_banner_anchor.pack(fill="x", padx=20)
        self._dash_expiry_banner = ctk.CTkFrame(
            dash_frame, fg_color="#713f12", border_width=1, border_color="#f59e0b", corner_radius=8
        )
        self._dash_expiry_banner_label = ctk.CTkLabel(
            self._dash_expiry_banner,
            text="", font=ctk.CTkFont(size=12, weight="bold"), text_color="white", anchor="w"
        )
        self._dash_expiry_banner_label.pack(side="left", padx=14, pady=8)
        ctk.CTkButton(
            self._dash_expiry_banner, text="Renew →", width=80, height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#f59e0b", hover_color="#d97706", text_color="black",
            command=lambda: __import__("webbrowser").open("https://spinedge.pro/shop")
        ).pack(side="right", padx=10)
        # Don't pack it yet — _update_expiry_banner will show it if needed
        self.root.after(100, self._update_expiry_banner)

        # --- FREE TIER: GET STARTED GUIDE ---
        # Always built here (top of dashboard) — shown/hidden by _update_free_guide()
        self._dash_free_guide = ctk.CTkFrame(dash_frame, fg_color="#0f172a", border_width=1, border_color="#334155", corner_radius=10)
        ctk.CTkLabel(self._dash_free_guide, text="🚀  Get Started with SpinEdge",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=GOLD).pack(anchor="w", padx=16, pady=(12, 6))
        steps = [
            ("1", "Set up your target window",   "Open Settings → Region/Coordinate Setup and calibrate your screen.", INFO),
            ("2", "Explore Statistics & History", "Use Statistics tab to review number patterns before placing bets.",   "#a78bfa"),
            ("3", "Unlock Bot Automation",        "Upgrade to BASIC or higher to run the automated betting engine.",    SUCCESS),
        ]
        for num, title, desc, color in steps:
            step_row = ctk.CTkFrame(self._dash_free_guide, fg_color="#1e293b", corner_radius=8)
            step_row.pack(fill="x", padx=12, pady=3)
            ctk.CTkLabel(step_row, text=num, width=28, height=28, font=ctk.CTkFont(size=12, weight="bold"),
                         fg_color=color, corner_radius=14, text_color="black").pack(side="left", padx=(10, 8), pady=8)
            step_col = ctk.CTkFrame(step_row, fg_color="transparent")
            step_col.pack(side="left", fill="x", expand=True, pady=6)
            ctk.CTkLabel(step_col, text=title, font=ctk.CTkFont(size=11, weight="bold"), text_color="white", anchor="w").pack(anchor="w")
            ctk.CTkLabel(step_col, text=desc,  font=ctk.CTkFont(size=10), text_color="#94a3b8", anchor="w").pack(anchor="w")
        ctk.CTkButton(
            self._dash_free_guide, text="⬆  Upgrade Plan — from $49",
            height=34, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981", hover_color="#059669",
            command=lambda: __import__("webbrowser").open("https://spinedge.pro/shop")
        ).pack(padx=16, pady=(8, 14))
        self._update_free_guide()

        # --- KPI CARDS STRIP ---
        kpi_strip = ctk.CTkFrame(dash_frame, fg_color="transparent")
        kpi_strip.pack(fill="x", padx=PAD_SECTION, pady=(PAD_GROUP, PAD_ITEM))
        for i in range(4):
            kpi_strip.columnconfigure(i, weight=1)

        def create_dash_kpi(parent, col, icon, title, default_val, accent_color, attr_name):
            card = ctk.CTkFrame(parent, **{**CARD_STYLE, "border_color": accent_color})
            card.grid(row=0, column=col, sticky="ew", padx=PAD_ITEM, pady=PAD_ITEM)
            card.columnconfigure(0, weight=1)
            # Icon + Title row
            title_row = ctk.CTkFrame(card, fg_color="transparent")
            title_row.pack(fill="x", padx=PAD_CARD_X, pady=(PAD_GROUP, 0))
            ctk.CTkLabel(title_row, text=icon, font=FONT_TITLE).pack(side="left")
            ctk.CTkLabel(title_row, text=title, **KPI_LABEL_STYLE).pack(side="left", padx=(6, 0))
            # Value
            val_label = ctk.CTkLabel(card, text=default_val, **KPI_VALUE_STYLE, text_color=accent_color)
            val_label.pack(anchor="w", padx=PAD_CARD_X, pady=(PAD_INNER, PAD_GROUP))
            setattr(self, attr_name, val_label)

        saved_bal = self.config.get("current_balance", 0.0)
        create_dash_kpi(kpi_strip, 0, "💰", "BALANCE", f"${saved_bal:.2f}", SUCCESS, "dash_kpi_balance")
        create_dash_kpi(kpi_strip, 1, "📈", "PROFIT", "$0.00", GOLD, "dash_kpi_profit")
        create_dash_kpi(kpi_strip, 2, "🎯", "WIN RATE", "0.0%", INFO, "dash_kpi_winrate")
        create_dash_kpi(kpi_strip, 3, "🔄", "SESSIONS", "0 / 0", PURPLE_HOVER, "dash_kpi_sessions")

        # --- MAIN ACTION COLUMN ---
        action_col = ctk.CTkFrame(dash_frame, fg_color="transparent")
        action_col.pack(fill="both", expand=True, padx=15, pady=5)

        # --- TOP: QUICK SETUP PANEL (collapsible, collapsed by default to save space) ---
        setup_panel = CollapsibleFrame(action_col, title="Quick Setup", expanded=False, accent_color=GOLD)
        setup_panel.pack(fill="x", pady=(0, PAD_ITEM))
        _sp = setup_panel.content_frame   # shorthand — all content goes here

        # Window selector
        window_section = ctk.CTkFrame(_sp, fg_color="transparent")
        window_section.pack(fill="x", padx=15, pady=(0, 2))
        ctk.CTkLabel(window_section, text="Target Window:", font=("Segoe UI", 12), text_color="gray60").pack(anchor="w")

        window_btn_row = ctk.CTkFrame(window_section, fg_color="transparent")
        window_btn_row.pack(fill="x", pady=(2, 0))
        window_btn_row.columnconfigure(0, weight=1)
        ctk.CTkButton(
            window_btn_row, text="🖥  Select Window", command=self.select_window_dialog,
            height=30, fg_color="#34495e", hover_color="#4a6680", corner_radius=8, font=("Segoe UI", 11)
        ).grid(row=0, column=0, sticky="ew")
        self.dash_window_label = ctk.CTkLabel(
            window_btn_row, text="No window selected", font=("Segoe UI", 12), text_color="#e74c3c"
        )
        self.dash_window_label.grid(row=0, column=1, padx=(8, 0), sticky="w")

        # Bundle selector
        bundle_section = ctk.CTkFrame(_sp, fg_color="transparent")
        bundle_section.pack(fill="x", padx=15, pady=(0, 2))
        ctk.CTkLabel(bundle_section, text="Wager Bundle:", font=("Segoe UI", 12), text_color="gray60").pack(anchor="w")

        bundle_row = ctk.CTkFrame(bundle_section, fg_color="transparent")
        bundle_row.pack(fill="x", pady=(2, 0))
        bundle_row.columnconfigure(0, weight=1)

        self.dashboard_bundle_dropdown = ctk.CTkComboBox(
            bundle_row, variable=self.dashboard_bundle_var, height=34,
            command=self.on_dashboard_bundle_select, corner_radius=8, font=("Segoe UI", 11)
        )
        self.dashboard_bundle_dropdown.grid(row=0, column=0, sticky="ew")
        # Type-to-search: filter the bundle list by typing (prefix/substring/
        # initials). Master list is refreshed in refresh_dashboard_bundles().
        self._dashboard_bundle_master = []
        self._make_combobox_searchable(self.dashboard_bundle_dropdown, "_dashboard_bundle_master")

        # Right-click dashboard bundle dropdown → add/remove favorite.
        def _show_bundle_context_menu(event):
            name = (self.dashboard_bundle_var.get() or "").strip()
            if not name or name in ("Select Bundle...", "No Bundles Found"):
                return
            menu = tk.Menu(self.root, tearoff=0)
            if name in self._get_favorite_dashboard_bundles():
                menu.add_command(label=f"☆  Remove '{name}' from favorites",
                                 command=lambda: self._remove_dashboard_bundle_favorite(name))
            else:
                menu.add_command(label=f"★  Add '{name}' to favorites",
                                 command=lambda: self._add_dashboard_bundle_favorite(name))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        self.dashboard_bundle_dropdown.bind("<Button-3>", _show_bundle_context_menu)
        _bundle_entry = getattr(self.dashboard_bundle_dropdown, "_entry", None)
        if _bundle_entry is not None:
            _bundle_entry.bind("<Button-3>", _show_bundle_context_menu)

        ctk.CTkButton(
            bundle_row, text="🔄", width=34, height=34, corner_radius=8,
            fg_color="#34495e", hover_color="#4a6680", font=("Segoe UI", 13),
            command=lambda: [self.license_manager.refresh_entitlements(), self.refresh_dashboard_bundles(), self.log_to_dashboard("Bundle list refreshed")]
        ).grid(row=0, column=1, padx=(5, 0))

        ctk.CTkButton(
            bundle_row, text="🗑", width=34, height=34, corner_radius=8,
            fg_color="#922b21", hover_color="#c0392b", font=("Segoe UI", 13),
            command=self.remove_selected_bundle,
        ).grid(row=0, column=2, padx=(5, 0))

        self.refresh_dashboard_bundles()

        # ── Quick-Toggle Pill Bar for dashboard bundles ──
        # Click pill → select bundle (same flow as the dropdown). Right-click → remove.
        # Right-click the dropdown above → add the current selection to favorites.
        bundle_fav_row = ctk.CTkFrame(bundle_section, fg_color="transparent")
        bundle_fav_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(bundle_fav_row, text="⚡ Quick:", font=("Segoe UI", 12), text_color="gray60").pack(side="left", padx=(0, 6))
        self._dashboard_bundle_pills_container = ctk.CTkFrame(bundle_fav_row, fg_color="transparent")
        self._dashboard_bundle_pills_container.pack(side="left", fill="x", expand=True)
        self._render_dashboard_bundle_bar()
        # Re-render highlight when the active bundle changes.
        self.dashboard_bundle_var.trace_add(
            "write",
            lambda *_: (self._render_dashboard_bundle_bar()
                        if getattr(self, "_dashboard_bundle_pills_container", None) is not None else None),
        )

        # Import + Shop buttons row
        bundle_btn_row = ctk.CTkFrame(_sp, fg_color="transparent")
        bundle_btn_row.pack(fill="x", padx=15, pady=(4, 2))
        bundle_btn_row.columnconfigure(0, weight=1)
        bundle_btn_row.columnconfigure(1, weight=1)

        ctk.CTkButton(
            bundle_btn_row, text="📥  Import Bundle", command=self.import_bundle_to_dashboard,
            height=32, font=("Segoe UI", 11), fg_color="#8e44ad", hover_color="#9b59b6", corner_radius=8
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))

        ctk.CTkButton(
            bundle_btn_row, text="🛒  Shop", command=self.open_shop,
            height=32, font=("Segoe UI", 11), fg_color="#e67e22", hover_color="#f39c12", corner_radius=8
        ).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # --- RISK PROFILE SECTION ---
        risk_section = ctk.CTkFrame(_sp, fg_color="transparent")
        risk_section.pack(fill="x", padx=15, pady=(4, 0))

        # Risk label + balance inline
        _risk_header_row = ctk.CTkFrame(risk_section, fg_color="transparent")
        _risk_header_row.pack(fill="x")
        ctk.CTkLabel(_risk_header_row, text="Risk Profile:", font=("Segoe UI", 10, "bold"), text_color="#E6C200").pack(side="left")
        _saved_bal = self.config.get("current_balance", 0.0)
        self.dash_risk_balance_label = ctk.CTkLabel(
            _risk_header_row, text=f"  Balance: ${_saved_bal:,.2f}",
            font=("Segoe UI", 12), text_color="gray60"
        )
        self.dash_risk_balance_label.pack(side="left")
        ctk.CTkButton(
            _risk_header_row, text="✏️", command=self._edit_risk_balance,
            height=20, width=28, font=("Segoe UI", 11),
            fg_color="#2c3e50", hover_color="#34495e", corner_radius=4
        ).pack(side="left", padx=(4, 0))

        self.dash_risk_profile_var = tk.StringVar(value="Use Bundle Values")
        risk_options = [
            "Use Bundle Values",
            "Auto (Smart Default)",
            "Conservative (0.5% Risk)",
            "Balanced (1% Risk)",
            "Aggressive (5.0% Risk)"
        ]
        self.dash_risk_dropdown = ctk.CTkComboBox(
            risk_section, variable=self.dash_risk_profile_var, values=risk_options,
            command=self.update_risk_profile_preview, height=26, font=("Segoe UI", 12)
        )
        self.dash_risk_dropdown.pack(fill="x", pady=(2, 2))

        self.dash_risk_preview_label = ctk.CTkLabel(risk_section, text="Base Bet: -- | Stop Loss: --", font=("Segoe UI", 11), text_color="gray60")
        self.dash_risk_preview_label.pack(anchor="w")
        self.update_risk_profile_preview()
        # ----------------------------

        if "Bot Control" in allowed:
            override_btn_row = ctk.CTkFrame(_sp, fg_color="transparent")
            override_btn_row.pack(fill="x", padx=15, pady=(4, 4))

            ctk.CTkButton(
                override_btn_row, text="⚙️ Advanced Settings",
                command=lambda: [self.select_navigation_tab("🎯 Operations"), self.nav_panels["🎯 Operations"].set("Bot Control")] if "Bot Control" in allowed else None,
                height=28, font=("Segoe UI", 12), fg_color="#2c3e50", hover_color="#34495e", corner_radius=8
            ).pack(fill="x", expand=True)

        # --- BOTTOM: SESSION CONTROL PANEL (always visible) ---
        control_panel = ctk.CTkFrame(action_col, **CARD_STYLE)
        control_panel.pack(fill="x", pady=(0, PAD_ITEM))

        ctk.CTkLabel(control_panel, text="🚀  Session Control", font=FONT_TITLE, text_color=GOLD).pack(anchor="w", padx=PAD_SECTION, pady=(PAD_CARD_Y, 8))

        # Status indicator
        status_row = ctk.CTkFrame(control_panel, fg_color="transparent")
        status_row.pack(fill="x", padx=PAD_SECTION, pady=(0, PAD_GROUP))
        self.dash_status_dot = ctk.CTkLabel(status_row, text="●", font=FONT_TITLE, text_color=STATUS_IDLE)
        self.dash_status_dot.pack(side="left")
        self.dash_status_text = ctk.CTkLabel(status_row, text="  Ready to launch", font=FONT_BODY, text_color=TEXT_SECONDARY)
        self.dash_status_text.pack(side="left")

        # --- Total Run Time input ---
        runtime_row = ctk.CTkFrame(control_panel, fg_color="#0f172a", corner_radius=8)
        runtime_row.pack(fill="x", padx=PAD_SECTION, pady=(0, 4))
        runtime_row.columnconfigure(1, weight=1)

        ctk.CTkLabel(runtime_row, text="⏳  Run for (hrs):", font=ctk.CTkFont(size=11),
                     text_color="#94a3b8").grid(row=0, column=0, padx=(10, 6), pady=8, sticky="w")

        if not hasattr(self, 'total_runtime_var'):
            import tkinter as _tk
            self.total_runtime_var = _tk.StringVar(value="")

        ctk.CTkEntry(runtime_row, textvariable=self.total_runtime_var,
                     width=64, height=28, font=ctk.CTkFont(size=11)).grid(row=0, column=1, padx=(0, 6), pady=8, sticky="w")

        ctk.CTkButton(
            runtime_row, text="Calculate", width=90, height=28,
            font=ctk.CTkFont(size=11), fg_color="#1d4ed8", hover_color="#1e40af",
            command=self._calculate_sessions_from_runtime
        ).grid(row=0, column=2, padx=(0, 10), pady=8)

        # --- Session Plan Summary ---
        plan_card = ctk.CTkFrame(control_panel, fg_color="#0f172a", corner_radius=8)
        plan_card.pack(fill="x", padx=PAD_SECTION, pady=(0, 8))
        plan_card.columnconfigure(0, weight=1)
        plan_card.columnconfigure(1, weight=1)
        plan_card.columnconfigure(2, weight=1)

        def _plan_stat(col, icon, label, attr):
            cell = ctk.CTkFrame(plan_card, fg_color="transparent")
            cell.grid(row=0, column=col, padx=8, pady=6, sticky="ew")
            ctk.CTkLabel(cell, text=icon, font=ctk.CTkFont(size=13)).pack()
            ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=9), text_color="#64748b").pack()
            lbl = ctk.CTkLabel(cell, text="—", font=ctk.CTkFont(size=11, weight="bold"), text_color="white")
            lbl.pack()
            setattr(self, attr, lbl)

        _plan_stat(0, "🔢", "Sessions",   "dash_plan_sessions")
        _plan_stat(1, "⏱",  "Est. Total", "dash_plan_total_time")
        _plan_stat(2, "⏸",  "Avg Gap",    "dash_plan_gap")
        self._refresh_dash_plan_summary()

        # Buttons
        self.dash_start_btn = ctk.CTkButton(
            control_panel, text="▶  START SESSION", command=self.start_dashboard_session,
            height=44, font=FONT_TITLE,
            fg_color=SUCCESS_HOVER, hover_color=SUCCESS, corner_radius=CORNER_RADIUS
        )
        self.dash_start_btn.pack(fill="x", padx=PAD_SECTION, pady=(0, 6))

        btn_row = ctk.CTkFrame(control_panel, fg_color="transparent")
        btn_row.pack(fill="x", padx=PAD_SECTION, pady=(0, 6))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.dash_pause_btn = ctk.CTkButton(
            btn_row, text="⏸  PAUSE", command=lambda: self.toggle_pause(),
            **BUTTON_WARNING, state="disabled"
        )
        self.dash_pause_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.dash_stop_btn = ctk.CTkButton(
            btn_row, text="■  STOP", command=self.stop_bot,
            **BUTTON_DANGER, state="disabled"
        )
        self.dash_stop_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        # --- RUNTIME RISK PROFILE HUD ---
        risk_hud_frame = ctk.CTkFrame(control_panel, fg_color="#0f172a", corner_radius=8)
        risk_hud_frame.pack(fill="x", padx=PAD_SECTION, pady=(6, 0))

        ctk.CTkLabel(
            risk_hud_frame, text="⚡  Risk Profile", font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#94a3b8"
        ).pack(anchor="w", padx=10, pady=(6, 2))

        risk_btn_row = ctk.CTkFrame(risk_hud_frame, fg_color="transparent")
        risk_btn_row.pack(fill="x", padx=8, pady=(0, 6))

        self._risk_hud_btns = {}
        _risk_profiles = [
            ("Bundle",  "#4a5568", "#718096"),
            ("Cons.",   "#1d4ed8", "#2563eb"),
            ("Bal.",    "#166534", "#16a34a"),
            ("Aggr.",   "#991b1b", "#dc2626"),
            ("Smart",   "#5b21b6", "#7c3aed"),
        ]
        for col, (name, fg, hover) in enumerate(_risk_profiles):
            risk_btn_row.columnconfigure(col, weight=1)
            b = ctk.CTkButton(
                risk_btn_row, text=name,
                height=26, font=ctk.CTkFont(size=9, weight="bold"),
                fg_color=fg, hover_color=hover, corner_radius=6,
                command=lambda n=name: self._runtime_switch_risk(n)
            )
            b.grid(row=0, column=col, sticky="ew", padx=2)
            self._risk_hud_btns[name] = b

        self._risk_hud_active_label = ctk.CTkLabel(
            risk_hud_frame, text="Active: Bundle  |  Bet: --  |  Stop: --",
            font=ctk.CTkFont(size=9), text_color="#64748b"
        )
        self._risk_hud_active_label.pack(anchor="w", padx=10, pady=(0, 6))

        # Highlight the default active profile button
        self._risk_hud_highlight("Bundle")
        # -----------------------------------

        # Spacer
        ctk.CTkFrame(control_panel, fg_color="transparent", height=PAD_ITEM).pack()

        # Overlay Toggle
        overlay_row = ctk.CTkFrame(control_panel, fg_color="transparent")
        overlay_row.pack(fill="x", padx=PAD_SECTION, pady=(0, 8))
        if not hasattr(self, 'show_hud_var'):
            self.show_hud_var = ctk.BooleanVar(value=True)
        self.dash_overlay_toggle = ctk.CTkSwitch(
            overlay_row, text="Live Overlay", variable=self.show_hud_var,
            command=self.toggle_hud, font=FONT_SMALL,
            progress_color=GOLD, button_color=GOLD
        )
        self.dash_overlay_toggle.pack(side="left")

        # --- RECENT ACTIVITY FEED (Bottom) ---
        activity_section = ctk.CTkFrame(dash_frame, **CARD_STYLE)
        activity_section.pack(fill="x", padx=PAD_SECTION, pady=(PAD_ITEM, PAD_SECTION))

        activity_header = ctk.CTkFrame(activity_section, fg_color="transparent")
        activity_header.pack(fill="x", padx=PAD_SECTION, pady=(PAD_GROUP, PAD_ITEM))
        ctk.CTkLabel(activity_header, text="📋  Recent Activity", font=FONT_HEADING, text_color=GOLD).pack(side="left")

        self.dash_activity_list = ctk.CTkTextbox(
            activity_section, height=100, font=FONT_MONO_SMALL,
            fg_color=BG_DARK, text_color=TEXT_LIGHT, corner_radius=CORNER_SMALL,
        )
        self.dash_activity_list.pack(fill="x", padx=15, pady=(0, 12))
        # Seed with welcome message
        self.dash_activity_list.insert("end", "  Welcome to SpinEdge. Select a bundle and start a session.\n")
        self.dash_activity_list.configure(state="disabled")

        # --- ROUND AUDIT MINI (Bottom of Dashboard) ---
        # Compact list of recent rounds, click any row → chip-placement
        # dialog with full metadata. Lets users audit what happened in a
        # session without leaving the Dashboard.
        try:
            from gui.round_audit import RoundAuditMini
            self._dashboard_audit_mini = RoundAuditMini(dash_frame, app=self)
            self._dashboard_audit_mini.pack(fill="x", padx=PAD_SECTION, pady=(0, PAD_SECTION))
            print("[RoundAudit] Dashboard mini card initialised.")
        except Exception as _audit_mini_exc:
            import traceback
            print(f"[RoundAudit] Dashboard mini init failed: {_audit_mini_exc}")
            traceback.print_exc()

        self.root.bind("<Control-Shift-Tab>", lambda e: self.cycle_tab(-1))
        
        # Store tab order for cycling
        self.tab_names = ["Dashboard", "Bot Control", "Auto Roulette", "Statistics", "Region/Coordinate Setup", 
                         "Strategy Builder", "Activity Log", "Winning Numbers", "OCR Settings", "Settings"]

        # Main content frame (Bot Control)
        main_content_frame = get_frame(self.nav_panels["🎯 Operations"], "Bot Control")
        
        # Auto Roulette tab
        auto_roulette_tab_frame = get_frame(self.nav_panels["🎯 Operations"], "Auto Roulette")
        
        # Backtesting tab
        backtesting_tab_frame = get_frame(self.nav_panels["🧠 Strategy Lab"], "Backtesting")

        backtesting_tab_frame.grid_columnconfigure(0, weight=1)
        backtesting_tab_frame.grid_rowconfigure(0, weight=1)
        self.backtesting_gui = BacktestingGUI(backtesting_tab_frame, app=self)
        
        # Auto Roulette content
        auto_roulette_content_frame = ctk.CTkFrame(auto_roulette_tab_frame)
        auto_roulette_content_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Grid Layout: 2 Columns
        auto_roulette_content_frame.columnconfigure(0, weight=1) # Left: Controls
        auto_roulette_content_frame.columnconfigure(1, weight=3) # Right: Visualizer & KPIs
        auto_roulette_content_frame.rowconfigure(0, weight=1)

        # --- LEFT COLUMN: CONTROLS & LOGIC ---
        left_col = ctk.CTkFrame(auto_roulette_content_frame, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=0)
        
        # Strategy & Config Panel
        control_panel = ctk.CTkFrame(left_col)
        control_panel.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(control_panel, text="Strategy Control", font=("Segoe UI", 14, "bold"), text_color="#E6C200").pack(anchor="w", padx=10, pady=(10, 5))
        
        # Strategy Selection
        ctk.CTkLabel(control_panel, text="Strategy Algorithm:").pack(anchor="w", padx=10)
        self.auto_roulette_strategy_var = tk.StringVar(value="dynamic_9street")
        _ar_strats = self.get_all_strategy_names()
        self._auto_roulette_strategy_master_list = list(_ar_strats)
        self.auto_roulette_strategy_dropdown = ctk.CTkComboBox(
            control_panel, variable=self.auto_roulette_strategy_var,
            values=_ar_strats, state="normal",
        )
        self.auto_roulette_strategy_dropdown.pack(fill="x", padx=10, pady=(0, 10))
        # Type-to-filter so long custom-strategy lists are navigable.
        self._make_combobox_searchable(
            self.auto_roulette_strategy_dropdown,
            "_auto_roulette_strategy_master_list",
        )

        # Parameters
        ctk.CTkLabel(control_panel, text="K-Value (Pattern Depth):").pack(anchor="w", padx=10)
        self.auto_roulette_k_var = tk.StringVar(value="2")
        self.auto_roulette_k_entry = ctk.CTkEntry(control_panel, textvariable=self.auto_roulette_k_var, width=80)
        self.auto_roulette_k_entry.pack(anchor="w", padx=10, pady=(0, 10))

        ctk.CTkLabel(control_panel, text="Progression Logic:").pack(anchor="w", padx=10)
        self.auto_roulette_progression_var = tk.StringVar(value="custom")
        self.auto_roulette_progression_dropdown = ctk.CTkComboBox(
            control_panel, variable=self.auto_roulette_progression_var,
            values=["custom", "martingale", "flat", "fibonacci", "dalembert"], state="readonly"
        )
        self.auto_roulette_progression_dropdown.pack(fill="x", padx=10, pady=(0, 15))
        
        # Main Actions
        self.start_auto_roulette_btn = ctk.CTkButton(control_panel, text="▶ START BOT", command=self.start_auto_roulette, 
                                                   height=40, font=("Segoe UI", 12, "bold"), fg_color="#2ecc71", hover_color="#27ae60")
        self.start_auto_roulette_btn.pack(fill="x", padx=10, pady=(0, 5))
        
        self.stop_auto_roulette_btn = ctk.CTkButton(control_panel, text="⏹ STOP BOT", command=self.stop_auto_roulette, 
                                                  height=40, font=("Segoe UI", 12, "bold"), fg_color="gray", state="disabled")
        self.stop_auto_roulette_btn.pack(fill="x", padx=10, pady=(0, 15))

        # Status Panel
        status_panel = ctk.CTkFrame(left_col)
        status_panel.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(status_panel, text="System Status", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        self.auto_roulette_status_var = tk.StringVar(value="READY")
        self.auto_roulette_status_label = ctk.CTkLabel(status_panel, textvariable=self.auto_roulette_status_var, font=("Consolas", 11))
        self.auto_roulette_status_label.pack(anchor="w", padx=10, pady=(0, 10))

        # Debug Panel
        debug_panel = ctk.CTkFrame(left_col)
        debug_panel.pack(fill="x", pady=(0, 0))
        ctk.CTkLabel(debug_panel, text="Debug Tools", font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        debug_input_row = ctk.CTkFrame(debug_panel, fg_color="transparent")
        debug_input_row.pack(fill="x", padx=5, pady=5)
        self.debug_number_var = tk.StringVar()
        ctk.CTkEntry(debug_input_row, textvariable=self.debug_number_var, width=60, placeholder_text="#").pack(side="left", padx=2)
        ctk.CTkButton(debug_input_row, text="Manual Add", command=self.debug_add_number_to_strategy, width=80).pack(side="left", padx=2)

        # --- RIGHT COLUMN: VISUALIZER & KPIs ---
        right_col = ctk.CTkFrame(auto_roulette_content_frame, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=0)
        right_col.rowconfigure(1, weight=1) # Visualizer gets space

        # 1. KPI Cards Row
        kpi_row = ctk.CTkFrame(right_col, fg_color="transparent")
        kpi_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        kpi_row.columnconfigure((0,1,2), weight=1)
        
        # Helper to make cards
        def create_kpi(parent, title, var_name, col, color="#E6C200"):
            card = ctk.CTkFrame(parent)
            card.grid(row=0, column=col, sticky="ew", padx=3)
            ctk.CTkLabel(card, text=title, font=("Arial", 10, "bold"), text_color="gray70").pack(anchor="w", padx=10, pady=(5,0))
            lbl = ctk.CTkLabel(card, text="--", font=("Segoe UI", 20, "bold"), text_color=color)
            lbl.pack(anchor="w", padx=10, pady=(0, 5))
            setattr(self, var_name, lbl) # Save ref
            return card

        create_kpi(kpi_row, "CURRENT BALANCE", "kpi_balance_label", 0, "#2ecc71")
        create_kpi(kpi_row, "SESSION PROFIT", "kpi_profit_label", 1, "#f1c40f")
        create_kpi(kpi_row, "WIN RATE", "kpi_winrate_label", 2, "#3498db")

        # 2. Live Visualizer (Roulette Board)
        visualizer_frame = ctk.CTkFrame(right_col)
        visualizer_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        
        ctk.CTkLabel(visualizer_frame, text="LIVE TABLE MONITOR", font=("Segoe UI", 12, "bold"), text_color="gray50").pack(pady=5)
        
        # Instantiate separate monitor board
        self.roulette_board_monitor = RouletteBoard(visualizer_frame, width=700, height=250)
        self.roulette_board_monitor.pack(fill="both", expand=True, padx=10, pady=10)

        # 3. Activity Stream (Mini Log)
        activity_frame = ctk.CTkFrame(right_col, height=150)
        activity_frame.grid(row=2, column=0, sticky="ew")
        ctk.CTkLabel(activity_frame, text="Recent Activity", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        self.activity_stream_list = tk.Listbox(activity_frame, height=6, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.activity_stream_list.pack(fill="x", padx=10, pady=5)
        
        # Method to update KPI cards (to be called by update_stats loop)
        self.kpi_vars = {
            'balance': 0.0,
            'profit': 0.0,
            'wins': 0,
            'rounds': 0
        }
        
        # Statistics frame
        stats_tab_frame = get_frame(self.nav_panels["🧠 Strategy Lab"], "Statistics")
        
        # Statistics content
        stats_content_frame = ctk.CTkFrame(stats_tab_frame)
        stats_content_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Statistics Sidebar (Left) vs Content (Right) or Just Vertical Stack? 
        # Let's do a Grid: Top Row = Cards/Graph, Bottom Row = History
        stats_content_frame.columnconfigure(0, weight=1)
        stats_content_frame.columnconfigure(1, weight=2)
        stats_content_frame.rowconfigure(0, weight=1) # Graph area
        stats_content_frame.rowconfigure(1, weight=1) # History area
        
        # --- TOP LEFT: AGGREGATE STATS ---
        aggregate_frame = ctk.CTkFrame(stats_content_frame)
        aggregate_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        ctk.CTkLabel(aggregate_frame, text="LIFETIME STATISTICS", font=("Segoe UI", 12, "bold"), text_color="gray70").pack(anchor="w", padx=10, pady=10)
        
        agg_inner = ctk.CTkFrame(aggregate_frame, fg_color="transparent")
        agg_inner.pack(fill="both", expand=True, padx=10)
        
        def create_stat_row(parent, label_text, var_attr_name, row):
            ctk.CTkLabel(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=5)
            lbl = ctk.CTkLabel(parent, text="--", font=("Consolas", 12, "bold"))
            lbl.grid(row=row, column=1, sticky="e", pady=5, padx=10)
            setattr(self, var_attr_name, lbl)
            
        create_stat_row(agg_inner, "Total Sessions:", "total_sessions_label", 0)
        create_stat_row(agg_inner, "Total Rounds:", "total_rounds_label", 1)
        create_stat_row(agg_inner, "Total Wins:", "total_wins_agg_label", 2)
        create_stat_row(agg_inner, "Total Losses:", "total_losses_agg_label", 3)
        create_stat_row(agg_inner, "Win Rate:", "overall_win_rate_label", 4)
        create_stat_row(agg_inner, "Net Profit:", "total_profit_label", 5)
        
        ctk.CTkButton(aggregate_frame, text="REFRESH DATA", command=self.refresh_aggregate_stats, fg_color="gray20", hover_color="gray30").pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkButton(aggregate_frame, text="RESET ALL STATS", command=self.confirm_clear_stats, fg_color="#c0392b", hover_color="#e74c3c").pack(fill="x", padx=10, pady=(5, 10))
        
        # --- TOP RIGHT: BANKROLL TREND GRAPH ---
        graph_frame = ctk.CTkFrame(stats_content_frame)
        graph_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        
        ctk.CTkLabel(graph_frame, text="BANKROLL TREND", font=("Segoe UI", 12, "bold"), text_color="gray70").pack(anchor="w", padx=10, pady=5)
        
        # Placeholder for graph (canvas)
        self.stats_figure = plt.Figure(figsize=(5, 3), dpi=100)
        self.stats_figure.patch.set_facecolor('#2b2b2b') # Dark bg
        self.stats_ax = self.stats_figure.add_subplot(111)
        self.stats_ax.set_facecolor('#2b2b2b')
        
        # Initial empty plot styling
        self.stats_ax.tick_params(colors='white')
        self.stats_ax.xaxis.label.set_color('white')
        self.stats_ax.yaxis.label.set_color('white')
        for spine in self.stats_ax.spines.values():
            spine.set_edgecolor('gray')

        self.stats_canvas = FigureCanvasTkAgg(self.stats_figure, master=graph_frame)
        self.stats_canvas.draw()
        self.stats_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)
        
        # --- BOTTOM: SESSION HISTORY TABLE ---
        history_frame = ctk.CTkFrame(stats_content_frame)
        history_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        
        ctk.CTkLabel(history_frame, text="RECENT SESSIONS", font=("Segoe UI", 12, "bold"), text_color="gray70").pack(anchor="w", padx=10, pady=(10, 5))
        
        # Create Scrollable Frame for Table
        self.history_table_frame = ctk.CTkScrollableFrame(history_frame)
        self.history_table_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Headers
        headers_frame = ctk.CTkFrame(self.history_table_frame, height=30, fg_color="gray20")
        headers_frame.pack(fill="x")
        cols = ["Date", "Start Bal", "End Bal", "Profit", "Rounds"]
        weights = [2, 1, 1, 1, 1]
        for i, col in enumerate(cols):
             lbl = ctk.CTkLabel(headers_frame, text=col, font=("Arial", 11, "bold"))
             lbl.pack(side="left", expand=True, fill="x")
        
        # We need a method to populate this later: populate_session_history()
        
        # Initial statistics load
        self.refresh_aggregate_stats()
        
        # NEW: Region/Coordinate Setup tab
        setup_tab_frame = get_frame(self.nav_panels["⚙️ Settings & Setup"], "Region/Coordinate Setup")

        # Configuration IO
        config_io_frame = ctk.CTkFrame(setup_tab_frame)
        config_io_frame.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(config_io_frame, text="Full Configuration", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        io_btns = ctk.CTkFrame(config_io_frame, fg_color="transparent")
        io_btns.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkButton(io_btns, text="📥 Import Config", command=self.import_config, fg_color="#2980b9").pack(side="left", padx=5)
        ctk.CTkButton(io_btns, text="📤 Export Setup", command=self.export_public_config, fg_color="#8e44ad").pack(side="left", padx=5)

        # --- PASSIVE RECORDING TOGGLE ---
        passive_frame = ctk.CTkFrame(setup_tab_frame)
        passive_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        self.passive_recording_var = tk.BooleanVar(value=False)
        passive_cb = ctk.CTkCheckBox(passive_frame, text="Enable Passive History Recording (Save numbers even when not betting)", 
                                     variable=self.passive_recording_var, font=("Arial", 11, "bold"))
        passive_cb.pack(anchor="w", padx=10, pady=10)
        ToolTip(passive_cb, "If enabled, the bot will save every number it sees to the database, building a history for backtesting.")

        # NEW: Strategy Builder tab
        strategy_builder_tab_frame = get_frame(self.nav_panels["🧠 Strategy Lab"], "Strategy Builder")

        # NEW: Activity Log tab
        activity_log_tab_frame = get_frame(self.nav_panels["🎯 Operations"], "Activity Log")
        
        # Grid layout for Log Tab
        activity_log_tab_frame.rowconfigure(1, weight=1)
        activity_log_tab_frame.columnconfigure(0, weight=1)
        
        # 1. Controls Toolbar
        log_controls_frame = ctk.CTkFrame(activity_log_tab_frame, fg_color="transparent")
        log_controls_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        
        ctk.CTkLabel(log_controls_frame, text="Filter:", font=("Arial", 12)).pack(side="left", padx=(0, 5))
        self.log_filter_var = tk.StringVar()
        self.log_filter_var.trace("w", self.filter_activity_log)
        self.log_filter_entry = ctk.CTkEntry(log_controls_frame, textvariable=self.log_filter_var, placeholder_text="Type to filter...")
        self.log_filter_entry.pack(side="left", padx=5, fill="x", expand=True)
        
        ctk.CTkButton(log_controls_frame, text="Clear Log", command=self.clear_activity_log, width=80, fg_color="#c0392b", hover_color="#e74c3c").pack(side="right", padx=5)
        ctk.CTkButton(log_controls_frame, text="Save Log", command=self.save_activity_log_to_file, width=80).pack(side="right", padx=5)

        # 2. Rich Text Console
        self.activity_log = ctk.CTkTextbox(activity_log_tab_frame, font=("Consolas", 11), state="disabled")
        self.activity_log.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        
        # Tags for coloring (CTkTextbox supports basic tags but full implementation might vary, 
        # standard Tkinter Text does. CTkTextbox wraps standard Text.
        # We'll access the underlying text widget for tagging if needed, or just insertion)
        # CTkTextbox.tag_config method exists in newer versions? Or direct access:
        # self.activity_log._textbox.tag_config("WIN", foreground="#2ecc71")
        # self.activity_log._textbox.tag_config("LOSS", foreground="#e74c3c")
        # self.activity_log._textbox.tag_config("ERROR", foreground="#e67e22")
        self.activity_log._textbox.tag_config("WIN", foreground="#2ecc71")
        self.activity_log._textbox.tag_config("LOSS", foreground="#e74c3c")
        self.activity_log._textbox.tag_config("ERROR", foreground="#e67e22")
        self.activity_log._textbox.tag_config("INFO", foreground="#bdc3c7")

        # NEW: Round Audit tab — chip-placement playback + full per-round metadata
        try:
            from gui.round_audit import RoundHistoryView
            audit_tab_frame = get_frame(self.nav_panels["🎯 Operations"], "Round Audit")
            audit_tab_frame.rowconfigure(0, weight=1)
            audit_tab_frame.columnconfigure(0, weight=1)
            self._round_audit_view = RoundHistoryView(audit_tab_frame, app=self)
            self._round_audit_view.grid(row=0, column=0, sticky="nsew")
            print("[RoundAudit] Tab initialised.")
        except Exception as _audit_init_exc:
            # Surface the failure with traceback so the next round of debugging
            # has something to look at, instead of a silent missing tab.
            import traceback
            print(f"[RoundAudit] Tab init failed: {_audit_init_exc}")
            traceback.print_exc()

        # NEW: Winning Numbers tab
        winning_numbers_tab_frame = get_frame(self.nav_panels["🎯 Operations"], "Winning Numbers")
        winning_numbers_tab_frame.rowconfigure(2, weight=1) # Table expands
        winning_numbers_tab_frame.columnconfigure(0, weight=1)
        
        # 1. Visual Ticker (Top)
        ticker_frame_outer = ctk.CTkFrame(winning_numbers_tab_frame, height=80)
        ticker_frame_outer.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(ticker_frame_outer, text="RECENT HISTORY", font=("Arial", 10, "bold"), text_color="gray70").pack(anchor="w", padx=10, pady=(5,0))
        
        self.ticker_canvas = ctk.CTkScrollableFrame(ticker_frame_outer, height=50, orientation="horizontal")
        self.ticker_canvas.pack(fill="x", padx=5, pady=5)
        # self.ticker_items_frame will be inside scrollable frame automatically
        
        # 2. Analysis Grid (Middle)
        analysis_frame = ctk.CTkFrame(winning_numbers_tab_frame)
        analysis_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        analysis_frame.columnconfigure(0, weight=1)
        analysis_frame.columnconfigure(1, weight=1)
        analysis_frame.columnconfigure(2, weight=1)
        
        # A. Hot/Cold
        hot_cold_frame = ctk.CTkFrame(analysis_frame)
        hot_cold_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(hot_cold_frame, text="HOT / COLD", font=("Arial", 12, "bold")).pack(pady=5)
        self.hot_numbers_label = ctk.CTkLabel(hot_cold_frame, text="Hot: --", text_color="#2ecc71")
        self.hot_numbers_label.pack()
        self.cold_numbers_label = ctk.CTkLabel(hot_cold_frame, text="Cold: --", text_color="#3498db")
        self.cold_numbers_label.pack()

        # B. Sectors
        sector_frame = ctk.CTkFrame(analysis_frame)
        sector_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(sector_frame, text="SECTOR ANALYSIS", font=("Arial", 12, "bold")).pack(pady=5)
        self.sector_stats_label = ctk.CTkLabel(sector_frame, text="Voi: --%\nTie: --%\nOrp: --%", justify="left")
        self.sector_stats_label.pack()
        
        # C. Patterns/Streaks
        streak_frame = ctk.CTkFrame(analysis_frame)
        streak_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(streak_frame, text="ACTIVE STREAKS", font=("Arial", 12, "bold")).pack(pady=5)
        self.streak_label = ctk.CTkLabel(streak_frame, text="None")
        self.streak_label.pack()
        
        # 3. Frequency Table (Bottom)
        table_container = ctk.CTkFrame(winning_numbers_tab_frame)
        table_container.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        ctk.CTkLabel(table_container, text="NUMBER FREQUENCY (Last 100)", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=5)
        
        # Headers
        freq_header = ctk.CTkFrame(table_container, height=30, fg_color="gray20")
        freq_header.pack(fill="x", padx=2)
        for t, w in [("Number", 1), ("Hits", 1), ("Percentage", 1), ("Last Seen", 1)]:
             l = ctk.CTkLabel(freq_header, text=t, font=("Arial", 11, "bold"))
             l.pack(side="left", expand=True, fill="x")

        self.frequency_table = ctk.CTkScrollableFrame(table_container)
        self.frequency_table.pack(fill="both", expand=True, padx=2, pady=2)
        
        # Refresh Button
        ctk.CTkButton(winning_numbers_tab_frame, text="REFRESH ANALYSIS", command=self.refresh_winning_numbers_tab).grid(row=3, column=0, pady=10)

        # NEW: OCR Settings tab
        ocr_settings_tab_frame = get_frame(self.nav_panels["⚙️ Settings & Setup"], "OCR Settings")

        # OCR Settings Content
        ocr_config_frame = ctk.CTkFrame(ocr_settings_tab_frame)
        ocr_config_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(ocr_config_frame, text="Tesseract Settings", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        ocr_config_inner = ctk.CTkFrame(ocr_config_frame, fg_color="transparent")
        ocr_config_inner.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(ocr_config_inner, text="Tesseract Path:").grid(row=0, column=0, sticky="w", pady=5)
        # Use existing variable or new one? Keeping existing.
        self.tesseract_path_var = tk.StringVar(value=self.config.get("tesseract_path", ""))
        self.tesseract_path_entry = ctk.CTkEntry(ocr_config_inner, textvariable=self.tesseract_path_var)
        self.tesseract_path_entry.grid(row=0, column=1, padx=5, sticky="ew")
        
        browse_btn = ctk.CTkButton(ocr_config_inner, text="Browse...", command=self.browse_tesseract_path, width=100)
        browse_btn.grid(row=0, column=2, padx=5)
        
        save_ocr_btn = ctk.CTkButton(ocr_config_inner, text="Save & Initialize", command=self.save_tesseract_path)
        save_ocr_btn.grid(row=1, column=1, pady=10, sticky="e")
        
        ocr_config_inner.columnconfigure(1, weight=1)

        # --- Move region/coordinate widgets to setup_tab_frame ---
        
        # 1. PRESET SECTION (NEW)
        preset_frame = ctk.CTkFrame(setup_tab_frame)
        preset_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        ctk.CTkLabel(preset_frame, text="Quick Setup (Presets)", font=("Arial", 12, "bold"), text_color="#E6C200").pack(anchor="w", padx=10, pady=(5,0))
        
        preset_controls = ctk.CTkFrame(preset_frame, fg_color="transparent")
        preset_controls.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(preset_controls, text="Select Casino:").pack(side="left", padx=(0,5))
        
        self.preset_var = tk.StringVar(value=get_preset_names()[0] if get_preset_names() else "")
        self.preset_dropdown = ctk.CTkComboBox(preset_controls, variable=self.preset_var, values=get_preset_names())
        self.preset_dropdown.pack(side="left", padx=5, fill="x", expand=True)
        
        
        ctk.CTkButton(preset_controls, text="Apply Preset", command=self.apply_preset_gui, width=120, fg_color="#2980b9").pack(side="left", padx=10)
        ctk.CTkButton(preset_controls, text="Auto-Detect Table", command=self.auto_detect_table, width=140, fg_color="#27ae60", hover_color="#2ecc71").pack(side="left", padx=10)
        ctk.CTkButton(preset_controls, text="Save Current as Preset", command=self.save_current_as_preset, width=150, fg_color="#8e44ad").pack(side="left", padx=10)
        
        # Display recorded regions/coordinates (replacing Canvas with scrollable frame)

        display_frame = ctk.CTkFrame(setup_tab_frame)
        display_frame.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(display_frame, text="Recorded Regions & Coordinates", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))


        # Undo and Clear All buttons
        undo_clear_frame = ctk.CTkFrame(display_frame, fg_color="transparent")
        undo_clear_frame.pack(fill="x", pady=(0, 5), padx=10)
        
        undo_btn = ctk.CTkButton(undo_clear_frame, text="Undo", command=self.undo_last_coord_action, width=80)
        undo_btn.pack(side="left", padx=(0, 5))
        ToolTip(undo_btn, "Undo the last add/edit/delete action.")
        
        clear_btn = ctk.CTkButton(undo_clear_frame, text="Clear All", command=self.clear_all_coordinates, width=80, fg_color="#c0392b", hover_color="#e74c3c")
        clear_btn.pack(side="left")
        ToolTip(clear_btn, "Remove all regions and coordinates (with confirmation).")
        
        # Scrollable list for coordinates
        self.coord_list_canvas = ctk.CTkScrollableFrame(display_frame, height=200) # Reusing name 'canvas' to minimize breakage if other methods access it, or better: rename and alias
        self.coord_list_canvas.pack(fill="both", expand=True, padx=10, pady=5)
        self.coord_list_inner = self.coord_list_scroll = self.coord_list_canvas # alias for compatibility
        
        self.update_coord_list_display()
        ToolTip(display_frame, "List of all recorded regions and coordinates. Hover to highlight, edit, or delete.")

        # Coordinate Recording Section
        coord_label_frame = ctk.CTkFrame(setup_tab_frame)
        coord_label_frame.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(coord_label_frame, text="Coordinate Recording", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        coord_inner = ctk.CTkFrame(coord_label_frame, fg_color="transparent")
        coord_inner.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(coord_inner, text="Bet Type:").grid(row=0, column=0, sticky="w", pady=2)
        self.bet_type_var = tk.StringVar()
        self.bet_type_button = ctk.CTkButton(coord_inner, text=VALID_BET_TYPES[0], command=self.open_bet_type_dialog, width=200)
        self.bet_type_button.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=2)
        self.bet_type_var.set(VALID_BET_TYPES[0])
        ToolTip(self.bet_type_button, "Select the bet type for coordinate recording.")
        
        self.record_coord_btn = ctk.CTkButton(coord_inner, text="Record Coordinate", command=self.record_coordinate)
        self.record_coord_btn.grid(row=0, column=2, padx=(10, 0), pady=2)
        ToolTip(self.record_coord_btn, "Click, then move mouse to the target and press F8 to record coordinate.")
        
        ctk.CTkLabel(coord_inner, text="(Press F8)", text_color="gray").grid(row=0, column=3, padx=(8, 0), pady=2)

        # Region Recording Section
        from core.strategy_engine import CHIP_DENOMINATIONS
        region_labels = ["balance*", "table_state*"] + list(getattr(self, 'custom_regions', []))
        
        region_frame = ctk.CTkFrame(setup_tab_frame)
        region_frame.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(region_frame, text="Region Recording", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        region_inner = ctk.CTkFrame(region_frame, fg_color="transparent")
        region_inner.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(region_inner, text="Region Label:").grid(row=0, column=0, sticky="w", pady=2)
        self.region_label_var = tk.StringVar()
        self.region_label_dropdown = ctk.CTkComboBox(region_inner, variable=self.region_label_var, values=region_labels, state="readonly", width=200)
        self.region_label_dropdown.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=2)
        self.region_label_dropdown.set(region_labels[0])
        ToolTip(self.region_label_dropdown, "Select the region label to record.")
        
        self.record_region_btn = ctk.CTkButton(region_inner, text="Record Region", command=self.record_region)
        self.record_region_btn.grid(row=0, column=2, padx=(10, 0), pady=2)
        ToolTip(self.record_region_btn, "Click, then move mouse to TOP-LEFT and press F8, then BOTTOM-RIGHT and press F9.")
        
        ctk.CTkLabel(region_inner, text="(F8: Top-Left, F9: Bottom-Right)", text_color="gray").grid(row=0, column=3, padx=(8, 0), pady=2)
        
        # Entry and button to add custom region label
        ctk.CTkLabel(region_inner, text="Add Custom Region:").grid(row=1, column=0, sticky="w", pady=2)
        self.custom_region_entry = ctk.CTkEntry(region_inner, width=150)
        self.custom_region_entry.grid(row=1, column=1, padx=(10, 0), pady=2)
        ToolTip(self.custom_region_entry, "Enter a custom label for a new region.")
        
        self.add_custom_region_btn = ctk.CTkButton(region_inner, text="Add", command=self.add_custom_region, width=60)
        self.add_custom_region_btn.grid(row=1, column=2, padx=(5, 0), pady=2)
        ToolTip(self.add_custom_region_btn, "Add the custom region label to the dropdown.")

        # --- Move Strategy Builder to its own tab ---
        # ===== STRATEGY BUILDER SECTION =====
        # ===== STRATEGY BUILDER SECTION =====
        strategy_frame = ctk.CTkFrame(strategy_builder_tab_frame, fg_color="transparent")
        strategy_frame.pack(fill="both", expand=True, padx=15, pady=15)
        # Main layout: Row 0 = Board, Row 1 = Split Columns
        strategy_frame.rowconfigure(1, weight=1)
        strategy_frame.columnconfigure(0, weight=1)

        # 1. TOP SECTION: VISUAL BOARD
        strategy_top_frame = ctk.CTkFrame(strategy_frame)
        strategy_top_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        strategy_top_frame.columnconfigure(0, weight=1)
        
        ctk.CTkLabel(strategy_top_frame, text="Region & Strategy Visualization", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        
        # Initialize Roulette Board (Full Width)
        self.roulette_board = RouletteBoard(strategy_top_frame, width=800, height=220)
        self.roulette_board.pack(fill="both", expand=True, padx=10, pady=5)
        self.roulette_board.set_click_callback(self._on_board_cell_click)
        self.roulette_board.set_unit_edit_callback(self._on_board_unit_edit)

        # 2. CONTENT SECTION: SPLIT COLUMNS
        strategy_content_frame = ctk.CTkFrame(strategy_frame, fg_color="transparent")
        strategy_content_frame.grid(row=1, column=0, sticky="nsew")
        strategy_content_frame.columnconfigure(0, weight=1) # Left (Builder)
        strategy_content_frame.columnconfigure(1, weight=1) # Right (Management)

        # === LEFT COLUMN: BUILDER ===
        builder_frame = ctk.CTkFrame(strategy_content_frame)
        builder_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        builder_frame.columnconfigure(0, weight=1)
        builder_frame.rowconfigure(2, weight=1) # Listbox expands

        ctk.CTkLabel(builder_frame, text="Strategy Builder Tools", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(5, 5), padx=10)

        # Search box
        search_frame = ctk.CTkFrame(builder_frame, fg_color="transparent")
        search_frame.grid(row=1, column=0, sticky="ew", pady=(0, 5), padx=5)
        search_frame.columnconfigure(1, weight=1)
        
        ctk.CTkLabel(search_frame, text="Search:", font=("Arial", 12)).pack(side="left", padx=(0, 5))
        self.label_search_var = tk.StringVar()
        self.label_search_entry = ctk.CTkEntry(search_frame, textvariable=self.label_search_var, width=120)
        self.label_search_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.label_search_entry.bind("<KeyRelease>", self.filter_label_selector)
        
        self.clear_search_btn = ctk.CTkButton(search_frame, text="Clear", command=self.clear_label_search, width=50)
        self.clear_search_btn.pack(side="left")

        self.clear_selections_btn = ctk.CTkButton(search_frame, text="Clear All", command=self.clear_all_label_selections, width=80, fg_color="#c0392b", hover_color="#e74c3c")
        self.clear_selections_btn.pack(side="right", padx=(5, 0))
        ToolTip(self.clear_selections_btn, "Clear all selected labels from the list")
        
        self.delete_label_btn = ctk.CTkButton(search_frame, text="🗑️ Del", command=self.delete_selected_label, width=60, fg_color="#c0392b", hover_color="#e74c3c")
        self.delete_label_btn.pack(side="right", padx=(5, 0))
        ToolTip(self.delete_label_btn, "Permanently delete selected label/coordinate from library")

        # Label Selector Grid Wrapper
        label_selector_frame = ctk.CTkFrame(builder_frame, fg_color="transparent")
        label_selector_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10), padx=5)
        label_selector_frame.columnconfigure(0, weight=1)
        label_selector_frame.rowconfigure(0, weight=1)

        # Styling Listbox
        self.label_selector = tk.Listbox(label_selector_frame, selectmode=tk.MULTIPLE, height=15, exportselection=False, 
                                        font=("Arial", 10), relief="flat", borderwidth=0,
                                        bg="#2b2b2b", fg="#dce4ee", selectbackground="#1f538d", highlightthickness=0)
        label_scrollbar = ttk.Scrollbar(label_selector_frame, orient="vertical", command=self.label_selector.yview)
        self.label_selector.configure(yscrollcommand=label_scrollbar.set)
        self.label_selector.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        label_scrollbar.grid(row=0, column=1, sticky="ns")
        
        # Mousewheel
        self.label_selector.bind("<MouseWheel>", self.on_mousewheel)
        self.label_selector.bind("<<ListboxSelect>>", self.on_label_selection_change) # Bind selection change

        # Strategy Input (Name & Units)
        strategy_input_frame = ctk.CTkFrame(builder_frame, fg_color="transparent")
        strategy_input_frame.grid(row=3, column=0, sticky="ew", pady=(0, 10), padx=5)
        strategy_input_frame.columnconfigure(1, weight=1)
        
        ctk.CTkLabel(strategy_input_frame, text="Name:").grid(row=0, column=0, sticky="w", pady=5)
        self.custom_strategy_var = tk.StringVar()
        strategy_entry = ctk.CTkEntry(strategy_input_frame, textvariable=self.custom_strategy_var)
        strategy_entry.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=5)

        # Bet Mode Selection (Static vs Dynamic Neighbors)
        ctk.CTkLabel(strategy_input_frame, text="Bet Mode:").grid(row=1, column=0, sticky="w", pady=5)
        self.bet_mode_var = tk.StringVar(value="Static")
        self.bet_mode_dropdown = ctk.CTkComboBox(
            strategy_input_frame, variable=self.bet_mode_var,
            values=["Static", "Neighbors", "Pattern Follower", "Composite"],
            state="readonly", width=160,
            command=self._on_bet_mode_change
        )
        self.bet_mode_dropdown.grid(row=1, column=1, sticky="w", padx=(5, 0), pady=5)

        # Neighbors config (hidden by default, shown when Neighbors mode selected)
        self.neighbors_config_frame = ctk.CTkFrame(strategy_input_frame, fg_color="transparent")
        self.neighbors_config_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self.neighbors_config_frame.grid_remove()

        # Row 1: Neighbors per side
        neighbors_row1 = ctk.CTkFrame(self.neighbors_config_frame, fg_color="transparent")
        neighbors_row1.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(neighbors_row1, text="Neighbors per side:").pack(side="left")
        self.neighbors_count_var = tk.IntVar(value=2)
        self.neighbors_count_entry = ctk.CTkEntry(
            neighbors_row1, textvariable=self.neighbors_count_var, width=50
        )
        self.neighbors_count_entry.pack(side="left", padx=5)
        ctk.CTkLabel(
            neighbors_row1,
            text="(e.g. 2 = 5 numbers per anchor)",
            font=("Arial", 10), text_color="#71717A"
        ).pack(side="left", padx=5)

        # Row 2: Anchor numbers (which past results to use)
        neighbors_row2 = ctk.CTkFrame(self.neighbors_config_frame, fg_color="transparent")
        neighbors_row2.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(neighbors_row2, text="Anchor numbers:").pack(side="left")
        self.neighbors_anchors_var = tk.StringVar(value="1")
        self.neighbors_anchors_entry = ctk.CTkEntry(
            neighbors_row2, textvariable=self.neighbors_anchors_var, width=100,
            placeholder_text="e.g. 1,3"
        )
        self.neighbors_anchors_entry.pack(side="left", padx=5)
        ctk.CTkLabel(
            neighbors_row2,
            text="(1=last, 2=2nd last, 1,3=last+3rd last)",
            font=("Arial", 10), text_color="#71717A"
        ).pack(side="left", padx=5)

        # Row 3: Hot / Cold numbers
        neighbors_row3 = ctk.CTkFrame(self.neighbors_config_frame, fg_color="transparent")
        neighbors_row3.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(neighbors_row3, text="Hot anchors:").pack(side="left")
        self.neighbors_hot_var = tk.IntVar(value=0)
        ctk.CTkEntry(
            neighbors_row3, textvariable=self.neighbors_hot_var, width=40
        ).pack(side="left", padx=(5, 10))
        ctk.CTkLabel(neighbors_row3, text="Cold anchors:").pack(side="left")
        self.neighbors_cold_var = tk.IntVar(value=0)
        ctk.CTkEntry(
            neighbors_row3, textvariable=self.neighbors_cold_var, width=40
        ).pack(side="left", padx=(5, 10))
        ctk.CTkLabel(
            neighbors_row3,
            text="(0=off, adds most/least frequent)",
            font=("Arial", 10), text_color="#71717A"
        ).pack(side="left")

        # Row 4: Lookback window for hot/cold
        neighbors_row4 = ctk.CTkFrame(self.neighbors_config_frame, fg_color="transparent")
        neighbors_row4.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(neighbors_row4, text="Lookback spins:").pack(side="left")
        self.neighbors_lookback_var = tk.IntVar(value=30)
        ctk.CTkEntry(
            neighbors_row4, textvariable=self.neighbors_lookback_var, width=50
        ).pack(side="left", padx=5)
        ctk.CTkLabel(
            neighbors_row4,
            text="(how many past spins for hot/cold analysis)",
            font=("Arial", 10), text_color="#71717A"
        ).pack(side="left", padx=5)

        # Pattern Follower config (hidden by default, shown when Pattern Follower mode selected)
        # Shares grid row 2 with neighbors_config_frame; only one is visible at a time.
        self.pattern_follower_frame = ctk.CTkFrame(strategy_input_frame, fg_color="transparent")
        self.pattern_follower_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self.pattern_follower_frame.grid_remove()
        self.pattern_follower_editor = PatternFollowerEditor(self.pattern_follower_frame)
        self.pattern_follower_editor.pack(fill="both", expand=True)

        # Composite config (hidden by default; shares row 2 with the others)
        self.composite_frame = ctk.CTkFrame(strategy_input_frame, fg_color="transparent")
        self.composite_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self.composite_frame.grid_remove()
        self.composite_editor = CompositeEditor(
            self.composite_frame,
            strategies_provider=lambda: list(self.custom_strategies.keys()),
        )
        self.composite_editor.pack(fill="both", expand=True)

        # Custom Bet Units Checkbox
        bet_amounts_frame = ctk.CTkFrame(builder_frame, fg_color="transparent")
        bet_amounts_frame.grid(row=4, column=0, sticky="ew", padx=5)
        
        self.enable_custom_bet_units_var = tk.BooleanVar(value=False)
        self.enable_custom_bet_units_cb = ctk.CTkCheckBox(
            bet_amounts_frame,
            text="Enable Custom Units",
            variable=self.enable_custom_bet_units_var,
            command=self.toggle_custom_bet_units
        )
        self.enable_custom_bet_units_cb.pack(anchor="w", pady=2)

        self.default_units_frame = ctk.CTkFrame(bet_amounts_frame, fg_color="transparent")
        self.default_units_frame.pack(fill="x", pady=2)
        ctk.CTkLabel(self.default_units_frame, text="Default Units:").pack(side="left")
        self.default_units_var = tk.IntVar(value=1)
        self.default_units_entry = ctk.CTkEntry(self.default_units_frame, textvariable=self.default_units_var, width=50)
        self.default_units_entry.pack(side="left", padx=5)
        ctk.CTkLabel(self.default_units_frame, text="(multiplier of base bet per label)",
                     font=("Arial", 10), text_color="#71717A").pack(side="left", padx=5)

        # Custom Units List (Hidden by default, shown when checkbox enabled)
        self.custom_bet_units_frame = ctk.CTkFrame(builder_frame)
        self.custom_bet_units_frame.grid(row=5, column=0, sticky="nsew", padx=5, pady=5)
        self.custom_bet_units_frame.grid_remove()
        self.bet_units_scroll = ctk.CTkScrollableFrame(self.custom_bet_units_frame, height=100)
        self.bet_units_scroll.pack(fill="both", expand=True, padx=5, pady=5)
        self.bet_units_inner_frame = self.bet_units_scroll
        self.bet_unit_entries = {}

        # Add Button
        self.add_to_strategy_btn = ctk.CTkButton(builder_frame, text="💾 Add / Updates Strategy", 
                                             command=self.add_label_to_strategy, fg_color="#27ae60", hover_color="#2ecc71")
        self.add_to_strategy_btn.grid(row=6, column=0, sticky="ew", padx=10, pady=10)
        
        # Initial Population of Label Selector
        self.filter_label_selector()


        # === RIGHT COLUMN: MANAGEMENT ===
        management_frame = ctk.CTkFrame(strategy_content_frame)
        management_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        management_frame.columnconfigure(0, weight=1)
        management_frame.rowconfigure(2, weight=1) # Details text expands

        ctk.CTkLabel(management_frame, text="Strategy Library & Preview", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(5, 5), padx=10)

        # Library Controls
        lib_controls = ctk.CTkFrame(management_frame, fg_color="transparent")
        lib_controls.grid(row=1, column=0, sticky="ew", padx=5)
        lib_controls.columnconfigure(0, weight=1)

        self.strategy_selector_var = tk.StringVar()
        self.strategy_selector = ctk.CTkComboBox(lib_controls, variable=self.strategy_selector_var, state="readonly")
        self.strategy_selector.grid(row=0, column=0, columnspan=2, sticky="ew", pady=5)
        self.strategy_selector.configure(command=lambda val: self.on_strategy_selected(val))

        lib_controls.columnconfigure(1, weight=1)
        lib_controls.columnconfigure(2, weight=0)

        edit_in_builder_btn = ctk.CTkButton(lib_controls, text="Edit in Builder", command=self.load_strategy_into_builder, width=100, fg_color="#2980b9", hover_color="#3498db")
        edit_in_builder_btn.grid(row=1, column=0, sticky="ew", padx=(0, 2), pady=2)

        update_strategy_btn = ctk.CTkButton(lib_controls, text="Update/Scan", command=self.update_strategy, width=100)
        update_strategy_btn.grid(row=1, column=1, sticky="ew", padx=2, pady=2)

        delete_strategy_btn = ctk.CTkButton(lib_controls, text="Delete", command=self.delete_strategy, width=80, fg_color="#c0392b", hover_color="#e74c3c")
        delete_strategy_btn.grid(row=1, column=2, sticky="ew", padx=(2, 0), pady=2)

        # Details Text (Preview)
        # This primarily acts as the Preview Text
        self.strategy_preview_text = ctk.CTkTextbox(management_frame, font=("Consolas", 12), wrap="word")
        self.strategy_preview_text.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.strategy_preview_text.configure(state="disabled")
        
        # Alias for legacy compatibility (prevents AttributeError)
        # In this layout, the preview text also serves as the list display area if needed, 
        # or we just let list updates overwrite it (usually preview updates happen on selection).
        self.strategy_list_display = self.strategy_preview_text

        # Edit Preview Units (Popup or overlay controls)
        preview_actions = ctk.CTkFrame(management_frame, fg_color="transparent")
        preview_actions.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        
        # We reused existing logic for editing units in preview, so we keep the button
        self.edit_units_btn = ctk.CTkButton(preview_actions, text="✏️ Edit Stored Units",
                                        command=self.toggle_units_editing)
        self.edit_units_btn.pack(side="left", fill="x", expand=True)
        self.edit_units_btn.grid_remove()

        # Convert pattern_follower → Composite migration button (hidden until a
        # pattern_follower preset is selected in the preview).
        self.convert_to_composite_btn = ctk.CTkButton(
            preview_actions, text="↗ Convert to Composite",
            command=self.convert_pattern_follower_to_composite,
            fg_color="#7c3aed", hover_color="#6d28d9",
        )
        self.convert_to_composite_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.convert_to_composite_btn.pack_forget()
        
        # Expand Button logic is less relevant in split view, but we can keep the variable to avoid errors
        self.preview_expanded = tk.BooleanVar(value=True)

        # We need a frame for the inline unit editor if we want to keep that functionality
        self.preview_units_frame = ctk.CTkFrame(management_frame)
        self.preview_units_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=5)
        self.preview_units_frame.grid_remove()
        
        # Recreating the units editor inside management frame
        units_controls_frame = ctk.CTkFrame(self.preview_units_frame, fg_color="transparent")
        units_controls_frame.pack(fill="x", pady=2)
        
        self.save_units_btn = ctk.CTkButton(units_controls_frame, text="Save", command=self.save_strategy_units, width=60)
        self.save_units_btn.pack(side="right", padx=2)
        self.cancel_units_btn = ctk.CTkButton(units_controls_frame, text="Cancel", command=self.cancel_units_editing, width=60, fg_color="#c0392b")
        self.cancel_units_btn.pack(side="right", padx=2)
        
        self.units_scroll = ctk.CTkScrollableFrame(self.preview_units_frame, height=120)
        self.units_scroll.pack(fill="both", expand=True)
        self.units_inner_frame = self.units_scroll
        self.preview_units_entries = {}

        # Import / Export Footer
        io_frame = ctk.CTkFrame(management_frame, fg_color="transparent")
        io_frame.grid(row=5, column=0, sticky="ew", padx=10, pady=10)
        io_frame.columnconfigure(0, weight=1)
        io_frame.columnconfigure(1, weight=1)
        
        ctk.CTkButton(io_frame, text="Import", command=self.import_strategies, width=80).grid(row=0, column=0, padx=2)
        ctk.CTkButton(io_frame, text="Export", command=self.export_strategies, width=80).grid(row=0, column=1, padx=2)
        ctk.CTkButton(io_frame, text="Refresh Encrypted", command=self.refresh_encrypted_strategies_ui, width=120).grid(row=1, column=0, columnspan=2, pady=5)

        # ===== ADVANCED STRATEGY BUILDER =====
        if "Advanced Strategy Builder" in allowed:
            adv_strat_tab = get_frame(self.nav_panels["🧠 Strategy Lab"], "Advanced Strategy Builder")
            self.init_advanced_strategy_tab(adv_strat_tab)

        # --- The rest of the widgets remain in their original tabs ---
        # main_content_frame is already a CTkScrollableFrame (returned by get_frame),
        # so no extra scroll wrapper is needed here.
        frame = main_content_frame

        # Configure grid weights for better alignment and resizing
        frame.columnconfigure(1, weight=1)

        # ===== BOT CONFIGURATION SECTION =====
        config_section = CollapsibleFrame(frame, title="Bot Configuration", expanded=True, accent_color=GOLD)
        config_section.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        config_frame = config_section.content_frame
        
        # Grid config for alignment
        config_frame.columnconfigure(1, weight=1)

        # Strategy settings
        ctk.CTkLabel(config_frame, text="Strategy:").grid(row=1, column=0, sticky="w", pady=2, padx=(10, 0))
        
        # Frame to hold dropdown and refresh button
        strategy_combo_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        strategy_combo_frame.grid(row=1, column=1, sticky="w", padx=(10, 10), pady=2)
        
        self.strategy_var = tk.StringVar(value=self.config.get("strategy", ""))
        # Wider entry to make searching comfortable.
        self.strategy_dropdown = ctk.CTkComboBox(strategy_combo_frame, variable=self.strategy_var, state="normal", width=200)
        self.strategy_dropdown.pack(side="left", padx=(0, 5))
        # Make the dropdown searchable: typing filters the values list.
        # update_strategy_dropdown writes _strategy_master_list, which
        # _make_combobox_searchable reads on every key press.
        self._strategy_master_list = []
        self._make_combobox_searchable(self.strategy_dropdown, "_strategy_master_list")

        self.refresh_strats_btn = ctk.CTkButton(
            strategy_combo_frame, text="🔄", command=self.refresh_custom_strategies,
            width=28, height=28, fg_color="#34495e", hover_color="#4a6680"
        )
        self.refresh_strats_btn.pack(side="left")
        ToolTip(self.strategy_dropdown,
                "Strategy name. Click the dropdown OR type to search —\n"
                "matching strategies stay visible in the list.\n"
                "Right-click to add/remove from quick-toggle favorites.")

        # Right-click strategy dropdown → add/remove favorite for the quick-toggle bar.
        def _show_strategy_context_menu(event):
            name = (self.strategy_var.get() or "").strip()
            if not name:
                return
            menu = tk.Menu(self.root, tearoff=0)
            if name in self._get_favorite_strategies():
                menu.add_command(label=f"☆  Remove '{name}' from favorites",
                                 command=lambda: self._remove_from_favorites(name))
            else:
                menu.add_command(label=f"★  Add '{name}' to favorites",
                                 command=lambda: self._add_to_favorites(name))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        self.strategy_dropdown.bind("<Button-3>", _show_strategy_context_menu)
        # CTkComboBox routes events to the inner entry; bind there too so right-click works on the text field.
        _inner_entry = getattr(self.strategy_dropdown, "_entry", None)
        if _inner_entry is not None:
            _inner_entry.bind("<Button-3>", _show_strategy_context_menu)

        # ── Quick-Toggle Pill Bar ──
        # Favorites (★) for one-click runtime swap. Most-played (🔥) lands in a later step.
        # Right-click any pill → remove. Right-click the strategy dropdown → add.
        self.quick_toggle_frame = ctk.CTkFrame(config_frame, fg_color="transparent")
        self.quick_toggle_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(10, 10), pady=(2, 4))
        ctk.CTkLabel(self.quick_toggle_frame, text="⚡ Quick:", font=("Segoe UI", 12)).pack(side="left", padx=(0, 6))
        self._quick_toggle_pills_container = ctk.CTkFrame(self.quick_toggle_frame, fg_color="transparent")
        self._quick_toggle_pills_container.pack(side="left", fill="x", expand=True)
        self._render_quick_toggle_bar()

        # Initialize strategy preview after strategy dropdown is created
        self.update_strategy_preview()
        # Update preview + pill-bar active highlight when strategy changes
        self.strategy_var.trace_add("write", lambda *_: self.update_strategy_preview())
        self.strategy_var.trace_add(
            "write",
            lambda *_: (self._render_quick_toggle_bar()
                        if getattr(self, "_quick_toggle_pills_container", None) is not None else None),
        )
        self.strategy_dropdown.configure(command=lambda e: self._on_manual_strategy_selected(e))
        
        # Initialize strategy selector dropdown
        self.update_strategy_selector()

        # Strategy Rotation Section (Moved outside config_frame for more width)
        rotation_section = CollapsibleFrame(frame, title="Strategy Rotation", expanded=False, accent_color=INFO)
        rotation_section.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        rotation_frame = rotation_section.content_frame
        rotation_frame.columnconfigure(1, weight=1)
        
        # Enable strategy rotation
        self.enable_strategy_rotation_var = tk.BooleanVar(value=self.config.get("enable_strategy_rotation", False))
        self.rotation_check = ctk.CTkCheckBox(rotation_frame, text="Enable Strategy Rotation", variable=self.enable_strategy_rotation_var)
        self.rotation_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        ToolTip(self.rotation_check, "Enable automatic rotation between different strategies across sessions")
        
        # Rotation mode
        ctk.CTkLabel(rotation_frame, text="Rotation Mode:").grid(row=2, column=0, sticky="w", pady=2, padx=5)
        self.rotation_mode_var = tk.StringVar(value=self.config.get("rotation_mode", "sequential"))
        self.rotation_mode_dropdown = ctk.CTkComboBox(
            rotation_frame,
            variable=self.rotation_mode_var,
            values=["sequential", "random", "smart_ranking", "smart_ranking_reverse"],
            state="readonly",
            width=200
        )
        self.rotation_mode_dropdown.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=2)
        ToolTip(self.rotation_mode_dropdown, "Sequential: Run strategies in listed order\nRandom: Pick a random strategy each time\nSmart Ranking: Best-performing strategy first\nSmart Ranking (Reverse): Worst-performing first (contrarian)")

        # Switch-on-loss toggle (row 3)
        switch_frame = ctk.CTkFrame(rotation_frame, fg_color="transparent")
        switch_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=2)

        self.rotation_trigger_var = tk.StringVar(value=self.config.get("rotation_trigger", "session_end"))
        self.switch_on_loss_var = tk.BooleanVar(value=self.config.get("rotation_trigger", "session_end") == "on_loss")
        self.switch_on_loss_check = ctk.CTkCheckBox(
            switch_frame, text="Switch on Loss", variable=self.switch_on_loss_var,
            command=self._on_switch_on_loss_toggled
        )
        self.switch_on_loss_check.pack(side="left", padx=(0, 10))
        ToolTip(self.switch_on_loss_check, "Rotate to the next strategy mid-session after consecutive losses\ninstead of waiting for the session to end")

        # Consecutive losses threshold
        self.switch_after_n_label = ctk.CTkLabel(switch_frame, text="after")
        self.switch_after_n_label.pack(side="left", padx=(0, 3))
        self.switch_after_n_losses_var = tk.IntVar(value=self.config.get("switch_after_n_losses", 1))
        self.switch_after_n_losses_entry = ctk.CTkEntry(switch_frame, textvariable=self.switch_after_n_losses_var, width=40, justify="center")
        self.switch_after_n_losses_entry.pack(side="left", padx=(0, 3))
        self.switch_after_n_suffix = ctk.CTkLabel(switch_frame, text="consecutive loss(es)")
        self.switch_after_n_suffix.pack(side="left")
        ToolTip(self.switch_after_n_losses_entry, "How many losses in a row before switching (default: 1)")

        # Carry progression toggle (row 3b — separate line for clarity)
        carry_frame = ctk.CTkFrame(rotation_frame, fg_color="transparent")
        carry_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=5, pady=2)

        self.carry_progression_var = tk.BooleanVar(value=self.config.get("carry_progression_on_switch", True))
        self.carry_progression_check = ctk.CTkCheckBox(carry_frame, text="Carry Progression on Switch", variable=self.carry_progression_var)
        self.carry_progression_check.pack(side="left", padx=(0, 8))
        ToolTip(self.carry_progression_check, "ON: Continue bet sizing from previous strategy\n(e.g. martingale step 3 carries over to next strategy)\nOFF: Reset to base bet when switching")
        self.carry_progression_hint = ctk.CTkLabel(carry_frame, text="(OFF = reset to base bet)", font=("Segoe UI", 11), text_color="#64748B")
        self.carry_progression_hint.pack(side="left")

        # Show/hide sub-controls based on toggle state
        self._on_switch_on_loss_toggled()

        # Reset rotation on session end
        reset_rot_frame = ctk.CTkFrame(rotation_frame, fg_color="transparent")
        reset_rot_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=5, pady=2)

        self.reset_rotation_on_session_var = tk.BooleanVar(value=self.config.get("reset_rotation_on_session", False))
        self.reset_rotation_check = ctk.CTkCheckBox(
            reset_rot_frame, text="Reset to 1st Strategy on Session End",
            variable=self.reset_rotation_on_session_var
        )
        self.reset_rotation_check.pack(side="left", padx=(0, 8))
        ToolTip(self.reset_rotation_check, "ON: Always restart from the first strategy in the bundle\nafter each session ends.\nOFF: Continue rotating to the next strategy (default).")

        # Filter by Regime Toggle & Indicator
        regime_frame = ctk.CTkFrame(rotation_frame, fg_color="transparent")
        regime_frame.grid(row=6, column=0, columnspan=2, sticky="ew", padx=5, pady=2)
        
        self.filter_regime_var = tk.BooleanVar(value=self.config.get("filter_by_regime", False))
        self.filter_regime_check = ctk.CTkCheckBox(regime_frame, text="Smart Filter", variable=self.filter_regime_var)
        self.filter_regime_check.pack(side="left", padx=(0, 10))
        ToolTip(self.filter_regime_check, "Only rotate to strategies that match the current table state")

        ctk.CTkLabel(regime_frame, text="Current Regime:").pack(side="left", padx=(5, 5))
        self.regime_status_label = ctk.CTkLabel(regime_frame, text="--", font=("Arial", 12, "bold"), text_color="gray")
        self.regime_status_label.pack(side="left")
        ToolTip(self.regime_status_label, "Detected Market State (Trending / Choppy / Neutral)")
        
        # Strategy selection for rotation
        ctk.CTkLabel(rotation_frame, text="Strategies to Rotate:").grid(row=7, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        self.rotation_strategies_var = tk.StringVar(value=self.config.get("rotation_strategies", ""))
        
        # New: Rotation Presets (Stacked vertically for responsiveness)
        list_preset_frame = ctk.CTkFrame(rotation_frame, fg_color="transparent")
        list_preset_frame.grid(row=8, column=0, columnspan=2, sticky="ew", padx=5, pady=2)
        
        self.rotation_strategies_entry = ctk.CTkEntry(list_preset_frame, textvariable=self.rotation_strategies_var)
        self.rotation_strategies_entry.pack(fill="x", expand=True, pady=(0, 5)) 
        ToolTip(self.rotation_strategies_entry, "Format: strategy:progression (e.g., 'martingale:flat,strat2:fibonacci')")
        
        # Preset Controls underneath the entry
        preset_controls = ctk.CTkFrame(list_preset_frame, fg_color="transparent")
        preset_controls.pack(fill="x")
        
        self.add_rotation_strategy_btn = ctk.CTkButton(
            preset_controls, 
            text="➕ Create / Edit Bundle", 
            command=self.add_rotation_strategy_dialog, 
            width=120, 
            fg_color="#27ae60", 
            hover_color="#2ecc71"
        )
        self.add_rotation_strategy_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.add_rotation_strategy_btn, "Open the Bundle & Rotation Builder to visually create/edit bundles")
        
        ctk.CTkButton(preset_controls, text="💾", command=self.save_rotation_list_preset, width=30, fg_color="#8e44ad").pack(side="left", padx=(0, 5))

        # Conditional-trigger editor — opens the per-strategy condition composer
        # that drives mid-session strategy selection when the bundle is in
        # `selection_mode: conditional`.
        self.triggers_editor_btn = ctk.CTkButton(
            preset_controls, text="🎯 Triggers",
            command=self.open_triggers_editor,
            width=90, fg_color="#7c3aed", hover_color="#6d28d9",
        )
        self.triggers_editor_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.triggers_editor_btn,
                "Configure per-strategy trigger conditions (cold/hot/streak), tiebreaker, "
                "and skip-round fallback. Edits the loaded bundle's selection logic.")
        
        self.rotation_preset_var = tk.StringVar(value="Select List...")
        self.rotation_preset_dropdown = ctk.CTkComboBox(
            preset_controls, 
            variable=self.rotation_preset_var,
            width=120,
            command=self.load_rotation_list_from_dropdown
        )
        self.rotation_preset_dropdown.pack(side="left")
        # Type-to-search the saved rotation-list presets (prefix/substring/initials).
        self._rotation_preset_master = []
        self._make_combobox_searchable(self.rotation_preset_dropdown, "_rotation_preset_master")
        self.refresh_rotation_presets_dropdown() # Populate initially
        
        # Per-strategy progression override
        ctk.CTkLabel(rotation_frame, text="Progression:").grid(row=9, column=0, sticky="w", pady=2, padx=5)
        self.rotation_progression_override_var = tk.BooleanVar(value=self.config.get("rotation_progression_override", False))
        self.rotation_progression_override_check = ctk.CTkCheckBox(rotation_frame, text="Each strategy uses its own progression", variable=self.rotation_progression_override_var)
        self.rotation_progression_override_check.grid(row=9, column=1, sticky="w", padx=(10, 0), pady=2)
        ToolTip(self.rotation_progression_override_check, "ON: Each strategy in the bundle uses the progression defined with it\n(e.g. strategy1:martingale, strategy2:fibonacci)\nOFF: All strategies share the main progression setting above")
        
        # Current rotation info
        self.rotation_info_var = tk.StringVar(value="No rotation active")
        self.rotation_info_label = ctk.CTkLabel(rotation_frame, textvariable=self.rotation_info_var, font=("Segoe UI", 10, "bold"), text_color="#EAB308")
        self.rotation_info_label.grid(row=10, column=0, columnspan=2, sticky="w", pady=(4, 5), padx=5)

        ctk.CTkLabel(config_frame, text="Progression:").grid(row=3, column=0, sticky="w", pady=2, padx=(10, 0))
        self.progression_var = tk.StringVar(value=self.config.get("progression_type", "flat"))
        self.progression_dropdown = ctk.CTkComboBox(
            config_frame,
            variable=self.progression_var,
            values=["flat", "martingale", "fibonacci", "dalembert", "custom_sequence", "dynamic"],
            state="readonly",
            width=200
        )
        self.progression_dropdown.grid(row=3, column=1, sticky="ew", padx=(10, 10), pady=2)
        self.progression_dropdown.configure(command=lambda e: self.on_progression_changed(None))

        # D'Alembert step (hidden unless dalembert selected)
        self.dalembert_step_var = tk.DoubleVar(value=self.config.get("dalembert_step", 1))
        # Note: Keeping DoubleVar for now. ctk.CTkEntry might prefer StringVar.
        self.dalembert_step_label = ctk.CTkLabel(config_frame, text="D'Alembert Step:")
        self.dalembert_step_entry = ctk.CTkEntry(config_frame, textvariable=self.dalembert_step_var, width=200)

        # Custom sequence (hidden unless custom_sequence selected)
        self.custom_sequence_var = tk.StringVar(value=','.join(str(x) for x in self.config.get("custom_sequence", [1])))
        self.custom_sequence_label = ctk.CTkLabel(config_frame, text="Custom Sequence:")
        self.custom_sequence_entry = ctk.CTkEntry(config_frame, textvariable=self.custom_sequence_var, width=200)

        self.on_progression_changed()  # Initial call to set visibility

        # =================================================================
        # SECTION 1: Betting Configuration
        # =================================================================
        betting_section = CollapsibleFrame(frame, title="Betting Configuration", expanded=True, accent_color=SUCCESS)
        betting_section.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        betting_frame = betting_section.content_frame
        betting_frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(betting_frame, text="Base Bet ($):").grid(row=1, column=0, sticky="w", pady=2, padx=5)
        self.base_bet_var = tk.StringVar(value=str(self.config["base_bet"]))
        ctk.CTkEntry(betting_frame, textvariable=self.base_bet_var, width=150).grid(row=1, column=1, sticky="w", padx=(5, 0), pady=2)
        # Keep the Dashboard risk preview label in sync when the user edits base bet.
        self.base_bet_var.trace_add("write", lambda *a: self._on_bundle_textbox_write('base_bet_var', '_bundle_base_bet', '_user_override_base_bet'))

        ctk.CTkLabel(betting_frame, text="Max Bet per Round ($):").grid(row=2, column=0, sticky="w", pady=2, padx=5)
        self.max_bet_var = tk.StringVar(value=str(self.config.get("max_bet", 100)))
        ctk.CTkEntry(betting_frame, textvariable=self.max_bet_var, width=150).grid(row=2, column=1, sticky="w", padx=(5, 0), pady=2)

        ctk.CTkLabel(betting_frame, text="Observation Trigger (Misses):").grid(row=3, column=0, sticky="w", pady=2, padx=5)
        self.observation_trigger_var = tk.StringVar(value=str(self.config.get("observation_trigger", 0)))
        ctk.CTkEntry(betting_frame, textvariable=self.observation_trigger_var, width=150).grid(row=3, column=1, sticky="w", padx=(5, 0), pady=2)
        ToolTip(betting_frame.grid_slaves(row=3, column=1)[0], "Number of times a target must MISS before the bot starts placing real chips.")

        # =================================================================
        # SECTION 2: Session Schedule
        # =================================================================
        schedule_section = CollapsibleFrame(frame, title="Session Schedule", expanded=False, accent_color=WARNING)
        schedule_section.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        schedule_frame = schedule_section.content_frame
        schedule_frame.columnconfigure(1, weight=1)
        schedule_frame.columnconfigure(3, weight=1)
        
        # Row 1: Number of Sessions | Duration
        ctk.CTkLabel(schedule_frame, text="Number of Sessions:").grid(row=1, column=0, sticky="w", pady=2, padx=5)
        self.num_sessions_var = tk.StringVar(value=str(self.config.get("num_sessions", 1)))
        ctk.CTkEntry(schedule_frame, textvariable=self.num_sessions_var, width=80).grid(row=1, column=1, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(schedule_frame, text="Session Duration (Minutes):").grid(row=1, column=2, sticky="w", pady=2, padx=5)
        self.session_duration_var = tk.StringVar(value=str(self.config["session_duration_minutes"]))
        ctk.CTkEntry(schedule_frame, textvariable=self.session_duration_var, width=80).grid(row=1, column=3, sticky="w", padx=(5, 0), pady=2)

        # Row 2: Timing Mode | (Gap placeholders)
        ctk.CTkLabel(schedule_frame, text="Session Timing Mode:").grid(row=2, column=0, sticky="w", pady=2, padx=5)
        self.session_timing_var = tk.StringVar(value=self.config.get("session_timing", "random"))
        timing_combo = ctk.CTkComboBox(schedule_frame, variable=self.session_timing_var, values=["random", "scheduled"], state="readonly", width=140, command=self.on_timing_changed)
        timing_combo.grid(row=2, column=1, columnspan=2, sticky="w", padx=(5, 0), pady=2)
        
        # Row 3: Gaps
        ctk.CTkLabel(schedule_frame, text="Inter-Session Gap (Min):").grid(row=3, column=0, sticky="w", pady=2, padx=5)
        self.min_gap_var = tk.IntVar(value=self.config.get("min_gap_minutes", 30))
        ctk.CTkEntry(schedule_frame, textvariable=self.min_gap_var, width=50).grid(row=3, column=1, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(schedule_frame, text="to (Max):").grid(row=3, column=2, sticky="w", pady=2, padx=5)
        self.max_gap_var = tk.IntVar(value=self.config.get("max_gap_minutes", 120))
        ctk.CTkEntry(schedule_frame, textvariable=self.max_gap_var, width=50).grid(row=3, column=3, sticky="w", padx=(5, 0), pady=2)

        # Row 3b: Total Run Time calculator
        total_time_row = ctk.CTkFrame(schedule_frame, fg_color="transparent")
        total_time_row.grid(row=3, column=4, columnspan=4, sticky="ew", padx=(20, 5), pady=2)

        ctk.CTkLabel(total_time_row, text="— or —  Total Run Time (hrs):",
                     font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(side="left", padx=(0, 6))
        if not hasattr(self, 'total_runtime_var'):
            self.total_runtime_var = tk.StringVar(value="")
        self.total_runtime_entry = ctk.CTkEntry(
            total_time_row, textvariable=self.total_runtime_var, width=60,
            placeholder_text="e.g. 2"
        )
        self.total_runtime_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            total_time_row, text="Calculate Sessions", width=140, height=28,
            font=ctk.CTkFont(size=11), fg_color="#1d4ed8", hover_color="#1e40af",
            command=self._calculate_sessions_from_runtime
        ).pack(side="left")
        self._calc_result_label = ctk.CTkLabel(
            total_time_row, text="", font=ctk.CTkFont(size=10), text_color="#10b981"
        )
        self._calc_result_label.pack(side="left", padx=(8, 0))

        # Row 4: Scheduled Timing (Hidden unless "scheduled" selected)
        self.scheduled_frame = ctk.CTkFrame(schedule_frame, fg_color="transparent")
        self.scheduled_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=2, padx=5)
        
        ctk.CTkLabel(self.scheduled_frame, text="Start Time (HH:MM):").grid(row=0, column=0, sticky="w", pady=2, padx=0)
        self.start_time_var = tk.StringVar(value=self.config.get("start_time", "09:00"))
        ctk.CTkEntry(self.scheduled_frame, textvariable=self.start_time_var, width=80).grid(row=0, column=1, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(self.scheduled_frame, text="End Time (HH:MM):").grid(row=0, column=2, sticky="w", pady=2, padx=(10,0))
        self.end_time_var = tk.StringVar(value=self.config.get("end_time", "18:00"))
        ctk.CTkEntry(self.scheduled_frame, textvariable=self.end_time_var, width=80).grid(row=0, column=3, sticky="w", padx=(5, 0), pady=2)
        
        # Initial visibility check
        self.on_timing_changed()

        # =================================================================
        # SECTION 3: Session Goals (Stop Conditions)
        # =================================================================
        # Auto-expand when escalation is already enabled in the loaded config
        # so the preview table is visible without the user having to hunt for it.
        _goals_default_expanded = bool(self.config.get("enable_escalation_on_loss", False))
        goals_section = CollapsibleFrame(frame, title="Session Goals (Stop Conditions)", expanded=_goals_default_expanded, accent_color=DANGER)
        goals_section.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        # Stash a reference so the escalation toggle trace can pop it open
        # the moment the user enables escalation on loss.
        self._goals_section = goals_section
        goals_frame = goals_section.content_frame
        goals_frame.columnconfigure(1, weight=1)
        goals_frame.columnconfigure(3, weight=1)
        
        # Master Toggle
        self.enable_session_stops_var = tk.BooleanVar(value=self.config.get("enable_session_stops", True))
        self.session_stops_check = ctk.CTkCheckBox(goals_frame, text="Enable Session Stops", variable=self.enable_session_stops_var)
        self.session_stops_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        self.enable_session_stops_var.trace_add("write", lambda *args: self.root.after(100, lambda: self.update_hud_safe()))

        # Profit & Loss
        ctk.CTkLabel(goals_frame, text="Profit Target ($ or %):").grid(row=2, column=0, sticky="w", pady=2, padx=5)
        self.profit_target_var = tk.StringVar(value=str(self.config.get("profit_target", 0)))
        entry_pt = ctk.CTkEntry(goals_frame, textvariable=self.profit_target_var, width=80)
        entry_pt.grid(row=2, column=1, sticky="w", padx=(5, 0), pady=2)
        ToolTip(entry_pt, "Target profit per session. Accepts $ (50) or % (10%) of starting balance.")

        ctk.CTkLabel(goals_frame, text="Stop Loss ($ or %):").grid(row=2, column=2, sticky="w", pady=2, padx=5)
        self.max_loss_var = tk.StringVar(value=str(self.config.get("max_loss", 100)))
        entry_sl = ctk.CTkEntry(goals_frame, textvariable=self.max_loss_var, width=80)
        entry_sl.grid(row=2, column=3, sticky="w", padx=(5, 0), pady=2)
        ToolTip(entry_sl, "Max loss per session. Accepts $ (100) or % (10%) of starting balance.")
        # Refresh the Dashboard risk preview label when the user edits the stop loss,
        # so "Use Bundle Values" shows the override instead of the stale bundle snapshot.
        self.max_loss_var.trace_add("write", lambda *a: self._on_bundle_textbox_write('max_loss_var', '_bundle_max_loss', '_user_override_max_loss'))

        # ── Escalation on Loss ─────────────────────────────────────────────
        # Multiplies base bet AND session stop-loss after each session
        # stop-loss hit. Resets to the originals on global profit / session
        # profit target. See _apply_session_escalation for the logic.
        self.enable_escalation_on_loss_var = tk.BooleanVar(
            value=self.config.get("enable_escalation_on_loss", False)
        )
        esc_check = ctk.CTkCheckBox(
            goals_frame, text="Escalate on Session Stop Loss",
            variable=self.enable_escalation_on_loss_var,
        )
        esc_check.grid(row=4, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        ToolTip(esc_check, "After a session stop-loss hits, multiply BOTH base bet\n"
                           "and session stop-loss by the multiplier below.\n"
                           "Resets to original values when global profit or session\n"
                           "profit target is reached.")

        ctk.CTkLabel(goals_frame, text="Multiplier ×:").grid(row=4, column=2, sticky="w", pady=2, padx=5)
        self.escalation_multiplier_var = tk.StringVar(
            value=str(self.config.get("escalation_multiplier", 2.0))
        )
        ctk.CTkEntry(goals_frame, textvariable=self.escalation_multiplier_var, width=80).grid(
            row=4, column=3, sticky="w", padx=(5, 0), pady=2
        )

        ctk.CTkLabel(goals_frame, text="Max steps:").grid(row=5, column=0, sticky="w", pady=2, padx=5)
        self.escalation_max_steps_var = tk.StringVar(
            value=str(self.config.get("escalation_max_steps", 4))
        )
        max_steps_entry = ctk.CTkEntry(goals_frame, textvariable=self.escalation_max_steps_var, width=80)
        max_steps_entry.grid(row=5, column=1, sticky="w", padx=(5, 0), pady=2)
        ToolTip(max_steps_entry, "Stop escalating after this many consecutive\n"
                                  "stop-loss hits. Prevents runaway scaling.\n"
                                  "Ignored when 'Per-step ×' is filled.")

        ctk.CTkLabel(goals_frame, text="Per-step ×:").grid(row=5, column=2, sticky="w", pady=2, padx=5)
        self.escalation_per_step_var = tk.StringVar(
            value=str(self.config.get("escalation_per_step", "") or "")
        )
        per_step_entry = ctk.CTkEntry(goals_frame, textvariable=self.escalation_per_step_var, width=120)
        per_step_entry.grid(row=5, column=3, sticky="w", padx=(5, 0), pady=2)
        ToolTip(per_step_entry, "Optional comma-separated multipliers per step,\n"
                                "applied to the INITIAL base bet & stop-loss.\n"
                                "  e.g. \"2,3,5,10\" means:\n"
                                "    after 1st SL → 2× initial\n"
                                "    after 2nd SL → 3× initial\n"
                                "    after 3rd SL → 5× initial\n"
                                "    after 4th SL → 10× initial\n"
                                "Number of values = max steps. Leave blank to\n"
                                "use uniform 'Multiplier ×' instead.")

        # ── Live preview: what each escalation step actually looks like ───
        # Shown values use the run's INITIAL base bet & SL so the table is
        # accurate even mid-run. Idle: textbox values. Running: snapshot
        # taken at start_bot.
        preview_label = ctk.CTkLabel(
            goals_frame,
            text="Escalation preview  (live, computed from run's initial base bet & SL):",
            font=("Segoe UI", 10, "bold"), text_color="#facc15", anchor="w",
        )
        preview_label.grid(row=6, column=0, columnspan=4, sticky="w", padx=5, pady=(8, 2))

        preview_frame = ctk.CTkFrame(goals_frame, fg_color="#0f172a", corner_radius=6)
        preview_frame.grid(row=7, column=0, columnspan=4, sticky="ew", padx=5, pady=(0, 6))

        try:
            _style = ttk.Style()
            _style.configure(
                "EscPreview.Treeview",
                background="#0f172a", fieldbackground="#0f172a",
                foreground="#e5e7eb", rowheight=22, borderwidth=0,
            )
            _style.configure(
                "EscPreview.Treeview.Heading",
                background="#1f2937", foreground="#e5e7eb",
                font=("Segoe UI", 9, "bold"),
            )
        except Exception:
            pass

        self.escalation_preview_tree = ttk.Treeview(
            preview_frame,
            columns=("step", "trigger", "mult", "base", "sl", "cum"),
            show="headings", height=6,
            style="EscPreview.Treeview",
        )
        for col, text, width, anchor in [
            ("step",    "Step",          50,  "center"),
            ("trigger", "Trigger",       180, "w"),
            ("mult",    "× Multiplier",  90,  "center"),
            ("base",    "Base Bet",      90,  "e"),
            ("sl",      "Session SL",    100, "e"),
            ("cum",     "Cumulative risk", 130, "e"),
        ]:
            self.escalation_preview_tree.heading(col, text=text)
            self.escalation_preview_tree.column(col, width=width, anchor=anchor)
        self.escalation_preview_tree.pack(fill="x", padx=4, pady=4)

        # Refresh now and whenever any input changes. Debounced via root.after
        # so rapid typing doesn't thrash.
        def _schedule_preview_refresh(*_a):
            try:
                if getattr(self, "_esc_preview_after_id", None):
                    self.root.after_cancel(self._esc_preview_after_id)
            except Exception:
                pass
            try:
                self._esc_preview_after_id = self.root.after(120, self._refresh_escalation_preview)
            except Exception:
                pass

        for _v in (self.base_bet_var, self.max_loss_var,
                   self.escalation_multiplier_var, self.escalation_max_steps_var,
                   self.escalation_per_step_var, self.enable_escalation_on_loss_var):
            try:
                _v.trace_add("write", _schedule_preview_refresh)
            except Exception:
                pass

        # Auto-expand the Session Goals section when the user ticks "Escalate
        # on Session Stop Loss" — otherwise the preview table sits hidden in
        # a collapsed section and people can't find it.
        def _auto_expand_on_escalation(*_a):
            try:
                if self.enable_escalation_on_loss_var.get() and hasattr(self, "_goals_section"):
                    self._goals_section.expand()
            except Exception:
                pass
        try:
            self.enable_escalation_on_loss_var.trace_add("write", _auto_expand_on_escalation)
        except Exception:
            pass
        # Initial render — defer to after the goals_frame is fully built.
        try:
            self.root.after(0, self._refresh_escalation_preview)
        except Exception:
            pass

        # Streaks
        ctk.CTkLabel(goals_frame, text="Stop on Win Streak (Rounds):").grid(row=3, column=0, sticky="w", pady=2, padx=5)
        self.max_session_wins_streak_var = tk.StringVar(value=str(self.config.get("max_session_wins_streak", 0)))
        ctk.CTkEntry(goals_frame, textvariable=self.max_session_wins_streak_var, width=80).grid(row=3, column=1, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(goals_frame, text="Stop on Loss Streak (Rounds):").grid(row=3, column=2, sticky="w", pady=2, padx=5)
        self.max_session_losses_streak_var = tk.StringVar(value=str(self.config.get("max_session_losses_streak", 0)))
        ctk.CTkEntry(goals_frame, textvariable=self.max_session_losses_streak_var, width=80).grid(row=3, column=3, sticky="w", padx=(5, 0), pady=2)
        # Note: Max Consecutive Losses (Global) removed/merged or kept? 
        # User requested consistency. "Stop on Loss Streak (Rounds)" effectively replaces "Max Consecutive Losses".
        # But Max Consec Losses might be strategy specific in original code? 
        # "max_consec_losses" config key was used. Let's map "Stop on Loss Streak" to that key if needed, or keep distinct.
        # Original code had BOTH "Max Consecutive Losses" (Row 8) AND "Stop on Streak - Losses" (Row 2).
        # We unified them in the SessionManager task. Let's keep just the Session Manager one ("max_session_losses_streak").
        # If "max_consec_losses" implies a strategy progression reset, that's different. 
        # For now, we stick to the Session Stop logic we just built.

        # =================================================================
        # SECTION 4: Advanced Behavior
        # =================================================================
        advanced_section = CollapsibleFrame(frame, title="Advanced Behavior (Trailing & Extensions)", expanded=False, accent_color=PURPLE)
        advanced_section.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        advanced_frame = advanced_section.content_frame
        advanced_frame.columnconfigure(1, weight=1)
        advanced_frame.columnconfigure(3, weight=1)
        
        # Trailing Stop
        self.enable_trailing_stop_var = tk.BooleanVar(value=self.config.get("enable_trailing_stop", False))
        self.trailing_stop_check = ctk.CTkCheckBox(advanced_frame, text="Enable Trailing Stop", variable=self.enable_trailing_stop_var)
        self.trailing_stop_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        
        ctk.CTkLabel(advanced_frame, text="Trailing Amount ($ or %):").grid(row=1, column=2, sticky="w", pady=2, padx=5)
        self.trailing_stop_amount_var = tk.StringVar(value=str(self.config.get("trailing_stop_amount", 0)))
        self.trailing_stop_entry = ctk.CTkEntry(advanced_frame, textvariable=self.trailing_stop_amount_var, width=80)
        self.trailing_stop_entry.grid(row=1, column=3, sticky="w", padx=(5, 0), pady=2)
        
        # Extensions toggles
        self.session_ext_after_win_var = tk.BooleanVar(value=self.config.get("session_ext_after_win", False))
        self.session_ext_at_high_var = tk.BooleanVar(value=self.config.get("session_ext_at_high", False))
        ctk.CTkCheckBox(advanced_frame, text="End Session Only After Win", variable=self.session_ext_after_win_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        ctk.CTkCheckBox(advanced_frame, text="End Session Only at Session High", variable=self.session_ext_at_high_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        
        # Safety Caps
        ctk.CTkLabel(advanced_frame, text="Max Ext Rounds:").grid(row=2, column=2, sticky="w", pady=2, padx=5)
        self.max_ext_rounds_var = tk.StringVar(value=str(self.config.get("max_extension_rounds", 20)))
        ctk.CTkEntry(advanced_frame, textvariable=self.max_ext_rounds_var, width=50).grid(row=2, column=3, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(advanced_frame, text="Give Up Amt ($):").grid(row=3, column=2, sticky="w", pady=2, padx=5)
        self.ext_give_up_var = tk.StringVar(value=str(self.config.get("extension_give_up_amount", 50)))
        ctk.CTkEntry(advanced_frame, textvariable=self.ext_give_up_var, width=50).grid(row=3, column=3, sticky="w", padx=(5, 0), pady=2)

        # =================================================================
        # SECTION 5: Global Safety Net
        # =================================================================
        global_section = CollapsibleFrame(frame, title="Global Safety Net (All Sessions)", expanded=False, accent_color=DANGER)
        global_section.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, PAD_SECTION), padx=PAD_GROUP)
        global_stop_frame = global_section.content_frame
        global_stop_frame.columnconfigure(1, weight=1)
        global_stop_frame.columnconfigure(3, weight=1)
        
        self.enable_global_stop_var = tk.BooleanVar(value=self.config.get("enable_global_stop", False))
        self.global_stop_check = ctk.CTkCheckBox(global_stop_frame, text="Enable Global Stops", variable=self.enable_global_stop_var)
        self.global_stop_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=2, padx=5)
        
        ctk.CTkLabel(global_stop_frame, text="Global Profit Target ($ or %):").grid(row=2, column=0, sticky="w", pady=2, padx=5)
        self.global_profit_stop_var = tk.StringVar(value=str(self.config.get("global_profit_stop", 0)))
        ctk.CTkEntry(global_stop_frame, textvariable=self.global_profit_stop_var, width=80).grid(row=2, column=1, sticky="w", padx=(5, 0), pady=2)
        
        ctk.CTkLabel(global_stop_frame, text="Global Stop Loss ($ or %):").grid(row=2, column=2, sticky="w", pady=2, padx=5)
        self.global_stop_loss_var = tk.StringVar(value=str(self.config.get("global_stop_loss", 0)))
        ctk.CTkEntry(global_stop_frame, textvariable=self.global_stop_loss_var, width=80).grid(row=2, column=3, sticky="w", padx=(5, 0), pady=2)
        
        self.enable_global_stop_var.trace_add("write", lambda *args: self.root.after(100, lambda: self.update_hud_safe()))
        self.global_profit_stop_var.trace_add("write", lambda *args: self.root.after(100, lambda: self.update_hud_safe()))
        self.global_stop_loss_var.trace_add("write", lambda *args: self.root.after(100, lambda: self.update_hud_safe()))

        # Move OCR Settings to its own tab
        # ===== OCR SETTINGS SECTION =====
        ocr_settings_frame = ctk.CTkFrame(ocr_settings_tab_frame)
        ocr_settings_frame.pack(fill="both", expand=True, padx=15, pady=15)
        ocr_settings_frame.columnconfigure(0, weight=1)
        ocr_settings_frame.columnconfigure(1, weight=1)

        # OCR Configuration
        ctk.CTkLabel(ocr_settings_frame, text="OCR Configuration", font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 15), padx=10)
        
        # Max Table State Failures
        ctk.CTkLabel(ocr_settings_frame, text="Max Table State Failures:").grid(row=1, column=0, sticky="w", pady=(0, 5), padx=10)
        self.max_table_state_failures_var = tk.IntVar(value=self.config.get("max_table_state_failures", 3))
        max_failures_entry = ctk.CTkEntry(ocr_settings_frame, textvariable=self.max_table_state_failures_var, width=150)
        max_failures_entry.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(0, 5))
        ToolTip(max_failures_entry, "Maximum number of consecutive table state OCR failures before reset.")
        
        # Reset Cooldown
        ctk.CTkLabel(ocr_settings_frame, text="Reset Cooldown (seconds):").grid(row=2, column=0, sticky="w", pady=(0, 5), padx=10)
        self.table_state_reset_cooldown_var = tk.IntVar(value=self.config.get("table_state_reset_cooldown", 30))
        reset_cooldown_entry = ctk.CTkEntry(ocr_settings_frame, textvariable=self.table_state_reset_cooldown_var, width=150)
        reset_cooldown_entry.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(0, 5))
        ToolTip(reset_cooldown_entry, "Time to wait before attempting to reset table state after failures.")
        
        # OCR Testing Section
        ocr_test_frame = ctk.CTkFrame(ocr_settings_frame)
        ocr_test_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(20, 0), padx=10)
        ocr_test_frame.columnconfigure(0, weight=1)
        ocr_test_frame.columnconfigure(1, weight=1)
        
        ctk.CTkLabel(ocr_test_frame, text="Test OCR functionality:", font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(5, 10), padx=5)
        
        test_balance_btn = ctk.CTkButton(ocr_test_frame, text="Test Balance OCR", command=self.test_balance_ocr)
        test_balance_btn.grid(row=1, column=0, sticky="ew", padx=(5, 5), pady=5)
        ToolTip(test_balance_btn, "Test OCR reading of balance from recorded region.")
        
        test_table_state_btn = ctk.CTkButton(ocr_test_frame, text="Test Table State OCR", command=self.test_table_state_ocr)
        test_table_state_btn.grid(row=1, column=1, sticky="ew", padx=(5, 5), pady=5)
        ToolTip(test_table_state_btn, "Test OCR reading of table state from recorded region.")
        
        # OCR Results Display
        ctk.CTkLabel(ocr_test_frame, text="OCR Test Results:", font=("Arial", 12, "bold")).grid(row=2, column=0, columnspan=2, sticky="w", pady=(15, 5), padx=5)
        self.ocr_results_text = ctk.CTkTextbox(ocr_test_frame, height=150, font=("Consolas", 12), wrap="word")
        self.ocr_results_text.configure(state="disabled")
        self.ocr_results_text.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10), padx=5)
        
        # ===== SETTINGS TAB =====
        settings_tab_frame = get_frame(self.nav_panels["⚙️ Settings & Setup"], "Settings")
        
        # General Settings Container
        general_settings_frame = ctk.CTkFrame(settings_tab_frame)
        general_settings_frame.pack(fill="both", expand=True, padx=15, pady=15)
        general_settings_frame.columnconfigure(0, weight=1)
        
        ctk.CTkLabel(general_settings_frame, text="Application Settings", font=("Segoe UI", 16, "bold"), text_color="#E6C200").pack(anchor="w", padx=10, pady=(10, 5))

        # Telegram Section
        tg_group = ctk.CTkFrame(general_settings_frame)
        tg_group.pack(fill="x", padx=10, pady=10)
        tg_group.columnconfigure(1, weight=1)
        
        ctk.CTkLabel(tg_group, text="Mobile Remote Control (Telegram)", font=("Arial", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(5, 10), padx=5)
        
        ctk.CTkLabel(tg_group, text="Bot Token:").grid(row=1, column=0, sticky="w", pady=5, padx=5)
        # Re-use existing var name to maintain logic compatibility
        # self.telegram_token_var was initialized when adding to Bot Control, but since we removed that block, 
        # we need to re-initialize it here safely.
        # Actually variables are usually init in __init__, but here they were init in create_widgets.
        # Since I deleted the previous init, I must re-init here.
        if not hasattr(self, 'telegram_token_var'):
             self.telegram_token_var = tk.StringVar(value=self.config.get("telegram_token", ""))
        
        ctk.CTkEntry(tg_group, textvariable=self.telegram_token_var, show="*").grid(row=1, column=1, sticky="ew", padx=(5, 5), pady=5)

        ctk.CTkLabel(tg_group, text="Chat ID:").grid(row=2, column=0, sticky="w", pady=5, padx=5)
        if not hasattr(self, 'telegram_chat_id_var'):
             self.telegram_chat_id_var = tk.StringVar(value=self.config.get("telegram_chat_id", ""))

        ctk.CTkEntry(tg_group, textvariable=self.telegram_chat_id_var).grid(row=2, column=1, sticky="ew", padx=(5, 5), pady=5)
        
        ctk.CTkLabel(tg_group, text="Get Token from @BotFather. Get Chat ID from @userinfobot.", font=("Arial", 10), text_color="gray").grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=(0, 5))
        
        save_tg = ctk.CTkButton(tg_group, text="Save & Restart Remote", command=self.save_telegram_config)
        save_tg.grid(row=4, column=0, columnspan=2, pady=10)

        # ── Subscription Card ─────────────────────────────────────────────────
        sub_card = ctk.CTkFrame(general_settings_frame, fg_color="#111827", corner_radius=12)
        sub_card.pack(fill="x", padx=10, pady=10)

        # Header row
        sub_header = ctk.CTkFrame(sub_card, fg_color="transparent")
        sub_header.pack(fill="x", padx=16, pady=(14, 0))

        ctk.CTkLabel(sub_header, text="My Subscription",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#f4f4f5").pack(side="left")

        # Refresh button (top-right)
        ctk.CTkButton(
            sub_header, text="↻  Refresh", width=90, height=28,
            font=ctk.CTkFont(size=11), fg_color="#1f2937", hover_color="#374151",
            command=self._refresh_license_status
        ).pack(side="right")

        # Divider
        ctk.CTkFrame(sub_card, height=1, fg_color="#1f2937").pack(fill="x", padx=16, pady=(10, 0))

        # Account email
        email_val = ""
        if hasattr(self, "license_manager") and self.license_manager.current_user:
            email_val = self.license_manager.current_user.email or ""
        info_grid = ctk.CTkFrame(sub_card, fg_color="transparent")
        info_grid.pack(fill="x", padx=16, pady=(12, 0))
        info_grid.columnconfigure(1, weight=1)

        def _sub_row(parent, row, label, value, value_color="#e4e4e7"):
            ctk.CTkLabel(parent, text=label,
                         font=ctk.CTkFont(size=11), text_color="#71717a"
                         ).grid(row=row, column=0, sticky="w", pady=3)
            ctk.CTkLabel(parent, text=value,
                         font=ctk.CTkFont(size=11, weight="bold"), text_color=value_color
                         ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=3)

        _sub_row(info_grid, 0, "Account", email_val or "—")

        # Tier badge
        tier_color = TIER_COLORS.get(self.license_tier, DANGER)
        ctk.CTkLabel(info_grid, text="Plan",
                     font=ctk.CTkFont(size=11), text_color="#71717a"
                     ).grid(row=1, column=0, sticky="w", pady=3)
        self.license_tier_label = ctk.CTkLabel(
            info_grid, text=f"  {self.license_tier}  ",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="white",
            fg_color=tier_color, corner_radius=6
        )
        self.license_tier_label.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=3)

        # Valid until
        ld = getattr(self.license_manager, "license_data", None) or {}
        valid_until = ld.get("valid_until")
        duration = ld.get("subscription_duration", "")
        if valid_until:
            try:
                from datetime import datetime, timezone
                exp = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
                days_left = (exp - datetime.now(timezone.utc)).days
                expiry_str = exp.strftime("%d %b %Y") + f"  ({days_left}d left)"
                expiry_color = "#f59e0b" if days_left < 14 else "#a1a1aa"
            except Exception:
                expiry_str = valid_until[:10]
                expiry_color = "#a1a1aa"
        elif duration == "lifetime" or self.license_tier == "ADMIN":
            expiry_str = "Lifetime"
            expiry_color = "#10b981"
        else:
            expiry_str = "—"
            expiry_color = "#a1a1aa"
        _sub_row(info_grid, 2, "Valid Until", expiry_str, expiry_color)

        # Active session
        ctk.CTkLabel(info_grid, text="Session",
                     font=ctk.CTkFont(size=11), text_color="#71717a"
                     ).grid(row=3, column=0, sticky="w", pady=3)
        self.session_status_label = ctk.CTkLabel(
            info_grid, text="",
            font=ctk.CTkFont(size=11, weight="bold"), text_color="#10b981"
        )
        self.session_status_label.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=3)
        self._update_session_label()

        # Entitlements
        entitlements = getattr(self.license_manager, "entitlements", [])
        ent_str = ", ".join(entitlements) if entitlements else "None"
        _sub_row(info_grid, 4, "Bundles Owned", ent_str)

        # Action buttons
        btn_row = ctk.CTkFrame(sub_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(14, 16))

        ctk.CTkButton(
            btn_row, text="⬆  Upgrade Plan", height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981", hover_color="#059669",
            command=lambda: __import__("webbrowser").open("https://spinedge.pro/shop")
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="🔄  Refresh License", height=34,
            font=ctk.CTkFont(size=12),
            fg_color="#1d4ed8", hover_color="#1e40af",
            command=self._refresh_license_status
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="🚪  Log Out", height=34,
            font=ctk.CTkFont(size=12),
            fg_color="#7f1d1d", hover_color="#991b1b",
            command=self._logout_and_restart
        ).pack(side="left")

        # Save All Settings Button
        ctk.CTkButton(general_settings_frame, text="💾 Save All Settings", command=self.save_all_settings, fg_color="#2980b9", hover_color="#2471a3", height=36, font=("Segoe UI", 13, "bold")).pack(pady=(15, 10))



        # ===== BOT STATUS & STATS SECTION =====
        stats_section = CollapsibleFrame(frame, title="Bot Status & Statistics", expanded=True, accent_color=INFO)
        stats_section.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, PAD_GROUP), padx=PAD_GROUP)
        stats_frame = stats_section.content_frame

        # Current session info
        session_info_frame = ctk.CTkFrame(stats_frame, fg_color="transparent")
        session_info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10), padx=5)
        
        ctk.CTkLabel(session_info_frame, text="Current Session:", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.current_session_label = ctk.CTkLabel(session_info_frame, text="Not Started", text_color="gray")
        self.current_session_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(session_info_frame, text="Session Progress:").grid(row=1, column=0, sticky="w")
        self.session_progress_label = ctk.CTkLabel(session_info_frame, text="0/0")
        self.session_progress_label.grid(row=1, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(session_info_frame, text="Time Remaining:").grid(row=2, column=0, sticky="w")
        self.time_remaining_label = ctk.CTkLabel(session_info_frame, text="--:--")
        self.time_remaining_label.grid(row=2, column=1, sticky="w", padx=(10, 0))

        # Betting information
        betting_info_frame = ctk.CTkFrame(stats_frame, fg_color="transparent")
        betting_info_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 10), padx=5)
        
        ctk.CTkLabel(betting_info_frame, text="Current Bet:", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.current_bet_label = ctk.CTkLabel(betting_info_frame, text="--", text_color="#3498db") # Blue-ish
        self.current_bet_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(betting_info_frame, text="Betting On:").grid(row=1, column=0, sticky="w")
        self.betting_on_label = ctk.CTkLabel(betting_info_frame, text="--")
        self.betting_on_label.grid(row=1, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(betting_info_frame, text="Table State:").grid(row=2, column=0, sticky="w")
        self.table_state_label = ctk.CTkLabel(betting_info_frame, text="--", text_color="#e67e22") # Orange-ish
        self.table_state_label.grid(row=2, column=1, sticky="w", padx=(10, 0))

        # Financial tracking
        financial_frame = ctk.CTkFrame(stats_frame, fg_color="transparent")
        financial_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 10), padx=5)
        
        ctk.CTkLabel(financial_frame, text="Starting Balance:", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.starting_balance_label = ctk.CTkLabel(financial_frame, text="--", text_color="#3498db")
        self.starting_balance_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(financial_frame, text="Projected Balance:", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w")
        self.projected_balance_label = ctk.CTkLabel(financial_frame, text="--", text_color="#2ecc71") # Green
        self.projected_balance_label.grid(row=1, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(financial_frame, text="Session P&L:").grid(row=2, column=0, sticky="w")
        self.session_pnl_label = ctk.CTkLabel(financial_frame, text="--")
        self.session_pnl_label.grid(row=2, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(financial_frame, text="Total P&L:").grid(row=3, column=0, sticky="w")
        self.total_pnl_label = ctk.CTkLabel(financial_frame, text="--")
        self.total_pnl_label.grid(row=3, column=1, sticky="w", padx=(10, 0))

        # Performance stats
        performance_frame = ctk.CTkFrame(stats_frame, fg_color="transparent")
        performance_frame.grid(row=5, column=0, columnspan=2, sticky="ew", padx=5, pady=(0, 5))
        
        ctk.CTkLabel(performance_frame, text="Rounds Played:", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w")
        self.rounds_played_label = ctk.CTkLabel(performance_frame, text="0")
        self.rounds_played_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(performance_frame, text="Wins:").grid(row=1, column=0, sticky="w")
        self.wins_label = ctk.CTkLabel(performance_frame, text="0", text_color="#2ecc71")
        self.wins_label.grid(row=1, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(performance_frame, text="Losses:").grid(row=2, column=0, sticky="w")
        self.losses_label = ctk.CTkLabel(performance_frame, text="0", text_color="#e74c3c")
        self.losses_label.grid(row=2, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(performance_frame, text="Win Rate:").grid(row=3, column=0, sticky="w")
        self.win_rate_label = ctk.CTkLabel(performance_frame, text="0%")
        self.win_rate_label.grid(row=3, column=1, sticky="w", padx=(10, 0))
        
        ctk.CTkLabel(performance_frame, text="Consecutive Losses:").grid(row=4, column=0, sticky="w")
        self.consecutive_losses_label = ctk.CTkLabel(performance_frame, text="0", text_color="#e74c3c")
        self.consecutive_losses_label.grid(row=4, column=1, sticky="w", padx=(10, 0))

        # Configure grid weights for stats sub-frames
        session_info_frame.columnconfigure(1, weight=1)
        betting_info_frame.columnconfigure(1, weight=1)
        financial_frame.columnconfigure(1, weight=1)
        performance_frame.columnconfigure(1, weight=1)

        # ===== CONTROL SECTION =====
        control_section = CollapsibleFrame(frame, title="Bot Control", expanded=True, accent_color=SUCCESS)
        control_section.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(0, PAD_GROUP), padx=PAD_GROUP)
        control_frame = control_section.content_frame

        # Window selection
        window_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        window_frame.pack(fill="x", pady=(5, 10))
        
        self.select_window_button = ctk.CTkButton(window_frame, text="Select Window", command=self.select_window_dialog)
        self.select_window_button.pack(side="left", padx=(10, 5))
        
        self.highlight_button = ctk.CTkButton(window_frame, text="Highlight Window", command=self.highlight_selected_window)
        self.highlight_button.pack(side="left", padx=5)
        
        # HUD Toggle
        if not hasattr(self, 'show_hud_var'):
            self.show_hud_var = ctk.BooleanVar(value=True)
        self.hud_checkbox = ctk.CTkCheckBox(window_frame, text="Show Live Overlay", variable=self.show_hud_var, command=self.toggle_hud)
        self.hud_checkbox.pack(side="left", padx=10)
        # Initialize HUD immediately if enabled
        if self.show_hud_var.get():
            self.root.after(1000, self.toggle_hud) # Delay slightly to ensure root is ready

        # Bot control buttons
        bot_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        bot_frame.pack(fill="x", pady=(0, 10))
        
        self.start_button = ctk.CTkButton(bot_frame, text="Start Bot", command=self.start_bot, fg_color="#2ecc71", hover_color="#27ae60", width=150, height=40)
        self.start_button.pack(side="left", padx=(10, 10))
        
        self.stop_bot_button = ctk.CTkButton(bot_frame, text="Stop Bot", command=self.stop_bot, state="disabled", fg_color="#e74c3c", hover_color="#c0392b", width=150, height=40)
        self.stop_bot_button.pack(side="left")



        # Dynamic progression rules editor (Strategy Rotation)
        self.dynamic_rules_frame = ctk.CTkFrame(frame)
        # Note: Original code used 'config_frame' which might be undefined in this snippet context if not careful, 
        # but based on line 1661 it expects a parent. 
        # To be safe and place it where it makes sense (Bot Control or Strategy Builder?), 
        # logic suggests it was likely in a Settings or Control tab.
        # Assuming we want to stick it in "Bot Control" or reuse the existing parent if 'config_frame' is known.
        # Since I cannot see 'config_frame' definition in the snippet, I will assume it's part of 'control_frame' or similar.
        # However, to avoid NameError, I will attach it to 'control_frame' defined at 1626 if possible, OR just search for where dynamic_rules_frame was.
        # Let's assume 'config_frame' was meant to be 'control_frame' or a frame inside 'Settings'.

        # Add status bar
        self.status_var = tk.StringVar(value="Ready.")

        status_bar = ctk.CTkLabel(self.root, textvariable=self.status_var, anchor="w", font=("Arial", 11), fg_color="#2b2b2b", padx=10)
        status_bar.pack(side="bottom", fill="x")
        self.set_status("Ready.")

        # Configure main window weights
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # Apply lock overlays to tabs the current tier cannot access
        self._tab_lock_overlays = {}
        self._apply_tab_lock_overlays(allowed)

        # Initialize stats display with saved balance
        saved_bal = self.config.get("current_balance", 0.0)
        self.update_stats_display(
            starting_balance=saved_bal, 
            projected_balance=saved_bal,
            current_balance=saved_bal
        )

    def refresh_dashboard_bundles(self):
        """Scans for available strategy bundles (.json and .spine) and updates the dropdown."""
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        if not os.path.exists(bundles_dir):
            os.makedirs(bundles_dir, exist_ok=True)
            
        json_bundles = glob.glob(os.path.join(bundles_dir, "*.json"))
        spine_bundles = glob.glob(os.path.join(bundles_dir, "*.spine"))
        all_bundles = json_bundles + spine_bundles
        
        # Use the authoritative license_tier set after auth
        user_tier = getattr(self, "license_tier", "FREE")
        TIER_LEVELS = {"FREE": 0, "BASIC": 1, "PLUS": 2, "PRO": 3, "ADMIN": 99}
        user_level = TIER_LEVELS.get(user_tier.upper(), 0)

        # FREE tier has no bundle access at all
        if user_level == 0:
            self.dashboard_bundle_dropdown.configure(values=["No Bundles Found"])
            self._dashboard_bundle_master = ["No Bundles Found"]
            self.dashboard_bundle_var.set("No Bundles Found")
            return

        valid_bundles = []
        for b_path in all_bundles:
            try:
                bundle_id = None
                if b_path.endswith(".json"):
                    with open(b_path, "r") as f:
                        data = json.load(f)
                        bundle_id = data.get("bundle_id")
                elif b_path.endswith(".spine"):
                    from core.encryption import decrypt_strategy_data
                    with open(b_path, "rb") as f:
                        encrypted_bytes = f.read()
                    data = decrypt_strategy_data(encrypted_bytes)
                    if data and isinstance(data, dict):
                        bundle_id = data.get("bundle_id")

                # Locally-created bundles are always visible; marketplace bundles need entitlements
                is_local = (data.get("source") == "local") if data else False
                if not is_local and user_level < TIER_LEVELS["ADMIN"]:
                    entitlements = getattr(self.license_manager, "entitlements", [])
                    if not bundle_id or bundle_id not in entitlements:
                        logger.info(f"Skipping bundle {os.path.basename(b_path)} - not in entitlements (bundle_id={bundle_id})")
                        continue

                valid_bundles.append(b_path)
            except Exception as e:
                logger.error(f"Error reading bundle {b_path}: {e}")
                
        bundle_names = sorted(set(os.path.splitext(os.path.basename(b))[0] for b in valid_bundles))
        
        if not bundle_names:
            bundle_names = ["No Bundles Found"]
            
        self.dashboard_bundle_dropdown.configure(values=bundle_names)
        # Keep the type-to-search master list in sync with the full set.
        self._dashboard_bundle_master = list(bundle_names)
        if hasattr(self, 'dashboard_bundle_var'):
            current = self.dashboard_bundle_var.get()
            if bundle_names == ["No Bundles Found"]:
                self.dashboard_bundle_var.set("No Bundles Found")
            elif current in bundle_names:
                # Preserve user's selection across refresh (e.g. after Save).
                self.dashboard_bundle_var.set(current)
            else:
                self.dashboard_bundle_var.set("Select Bundle...")
        # Re-render favorites pill-bar — newly imported or entitled bundles
        # become available immediately, and previously-favorited bundles that
        # are no longer entitled drop out of the visible pills.
        if getattr(self, "_dashboard_bundle_pills_container", None) is not None:
            try:
                self._render_dashboard_bundle_bar()
            except Exception:
                pass

    def _refresh_botcontrol_bundles(self):
        """Refresh the Bot Control bundle dropdown — shows ALL local bundles (no entitlement filter)."""
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        os.makedirs(bundles_dir, exist_ok=True)

        json_bundles = glob.glob(os.path.join(bundles_dir, "*.json"))
        spine_bundles = glob.glob(os.path.join(bundles_dir, "*.spine"))
        bundle_names = sorted(set(
            os.path.splitext(os.path.basename(b))[0]
            for b in json_bundles + spine_bundles
        ))

        if not bundle_names:
            bundle_names = ["No Bundles Found"]

        self.botcontrol_bundle_dropdown.configure(values=bundle_names)
        # Type-to-search (idempotent — safe to call on every refresh).
        self._botcontrol_bundle_master = list(bundle_names)
        self._make_combobox_searchable(self.botcontrol_bundle_dropdown, "_botcontrol_bundle_master")
        current = self.botcontrol_bundle_var.get()
        if bundle_names == ["No Bundles Found"]:
            self.botcontrol_bundle_var.set("No Bundles Found")
        elif current in bundle_names:
            # Preserve user's selection across refresh (e.g. after Save).
            self.botcontrol_bundle_var.set(current)
        else:
            self.botcontrol_bundle_var.set("Select Bundle...")

    def _on_botcontrol_bundle_select(self, selection):
        """Handle bundle selection from Bot Control dropdown — loads it via load_bundle logic."""
        if not selection or selection in ("Select Bundle...", "No Bundles Found"):
            return

        # Snapshot the current manual Bot Control config BEFORE the bundle
        # overwrites it, so switching back to a single strategy restores it.
        self._snapshot_manual_config()

        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        # Prefer .json over .spine for local usage
        json_path = os.path.join(bundles_dir, f"{selection}.json")
        spine_path = os.path.join(bundles_dir, f"{selection}.spine")

        if os.path.exists(json_path):
            filepath = json_path
        elif os.path.exists(spine_path):
            filepath = spine_path
        else:
            messagebox.showerror("Error", f"Bundle file not found: {selection}")
            return

        try:
            data = None
            if filepath.endswith(".spine"):
                from core.encryption import decrypt_strategy_data
                with open(filepath, "rb") as f:
                    data = decrypt_strategy_data(f.read())
                if data is None:
                    messagebox.showerror("Error", "Failed to decrypt bundle.")
                    return
            else:
                with open(filepath, "r") as f:
                    data = json.load(f)

            bundle_name = data.get("name") or data.get("meta", {}).get("name") or selection

            # Handle rotation preset format
            if "strategies_string" in data and "strategy_config" not in data:
                if hasattr(self, 'rotation_strategies_var'):
                    self.rotation_strategies_var.set(data["strategies_string"])
                self._select_strategy_source('bundle')
                self.set_status(f"Loaded preset: {bundle_name}")
                return

            # Apply full bundle (reuse same logic as load_bundle)
            if "dynamic_rules" in data:
                self.dynamic_rules = data["dynamic_rules"]
                self.config["dynamic_rules"] = self.dynamic_rules
                if hasattr(self, 'refresh_dynamic_rules_listbox'):
                    self.refresh_dynamic_rules_listbox()

            strat_conf = data.get("strategy_config", {})
            if "strategy_name" in strat_conf and hasattr(self, 'auto_roulette_strategy_var'):
                self.auto_roulette_strategy_var.set(strat_conf["strategy_name"])
            if "progression_type" in strat_conf and hasattr(self, 'auto_roulette_progression_var'):
                self.auto_roulette_progression_var.set(strat_conf["progression_type"])
            if "k_value" in strat_conf and hasattr(self, 'auto_roulette_k_var'):
                self.auto_roulette_k_var.set(strat_conf["k_value"])
            if "rotation_list_str" in strat_conf and hasattr(self, 'rotation_strategies_var'):
                self.rotation_strategies_var.set(strat_conf["rotation_list_str"])
            if "rotation_mode" in strat_conf and hasattr(self, 'rotation_mode_var'):
                self.rotation_mode_var.set(strat_conf["rotation_mode"])
            if "rotation_trigger" in strat_conf and hasattr(self, 'rotation_trigger_var'):
                self.rotation_trigger_var.set(strat_conf["rotation_trigger"])

            bet_conf = data.get("betting_config", {})
            if "base_bet" in bet_conf:
                self.config["base_bet"] = float(bet_conf["base_bet"])
            if "max_loss" in bet_conf:
                raw_ml = bet_conf["max_loss"]
                current_bal = float(self.config.get("current_balance", 0))
                parsed_ml = self.parse_hybrid_value(raw_ml, current_bal)
                if parsed_ml and parsed_ml > 0:
                    self.config["max_loss"] = parsed_ml
                elif isinstance(raw_ml, (int, float)):
                    self.config["max_loss"] = float(raw_ml)
                else:
                    self.config["max_loss"] = 100.0

            _bc_fields = {
                "max_bet": (float, 100.0), "num_sessions": (int, 1),
                "min_gap_minutes": (int, 30), "max_gap_minutes": (int, 120),
                "profit_target": (float, 0), "enable_trailing_stop": (bool, False),
                "trailing_stop_amount": (float, 0), "session_ext_after_win": (bool, False),
                "session_ext_at_high": (bool, False), "max_extension_rounds": (int, 20),
                "extension_give_up_amount": (float, 50.0), "enable_global_stop": (bool, False),
                "global_profit_stop": (float, 0), "global_stop_loss": (float, 0),
                "enable_escalation_on_loss": (bool, False),
                "escalation_multiplier": (float, 2.0),
                "escalation_max_steps": (int, 4),
                "escalation_per_step": (str, ""),
            }
            for key, (cast, _default) in _bc_fields.items():
                if key in bet_conf:
                    self.config[key] = cast(bet_conf[key])

            save_config(self.config)
            self._select_strategy_source('bundle')
            self.set_status(f"Bundle loaded: {bundle_name}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load bundle: {e}")
            logger.error(f"Bot Control Load Bundle Error: {e}")

    def import_bundle_to_dashboard(self):
        """Open a file dialog to import a .json or .spine bundle file into the bundles directory."""
        filepath = filedialog.askopenfilename(
            title="Import Wager Bundle",
            filetypes=[("Encrypted Bundles", "*.spine"), ("JSON Bundles", "*.json"), ("All Files", "*.*")],
            initialdir=os.path.expanduser("~")
        )
        if not filepath:
            return
        
        try:
            bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
            os.makedirs(bundles_dir, exist_ok=True)
            
            filename = os.path.basename(filepath)
            dest = os.path.join(bundles_dir, filename)
            
            if filepath.endswith(".spine"):
                # Validate that we can decrypt it before importing
                from core.encryption import decrypt_strategy_data
                with open(filepath, "rb") as f:
                    encrypted_data = f.read()
                test_data = decrypt_strategy_data(encrypted_data)
                if test_data is None:
                    messagebox.showerror("Import Error", "Cannot decrypt this bundle.\nThe file may be corrupt or from a different version.")
                    return
                # Verify Entitlements
                bundle_id = test_data.get("bundle_id")
                user_tier = getattr(self, "license_tier", "FREE")
                if bundle_id and user_tier != "ADMIN":
                    if bundle_id not in self.license_manager.entitlements:
                        self._show_upgrade_dialog("BASIC")
                        return
                
                # Copy encrypted file as-is (DO NOT write decrypted to disk)
                import shutil
                shutil.copy2(filepath, dest)
                bundle_label = test_data.get("name", os.path.splitext(filename)[0])
            elif filepath.endswith(".json"):
                with open(filepath, "r") as f:
                    data = json.load(f)
                with open(dest, "w") as f:
                    json.dump(data, f, indent=2)
                bundle_label = data.get("name", os.path.splitext(filename)[0])
            else:
                messagebox.showerror("Import Error", "Unsupported file type. Use .spine or .json files.")
                return
            
            # Refresh dropdown and auto-select
            self.refresh_dashboard_bundles()
            bundle_name = os.path.splitext(filename)[0]
            self.dashboard_bundle_var.set(bundle_name)
            self.on_dashboard_bundle_select(bundle_name)
            
            self.log_to_dashboard(f"Imported bundle: {bundle_label}")
            messagebox.showinfo("Import Successful", f"Bundle '{bundle_label}' imported and ready.")
            
        except json.JSONDecodeError:
            messagebox.showerror("Import Error", "Invalid JSON file. Please check the bundle format.")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import bundle:\n{e}")
            logger.error(f"Bundle import error: {e}")

    def remove_selected_bundle(self):
        """Delete the currently selected bundle file after confirmation."""
        choice = self.dashboard_bundle_var.get()
        if choice in ("Select Bundle...", "No Bundles Found", ""):
            messagebox.showwarning("No Bundle Selected", "Please select a bundle to remove.")
            return

        if not messagebox.askyesno(
            "Remove Bundle",
            f"Delete bundle '{choice}'?\n\nThis cannot be undone.",
            icon="warning",
        ):
            return

        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        removed = False
        for ext in (".spine", ".json"):
            path = os.path.join(bundles_dir, f"{choice}{ext}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed = True
                except Exception as e:
                    messagebox.showerror("Error", f"Could not delete file:\n{e}")
                    return
                break

        if removed:
            self.log_to_dashboard(f"Removed bundle: {choice}")
            self.refresh_dashboard_bundles()
        else:
            messagebox.showerror("Error", f"Bundle file for '{choice}' not found.")

    def open_shop(self):
        """Open the SpinEdge strategy shop in the default browser."""
        import webbrowser
        shop_url = "https://spinedge.io/shop"  # Placeholder — update with real URL
        webbrowser.open(shop_url)
        self.log_to_dashboard("Opened strategy shop")

    def on_dashboard_bundle_select(self, choice):
        """Handles selection of a bundle from the dashboard dropdown."""
        if choice in ["Select Bundle...", "No Bundles Found", ""]:
            return

        # Snapshot the current manual Bot Control config BEFORE the bundle
        # overwrites it, so switching back to a single strategy restores it.
        self._snapshot_manual_config()

        # Load the selected bundle — prefer .json (local edits) over .spine
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        spine_path = os.path.join(bundles_dir, f"{choice}.spine")
        json_path = os.path.join(bundles_dir, f"{choice}.json")

        try:
            data = None

            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    data = json.load(f)
            elif os.path.exists(spine_path):
                # Decrypt .spine in memory (never written to disk as plaintext)
                from core.encryption import decrypt_strategy_data
                with open(spine_path, "rb") as f:
                    encrypted_bytes = f.read()
                data = decrypt_strategy_data(encrypted_bytes)
                if data is None:
                    messagebox.showerror("Error", f"Failed to decrypt bundle '{choice}'.\nFile may be corrupt.")
                    return
                # Verify Entitlements
                bundle_id = data.get("bundle_id")
                user_tier = getattr(self, "license_tier", "FREE")
                if bundle_id and user_tier != "ADMIN":
                    if bundle_id not in self.license_manager.entitlements:
                        self._show_upgrade_dialog("BASIC")
                        self.dashboard_bundle_var.set("Select Bundle...")
                        return
            else:
                messagebox.showerror("Error", f"Bundle '{choice}' not found.")
                return
        
            # Detect format: rotation preset vs full bundle
            if "strategies_string" in data and "strategy_config" not in data:
                # --- ROTATION PRESET FORMAT ---
                rotation_str = data["strategies_string"]
                self.config["rotation_strategies"] = rotation_str
                self.config["enable_strategy_rotation"] = True
                if hasattr(self, 'rotation_strategies_var'):
                    self.rotation_strategies_var.set(rotation_str)
                if hasattr(self, 'enable_strategy_rotation_var'):
                    self.enable_strategy_rotation_var.set(True)
                # Snapshot current config values as "bundle values" for risk profile restore
                self._bundle_base_bet = float(self.config.get("base_bet", 1.0))
                self._bundle_max_loss = float(self.config.get("max_loss", 100.0))
                save_config(self.config)
                bundle_label = data.get("name", choice)
                self.log_to_dashboard(f"Loaded rotation list: {bundle_label}")
            else:
                # --- FULL BUNDLE FORMAT ---
                # 1. Apply Dynamic Rules
                if "dynamic_rules" in data:
                    self.dynamic_rules = data["dynamic_rules"]
                    self.config["dynamic_rules"] = self.dynamic_rules
                    
                # 2. Apply Strategy Settings
                strat_conf = data.get("strategy_config", {})
                if "strategy_name" in strat_conf and hasattr(self, 'auto_roulette_strategy_var'):
                    self.auto_roulette_strategy_var.set(strat_conf["strategy_name"])
                if "progression_type" in strat_conf and hasattr(self, 'auto_roulette_progression_var'):
                    self.auto_roulette_progression_var.set(strat_conf["progression_type"])
                if "k_value" in strat_conf and hasattr(self, 'auto_roulette_k_var'):
                    self.auto_roulette_k_var.set(strat_conf["k_value"])
                
                # Rotation — write to config directly to avoid triggering dropdown callback
                if "rotation_list_str" in strat_conf:
                    self.config["rotation_strategies"] = strat_conf["rotation_list_str"]
                    if hasattr(self, 'rotation_strategies_var'):
                        self.rotation_strategies_var.set(strat_conf["rotation_list_str"])
                if "rotation_mode" in strat_conf:
                    self.config["rotation_mode"] = strat_conf["rotation_mode"]
                    if hasattr(self, 'rotation_mode_var'):
                        self.rotation_mode_var.set(strat_conf["rotation_mode"])
                if "rotation_trigger" in strat_conf:
                    self.config["rotation_trigger"] = strat_conf["rotation_trigger"]
                    if hasattr(self, 'rotation_trigger_var'):
                        self.rotation_trigger_var.set(strat_conf["rotation_trigger"])
                    if hasattr(self, 'switch_on_loss_var'):
                        self.switch_on_loss_var.set(strat_conf["rotation_trigger"] == "on_loss")
                        self._on_switch_on_loss_toggled()
                if "switch_after_n_losses" in strat_conf:
                    self.config["switch_after_n_losses"] = int(strat_conf["switch_after_n_losses"])
                    if hasattr(self, 'switch_after_n_losses_var'):
                        self.switch_after_n_losses_var.set(int(strat_conf["switch_after_n_losses"]))
                if "carry_progression_on_switch" in strat_conf:
                    self.config["carry_progression_on_switch"] = bool(strat_conf["carry_progression_on_switch"])
                    if hasattr(self, 'carry_progression_var'):
                        self.carry_progression_var.set(bool(strat_conf["carry_progression_on_switch"]))
                if "reset_rotation_on_session" in strat_conf:
                    self.config["reset_rotation_on_session"] = bool(strat_conf["reset_rotation_on_session"])
                    if hasattr(self, 'reset_rotation_on_session_var'):
                        self.reset_rotation_on_session_var.set(bool(strat_conf["reset_rotation_on_session"]))
                if "rotation_progression_override" in strat_conf:
                    self.config["rotation_progression_override"] = bool(strat_conf["rotation_progression_override"])
                    if hasattr(self, 'rotation_progression_override_var'):
                        self.rotation_progression_override_var.set(bool(strat_conf["rotation_progression_override"]))
                if "filter_by_regime" in strat_conf:
                    self.config["filter_by_regime"] = bool(strat_conf["filter_by_regime"])
                    if hasattr(self, 'filter_regime_var'):
                        self.filter_regime_var.set(bool(strat_conf["filter_by_regime"]))

                # Conditional-trigger config (see core/triggers.py). Without
                # this, picking a bundle from the dashboard dropdown was
                # leaving triggers_config empty — _init_trigger_engine then
                # bailed out and no triggers fired even when the bundle
                # clearly had selection_mode='conditional'. Mirrors what the
                # toolbar load_bundle / bundle creator load paths already do.
                self.triggers_config = {
                    "selection_mode": (strat_conf.get("selection_mode") or "rotation"),
                    "triggers":       dict(strat_conf.get("triggers") or {}),
                    "global_trigger": strat_conf.get("global_trigger") or None,
                    "tiebreaker":     (strat_conf.get("tiebreaker") or "coldest"),
                    "fallback":       (strat_conf.get("fallback") or "stay"),
                }
                _tc = self.triggers_config
                logger.info(f"[Dashboard] Loaded triggers_config: mode={_tc['selection_mode']}, "
                            f"per-strategy={len(_tc['triggers'])}, "
                            f"global={_tc['global_trigger']}, "
                            f"tiebreaker={_tc['tiebreaker']}, fallback={_tc['fallback']}")

                # 3. Apply Betting Config
                bet_conf = data.get("betting_config", {})
                if "base_bet" in bet_conf:
                    self.config["base_bet"] = float(bet_conf["base_bet"])
                    if hasattr(self, 'base_bet_var'):
                        self.base_bet_var.set(str(self.config["base_bet"]))
                if "max_loss" in bet_conf:
                    raw_ml = bet_conf["max_loss"]
                    current_bal = float(self.config.get("current_balance", 0))
                    parsed_ml = self.parse_hybrid_value(raw_ml, current_bal)
                    # Always store a numeric value; fall back to 100.0 if parsing fails
                    if parsed_ml and parsed_ml > 0:
                        self.config["max_loss"] = parsed_ml
                    elif isinstance(raw_ml, (int, float)):
                        self.config["max_loss"] = float(raw_ml)
                    else:
                        self.config["max_loss"] = 100.0
                    if hasattr(self, 'max_loss_var'):
                        # Display the original string (e.g. "2%") so user sees intent
                        self.max_loss_var.set(str(raw_ml))

                # 4. Apply extended betting config (v1.1+ bundles) to config AND
                # the visible GUI vars via the shared helper (same path the
                # toolbar load uses), so both loaders stay in lockstep.
                self._apply_betting_config(bet_conf)

                # Sync session_duration -> session_duration_minutes (run_bot reads _minutes key)
                if "session_duration" in bet_conf:
                    self.config["session_duration_minutes"] = int(bet_conf["session_duration"])
                    if hasattr(self, 'session_duration_var'):
                        self.session_duration_var.set(str(int(bet_conf["session_duration"])))

                # Snapshot the bundle's native bet values so "Use Bundle Values" can
                # always restore them even after a risk profile override has been applied.
                self._bundle_base_bet = float(self.config.get("base_bet", 1.0))
                self._bundle_max_loss = float(self.config.get("max_loss", 100.0))

                save_config(self.config)
                bundle_label = data.get("name", choice)
                self.log_to_dashboard(f"Loaded bundle: {bundle_label}")
                
            # Reset Risk Profile to use the bundle's native values when a new bundle is loaded
            if hasattr(self, 'dash_risk_profile_var'):
                self.dash_risk_profile_var.set("Use Bundle Values")
                self.update_risk_profile_preview()
            # A fresh bundle clears any prior user override and resets active mode.
            self._user_override_base_bet = None
            self._user_override_max_loss = None
            self._active_risk_profile = "Bundle"

            # ── This is now a bundle-driven run (strict XOR) ──────────────────
            # Mark the source and, if a session is live, queue a thread-safe
            # engine re-arm. The round-boundary handler (_apply_pending_engine_rearm)
            # rebuilds the rotation list + trigger engine + live StrategyEngine
            # from the freshly-loaded bundle. Previously this re-inited rotation
            # synchronously from the GUI thread — racing the run loop — and only
            # queued a single-strategy swap, so the live engine kept running the
            # prior bundle's members (the reported "strategy leaking" bug).
            self._select_strategy_source('bundle')

            # Refresh the live SessionManager's risk limits from the new bundle.
            # (Independent of the strategy rebuild; safe to apply directly since
            # it only mutates SessionManager attributes from current config.)
            if getattr(self, "bot_running", False):
                try:
                    if hasattr(self, "update_runtime_limits"):
                        self.update_runtime_limits()
                    sm = getattr(self, "session_manager", None)
                    if sm is not None:
                        sess_stops_active = self.enable_session_stops_var.get() if hasattr(self, "enable_session_stops_var") else False
                        try:
                            sm.max_win_streak = (int(self.max_session_wins_streak_var.get() or 0)
                                                 if sess_stops_active and hasattr(self, "max_session_wins_streak_var") else 0)
                            sm.max_loss_streak = (int(self.max_session_losses_streak_var.get() or 0)
                                                  if sess_stops_active and hasattr(self, "max_session_losses_streak_var") else 0)
                            sm.session_duration = int(float(self.config.get("session_duration_minutes", 60))) * 60
                            sm.max_extension_rounds = int(self.config.get("max_extension_rounds", 20))
                            sm.extension_give_up_amount = float(self.config.get("extension_give_up_amount", 50.0))
                            if hasattr(sm, "config") and sm.config:
                                sm.config["session_ext_after_win"] = bool(self.config.get("session_ext_after_win", False))
                                sm.config["session_ext_at_high"] = bool(self.config.get("session_ext_at_high", False))
                        except Exception as e:
                            logger.error(f"SessionManager limit refresh failed: {e}")
                except Exception as e:
                    logger.error(f"Mid-session bundle limit refresh failed: {e}")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to load bundle: {e}")
            logger.error(f"Load Bundle Error: {e}")

    def start_dashboard_session(self):
        """Start button logic for Dashboard"""
        # 1. Validation
        if self.dashboard_bundle_var.get() in ["Select Bundle...", "No Bundles Found", ""]:
            messagebox.showwarning("Setup Incomplete", "Please select a Wager Bundle first.")
            return
            
        if not self.selected_window_title:
             self.select_window_dialog()
             if not self.selected_window_title:
                 return # User cancelled
        
        # 2. Ensure Rotation is Enabled (logic override)
        self.config["enable_strategy_rotation"] = True
        if hasattr(self, 'enable_strategy_rotation_var'):
            self.enable_strategy_rotation_var.set(True)
        
        # 3. Apply Risk Profile Overrides
        if hasattr(self, 'dash_risk_profile_var'):
            profile = self.dash_risk_profile_var.get()
            if profile == "Use Bundle Values":
                # Restore bundle-native values, but only when the user hasn't
                # manually edited the textbox. If the live var differs from the
                # bundle snapshot, treat it as a deliberate override and keep it.
                def _user_edited(var_name, snapshot):
                    if snapshot is None or not hasattr(self, var_name):
                        return False
                    try:
                        return abs(float(getattr(self, var_name).get()) - float(snapshot)) > 1e-9
                    except (ValueError, TypeError):
                        return True

                bb = getattr(self, '_bundle_base_bet', None)
                sl = getattr(self, '_bundle_max_loss', None)
                if bb is not None and not _user_edited('base_bet_var', bb):
                    self.config["base_bet"] = bb
                    if hasattr(self, 'base_bet_var'): self.base_bet_var.set(str(bb))
                if sl is not None and not _user_edited('max_loss_var', sl):
                    self.config["max_loss"] = sl
                    if hasattr(self, 'max_loss_var'): self.max_loss_var.set(str(sl))
            else:
                balance = float(self.config.get("current_balance", 1000.0))
                # Set minimum safe floors
                if balance < 10.0: balance = 1000.0 # Extreme fallback to prevent division errors
                
                # Multipliers: (Base Bet %, Stop Loss %)
                multipliers = {
                    "Auto (Smart Default)": (0.001, 0.15),
                    "Conservative (0.5% Risk)": (0.005, 0.10),
                    "Balanced (1% Risk)": (0.01, 0.20),
                    "Aggressive (5.0% Risk)": (0.05, 0.40)
                }
                
                if profile in multipliers:
                    bb_pct, sl_pct = multipliers[profile]
                    
                    # Calculate & Apply Floors
                    calc_base = balance * bb_pct
                    calc_base = max(0.10, round(calc_base, 2)) # Hard floor $0.10 minimum bet
                    
                    calc_stop = balance * sl_pct
                    calc_stop = round(calc_stop, 2)
                    
                    self.config["base_bet"] = calc_base
                    self.config["max_loss"] = calc_stop
                    
                    # Sync to Bot Control UI variables
                    if hasattr(self, 'base_bet_var'): self.base_bet_var.set(str(calc_base))
                    if hasattr(self, 'max_loss_var'): self.max_loss_var.set(str(calc_stop))
                    
                    self.log_to_dashboard(f"Applied {profile.split()[0]} Risk: Bet ${calc_base} | Stop ${calc_stop}")
        
        # 4. Trigger Main Start
        self.start_bot()
        
        # 4. Update Dashboard UI State (only if bot actually started)
        # Give start_bot a moment to set bot_running via its thread
        self.root.after(500, self._update_dashboard_after_start)

    def _update_dashboard_after_start(self):
        """Deferred UI update after start_bot — only enables buttons if bot is running."""
        if self.bot_running:
            if hasattr(self, 'dash_start_btn'):
                self.dash_start_btn.configure(state="disabled", fg_color="gray")
            if hasattr(self, 'dash_stop_btn'):
                self.dash_stop_btn.configure(state="normal", fg_color="#c0392b")
            if hasattr(self, 'dash_pause_btn'):
                self.dash_pause_btn.configure(state="normal", fg_color="#f39c12")
            if hasattr(self, 'dash_status_dot'):
                self.dash_status_dot.configure(text_color="#2ecc71")
                self.dash_status_text.configure(text="  Bot running", text_color="#2ecc71")
            self.log_to_dashboard(f"Session started — Bundle: {self.dashboard_bundle_var.get()}")
            # Refresh HUD label color to green (live)
            if hasattr(self, '_risk_hud_active_label'):
                current_text = self._risk_hud_active_label.cget("text")
                self._risk_hud_active_label.configure(text=current_text, text_color="#4ade80")
        else:
            # Bot didn't actually start — reset to ready state
            if hasattr(self, 'dash_start_btn'):
                self.dash_start_btn.configure(state="normal", fg_color="#27ae60")
            self.log_to_dashboard("Start failed — check license or settings")

    def _edit_risk_balance(self):
        """Inline dialog to update the balance used for risk profile calculations."""
        current = self.config.get("current_balance", 0.0)

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Update Balance")
        dialog.geometry("300x160")
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)

        ctk.CTkLabel(dialog, text="Current Balance ($)", font=("Segoe UI", 11, "bold")).pack(pady=(16, 4))

        entry = ctk.CTkEntry(dialog, font=("Segoe UI", 13), width=180, justify="center")
        entry.insert(0, f"{current:.2f}")
        entry.select_range(0, "end")
        entry.pack(pady=(0, 12))
        entry.focus_set()

        def _apply():
            try:
                val = float(entry.get().strip().lstrip("$").replace(",", ""))
                if val <= 0:
                    raise ValueError
            except ValueError:
                entry.configure(border_color="red")
                return
            self.config["current_balance"] = val
            try:
                save_config(self.config)
            except Exception:
                pass
            if hasattr(self, "dash_risk_balance_label"):
                self.dash_risk_balance_label.configure(text=f"Balance: ${val:,.2f}")
            self.update_risk_profile_preview()
            dialog.destroy()

        entry.bind("<Return>", lambda _e: _apply())
        ctk.CTkButton(dialog, text="Save", command=_apply, width=100,
                      fg_color="#27ae60", hover_color="#2ecc71").pack()

        dialog.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass
        dialog.wait_window()

    def update_risk_profile_preview(self, *args):
        """Calculates and displays the live $ amounts for the selected Risk Profile based on current balance."""
        if not hasattr(self, 'dash_risk_profile_var') or not hasattr(self, 'dash_risk_preview_label'):
            return

        # Keep the balance label in sync with config
        if hasattr(self, "dash_risk_balance_label"):
            bal_display = self.config.get("current_balance", 0.0)
            self.dash_risk_balance_label.configure(text=f"Balance: ${float(bal_display):,.2f}")

        profile = self.dash_risk_profile_var.get()
        if profile == "Use Bundle Values":
            # Prefer the live textbox value when the user has edited it, so the
            # preview matches what start_dashboard_session will actually run with.
            bundle_bb = getattr(self, '_bundle_base_bet', None)
            bundle_sl = getattr(self, '_bundle_max_loss', None)

            def _live_or_bundle(var_name, snapshot):
                if not hasattr(self, var_name):
                    return snapshot, False
                try:
                    live = float(getattr(self, var_name).get())
                except (ValueError, TypeError):
                    return snapshot, False
                if snapshot is None:
                    return live, False
                edited = abs(live - float(snapshot)) > 1e-9
                return (live if edited else snapshot), edited

            bb, bb_edited = _live_or_bundle('base_bet_var', bundle_bb)
            sl, sl_edited = _live_or_bundle('max_loss_var', bundle_sl)

            if bb is None: bb = self.config.get('base_bet', '--')
            if sl is None: sl = self.config.get('max_loss', '--')

            overridden = bb_edited or sl_edited
            label = "Override" if overridden else "Bundle Values"
            color = "#f59e0b" if overridden else "gray60"
            self.dash_risk_preview_label.configure(text=f"{label}: Base ${bb} | Stop ${sl}", text_color=color)
            return

        balance = float(self.config.get("current_balance", 0.0))
        if balance <= 0:
            self.dash_risk_preview_label.configure(text="Requires valid balance to calculate.", text_color="#f39c12")
            return

        multipliers = {
            "Auto (Smart Default)": (0.001, 0.15),
            "Conservative (0.5% Risk)": (0.005, 0.10),
            "Balanced (1% Risk)": (0.01, 0.20),
            "Aggressive (5.0% Risk)": (0.05, 0.40)
        }

        if profile in multipliers:
            bb_pct, sl_pct = multipliers[profile]
            calc_base = max(0.10, round(balance * bb_pct, 2))
            calc_stop = round(balance * sl_pct, 2)
            self.dash_risk_preview_label.configure(text=f"Projected: Base ${calc_base} | Stop ${calc_stop}", text_color="#2ecc71")

    # ── Runtime Risk Profile Switcher ─────────────────────────────────────────

    _RISK_MULTIPLIERS = {
        # name: (base_bet_pct, stop_loss_pct)
        "Bundle": None,           # Use bundle-native values
        "Cons.":  (0.005, 0.10),
        "Bal.":   (0.01,  0.20),
        "Aggr.":  (0.05,  0.40),
        "Smart":  (0.001, 0.15),
    }

    _RISK_HUD_COLORS = {
        "Bundle": ("#4a5568", "#718096"),
        "Cons.":  ("#1d4ed8", "#2563eb"),
        "Bal.":   ("#166634", "#16a34a"),
        "Aggr.":  ("#991b1b", "#dc2626"),
        "Smart":  ("#5b21b6", "#7c3aed"),
    }

    def _risk_hud_highlight(self, active_name: str):
        """Visually mark the active risk button with a white border glow."""
        if not hasattr(self, '_risk_hud_btns'):
            return
        for name, btn in self._risk_hud_btns.items():
            fg, hover = self._RISK_HUD_COLORS.get(name, ("#4a5568", "#718096"))
            if name == active_name:
                btn.configure(border_width=2, border_color="white")
            else:
                btn.configure(border_width=0)

    def _on_bundle_textbox_write(self, var_name: str, snapshot_attr: str, override_attr: str):
        """Trace listener for base_bet/max_loss textboxes.

        - Always refreshes the Dashboard risk preview label.
        - When the active risk profile is "Bundle" (or unset), captures the new
          value as a user override if it differs from the bundle snapshot, so
          a later Smart→Bundle round-trip can restore it. Programmatic writes
          done by _runtime_switch_risk are skipped via _suppress_override_capture.
        """
        if hasattr(self, 'dash_risk_preview_label'):
            self.update_risk_profile_preview()

        if getattr(self, '_suppress_override_capture', False):
            return
        if getattr(self, '_active_risk_profile', 'Bundle') != 'Bundle':
            return
        if not hasattr(self, var_name):
            return
        try:
            live = float(getattr(self, var_name).get())
        except (ValueError, TypeError):
            return
        snapshot = getattr(self, snapshot_attr, None)
        if snapshot is None:
            # No bundle loaded yet — nothing to override against.
            setattr(self, override_attr, None)
            return
        if abs(live - float(snapshot)) > 1e-9:
            setattr(self, override_attr, live)
        else:
            # Edited back to bundle default — clear the override.
            setattr(self, override_attr, None)

    def _runtime_switch_risk(self, profile_name: str):
        """
        Switch risk profile at runtime — works whether the bot is running or idle.
        Updates base_bet in config (read live by the bet loop) and stop_loss
        directly on session_manager if a session is active.
        """
        multiplier = self._RISK_MULTIPLIERS.get(profile_name)

        # Suppress the textbox-trace listener for the duration of this switch
        # so our programmatic writes (Smart/Cons/etc.) don't get mistaken for a
        # user override and clobber the real one.
        self._suppress_override_capture = True
        try:
            if multiplier is None:
                # Restore bundle path. Prefer the user's override (captured while
                # they were in "Bundle" mode) so a Smart→Bundle round-trip keeps
                # whatever they had typed before switching away.
                bb = getattr(self, '_user_override_base_bet', None)
                if bb is None:
                    bb = getattr(self, '_bundle_base_bet', None)
                sl = getattr(self, '_user_override_max_loss', None)
                if sl is None:
                    sl = getattr(self, '_bundle_max_loss', None)
                if bb is None:
                    bb = float(self.config.get("base_bet", 1.0))
                if sl is None:
                    sl = float(self.config.get("max_loss", 100.0))
                src = "user override" if (
                    getattr(self, '_user_override_base_bet', None) is not None or
                    getattr(self, '_user_override_max_loss', None) is not None
                ) else "bundle snapshot"
                print(f"[RiskSwitch] Restoring bundle values from {src}: bb={bb}, sl={sl}")
            else:
                balance = float(self.config.get("current_balance", 0.0))
                if balance <= 0:
                    self.log_to_dashboard("⚠ Set your balance first to use risk profiles.")
                    return
                bb_pct, sl_pct = multiplier
                bb = max(0.10, round(balance * bb_pct, 2))
                sl = round(balance * sl_pct, 2)

            # 1. Update live config (bet loop reads this on every round)
            bb = float(bb)
            sl = float(sl)
            self.config["base_bet"] = bb
            self.config["max_loss"] = sl
            if hasattr(self, 'base_bet_var'):
                self.base_bet_var.set(str(bb))
            if hasattr(self, 'max_loss_var'):
                self.max_loss_var.set(str(sl))

            # 2. Push to running strategy & session_manager immediately
            if hasattr(self, '_live_strategy') and self._live_strategy:
                self._live_strategy.base_bet = bb
                if hasattr(self._live_strategy, 'progression'):
                    self._live_strategy.progression.base_bet = bb
                    # For DynamicProgressionStrategy, only update base_bet — do NOT reset
                    # current_bet or martingale_level, as it manages its own progression
                    # state via session_high rules. Resetting would break mid-recovery martingale.
                    if self._live_strategy.progression.__class__.__name__ != 'DynamicProgressionStrategy':
                        self._live_strategy.progression.current_bet = bb
                        if hasattr(self._live_strategy.progression, 'martingale_level'):
                            self._live_strategy.progression.martingale_level = 0
                print(f"[RiskSwitch] Live strategy updated: base_bet={bb}")
            if hasattr(self, 'session_manager') and self.session_manager:
                self.session_manager.stop_loss = sl
            # Also update the active limit used by bot loop's dynamic config reload
            self.active_session_loss_limit = sl

            # 3. Keep dropdown in sync
            _dropdown_map = {
                "Bundle": "Use Bundle Values",
                "Cons.":  "Conservative (0.5% Risk)",
                "Bal.":   "Balanced (1% Risk)",
                "Aggr.":  "Aggressive (5.0% Risk)",
                "Smart":  "Auto (Smart Default)",
            }
            if hasattr(self, 'dash_risk_profile_var'):
                self.dash_risk_profile_var.set(_dropdown_map.get(profile_name, "Use Bundle Values"))
                self.update_risk_profile_preview()

            # 4. Update HUD label and highlight active button
            self._risk_hud_highlight(profile_name)
            if hasattr(self, '_risk_hud_active_label'):
                self._risk_hud_active_label.configure(
                    text=f"Active: {profile_name}  |  Bet: ${bb}  |  Stop: ${sl}",
                    text_color="#4ade80" if self.bot_running else "#94a3b8"
                )

            self.log_to_dashboard(f"⚡ Risk switched → {profile_name}: Bet ${bb} | Stop Loss ${sl}")
        finally:
            self._suppress_override_capture = False
            # Remember which profile is now active so the trace knows whether
            # subsequent textbox edits should be captured as a bundle override.
            self._active_risk_profile = profile_name

    # ── Session-level escalation on loss ─────────────────────────────────────

    def _refresh_escalation_preview(self) -> None:
        """Repopulate the Session Goals 'Escalation preview' table.

        Runs from the GUI thread (Tk traces or root.after). Reads the live
        var values, computes each step's scaled base-bet and session SL, and
        also the cumulative risk if every session through that step lost in
        full. Visualises both the uniform `multiplier ** step` mode and the
        per-step CSV mode that overrides it.
        """
        tree = getattr(self, "escalation_preview_tree", None)
        if tree is None:
            return
        try:
            for iid in tree.get_children():
                tree.delete(iid)
        except Exception:
            return

        def _f(s, default=0.0):
            try:
                return float(s)
            except (TypeError, ValueError):
                return default

        enabled = bool(self.enable_escalation_on_loss_var.get()) \
                  if hasattr(self, "enable_escalation_on_loss_var") else False

        # When a run is in progress and we've already escalated, the textbox
        # holds the *current* (escalated) values, not the initial. Treating
        # those as "step 0" would multiply an already-multiplied number.
        # Prefer the snapshot taken at start_bot for the initial — falling
        # back to the textbox when no run is active.
        running = bool(getattr(self, "bot_running", False))
        snap_bb = getattr(self, "_escalation_initial_base_bet", None)
        snap_sl = getattr(self, "_escalation_initial_max_loss", None)

        if running and snap_bb is not None:
            init_bb = float(snap_bb)
        else:
            try:
                init_bb = _f(self.base_bet_var.get(), 0.0)
            except Exception:
                init_bb = 0.0

        if running and snap_sl is not None:
            init_sl = float(snap_sl)
        else:
            try:
                ref_bal = float(self.config.get("current_balance", 0.0)) or 0.0
                init_sl = float(self.parse_hybrid_value(self.max_loss_var.get(), ref_bal) or 0.0)
            except Exception:
                init_sl = _f(self.max_loss_var.get(), 0.0)

        mult = _f(self.escalation_multiplier_var.get(), 2.0)
        max_steps = int(_f(self.escalation_max_steps_var.get(), 4))

        per_step: list[float] = []
        try:
            for tok in str(self.escalation_per_step_var.get() or "").split(","):
                tok = tok.strip()
                if not tok:
                    continue
                v = _f(tok, 0.0)
                if v > 0:
                    per_step.append(v)
        except Exception:
            per_step = []

        if per_step:
            n_rows = len(per_step)
        else:
            n_rows = max(0, max_steps)

        # Always include step 0 (initial / unscaled) as the first row
        rows = [(0, "Run start (initial)", 1.0)]
        if enabled and (per_step or mult > 1.0) and n_rows > 0:
            for i in range(1, n_rows + 1):
                if per_step:
                    scale = per_step[min(i - 1, len(per_step) - 1)]
                else:
                    scale = mult ** i
                trig = ("After 1st session SL" if i == 1 else
                        "After 2nd session SL" if i == 2 else
                        "After 3rd session SL" if i == 3 else
                        f"After {i}th session SL")
                rows.append((i, trig, scale))

        cumulative_risk = 0.0
        for step, trig, scale in rows:
            bb = round(init_bb * scale, 2)
            sl = round(init_sl * scale, 2)
            cumulative_risk += sl
            mult_str = f"×{scale:g}" if step > 0 else "×1"
            tree.insert("", "end", values=(
                step, trig, mult_str,
                f"${bb:.2f}", f"${sl:.2f}", f"${cumulative_risk:.2f}",
            ))

        # Footer-style hint row when escalation is off, so the table doesn't
        # look broken — just shows the "step 0" line and a note.
        if not enabled:
            tree.insert("", "end", values=(
                "—", "Escalation disabled (toggle to enable)", "—", "—", "—", "—",
            ))

    def _apply_session_escalation(self) -> None:
        """Adjust base_bet and session stop-loss based on the last session's outcome.

        Called between sessions, after run_bot() returns.

          - last_stop_reason == "Session stop-loss" → escalate (multiply both
            by escalation_multiplier, capped at escalation_max_steps).
          - last_stop_reason == "Session profit target" OR stop_all_sessions
            (a global target/limit was hit) → reset to the initial values
            captured in start_bot.
          - Other reasons (time limit, trailing, streak target, etc.) →
            no change; whatever level we were at is preserved.

        Writes to self.config and the textbox vars, mirroring the pattern in
        _runtime_switch_risk so guardrails and the bet loop pick up the new
        values on the next session start.
        """
        if not getattr(self, 'enable_escalation_on_loss_var', None):
            return
        if not self.enable_escalation_on_loss_var.get():
            return

        try:
            multiplier = float(self.escalation_multiplier_var.get() or 2.0)
        except (TypeError, ValueError):
            multiplier = 2.0
        try:
            max_steps = int(self.escalation_max_steps_var.get() or 4)
        except (TypeError, ValueError):
            max_steps = 4

        # Optional per-step multiplier list (CSV like "2,3,5,10"). When set,
        # each entry IS the multiplier vs the initial values for that step
        # (NOT cumulative): step 1 → init × list[0], step 2 → init × list[1],
        # etc. Number of entries determines max_steps. Falls back to the
        # uniform 'multiplier ** step' geometric scaling when blank.
        per_step: list[float] = []
        try:
            raw = (getattr(self, 'escalation_per_step_var', None).get()
                   if hasattr(self, 'escalation_per_step_var') else
                   str(self.config.get("escalation_per_step", "") or ""))
            for tok in str(raw).split(","):
                tok = tok.strip()
                if not tok:
                    continue
                v = float(tok)
                if v <= 0:
                    continue
                per_step.append(v)
        except (TypeError, ValueError, AttributeError):
            per_step = []

        if per_step:
            max_steps = len(per_step)
        elif multiplier <= 1.0:
            return  # uniform mode with multiplier <= 1 → nothing to do

        init_bb = self._escalation_initial_base_bet
        init_sl = self._escalation_initial_max_loss
        if init_bb is None or init_sl is None:
            return  # no snapshot yet — start_bot didn't run

        reason = str(getattr(self, 'last_stop_reason', '') or '')
        global_hit = bool(getattr(self, 'stop_all_sessions', False))

        # Compute the post-session global PnL. After run_bot's cleanup, the
        # offset already includes this session's PnL, so it IS the global PnL.
        post_global = float(getattr(self, 'cumulative_profit_offset', 0.0))
        peak = float(getattr(self, '_peak_global_pnl', 0.0))
        # "At the all-time high" → recovered fully → reset escalation.
        # Compare in cents so two values that both display as "$0.60" are
        # treated equal, even if internal floats drift (e.g. peak was set
        # at 0.6000000001 from cumulative_net_profit=0.40000000000009095
        # but post_global rounds back to exactly 0.60). The peak > 0 guard
        # avoids resetting when we never made any profit (peak 0, post 0 →
        # trivially at peak but not a real recovery).
        recovered_to_peak = peak > 0 and round(post_global, 2) >= round(peak, 2)

        print(f"[Escalation] Decision inputs: reason={reason!r} global_hit={global_hit} "
              f"step={self._escalation_step} post_global=${post_global:.2f} "
              f"peak=${peak:.2f} recovered_to_peak={recovered_to_peak}")

        # Decide direction
        if global_hit or reason == "Session profit target" or recovered_to_peak:
            new_step = 0
            if global_hit:
                label = "global target reached"
            elif reason == "Session profit target":
                label = "session profit target"
            else:
                label = f"recovered to peak (${post_global:.2f} ≥ ${peak:.2f})"
        elif reason == "Session stop-loss":
            if self._escalation_step >= max_steps:
                print(f"[Escalation] Cap reached ({max_steps} steps); holding at {self._escalation_step}×")
                return
            new_step = self._escalation_step + 1
            # The multiplier for `label` mirrors what we'll actually use below.
            preview_scale = per_step[new_step - 1] if per_step else (multiplier ** new_step)
            label = f"session stop-loss ×{preview_scale:g}"
        else:
            return  # neutral end (time limit, trailing, etc.) — keep current level

        # Always write on a reset path even if step is already 0 — the textbox
        # could have been edited or rotated since the last reset, so we want
        # the bot to truly start the next run from the snapshotted initials.
        is_reset = (new_step == 0 and self._escalation_step != 0) or \
                   (new_step == 0 and (
                        abs(float(self.config.get("base_bet", 0.0) or 0.0) - init_bb) > 1e-9 or
                        abs(float(self.parse_hybrid_value(self.config.get("max_loss"), self.initial_run_balance)
                                  or 0.0) - init_sl) > 1e-9
                   ))
        if new_step == self._escalation_step and not is_reset:
            return

        self._escalation_step = new_step
        if per_step and new_step >= 1:
            # Per-step list takes precedence: each entry is an absolute
            # multiplier vs the initial values, not cumulative. Step 0 is
            # always the unscaled initial (handled by new_step==0 branch).
            idx = min(new_step - 1, len(per_step) - 1)
            scale = per_step[idx]
        else:
            scale = multiplier ** new_step
        new_bb = round(init_bb * scale, 2)
        new_sl = round(init_sl * scale, 2)

        # _apply_session_escalation runs on the session worker thread, but
        # Tkinter var.set() must run on the main thread or the entry widget
        # can silently fail to redraw — which looked like "the reset didn't
        # happen" in the UI even though config was updated. Dispatch the
        # GUI-touching writes via root.after.
        def _do_writes():
            self._suppress_override_capture = True
            try:
                self.config["base_bet"] = new_bb
                self.config["max_loss"] = new_sl
                if hasattr(self, 'base_bet_var'):
                    self.base_bet_var.set(str(new_bb))
                if hasattr(self, 'max_loss_var'):
                    self.max_loss_var.set(str(new_sl))
                self.active_session_loss_limit = new_sl
            finally:
                self._suppress_override_capture = False

        try:
            self.root.after(0, _do_writes)
        except Exception:
            # Fallback for environments without a Tk root (tests etc.) — write
            # config directly. Skips the var.set so no widget is touched.
            self.config["base_bet"] = new_bb
            self.config["max_loss"] = new_sl
            self.active_session_loss_limit = new_sl

        msg = (f"🔼 Escalation step {new_step} ({label}): "
               f"base ${new_bb}, stop-loss ${new_sl}") if new_step > 0 else (
               f"🔁 Escalation reset ({label}): base ${new_bb}, stop-loss ${new_sl}")
        print(f"[Escalation] {msg}")
        try:
            self.log_simulation(msg)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────

    def cycle_tab(self, direction=1):
        """Cycle through allowed tabs."""
        try:
            # Get current tab name
            current = self.tabview.get()
            
            # We need the list of *currently created* tabs, not all possibilities.
            # self.tabview._tab_dict contains created tabs.
            # But specific order? self.tab_names might contain everything.
            # Let's filter self.tab_names by what's actually in tabview
            
            existing_tabs = [t for t in self.tab_names if t in self.tabview._tab_dict]
            
            if not existing_tabs:
                return
                
            if current in existing_tabs:
                idx = existing_tabs.index(current)
                next_idx = (idx + direction) % len(existing_tabs)
                self.tabview.set(existing_tabs[next_idx])
        except Exception as e:
            logger.error(f"Tab cycle error: {e}")

    def on_mousewheel(self, event):
        """Handle mouse wheel scrolling for all scrollable widgets"""
        widget = event.widget
        if hasattr(widget, 'yview'):
            # Windows
            if hasattr(event, 'delta'):
                if event.delta > 0:
                    widget.yview_scroll(-1, "units")
                else:
                    widget.yview_scroll(1, "units")
            # Linux
            elif event.num == 4:
                widget.yview_scroll(-1, "units")
            elif event.num == 5:
                widget.yview_scroll(1, "units")
        return "break"
    
    def on_mousewheel_scroll(self, event, widget):
        """Alternative mouse wheel handler that takes a specific widget"""
        if hasattr(widget, 'yview'):
            # Windows
            if hasattr(event, 'delta'):
                if event.delta > 0:
                    widget.yview_scroll(-1, "units")
                else:
                    widget.yview_scroll(1, "units")
            # Linux
            elif event.num == 4:
                widget.yview_scroll(-1, "units")
            elif event.num == 5:
                widget.yview_scroll(1, "units")
        return "break"

    # Obsolete tab scrolling methods removed

    def prev_tab(self):
        """Navigate to previous tab"""
        try:
            tab_names = ["Auto Roulette", "Statistics", "OCR Settings", "Region/Coordinate Setup", "Strategy Builder", "Activity Log", "Winning Numbers", "Bot Control"]
            current_name = self.tabview.get()
            if current_name in tab_names:
                current_idx = tab_names.index(current_name)
                new_idx = (current_idx - 1) % len(tab_names)
                self.tabview.set(tab_names[new_idx])
        except Exception as e:
            print(f"Error navigating tabs: {e}")

    def next_tab(self):
        """Navigate to next tab"""
        try:
            tab_names = ["Auto Roulette", "Statistics", "OCR Settings", "Region/Coordinate Setup", "Strategy Builder", "Activity Log", "Winning Numbers", "Bot Control"]
            current_name = self.tabview.get()
            if current_name in tab_names:
                current_idx = tab_names.index(current_name)
                new_idx = (current_idx + 1) % len(tab_names)
                self.tabview.set(tab_names[new_idx])
        except Exception as e:
            print(f"Error navigating tabs: {e}")

    def test_balance_ocr(self):
        """Test OCR reading of balance from recorded region"""
        try:
            if "balance" not in self.coordinates:
                self.log_message("ERROR: Balance region not recorded. Please record balance region first.")
                return
            
            # Capture screen and read balance
            balance_region = self.coordinates["balance"]
            balance_text = self.read_balance_from_region(balance_region)
            
            self.ocr_results_text.configure(state="normal")
            self.ocr_results_text.insert("end", f"[{self.get_timestamp()}] Balance OCR Test:\n")
            self.ocr_results_text.insert("end", f"Region: {balance_region}\n")
            self.ocr_results_text.insert("end", f"Read Value: '{balance_text}'\n")
            self.ocr_results_text.insert("end", "-" * 50 + "\n")
            self.ocr_results_text.see("end")
            self.ocr_results_text.configure(state="disabled")
            
            self.log_message(f"Balance OCR test completed. Read: '{balance_text}'")
            
        except Exception as e:
            self.log_message(f"ERROR: Balance OCR test failed: {str(e)}")

    def test_table_state_ocr(self):
        """Test OCR reading of table state from recorded region"""
        try:
            if "table_state" not in self.coordinates:
                self.log_message("ERROR: Table state region not recorded. Please record table state region first.")
                return
            
            # Capture screen and read table state
            table_state_region = self.coordinates["table_state"]
            table_state = self.read_table_state_from_region(table_state_region)
            
            self.ocr_results_text.configure(state="normal")
            self.ocr_results_text.insert("end", f"[{self.get_timestamp()}] Table State OCR Test:\n")
            self.ocr_results_text.insert("end", f"Region: {table_state_region}\n")
            self.ocr_results_text.insert("end", f"Read Value: '{table_state}'\n")
            self.ocr_results_text.insert("end", "-" * 50 + "\n")
            self.ocr_results_text.see("end")
            self.ocr_results_text.configure(state="disabled")
            
            self.log_message(f"Table state OCR test completed. Read: '{table_state}'")
            
        except Exception as e:
            self.log_message(f"ERROR: Table state OCR test failed: {str(e)}")

    def record_region(self):
        label = self.region_label_var.get().replace("*", "").strip()
        if not label:
            self.set_status("Please select a region label before recording.")
            messagebox.showerror("Error", "Please select a region label before recording a region.")
            return
        if not self.recorder.browser_win:
            self.set_status("Please select a browser window first.")
            messagebox.showerror("Error", "Please select a browser window first.")
            return
        self.set_status(f"Recording region for '{label}'. Move mouse to TOP-LEFT (F8), then BOTTOM-RIGHT (F9).")
        self.recorder.capture_region(label)
        self.update_required_region_status(label)
        self.set_status("Ready.")

    def record_coordinate(self):
        bet_type = self.bet_type_var.get().strip()
        if not bet_type:
            self.set_status("Please select a bet type before recording.")
            messagebox.showerror("Error", "Please select a bet type before recording.")
            return
        if not self.recorder.browser_win:
            self.set_status("Please select a browser window first.")
            messagebox.showerror("Error", "Please select a browser window first.")
            return
        self.set_status(f"Recording coordinate for '{bet_type}'. Move mouse and press F8.")
        self.recorder.capture_coordinate(bet_type)
        self.update_required_region_status(bet_type)
        self.set_status("Ready.")

    def update_required_region_status(self, label):
        essentials = ["balance", "table_state"]
        if hasattr(self, 'required_region_vars'):
            for region in essentials:
                if region == label and region in self.required_region_vars:
                    self.required_region_vars[region].set("Set")
        self.update_coordinate_display()

    def on_timing_changed(self, event=None):
        """Show/hide scheduled timing options based on selection"""
        if self.session_timing_var.get() == "scheduled":
            self.scheduled_frame.grid()
        else:
            self.scheduled_frame.grid_remove()

    def _calculate_sessions_from_runtime(self):
        """Calculate number of sessions that fit in the given total run time."""
        try:
            total_hours = float(self.total_runtime_var.get())
            if total_hours <= 0:
                raise ValueError
        except (ValueError, TypeError):
            if hasattr(self, '_calc_result_label'):
                self._calc_result_label.configure(text="Enter a valid number of hours", text_color="#ef4444")
            return

        try:
            _sd = str(self.session_duration_var.get()).strip()
            session_mins = float(_sd) if _sd != "" else 15
        except (ValueError, TypeError):
            session_mins = 15

        try:
            _mn = str(self.min_gap_var.get()).strip()
            _mx = str(self.max_gap_var.get()).strip()
            min_gap = float(_mn) if _mn != "" else 0
            max_gap = float(_mx) if _mx != "" else 1
        except (ValueError, TypeError):
            min_gap, max_gap = 0, 1

        avg_gap = (min_gap + max_gap) / 2
        total_mins = total_hours * 60

        # First session has no leading gap; each subsequent session needs session_mins + avg_gap
        # total = session_mins + (n-1)*(session_mins + avg_gap)
        # => n = 1 + floor((total_mins - session_mins) / (session_mins + avg_gap))
        if total_mins < session_mins:
            sessions = 0
        else:
            sessions = 1 + int((total_mins - session_mins) / (session_mins + avg_gap))

        if sessions < 1:
            if hasattr(self, '_calc_result_label'):
                self._calc_result_label.configure(
                    text=f"Total time too short for even 1 session ({session_mins:.0f}m each)", text_color="#ef4444"
                )
            return

        # Apply and show feedback
        self.num_sessions_var.set(str(sessions))
        actual_time = session_mins + (sessions - 1) * (session_mins + avg_gap)
        hrs = int(actual_time // 60)
        mins = int(actual_time % 60)
        if hasattr(self, '_calc_result_label'):
            self._calc_result_label.configure(
                text=f"→ {sessions} sessions  (~{hrs}h {mins}m total)",
                text_color="#10b981"
            )
        self._refresh_dash_plan_summary()

    def _refresh_dash_plan_summary(self):
        """Update the Dashboard Session Plan card with current session settings."""
        if not all(hasattr(self, a) for a in ("dash_plan_sessions", "dash_plan_total_time", "dash_plan_gap",
                                               "num_sessions_var", "session_duration_var", "min_gap_var", "max_gap_var")):
            return

        try:
            sessions = int(self.num_sessions_var.get() or 1)
        except (ValueError, TypeError):
            sessions = 1

        try:
            _sd = str(self.session_duration_var.get()).strip()
            session_mins = float(_sd) if _sd != "" else 15
        except (ValueError, TypeError):
            session_mins = 15

        try:
            _mn = str(self.min_gap_var.get()).strip()
            _mx = str(self.max_gap_var.get()).strip()
            min_gap = float(_mn) if _mn != "" else 0
            max_gap = float(_mx) if _mx != "" else 1
        except (ValueError, TypeError):
            min_gap, max_gap = 0, 1

        avg_gap = (min_gap + max_gap) / 2
        total_mins = session_mins + (sessions - 1) * (session_mins + avg_gap)
        hrs = int(total_mins // 60)
        mins = int(total_mins % 60)
        total_str = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"
        avg_gap_str = f"{avg_gap:.0f}m"

        self.dash_plan_sessions.configure(text=str(sessions))
        self.dash_plan_total_time.configure(text=total_str)
        self.dash_plan_gap.configure(text=avg_gap_str)

    def update_stats_display(self, **kwargs):
        """Update the stats display with new information"""
        if 'starting_balance' in kwargs:
            starting_balance = kwargs['starting_balance']
            if isinstance(starting_balance, (int, float)):
                self.starting_balance_label.configure(text=f"${starting_balance:.2f}")
            else:
                self.starting_balance_label.configure(text=str(starting_balance))
        if 'projected_balance' in kwargs:
            projected_balance = kwargs['projected_balance']
            if isinstance(projected_balance, (int, float)):
                self.projected_balance_label.configure(text=f"${projected_balance:.2f}")
            else:
                self.projected_balance_label.configure(text=str(projected_balance))
        
        if 'current_bet' in kwargs:
            current_bet = kwargs['current_bet']
            self.latest_bet_amount = current_bet # Expose for Telegram
            if isinstance(current_bet, (int, float)):
                self.current_bet_label.configure(text=f"${current_bet:.2f}")
            else:
                self.current_bet_label.configure(text=str(current_bet))
        
        if 'betting_on' in kwargs:
            self.betting_on_label.configure(text=str(kwargs['betting_on']))
        
        if 'table_state' in kwargs:
            self.table_state_label.configure(text=str(kwargs['table_state']))
        
        if 'session_pnl' in kwargs:
            session_pnl = kwargs['session_pnl']
            if isinstance(session_pnl, (int, float)):
                color = "#2ecc71" if session_pnl >= 0 else "#e74c3c"
                self.session_pnl_label.configure(text=f"${session_pnl:.2f}", text_color=color)
                
                # --- UPDATE NEW KPI CARD ---
                if hasattr(self, 'kpi_profit_label'):
                    self.kpi_profit_label.configure(text=f"${session_pnl:.2f}", text_color=color)
            else:
                self.session_pnl_label.configure(text=str(session_pnl))
        
        if 'total_pnl' in kwargs:
            total_pnl = kwargs['total_pnl']
            if isinstance(total_pnl, (int, float)):
                color = "#2ecc71" if total_pnl >= 0 else "#e74c3c"
                self.total_pnl_label.configure(text=f"${total_pnl:.2f}", text_color=color)
            else:
                self.total_pnl_label.configure(text=str(total_pnl))
        
        if 'rounds_played' in kwargs:
            self.rounds_played_label.configure(text=str(kwargs['rounds_played']))
        
        if 'wins' in kwargs:
            self.wins_label.configure(text=str(kwargs['wins']))
            
        if 'losses' in kwargs:
            self.losses_label.configure(text=str(kwargs['losses']))
            
        if 'current_streak' in kwargs:
            self.streak_label.configure(text=str(kwargs['current_streak']))

        if 'time_remaining' in kwargs:
            self.latest_time_remaining = str(kwargs['time_remaining'])
            self.time_remaining_label.configure(text=self.latest_time_remaining)

        # Expose Session Wait Time (if passed)
        if 'next_session_timer' in kwargs:
            val = kwargs['next_session_timer']
            self.latest_next_session_timer = str(val) if val else None
            
        # Update Telegram Dashboard (Debounced in Bot)
        if hasattr(self, 'telegram_bot') and self.telegram_bot and self.telegram_bot.loop:
            try:

                # Fire and forget update
                asyncio.run_coroutine_threadsafe(
                    self.telegram_bot.update_live_dashboard(), 
                    self.telegram_bot.loop
                )
            except Exception as e:
                # Don't block GUI
                print(f"Telegram Update Error: {e}")
        
        if 'win_rate' in kwargs:
            win_rate = kwargs['win_rate']
            if isinstance(win_rate, (int, float)):
                self.win_rate_label.configure(text=f"{win_rate:.1f}%")
                # --- UPDATE NEW KPI CARD ---
                if hasattr(self, 'kpi_winrate_label'):
                     self.kpi_winrate_label.configure(text=f"{win_rate:.1f}%")
            else:
                self.win_rate_label.configure(text=str(win_rate))
        
        if 'consecutive_losses' in kwargs:
            self.consecutive_losses_label.configure(text=str(kwargs['consecutive_losses']))
        
        if 'session_progress' in kwargs:
            self.session_progress_label.configure(text=str(kwargs['session_progress']))
        
        if 'time_remaining' in kwargs:
            self.time_remaining_label.configure(text=str(kwargs['time_remaining']))
        
        if 'current_session' in kwargs:
            self.current_session_label.configure(text=str(kwargs['current_session']))
            
        # --- UPDATE BALANCE KPI ---
        if 'projected_balance' in kwargs and hasattr(self, 'kpi_balance_label'):
             val = kwargs['projected_balance']
             if isinstance(val, (int, float)):
                 self.kpi_balance_label.configure(text=f"${val:.2f}")
             else:
                 self.kpi_balance_label.configure(text=str(val))

        # --- UPDATE DASHBOARD KPIs ---
        if 'projected_balance' in kwargs and hasattr(self, 'dash_kpi_balance'):
            val = kwargs['projected_balance']
            if isinstance(val, (int, float)):
                self.dash_kpi_balance.configure(text=f"${val:.2f}")
        if 'session_pnl' in kwargs and hasattr(self, 'dash_kpi_profit'):
            val = kwargs['session_pnl']
            if isinstance(val, (int, float)):
                color = "#2ecc71" if val >= 0 else "#e74c3c"
                self.dash_kpi_profit.configure(text=f"${val:.2f}", text_color=color)
        if 'win_rate' in kwargs and hasattr(self, 'dash_kpi_winrate'):
            val = kwargs['win_rate']
            if isinstance(val, (int, float)):
                self.dash_kpi_winrate.configure(text=f"{val:.1f}%")
        if 'session_progress' in kwargs and hasattr(self, 'dash_kpi_sessions'):
            self.dash_kpi_sessions.configure(text=str(kwargs['session_progress']))
        # Update dashboard status dot
        if hasattr(self, 'dash_status_dot'):
            if self.bot_running:
                self.dash_status_dot.configure(text_color="#2ecc71")
                self.dash_status_text.configure(text="  Bot running", text_color="#2ecc71")
            elif getattr(self, 'bot_paused', False):
                self.dash_status_dot.configure(text_color="#f39c12")
                self.dash_status_text.configure(text="  Paused", text_color="#f39c12")


    def log_to_dashboard(self, message):
        """Add a timestamped message to the Dashboard activity feed."""
        if hasattr(self, 'dash_activity_list'):
            timestamp = datetime.now().strftime("%H:%M:%S")
            entry = f"  {timestamp}  {message}\n"
            self.dash_activity_list.configure(state="normal")
            self.dash_activity_list.insert("0.0", entry)
            # Keep max ~50 lines
            content = self.dash_activity_list.get("1.0", "end-1c")
            lines = content.split("\n")
            if len(lines) > 50:
                self.dash_activity_list.delete(f"{51}.0", "end")
            self.dash_activity_list.configure(state="disabled")

    def populate_session_history(self):
        """Fetch past sessions from DB and populate table"""
        # Clear existing rows (skipping header which is in headers_frame)
        self.history_table_headers = self.history_table_frame.winfo_children()[0] # Header
        for widget in self.history_table_frame.winfo_children():
               if widget != self.history_table_headers:
                   widget.destroy()
        
        try:
            from core.utils.db_utils import get_recent_sessions
            sessions = get_recent_sessions(limit=50) # Fetch real data
            
            if not sessions:
                row_frame = ctk.CTkFrame(self.history_table_frame, fg_color="transparent")
                row_frame.pack(fill="both", expand=True, padx=2, pady=20)
                
                # Premium Empty State
                icon_lbl = ctk.CTkLabel(row_frame, text="📊", font=("Segoe UI", 32))
                icon_lbl.pack(pady=(10, 5))
                text_lbl = ctk.CTkLabel(row_frame, text="Awaiting first session...\nYour completed sessions will appear here.", 
                                      font=("Segoe UI", 12), text_color="gray50", justify="center")
                text_lbl.pack(pady=5)
                return

            for session in sessions:
                row_frame = ctk.CTkFrame(self.history_table_frame, fg_color="transparent")
                row_frame.pack(fill="x", padx=2, pady=2)
                
                # Format: Date, Start Bal?, End Bal?, Profit, Rounds
                # DB has: start_time, rounds, profit, wins, losses, strategy
                # We don't have start/end balance in DB yet, so we'll show Profit & Strategy instead
                
                # Columns need to match Headers: ["Date", "Start Bal", "End Bal", "Profit", "Rounds"]
                # Adjusted mapping:
                # Date -> start_time
                # Start Bal -> Strategy (Hijacking column)
                # End Bal -> W/L Record
                # Profit -> Profit
                # Rounds -> rounds
                
                try:
                    dt = datetime.fromisoformat(session['start_time']).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    dt = session['start_time']
                
                col_vals = [
                    dt,
                    session.get('strategy', 'Unknown')[:10], # Truncate long names
                    f"{session['wins']}W - {session['losses']}L",
                    f"${session['profit']:.2f}",
                    str(session['rounds'])
                ]
                
                # Manual Grid layout for row to match header weights approximately
                # Using pack side=left with expand=True is easiest if headers used that
                for val in col_vals:
                    lbl = ctk.CTkLabel(row_frame, text=val, font=("Arial", 11))
                    lbl.pack(side="left", expand=True, fill="x")
                    
                    # Colorize profit
                    if "$" in val:
                         try:
                             profit_val = float(val.replace("$", ""))
                             if profit_val >= 0: lbl.configure(text_color="#2ecc71")
                             else: lbl.configure(text_color="#e74c3c")
                         except Exception: pass

        except Exception as e:
            print(f"Error populating history: {e}")

    def update_bankroll_graph(self):
        """Draw bankroll trend using real data"""
        try:
            from core.utils.db_utils import get_bankroll_trend
            dates, profits = get_bankroll_trend(limit=50)
            
            self.stats_ax.clear()
            self.stats_ax.set_facecolor('#2b2b2b')
            
            if not dates:
                 self.stats_ax.text(0.5, 0.5, 'Awaiting first spin...\nChart will appear here', 
                                  horizontalalignment='center', verticalalignment='center', 
                                  color='gray', fontsize=12, style='italic', alpha=0.7)
                 self.stats_canvas.draw()
                 return

            # Plot cumulative profit
            # Green line if positive trend overall, Red if negative? 
            # Simple logic: Last value
            line_color = '#2ecc71' if profits[-1] >= 0 else '#e74c3c'
            
            self.stats_ax.plot(dates, profits, color=line_color, marker='o', linewidth=2, markersize=4)
            self.stats_ax.grid(True, color='gray', linestyle='--', alpha=0.3)
            self.stats_ax.set_title("Cumulative Profit Trend", color='white', fontsize=10)
            
            # Format x-axis dates if too many
            if len(dates) > 5:
                # Show only every Nth label or rotate
                plt.setp(self.stats_ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)
            
            self.stats_figure.tight_layout()
            self.stats_canvas.draw()
            
        except Exception as e:
            logger.error(f"Graph error: {e}")
            print(f"Graph Debug Error: {e}")

    def confirm_clear_stats(self):
        """Ask for confirmation before clearing all stats"""
        if messagebox.askyesno("Confirm Reset", "Are you sure you want to DELETE ALL HISTORICAL STATISTICS?\n(This cannot be undone)"):
            try:
                from core.utils.db_utils import clear_all_statistics
                clear_all_statistics()
                messagebox.showinfo("Success", "All statistics have been reset.")
                self.refresh_aggregate_stats()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to reset stats: {e}")

    def refresh_aggregate_stats(self):
        """Refresh the aggregate statistics display"""
        print("DEBUG: Refreshing Aggregate Stats...") # Debug print
        try:
            stats = get_aggregate_stats()
            print(f"DEBUG: Stats retrieved: {stats}") # Debug print
            
            self.total_sessions_label.configure(text=str(stats['total_sessions']))
            self.total_rounds_label.configure(text=str(stats['total_rounds']))
            self.total_wins_agg_label.configure(text=str(stats['total_wins']))
            self.total_losses_agg_label.configure(text=str(stats['total_losses']))
            
            win_rate = 0
            if stats['total_rounds'] > 0:
                win_rate = (stats['total_wins'] / stats['total_rounds']) * 100
            self.overall_win_rate_label.configure(text=f"{win_rate:.1f}%")
            
            profit = stats.get('total_profit', 0.0)
            color = "#2ecc71" if profit >= 0 else "#e74c3c"
            self.total_profit_label.configure(text=f"${profit:.2f}", text_color=color)
            
            # Update new components
            self.populate_session_history()
            self.update_bankroll_graph()
        except Exception as e:
            print(f"ERROR in refresh_aggregate_stats: {e}")
            import traceback
            traceback.print_exc()
        
    def refresh_winning_numbers_tab(self):
        """Fetch analysis data and populate Winning Numbers tab"""
        try:
            from core.utils.db_utils import get_recent_winning_numbers, get_number_frequency, get_sector_stats
            
            # A. Visual Ticker (Recent 20)
            recents = get_recent_winning_numbers(limit=20)
            # Clear ticker
            for widget in self.ticker_canvas.winfo_children():
                widget.destroy()
            
            for entry in recents:
                num = entry['number']
                color_code = "#2ecc71" if num == 0 else ("#e74c3c" if entry['color'] == "red" else "#2c3e50") # Green, Red, Black
                text_color = "white"
                
                # Circle/Card representation
                card = ctk.CTkFrame(self.ticker_canvas, width=40, height=40, fg_color=color_code, corner_radius=20)
                card.pack(side="left", padx=2, pady=2)
                # Force size by using a dummy label that fills it or fixed size logic (pack propogate)
                lbl = ctk.CTkLabel(card, text=str(num), font=("Arial", 12, "bold"), text_color=text_color)
                lbl.place(relx=0.5, rely=0.5, anchor="center")
                
            # B. Hot/Cold & Frequency
            freq_data = get_number_frequency(limit=100)
            if freq_data:
                # Hot: Top 3
                hot = freq_data[:3]
                hot_text = ", ".join([f"{x['number']} ({x['count']})" for x in hot])
                self.hot_numbers_label.configure(text=f"Hot: {hot_text}")
                
                # Cold: Bottom 3 (Top of the reversed list excluding zeros if we want strictly hit counts, 
                # or just the ones with 0 count. Let's take the last 3 of the list which is sorted descending)
                cold = freq_data[-3:]
                cold_text = ", ".join([f"{x['number']} ({x['last_seen']})" for x in cold]) 
                # Note: last_seen is 'rounds ago'
                self.cold_numbers_label.configure(text=f"Cold (Ago): {cold_text}")
                
                # Frequency Table
                # Clear table
                for widget in self.frequency_table.winfo_children():
                    widget.destroy()
                    
                for item in freq_data:
                    row = ctk.CTkFrame(self.frequency_table, fg_color="transparent")
                    row.pack(fill="x", padx=2, pady=1)
                    
                    bg_color = "transparent"
                    if item['number'] in [x['number'] for x in hot]: bg_color = "#2ecc71" # Highlight hot
                    
                    vals = [
                        str(item['number']),
                        str(item['count']),
                        f"{item['percentage']:.1f}%",
                        str(item['last_seen'])
                    ]
                    
                    for i, val in enumerate(vals):
                        l = ctk.CTkLabel(row, text=val, font=("Arial", 11), fg_color=bg_color if i==0 else "transparent", corner_radius=5)
                        l.pack(side="left", expand=True, fill="x")
                        
            # C. Sector Stats
            sectors = get_sector_stats(limit=100)
            sec_text = "\n".join([f"{k}: {v:.1f}%" for k, v in sectors.items()])
            self.sector_stats_label.configure(text=sec_text)
            
            # D. Streaks (Simple Logic on Recents)
            if recents:
                current_color = recents[0]['color']
                streak_count = 1
                for i in range(1, len(recents)):
                    if recents[i]['color'] == current_color:
                        streak_count += 1
                    else:
                        break
                
                streak_text = f"{current_color.title()} x{streak_count}" if current_color else "None"
                self.streak_label.configure(text=streak_text, text_color="#e74c3c" if current_color=="red" else "white")
            else:
                self.streak_label.configure(text="No Data")
                
        except Exception as e:
            print(f"Error refreshing winning numbers: {e}") 
            import traceback
            traceback.print_exc()
    def reset_session_stats(self):
        """Reset session-specific statistics"""
        self.session_start_time = datetime.now()
        self.session_start_timestamp = int(datetime.now().timestamp())
        self.session_start_balance = None
        self.session_end_time = None
        self.update_stats_display(
            current_bet="--",
            betting_on="--",
            table_state="--",
            current_balance="--",
            session_pnl="--",
            time_remaining="--:--"
        )

    def calculate_win_rate(self):
        """Calculate current win rate"""
        total_rounds = self.total_wins + self.total_losses
        if total_rounds == 0:
            return 0.0
        return (self.total_wins / total_rounds) * 100

    def update_label_selector(self):
        self.label_selector.delete(0, tk.END)
        chip_labels = list(CHIP_DENOMINATIONS.keys())
        for chip in chip_labels:
            self.label_selector.insert(tk.END, chip)
        for label in self.coordinates:
            if label in VALID_BET_TYPES and label not in chip_labels:
                self.label_selector.insert(tk.END, label)
        
        # Initialize global selections if not exists
        if not hasattr(self, '_global_label_selections'):
            self._global_label_selections = set()
        
        # Apply current search filter if any
        if hasattr(self, 'label_search_var') and self.label_search_var.get():
            self.filter_label_selector()

    def update_strategy_dropdown(self):
        built_in = ["martingale", "flat"]
        custom = list(self.config.get("custom_strategies", {}).keys())
        full = built_in + custom
        # Keep the unfiltered master list so the search filter can restore
        # entries when the user clears their query.
        self._strategy_master_list = list(full)
        self.strategy_dropdown.configure(values=full)
        # Sync the auto-roulette dropdown too if it exists — same source.
        if hasattr(self, 'auto_roulette_strategy_dropdown'):
            try:
                self._auto_roulette_strategy_master_list = list(full)
                self.auto_roulette_strategy_dropdown.configure(values=full)
            except Exception:
                pass
        # Re-render the quick-toggle bar in case a favorite was just added/removed
        # via custom_strategies refresh.
        if getattr(self, "_quick_toggle_pills_container", None) is not None:
            try:
                self._render_quick_toggle_bar()
            except Exception:
                pass

    # ── Quick-Toggle (Favorites + Most-Played) ──────────────────────────
    # Persistence: self.config["favorite_strategies"] = ["name1", "name2", ...]
    # Step 1 ships read-only UI. Click handler is a stub until step 2 wires
    # the actual round-boundary strategy swap.

    def _available_strategy_names(self):
        built_in = {"martingale", "flat"}
        custom = set((self.config.get("custom_strategies") or {}).keys())
        return built_in | custom

    def _get_favorite_strategies(self):
        """Persisted favorites, filtered to strategies that still exist."""
        favs = self.config.get("favorite_strategies", []) or []
        if not isinstance(favs, list):
            return []
        available = self._available_strategy_names()
        return [f for f in favs if f in available]

    def _add_to_favorites(self, name: str):
        if not name:
            return
        favs = list(self.config.get("favorite_strategies", []) or [])
        if name in favs:
            return
        favs.append(name)
        self.config["favorite_strategies"] = favs
        save_config(self.config)
        self._render_quick_toggle_bar()
        if hasattr(self, "log_to_dashboard"):
            self.log_to_dashboard(f"★ Added '{name}' to favorites")

    def _remove_from_favorites(self, name: str):
        if not name:
            return
        favs = list(self.config.get("favorite_strategies", []) or [])
        if name not in favs:
            return
        favs = [f for f in favs if f != name]
        self.config["favorite_strategies"] = favs
        save_config(self.config)
        self._render_quick_toggle_bar()
        if hasattr(self, "log_to_dashboard"):
            self.log_to_dashboard(f"☆ Removed '{name}' from favorites")

    def _on_quick_toggle_click(self, name: str):
        """Pill click (Bot Control bar or HUD overlay) — switch to a single
        strategy in strict manual mode."""
        if not name:
            return
        # Record the pick as the active single strategy.
        try:
            self.strategy_var.set(name)
            self.config["strategy"] = name
        except Exception as e:
            logger.error(f"Quick-toggle set strategy failed: {e}")

        # Enforce manual mode. This clears ANY loaded bundle — rotation list,
        # conditional trigger engine, AND triggers_config — and restores the
        # pre-bundle Bot Control config. Previously this path only flipped
        # enable_strategy_rotation off, leaving a *conditional* bundle's trigger
        # engine alive to re-select a member every round and silently override
        # the pick — so switching bundle→strategy via the HUD appeared to do
        # nothing. When a session is live, _select_strategy_source queues a
        # round-boundary re-arm that rebuilds the engine onto this single
        # strategy, so the switch actually sticks. When idle, it just preps the
        # next session. It also emits the "switch queued" notification.
        self._select_strategy_source('manual')

        if not getattr(self, "bot_running", False):
            try:
                save_config(self.config)
            except Exception:
                pass
            if hasattr(self, "log_to_dashboard"):
                self.log_to_dashboard(f"⚡ Selected '{name}' (bot not running — applies to next session)")

    def _render_quick_toggle_bar(self):
        """Clear and rebuild the quick-toggle pill bar from current favorites."""
        container = getattr(self, "_quick_toggle_pills_container", None)
        if container is None:
            return
        for child in container.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        favs = self._get_favorite_strategies()
        if not favs:
            ctk.CTkLabel(
                container,
                text="(right-click the strategy dropdown to add favorites)",
                font=("Segoe UI", 11),
                text_color="#7f8c8d",
            ).pack(side="left")
            return
        # Strict XOR: a strategy pill is only "active" (green) when a single
        # strategy — not a bundle — is the running source.
        manual_active = getattr(self, "active_strategy_source", None) != "bundle"
        active = (self.strategy_var.get() or "").strip() if hasattr(self, "strategy_var") else ""
        for name in favs:
            is_active = manual_active and (name == active)
            btn = ctk.CTkButton(
                container,
                text=f"★ {name}",
                width=110, height=24,
                font=("Segoe UI", 10, "bold" if is_active else "normal"),
                fg_color="#27ae60" if is_active else "#34495e",
                hover_color="#e6b22e",
                command=lambda n=name: self._on_quick_toggle_click(n),
            )
            btn.pack(side="left", padx=2)
            btn.bind("<Button-3>", lambda e, n=name: self._remove_from_favorites(n))
            ToolTip(btn, f"Click to switch to '{name}' (queued for end of round).\nRight-click to remove from favorites.")

    # ── Global Hotkeys (Ctrl+1..9 → favorite swap, Ctrl+` → toggle) ─────
    # Uses the `keyboard` module (in requirements.txt). Hotkeys fire from
    # the keyboard listener thread, so all swap calls are marshaled back to
    # the Tk main thread via root.after(0, ...).

    def _hotkey_dispatch_slot(self, slot_index: int):
        """Resolve slot_index → favorite name (strategies first, then bundles)
        and queue the appropriate swap. Runs on the Tk main thread."""
        try:
            strat_favs = self._get_favorite_strategies() if hasattr(self, "_get_favorite_strategies") else []
            bundle_favs = self._get_favorite_dashboard_bundles() if hasattr(self, "_get_favorite_dashboard_bundles") else []
            # Match the HUD ordering (strategies, then bundles, max 3 each).
            combined = [("strategy", n) for n in strat_favs[:3]] + [("bundle", n) for n in bundle_favs[:3]]
            if slot_index >= len(combined):
                return
            kind, name = combined[slot_index]
            if kind == "strategy":
                self._on_quick_toggle_click(name)
            else:
                self._on_dashboard_bundle_pill_click(name)
        except Exception as e:
            logger.error(f"Hotkey dispatch failed for slot {slot_index}: {e}")

    def _hotkey_toggle_last(self):
        """Ctrl+` — flip back to the previous strategy (terminal-tab style)."""
        prev = getattr(self, "last_strategy_swap", None)
        if not prev:
            if hasattr(self, "log_to_dashboard"):
                self.log_to_dashboard("⚡ No previous strategy to toggle to yet.")
            return
        self._on_quick_toggle_click(prev)

    def _register_global_hotkeys(self):
        """Bind Ctrl+1..9 and Ctrl+` system-wide. Idempotent."""
        if getattr(self, "_hotkeys_registered", False):
            return
        try:
            import keyboard as _kb
        except Exception as e:
            logger.warning(f"Global hotkeys unavailable (keyboard module not loadable): {e}")
            return
        self._kb_module = _kb
        self._hotkey_handles = []

        def _marshal(fn):
            # Trampoline into the Tk thread so we don't touch widgets from the
            # keyboard listener thread.
            return lambda: self.root.after(0, fn)

        try:
            for i in range(9):
                # keyboard.add_hotkey returns a handle we can pass to remove_hotkey.
                handle = _kb.add_hotkey(
                    f"ctrl+{i + 1}",
                    _marshal(lambda idx=i: self._hotkey_dispatch_slot(idx)),
                    suppress=False,
                )
                self._hotkey_handles.append(handle)
            # Ctrl+` — backtick. Some layouts call it "grave".
            try:
                handle = _kb.add_hotkey("ctrl+`",
                                        _marshal(self._hotkey_toggle_last),
                                        suppress=False)
                self._hotkey_handles.append(handle)
            except Exception:
                # Fallback for layouts where "`" isn't a recognized key name.
                try:
                    handle = _kb.add_hotkey("ctrl+grave",
                                            _marshal(self._hotkey_toggle_last),
                                            suppress=False)
                    self._hotkey_handles.append(handle)
                except Exception:
                    pass
            self._hotkeys_registered = True
            logger.info("Global hotkeys registered: Ctrl+1..9 = favorites, Ctrl+` = toggle previous")
        except Exception as e:
            logger.error(f"Failed to register global hotkeys: {e}")
            self._hotkey_handles = []

    def _unregister_global_hotkeys(self):
        """Release the global hotkey hooks. Safe to call multiple times."""
        kb = getattr(self, "_kb_module", None)
        handles = getattr(self, "_hotkey_handles", [])
        if not kb or not handles:
            self._hotkeys_registered = False
            self._hotkey_handles = []
            return
        for h in handles:
            try:
                kb.remove_hotkey(h)
            except Exception:
                pass
        self._hotkey_handles = []
        self._hotkeys_registered = False

    # ── Dashboard Bundle Favorites ──────────────────────────────────────
    # Persistence: self.config["favorite_bundles"] = ["bundleA", "bundleB", ...]
    # Click a pill → applies the bundle immediately (same as picking from the
    # dashboard dropdown). Right-click a pill → removes from favorites.

    def _available_dashboard_bundles(self):
        """Bundles currently allowed for this user (after entitlement filter)."""
        try:
            vals = self.dashboard_bundle_dropdown.cget("values") or []
        except Exception:
            return set()
        return {v for v in vals if v and v not in ("Select Bundle...", "No Bundles Found")}

    def _get_favorite_dashboard_bundles(self):
        favs = self.config.get("favorite_bundles", []) or []
        if not isinstance(favs, list):
            return []
        available = self._available_dashboard_bundles()
        # Keep favorites that are still entitled; silently drop the rest from the visible bar
        # but preserve the underlying config list so re-entitling restores them.
        return [f for f in favs if f in available]

    def _add_dashboard_bundle_favorite(self, name: str):
        if not name:
            return
        favs = list(self.config.get("favorite_bundles", []) or [])
        if name in favs:
            return
        favs.append(name)
        self.config["favorite_bundles"] = favs
        save_config(self.config)
        self._render_dashboard_bundle_bar()
        if hasattr(self, "log_to_dashboard"):
            self.log_to_dashboard(f"★ Added bundle '{name}' to favorites")

    def _remove_dashboard_bundle_favorite(self, name: str):
        if not name:
            return
        favs = list(self.config.get("favorite_bundles", []) or [])
        if name not in favs:
            return
        favs = [f for f in favs if f != name]
        self.config["favorite_bundles"] = favs
        save_config(self.config)
        self._render_dashboard_bundle_bar()
        if hasattr(self, "log_to_dashboard"):
            self.log_to_dashboard(f"☆ Removed bundle '{name}' from favorites")

    def _on_dashboard_bundle_pill_click(self, name: str):
        """Pill click — load the bundle. Mid-session swap handling lives inside
        on_dashboard_bundle_select itself so the dropdown gets the same
        behavior."""
        if not name:
            return
        try:
            self.dashboard_bundle_var.set(name)
            self.on_dashboard_bundle_select(name)
        except Exception as e:
            logger.error(f"Dashboard bundle pill click failed for '{name}': {e}")

    def _render_dashboard_bundle_bar(self):
        container = getattr(self, "_dashboard_bundle_pills_container", None)
        if container is None:
            return
        for child in container.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        favs = self._get_favorite_dashboard_bundles()
        if not favs:
            ctk.CTkLabel(
                container,
                text="(right-click the bundle dropdown to add favorites)",
                font=("Segoe UI", 11),
                text_color="#7f8c8d",
            ).pack(side="left")
            return
        # Strict XOR: a bundle pill is only "active" (green) when a bundle is
        # the running source.
        bundle_active = getattr(self, "active_strategy_source", None) == "bundle"
        active = (self.dashboard_bundle_var.get() or "").strip() if hasattr(self, "dashboard_bundle_var") else ""
        for name in favs:
            is_active = bundle_active and (name == active)
            btn = ctk.CTkButton(
                container,
                text=f"★ {name}",
                width=110, height=24,
                font=("Segoe UI", 10, "bold" if is_active else "normal"),
                fg_color="#27ae60" if is_active else "#34495e",
                hover_color="#e6b22e",
                command=lambda n=name: self._on_dashboard_bundle_pill_click(n),
            )
            btn.pack(side="left", padx=2)
            btn.bind("<Button-3>", lambda e, n=name: self._remove_dashboard_bundle_favorite(n))
            ToolTip(btn, f"Click to load bundle '{name}'.\nRight-click to remove from favorites.")

    @staticmethod
    def _filter_by_query(master, query):
        """Rank `master` items against `query` (case-insensitive) in three
        tiers, best first: prefix → substring → fuzzy subsequence. The fuzzy
        tier is what makes 'initials' search work — '6sb' matches
        '6streetstratbundle' because those letters appear in order. Empty
        query returns the full list unchanged; no match returns []."""
        q = (query or "").strip().lower()
        if not q:
            return list(master)
        prefix, contains, fuzzy = [], [], []
        for s in master:
            sl = str(s).lower()
            if sl.startswith(q):
                prefix.append(s)
            elif q in sl:
                contains.append(s)
            else:
                it = iter(sl)  # subsequence test
                if all(ch in it for ch in q):
                    fuzzy.append(s)
        return prefix + contains + fuzzy

    def _make_combobox_searchable(self, combobox, master_list_attr: str,
                                  prefer_prefix: bool = True) -> None:
        """Wire a CTkComboBox so typing into it filters the dropdown values
        (prefix → substring → fuzzy/initials). The master list is read LIVE
        from the named attribute on self, so callers that refresh the list
        elsewhere (e.g. update_strategy_dropdown / refresh_dashboard_bundles)
        don't need to re-wire. Re-entrant-safe: calling twice on the same
        widget won't stack duplicate bindings. Falls back gracefully if CTk's
        internal entry can't be located on this build.
        """
        try:
            combobox.configure(state="normal")
        except Exception:
            return
        # CTkComboBox keeps the inner CTkEntry as `_entry`. The KeyRelease
        # fires after the StringVar updates, so combobox.get() returns the
        # current text.
        entry = getattr(combobox, "_entry", None)
        if entry is None:
            return
        # Idempotency: never bind the filter twice (refresh paths may call us
        # again). The master list is read live, so one binding is enough.
        if getattr(combobox, "_search_wired", False):
            return

        def _on_key(event=None):
            # Ignore navigation/commit keys so arrowing the dropdown or pressing
            # Enter to select doesn't re-filter and fight the user.
            if event is not None and getattr(event, "keysym", "") in (
                    "Up", "Down", "Return", "Escape", "Tab", "Left", "Right"):
                return
            query = (combobox.get() or "")
            master = list(getattr(self, master_list_attr, []) or [])
            filtered = self._filter_by_query(master, query)
            try:
                # Show all on no-match: CTkComboBox won't open an empty dropdown,
                # which would otherwise look like the widget is broken.
                combobox.configure(values=filtered or master)
            except Exception:
                pass

        entry.bind("<KeyRelease>", _on_key)
        combobox._search_wired = True

    def refresh_custom_strategies(self):
        """Reload custom strategy presets from disk and update dropdowns"""
        try:
            import logging
            from config.schema import load_config
            new_config = load_config()
            self.config["custom_strategies"] = new_config.get("custom_strategies", {})
            self.update_strategy_dropdown()
            
            # Also update the Advanced Strategy Builder selector if active
            if hasattr(self, 'update_strategy_selector'):
                self.update_strategy_selector()
                
            self.log_to_dashboard("✅ Custom strategies refreshed from disk.")
            self.log_simulation("✅ Custom strategies refreshed from disk.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to refresh strategies: {e}")


    def save_coordinate(self, label, *args):
        if len(args) == 1 and isinstance(args[0], dict):  # Region dictionary
            self.coordinates[label] = args[0]
            # OCR preview only if it's a region
            try:
                # Defensive: check for valid region size
                region = args[0]
                x1 = region.get('x1_pct')
                y1 = region.get('y1_pct')
                x2 = region.get('x2_pct')
                y2 = region.get('y2_pct')
                if None in (x1, y1, x2, y2) or abs(x2 - x1) < 0.001 or abs(y2 - y1) < 0.001:
                    messagebox.showerror("Region Error", "Invalid region size selected. Please click and drag to select a valid region.")
                    return
                numbers = extract_recent_numbers(self.recorder.browser_win, region)
                if numbers is None:
                    messagebox.showerror("OCR Error", "Failed to capture or preview the selected region. Please try again.")
                    return
                messagebox.showinfo("OCR Preview", f"🧾 Detected Numbers: {numbers}")
            except Exception as e:
                print("❌ OCR error:", e)
                messagebox.showerror("OCR Error", f"Failed to detect numbers.\n\n{e}")
        elif len(args) == 2:  # Point (x_pct, y_pct)
            x_pct, y_pct = args
            self.coordinates[label] = {"x_pct": x_pct, "y_pct": y_pct}
        else:
            print("❌ Invalid arguments passed to save_coordinate.")
            return

        self.config["coordinates"] = self.coordinates
        save_config(self.config)
        self.update_coordinate_display()
        self.update_label_selector()



    def update_coordinate_display(self):
        # Deprecated: replaced by update_coord_list_display
        pass


    def update_strategy_list_display(self):
        self.strategy_list_display.configure(state="normal")
        self.strategy_list_display.delete("1.0", tk.END)
        for name, strategy_data in self.custom_strategies.items():
            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'neighbors':
                n = strategy_data.get('neighbors', 2)
                anchors = strategy_data.get('anchor_offsets', [1])
                hot_c = strategy_data.get('hot_count', 0)
                cold_c = strategy_data.get('cold_count', 0)
                anchor_desc = self._describe_anchors(anchors, hot_c, cold_c)
                per_anchor = 1 + 2 * n
                total_anchors = len(anchors) + hot_c + cold_c
                self.strategy_list_display.insert(
                    tk.END,
                    f"{name}: [Neighbors ±{n} of {anchor_desc}] ({per_anchor}/anchor, {total_anchors} anchor{'s' if total_anchors > 1 else ''})\n"
                )
            elif isinstance(strategy_data, dict) and strategy_data.get('mode') == 'pattern_follower':
                rules = strategy_data.get('rules', [])
                hsize = strategy_data.get('history_size', 50)
                detector_set = sorted({r.get('detect', 'streak') for r in rules
                                       if isinstance(r, dict)})
                detector_str = f" [{', '.join(detector_set)}]" if detector_set else ""
                self.strategy_list_display.insert(
                    tk.END,
                    f"{name}: [Pattern Follower] {len(rules)} rule{'s' if len(rules) != 1 else ''}, history={hsize}{detector_str}\n"
                )
            elif isinstance(strategy_data, dict) and strategy_data.get('mode') == 'composite':
                rules = strategy_data.get('rules', [])
                # Count delegate actions for a quick at-a-glance summary
                n_delegates = sum(
                    1 for r in rules if isinstance(r, dict)
                    and (r.get('then', {}).get('action') == 'delegate'
                         or r.get('action') == 'delegate')
                )
                self.strategy_list_display.insert(
                    tk.END,
                    f"{name}: [Composite] {len(rules)} rule{'s' if len(rules) != 1 else ''}, "
                    f"{n_delegates} delegate{'s' if n_delegates != 1 else ''}\n"
                )
            elif isinstance(strategy_data, dict) and 'labels' in strategy_data:
                labels = strategy_data['labels']
                display_text = f"{name}: {', '.join(labels)}\n"

                # New: Prioritize 'bet_units'
                if 'bet_units' in strategy_data:
                    bet_units = strategy_data['bet_units']
                    if bet_units:
                        units_display = [f'{label}({units} units)' for label, units in bet_units.items()]
                        display_text += f"  Bet units: {', '.join(units_display)}\n"
                # Legacy: Handle 'bet_amounts' for backward compatibility
                elif 'bet_amounts' in strategy_data:
                    bet_amounts = strategy_data.get('bet_amounts', {})
                    if bet_amounts:
                        base_bet = self.config.get("base_bet", 1.0)
                        units_display = []
                        for label, amount in bet_amounts.items():
                            units = int(amount / base_bet) if base_bet > 0 else 0
                            units_display.append(f'{label}({units} units)')
                        display_text += f"  Bet units (legacy): {', '.join(units_display)}\n"
                self.strategy_list_display.insert(tk.END, display_text)
            elif isinstance(strategy_data, list):
                # Old format - just labels
                self.strategy_list_display.insert(tk.END, f"{name}: {', '.join(strategy_data)}\n")
        self.strategy_list_display.configure(state="disabled")
        # Also update the strategy selector when the list is updated
        self.update_strategy_selector()

    def add_label_to_strategy(self):
        strategy_name = self.custom_strategy_var.get().strip()
        bet_mode = self.bet_mode_var.get()

        if not strategy_name:
            messagebox.showerror("Error", "Please enter a strategy name.")
            return

        # --- Dynamic Neighbors Mode ---
        if bet_mode == "Neighbors":
            try:
                neighbors = int(self.neighbors_count_var.get())
                if neighbors < 1 or neighbors > 17:
                    raise ValueError
            except (ValueError, tk.TclError):
                messagebox.showerror("Error", "Neighbors per side must be a number between 1 and 17.")
                return

            # Parse anchor offsets
            anchors_str = self.neighbors_anchors_var.get().strip()
            try:
                anchor_offsets = [int(x.strip()) for x in anchors_str.split(",") if x.strip()]
                if not anchor_offsets or any(a < 1 or a > 20 for a in anchor_offsets):
                    raise ValueError
            except (ValueError, TypeError):
                messagebox.showerror("Error", "Anchor numbers must be comma-separated positive integers (1-20).\n"
                                     "e.g. '1' for last, '1,3' for last + 3rd last.")
                return

            # Parse hot/cold counts
            try:
                hot_count = int(self.neighbors_hot_var.get())
                if hot_count < 0 or hot_count > 10:
                    raise ValueError
            except (ValueError, tk.TclError):
                hot_count = 0
            try:
                cold_count = int(self.neighbors_cold_var.get())
                if cold_count < 0 or cold_count > 10:
                    raise ValueError
            except (ValueError, tk.TclError):
                cold_count = 0
            try:
                lookback = int(self.neighbors_lookback_var.get())
                if lookback < 5:
                    lookback = 5
                elif lookback > 500:
                    lookback = 500
            except (ValueError, tk.TclError):
                lookback = 30

            if "custom_strategies" not in self.config:
                self.config["custom_strategies"] = {}

            strategy_data = {
                'labels': [],
                'mode': 'neighbors',
                'neighbors': neighbors,
                'anchor_offsets': anchor_offsets,
                'hot_count': hot_count,
                'cold_count': cold_count,
                'lookback': lookback,
                'bet_units': {}
            }
            self.config["custom_strategies"][strategy_name] = strategy_data

            save_config(self.config)
            self.custom_strategies = self.config["custom_strategies"]
            self.update_strategy_list_display()
            self.update_strategy_dropdown()
            self.update_strategy_selector()
            self.strategy_selector_var.set(strategy_name)
            self.update_strategy_preview()

            per_anchor = 1 + 2 * neighbors
            anchor_desc = self._describe_anchors(anchor_offsets, hot_count, cold_count)
            messagebox.showinfo("Success",
                                f"Dynamic Neighbors strategy '{strategy_name}' saved.\n"
                                f"Anchors: {anchor_desc}\n"
                                f"{per_anchor} numbers per anchor, overlaps deduplicated.")
            self.custom_strategy_var.set("")
            return

        # --- Pattern Follower Mode ---
        if bet_mode == "Pattern Follower":
            try:
                rules = self.pattern_follower_editor.get_rules()
                history_size = self.pattern_follower_editor.get_history_size()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read pattern rules: {e}")
                return

            if not rules:
                messagebox.showerror("Error", "Add at least one rule before saving.")
                return

            # Validate via constructor (raises ValueError on bad config)
            try:
                from core.strategies.pattern_follower import PatternFollowerStrategy
                PatternFollowerStrategy(base_bet=1.0, rules=rules, history_size=history_size)
            except (ValueError, TypeError) as e:
                messagebox.showerror("Invalid rules", str(e))
                return

            if "custom_strategies" not in self.config:
                self.config["custom_strategies"] = {}

            strategy_data = {
                'mode': 'pattern_follower',
                'rules': rules,
                'history_size': history_size,
            }
            self.config["custom_strategies"][strategy_name] = strategy_data

            save_config(self.config)
            self.custom_strategies = self.config["custom_strategies"]
            self.update_strategy_list_display()
            self.update_strategy_dropdown()
            self.update_strategy_selector()
            self.strategy_selector_var.set(strategy_name)
            self.update_strategy_preview()

            messagebox.showinfo(
                "Success",
                f"Pattern Follower strategy '{strategy_name}' saved with "
                f"{len(rules)} rule{'s' if len(rules) != 1 else ''}."
            )
            self.custom_strategy_var.set("")
            return

        # --- Composite Mode ---
        if bet_mode == "Composite":
            try:
                rules = self.composite_editor.get_rules()
                history_size = self.composite_editor.get_history_size()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read composite rules: {e}")
                return

            if not rules:
                messagebox.showerror("Error", "Add at least one rule before saving.")
                return

            # Validate via parse_rule + presence-check delegated sub-strategies
            try:
                from core.decision.rules import parse_rule, extract_delegate_names
                parsed = [parse_rule(r) for r in rules]
                sub_names = extract_delegate_names(parsed)
            except (ValueError, TypeError) as e:
                messagebox.showerror("Invalid composite rules", str(e))
                return

            available_lower = {k.lower() for k in self.custom_strategies.keys()}
            missing = [n for n in sub_names if n.lower() not in available_lower
                       and n.lower() != strategy_name.lower()]
            if missing:
                available = sorted(self.custom_strategies.keys())
                messagebox.showerror(
                    "Unknown sub-strategy",
                    f"Delegate actions reference strategies that don't exist:\n  "
                    + ", ".join(missing)
                    + "\n\nAvailable strategies:\n  "
                    + (", ".join(available) if available else "(none yet)")
                )
                return

            # Self-reference is allowed in JSON but the engine catches the cycle at
            # load. Warn early so the user doesn't ship a broken preset.
            if strategy_name.lower() in {n.lower() for n in sub_names}:
                if not messagebox.askokcancel(
                    "Self-delegate detected",
                    f"This preset delegates to itself ('{strategy_name}'). "
                    "Loading it will fail with a cycle-detection error at runtime.\n\n"
                    "Save anyway?"
                ):
                    return

            if "custom_strategies" not in self.config:
                self.config["custom_strategies"] = {}

            strategy_data = {
                "mode": "composite",
                "rules": rules,
                "history_size": history_size,
            }
            self.config["custom_strategies"][strategy_name] = strategy_data

            save_config(self.config)
            self.custom_strategies = self.config["custom_strategies"]
            self.update_strategy_list_display()
            self.update_strategy_dropdown()
            self.update_strategy_selector()
            self.strategy_selector_var.set(strategy_name)
            self.update_strategy_preview()

            messagebox.showinfo(
                "Success",
                f"Composite strategy '{strategy_name}' saved with "
                f"{len(rules)} rule{'s' if len(rules) != 1 else ''}, "
                f"{len(sub_names)} delegate{'s' if len(sub_names) != 1 else ''}."
            )
            self.custom_strategy_var.set("")
            return

        # --- Static Mode (original behavior) ---
        selected_indices = self.label_selector.curselection()

        if not selected_indices:
            messagebox.showerror("Error", "Please select at least one label.")
            return

        # Filter valid labels: Must be a standard bet type OR a Recorded Coordinate
        selected_labels = []
        for i in selected_indices:
            lbl = self.label_selector.get(i)
            if lbl in VALID_BET_TYPES or lbl in self.coordinates:
                selected_labels.append(lbl)

        if not selected_labels:
            messagebox.showerror("Error", "Please select at least one valid bet type or recorded coordinate.")
            return

        if "custom_strategies" not in self.config:
            self.config["custom_strategies"] = {}

        # Check if custom bet units are enabled
        custom_bet_units = {}
        if self.enable_custom_bet_units_var.get():
            custom_bet_units = self.get_custom_bet_units()

        # Save only units (base-bet independent) — engine computes $ at runtime
        strategy_data = {
            'labels': selected_labels,
            'bet_units': custom_bet_units if custom_bet_units else {}
        }
        self.config["custom_strategies"][strategy_name] = strategy_data

        save_config(self.config)
        self.custom_strategies = self.config["custom_strategies"]
        self.update_strategy_list_display()
        self.update_strategy_dropdown()
        self.update_strategy_selector()
        
        # Select the new strategy and update preview
        self.strategy_selector_var.set(strategy_name)
        self.update_strategy_preview()

        messagebox.showinfo("Success", f"Strategy '{strategy_name}' updated.")
        self.custom_strategy_var.set("")
        self.label_selector.selection_clear(0, tk.END)

    def select_window_dialog(self):
        windows = self.recorder.list_windows()
        if not windows:
            return

        # Snapshot HWNDs + titles at listing time — immune to later reordering
        window_snapshots = [(w._hWnd, w.title) for w in windows]

        # 1. Start Telegram Request if active
        if self.telegram_bot and self.telegram_bot.is_running:
            self.telegram_bot.input_value = None
            self.telegram_bot.input_event.clear()
            self.telegram_bot.expecting_input = True

            # Format window list
            msg = "🪟 **SELECT WINDOW**\nReply with ID:\n\n"
            for i, (hwnd, title) in enumerate(window_snapshots[:20]):
                short = title[:30] + "..." if len(title) > 30 else title
                msg += f"`{i}`: {short}\n"

            self.telegram_bot.request_input(msg)

        # 2. Setup GUI Dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Select Browser Window")
        dialog.geometry("500x400")
        dialog.grab_set()  # Make dialog modal

        # Center dialog
        dialog.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception: pass

        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill="both", expand=True)

        # Title label
        title_label = ttk.Label(main_frame, text="Choose a window to track:", font=("Arial", 10, "bold"))
        title_label.pack(pady=(0, 10))

        # Create frame for listbox and scrollbar
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        # Listbox with scrollbar
        listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            width=60,
            height=15,
            font=("Consolas", 11),
            selectmode=tk.SINGLE
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=listbox.yview)

        # Populate listbox from snapshot (stable regardless of new windows appearing)
        for i, (hwnd, title) in enumerate(window_snapshots):
            short = title[:80] + "..." if len(title) > 80 else title
            listbox.insert(tk.END, f"{i:2d}: {short}")

        # Select first item by default
        if listbox.size() > 0:
            listbox.selection_set(0)

        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(10, 0))

        result = {"selected_hwnd": None, "selected_title": None}

        def on_select():
            selection = listbox.curselection()
            if selection:
                idx = selection[0]
                if idx < len(window_snapshots):
                    result["selected_hwnd"] = window_snapshots[idx][0]
                    result["selected_title"] = window_snapshots[idx][1]
                dialog.destroy()
            else:
                messagebox.showwarning("Warning", "Please select a window.")

        def on_cancel():
            dialog.destroy()

        # Buttons
        select_btn = ttk.Button(button_frame, text="Select", command=on_select)
        select_btn.pack(side="right", padx=(5, 0))

        cancel_btn = ttk.Button(button_frame, text="Cancel", command=on_cancel)
        cancel_btn.pack(side="right")

        # Bind double-click to select
        listbox.bind("<Double-Button-1>", lambda e: on_select())

        # Bind Enter key to select
        listbox.bind("<Return>", lambda e: on_select())

        # Focus on listbox
        listbox.focus_set()

        # 3. Wait Loop (Hybrid)
        while dialog.winfo_exists():
            # Check Telegram
            if self.telegram_bot and self.telegram_bot.is_running and self.telegram_bot.input_event.is_set():
                val = self.telegram_bot.input_value
                try:
                    idx = int(val.strip())
                    if 0 <= idx < len(window_snapshots):
                        result["selected_hwnd"] = window_snapshots[idx][0]
                        result["selected_title"] = window_snapshots[idx][1]
                        self.telegram_bot.send_notification(f"✅ Selected: {window_snapshots[idx][1]}")
                        dialog.destroy()
                        break
                    else:
                        self.telegram_bot.send_notification("❌ Invalid Index. Try again.")
                        self.telegram_bot.expecting_input = True
                        self.telegram_bot.input_event.clear()
                except ValueError:
                    self.telegram_bot.send_notification("❌ Invalid Format. Send ID (Number).")
                    self.telegram_bot.expecting_input = True
                    self.telegram_bot.input_event.clear()

            self.root.update()
            time.sleep(0.05)

        # Cleanup telegram expectation
        if self.telegram_bot:
            self.telegram_bot.expecting_input = False

        # Process result — select by HWND, not by index
        if result["selected_hwnd"] is not None:
            try:
                if self.recorder.select_window_by_hwnd(result["selected_hwnd"]):
                    self.selected_window_title = result["selected_title"]
                    # Update Dashboard window label
                    if hasattr(self, 'dash_window_label'):
                        short_title = self.selected_window_title[:40] + ('...' if len(self.selected_window_title) > 40 else '')
                        self.dash_window_label.configure(text=f"✅ {short_title}", text_color="#2ecc71")
                    self.log_to_dashboard(f"Window selected: {self.selected_window_title[:30]}")

                    # Show gold border watermark on the selected window
                    self._attach_window_watermark()

                    # Remote Ack
                    self.show_remote_info_blocking("Selected", f"Tracking window: {self.selected_window_title}")
                else:
                    self.show_remote_error("Error", "Failed to select window.")

            except (ValueError, IndexError) as e:
                self.show_remote_error("Error", f"Invalid selection: {e}")

    def simple_input(self, prompt, title):
        """Get input from local GUI or Telegram"""
        try:
            # 1. Start Telegram Request if active
            if self.telegram_bot and self.telegram_bot.is_running:
                self.telegram_bot.input_value = None
                self.telegram_bot.input_event.clear()
                self.telegram_bot.expecting_input = True
                
                print(f"[Debug] Requesting Telegram Input: {prompt}")
                self.telegram_bot.request_input(prompt)

            # 2. Setup GUI Dialog
            input_win = tk.Toplevel(self.root)
            input_win.title(title)
            
            # Center dialog
            input_win.update_idletasks()
            try:
                x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (input_win.winfo_width() // 2)
                y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (input_win.winfo_height() // 2)
                input_win.geometry(f"+{x}+{y}")
            except Exception: pass
                
            ttk.Label(input_win, text=prompt, justify="left").grid(row=0, column=0, columnspan=2, padx=10, pady=10)

            var = tk.StringVar()
            entry = ttk.Entry(input_win, textvariable=var, width=40)
            entry.grid(row=1, column=0, columnspan=2, padx=10)
            entry.focus()
            
            result = {"value": None}

            def on_ok():
                result["value"] = var.get()
                input_win.destroy()

            def on_cancel():
                input_win.destroy()

            ttk.Button(input_win, text="OK", command=on_ok).grid(row=2, column=0, pady=10, padx=5, sticky="ew")
            ttk.Button(input_win, text="Cancel", command=on_cancel).grid(row=2, column=1, pady=10, padx=5, sticky="ew")
            input_win.protocol("WM_DELETE_WINDOW", on_cancel)
            input_win.bind("<Return>", lambda e: on_ok())
            input_win.bind("<Escape>", lambda e: on_cancel())

            # Poll Telegram asynchronously — no blocking root.update() loop
            def _poll_telegram():
                if not input_win.winfo_exists():
                    return
                if (self.telegram_bot and self.telegram_bot.is_running
                        and self.telegram_bot.input_event.is_set()):
                    result["value"] = self.telegram_bot.input_value
                    input_win.destroy()
                    return
                input_win.after(100, _poll_telegram)

            if self.telegram_bot and self.telegram_bot.is_running:
                input_win.after(100, _poll_telegram)

            input_win.wait_window()

            if self.telegram_bot:
                self.telegram_bot.expecting_input = False

            return result["value"]
        except Exception as e:
            logger.error(f"Error in simple_input: {e}")
            messagebox.showerror("Error", f"Input dialog failed: {e}")
            return None

    def show_remote_error(self, title, message):
        """Show error locally and send to Telegram"""
        if self.telegram_bot:
            self.telegram_bot.send_notification(f"❌ **ERROR: {title}**\n{message}")
        messagebox.showerror(title, message)

    def show_remote_info_blocking(self, title, message):
        """Show info locally; send a non-blocking notification to Telegram."""
        # Non-blocking Telegram notification — never block the GUI thread here.
        if self.telegram_bot and self.telegram_bot.is_running:
            self.telegram_bot.send_notification(f"ℹ️ *{title}*\n{message}")

        # Local modal dialog — wait_window() processes events normally (no freeze).
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.geometry("300x150")
        dialog.attributes("-topmost", True)

        ctk.CTkLabel(dialog, text=message, wraplength=280).pack(pady=20)
        ctk.CTkButton(dialog, text="OK", command=dialog.destroy, width=100).pack(pady=10)

        dialog.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception:
            pass

        dialog.wait_window()

    def ask_hybrid_confirmation(self, title, prompt):
        """Ask Yes/No via GUI or Telegram"""
        print(f"[Debug] Asking Confirmation: {prompt}")
        # 1. Start Telegram Request if active
        if self.telegram_bot and self.telegram_bot.is_running:
            self.telegram_bot.confirmation_value = None
            self.telegram_bot.confirmation_event.clear()
            self.telegram_bot.expecting_confirmation = True
            
            print("[Debug] Sending Telegram confirmation request...")
            # Use non-blocking send, we handle the wait loop here manually
            self.telegram_bot.send_confirmation_request(prompt)
            # Removed silent try/except to reveal errors

        # 2. Setup GUI Dialog (Using messagebox is blocking, so we need Toplevel)
        # Using Toplevel for non-blocking local check
        result = {"value": None}
        
        dialog = ctk.CTkToplevel(self.root)
        dialog.title(title)
        dialog.geometry("300x150")
        dialog.attributes("-topmost", True)
        
        ctk.CTkLabel(dialog, text=prompt, wraplength=280).pack(pady=20)
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)
        
        def on_yes():
            result["value"] = True
            dialog.destroy()
            
        def on_no():
            result["value"] = False
            dialog.destroy()
            
        ctk.CTkButton(btn_frame, text="Yes", command=on_yes, width=80, fg_color="green").pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="No", command=on_no, width=80, fg_color="red").pack(side="left", padx=10)
        
        # Center dialog
        dialog.update_idletasks()
        try:
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
            dialog.geometry(f"+{x}+{y}")
        except Exception: pass

        # 3. Wait Loop
        while dialog.winfo_exists():
            # Check Telegram
            if self.telegram_bot and self.telegram_bot.is_running and self.telegram_bot.confirmation_event.is_set():
                result["value"] = self.telegram_bot.confirmation_value
                dialog.destroy()
                break
                
            self.root.update()
            time.sleep(0.05)
            
        # Cleanup telegram expectation
        if self.telegram_bot:
            self.telegram_bot.expecting_confirmation = False
            
        return result["value"]

    def start_telegram_bot(self):
        """Initialize and start the Telegram control bot"""
        token = self.config.get("telegram_token", "")
        chat_id = self.config.get("telegram_chat_id", "")
        
        if not token or not chat_id:
            logger.info("Telegram Bot not configured (Token or Chat ID missing).")
            return

        if self.telegram_bot and self.telegram_bot.is_running:
            # If token changed? For now, we don't support hot-swapping token easily without restart or complex logic
            if self.telegram_bot.token != token:
                 print("Telegram configuration changed, restart required for bot updates.")
            return

        print(f"Starting Telegram Bot... (ID: {chat_id})")
        self.telegram_bot = RouletteTelegramBot(token, chat_id, self)
        self.telegram_bot.start()
        # Wait for bot loop to initialize
        if not self.telegram_bot.wait_until_ready(timeout=5):
            print("[Telegram] Warning: Bot initialization timed out.")

    def save_telegram_config(self):
        """Save Telegram settings and restart the bot thread"""
        self.config["telegram_token"] = self.telegram_token_var.get().strip()
        self.config["telegram_chat_id"] = self.telegram_chat_id_var.get().strip()
        save_config(self.config)
        self.start_telegram_bot()
        messagebox.showinfo("Saved", "Telegram configuration saved.\nRemote control should be active if details are correct.")

    def _update_session_label(self):
        """Update the session status label in the subscription card."""
        if not hasattr(self, "session_status_label"):
            return
        ld = getattr(self.license_manager, "license_data", None) or {}
        session_token = getattr(self.license_manager, "session_token", None)
        session_started = ld.get("session_started_at", "")
        if session_token and session_started:
            try:
                from datetime import datetime, timezone
                started = datetime.fromisoformat(session_started.replace("Z", "+00:00"))
                session_str = "Active since " + started.strftime("%d %b %Y %H:%M")
                session_color = "#10b981"
            except Exception:
                session_str = "Active"
                session_color = "#10b981"
        elif session_token:
            session_str = "Active"
            session_color = "#10b981"
        else:
            session_str = "No active session"
            session_color = "#6b7280"
        self.session_status_label.configure(text=session_str, text_color=session_color)

    def _refresh_license_status(self):
        """Re-validate license from Supabase and apply any tier changes immediately."""
        if not hasattr(self, "license_manager") or not self.license_manager.is_authenticated:
            messagebox.showinfo("Not logged in", "Please log in first.")
            return
        valid, msg = self.license_manager.validate_license(force_refresh=True)
        if valid:
            new_tier = self.license_manager.license_data.get("subscription_tier", "FREE")
            self._apply_tier_change(new_tier)
            self._update_session_label()
            messagebox.showinfo("License Refreshed", f"Status: Active\nPlan: {new_tier}")
        else:
            messagebox.showerror("License Check Failed", msg)

    def _logout_and_restart(self):
        """Log out the current user and restart the app."""
        if not messagebox.askyesno("Log Out", "Are you sure you want to log out?\nThe app will restart."):
            return
        if hasattr(self, "license_manager"):
            self.license_manager.logout()
        import sys, os
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def save_all_settings(self):
        """Gather all UI variable values into self.config and save to disk."""
        # Bot Control vars
        try:
            self.config["num_sessions"] = int(self.num_sessions_var.get() or 1)
        except (ValueError, TypeError):
            self.config["num_sessions"] = 1
        self.config["min_gap"] = self.min_gap_var.get()
        self.config["max_gap"] = self.max_gap_var.get()
        self.config["session_timing"] = self.session_timing_var.get()
        self.config["start_time"] = self.start_time_var.get()
        self.config["end_time"] = self.end_time_var.get()
        
        # Auto Roulette vars
        self.config["auto_roulette_strategy"] = self.auto_roulette_strategy_var.get()
        self.config["auto_roulette_progression"] = self.auto_roulette_progression_var.get()
        self.config["auto_roulette_k"] = self.auto_roulette_k_var.get()
        
        # Telegram vars
        if hasattr(self, 'telegram_token_var'):
            self.config["telegram_token"] = self.telegram_token_var.get().strip()
        if hasattr(self, 'telegram_chat_id_var'):
            self.config["telegram_chat_id"] = self.telegram_chat_id_var.get().strip()
        
        # Dynamic rules
        self.config["dynamic_rules"] = self.dynamic_rules
        self.config["coordinates"] = self.coordinates
        
        save_config(self.config)
        print("DEBUG: All settings saved to disk.")
        return True

    def on_closing(self):
        """Handle window close: autosave settings and destroy."""
        try:
            self.save_all_settings()
        except Exception as e:
            print(f"DEBUG: Error saving on close: {e}")

        # Stop background threads
        self.winning_number_watcher_running = False
        self.bot_running = False

        # Release global hotkeys so they don't linger after exit.
        try:
            self._unregister_global_hotkeys()
        except Exception:
            pass

        if self.telegram_bot and self.telegram_bot.is_running:
            try:
                self.telegram_bot.stop()
            except Exception:
                pass

        self.root.destroy()

    def _show_upgrade_dialog(self, required_tier: str = "BASIC"):
        """Show a styled upsell dialog prompting FREE users to upgrade."""
        import webbrowser

        TIER_FEATURES = {
            "BASIC":  ["Strategy Builder", "Bot start", "Bundle access"],
            "PLUS":   ["Advanced Strategy Builder", "Backtesting", "All BASIC features"],
            "PRO":    ["Bot Control tab", "Auto Roulette tab", "All PLUS features"],
        }
        PRICES = {
            "BASIC":  [("1 Week", "$49"), ("1 Month", "$149"), ("3 Months", "$379"), ("6 Months", "$649"), ("Lifetime", "$899")],
            "PLUS":   [("1 Week", "$89"), ("1 Month", "$279"), ("3 Months", "$699"), ("6 Months", "$1,199"), ("Lifetime", "$1,699")],
            "PRO":    [("1 Week", "$149"), ("1 Month", "$449"), ("3 Months", "$1,099"), ("6 Months", "$1,899"), ("Lifetime", "$2,699")],
        }
        TIER_COLORS = {"BASIC": "#3b82f6", "PLUS": "#a855f7", "PRO": "#f59e0b"}

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Upgrade Required")
        dialog.geometry("620x520")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.focus_set()

        ctk.CTkLabel(dialog, text="Upgrade Your Plan",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(pady=(28, 4))
        ctk.CTkLabel(dialog, text=f"This feature requires {required_tier} or higher.",
                     font=ctk.CTkFont(size=13), text_color="#a1a1aa").pack(pady=(0, 18))

        tabs = ctk.CTkTabview(dialog, width=560, height=280)
        tabs.pack(padx=20)

        show_tiers = ["BASIC", "PLUS", "PRO"]
        for tier in show_tiers:
            tabs.add(tier)
            frame = tabs.tab(tier)
            color = TIER_COLORS[tier]

            ctk.CTkLabel(frame, text=f"{tier} TIER",
                         font=ctk.CTkFont(size=14, weight="bold"),
                         text_color=color).pack(pady=(10, 4))
            ctk.CTkLabel(frame, text="\n".join(f"✓  {f}" for f in TIER_FEATURES[tier]),
                         font=ctk.CTkFont(size=12), text_color="#d4d4d8",
                         justify="left").pack(pady=(0, 10))

            price_frame = ctk.CTkFrame(frame, fg_color="transparent")
            price_frame.pack()
            for label, price in PRICES[tier]:
                ctk.CTkLabel(price_frame, text=f"{label}: {price}",
                             font=ctk.CTkFont(size=11), text_color="#a1a1aa").pack(side="left", padx=8)

        if required_tier in show_tiers:
            tabs.set(required_tier)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=20)

        ctk.CTkButton(
            btn_frame, text="Buy Now at spinedge.pro",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#10b981", hover_color="#059669",
            width=220, height=42,
            command=lambda: (webbrowser.open("https://spinedge.pro/shop"), dialog.destroy())
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            btn_frame, text="Maybe Later",
            font=ctk.CTkFont(size=13),
            fg_color="#27272a", hover_color="#3f3f46",
            width=130, height=42,
            command=dialog.destroy
        ).pack(side="left", padx=8)

    def start_bot(self):
        track("session_start", {"strategy": self.strategy_var.get() if hasattr(self, 'strategy_var') else "unknown", "mode": "bot"})
        # --- LICENSE CHECK ---
        if not self.license_manager.is_licensed or self.license_tier == "FREE":
            self._show_upgrade_dialog("BASIC")
            return
        # ---------------------

        # Update config first
        try:
            # Check for Window Selection
            if self.selected_window_title:
                # Ask if user wants to change current window
                if self.ask_hybrid_confirmation("Check Window", f"Current Window: {self.selected_window_title}\nDo you want to change it?"):
                    self.select_window_dialog()
            else:
                self.select_window_dialog()
                
            if not self.selected_window_title:
                self.show_remote_error("Error", "No Window Selected. Bot cannot start.")
                return

            num_sessions = int(self.num_sessions_var.get() or 1)
        except ValueError:
            num_sessions = 1
            
        if num_sessions < 1:
            self.show_remote_error("Error", "Number of sessions must be at least 1")
            return
        
        # Validate gap settings (required for both random and scheduled)
        min_gap = self.min_gap_var.get()
        max_gap = self.max_gap_var.get()
        if min_gap >= max_gap:
            self.show_remote_error("Error", "Min gap must be less than max gap")
            return
        if min_gap < 0 or max_gap < 0:
            self.show_remote_error("Error", "Gap times must be positive")
            return
        
        if self.session_timing_var.get() == "scheduled":
            # Validate time format for scheduled sessions
            try:
                start_time = self.start_time_var.get()
                end_time = self.end_time_var.get()
                datetime.strptime(start_time, "%H:%M")
                datetime.strptime(end_time, "%H:%M")
            except ValueError:
                self.show_remote_error("Error", "Invalid time format. Use HH:MM (e.g., 09:00)")
                return

        # Prompt user to confirm or update balance
        current_bal_display = self.config.get("current_balance", 0.0)
        
        # UX Improvement: Clearer prompt
        # "Yes" -> I want to update it
        # "No" -> Keep it as is
        prompt_msg = f"Current saved balance is ${current_bal_display:.2f}.\n\nDo you want to UPDATE this balance?"
        
        print(f"[Debug] Asking balance update confirmation: {prompt_msg}")
        if self.ask_hybrid_confirmation("Balance Check", prompt_msg):
            print("[Debug] User chose to UPDATE balance.")
            while True:
                try:
                    bal = self.simple_input("Enter your ACTUAL current balance:", "Update Balance")
                    if bal is None: 
                        print("[Debug] Balance update cancelled.")
                        break 
                    
                    # Clean input
                    bal = bal.replace('$', '').strip()
                    if not bal: continue 

                    bal_val = float(bal)
                    self.config["current_balance"] = bal_val
                    save_config(self.config)
                    # Update stats display
                    self.update_stats_display(starting_balance=bal_val, projected_balance=bal_val, current_balance=bal_val)
                    self.show_remote_info_blocking("Success", f"Balance updated to: {bal_val}")
                    break
                except ValueError:
                    self.show_remote_error("Invalid Input", "Please enter a valid number (e.g. 100.50)")
                except Exception as e:
                    logger.error(f"Balance update error: {e}")
                    self.show_remote_error("Error", f"Failed to update balance: {e}")
                    break
        else:
             print("[Debug] User chose to KEEP existing balance.")

        self.config.update({
            "strategy": self.strategy_var.get(),
            "progression_type": self.progression_var.get(),
            "base_bet": float(self.base_bet_var.get() or 1.0),
            "max_loss": self.max_loss_var.get() or 100.0,
            "max_bet": float(self.max_bet_var.get() or 100.0),
            "observation_trigger": int(self.observation_trigger_var.get() or 0),
            # 0 = disabled (per-strategy only, via bundle entry suffix). Preserve
            # any explicit value already in config but never default to a cap.
            "max_consec_losses": int(self.config.get("max_consec_losses", 0)),
            "session_duration_minutes": int(self.session_duration_var.get() or 60),
            "num_sessions": num_sessions,
            "session_timing": self.session_timing_var.get(),
            "start_time": self.start_time_var.get(),
            "end_time": self.end_time_var.get(),
            "min_gap_minutes": self.min_gap_var.get(),
            "max_gap_minutes": self.max_gap_var.get(),
            "max_table_state_failures": self.max_table_state_failures_var.get(),
            "table_state_reset_cooldown": self.table_state_reset_cooldown_var.get(),
            "coordinates": self.coordinates,
            "custom_strategies": self.custom_strategies,
            
            # Logic Fix: If user sets 0 (disabled), legacy engine treats it as "Stop at 0".
            # So if 0, we pass a high number (e.g. 1000) for legacy support, 
            # while the new SessionManager handles the actual streak stopping (checking > 0).
            "profit_target": self.profit_target_var.get(), 
            "win_streak_target": int(self.max_session_wins_streak_var.get() or 0), 
            "enable_profit_target": self.enable_session_stops_var.get(), 
            "enable_win_streak_target": self.enable_session_stops_var.get(),
            "enable_session_stops": self.enable_session_stops_var.get(),
            "enable_trailing_stop": self.enable_trailing_stop_var.get(),
            "trailing_stop_amount": self.trailing_stop_amount_var.get(), 
            "session_ext_after_win": self.session_ext_after_win_var.get(),
            "session_ext_at_high": self.session_ext_at_high_var.get(),
            "max_extension_rounds": int(self.max_ext_rounds_var.get() or 20),
            "extension_give_up_amount": float(self.ext_give_up_var.get() or 50.0),
            "max_session_wins_streak": int(self.max_session_wins_streak_var.get() or 0),
            "max_session_losses_streak": int(self.max_session_losses_streak_var.get() or 0),
            "enable_escalation_on_loss": self.enable_escalation_on_loss_var.get(),
            "escalation_multiplier": float(self.escalation_multiplier_var.get() or 2.0),
            "escalation_max_steps": int(self.escalation_max_steps_var.get() or 4),
            "escalation_per_step": str(self.escalation_per_step_var.get() or "").strip(),
            "current_balance": self.config["current_balance"],
            # Global Stops
            "enable_global_stop": self.enable_global_stop_var.get(),
            "global_profit_stop": self.global_profit_stop_var.get(), 
            "global_stop_loss": self.global_stop_loss_var.get(), 
            # Strategy rotation settings
            "enable_strategy_rotation": self.enable_strategy_rotation_var.get(),
            "rotation_mode": self.rotation_mode_var.get(),
            "rotation_trigger": self.rotation_trigger_var.get(),
            "carry_progression_on_switch": self.carry_progression_var.get(),
            "reset_rotation_on_session": self.reset_rotation_on_session_var.get(),
            "switch_after_n_losses": self.switch_after_n_losses_var.get(),
            "rotation_strategies": self.rotation_strategies_var.get(),
            "rotation_progression_override": self.rotation_progression_override_var.get(),
            # Telegram Settings
            "telegram_token": self.telegram_token_var.get(),
            "telegram_chat_id": self.telegram_chat_id_var.get(),
        })

        save_config(self.config)
        self.show_remote_info_blocking("Info", "Config saved.")
        # Reset session timestamp for new session
        self.reset_session_timestamp()
        
        self.start_button.configure(state="disabled")
        self.stop_bot_button.configure(state="normal")
        self.bot_running = True
        # Seed the keep-alive idle timer from bot start so the first real
        # bet (or 3-min idle threshold, whichever comes first) is the
        # baseline. Without this, the very first sit-out stretch could
        # trip the time-based keep-alive based on stale state.
        self._last_real_bet_at = time.time()
        self._consecutive_sitouts = 0
        
        # Update Dashboard controls
        if hasattr(self, 'dash_start_btn'):
            self.dash_start_btn.configure(state="disabled", fg_color="gray")
        if hasattr(self, 'dash_stop_btn'):
            self.dash_stop_btn.configure(state="normal", fg_color="#c0392b")
        if hasattr(self, 'dash_pause_btn'):
            self.dash_pause_btn.configure(state="normal", fg_color="#f39c12")
        if hasattr(self, 'dash_status_dot'):
            self.dash_status_dot.configure(text_color="#2ecc71")
            self.dash_status_text.configure(text="  Bot running", text_color="#2ecc71")
        self.log_to_dashboard(f"Bot started")
        
        # Initialize cumulative separate history for multi-session graph
        self.pnl_history = [0.0]
        self.peak_net_profit = 0.0
        self.initial_run_balance = self.config.get("current_balance", 0)
        self.stop_all_sessions = False # Reset global stop flag
        # Clear stop/pause context from any previous run so the telegram banner
        # doesn't keep showing the old reason after a fresh start.
        self.last_stop_reason = ""
        self.paused_by = ""

        # Snapshot the starting base bet and session stop-loss so escalation
        # knows the values to scale from and to restore to. These are taken
        # AFTER the var → config sync above so any user override / risk
        # profile selected just before pressing Start is honored.
        try:
            self._escalation_initial_base_bet = float(self.config.get("base_bet", 1.0))
        except (TypeError, ValueError):
            self._escalation_initial_base_bet = 1.0
        try:
            self._escalation_initial_max_loss = float(
                self.parse_hybrid_value(self.config.get("max_loss"), self.initial_run_balance)
            )
        except Exception:
            self._escalation_initial_max_loss = float(self.config.get("max_loss", 100.0) or 100.0)
        self._escalation_step = 0
        # Reset the all-time-high tracker for the new run so the first session's
        # peak isn't compared against a leftover from a previous bot run.
        self._peak_global_pnl = 0.0
        self.cumulative_profit_offset = 0.0
        self.pnl_history = [0.0]
        self.cumulative_profit_offset = 0.0
        self.graph_markers = []  # Markers for session start (index, label)
        self.bot_global_start_time = time.time()
        self.current_session_num = 0  # reset so stop_bot summary guard works correctly
        if getattr(self, 'overlay', None):
            self.overlay.start_global_timer()

        # Ensure window is focused before starting
        if self.recorder.browser_win:
             print("[Debug] auto-focusing selected window...")
             self.recorder.activate_window_with_click()

        if getattr(self, '_sessions_thread', None) and self._sessions_thread.is_alive():
            logger.warning("Sessions thread already running")
            return
        self._sessions_thread = threading.Thread(target=self.run_multiple_sessions, daemon=True)
        self._sessions_thread.start()

    def run_multiple_sessions(self):
        """Run multiple bot sessions with specified timing"""
        try:
            num_sessions = self.config["num_sessions"]
            session_timing = self.config["session_timing"]
            
            self.total_sessions = num_sessions
            self.current_session_num = 0
            
            # Initialize rotation / conditional triggers from the current config.
            # Extracted so the mid-session re-arm path can rebuild identically.
            self._setup_rotation_and_triggers()

            self.log_simulation(f"🚀 Starting {num_sessions} session(s) with {session_timing} timing")
            
            if session_timing == "random":
                self.run_random_sessions(num_sessions)
            elif session_timing == "scheduled":
                self.run_scheduled_sessions(num_sessions)
            else:
                # Fallback to single session
                self.run_bot()
                
        except Exception as e:
            self.log_simulation(f"❌ Multiple sessions error: {e}")
            print(f"❌ Multiple sessions error: {e}")
        finally:
            self.start_button.configure(state="normal")
            with self._state_lock:
                self.bot_running = False

    def handle_remote_config(self, key, value, var_name):
        """Update config from remote bot (Thread Safe)"""
        try:
            print(f"[Remote] Attempting update: {key} -> {value} (Var: {var_name})")

            with self._config_lock:
                # Update Config Dict
                if key in self.config or key.startswith("global") or key.startswith("enable"):
                    self.config[key] = value

                    # Update Tkinter Var
                    if hasattr(self, var_name):
                        try:
                           var = getattr(self, var_name)
                           # Handle BooleanVar specific (prefer ints)
                           if isinstance(var, tk.BooleanVar):
                               value = 1 if value else 0
                           var.set(value)
                           print(f"[Remote] Set {var_name} check to {value}")
                        except Exception as e:
                            print(f"Error setting var {var_name}: {e}")
                    else:
                        print(f"[Remote] Warning: Var {var_name} not found in GUI.")

                # Save Config
                save_config(self.config)
            print(f"[Remote] Saved config. {key}={value}")
            
            # Apply changes to running session if applicable
            self.update_runtime_limits()
            
        except Exception as e:
            print(f"[Remote] Config Update Error: {e}")

    def update_runtime_limits(self):
        """Re-calculate and apply limits to running session manager"""
        if not hasattr(self, 'session_manager') or not self.session_manager:
            return

        # Determine reference balance (Session Start Balance if running, else current)
        ref_bal = getattr(self, 'session_start_balance', 0.0)
        if ref_bal == 0.0:
             ref_bal = self.config.get("current_balance", 0.0)
             
        # Parse Limits
        profit_target = self.parse_hybrid_value(self.config.get("profit_target"), ref_bal)
        max_loss = self.parse_hybrid_value(self.config.get("max_loss"), ref_bal)
        trailing_stop = self.parse_hybrid_value(self.config.get("trailing_stop_amount"), ref_bal)
        
        # Check Master Switch
        sess_stops_active = self.enable_session_stops_var.get()
        
        # Apply to Session Manager
        self.session_manager.stop_loss = max_loss if sess_stops_active else 0
        self.session_manager.profit_target = profit_target
        self.session_manager.trailing_stop = trailing_stop
        
        # Update Config Flags in Session Manager
        if hasattr(self.session_manager, 'config') and self.session_manager.config:
             self.session_manager.config["enable_profit_target"] = sess_stops_active and profit_target > 0
             self.session_manager.config["enable_trailing_stop"] = self.enable_trailing_stop_var.get()
             self.session_manager.config["enable_session_stops"] = sess_stops_active
             
        print(f"[Runtime Update] Stops Active: {sess_stops_active}, TP: {profit_target}, SL: {self.session_manager.stop_loss}")

    def should_end_session(self):
        """Check if session should end based on stop conditions (User request, Win/Loss limits, Profit targets)"""
        # 1. User Stop Request
        if not self.bot_running:
            return True

        # 2. Global Stop Conditions (Checked every time)
        # 2. Global Stop Conditions (Checked every time)
        # Calculate Global PnL (Total PnL across all sessions this run)
        current_global_pnl = getattr(self, 'cumulative_profit_offset', 0.0) + getattr(self, 'cumulative_net_profit', 0.0)
        
        print(f"[StopCheck] Sess PnL: {getattr(self, 'cumulative_net_profit', 0):.2f}, Global PnL: {current_global_pnl:.2f}, Sess Limit: {getattr(self, 'active_session_loss_limit', 'NA')}, Glob Limit: {getattr(self, 'active_global_loss_limit', 'NA')}")

        print(f"[StopCheck] Sess PnL: {getattr(self, 'cumulative_net_profit', 0):.2f}, Global PnL: {current_global_pnl:.2f}, Sess Limit: {getattr(self, 'active_session_loss_limit', 'NA')}, Glob Limit: {getattr(self, 'active_global_loss_limit', 'NA')}")

        # Check 'Enabled' checkbox LIVE
        global_stop_enabled = self.config.get("enable_global_stop", False)
        if hasattr(self, 'enable_global_stop_var'):
            global_stop_enabled = self.enable_global_stop_var.get()

        print(f"[StopCheck] Global Stop Enabled: {global_stop_enabled}")

        if global_stop_enabled:
            # Global Profit Check (Fixed)
            if self.active_global_profit_limit > 0:
                 print(f"[StopCheck Profit] GlobalPnL: {current_global_pnl:.4f} vs Limit: {self.active_global_profit_limit:.4f} | Enabled: {global_stop_enabled}")

            if self.active_global_profit_limit > 0 and current_global_pnl >= self.active_global_profit_limit:
                print(f"🛑 GLOBAL PROFIT LIMIT REACHED: ${current_global_pnl:.2f} >= ${self.active_global_profit_limit:.2f}")
                self.last_stop_reason = "Global profit limit"
                self.stop_all_sessions = True
                return True

            # Global Profit Check (Percentage)
            if hasattr(self, 'global_profit_pct_stop_var'):
                try:
                    pct_limit = float(self.global_profit_pct_stop_var.get())
                    start_bal = getattr(self, 'initial_run_balance', 0)
                    if pct_limit > 0 and start_bal > 0:
                        current_pct = (current_global_pnl / start_bal) * 100
                        if current_pct >= pct_limit:
                            print(f"🛑 GLOBAL PERCENTAGE PROFIT REACHED: {current_pct:.2f}% >= {pct_limit:.2f}%")
                            self.last_stop_reason = "Global profit % limit"
                            self.stop_all_sessions = True
                            return True
                except Exception: pass

            # Global Stop after Cons. Wins/Losses (if variables exist)
            if hasattr(self, 'stop_after_wins_var'):
                try:
                    target_wins = int(self.stop_after_wins_var.get())
                    curr_wins = getattr(self, 'consecutive_wins', 0) 
                    if target_wins > 0 and curr_wins >= target_wins:
                         print(f"🛑 STOP AFTER WINS REACHED: {curr_wins} >= {target_wins}")
                         self.last_stop_reason = f"Win streak target ({curr_wins})"
                         self.stop_all_sessions = True
                         return True
                except Exception: pass
                
            if hasattr(self, 'stop_after_losses_var'):
                try:
                    target_losses = int(self.stop_after_losses_var.get())
                    curr_losses = getattr(self, 'consecutive_losses', 0)
                    if target_losses > 0 and curr_losses >= target_losses:
                         print(f"🛑 STOP AFTER LOSSES REACHED: {curr_losses} >= {target_losses}")
                         self.last_stop_reason = f"Loss streak target ({curr_losses})"
                         self.stop_all_sessions = True
                         return True
                except Exception: pass
                    
            # Global Loss Check Debugging
            if self.active_global_loss_limit > 0:
                loss_threshold = -self.active_global_loss_limit
                is_triggered = current_global_pnl <= loss_threshold
                print(f"[StopCheck Details] PnL: {current_global_pnl:.4f} vs Limit: {loss_threshold:.4f} | Triggered: {is_triggered}")
                
            # Global Loss Check 
            if self.active_global_loss_limit > 0 and current_global_pnl <= -self.active_global_loss_limit:
                  print(f"🛑 GLOBAL STOP LOSS REACHED: ${current_global_pnl:.2f} <= -${self.active_global_loss_limit:.2f}")
                  self.last_stop_reason = "Global stop-loss"
                  self.stop_all_sessions = True
                  return True

        # 3. Session Stop Conditions
        # Session Profit Target
        # 3. Session Stop Conditions
        
        # A. Session Stop Loss (PRIORITY: SAFETY)
        sess_stops_active = self.enable_session_stops_var.get() if hasattr(self, 'enable_session_stops_var') else self.config.get("enable_session_stops", False)
        if sess_stops_active and self.active_session_loss_limit > 0 and self.cumulative_net_profit <= -self.active_session_loss_limit:
            print(f"🛑 SESSION STOP LOSS REACHED: ${self.cumulative_net_profit:.2f} <= -${self.active_session_loss_limit:.2f}")
            self.last_stop_reason = "Session stop-loss"
            return True

        # B. Extension Checks (PRIORITY: OVERRIDE SOFT STOPS like Profit Target, Trailing Stop, Time Limit)
        
        # Extension: Until Win (Only if active)
        if hasattr(self, 'session_ext_after_win_var') and self.session_ext_after_win_var.get():
             last_res = getattr(self, 'last_bet_result', None)
             if last_res != 'win':
                 # If we haven't won, we extend (skip remaining checks)
                 print(f"🔄 Extension: Extending session until WIN (Last: {last_res})")
                 return False

        # Extension: Until High (Recovery Mode)
        if hasattr(self, 'session_ext_at_high_var') and self.session_ext_at_high_var.get():
             curr = self.cumulative_net_profit
             high = getattr(self, 'peak_net_profit', 0)
             # "Only end session at session high" -> If current < high, EXTEND.
             if curr < high - 0.01:
                 print(f"🔄 Extension: Extending session until HIGH (Curr: {curr:.2f} < Peak: {high:.2f})")
                 return False

        # C. Soft Stops (Can be overridden by extensions)
        # Read the live Tk vars instead of self.config[...]: start_bot snapshots
        # the var value into config once, but if the user toggles the master
        # session-stops checkbox or trailing-stop checkbox AFTER the run starts,
        # config stays stale. Reading the vars makes mid-run toggles effective.

        # Session Profit Target — gated by the session-stops master toggle.
        sess_stops_live = (
            self.enable_session_stops_var.get()
            if hasattr(self, 'enable_session_stops_var')
            else self.config.get("enable_profit_target", False)
        )
        if sess_stops_live:
            if self.active_session_profit_limit > 0 and self.cumulative_net_profit >= self.active_session_profit_limit:
                print(f"🛑 SESSION PROFIT TARGET REACHED: ${self.cumulative_net_profit:.2f} >= ${self.active_session_profit_limit:.2f}")
                self.last_stop_reason = "Session profit target"
                return True

        # Trailing Stop — gated by its own toggle (independent of session stops).
        trailing_live = (
            self.enable_trailing_stop_var.get()
            if hasattr(self, 'enable_trailing_stop_var')
            else self.config.get("enable_trailing_stop", False)
        )
        if trailing_live:
            # Use active_trailing_stop_limit parsed in run_bot
            trailing_amt = getattr(self, 'active_trailing_stop_limit', 0.0)
            if trailing_amt > 0:
                drop_from_peak = self.peak_net_profit - self.cumulative_net_profit
                if drop_from_peak >= trailing_amt:
                    print(f"🛑 TRAILING STOP TRIGGERED: Drop ${drop_from_peak:.2f} >= ${trailing_amt:.2f} (Peak: ${self.peak_net_profit:.2f})")
                    self.last_stop_reason = f"Trailing stop (-${drop_from_peak:.2f} from peak)"
                    return True
                    
        # 4. Session Time Limit
        try:
            # Default to 60 minutes if invalid
            session_duration_minutes = 60
            if hasattr(self, 'session_duration_var'):
                val = self.session_duration_var.get()
                if val and str(val).strip():
                    session_duration_minutes = int(val)
            
            session_duration_seconds = session_duration_minutes * 60
            
            # Calculate effective duration (wall time minus paused time)
            # Use timestamp set in run_bot
            start_ts = getattr(self, 'session_start_timestamp', 0)
            if start_ts > 0:
                current_ts = time.time()
                elapsed_time = current_ts - start_ts
                
                # Subtract pause duration if tracked
                total_paused = getattr(self, 'total_paused_duration', 0.0)
                effective_duration = elapsed_time - total_paused
                
                if effective_duration >= session_duration_seconds:
                    print(f"🛑 SESSION TIME LIMIT REACHED: {effective_duration:.1f}s >= {session_duration_seconds:.1f}s")
                    self.last_stop_reason = "Session time limit"
                    return True
        except Exception as e:
            print(f"⚠️ Error checking session time: {e}")

        return False

    def run_random_sessions(self, num_sessions):
        """Run sessions with random gaps between them"""
        for session_num in range(1, num_sessions + 1):
            if not self.bot_running:
                break
                
            self.current_session_num = session_num
            self.update_stats_display(
                current_session=f"Session {session_num}/{num_sessions}",
                session_progress=f"{session_num}/{num_sessions}"
            )
            
            # Apply strategy rotation if enabled — ALWAYS re-pick at session start
            if self.enable_strategy_rotation_var.get():
                old_strat = self.config.get("strategy", "?")
                trigger = getattr(self, 'rotation_trigger', 'session_end')
                # For on_loss mode: re-pick best strategy at session start using rotation algorithm
                # (mid-session switches still happen on each loss)
                # For session_end mode: rotate to next strategy as before
                if trigger == 'on_loss':
                    # Reset smart ranking index so it picks the top-ranked strategy fresh
                    if hasattr(self, 'smart_ranking_index'):
                        self.smart_ranking_index = 0
                # Reset to first strategy if checkbox is enabled
                if getattr(self, 'reset_rotation_on_session_var', None) and self.reset_rotation_on_session_var.get():
                    self.current_rotation_index = 0
                    if hasattr(self, 'smart_ranking_index'):
                        self.smart_ranking_index = 0
                self.apply_rotation_strategy()
                new_strat = self.config.get("strategy", "?")
                self.log_simulation(f"🔄 Session {session_num} rotation: '{old_strat}' → '{new_strat}' (trigger={trigger})")
                print(f"🔄 Session {session_num} rotation: '{old_strat}' → '{new_strat}' (trigger={trigger})")

            self.log_simulation(f"🎯 Starting session {session_num}/{num_sessions}")
            print(f"🎯 Starting session {session_num}/{num_sessions}")

            # Reset DynamicProgression session tracking for the new session
            # (session_high and total_profit must start fresh each session,
            #  otherwise the progression thinks it's always at session high
            #  and always resets to base bet instead of following rules)
            if hasattr(self, '_live_strategy') and self._live_strategy:
                prog = getattr(self._live_strategy, 'progression', None)
                if prog and prog.__class__.__name__ == 'DynamicProgressionStrategy':
                    prog.session_high = 0.0
                    prog.total_profit = 0.0
                    current_bal = float(self.config.get("current_balance", 0.0))
                    if current_bal > 0:
                        prog.session_start_balance = current_bal
                    print(f"🔄 DynamicProgression session reset: session_high=0, total_profit=0, start_bal={prog.session_start_balance}")

            # Run the session
            self.run_bot()

            # Apply escalation-on-loss BEFORE the global-stop break so a global
            # profit hit can also reset the escalation level for the next run.
            try:
                self._apply_session_escalation()
            except Exception as e:
                print(f"[Escalation] Skipped due to error: {e}")

            # Check if bot was stopped during the session
            if not self.bot_running:
                self.log_simulation("⛔ Bot stopped during session")
                break

            # Check Global Stop Flag
            if getattr(self, 'stop_all_sessions', False):
                self.log_simulation("🛑 Global Stop Condition Met. Terminating remaining sessions.")
                with self._state_lock:
                    self.bot_running = False
                break

            # Add random gap between sessions (if not the last session)
            if session_num < num_sessions:
                min_gap = self.config.get("min_gap_minutes", 30) * 60  # Convert to seconds
                max_gap = self.config.get("max_gap_minutes", 120) * 60
                gap_seconds = random.randint(min_gap, max_gap)
                
                self.log_simulation(f"⏸️ Waiting {gap_seconds//60} minutes before next session...")
                print(f"⏸️ Waiting {gap_seconds//60} minutes before next session...")
                
                # Update display to show waiting status
                self.update_stats_display(
                    current_session=f"Waiting for Session {session_num + 1}/{num_sessions}",
                    time_remaining=f"{(gap_seconds//60):02d}:{(gap_seconds%60):02d}"
                )
                
                # Wait in smaller chunks to allow stopping and show progress
                for remaining in range(gap_seconds, 0, -1):
                    if not self.bot_running:
                        self.log_simulation("⛔ Bot stopped during wait period")
                        break
                    
                    # Update countdown every second
                    minutes = remaining // 60
                    seconds = remaining % 60
                    time_str = f"{minutes:02d}:{seconds:02d}"
                    
                    if remaining % 30 == 0:
                        self.update_stats_display(
                            time_remaining=time_str
                        )
                        self.log_simulation(f"⏰ Next session in {time_str}")
                    
                    # Update HUD every second
                    self.update_hud_safe(next_sess=time_str)
                    
                    time.sleep(1)
                # Check if user stopped manually or global stop triggered
            if not self.bot_running:
                self.log_simulation("⛔ Sessions stopped by user")
                return

            if getattr(self, 'stop_all_sessions', False):
                self.log_simulation("🛑 Global Stop Condition Met. Terminating remaining sessions.")
                self.log_message("🛑 Global Stop Condition Met. Terminating remaining sessions.")
                break

            # The original code had a check here: `if not self.bot_running: break`
            # This is now handled by the `if not self.bot_running: return` above.
            # The original code also had a final `if self.bot_running:` block.
            # The new structure implies that if we reach here and bot_running is True,
            # we continue to the next session or finish.
            # The instruction's provided snippet for `self.current_session_num += 1` and
            # `if self.current_session_num > num_sessions:` seems to be a partial
            # or malformed replacement for the final completion logic.
            # I will assume the intent is to replace the final `if self.bot_running:` block
            # with the new global stop logic and then handle completion.
            # Given the instruction, I will insert the provided code as faithfully as possible,
            # even if it results in some redundancy or requires minor adjustment for syntax.
            # The `breakssion_progress` is clearly a typo and will be corrected to `session_progress`.
            # Also, `logger.info` is not defined, so I'll use `self.log_simulation` as used elsewhere.

        if self.bot_running: # This check was originally outside the loop, now it's effectively handled by the `return` and `break` inside.
            self.log_simulation("✅ All sessions completed")
            self.update_stats_display(
                current_session="All Sessions Complete",
                session_progress=f"{num_sessions}/{num_sessions}"
            )
            self.root.after(0, self._show_session_summary)
        else:
            self.log_simulation("⛔ Sessions stopped by user")

    def run_scheduled_sessions(self, num_sessions):
        """Run sessions within specified time window"""
        start_time_str = self.config["start_time"]
        end_time_str = self.config["end_time"]
        min_gap = self.config.get("min_gap_minutes", 30)
        max_gap = self.config.get("max_gap_minutes", 120)
        
        # Parse times
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()
        
        self.log_simulation(f"📅 Scheduled sessions: {start_time_str} - {end_time_str}")
        
        session_num = 0
        while session_num < num_sessions and self.bot_running:
            current_time = datetime.now().time()
            
            # Check if we're within the allowed time window
            if start_time <= current_time <= end_time:
                session_num += 1
                self.current_session_num = session_num
                self.update_stats_display(
                    current_session=f"Scheduled Session {session_num}/{num_sessions}",
                    session_progress=f"{session_num}/{num_sessions}"
                )
                
                # Apply strategy rotation if enabled — ALWAYS re-pick at session start
                if self.enable_strategy_rotation_var.get():
                    old_strat = self.config.get("strategy", "?")
                    trigger = getattr(self, 'rotation_trigger', 'session_end')
                    if trigger == 'on_loss':
                        if hasattr(self, 'smart_ranking_index'):
                            self.smart_ranking_index = 0
                    self.apply_rotation_strategy()
                    new_strat = self.config.get("strategy", "?")
                    self.log_simulation(f"🔄 Session {session_num} rotation: '{old_strat}' → '{new_strat}' (trigger={trigger})")
                    print(f"🔄 Session {session_num} rotation: '{old_strat}' → '{new_strat}' (trigger={trigger})")

                self.log_simulation(f"🎯 Starting scheduled session {session_num}/{num_sessions}")
                print(f"🎯 Starting scheduled session {session_num}/{num_sessions}")
                
                # Run the session
                self.run_bot()
                
                # Check if bot was stopped during the session
                if not self.bot_running:
                    self.log_simulation("⛔ Bot stopped during session")
                    break
                
                # Check Global Stop Flag
                if getattr(self, 'stop_all_sessions', False):
                    self.log_simulation("🛑 Global Stop Condition Met. Terminating scheduled sessions.")
                    with self._state_lock:
                        self.bot_running = False
                    break
                
                # Add random gap between sessions (if not the last session)
                if session_num < num_sessions:
                    gap_minutes = random.randint(min_gap, max_gap)
                    self.log_simulation(f"⏸️ Waiting {gap_minutes} minutes before next session...")
                    print(f"⏸️ Waiting {gap_minutes} minutes before next session...")
                    
                    # Update display to show waiting status
                    self.update_stats_display(
                        current_session=f"Waiting for Session {session_num + 1}/{num_sessions}",
                        time_remaining=f"{gap_minutes:02d}:00",
                        next_session_timer=f"{gap_minutes:02d}:00"
                    )
                    
                    # Wait in smaller chunks to allow stopping and show progress
                    for remaining in range(gap_minutes * 60, 0, -1):
                        if not self.bot_running:
                            self.log_simulation("⛔ Bot stopped during wait period")
                            break
                        
                        # Update countdown every 30 seconds
                        if remaining % 30 == 0:
                            minutes = remaining // 60
                            seconds = remaining % 60
                            timer_str = f"{minutes:02d}:{seconds:02d}"
                            self.update_stats_display(
                                time_remaining=timer_str,
                                next_session_timer=timer_str
                            )
                            self.log_simulation(f"⏰ Next session in {timer_str}")
                        
                        time.sleep(1)
                    
                    if not self.bot_running:
                        break
            else:
                # Outside time window, wait and check again
                wait_minutes = 5
                self.log_simulation(f"⏰ Outside scheduled time ({start_time_str}-{end_time_str}), waiting {wait_minutes} minutes...")
                self.update_stats_display(
                    current_session=f"Outside Scheduled Time",
                    time_remaining=f"{wait_minutes:02d}:00",
                    next_session_timer=f"{wait_minutes:02d}:00"
                )
                
                for remaining in range(wait_minutes * 60, 0, -1):
                    if not self.bot_running:
                        break
                    
                    # Update countdown every minute
                    if remaining % 60 == 0:
                        minutes = remaining // 60
                        self.update_stats_display(
                            time_remaining=f"{minutes:02d}:00"
                        )
                    
                    time.sleep(1)
        
        if self.bot_running:
            self.log_simulation("✅ All scheduled sessions completed")
            self.update_stats_display(
                current_session="All Sessions Complete",
                session_progress=f"{num_sessions}/{num_sessions}"
            )
            self.root.after(0, self._show_session_summary)
        else:
            self.log_simulation("⛔ Sessions stopped by user")

    def toggle_pause_bot(self):
        """Toggle pause state for ANY running bot mode (Normal or Auto Roulette)"""
        # Toggle boolean states
        self.auto_roulette_paused = not self.auto_roulette_paused
        self.bot_paused = not self.bot_paused # New flag for run_bot
        
        is_paused = self.bot_paused or self.auto_roulette_paused
        state_str = "PAUSED" if is_paused else "RESUMED"
        print(f"[Bot] {state_str} by user")
        
        if is_paused:
             self.auto_roulette_status_var.set("PAUSED")
             self.pause_start_time = time.time()
             # Minimal visual feedback for normal bot if needed, though it uses console mostly
        else:
             self.auto_roulette_status_var.set("Resuming...")
             if self.pause_start_time:
                 paused_duration = time.time() - self.pause_start_time
                 self.total_paused_duration += paused_duration
                 print(f"⏱ Resuming after {paused_duration:.1f}s pause. Session extended.")
                 self.pause_start_time = None
             
        # Trigger HUD update immediately
        self.update_hud_safe(is_paused=is_paused)

    def toggle_pause(self):
        """Toggle pause state"""
        self.bot_paused = not getattr(self, 'bot_paused', False)
        # Identify alias
        self.is_paused = self.bot_paused
        # Track who paused so the telegram dashboard can surface "Paused by user"
        # — guardrail-driven pauses set this attribute themselves before flipping
        # is_paused. Default to "user" here since this method is the manual path.
        if self.bot_paused:
            if not getattr(self, 'paused_by', ''):
                self.paused_by = "user"
        else:
            self.paused_by = ""

        state = "Paused" if self.bot_paused else "Resumed"
        print(f"⏯ Bot {state}")
        self.update_hud_safe(is_paused=self.bot_paused)
        # Log to list
        self.log_simulation(f"⏯ Bot {state}.")
        
        # Update Dashboard UI
        if self.bot_paused:
            if hasattr(self, 'dash_pause_btn'):
                self.dash_pause_btn.configure(text="▶  RESUME", fg_color="#27ae60", hover_color="#2ecc71")
            if hasattr(self, 'dash_status_dot'):
                self.dash_status_dot.configure(text_color="#f39c12")
                self.dash_status_text.configure(text="  Paused", text_color="#f39c12")
        else:
            if hasattr(self, 'dash_pause_btn'):
                self.dash_pause_btn.configure(text="⏸  PAUSE", fg_color="#f39c12", hover_color="#e67e22")
            if hasattr(self, 'dash_status_dot'):
                self.dash_status_dot.configure(text_color="#2ecc71")
                self.dash_status_text.configure(text="  Bot running", text_color="#2ecc71")
        self.log_to_dashboard(f"Bot {state}")

        # Force Update Telegram Dashboard
        if hasattr(self, 'telegram_bot') and self.telegram_bot and self.telegram_bot.loop:
             try:
                 import asyncio
                 asyncio.run_coroutine_threadsafe(
                     self.telegram_bot.update_live_dashboard(force=True),
                     self.telegram_bot.loop
                 )
             except Exception: pass

    def get_graph_png(self):
        """Return graph as PNG bytes with Enhanced Styling"""
        import io
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        
        try:
            # Theme Colors
            face_color = '#121212'  # Very dark grey
            grid_color = '#333333'
            text_color = '#E0E0E0'
            profit_color = '#00FF00' # Bright Green
            loss_color = '#FF4444'   # Bright Red
            ma_color = '#FFFF00'     # Yellow
            
            # Data Preparation
            data = getattr(self, 'pnl_history', [0])
            if not data: data = [0]
            
            # Create Figure
            fig = Figure(figsize=(10, 5), dpi=100, facecolor=face_color)
            canvas = FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            ax.set_facecolor(face_color)
            
            # Main Plot Line
            current_pnl = data[-1]
            line_color = profit_color if current_pnl >= 0 else loss_color
            
            # Plot Data
            x = range(len(data))
            ax.plot(x, data, color=line_color, linewidth=2, label='PnL')
            
            # Gradient Fill (Simulated with alpha)
            ax.fill_between(x, data, 0, color=line_color, alpha=0.15)
            
            # Zero Line
            ax.axhline(y=0, color='#666666', linestyle='-', linewidth=1, alpha=0.5)
            
            # Moving Average (SMA 10) for Trend
            if len(data) > 10:
                def sma(arr, window):
                    cumsum = [0]
                    moving_aves = []
                    for i, x in enumerate(arr, 1):
                        cumsum.append(cumsum[i-1] + x)
                        if i >= window:
                            moving_ave = (cumsum[i] - cumsum[i-window]) / window
                            moving_aves.append(moving_ave)
                        else:
                            moving_aves.append(None) # Padding
                    return moving_aves

                ma_10 = sma(data, 10)
                # Filter out None values for plotting
                ma_x = [i for i, v in enumerate(ma_10) if v is not None]
                ma_y = [v for v in ma_10 if v is not None]
                
                if ma_x:
                    ax.plot(ma_x, ma_y, color=ma_color, linewidth=1.5, linestyle='--', alpha=0.7, label='MA(10)')

            # Grid & Spines
            ax.grid(True, color=grid_color, linestyle=':', alpha=0.6)
            ax.tick_params(colors=text_color, direction='out')
            for spine in ax.spines.values():
                spine.set_color(grid_color)
                
            # Markers (Session Starts)
            markers = getattr(self, 'graph_markers', [])
            if markers:
                for item in markers:
                    # Robust unpacking
                    try:
                        idx = item[0]
                        label = str(item[1])
                        if idx < len(data):
                             ax.axvline(x=idx, color='#444444', linestyle='--', alpha=0.5)
                            # Only show label if not crowded? Simple version for now.
                    except Exception: pass

            # Latest Value Annotation
            ax.scatter([len(data)-1], [current_pnl], color=line_color, s=50, zorder=5)
            ax.annotate(f"${current_pnl:.2f}", 
                        xy=(len(data)-1, current_pnl), 
                        xytext=(10, 10), 
                        textcoords='offset points',
                        color=line_color,
                        fontweight='bold',
                        bbox=dict(boxstyle="round,pad=0.3", fc=face_color, ec=line_color, alpha=0.8))

            # Title
            ax.set_title(f"Profit Trend ({len(data)} rounds)", color=text_color, fontsize=14, fontweight='bold', pad=15)
            
            # Render to Buffer
            buf = io.BytesIO()
            fig.tight_layout()
            fig.savefig(buf, format='png', facecolor=face_color, edgecolor='none')
            buf.seek(0)
            return buf
            
        except Exception as e:
            print(f"Graph Gen Error: {e}")
            # FALBACK IMAGE (To prevent Telegram Type Errors)
            try:
                msg_fig = Figure(figsize=(8, 4), dpi=100, facecolor='#222222')
                msg_canvas = FigureCanvasAgg(msg_fig)
                msg_ax = msg_fig.add_subplot(111)
                msg_ax.set_facecolor('#222222')
                msg_ax.text(0.5, 0.5, "Graph Unavailable\n(Waiting for Data)", 
                           color='white', ha='center', va='center', fontsize=12)
                msg_ax.axis('off')
                
                err_buf = io.BytesIO()
                msg_fig.savefig(err_buf, format='png')
                err_buf.seek(0)
                return err_buf
            except Exception:
                return None

    def stop_bot(self):
        with self._state_lock:
            self.bot_running = False
        self.bot_paused = False # Reset pause
        self.update_hud_safe(is_paused=False)
        if getattr(self, 'overlay', None):
            self.overlay.stop_global_timer()
        print("⛔ Bot stop requested.")
        
        # Reset Dashboard button states
        if hasattr(self, 'dash_start_btn'):
            self.dash_start_btn.configure(state="normal", fg_color="#27ae60")
        if hasattr(self, 'dash_stop_btn'):
            self.dash_stop_btn.configure(state="disabled", fg_color="gray")
        if hasattr(self, 'dash_pause_btn'):
            self.dash_pause_btn.configure(state="disabled", text="⏸  PAUSE", fg_color="#f39c12")
        if hasattr(self, 'dash_status_dot'):
            self.dash_status_dot.configure(text_color="#7f8c8d")
            self.dash_status_text.configure(text="  Stopped", text_color="#7f8c8d")
        self.log_to_dashboard("Session stopped")
        # Show summary if at least one session ran
        if getattr(self, 'current_session_num', 0) > 0:
            self.root.after(300, self._show_session_summary)

    def _show_session_summary(self):
        """Post-session summary modal shown when all sessions complete."""
        import time as _time

        # Gather stats
        elapsed_sec = 0
        if hasattr(self, 'bot_global_start_time'):
            elapsed_sec = int(_time.time() - self.bot_global_start_time)

        h = elapsed_sec // 3600
        m = (elapsed_sec % 3600) // 60
        s = elapsed_sec % 60
        elapsed_str = f"{h:02}:{m:02}:{s:02}"

        current_bal = self.config.get("current_balance", 0.0)
        start_bal   = getattr(self, 'initial_run_balance', current_bal)
        net_pnl     = current_bal - start_bal
        pnl_color   = "#10b981" if net_pnl >= 0 else "#ef4444"
        pnl_str     = f"+${net_pnl:.2f}" if net_pnl >= 0 else f"-${abs(net_pnl):.2f}"

        wins   = getattr(self, 'session_wins',   0)
        losses = getattr(self, 'session_losses', 0)
        total  = wins + losses
        win_rate = f"{(wins / total * 100):.1f}%" if total > 0 else "N/A"
        best_streak = getattr(self, 'best_win_streak', 0)

        strategy = self.config.get("strategy", "—")
        num_sessions = getattr(self, 'current_session_num', 0)

        # Build dialog
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Session Complete")
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.attributes("-topmost", True)
        dialog.geometry("380x420")
        try:
            dialog.after(10, lambda: dialog.geometry(
                f"+{self.root.winfo_x() + self.root.winfo_width()//2 - 190}"
                f"+{self.root.winfo_y() + self.root.winfo_height()//2 - 210}"
            ))
        except Exception:
            pass

        frame = ctk.CTkFrame(dialog, fg_color="#0f172a", corner_radius=0)
        frame.pack(fill="both", expand=True)

        # Header
        ctk.CTkLabel(frame, text="✅  Session Complete",
                     font=ctk.CTkFont(size=16, weight="bold"), text_color="#f1c40f"
                     ).pack(pady=(20, 4))
        ctk.CTkLabel(frame, text=f"Strategy: {strategy}  |  Sessions: {num_sessions}",
                     font=ctk.CTkFont(size=11), text_color="#94a3b8"
                     ).pack(pady=(0, 12))

        # Gold divider
        ctk.CTkFrame(frame, fg_color="#f1c40f", height=1, corner_radius=0).pack(fill="x", padx=20, pady=(0, 14))

        # Stats grid
        stats_frame = ctk.CTkFrame(frame, fg_color="transparent")
        stats_frame.pack(fill="x", padx=24)
        stats_frame.columnconfigure((0, 1), weight=1)

        def stat_cell(parent, row, col, label, value, value_color="white"):
            cell = ctk.CTkFrame(parent, fg_color="#1e293b", corner_radius=8)
            cell.grid(row=row, column=col, padx=5, pady=5, sticky="ew")
            ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=10), text_color="#64748b").pack(pady=(8, 2))
            ctk.CTkLabel(cell, text=value, font=ctk.CTkFont(size=16, weight="bold"), text_color=value_color).pack(pady=(0, 8))

        stat_cell(stats_frame, 0, 0, "Net P&L",      pnl_str,      pnl_color)
        stat_cell(stats_frame, 0, 1, "Duration",      elapsed_str,  "#a78bfa")
        stat_cell(stats_frame, 1, 0, "Win Rate",      win_rate,     "#38bdf8")
        stat_cell(stats_frame, 1, 1, "Best Streak",   str(best_streak), "#f1c40f")
        stat_cell(stats_frame, 2, 0, "Wins",          str(wins),    "#10b981")
        stat_cell(stats_frame, 2, 1, "Losses",        str(losses),  "#ef4444")

        # Buttons
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(16, 20))
        ctk.CTkButton(
            btn_row, text="Close", height=36, fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=12), command=dialog.destroy
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            btn_row, text="▶  Run Again", height=36, fg_color="#10b981", hover_color="#059669",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: [dialog.destroy(), self.start_bot()]
        ).pack(side="right", fill="x", expand=True, padx=(6, 0))

    def start_auto_roulette(self):
        """Start the auto roulette bot with the selected strategy"""
        # --- LICENSE CHECK ---
        if not self.license_manager.is_licensed or self.license_tier == "FREE":
            self._show_upgrade_dialog("BASIC")
            return
        # ---------------------

        if not self.recorder.browser_win:
            messagebox.showerror("Error", "Please select a browser window first.")
            return
            
        if "balance" not in self.coordinates or "table_state" not in self.coordinates:
            messagebox.showerror("Error", "Please record 'balance' and 'table_state' regions first.")
            return
            
        # Get selected strategy and configuration
        strategy_name = self.auto_roulette_strategy_var.get()
        k_value = self.auto_roulette_k_var.get()
        
        # Update status
        self.auto_roulette_status_var.set(f"Starting {strategy_name} with k={k_value}...")
        self.start_auto_roulette_btn.configure(state="disabled")
        self.stop_auto_roulette_btn.configure(state="normal")
        
        # Start auto roulette in a separate thread
        self.auto_roulette_running = True
        if getattr(self, '_auto_thread', None) and self._auto_thread.is_alive():
            logger.warning("Automation thread already running")
            return
        self._auto_thread = threading.Thread(target=self.run_auto_roulette, daemon=True)
        self._auto_thread.start()

    def stop_auto_roulette(self):
        """Stop the auto roulette bot"""
        self.auto_roulette_running = False
        self.auto_roulette_status_var.set("Stopping...")
        self.start_auto_roulette_btn.configure(state="normal")
        self.stop_auto_roulette_btn.configure(state="disabled")
        
        # Dashboard: Reset Action Buttons
        if hasattr(self, 'dash_start_btn'):
            self.dash_start_btn.configure(state="normal", fg_color="#27ae60")
            self.dash_stop_btn.configure(state="disabled", fg_color="#c0392b")
            self.dashboard_bundle_dropdown.configure(state="normal")
            
        # Reset pause state
        self.auto_roulette_paused = False
        self.update_hud_safe(is_paused=False)

    # Removed toggle_pause_auto_roulette in favor of toggle_pause_bot

    def manual_refresh_overlay(self):
        """
        Manually trigger a data refresh for the overlay.
        User Feedback: 'refresh should reset the stats and begin fresh'
        So this is effectively a SESSION RESET button.
        """
        print(f"[Bot] RESETTING Session Stats via Overlay...")
        
        # 1. Reset Core Stats
        self.pnl_history = [0.0]
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_losses = 0
        self.current_session_num = 0
        self.initial_run_balance = self.config.get("current_balance", 0)
        self.stop_all_sessions = False # Reset global stop flag
        self.cumulative_net_profit = 0.0
        self.current_win_streak = 0
        self.session_high = 0.0
        
        # 2. Reset Time Tracking
        self.session_start_timestamp = time.time() # Reset start time
        self.total_paused_duration = 0
        if self.auto_roulette_paused or self.bot_paused:
             self.pause_start_time = time.time() # If paused, reset pause start to now
        else:
             self.pause_start_time = None

        # 3. Reset Graph Markers
        self.graph_markers = []
        
        # 4. Update HUD
        self.update_bankroll_graph()
        self.update_hud_safe(
            pnl="$0.00",
            streak="0",
            result="RESET",
            graph_data=self.pnl_history, 
            graph_markers=self.graph_markers,
            time_rem="--:--" # Will update next loop
        )
        print("[Bot] Session stats and graph reset.")

    # --- HUD Integration Helpers ---
    def on_overlay_close(self):
        """Handle overlay close from the overlay itself"""
        self.show_hud_var.set(False)
        self.toggle_hud()

    def toggle_hud(self):
        """Show/Hide Overlay"""
        if self.show_hud_var.get():
            first_create = not getattr(self, 'overlay', None)
            if first_create:
                from gui.components.overlay import BotOverlay
                self.overlay = BotOverlay(self.root, close_callback=self.on_overlay_close,
                                          pause_callback=self.toggle_pause_bot,
                                          refresh_callback=self.manual_refresh_overlay)
            self.overlay.deiconify()
            # Try to snap to browser
            if self.recorder.browser_win:
                self.overlay.attach_to_window(self.recorder.browser_win)
            # Push initial data so the HUD doesn't sit empty until the first
            # session event fires. update_hud_safe recomputes balance / PnL /
            # target / favorites from current state, so a single empty call
            # is enough to populate everything visible at idle.
            try:
                self.update_hud_safe(strategy_name=self.config.get("strategy"))
            except Exception:
                pass
        else:
            if getattr(self, 'overlay', None):
                self.overlay.withdraw()

    def update_hud_safe(self, **kwargs):
        """Update HUD from any thread"""
        if getattr(self, 'overlay', None) and self.show_hud_var.get():
            try:
                # Inject Global Timer if running
                if hasattr(self, 'bot_global_start_time'):
                     elapsed = time.time() - self.bot_global_start_time
                     h = int(elapsed // 3600)
                     m = int((elapsed % 3600) // 60)
                     s = int(elapsed % 60)
                     kwargs['global_time'] = f"{h:02}:{m:02}:{s:02}"
                
                # --- Calculate Global PnL & Target ---
                try:
                    # Coerce to float defensively — getattr(..., default) only kicks
                    # in when the attribute is *missing*, not when it's present-but-None
                    # (which can happen after a bundle load writes null into config).
                    current = float(self.config.get("current_balance") or 0)
                    start = float(getattr(self, 'initial_run_balance', None) or current)
                    global_pnl = current - start
                    kwargs['global_pnl'] = global_pnl
                    
                    # Formatting Target String (Actual Balance Target)
                    target_str = "--"
                    enabled = self.enable_global_stop_var.get() if hasattr(self, 'enable_global_stop_var') else False
                    if enabled:
                         candidates = []
                         # Global Profit (Absolute or %)
                         if hasattr(self, 'global_profit_stop_var'):
                             try:
                                 # This handles $100 or 10%
                                 target_val = self.parse_hybrid_value(self.global_profit_stop_var.get(), start)
                                 if target_val > 0: candidates.append(start + target_val)
                             except Exception: pass

                         if candidates:
                             # Use the closest target (min value above start)
                             final_target = min(candidates)
                             target_str = f"${final_target:.2f}"
                    
                    # --- 2. Global Stop Loss ---
                    # `enable_global_stop_var` is the master toggle for BOTH global
                    # profit AND global loss. Previously this block ignored the
                    # toggle and always displayed the stop-loss value, so users
                    # who unchecked Global Stops still saw a hot G.Stop on the HUD.
                    global_stop_loss_str = "--"
                    if enabled and hasattr(self, 'global_stop_loss_var'):
                        try:
                            loss_val = self.parse_hybrid_value(self.global_stop_loss_var.get(), start)
                            if loss_val > 0:
                                global_stop_loss_str = f"-${loss_val:.2f}"
                        except Exception: pass
                    kwargs['global_stop'] = global_stop_loss_str

                    # --- 3. Session Target & Stop (Using Session Start Balance) ---
                    sess_start = float(getattr(self, 'session_start_balance', None) or current)
                    
                    # Session Profit Target
                    sess_target_str = "--"
                    # Check Master Switch first
                    session_stops_enabled = False
                    if hasattr(self, 'enable_session_stops_var'):
                        session_stops_enabled = self.enable_session_stops_var.get()
                    elif self.config.get("enable_profit_target", False): # Fallback
                         session_stops_enabled = True

                    if session_stops_enabled:
                         if hasattr(self, 'profit_target_var'):
                             try:
                                 # Profit target is relative to session start
                                 val = self.parse_hybrid_value(self.profit_target_var.get(), sess_start)
                                 if val > 0: sess_target_str = f"${val:.2f}"
                             except Exception: pass
                    kwargs['session_target'] = sess_target_str

                    # Session Stop Loss
                    sess_stop_str = "--"
                    if session_stops_enabled: # Only show if enabled
                        if hasattr(self, 'max_loss_var'):
                            try:
                                val = self.parse_hybrid_value(self.max_loss_var.get(), sess_start)
                                if val > 0: sess_stop_str = f"-${val:.2f}"
                            except Exception: pass
                    kwargs['session_stop'] = sess_stop_str
                    
                    # --- 4. Trailing Stop ---
                    trail_str = "--"
                    # Use live variable
                    ts_enabled = False
                    if hasattr(self, 'enable_trailing_stop_var'):
                        ts_enabled = self.enable_trailing_stop_var.get()
                    elif self.config.get("enable_trailing_stop", False):
                        ts_enabled = True

                    if ts_enabled:
                        if hasattr(self, 'trailing_stop_amount_var'):
                                try:
                                    val = self.parse_hybrid_value(self.trailing_stop_amount_var.get(), sess_start)
                                    if val > 0:
                                        # Show: Current Drop / Max Drop
                                        # Drop = Peak - Current
                                        peak = float(getattr(self, 'peak_net_profit', None) or 0.0)
                                        curr = float(getattr(self, 'cumulative_net_profit', None) or 0.0)
                                        drop = peak - curr
                                        # Color warning? Handled by overlay logic effectively, just passing text here
                                        trail_str = f"Drop: ${drop:.2f} / ${val:.2f}"
                                except Exception: pass
                    kwargs['trailing_stop'] = trail_str

                    kwargs['global_target'] = target_str

                    # Inject Balance, Session Num, and Bundle Name
                    kwargs['balance'] = f"{current:.2f}"
                    kwargs['session_num'] = getattr(self, 'current_session_num', 1)
                    if hasattr(self, 'dashboard_bundle_var'):
                        kwargs['bundle_name'] = self.dashboard_bundle_var.get()

                    # --- 5. New HUD metrics: round, win rate, bet, progress bars ---
                    total_w = getattr(self, 'total_wins', 0)
                    total_l = getattr(self, 'total_losses', 0)
                    total_r = total_w + total_l
                    kwargs['round_num'] = total_r
                    if total_r > 0:
                        kwargs['win_rate'] = f"{(total_w / total_r) * 100:.0f}%"

                    # Current bet amount (stored by game loop as self.current_bet_amount)
                    cur_bet = getattr(self, 'current_bet_amount', None)
                    if cur_bet is None or cur_bet == 0:
                        # Fallback: try reading from config base_bet
                        cur_bet = float(self.config.get("base_bet", 0))
                    if cur_bet and cur_bet > 0:
                        kwargs['current_bet'] = f"${cur_bet:.2f}"

                    # Session PnL and target/stop values for progress bars
                    sess_pnl = current - sess_start
                    kwargs['session_pnl'] = sess_pnl

                    # Parse raw target/stop values for progress bar ratios
                    if session_stops_enabled and hasattr(self, 'profit_target_var'):
                        try:
                            tgt_val = self.parse_hybrid_value(self.profit_target_var.get(), sess_start)
                            if tgt_val > 0:
                                kwargs['session_target_val'] = tgt_val
                        except Exception:
                            pass
                    if session_stops_enabled and hasattr(self, 'max_loss_var'):
                        try:
                            stp_val = self.parse_hybrid_value(self.max_loss_var.get(), sess_start)
                            if stp_val > 0:
                                kwargs['session_stop_val'] = stp_val
                        except Exception:
                            pass
                    
                except Exception as e:
                    print(f"Global Stats Error: {e}")
                # -------------------------------------
                
                def safe_update_overlay():
                    if getattr(self, 'overlay', None) and hasattr(self.overlay, 'update_info'):
                        try: self.overlay.update_info(**kwargs)
                        except Exception: pass
                    # Push favorites into the HUD's pill row so the user can
                    # one-click swap during gameplay. The overlay no-ops when
                    # the signature is unchanged, so this is cheap.
                    if getattr(self, 'overlay', None) and hasattr(self.overlay, 'set_favorites'):
                        try:
                            strat_favs = self._get_favorite_strategies() if hasattr(self, '_get_favorite_strategies') else []
                            bundle_favs = self._get_favorite_dashboard_bundles() if hasattr(self, '_get_favorite_dashboard_bundles') else []
                            # Strict XOR: only the running source highlights
                            # green. Null out the inactive side so the HUD never
                            # shows both a strategy and a bundle pill as active.
                            _src = getattr(self, 'active_strategy_source', None)
                            _active_strat = self.config.get("strategy") if _src != 'bundle' else None
                            _active_bundle = (self.dashboard_bundle_var.get()
                                              if (_src == 'bundle' and hasattr(self, 'dashboard_bundle_var'))
                                              else None)
                            self.overlay.set_favorites(
                                strategy_favs=strat_favs,
                                bundle_favs=bundle_favs,
                                active_strategy=_active_strat,
                                active_bundle=_active_bundle,
                                on_strategy_click=self._on_quick_toggle_click,
                                on_bundle_click=self._on_dashboard_bundle_pill_click,
                            )
                        except Exception:
                            pass
                self.root.after(0, safe_update_overlay)
            except Exception as e:
                print(f"HUD Update Error: {e}")
            
    def update_hud_position(self):
        """Periodically check browser position"""
        if getattr(self, 'overlay', None) and self.show_hud_var.get() and self.recorder.browser_win:
            try:
                # Naive sync: every few seconds, we just check if browser moved? 
                # Actually, attaching once is usually enough unless user moves.
                pass 
            except Exception: pass


    def run_auto_roulette(self):
        """Main auto roulette bot loop"""
        try:
            # Initialize strategy engine with auto roulette strategy
            strategy_name = self.auto_roulette_strategy_var.get()
            k_value = self.auto_roulette_k_var.get()
            
            # Get progression type from GUI
            progression_type = self.auto_roulette_progression_var.get()
            print(f"[AutoRoulette] Using progression type: {progression_type}")
            
            # Check if advanced strategy
            advanced_strategies = self.load_advanced_strategies()
            if strategy_name in advanced_strategies:
                print(f"[AutoRoulette] using AdvancedStrategyEngine for {strategy_name}")
                # Create Virtual Manager
                virtual_manager = VirtualStrategyManager()
                # Init Engine
                strategy_config = advanced_strategies[strategy_name]
                # AdvancedStrategyEngine adapts to StrategyEngine interface (duck typing or wrapper needed?)
                # StrategyEngine has methods: record_result, get_current_bet, get_next_bet, get_bet_labels...
                # AdvancedStrategyEngine has: get_next_bets() returning list of dicts.
                # We need an adapter or update AdvancedStrategyEngine to match expected interface if possible, 
                # OR handle it differently here.
                # The loop expects 'strategy' object.
                # Let's wrap it in a lightweight adapter class inline or use the engine directly if compatible.
                # AdvancedStrategyEngine.get_next_bets returns [{label, amount}].
                # Existing StrategyEngine.get_next_bet returns amount, get_bet_labels returns labels.
                
                # To avoid breaking the loop which expects 'strategy.get_next_bet()' returning a single amount 
                # and 'get_bet_labels' returning list, we should probably stick to the loop's expectations 
                # OR modify the loop.
                # Since Advanced can return DIFFERENT amounts for different labels, the existing loop structure 
                # (one bet amount for all labels) is insufficient.
                # However, for MVP of Advanced, let's assume one bet amount or handle the detailed bets logic.
                
                # We can create a Hybrid Wrapper.
                class AdvancedAdapter:
                    def __init__(self, engine):
                        self.engine = engine
                        self.strategy = self # for 'hasattr(strategy.strategy)' checks
                        self.last_numbers = [] # Dummy
                        self.next_bets = []
                        
                    def record_result(self, win, last_number=None):
                        # Update Virtual Strategies
                        if hasattr(self.engine, 'virtual_manager') and self.engine.virtual_manager and last_number is not None:
                            self.engine.virtual_manager.update_all(last_number)
                        # Advanced Engine state re-evals every round via DB, so no other state needed.
                        pass
                        
                    def get_next_bet(self):
                        # This is called to get amount.
                        # We should compute bets here.
                        self.next_bets = self.engine.get_next_bets()
                        if not self.next_bets: return 0
                        return self.next_bets[0]['amount'] # Return first amount as 'base'
                        
                    def get_bet_labels(self):
                        # Return labels from pre-computed bets
                        return [b['label'] for b in self.next_bets]
                    
                    def get_full_bets(self):
                        return self.next_bets

                strategy_engine_core = AdvancedStrategyEngine(strategy_config, base_bet=float(self.config["base_bet"]), virtual_manager=virtual_manager)
                strategy = AdvancedAdapter(strategy_engine_core)
                
            else:
                # Create strategy engine with the selected auto roulette strategy
                strategy = StrategyEngine(
                    strategy_name=strategy_name,
                    base_bet=float(self.config["base_bet"]),
                    max_loss=float(self.config["max_loss"]),
                    progression_type=progression_type,
                    max_bet=float(self.config.get("max_bet", 100)),
                    max_consec_losses=None,  # per-strategy only: absent ⇒ disabled. A |max_consec_losses=N suffix in strategy_name still overrides.
                    custom_strategies=self.custom_strategies,
                    observation_trigger=int(self.config.get("observation_trigger", 0))
                )
            
            # If using dynamic_9street, set the k value
            if strategy_name == "dynamic_9street" and hasattr(strategy, 'strategy') and hasattr(strategy.strategy, 'k'):
                strategy.strategy.k = k_value
            
            self.auto_roulette_status_var.set(f"Running {strategy_name}...")
            
            # Main auto roulette loop
            print(f"[AutoRoulette] Starting main loop...")
            waiting_for_new_pattern = False  # Flag to track when we're waiting for new pattern after win
            consecutive_ocr_failures = 0 # Safety counter for Lid Close / Screen Off detection
            
            while self.auto_roulette_running:
                # Check for PAUSE
                if self.auto_roulette_paused:
                    self.auto_roulette_status_var.set("PAUSED")
                    time.sleep(0.5)
                    continue

                print(f"[AutoRoulette] In main loop iteration...")
                
                # First, wait for a new winning number and update strategy
                print(f"[AutoRoulette] Waiting for new winning number...")
                waiting_for_number = True
                last_processed_number = None
                pattern_detected_after_result = False
                
                while waiting_for_number and self.auto_roulette_running:
                    if self.auto_roulette_paused: break # Break inner loop to hit main loop pause check
                    print(f"[AutoRoulette] In number waiting loop...")
                    
                    # Get winning number directly from table state region
                    table_state_region = self.coordinates.get("table_state")
                    if table_state_region:
                        winning_number, winning_color = extract_winning_number_from_table_state(
                            self.recorder.browser_win, 
                            table_state_region
                        )
                        print(f"🔍 DEBUG: Direct OCR result: {winning_number} {winning_color or ''}")
                        
                        if winning_number is not None:
                            # Always update the strategy with the new number
                            if hasattr(strategy.strategy, 'record_result'):
                                strategy.strategy.record_result(False, last_number=winning_number)
                                logger.debug(f"[AutoRoulette] Called strategy.record_result(False, last_number={winning_number})")
                            else:
                                strategy.record_result(False, winning_number=winning_number)
                                logger.debug(f"[AutoRoulette] Called strategy.record_result(False, winning_number={winning_number})")

                            logger.info(f"[AutoRoulette] Updated strategy with number: {winning_number}")
                            logger.debug(f"[AutoRoulette] Strategy internal numbers: {strategy.strategy.last_numbers}")

                            # Always check for a new pattern after every number (unless waiting for new pattern after 2 wins)
                            if not waiting_for_new_pattern:
                                bet_labels = strategy.get_bet_labels()
                                if bet_labels:
                                    logger.info(f"[AutoRoulette] Pattern detected! Bet labels: {bet_labels}")
                                    # Break out of number waiting loop to place bets
                                    waiting_for_number = False
                                    break

                            # If waiting for new pattern, check if enough numbers have been collected to clear the flag
                            if waiting_for_new_pattern and len(strategy.strategy.last_numbers) >= k_value:
                                waiting_for_new_pattern = False
                                logger.info(f"[AutoRoulette] New pattern search completed! Ready to place bets.")

                            last_processed_number = winning_number
                        else:
                            logger.debug(f"[AutoRoulette] No new winning number yet, waiting...")
                    else:
                        logger.warning(f"[AutoRoulette] No table_state region configured")
                        break
                    
                    time.sleep(0.5)
                
                # Check if we should break out due to pattern detected after result
                if pattern_detected_after_result:
                    print(f"[AutoRoulette] Pattern was detected after result, proceeding to place bets...")
                    waiting_for_number = False
                
                # Global Stop Check for Auto Roulette
                if self.should_end_session():
                    self.log_simulation("🛑 Auto Roulette stopped by Global Stop Condition")
                    self.auto_roulette_status_var.set("Stopped by Global Limit")
                    break

                if not self.auto_roulette_running:
                    break
                
                # Now check if we should place bets
                # Get current balance
                # balance_region = self.coordinates.get("balance")
                # if balance_region:
                #     current_balance = extract_balance(self.recorder.browser_win, balance_region, {})
                #     if current_balance is None:
                #         print("[AutoRoulette] BREAK: Failed to read balance")
                #         self.auto_roulette_status_var.set("Failed to read balance")
                #         time.sleep(2)
                #         continue
                # else:
                #     print("[AutoRoulette] BREAK: No balance region configured")
                #     self.auto_roulette_status_var.set("No balance region configured")
                #     break
                
                # Check if we're waiting for new pattern after a win
                if waiting_for_new_pattern:
                    print(f"[AutoRoulette] Waiting for new k={k_value} pattern after win...")
                    self.auto_roulette_status_var.set(f"Waiting for new k={k_value} pattern after win...")
                    time.sleep(1)
                    continue
                
                # Get bet amount and labels from strategy
                bet_amount = strategy.get_next_bet()
                print(f"[AutoRoulette] Strategy bet amount: {bet_amount}")
                
                # Debug progression state
                if hasattr(strategy.progression, 'consecutive_wins'):
                    print(f"[AutoRoulette] Progression state: consecutive_wins={strategy.progression.consecutive_wins}, consecutive_losses={strategy.progression.consecutive_losses}, current_bet={strategy.progression.current_bet}")
                elif hasattr(strategy.progression, 'consecutive_losses'):
                    print(f"[AutoRoulette] Progression state: consecutive_losses={strategy.progression.consecutive_losses}, current_bet={strategy.progression.current_bet}")
                
                bet_labels = strategy.get_bet_labels()
                print(f"[AutoRoulette] Strategy bet labels: {bet_labels}")
                
                if bet_amount <= 0:
                    print(f"[AutoRoulette] BREAK: Strategy stopped (bet amount <= 0), bet_amount={bet_amount}")
                    self.auto_roulette_status_var.set("Strategy stopped (bet amount <= 0)")
                    break
                    
                if not bet_labels:
                    print(f"[AutoRoulette] CONTINUE: No bets to place (waiting for pattern), bet_labels={bet_labels}")
                    # Debug: Show what numbers the strategy currently has
                    if hasattr(strategy.strategy, 'last_numbers'):
                        print(f"[AutoRoulette] Strategy has numbers: {strategy.strategy.last_numbers}")
                        if len(strategy.strategy.last_numbers) >= k_value:
                            print(f"[AutoRoulette] Strategy has {len(strategy.strategy.last_numbers)} numbers but no pattern detected")
                            # Additional debug: Check street indices manually
                            if hasattr(strategy.strategy, 'number_to_street_index'):
                                street_indices = [strategy.strategy.number_to_street_index(n) for n in strategy.strategy.last_numbers]
                                print(f"[AutoRoulette] Manual street indices check: {street_indices}")
                                if len(set(street_indices)) == 1:
                                    print(f"[AutoRoulette] WARNING: Street indices ARE the same ({street_indices[0]}), but strategy didn't trigger!")
                                else:
                                    print(f"[AutoRoulette] Street indices are different: {street_indices}")
                        else:
                            print(f"[AutoRoulette] Strategy needs {k_value - len(strategy.strategy.last_numbers)} more numbers")
                    
                    # If we were waiting for new pattern and now have enough numbers, clear the flag
                    if waiting_for_new_pattern and len(strategy.strategy.last_numbers) >= k_value:
                        waiting_for_new_pattern = False
                        print(f"[AutoRoulette] New pattern search completed! Ready to place bets.")
                    
                    self.auto_roulette_status_var.set("No bets to place (waiting for pattern)")
                    time.sleep(1)
                    continue
                
                # # Check if we have enough balance
                # if bet_amount > current_balance:
                #     print(f"[AutoRoulette] BREAK: Insufficient balance, bet_amount={bet_amount}, current_balance={current_balance}")
                #     self.auto_roulette_status_var.set("Insufficient balance")
                #     break
                
                # Check if table is accepting bets (follow same logic as normal bot)
                table_state_region = self.coordinates.get("table_state")
                if table_state_region:
                    print(f"[AutoRoulette] Using table_state region: {table_state_region}")
                    
                    # Retry loop to get proper table state (not winning number)
                    max_retries = 10
                    retry_count = 0
                    proper_table_state_found = False
                    
                    while retry_count < max_retries and not proper_table_state_found and self.auto_roulette_running:
                        table_state = extract_table_state(self.recorder.browser_win, table_state_region, {})
                        table_state_upper = table_state.upper() if table_state else ""
                        print(f"[AutoRoulette] Table state OCR result (attempt {retry_count + 1}): '{table_state}'")
                        
                        # Check if this looks like a winning number instead of table state
                        def is_winning_number(text):
                            # Use robust shared logic to detect even malformed numbers (e.g. "g RED")
                            cleaned = clean_ocr_text(text)
                            num, col = extract_number_and_color(cleaned)
                            if num is not None:
                                print(f"[AutoRoulette] is_winning_number: Parsed '{text}' as {num} {col}")
                                return True
                            return False
                        
                        # Check if this contains betting-related keywords (proper table state)
                        def is_betting_related_state(text):
                            betting_keywords = ["PLACE", "BET", "ACCEPTED", "NEXT", "GAME", "SOON"]
                            return any(keyword in text.upper() for keyword in betting_keywords)
                        
                        if table_state and is_winning_number(table_state):
                            print(f"[AutoRoulette] Detected winning number: '{table_state}', retrying for table state...")
                            self.auto_roulette_status_var.set("Waiting for table to open (winning number detected)")
                            time.sleep(1)
                            retry_count += 1
                            continue
                        
                        # Only accept as proper table state if it contains betting-related keywords
                        if table_state and is_betting_related_state(table_state):
                            proper_table_state_found = True
                            print(f"[AutoRoulette] Proper table state found: '{table_state}'")
                        else:
                            print(f"[AutoRoulette] Not a betting-related state: '{table_state}', retrying...")
                            self.auto_roulette_status_var.set("Waiting for proper table state...")
                            time.sleep(1)
                            retry_count += 1
                            continue
                    
                    if not proper_table_state_found:
                        print(f"[AutoRoulette] Failed to get proper table state after {max_retries} attempts")
                        self.auto_roulette_status_var.set("Failed to detect table state")
                        
                        # --- Safety Check for Lid Close / Screen Off ---
                        consecutive_ocr_failures += 1
                        print(f"[Safety] Consecutive OCR failures: {consecutive_ocr_failures}")
                        
                        if consecutive_ocr_failures >= 3: # Approx 45-60 seconds of failure
                             print("⛔ SAFETY TRIGGRED: Persistent OCR failure. Pausing Bot.")
                             self.log_simulation("⛔ Safety Pause: Screen appears to be off or obscured.")
                             
                             # Force Pause
                             self.toggle_pause_bot()
                             
                             # Show Alert (Non-blocking)
                             def show_safety_alert():
                                 messagebox.showwarning("Safety Pause", 
                                     "The bot has been paused because it cannot see the table.\n\n"
                                     "POSSIBLE CAUSES:\n"
                                     "1. Laptop lid is closed (Display is Off).\n"
                                     "2. Browser is minimized or obscured.\n"
                                     "3. Screen saver is active.\n\n"
                                     "If running with lid closed, you MUST use an HDMI Dummy Plug."
                                 )
                             self.root.after(0, show_safety_alert)
                             
                        time.sleep(2)
                        continue
                    else:
                        # Reset counter on success
                        consecutive_ocr_failures = 0
                    
                    # Now process the proper table state
                    # Follow same logic as normal bot flow (more flexible matching)
                    # Check for betting state with flexible matching
                    def is_betting_state(text):
                        # Accept "PLACE YOUR BETS", "PLACE", "BET", etc.
                        betting_keywords = ["PLACE", "BET"]
                        result = any(keyword in text for keyword in betting_keywords)
                        print(f"[AutoRoulette] is_betting_state check: text='{text}', keywords={betting_keywords}, result={result}")
                        return result
                    
                    def is_bets_accepted_state(text):
                        # Accept "BETS ACCEPTED", "ACCEPTED", etc.
                        accepted_keywords = ["ACCEPTED", "BETS ACCEPTED"]
                        return any(keyword in text for keyword in accepted_keywords)
                    
                    def is_next_game_state(text):
                        # Accept "NEXT GAME SOON", "NEXT", "SOON", etc.
                        next_game_keywords = ["NEXT", "SOON", "NEXT GAME"]
                        return any(keyword in text for keyword in next_game_keywords)
                    
                    print(f"[AutoRoulette] Checking table state: '{table_state}' -> '{table_state_upper}'")
                    
                    if is_betting_state(table_state_upper):
                        print(f"[AutoRoulette] Betting state detected: '{table_state}', proceeding to place bets...")
                    elif is_bets_accepted_state(table_state_upper):
                        print(f"[AutoRoulette] Bets accepted state detected: '{table_state}', waiting...")
                        self.auto_roulette_status_var.set("Bets accepted, waiting...")
                        time.sleep(1)
                        continue
                    elif is_next_game_state(table_state_upper):
                        print(f"[AutoRoulette] Next game state detected: '{table_state}', waiting...")
                        self.auto_roulette_status_var.set("Next game soon, waiting...")
                        time.sleep(1)
                        continue
                    else:
                        print(f"[AutoRoulette] Table not in betting state (state: '{table_state}'), waiting...")
                        self.auto_roulette_status_var.set("Waiting for betting to open")
                        time.sleep(1)
                        continue
                else:
                    print(f"[AutoRoulette] No table_state region configured")
                    break
                
                print(f"[AutoRoulette] About to place bets: amount={bet_amount}, labels={bet_labels}")
                
                # Place bets using the automation driver
                driver = RouletteBrowserAutomation(
                    coordinates=self.config["coordinates"],
                    window_title=self.recorder.browser_win.title
                )
                
                # Place bets on all labels
                chip_breakdown = get_chip_breakdown(bet_amount)
                print(f"[AutoRoulette] Chip breakdown: {chip_breakdown}")
                for chip_label, count in chip_breakdown:
                    print(f"[AutoRoulette] Selecting chip: {chip_label} x{count}")
                    driver.select_chip(chip_label)
                    time.sleep(0.05)  # Reduced from 0.1
                    
                    for _ in range(count):
                        for label in bet_labels:
                            print(f"[AutoRoulette] Placing bet on: {label}")
                            driver.place_bet(label)
                            time.sleep(0.01)  # Reduced from 0.03
                
                print(f"[AutoRoulette] Bet placement completed!")
                self.auto_roulette_status_var.set(f"Placed ${bet_amount:.2f} on {len(bet_labels)} bets")
                
                # Wait for result
                waiting_for_result = True
                print(f"[AutoRoulette] Starting to wait for result...")
                last_result_number = None
                while waiting_for_result and self.auto_roulette_running:
                    if self.auto_roulette_paused: break # Allow breaking (will finish round logic after result?) 
                    # Actually if we break here we lose the result. 
                    # Better to NOT break here so we finish the round, THEN pause at top of main loop.
                    # check for pause only to update status?
                    if self.auto_roulette_paused:
                        self.auto_roulette_status_var.set("PAUSED (Finishing Round)")
                    
                    print(f"[AutoRoulette] In result waiting loop...")
                    
                    # Get winning number directly from table state region
                    table_state_region = self.coordinates.get("table_state")
                    if table_state_region:
                        winning_number, winning_color = extract_winning_number_from_table_state(
                            self.recorder.browser_win, 
                            table_state_region
                        )
                        print(f"🔍 DEBUG: Result OCR result: {winning_number} {winning_color or ''}")
                        
                        if winning_number is not None and winning_number != last_result_number:
                            # Determine if this is a win
                            covered_numbers = strategy.get_covered_numbers()
                            is_win = winning_number in covered_numbers
                            
                            # Record result with winning number for auto roulette strategies
                            # First update the strategy with the winning number
                            if hasattr(strategy.strategy, 'record_result'):
                                strategy.strategy.record_result(is_win, last_number=winning_number)
                            
                            # Then record the result in the progression (this updates bet amounts)
                            strategy.record_result(is_win, current_balance=current_balance, winning_number=winning_number)

                            # IMMEDIATE STOP CHECK (Moved here to capture last result)
                            if self.should_end_session():
                                # Check if it was a Global Stop (flag set inside should_end_session)
                                if getattr(self, 'stop_all_sessions', False):
                                    # This block requires `projected_balance` and `cumulative_net_profit`
                                    # which are not available in this scope.
                                    # Assuming this is a placeholder for a more complete check.
                                    msg = f"🏆 **GLOBAL TARGET MET**\nStopped at Balance: ${self.current_balance}\nGlobal PnL: ${self.cumulative_net_profit}"
                                    print(f"🛑 GLOBAL STOP TRIGGERED: {msg}")
                                    self.auto_roulette_running = False 
                                    
                                    if self.telegram_bot:
                                        self.telegram_bot.send_notification(msg)

                                    # NOTIFY OVERLAY
                                    self.update_hud_safe(
                                        header="🏆 GLOBAL TARGET MET",
                                        result="STOPPED",
                                        global_pnl=self.cumulative_net_profit
                                    )
                                    break
                                else:
                                    # Session Stop (Profit/Wins/etc) -> End Session, Continue Bot if Multi-Session
                                    print(f"🛑 Session Stop Triggered immediately after balance update.")
                                    if self.telegram_bot:
                                        self.telegram_bot.send_notification(f"⚠️ Session Stop Triggered. Moving to next session (if available).")
                                    break
                            
                            # Debug progression state after recording result
                            if hasattr(strategy.progression, 'consecutive_wins'):
                                logger.debug(f"[AutoRoulette] After recording {'WIN' if is_win else 'LOSS'}: consecutive_wins={strategy.progression.consecutive_wins}, consecutive_losses={strategy.progression.consecutive_losses}, next_bet={strategy.progression.current_bet}")
                            elif hasattr(strategy.progression, 'consecutive_losses'):
                                logger.debug(f"[AutoRoulette] After recording {'WIN' if is_win else 'LOSS'}: consecutive_losses={strategy.progression.consecutive_losses}, next_bet={strategy.progression.current_bet}")
                            
                            # If this was a win, check if we need to wait for new pattern
                            if is_win:
                                # Check if this is the second consecutive win
                                if hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins >= 2:
                                    waiting_for_new_pattern = True
                                    logger.info(f"[AutoRoulette] 2 consecutive wins detected! Waiting for new k={k_value} pattern...")
                                elif hasattr(strategy.progression, 'consecutive_wins') and strategy.progression.consecutive_wins == 1:
                                    logger.info(f"[AutoRoulette] First win detected! Continuing to place bets...")
                                else:
                                    # This shouldn't happen, but just in case
                                    waiting_for_new_pattern = True
                                    logger.info(f"[AutoRoulette] WIN detected! Waiting for new k={k_value} pattern...")
                            
                            # Always check for new patterns immediately after recording result (unless waiting for new pattern)
                            if not waiting_for_new_pattern:
                                bet_labels = strategy.get_bet_labels()
                                if bet_labels:
                                    logger.info(f"[AutoRoulette] Pattern detected after result! Bet labels: {bet_labels}")
                                    # Set flag to break out of number waiting loop and place bets immediately
                                    pattern_detected_after_result = True
                                else:
                                    logger.info(f"[AutoRoulette] No pattern detected after result, waiting for more numbers...")
                            
                            self.auto_roulette_status_var.set(f"Result: {'WIN' if is_win else 'LOSS'} ({winning_number})")
                            last_result_number = winning_number
                            waiting_for_result = False
                        else:
                            logger.debug(f"[AutoRoulette] No new winning number yet, continuing to wait...")
                    else:
                        logger.warning(f"[AutoRoulette] No table_state region configured")
                        break
                    
                    time.sleep(0.2)  # Reduced from 0.5
                
                # Small delay between rounds
                time.sleep(0.5)  # Reduced from 1
                
        except Exception as e:
            self.auto_roulette_status_var.set(f"Error: {str(e)}")
            print(f"Auto roulette error: {e}")
        finally:
            self.auto_roulette_running = False
            self.auto_roulette_status_var.set("Stopped")
            self.start_auto_roulette_btn.configure(state="normal")
            self.stop_auto_roulette_btn.configure(state="disabled")

    def debug_add_number_to_strategy(self):
        """Debug method to manually add a number to the strategy"""
        number = self.debug_number_var.get()
        if 1 <= number <= 36:
            # Create a temporary strategy engine to test
            strategy = StrategyEngine(
                strategy_name="dynamic_9street",
                base_bet=1.0,
                max_loss=100.0,
                observation_trigger=self.config.get("observation_trigger", 0)
            )
            
            # Add the number to the strategy
            if hasattr(strategy.strategy, 'record_result'):
                strategy.strategy.record_result(True, last_number=number)
                print(f"[Debug] Added number {number} to strategy")
                print(f"[Debug] Strategy now has numbers: {strategy.strategy.last_numbers}")
                
                # Check what labels would be returned
                labels = strategy.get_bet_labels()
                print(f"[Debug] Strategy would bet on: {labels}")
                
                self.auto_roulette_status_var.set(f"Debug: Added {number}, would bet on {len(labels)} labels")
            else:
                print(f"[Debug] Strategy doesn't have record_result method")
        else:
            print(f"[Debug] Invalid number: {number}")

    def calculate_chip_clicks(self, amount):
        denominations = [100, 25, 5, 1, 0.5, 0.1]
        for chip in denominations:
            if amount % chip == 0:
                return (chip, int(amount / chip))
        return (0.1, int(round(amount / 0.1)))

    def parse_hybrid_value(self, value_str, base_amount=0.0):
        """
        Parses a string that can be an absolute number (e.g. "100") or a percentage (e.g. "10%").
        Returns the absolute float value.
        """
        try:
            val_s = str(value_str).strip()
            if not val_s:
                return 0.0
            
            if val_s.endswith("%"):
                pct = float(val_s.rstrip("%"))
                return base_amount * (pct / 100.0)
            else:
                return float(val_s)
        except Exception as e:
            # print(f"Error parsing hybrid value '{value_str}': {e}") # Reduce noise
            return 0.0

    # Bot Control betting/session config keys that a bundle overwrites and that
    # must be reverted when the user switches back to a single strategy. Each is
    # (config_key, gui_var_name_or_None). The strategy *name* and rotation state
    # are excluded (driven by the active selection / XOR), but progression IS
    # included: the rotation engine overwrites progression_var per entry while a
    # bundle runs (e.g. leaves it on "dynamic"), and the user doesn't choose
    # progression when clicking a strategy pill — so it must revert to the
    # pre-bundle manual value.
    _MANUAL_SNAPSHOT_FIELDS = [
        ("progression_type", "progression_var"),
        ("base_bet", "base_bet_var"),
        ("max_loss", "max_loss_var"),
        ("max_bet", "max_bet_var"),
        ("num_sessions", "num_sessions_var"),
        ("min_gap_minutes", "min_gap_var"),
        ("max_gap_minutes", "max_gap_var"),
        ("profit_target", "profit_target_var"),
        ("enable_trailing_stop", "enable_trailing_stop_var"),
        ("trailing_stop_amount", "trailing_stop_amount_var"),
        ("session_ext_after_win", "session_ext_after_win_var"),
        ("session_ext_at_high", "session_ext_at_high_var"),
        ("max_extension_rounds", "max_ext_rounds_var"),
        ("extension_give_up_amount", "ext_give_up_var"),
        ("enable_global_stop", "enable_global_stop_var"),
        ("global_profit_stop", "global_profit_stop_var"),
        ("global_stop_loss", "global_stop_loss_var"),
        ("observation_trigger", "observation_trigger_var"),
        ("max_consec_losses", None),
        ("enable_escalation_on_loss", "enable_escalation_on_loss_var"),
        ("escalation_multiplier", "escalation_multiplier_var"),
        ("escalation_max_steps", "escalation_max_steps_var"),
        ("escalation_per_step", "escalation_per_step_var"),
        ("session_duration_minutes", "session_duration_var"),
    ]

    def _snapshot_manual_config(self):
        """Capture the current Bot Control betting/session config so it can be
        restored later. Called at the top of a bundle load, BEFORE the bundle
        overwrites config. No-op when a bundle is already active, so loading
        bundle B over bundle A doesn't capture A's values as 'manual'."""
        if self.active_strategy_source == 'bundle':
            return
        import copy
        snap_config = {}
        snap_vars = {}
        for key, var_name in self._MANUAL_SNAPSHOT_FIELDS:
            if key in self.config:
                try:
                    snap_config[key] = copy.deepcopy(self.config[key])
                except Exception:
                    snap_config[key] = self.config[key]
            if var_name and getattr(self, var_name, None) is not None:
                try:
                    snap_vars[var_name] = getattr(self, var_name).get()
                except Exception:
                    pass
        # dynamic_rules lives on both the config and a direct attribute.
        try:
            snap_config['dynamic_rules'] = copy.deepcopy(self.config.get('dynamic_rules'))
            snap_vars['__dynamic_rules_attr__'] = copy.deepcopy(getattr(self, 'dynamic_rules', None))
        except Exception:
            pass
        self._manual_config_snapshot = {'config': snap_config, 'vars': snap_vars}
        logger.info(f"[ManualConfig] snapshotted {len(snap_config)} keys before bundle load")

    def _restore_manual_config(self):
        """Restore the betting/session config captured by _snapshot_manual_config
        (the user's pre-bundle Bot Control values). No-op when nothing was
        captured. Clears the snapshot afterwards so the next bundle load
        captures a fresh manual baseline."""
        snap = getattr(self, '_manual_config_snapshot', None)
        if not snap:
            return
        for key, val in (snap.get('config') or {}).items():
            self.config[key] = val
        for var_name, val in (snap.get('vars') or {}).items():
            if var_name == '__dynamic_rules_attr__':
                self.dynamic_rules = val
                continue
            var = getattr(self, var_name, None)
            if var is not None:
                try:
                    var.set(val)
                except Exception:
                    pass
        try:
            save_config(self.config)
        except Exception:
            pass
        self._manual_config_snapshot = None
        logger.info("[ManualConfig] restored pre-bundle Bot Control config")

    def _select_strategy_source(self, source):
        """Enforce strict XOR between a bundle and a single manual strategy.
        Selecting one fully clears the other's state, so a run is never driven
        by a mix of the two (the root cause of foreign strategies leaking into
        a bundle run). When a session is live, queue an immediate re-arm so the
        new selection takes effect at the next round boundary."""
        source = (source or '').lower()
        if source not in ('bundle', 'manual'):
            return
        self.active_strategy_source = source

        if source == 'bundle':
            # Bundle drives the run via rotation/triggers. The bundle-load
            # handler has already populated rotation_strategies_var /
            # triggers_config before calling us.
            if hasattr(self, 'enable_strategy_rotation_var'):
                self.enable_strategy_rotation_var.set(True)
            self.config['enable_strategy_rotation'] = True
        else:  # 'manual' — a single strategy drives the run
            # Revert the Bot Control betting/session config to whatever it was
            # before a bundle overwrote it — the bundle's values shouldn't
            # linger in manual mode. (Strategy/progression are NOT restored: the
            # user is actively choosing those right now.)
            self._restore_manual_config()
            # Wipe all bundle/rotation state so a previously-loaded bundle's
            # members can't leak into a single-strategy run.
            if hasattr(self, 'enable_strategy_rotation_var'):
                self.enable_strategy_rotation_var.set(False)
            self.config['enable_strategy_rotation'] = False
            if hasattr(self, 'rotation_strategies_var'):
                self.rotation_strategies_var.set("")
            self.config['rotation_strategies'] = ""
            self.rotation_strategies = []
            self._trigger_engine = None
            self._trigger_engines_by_base = {}
            self.triggers_config = {"selection_mode": "rotation", "triggers": {},
                                    "global_trigger": None, "tiebreaker": "coldest",
                                    "fallback": "stay"}
            # Clear the dashboard bundle display. Programmatic .set() does NOT
            # re-trigger on_dashboard_bundle_select (CTk command fires on user
            # interaction only), and that handler ignores the placeholder anyway.
            if hasattr(self, 'dashboard_bundle_var'):
                try:
                    self.dashboard_bundle_var.set("Select Bundle...")
                except Exception:
                    pass

        self._update_selection_lock_ui()

        # If a session is live, apply the switch immediately (at the next safe
        # round boundary) instead of silently desyncing the display from the
        # running engine. The change can't take effect mid-bet, so tell the user
        # it's queued — across status bar, dashboard, HUD banner, and Telegram.
        if getattr(self, 'bot_running', False) or getattr(self, 'auto_roulette_running', False):
            self.pending_engine_rearm = True
            if source == 'bundle':
                target = self.dashboard_bundle_var.get() if hasattr(self, 'dashboard_bundle_var') else 'bundle'
                label = f"bundle '{target}'"
            else:
                target = self.strategy_var.get() if hasattr(self, 'strategy_var') else 'strategy'
                label = f"strategy '{target}'"
            self._broadcast_alert(
                header="🔄 SWITCH QUEUED",
                hud_result=f"{label} → next round",
                message=(f"🔄 Switch to {label} queued — a round is in progress; "
                         f"it applies at the end of the current round."),
            )

    def _broadcast_alert(self, header=None, message="", hud_result=None, strategy_name=None):
        """Push a user-facing alert across every available channel: status bar,
        dashboard activity log, HUD overlay banner, and Telegram (when running).
        Each channel is best-effort — a missing widget or stopped bot never
        breaks the others."""
        if message and hasattr(self, 'set_status'):
            try: self.set_status(message)
            except Exception: pass
        if message and hasattr(self, 'log_to_dashboard'):
            try: self.log_to_dashboard(message)
            except Exception: pass
        try:
            hud_kwargs = {}
            if header is not None: hud_kwargs['header'] = header
            if hud_result is not None: hud_kwargs['result'] = hud_result
            if strategy_name is not None: hud_kwargs['strategy_name'] = strategy_name
            if hud_kwargs:
                self.update_hud_safe(**hud_kwargs)
        except Exception:
            pass
        try:
            if message and getattr(self, 'telegram_bot', None) and self.telegram_bot.is_running:
                self.telegram_bot.send_notification(message)
        except Exception:
            pass

    def _update_selection_lock_ui(self):
        """Keep both selection controls clickable. The bundle/strategy XOR is
        enforced functionally — picking a strategy clears any loaded bundle and
        vice-versa — NOT by disabling a widget. Disabling the strategy dropdown
        would lock the user out of the very action (selecting a strategy) that
        switches them to manual mode, so this method only ever re-enables it
        (defensively clearing any stale disabled state)."""
        try:
            if getattr(self, 'strategy_dropdown', None) is not None:
                self.strategy_dropdown.configure(state="normal")
        except Exception:
            pass

    def _on_manual_strategy_selected(self, _choice=None):
        """User picked a single strategy from the Bot Control dropdown (fires on
        user interaction only, not on programmatic .set()). Treat as manual mode
        and clear any loaded bundle (strict XOR)."""
        try:
            self.update_strategy_preview()
        except Exception:
            pass
        self._select_strategy_source('manual')

    def _setup_rotation_and_triggers(self):
        """Build rotation list + conditional-trigger engine from the current
        config/GUI vars. Shared by run_multiple_sessions (session start) and the
        mid-session re-arm so both resolve the active strategy set identically."""
        if self.enable_strategy_rotation_var.get():
            self.initialize_strategy_rotation()
        else:
            # Defensive cleanup: rotation is OFF for this run, so flush any
            # stale rotation state from a previously loaded bundle. Without
            # this, a single-strategy run inherits self.rotation_strategies
            # and self._trigger_engine from the prior bundle session and
            # the per-entry resolution logic hijacks the user's chosen
            # strategy with the bundle's first rotation entry.
            self.rotation_strategies = []
            self._trigger_engine = None
            self._trigger_engines_by_base = {}
            # Even with classical rotation off, the bundle may still have
            # conditional triggers configured — those should fire too,
            # otherwise users have to enable rotation just to get triggers.
            # Build the trigger engine independently from the rotation list.
            tcfg = getattr(self, 'triggers_config', None) or {}
            if (tcfg.get('selection_mode') or 'rotation').lower() in ('conditional', 'parallel'):
                rot_str = (self.rotation_strategies_var.get().strip()
                           if hasattr(self, 'rotation_strategies_var') else "")
                entries = [s.strip() for s in rot_str.split(",") if s.strip()]
                if entries:
                    try:
                        self._init_trigger_engine(entries)
                        # Also stash the entries so the live loop's swap path
                        # can resolve target strategies.
                        self.rotation_strategies = entries
                    except Exception as e:
                        logger.warning(f"[Triggers] standalone init failed: {e}")
                else:
                    logger.info("[Triggers] conditional/parallel mode set but rotation list is empty — engine not built")

    def _build_live_strategy(self):
        """Construct the session's StrategyEngine from current config + rotation
        state. Shared by run_bot's session init and the mid-session re-arm
        (_apply_pending_engine_rearm) so both paths resolve the active strategy
        identically. Relies on self.active_session_loss_limit already being set
        by the caller (run_bot parses guardrails before calling this)."""
        # Get progression type and params from GUI
        progression_type = self.progression_var.get()
        progression_params = self.get_progression_params()

        # --- Per-entry progression resolution ---
        # Per-entry resolution only kicks in when ALL THREE are true:
        #   1. Bundle/rotation explicitly enabled (enable_strategy_rotation_var)
        #   2. A non-empty rotation list exists
        #   3. EITHER rotation_progression_override is on, OR conditional
        #      triggers are active
        # AND the configured strategy's base name matches a rotation entry.
        # Without (1), a user who switched from a bundle to a single strategy
        # would still get the bundle's first rotation entry forced on them
        # (because rotation_strategies persists in memory). Without the
        # match check, picking an unrelated strategy would silently get
        # rewritten to rot_strats[0].
        initial_strategy_name = self.config["strategy"]
        rot_strats = getattr(self, 'rotation_strategies', None) or []
        rotation_actually_on = (
            hasattr(self, 'enable_strategy_rotation_var')
            and self.enable_strategy_rotation_var.get()
        )
        use_per_entry = (
            rot_strats
            and rotation_actually_on
            and (
                (hasattr(self, 'rotation_progression_override_var')
                 and self.rotation_progression_override_var.get())
                or getattr(self, '_trigger_engine', None) is not None
            )
        )
        if use_per_entry:
            _base_target = (initial_strategy_name or "").split(":", 1)[0].split("|", 1)[0].strip()
            _picked = None
            for _entry in rot_strats:
                if _entry.split(":", 1)[0].strip() == _base_target:
                    _picked = _entry
                    break
            # No silent fallback to rot_strats[0] — if the user's configured
            # strategy isn't in the rotation list, respect that choice and
            # use the original name. Otherwise we'd hijack a single-strategy
            # run with whatever was first in a previously-loaded bundle.
            if _picked is not None and _picked != initial_strategy_name:
                logger.info(f"[Init] Using full rotation entry '{_picked}' "
                            f"(rotation_progression_override / conditional triggers active)")
                initial_strategy_name = _picked
                self.config["strategy"] = _picked.split(":", 1)[0].strip()
            elif _picked is None:
                logger.info(f"[Init] Strategy '{_base_target}' not in rotation list — "
                            f"using user-selected strategy directly (no per-entry override).")

        # --- PATCH: Pass dynamic_rules at construction ---
        strategy = StrategyEngine(
            strategy_name=initial_strategy_name,
            base_bet=float(self.config["base_bet"]),
            max_loss=self.active_session_loss_limit, # Pass PARSED float value
            progression_type=progression_type,
            max_bet=float(self.config.get("max_bet", 100)),
            max_consec_losses=None,  # per-strategy only: absent ⇒ disabled. A |max_consec_losses=N suffix in strategy_name still overrides.
            custom_strategies=self.custom_strategies,
            dynamic_rules=progression_params.get('dynamic_rules', []),
            custom_sequence=progression_params.get('custom_sequence'),
            dalembert_step=progression_params.get('dalembert_step', 1),
            observation_trigger=int(self.config.get("observation_trigger", 0))
        )
        self._live_strategy = strategy  # Store reference for runtime risk profile updates
        # Surface what actually got loaded so the user can verify rules parsed correctly.
        try:
            logger.info(f"[Init] strategy={strategy.strategy_name}, "
                        f"progression={strategy.progression.__class__.__name__}, "
                        f"dynamic_rules={strategy.dynamic_rules}")
        except Exception:
            pass
        return strategy

    def _apply_pending_engine_rearm(self, strategy):
        """If the user switched bundle/strategy mid-session, rebuild the whole
        engine (rotation list + trigger engine + live StrategyEngine) at the
        round boundary so the change applies immediately. Returns the (possibly
        new) strategy. No-op + returns the original when nothing is queued."""
        if not getattr(self, 'pending_engine_rearm', False):
            return strategy
        self.pending_engine_rearm = False
        try:
            # Re-sync the run config from the (just-changed) GUI vars so the
            # rebuilt engine reflects the new selection.
            if hasattr(self, 'enable_strategy_rotation_var'):
                self.config['enable_strategy_rotation'] = self.enable_strategy_rotation_var.get()
            if hasattr(self, 'rotation_strategies_var'):
                self.config['rotation_strategies'] = self.rotation_strategies_var.get()
            if hasattr(self, 'rotation_mode_var'):
                self.config['rotation_mode'] = self.rotation_mode_var.get()

            # Rebuild rotation list + trigger engine from current config.
            self._setup_rotation_and_triggers()

            # Point config['strategy'] at the new active entry: the first
            # rotation entry for a bundle, or the user's single pick otherwise.
            if self.active_strategy_source == 'bundle' and getattr(self, 'rotation_strategies', None):
                self.config['strategy'] = self.rotation_strategies[0].split(':', 1)[0].strip()
            elif hasattr(self, 'strategy_var'):
                self.config['strategy'] = self.strategy_var.get()

            new_strategy = self._build_live_strategy()
            # Drop any queued pill/hotkey swap so it doesn't fire on top of the
            # freshly-armed engine.
            self.pending_strategy_swap = None

            src = self.active_strategy_source or 'selection'
            active = self.config.get('strategy')
            msg = f"✅ Switch applied — now running {src}: {active}"
            logger.info(msg)
            # Confirm across status bar, dashboard, HUD banner, and Telegram so
            # the user knows the queued switch is now live.
            self._broadcast_alert(
                header="✅ SWITCH APPLIED",
                hud_result=f"{src}: {active}",
                message=msg,
                strategy_name=active,
            )
            return new_strategy
        except Exception as e:
            logger.error(f"[Re-arm] failed to apply new selection: {e}")
            self._broadcast_alert(
                header="⚠️ SWITCH FAILED",
                message=f"⚠️  Re-arm failed: {e}",
            )
            return strategy

    def run_bot(self):
        try:
            # Ensure session number is at least 1 (for Standard/Immediate mode)
            if getattr(self, 'current_session_num', 0) == 0:
                self.current_session_num = 1
                
            self.reset_session_timestamp()
            # Initialize session stats
            self.reset_session_stats()
            session_rounds = 0
            session_wins = 0
            session_losses = 0
            with self._state_lock:
                self.cumulative_net_profit = 0.0
                self.peak_net_profit = 0.0
            self.session_manager = None # Force re-init for new session parameters
            # Disable PyAutoGUI failsafe to prevent corner trigger errors
            import pyautogui
            pyautogui.FAILSAFE = False
            
            # Parse Guardrails (using helper to handle % values)
            self.session_start_balance = self.config["current_balance"]
            
            # 1. Global Limits (Based on Initial Run Balance)
            # If this is the first session, set initial_run_balance
            if getattr(self, 'current_session_num', 1) <= 1:
                self.initial_run_balance = self.session_start_balance
                
            self.active_global_profit_limit = self.parse_hybrid_value(self.config.get("global_profit_stop"), self.initial_run_balance)
            self.active_global_loss_limit = self.parse_hybrid_value(self.config.get("global_stop_loss"), self.initial_run_balance)
            
            # 2. Session Limits (Based on Session Start Balance)
            self.active_session_profit_limit = self.parse_hybrid_value(self.config.get("profit_target"), self.session_start_balance)
            self.active_session_loss_limit = self.parse_hybrid_value(self.config.get("max_loss"), self.session_start_balance)
            self.active_trailing_stop_limit = self.parse_hybrid_value(self.config.get("trailing_stop_amount"), self.session_start_balance)
            
            print(f"[Guardrails] Global Profit: {self.active_global_profit_limit}, Global Loss: {self.active_global_loss_limit}")
            print(f"[Guardrails] Session Profit: {self.active_session_profit_limit}, Session Loss: {self.active_session_loss_limit}")
            print(f"[Guardrails] Trailing Stop: {self.active_trailing_stop_limit}")

            # --- INIT SESSION MANAGER ---
            # Create safe config for SessionManager using parsed limits and live toggles
            sm_config = self.config.copy()
            sess_stops_active = self.enable_session_stops_var.get()
            
            # Session Stops (Loss, Profit, Streaks) controlled by Master Toggle.
            # Keep gating consistent with the in-loop reinit path below — otherwise
            # stale config values (e.g. a previously-saved loss streak of 4) fire
            # even when the user has disabled session stops.
            sm_config["max_loss"] = self.active_session_loss_limit if sess_stops_active else 0
            sm_config["profit_target"] = self.active_session_profit_limit
            sm_config["enable_profit_target"] = sess_stops_active and self.active_session_profit_limit > 0
            sm_config["max_session_wins_streak"] = int(self.max_session_wins_streak_var.get() or 0) if sess_stops_active else 0
            sm_config["max_session_losses_streak"] = int(self.max_session_losses_streak_var.get() or 0) if sess_stops_active else 0

            # Trailing Stop controlled by its own toggle
            sm_config["trailing_stop_amount"] = self.active_trailing_stop_limit
            sm_config["enable_trailing_stop"] = self.enable_trailing_stop_var.get()

            self.session_manager = SessionManager(sm_config)

            # Build the live strategy engine for this session. Extracted into a
            # helper so the mid-session re-arm path (_apply_pending_engine_rearm)
            # rebuilds it identically when the user switches bundle/strategy.
            strategy = self._build_live_strategy()
            # The helper computes these internally; run_bot's later logic (e.g.
            # the dynamic-progression branch below) still needs progression_type
            # as a local, so re-read it here.
            progression_type = self.progression_var.get()

            # --- HUD UPDATE ---
            self.update_stats_display(next_session_timer=None) # Clear waiting timer
            self.update_hud_safe(strategy_name=self.config["strategy"])
            
            # For dynamic progression, set session_start_balance
            if progression_type == "dynamic":
                session_start_balance = 0.0
                balance_region = self.coordinates.get("balance")
                if balance_region:
                    session_start_balance = extract_balance(self.recorder.browser_win, balance_region, {}) or 0.0
                # If OCR failed (0.0), fall back to config balance so profit calc isn't wildly off
                if session_start_balance == 0.0:
                    session_start_balance = float(self.config.get("current_balance", 0.0))
                    logger.warning(f"[DynamicProgression] OCR balance failed, using config balance: {session_start_balance}")
                strategy.progression.session_start_balance = session_start_balance

            driver = RouletteBrowserAutomation(
                coordinates=self.config["coordinates"],
                window_title=self.recorder.browser_win.title
            )

            duration = self.config["session_duration_minutes"] * 60
            # We track end_time but now we need to account for pauses.
            # Instead of a fixed end_time, let's track start_time and enforce duration limit manually
            # or rely on an end_time that we PUSH back on resume.
            # Let's use the push-back approach as it minimizes loop logic changes.
            session_start_time_wall = time.time()
            end_time = session_start_time_wall + duration
            
            # Reset pause tracking for new session
            self.total_paused_duration = 0 
            self.pause_start_time = None
            
            # Track balance and game state with improved state machine
            initial_balance_this_round = None
            current_balance = None
            last_table_state = ""
            scroll_reset_needed = True  # Track if we need to reset scroll
            last_balance_read_time = 0
            balance_read_cooldown = 2  # Seconds between balance reads
            consecutive_failures = 0  # Track consecutive OCR failures
            max_consecutive_failures = 5  # Max failures before forcing scroll reset
            
            # Table state detection tracking
            table_state_failures = 0  # Track consecutive table state detection failures
            max_table_state_failures = self.config.get("max_table_state_failures", 3)  # Max failures before keyboard reset
            last_table_state_reset = 0  # Track when we last reset for table state
            table_state_reset_cooldown = self.config.get("table_state_reset_cooldown", 30)  # Seconds between table state resets

            # ── Region-drift alarm ──
            # If we go this long without ever reading a recognizable table state,
            # the table_state (and usually balance) region has almost certainly
            # drifted off the game — browser zoom changed, window resized, or a
            # popup covered it. Fire ONE loud alert (status + HUD + Telegram)
            # telling the user to recalibrate, instead of silently spinning.
            # 45s default is comfortably longer than the longest normal gap with
            # no 'PLACE YOUR BETS'/'NEXT GAME SOON' on screen (one spin + result),
            # so it won't false-fire mid-round, but still catches a dead region
            # well inside a 2-minute session.
            last_valid_table_state_time = time.time()
            drift_alert_secs = self.config.get("region_drift_alert_secs", 45)
            drift_alerted = False
            
            # Betting state tracking
            current_bet_amount = None  # Current bet amount for this round
            waiting_for_result = False  # Whether we've placed a bet and waiting for result
            bet_placed_this_round = False  # Flag to prevent multiple bets in same round
            round_number = 0  # Track round number
            is_first_round = True  # Flag to track if this is the first round
            self.has_placed_first_bet = False # Reset first bet flag
            bet_placed_time = 0 # Track when bet was placed to filter old results

            # ── Result-wait watchdog ──
            # The result handler depends on the watcher advancing
            # latest_winning_timestamp. If the winning-number OCR can't read the
            # result digit (noisy/misaligned region), that never happens and the
            # loop would wait FOREVER — the "won once, then never re-bets" hang.
            # After this long with a bet pending and no result, we recover the
            # round so betting resumes. 100s ≈ two Evolution rounds — long enough
            # not to fire mid-spin, short enough to unstick quickly.
            result_wait_recover_secs = self.config.get("result_wait_recover_secs", 100)
            result_wait_warned_for = 0  # bet_placed_time we've already recovered (auto-rearms per bet)

            # Session high point tracking
            session_high_point_reached = False  # Flag to track if session high point was reached
            current_win_streak = 0  # Track current consecutive wins
            max_win_streak_this_session = 0  # Track highest win streak in this session
            
            # Record Session Start Marker for Graph
            if hasattr(self, 'graph_markers') and hasattr(self, 'pnl_history'):
                # Use current length of history as X coordinate
                idx = len(self.pnl_history) - 1
                if idx < 0: idx = 0
                
                # Fix: current_session_num is 1-based in update loops, but 0-based default
                sess_num = getattr(self, 'current_session_num', 0)
                label = f"S{sess_num if sess_num > 0 else 1}"
                
                strat = str(self.config.get("strategy", "Unknown")).split(":")[0].split("[")[0].split("(")[0].strip()
                self.graph_markers.append((idx, label, strat))
            
            # OCR optimization - cache successful configurations
            successful_ocr_configs = {
                'table_state': None,
                'balance': None
            }

            # Session initialization - ensure window is properly positioned
            print("🚀 Initializing session - ensuring window is properly positioned...")
            self.log_simulation("🚀 Initializing session - ensuring window is properly positioned...")
            
            # Set session start timestamp for filtering historical data
            self.session_start_timestamp = time.time()
            print(f"🕐 Session start timestamp set: {self.session_start_timestamp}")
            self.log_simulation(f"🕐 Session start timestamp set: {self.session_start_timestamp}")
            
            # Force initial scroll reset and wait for window to stabilize
            driver.reset_scroll_keyboard()
            time.sleep(2.0)  # Wait longer for initial positioning
            
            # Verify table state region is visible and readable
            table_state_region = self.coordinates.get("table_state")
            if table_state_region:
                print("🔍 Verifying table state region is visible...")
                initial_state_check = extract_table_state(self.recorder.browser_win, table_state_region, successful_ocr_configs)
                if initial_state_check and initial_state_check.strip():
                    print(f"✅ Table state region is visible and readable: '{initial_state_check}'")
                    self.log_simulation(f"✅ Table state region is visible and readable: '{initial_state_check}'")
                else:
                    print("⚠️ Table state region not readable on first attempt, performing additional reset...")
                    self.log_simulation("⚠️ Table state region not readable on first attempt, performing additional reset...")
                    driver.reset_scroll_keyboard()
                    time.sleep(1.0)
            else:
                print("❌ No table_state region configured")
                return

            print("✅ Session initialization completed")
            self.log_simulation("✅ Session initialization completed")

            # Initialize session high using payout-based profit (starts at 0)
            self.session_high = 0.0
            self.last_bet_result = None # Track for extension logic

            # Store starting balance for this session
            self.session_start_balance = self.config["current_balance"]
            current_balance = self.session_start_balance

            while self.bot_running:
                # print(f"[LoopHeartbeat] Running... Paused={self.bot_paused}") # Debug heartbeat
                # Check for PAUSE
                if self.bot_paused:
                    # Update status periodically or just sleep
                    # self.update_stats_display(current_session="PAUSED") # Optional: update display
                    time.sleep(0.5)
                    continue

                # --- DYNAMIC CONFIG RELOAD ---
                # Re-parse limits from GUI VARS (Source of Truth) or Config
                try:
                    # Helper to get value from Var or Config
                    def get_live_val(var_name, config_key):
                        if hasattr(self, var_name):
                            return getattr(self, var_name).get()
                        return self.config.get(config_key)

                    self.active_global_profit_limit = self.parse_hybrid_value(get_live_val('global_profit_stop_var', 'global_profit_stop'), self.initial_run_balance)
                    self.active_global_loss_limit = self.parse_hybrid_value(get_live_val('global_stop_loss_var', 'global_stop_loss'), self.initial_run_balance)
                    self.active_session_profit_limit = self.parse_hybrid_value(get_live_val('profit_target_var', 'profit_target'), self.session_start_balance)
                    self.active_session_loss_limit = self.parse_hybrid_value(get_live_val('max_loss_var', 'max_loss'), self.session_start_balance)
                    self.active_trailing_stop_limit = self.parse_hybrid_value(get_live_val('trailing_stop_amount_var', 'trailing_stop_amount'), self.session_start_balance)
                except Exception as e:
                     print(f"⚠️ Error reloading dynamic config: {e}")
                
                # Debug print to confirm limits in the loop
                # print(f"[LoopDebug] GlobLimit: {self.active_global_loss_limit}, SessLimit: {self.active_session_loss_limit}") # Uncomment for verbose debugging
                # -----------------------------

                # Check for session end ONLY if we are not waiting for a result
                # This ensures we don't cut off a round mid-spin
                # Recalculate strict end condition based on effective duration
                current_time = time.time()
                # Use total_paused_duration updated by toggle/main loop
                # If currently paused, total_paused is growing? No, tracked on resume.
                # Actually, if we are paused, time.time() grows but total_paused doesn't update until resume.
                # But we are INSIDE the loop, continue-ing if paused. So we only reach here if RUNNING.
                # At this point, total_paused_duration encompasses all *previous* pauses.
                
                if self.auto_roulette_paused or self.bot_paused:
                    # While paused, effective duration is constant (frozen at the time of pause)
                    if self.pause_start_time:
                         # time_elapsed_at_pause = (pause_start - start) - total_prev_paused
                         effective_duration = (self.pause_start_time - session_start_time_wall) - self.total_paused_duration
                    else:
                         effective_duration = (current_time - session_start_time_wall) - self.total_paused_duration
                else:
                    # Running: effective = (now - start) - total_paused
                    effective_duration = (current_time - session_start_time_wall) - self.total_paused_duration

                if round_number % 10 == 0 or self.auto_roulette_paused: # Print debug more often if paused logic checking
                     # print(f"[DEBUG] Time: Active={effective_duration:.1f}s / Limit={duration:.1f}s | PausedTotal={self.total_paused_duration:.1f}s")
                     pass 

                # Initialize SessionManager for this session if not invalid or different
                if self.session_manager is None:
                    # Initialize with CURRENT running config (ensure live values are captured)
                    # We might need to update the config dict with live VAR values first if they drifted
                    live_config = self.config.copy()
                    live_config.update({
                       "current_balance": self.session_start_balance,
                       "max_extension_rounds": int(self.max_ext_rounds_var.get() or 20),
                       "extension_give_up_amount": float(self.ext_give_up_var.get() or 50.0),
                       "global_profit_stop": getattr(self, 'active_global_profit_limit', 0),
                       "global_stop_loss": getattr(self, 'active_global_loss_limit', 0),
                       "profit_target": getattr(self, 'active_session_profit_limit', 0),
                       "max_loss": getattr(self, 'active_session_loss_limit', 0),
                       "max_session_wins_streak": int(self.max_session_wins_streak_var.get() or 0) if self.enable_session_stops_var.get() else 0,
                       "max_session_losses_streak": int(self.max_session_losses_streak_var.get() or 0) if self.enable_session_stops_var.get() else 0,
                       "trailing_stop_amount": getattr(self, 'active_trailing_stop_limit', 0),
                       "session_ext_after_win": self.session_ext_after_win_var.get(),
                       "session_ext_at_high": self.session_ext_at_high_var.get()
                    })
                    self.session_manager = SessionManager(live_config)

                # Update Session Manager State
                # Using current_win_streak from GUI loop. Warning: GUI only tracks WIN streak?
                # We need a signed streak (+Wins, -Losses). 
                # Assuming current_win_streak is only +ve.
                # Let's derive signed streak from last_bet_result and current_win_streak logic
                # Actually main_gui loops: if win -> streak++, if loss -> streak=0.
                # So it only tracks Win Streak locally.
                # We need to track signed streak or separate streaks properly.
                # Let's use the local `current_win_streak` if >0, else calculate loss streak?
                # The GUI has `current_win_streak` reset to 0 on loss. 
                # It does NOT appear to track `current_loss_streak` explicitly in the loop variables shown previously.
                
                # RE-CHECK: `consecutive_losses` is tracked in strategy? 
                # Yes: `strategy.consecutive_losses` is available in the loop.
                # So we can construct signed_streak:
                signed_streak = 0
                if current_win_streak > 0:
                    signed_streak = current_win_streak
                elif hasattr(strategy, 'consecutive_losses') and strategy.consecutive_losses > 0:
                    signed_streak = -strategy.consecutive_losses
                
                self.session_manager.update_state(
                    pnl=self.cumulative_net_profit,
                    wins=session_wins,
                    losses=session_losses,
                    global_pnl=getattr(self, 'cumulative_profit_offset', 0.0) + self.cumulative_net_profit,
                    streak=signed_streak
                )

                # Check Stop Conditions
                should_stop, stop_reason, stop_msg = self.session_manager.check_stop_conditions(
                    bot_running=self.bot_running,
                    last_result=self.last_bet_result
                )

                # UI Feedback for Extension
                if stop_reason == "EXTENDING":
                     # Show Orange Status in GUI
                     self.status_var.set(f"⚠️ {stop_msg}")
                     
                     # Show in Overlay
                     self.update_hud_safe(
                         header="⏳ EXTENDING",
                         result=stop_msg,
                         pnl=f"${self.cumulative_net_profit:.2f}"
                     )
                elif stop_reason != StopReason.CONTINUE:
                     # Stop Triggered
                     print(f"🛑 STOP: {stop_msg} ({stop_reason})")
                     self.log_simulation(f"🛑 STOP: {stop_msg}")
                     
                     # Determine Header Color/Text based on reason
                     header_text = f"STOP: {stop_reason}"
                     if stop_reason in [StopReason.GLOBAL_PROFIT, StopReason.PROFIT_TARGET]:
                         header_text = "🏆 TARGET REACHED"
                     elif stop_reason in [StopReason.GLOBAL_LOSS, StopReason.STOP_LOSS, StopReason.TRAILING_STOP, "STREAK_LIMIT"]:
                         header_text = "🛑 LIMIT REACHED"
                     elif stop_reason == StopReason.TIME_LIMIT:
                         header_text = "⏱️ TIME LIMIT"
                         
                     # Global Stop Flag Handling
                     if stop_reason in [StopReason.GLOBAL_PROFIT, StopReason.GLOBAL_LOSS]:
                         self.stop_all_sessions = True
                         header_text = "🌍 GLOBAL STOP"

                     # Update HUD immediately
                     self.update_hud_safe(
                         header=header_text,
                         result=stop_msg, # Display the detail message in the 'result' slot or similar
                         pnl=f"${self.cumulative_net_profit:.2f}"
                     )

                     # Handle "Soft Stop" waiting logic
                     if not waiting_for_result:
                         break
                     else:
                         # Wait for result, will break next loop
                         pass
                
                # 1. Check table state (only reset scroll when needed)
                table_state_region = self.coordinates.get("table_state")
                balance_region = self.coordinates.get("balance")
                
                if not table_state_region:
                    print("❌ No table_state region configured")
                    break
                
                # Force scroll reset if too many consecutive failures
                if consecutive_failures >= max_consecutive_failures:
                    print(f"🔄 Forcing scroll reset after {consecutive_failures} consecutive failures")
                    scroll_reset_needed = True
                    consecutive_failures = 0
                
                # Only reset scroll if we haven't done it recently or if state changed
                if scroll_reset_needed:
                    driver.reset_scroll_keyboard()
                    scroll_reset_needed = False
                    time.sleep(0.5)  # Reduced wait time
                
                state_text = extract_table_state(self.recorder.browser_win, table_state_region, successful_ocr_configs)
                state_upper = state_text.upper()
                
                # Define expected keywords for valid table states
                expected_keywords = ['PLACE', 'NEXT', 'GAME', 'YOUR', 'SOON', 'BETS', 'CLOSED', 'ACCEPTED']
                
                # Check if the detected state is valid
                is_valid_state = any(keyword in state_upper for keyword in expected_keywords)
                
                if is_valid_state:
                    consecutive_failures = 0  # Reset failure counter on valid state
                    table_state_failures = 0  # Reset table state failure counter on valid state
                    last_valid_table_state_time = time.time()
                    if drift_alerted:
                        # Region recovered (recalibrated / popup closed / zoom restored).
                        drift_alerted = False
                        self.log_simulation("✅ Table state region recovered — detection resumed.")
                        self.set_status("✅ Table detection resumed")
                else:
                    consecutive_failures += 1
                    table_state_failures += 1
                    print(f"⚠️ Unexpected table state detected: '{state_text}' (failures: {table_state_failures})")
                    self.log_simulation(f"⚠️ Unexpected table state: '{state_text}' (failures: {table_state_failures})")
                    # ── Region-drift alarm ── sustained inability to read ANY
                    # valid state ⇒ the region is off the game. Fire once, loudly.
                    if (not drift_alerted
                            and time.time() - last_valid_table_state_time > drift_alert_secs):
                        drift_alerted = True
                        secs = int(time.time() - last_valid_table_state_time)
                        # Corroborate with the balance region — if it's ALSO dead,
                        # the whole coordinate map has shifted (zoom/resize), not
                        # just a one-off banner read.
                        bal_dead = False
                        try:
                            _bal_region = self.coordinates.get("balance")
                            if _bal_region is not None:
                                bal_dead = extract_balance(self.recorder.browser_win, _bal_region) is None
                        except Exception:
                            pass
                        _both = (" Balance region is ALSO unreadable → the whole coordinate "
                                 "map has shifted." if bal_dead else "")
                        msg = (f"Table region misaligned — no valid state for {secs}s "
                               f"(last read: '{state_text}').{_both} "
                               f"Fix: set browser zoom to 100%, close any wallet/withdrawal "
                               f"popup, then re-record the 'table_state' region (or run "
                               f"Auto-Detect Table).")
                        print("🚨 " + msg)
                        self.log_simulation("🚨 " + msg)
                        self.set_status("🚨 Table region misaligned — recalibrate (see log)")
                        try:
                            self.update_hud_safe(header="🚨 REGION MISALIGNED",
                                                 result=f"reading '{state_text}'",
                                                 status="recalibrate table_state")
                        except Exception:
                            pass
                        try:
                            if getattr(self, 'telegram_bot', None):
                                self.telegram_bot.send_notification("🚨 Spinedge: " + msg)
                        except Exception:
                            pass
                
                # Check if we need to reset keyboard due to table state detection failures
                current_time = time.time()
                if (table_state_failures >= max_table_state_failures and 
                    current_time - last_table_state_reset > table_state_reset_cooldown):
                    
                    print(f"🔄 Table state detection failed {table_state_failures} times, performing keyboard reset...")
                    self.log_simulation(f"🔄 Table state detection failed {table_state_failures} times, performing keyboard reset...")
                    
                    # Perform keyboard reset
                    # driver.reset_scroll_keyboard()
                    # time.sleep(1.0)  # Wait a bit longer after keyboard reset
                    
                    # Reset counters
                    table_state_failures = 0
                    last_table_state_reset = current_time
                    scroll_reset_needed = False  # We just did a reset
                    
                    print("✅ Keyboard reset completed for table state detection")
                    self.log_simulation("✅ Keyboard reset completed for table state detection")
                
                # Only print state if it changed
                if state_text != last_table_state:
                    print(f"📊 Table state: {state_text}")
                    last_table_state = state_text
                
                # Handle win/loss determination using winning number detection (separate from bet placement)
                if self.has_placed_first_bet and waiting_for_result:
                    # 🔍 DEBUG: Log session timestamp for validation
                    current_time = time.time()
                    if self.session_start_timestamp:
                        time_since_session_start = current_time - self.session_start_timestamp
                        print(f"🔍 DEBUG: Session timestamp check - Current: {current_time:.2f}, Session start: {self.session_start_timestamp:.2f}, Time since start: {time_since_session_start:.2f}s")
                    else:
                        print(f"⚠️ WARNING: No session timestamp set! This may cause historical data processing issues.")
                    
                    # Get the latest winning number from the watcher
                    winning_number, winning_color = self.get_latest_winning_number()
                    if winning_number is not None:
                        # 🕒 TIMESTAMP CHECK: Ensure result appeared AFTER we placed the bet
                        if self.latest_winning_timestamp <= bet_placed_time:
                             print(f"⚠️ Ignoring winning number {winning_number} detected at {self.latest_winning_timestamp:.2f} (Before bet placed at {bet_placed_time:.2f})")
                             logger.info(f"Ignoring old result {winning_number} (timestamp {self.latest_winning_timestamp:.2f} <= bet {bet_placed_time:.2f})")
                             # Still feed the number to the strategy's history for dynamic strategies
                             # (neighbors needs history to know what to bet next round)
                             if hasattr(strategy, 'strategy') and hasattr(strategy.strategy, 'record_result'):
                                 import inspect
                                 sig = inspect.signature(strategy.strategy.record_result)
                                 if 'last_number' in sig.parameters:
                                     strategy.strategy.record_result(False, last_number=winning_number)
                                     logger.info(f"[HistorySeed] Fed pre-bet number {winning_number} to strategy history")
                             # We already consumed it from get_latest_winning_number, so just continue waiting
                             # waiting_for_result remains True
                             continue

                        # ── Keep-alive bet result handling ──
                        # Keep-alive bets are placed during long sit-out streaks to keep
                        # the casino table active. They MUST NOT advance progression.
                        # We feed the winning number to history, update the HUD, reset
                        # round flags, and skip the rest of the result block.
                        if getattr(self, '_keep_alive_pending', False):
                            try:
                                if hasattr(strategy, 'strategy') and hasattr(strategy.strategy, 'record_result'):
                                    import inspect
                                    sig = inspect.signature(strategy.strategy.record_result)
                                    if 'last_number' in sig.parameters:
                                        strategy.strategy.record_result(False, last_number=winning_number)
                            except Exception as e:
                                logger.warning(f"[Keep-alive] Failed to feed history: {e}")
                            # CRITICAL: also feed the trigger engine. The keep-alive
                            # short-circuit consumes the OCR's last_processed cursor
                            # (so the skip-path get_latest_winning_number sees nothing
                            # new), but without this call the trigger NumberHistory
                            # would never grow during long all-skip stretches — making
                            # consecutive_* conditions impossible to arm.
                            try:
                                self._trigger_feed_winning_number(winning_number)
                            except Exception as _trig_err:
                                logger.warning(f"[Keep-alive] Failed to feed trigger history: {_trig_err}")
                            try:
                                self.update_hud_safe(
                                    number=f"{winning_number} {winning_color or ''}",
                                    result="KEEP-ALIVE",
                                    pnl=f"${getattr(self, 'cumulative_net_profit', 0.0):.2f}",
                                )
                            except Exception:
                                pass
                            print(f"⏰ Keep-alive result: {winning_number} (progression unchanged)")
                            self._keep_alive_pending = False
                            waiting_for_result = False
                            bet_placed_this_round = False
                            current_bets = []
                            continue

                        # ── PARALLEL-mode result handling ──
                        # Each armed strategy in the parallel round gets its own
                        # win/loss based on its own labels — their progressions
                        # advance independently (one martingales, another resets).
                        # Bundle-level P&L is the sum of all per-strategy P&Ls,
                        # so cumulative_net_profit / session_pnl / streak counters
                        # still reflect what users expect.
                        if getattr(self, '_parallel_round', None):
                            try:
                                net_pnl, summary, total_bet, results = \
                                    self._handle_parallel_result(winning_number, winning_color)
                            except Exception as _par_err:
                                logger.error(f"[Parallel] result handler failed: {_par_err}")
                                net_pnl, summary, total_bet, results = 0.0, "(error)", 0.0, []
                            self.cumulative_net_profit += net_pnl
                            self.session_high = max(getattr(self, 'session_high', 0.0),
                                                    self.cumulative_net_profit)
                            current_total = (getattr(self, 'cumulative_profit_offset', 0.0)
                                             + self.cumulative_net_profit)
                            self.pnl_history.append(current_total)
                            TOL = 1e-6
                            if net_pnl > TOL:
                                session_wins += 1
                                self.total_wins += 1
                                current_win_streak += 1
                                max_win_streak_this_session = max(max_win_streak_this_session, current_win_streak)
                                self.last_bet_result = 'win'
                                _hud_result = "WIN"
                            elif net_pnl < -TOL:
                                self.total_losses += 1
                                current_win_streak = 0
                                self.last_bet_result = 'loss'
                                _hud_result = "LOSS"
                            else:
                                self.last_bet_result = 'breakeven'
                                _hud_result = "BREAKEVEN"
                            try:
                                self.update_hud_safe(
                                    number=f"{winning_number} {winning_color or ''}",
                                    result=f"{_hud_result} (×{len(results)})",
                                    pnl=f"${self.cumulative_net_profit:.2f}",
                                    streak=current_win_streak,
                                    graph_data=self.pnl_history,
                                    graph_markers=getattr(self, 'graph_markers', None),
                                )
                            except Exception:
                                pass
                            logger.info(f"🎯 Parallel result: spin={winning_number} net=${net_pnl:+.2f} | {summary}")
                            self.log_simulation(f"🎯 Parallel ({len(results)} strats): net ${net_pnl:+.2f} — {summary}")
                            # Feed trigger engine with the new spin (engines
                            # already updated via record_result in the handler).
                            try:
                                self._trigger_feed_winning_number(winning_number)
                            except Exception:
                                pass
                            # Session-manager update (bundle-level state)
                            if hasattr(self, 'session_manager') and self.session_manager is not None:
                                try:
                                    _streak = current_win_streak if current_win_streak > 0 else 0
                                    self.session_manager.update_state(
                                        pnl=self.cumulative_net_profit,
                                        wins=session_wins,
                                        losses=session_rounds - session_wins,
                                        global_pnl=(getattr(self, 'cumulative_profit_offset', 0.0)
                                                    + self.cumulative_net_profit),
                                        streak=_streak,
                                    )
                                except Exception:
                                    pass
                            session_rounds += 1
                            self._parallel_round = None
                            waiting_for_result = False
                            bet_placed_this_round = False
                            current_bets = []
                            continue

                        from core.strategy_engine import calculate_win_amount

                        total_bet = sum(b['amount'] for b in current_bets)
                        win_amt, details = calculate_win_amount(current_bets, winning_number)
                        # Add original bet for each winning bet to get total return
                        total_return = win_amt + sum(b['amount'] for b, d in zip(current_bets, details) if d['win'])
                        net_profit = total_return - total_bet

                        TOL = 1e-6 
                        # Use net_profit for win/loss logic (ignore break-even/partial win)
                        if net_profit > TOL:
                            # Proper win
                            session_wins += 1
                            self.total_wins += 1
                            current_win_streak += 1
                            max_win_streak_this_session = max(max_win_streak_this_session, current_win_streak)
                            print(f"📈 WIN recorded! Win streak: {current_win_streak}")
                            current_total = getattr(self, 'cumulative_profit_offset', 0.0) + self.cumulative_net_profit + net_profit
                            current_session_profit = self.cumulative_net_profit + net_profit
                            self.pnl_history.append(current_total)
                            self.last_bet_result = 'win'
                            self.update_hud_safe(
                                number=f"{winning_number} {winning_color or ''}", 
                                result="WIN", 
                                pnl=f"${current_session_profit:.2f}", 
                                streak=current_win_streak,
                                graph_data=self.pnl_history,
                                graph_markers=getattr(self, 'graph_markers', None)
                            )
                        elif net_profit < -TOL:
                            # Loss
                            self.total_losses += 1
                            current_win_streak = 0
                            print(f"📉 LOSS recorded! Win streak reset to 0")
                            current_total = getattr(self, 'cumulative_profit_offset', 0.0) + self.cumulative_net_profit + net_profit
                            current_session_profit = self.cumulative_net_profit + net_profit
                            self.pnl_history.append(current_total)
                            self.last_bet_result = 'loss'
                            self.update_hud_safe(
                                number=f"{winning_number} {winning_color or ''}", 
                                result="LOSS", 
                                pnl=f"${current_session_profit:.2f}", 
                                streak=0,
                                graph_data=self.pnl_history,
                                graph_markers=getattr(self, 'graph_markers', None)
                            )
                        else:
                            # net_profit == 0: break even, do not record as win or loss
                            # net_profit == 0: break even, do not record as win or loss
                            print(f"➖ Break even (partial win). No win/loss recorded.")
                            current_total = getattr(self, 'cumulative_profit_offset', 0.0) + self.cumulative_net_profit + net_profit
                            self.last_bet_result = 'tie' # Treat as non-win for strict extension
                            self.update_hud_safe(
                                number=f"{winning_number} {winning_color or ''}", 
                                result="BREAK EVEN", 
                                pnl=f"${current_total:.2f}",
                                graph_data=self.pnl_history,
                                graph_markers=getattr(self, 'graph_markers', None)
                            )
                        session_rounds += 1
                        with self._state_lock:
                            self.cumulative_net_profit += net_profit
                            projected_balance = self.session_start_balance + self.cumulative_net_profit
                        with self._config_lock:
                            self.config["current_balance"] = projected_balance # Update config for global tracking
                        self.update_stats_display(projected_balance=projected_balance)
                        
                        # (Moved Immediate Stop Check to after result recording)
                        if self.cumulative_net_profit > self.peak_net_profit:
                            self.peak_net_profit = self.cumulative_net_profit
                            # Only reset non-dynamic progressions here.
                            # DynamicProgressionStrategy manages its own session_high/reset logic
                            # via profit_at_or_above_session_high rules — external reset would
                            # wipe its internal state and break conditional keep/martingale rules.
                            if hasattr(strategy, 'progression') and hasattr(strategy.progression, 'reset'):
                                if strategy.progression.__class__.__name__ != 'DynamicProgressionStrategy':
                                    strategy.progression.reset()
                                    print(f"🔄 Progression reset to base bet due to new peak net profit: {self.peak_net_profit}")
                        # Track the all-time-high GLOBAL PnL during the run so
                        # escalation can reset when we recover to that peak.
                        running_global = (getattr(self, 'cumulative_profit_offset', 0.0)
                                          + self.cumulative_net_profit)
                        prior_peak = getattr(self, '_peak_global_pnl', 0.0)
                        if running_global > prior_peak:
                            self._peak_global_pnl = running_global
                            print(f"[Escalation] 📈 New global peak: ${running_global:.2f} "
                                  f"(prev ${prior_peak:.2f})")

                        # ── Round audit capture ───────────────────────────────
                        try:
                            from gui.round_audit import record_round
                            if net_profit > TOL:
                                _audit_result = "WIN"
                            elif net_profit < -TOL:
                                _audit_result = "LOSS"
                            else:
                                _audit_result = "BREAK_EVEN"
                            _audit_progression = getattr(strategy, 'progression', None)
                            _audit_loss_streak = getattr(strategy, 'consecutive_losses', 0)
                            # Store just the base strategy name (e.g. "romanvski6")
                            # rather than the full rotation spec
                            # ("romanvski6:dynamic|rules=...") so the audit filter
                            # dropdown stays readable.
                            _audit_strategy_full = str(self.config.get("strategy", "") or "")
                            _audit_strategy_name = _audit_strategy_full.split(":", 1)[0].strip() \
                                                   if _audit_strategy_full else ""
                            record_round(
                                self,
                                session_num=getattr(self, 'current_session_num', 0),
                                round_index=session_rounds,
                                winning_number=int(winning_number) if winning_number is not None else None,
                                winning_color=str(winning_color or ''),
                                result=_audit_result,
                                bets=list(details),
                                total_bet=float(total_bet),
                                total_return=float(total_return),
                                net_profit=float(net_profit),
                                strategy_name=_audit_strategy_name,
                                progression_type=str(self.config.get("progression_type", "") or ""),
                                base_bet=float(self.config.get("base_bet", 0.0) or 0.0),
                                current_bet=float(getattr(_audit_progression, 'current_bet', 0.0) or 0.0)
                                            if _audit_progression else 0.0,
                                martingale_level=int(getattr(_audit_progression, 'martingale_level', 0) or 0)
                                                  if _audit_progression else 0,
                                session_pnl_after=float(self.cumulative_net_profit),
                                global_pnl_after=float(running_global),
                                balance_after=float(projected_balance),
                                win_streak=int(current_win_streak),
                                loss_streak=int(_audit_loss_streak),
                                escalation_step=int(getattr(self, '_escalation_step', 0) or 0),
                            )
                        except Exception as _audit_exc:
                            print(f"[RoundAudit] Capture skipped: {_audit_exc}")
                        # Use strategy mapping to determine if this is a win
                        # if hasattr(strategy, 'is_winning_number'):
                        #     result = strategy.is_winning_number(winning_number)
                        #     if result:
                        #         print(f"✅ WIN! Number {winning_number} is covered by strategy")
                        #     else:
                        #         print(f"❌ LOSS! Number {winning_number} is not covered by strategy")
                        #     # Process the result
                        #     if result:
                        #         session_wins += 1
                        #         self.total_wins += 1
                        #         current_win_streak += 1
                        #         max_win_streak_this_session = max(max_win_streak_this_session, current_win_streak)
                        #         print(f"📈 WIN recorded! Win streak: {current_win_streak}")
                        #     else:
                        #         self.total_losses += 1
                        #         current_win_streak = 0
                        #         print(f"📉 LOSS recorded! Win streak reset to 0")
                        #     session_rounds += 1
                            # Update stats display using payout-based profit
                        current_profit = getattr(self, 'cumulative_net_profit', 0.0)
                        # Total PnL must include the rolled-over offset from prior
                        # sessions; otherwise the widget appears to "reset" to $0
                        # at the start of every new session after a stop-loss.
                        running_total = getattr(self, 'cumulative_profit_offset', 0.0) + current_profit
                        self.update_stats_display(
                            wins=self.total_wins,
                            losses=self.total_losses,
                            win_rate=self.calculate_win_rate(),
                            consecutive_losses=strategy.consecutive_losses,
                            session_pnl=current_profit,
                            total_pnl=running_total,
                            rounds_played=session_rounds
                        )

                        # Record result in strategy (this will calculate next bet)
                        is_win = net_profit > TOL
                        if hasattr(strategy, 'progression') and hasattr(strategy.progression, 'record_result') and strategy.progression.__class__.__name__ == 'DynamicProgressionStrategy':
                            # Use payout-based profit instead of balance for dynamic progression.
                            # Reconstruct the live balance from the SAME baseline the engine
                            # subtracts (progression.session_start_balance) so current_profit ==
                            # session P&L (cumulative_net_profit) EXACTLY. Using
                            # self.session_start_balance here caused an offset whenever the live
                            # OCR start balance (set on progression.session_start_balance at
                            # session start) differed from config["current_balance"] — that offset
                            # shifted the "session high" zero point and stopped
                            # reset_to_base|condition=profit_at_or_above_session_high from firing
                            # when actually in profit. (Invisible in simulation: OCR returns 0 there
                            # and falls back to config, so both baselines matched.)
                            prog_baseline = getattr(strategy.progression, 'session_start_balance', None)
                            if prog_baseline is None:
                                prog_baseline = getattr(self, 'session_start_balance', 0.0)
                            pass_balance = prog_baseline + getattr(self, 'cumulative_net_profit', 0.0)
                            logger.debug(f"🔍 DEBUG: Using DynamicProgressionStrategy with projected_balance={pass_balance}")
                            strategy.record_result(is_win, current_balance=pass_balance, winning_number=winning_number, round_pnl=net_profit)
                        else:
                            logger.debug(f"🔍 DEBUG: Using standard progression strategy")
                            strategy.record_result(is_win, winning_number=winning_number, round_pnl=net_profit)
                        logger.debug(f"🔍 DEBUG: strategy.record_result() completed")
                        logger.info(f"📈 Strategy: {'Win' if is_win else 'Loss'} recorded. Last bet was: {current_bet_amount}")
                        logger.debug(f"🔍 DEBUG: Win/loss determination completed")

                        # --- PER-LEG STOP (single / sequential mode) ---
                        # Parallel mode disarms individual legs inside
                        # _handle_parallel_result; here the active strategy IS the
                        # session, so its per-strategy "Stop" (wins/losses/profit/
                        # loss/time from the bundle entry) halts the bot. Scoped to
                        # non-parallel runs (_trigger_engine is None) to avoid
                        # double-counting across conditional-mode label swaps.
                        if getattr(self, '_trigger_engine', None) is None and hasattr(strategy, 'check_session_stop'):
                            _leg_stop = strategy.check_session_stop()
                            if _leg_stop:
                                _sname = getattr(strategy, 'strategy_name', self.config.get('strategy', '?'))
                                _reason = f"{_sname} {_leg_stop}"
                                logger.info(f"🛑 Strategy stop reached: {_reason}")
                                self.log_simulation(f"🛑 Bot stopped — strategy stop: {_reason}")
                                self.set_status(f"Bot stopped — strategy stop: {_reason}")
                                try:
                                    self.update_hud_safe(header="🛑 STOPPED", result=_leg_stop)
                                except Exception:
                                    pass
                                break

                        # Feed the spin into the conditional trigger engine BEFORE the
                        # legacy switch-on-loss block. When triggers are active they take
                        # over swap decisions and the switch-on-loss path is bypassed to
                        # avoid double-swapping the same loss.
                        self._trigger_feed_winning_number(winning_number)

                        # --- SWITCH-ON-LOSS: Rotate strategy mid-session if enabled ---
                        # Bypassed when the conditional TriggerEngine is in charge.
                        if (getattr(self, '_trigger_engine', None) is None
                                and not is_win
                                and self.enable_strategy_rotation_var.get()
                                and getattr(self, 'rotation_trigger', 'session_end') == 'on_loss'):
                            switch_threshold = max(1, self.switch_after_n_losses_var.get())
                            # Use consecutive_losses from strategy engine (already updated by record_result above)
                            logger.info(f"🔀 Switch-on-loss check: consec_losses={strategy.consecutive_losses}, threshold={switch_threshold}")
                            if strategy.consecutive_losses >= switch_threshold:
                                old_name = self.config.get("strategy", "?")
                                strategy = self.rebuild_strategy_on_loss(strategy)
                                new_name = self.config.get("strategy", "?")
                                logger.info(f"🔀 Strategy rotated: {old_name} → {new_name}")

                        # Record result in strategy (this will calculate next bet)
                        # print(f"🔍 DEBUG: About to call strategy.record_result(result={result})")
                        # if hasattr(strategy, 'progression') and hasattr(strategy.progression, 'record_result') and strategy.progression.__class__.__name__ == 'DynamicProgressionStrategy':
                        #     print(f"🔍 DEBUG: Using DynamicProgressionStrategy with current_balance={current_balance}")
                        #     strategy.record_result(result, current_balance)
                        # else:
                        #     print(f"🔍 DEBUG: Using standard progression strategy")
                        #     strategy.record_result(result)
                        # print(f"🔍 DEBUG: strategy.record_result() completed")
                        # print(f"📈 Strategy: {'Win' if result else 'Loss'} recorded. Last bet was: {current_bet_amount}")
                        # print(f"🔍 DEBUG: Win/loss determination completed")
                        # Reset round flags ONLY after result is recorded
                        waiting_for_result = False
                        bet_placed_this_round = False
                        # --- STOP CHECKS ---
                        # Synthesize streak
                        current_streak = current_win_streak if current_win_streak > 0 else -strategy.consecutive_losses
                        
                        # Update SessionManager
                        self.session_manager.update_state(
                            pnl=self.cumulative_net_profit,
                            wins=session_wins,
                            losses=session_rounds - session_wins,
                            global_pnl=getattr(self, 'cumulative_profit_offset', 0.0) + self.cumulative_net_profit,
                            streak=current_streak
                        )
                        
                        # 1. Check Session/Manager Stops
                        should_stop_sess, stop_reason_sess, stop_msg_sess = self.session_manager.check_stop_conditions(self.bot_running, self.last_bet_result)
                        
                        # 2. Check Global Stops (Legacy)
                        should_stop_glob = self.should_end_session()
                        
                        if should_stop_sess or should_stop_glob:
                             final_msg = stop_msg_sess if should_stop_sess else "Global Stop Condition Met"
                             if should_stop_glob and getattr(self, 'stop_all_sessions', False):
                                  final_msg = "Global Target/Limit Met"
                             
                             print(f"🛑 STOP TRIGGERED: {final_msg}")
                             self.set_status(final_msg)
                             
                             # NOTIFY HUD & TELEGRAM
                             self.update_hud_safe(result="STOPPED", status=final_msg)
                             if self.telegram_bot:
                                 self.telegram_bot.send_notification(f"🛑 Session Stopped\n{final_msg}")

                             break

                        # ── Round-boundary engine re-arm (user switched bundle/strategy mid-run) ──
                        # Applies a queued bundle/strategy change immediately at
                        # the safe round boundary. No-op when nothing is queued.
                        strategy = self._apply_pending_engine_rearm(strategy)

                        # ── Round-boundary strategy swap (favorites pills / hotkeys / Telegram) ──
                        # If a swap was queued via request_strategy_swap(), rebuild the engine here.
                        # Returns old strategy unchanged when nothing is queued.
                        if getattr(self, 'pending_strategy_swap', None):
                            strategy = self._apply_pending_strategy_swap(strategy)
                        # Check session high point conditions
                        if False: # REDUNDANT BLOCK DISABLED (Handled by should_end_session)
                            # Check profit target using payout-based profit
                            current_profit = getattr(self, 'cumulative_net_profit', 0.0)
                            if (self.config.get("enable_profit_target", False) and 
                                self.active_session_profit_limit > 0 and 
                                current_profit >= self.active_session_profit_limit):
                                print(f"🎯 Session profit target reached: ${current_profit:.2f} >= ${self.active_session_profit_limit:.2f} (payout-based)")
                                self.log_simulation(f"🎯 Session profit target reached: ${current_profit:.2f} >= ${self.active_session_profit_limit:.2f} (payout-based)")
                                session_high_point_reached = True
                                break
                            # Check win streak target
                            if (self.config.get("enable_win_streak_target", False) and 
                                self.config.get("win_streak_target", 0) > 0 and 
                                current_win_streak >= self.config["win_streak_target"]):
                                print(f"🔥 Session win streak target reached: {current_win_streak} >= {self.config['win_streak_target']}")
                                self.log_simulation(f" Session win streak target reached: {current_win_streak} >= {self.config['win_streak_target']}")
                                session_high_point_reached = True
                                break
                    else:
                        # ── Result-wait watchdog ── unstick the "won once then
                        # never re-bets" hang. If a bet has been pending with no
                        # readable result for a full ~2 rounds, the OCR missed it;
                        # recover the round so betting resumes on the next PLACE
                        # YOUR BETS. We do NOT fabricate a win/loss — progression
                        # is left untouched for the missed round (safest choice).
                        _wait_elapsed = time.time() - bet_placed_time
                        if (bet_placed_time > 0
                                and _wait_elapsed > result_wait_recover_secs
                                and result_wait_warned_for != bet_placed_time):
                            result_wait_warned_for = bet_placed_time
                            _wd = (f"No result read for {int(_wait_elapsed)}s — winning-number "
                                   f"OCR is missing results (noisy table_state region). "
                                   f"Recovering the round so betting resumes; this round's "
                                   f"win/loss was NOT recorded (progression unchanged). "
                                   f"If this repeats, recalibrate table_state at 100% zoom.")
                            print("🚨 " + _wd)
                            self.log_simulation("🚨 " + _wd)
                            try:
                                if getattr(self, 'telegram_bot', None):
                                    self.telegram_bot.send_notification("⚠️ Spinedge: " + _wd)
                            except Exception:
                                pass
                            waiting_for_result = False
                            bet_placed_this_round = False
                            current_bets = []
                        else:
                            logger.debug("🔍 No new winning number yet, continuing to wait...")
                else:
                    logger.debug(f"🔍 DEBUG: No new winning number detected yet, continuing to wait...")
                # if is_first_round:
                #     bet_placed_this_round = True

                # 1. Handle "PLACE YOUR BETS" - place bet using config balance
                if "PLACE YOUR BETS" in state_upper and not bet_placed_this_round:
                    time.sleep(.3)
                    
                    if getattr(strategy, 'is_observing', False):
                        trigger_target = getattr(strategy, 'observation_trigger', 0)
                        misses = getattr(strategy, 'consecutive_misses', 0)
                        msg = f"Waiting for Sequence ({misses}/{trigger_target})"
                        print(f"👀 Observation Mode: {msg}")
                        self.update_stats_display(current_bet=0.0, betting_on="👀 OBSERVER")
                        self.update_hud_safe(header="👀 OBSERVING", result=msg)
                        
                        bet_placed_this_round = True
                        waiting_for_result = True
                        bet_placed_time = time.time()
                    elif current_balance is not None:
                        # ── PARALLEL-mode round (every armed strategy bets) ──
                        # When selection_mode == "parallel", we bypass the legacy
                        # single-strategy bet placement entirely. Each armed
                        # candidate places its own bet (from its own engine's
                        # progression), all chips are merged into one click
                        # sequence, and the result handler attributes per-strategy
                        # win/loss + advances each progression independently.
                        if (getattr(self, '_trigger_engine', None) is not None
                                and getattr(self._trigger_engine, 'selection_mode', 'conditional') == 'parallel'):
                            per_strat, merged_bets, total_bet = self._build_parallel_round_plan()
                            # If EVERY leg has hit its per-strategy stop, there's
                            # nothing left to play — halt instead of sitting out /
                            # keep-alive cycling forever.
                            _all_legs = set((self._trigger_engines_by_base or {}).keys())
                            _stopped_legs = getattr(self, '_parallel_stopped_legs', None) or set()
                            if _all_legs and _stopped_legs >= _all_legs:
                                logger.info("🛑 Parallel: all legs hit their per-strategy stops — stopping bot")
                                self.log_simulation("🛑 Bot stopped — all parallel legs reached their stops")
                                self.set_status("Bot stopped — all legs reached their stops")
                                try:
                                    self.update_hud_safe(header="🛑 STOPPED", result="all legs stopped")
                                except Exception:
                                    pass
                                break
                            if not per_strat:
                                # No candidate armed (or all refused) — treat as
                                # a skip-round and let keep-alive logic decide
                                # whether to place a maintenance bet.
                                logger.info("🎯 Parallel: no candidate armed — skip round")
                                self.set_status("⏸ Parallel: no candidate armed")
                                try:
                                    _wn, _wc = (None, None)
                                    try:
                                        _wn, _wc = self.get_latest_winning_number()
                                    except Exception:
                                        pass
                                    if _wn is not None:
                                        try: self._trigger_feed_winning_number(_wn)
                                        except Exception: pass
                                    self.update_hud_safe(header="⏸ PARALLEL SKIP",
                                                         result="no candidate armed",
                                                         number=(f"{_wn} {_wc or ''}" if _wn is not None else None))
                                except Exception:
                                    pass
                                self._consecutive_sitouts = getattr(self, '_consecutive_sitouts', 0) + 1
                                _should_ka, _ka_reason = self._keep_alive_due()
                                if _should_ka:
                                    placed = self._place_keep_alive_bet(driver, f"parallel-skip {_ka_reason}")
                                    if placed:
                                        current_bets = placed
                                        bet_placed_this_round = True
                                        waiting_for_result = True
                                        bet_placed_time = time.time()
                                        continue
                                time.sleep(1.5)
                                continue
                            if total_bet > current_balance:
                                print(f"🛑 Insufficient balance for parallel round: "
                                      f"${total_bet:.2f} > ${current_balance:.2f}")
                                break
                            round_number += 1
                            print(f"🎲 Parallel Round {round_number}: "
                                  f"{len(per_strat)} strategies, total ${total_bet:.2f}")
                            for ps in per_strat:
                                print(f"   • {ps['name']}: ${ps['total_bet']:.2f} on "
                                      f"{[b['label'] for b in ps['bets']]}")
                            self.update_stats_display(
                                current_bet=total_bet,
                                betting_on=" | ".join(f"{ps['name']}({len(ps['bets'])})" for ps in per_strat),
                            )
                            try:
                                current_bets = self._place_parallel_bets(driver, merged_bets)
                            except Exception as _place_err:
                                logger.error(f"[Parallel] chip placement failed: {_place_err}")
                                time.sleep(1.5)
                                continue
                            self._parallel_round = per_strat
                            bet_placed_this_round = True
                            waiting_for_result = True
                            bet_placed_time = time.time()
                            self._last_real_bet_at = time.time()
                            self._consecutive_sitouts = 0
                            if not self.has_placed_first_bet:
                                self.has_placed_first_bet = True
                            print("⏳ Parallel bets placed, waiting for result...")
                            continue

                        # ── Conditional-trigger pre-check ───────────────────────────────
                        # The trigger is a GATE TO ENTER, not a GATE TO STAY. Once it
                        # fires and progression starts climbing (martingale doubling /
                        # fibonacci stepping / etc.), keep betting with the active
                        # strategy until the progression returns to base (= recovered
                        # to session high per the user's dynamic rule). Otherwise the
                        # bot would skip mid-recovery and abandon the doubled bet.
                        in_recovery = False
                        try:
                            _prog = getattr(strategy, 'progression', None)
                            _cur = getattr(_prog, 'current_bet', None) if _prog else None
                            _base = getattr(_prog, 'base_bet', None) if _prog else None
                            if _cur is not None and _base is not None and _cur > _base * 1.001:
                                in_recovery = True
                        except Exception:
                            pass

                        if in_recovery:
                            # Skip the trigger eval entirely — bet with the current
                            # strategy + progression. The log line lets the user see
                            # why no trigger evaluation appears this round.
                            trig_decision = None
                            try:
                                _mlvl = getattr(strategy.progression, 'martingale_level', '?')
                                logger.info(f"🔁 In recovery (bet={_cur:.2f} > base={_base:.2f}, "
                                            f"martingale_lvl={_mlvl}) — bypassing trigger eval")
                            except Exception:
                                pass
                        else:
                            trig_decision = self._evaluate_trigger_engine(
                                getattr(strategy, 'strategy_name', self.config.get('strategy', '?'))
                            )
                        if trig_decision is not None:
                            if trig_decision.action == 'skip':
                                # No bet this round. Mark the round closed for the
                                # state machine; the next "PLACE YOUR BETS" cycle will
                                # re-evaluate. The natural BETS-CLOSED → NEXT-GAME-SOON
                                # transition still advances spin history.
                                _hist_len = len(getattr(self._trigger_engine, 'history', []) or [])
                                logger.info(f"🎯 Trigger: skip round ({trig_decision.reason}) "
                                            f"[history={_hist_len} spins observed this session]")
                                self.log_simulation(f"🎯 Trigger skip: {trig_decision.reason} [{_hist_len} spins]")
                                self.set_status(f"⏸ Skip (trigger): {trig_decision.reason}")
                                # HUD update — mirror the sit-out path so users see the
                                # last winning number even on skip rounds (was blank
                                # before — looked like the HUD froze).
                                #
                                # CRITICAL: also feed the new spin into the trigger
                                # engine's NumberHistory + each strategy's internal
                                # history. Without this, history NEVER grows during
                                # long skip stretches because the result-recording
                                # branch (which normally feeds history) only fires
                                # for placed bets. That breaks all consecutive_*
                                # conditions — they stay stuck at 0 and never arm.
                                _wn, _wc = (None, None)
                                try:
                                    _wn, _wc = self.get_latest_winning_number()
                                except Exception:
                                    pass
                                if _wn is not None:
                                    try:
                                        self._trigger_feed_winning_number(_wn)
                                    except Exception:
                                        pass
                                    try:
                                        import inspect as _i
                                        inner = getattr(strategy, 'strategy', None)
                                        if inner and hasattr(inner, 'record_result'):
                                            _sig = _i.signature(inner.record_result)
                                            if 'last_number' in _sig.parameters:
                                                inner.record_result(False, last_number=_wn)
                                    except Exception:
                                        pass
                                try:
                                    _sp = getattr(self, 'cumulative_net_profit', 0.0)
                                    self.update_hud_safe(
                                        header="⏸ TRIGGER SKIP",
                                        result=trig_decision.reason,
                                        number=(f"{_wn} {_wc or ''}" if _wn is not None else None),
                                        pnl=f"${_sp:.2f}",
                                    )
                                except Exception:
                                    pass
                                self._consecutive_sitouts = getattr(self, '_consecutive_sitouts', 0) + 1
                                # Time-based keep-alive: trigger-skip rounds also count
                                # toward inactivity, so the casino doesn't time us out
                                # during a long no-arm stretch.
                                _should_ka, _ka_reason = self._keep_alive_due()
                                if _should_ka:
                                    placed = self._place_keep_alive_bet(driver, f"trigger-skip {_ka_reason}")
                                    if placed:
                                        current_bets = placed
                                        bet_placed_this_round = True
                                        waiting_for_result = True
                                        bet_placed_time = time.time()
                                        print("⏰ Keep-alive placed during trigger-skip, waiting for result...")
                                        continue
                                time.sleep(1.5)
                                continue
                            if (trig_decision.action == 'use' and trig_decision.strategy
                                    and trig_decision.strategy != getattr(strategy, 'strategy_name', None)):
                                logger.info(f"🎯 Trigger swap: → {trig_decision.strategy} ({trig_decision.reason})")
                                self.log_simulation(f"🎯 Trigger swap → {trig_decision.strategy}: {trig_decision.reason}")
                                # Minimal swap: ONLY change which numbers the bot
                                # bets on. The StrategyEngine wrapper (and its
                                # progression, dynamic_rules, session_high,
                                # consecutive_losses, martingale_level, total_profit
                                # — all of it) is left untouched. Without this,
                                # any rebuild would either drop the dynamic rules
                                # (when reading the global progression_var) or
                                # reset session_high / progression state even when
                                # we copy it over (because record_result was about
                                # to fire next round on a "fresh" engine that had
                                # no history).
                                try:
                                    _target_eng = (self._trigger_engines_by_base or {}).get(
                                        trig_decision.strategy)
                                    if _target_eng is not None and getattr(_target_eng, 'strategy', None) is not None:
                                        _old_name = getattr(strategy, 'strategy_name', '?')
                                        strategy.strategy = _target_eng.strategy
                                        strategy.strategy_name = trig_decision.strategy
                                        self.config["strategy"] = trig_decision.strategy
                                        try:
                                            self.update_hud_safe(strategy_name=trig_decision.strategy)
                                        except Exception:
                                            pass
                                        _bet_str = "?"
                                        try:
                                            _bet_str = f"${strategy.progression.get_current_bet():.2f}"
                                        except Exception:
                                            pass
                                        logger.info(f"🔄 Bet-labels swapped: {_old_name} → "
                                                    f"{trig_decision.strategy} | "
                                                    f"progression unchanged (next bet: {_bet_str})")
                                    else:
                                        logger.warning(
                                            f"[Triggers] no cached engine for '{trig_decision.strategy}', "
                                            f"swap skipped")
                                except Exception as _swap_err:
                                    logger.warning(f"[Triggers] swap to {trig_decision.strategy} failed: {_swap_err}")

                        round_number += 1
                        print(f"🎲 Round {round_number}: Placing bet...")
                        initial_balance_this_round = current_balance
                        current_bet_amount = strategy.get_next_bet()
                        self.current_bet_amount = current_bet_amount

                        # Hard stop: get_next_bet() returns 0.0 only when the strategy
                        # refuses to bet — license check failed, max consec losses
                        # reached, or similar guard. Without this stop, total_bet_amount
                        # becomes 0 -> sit-out fires -> keep-alive cycles every 5 spins
                        # forever. Loudly halt so the user sees what happened.
                        if current_bet_amount is None or current_bet_amount <= 0:
                            reason = "(unknown)"
                            try:
                                from core.security.license_manager import get_license_manager
                                lm = get_license_manager()
                                if not getattr(lm, 'is_licensed', True):
                                    reason = "license check failed (lm.is_licensed=False)"
                            except Exception:
                                pass
                            if reason == "(unknown)":
                                # Use the active strategy's own cap for the
                                # diagnostic (None/0 = disabled), matching the
                                # engine gate — not a global config default.
                                ml = getattr(strategy, "max_consec_losses", None) or 0
                                if ml > 0 and strategy.consecutive_losses >= ml:
                                    reason = f"max consecutive losses reached ({strategy.consecutive_losses}/{ml})"
                                else:
                                    reason = "strategy.get_next_bet() returned 0 (no specific reason detected)"
                            print(f"🛑 Bot stopping: get_next_bet()={current_bet_amount}. Reason: {reason}")
                            self.set_status(f"Bot stopped — {reason}")
                            try:
                                self.update_hud_safe(header="🛑 STOPPED", result=reason)
                            except Exception:
                                pass
                            break

                        # Debug: log progression state before placing bet
                        if hasattr(strategy, 'progression'):
                            p = strategy.progression
                            print(f"[ProgDebug] current_bet={p.get_current_bet()}, base_bet={p.base_bet}, "
                                  f"session_high={getattr(p, 'session_high', 'N/A')}, "
                                  f"total_profit={getattr(p, 'total_profit', 'N/A')}, "
                                  f"martingale_lvl={getattr(p, 'martingale_level', 'N/A')}, "
                                  f"last_action={getattr(p, 'last_action', 'N/A')}")
                        progression_type = self.progression_var.get()
                        if is_first_round:
                            print(f"🎯 Starting with base bet: {current_bet_amount}")
                            is_first_round = False
                            # bet_placed_this_round = True
                            print(f"🎯 Next bet amount: {current_bet_amount}")
                        if current_bet_amount > float(self.config.get("max_bet", 100)):
                            print(f"🚫 Bet amount {current_bet_amount} exceeds max allowed {self.config['max_bet']}. Stopping bot.")
                            break
                        # Defer to the ACTIVE strategy's own per-strategy cap
                        # (None/0 = disabled) — the same value the engine's
                        # get_next_bet() gate uses. Do NOT read a global config
                        # default here: that re-imposed a hidden 5-loss stop that
                        # killed parallel/rotation legs even when the cap was off,
                        # which the engine-level fixes alone could not prevent.
                        _mcl = getattr(strategy, "max_consec_losses", None) or 0
                        if _mcl > 0 and strategy.consecutive_losses >= _mcl:
                            print(f"🚨 Too many consecutive losses ({strategy.consecutive_losses}/{_mcl}). Stopping bot.")
                            break
                        bet_labels = strategy.get_bet_labels()

                        # Get custom bet amounts if available
                        bet_amounts = strategy.get_bet_amounts()
                        total_bet_amount = strategy.get_total_bet_amount()

                        # Sit-out guard: when the strategy returns no labels (composite /
                        # pattern_follower waiting for a regime match), skip placement and
                        # do NOT advance progression.
                        # CRITICAL: do NOT set bet_placed_this_round=True here — that flag
                        # is only reset inside the result-recording block (line ~9276) which
                        # requires waiting_for_result, which we're explicitly NOT setting on
                        # sit-out. Setting bet_placed_this_round=True would deadlock the bot
                        # in permanent sit-out mode. We just sleep and continue — the natural
                        # table-state cycle ("PLACE YOUR BETS" -> "BETS ACCEPTED" -> "NEXT
                        # GAME SOON" -> next "PLACE YOUR BETS") moves things forward.
                        if not bet_labels or total_bet_amount <= 0:
                            print(f"🤚 Strategy sitting out (bet_labels={bet_labels}, "
                                  f"total_bet_amount={total_bet_amount}) — progression preserved")
                            self.set_status("Sitting out (no pattern match)")
                            self.update_stats_display(current_bet=0.0, betting_on="—")

                            # Feed winning number to strategy's history (sit-out path),
                            # otherwise pattern detectors never see new spins.
                            try:
                                wn, wc = self.get_latest_winning_number()
                                if wn is not None:
                                    if hasattr(strategy, 'strategy') and hasattr(strategy.strategy, 'record_result'):
                                        import inspect
                                        sig = inspect.signature(strategy.strategy.record_result)
                                        if 'last_number' in sig.parameters:
                                            strategy.strategy.record_result(False, last_number=wn)
                                            print(f"[Sit-out] Fed winning number {wn} {wc or ''} to strategy history")
                                    # Also feed the trigger engine — same fix as the
                                    # keep-alive result handler, since sit-out also
                                    # consumes the OCR cursor without going through
                                    # the main result-recording block.
                                    try:
                                        self._trigger_feed_winning_number(wn)
                                    except Exception:
                                        pass
                                    try:
                                        sp = getattr(self, 'cumulative_net_profit', 0.0)
                                        self.update_hud_safe(
                                            number=f"{wn} {wc or ''}",
                                            result="SIT-OUT",
                                            pnl=f"${sp:.2f}",
                                        )
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.warning(f"[Sit-out] Could not poll/feed winning number: {e}")

                            # ── Keep-alive bet to prevent table inactivity timeout ──
                            # Fires when EITHER condition is met:
                            #   - consecutive_sitouts >= keep_alive_after_n_sitouts (default 5)
                            #   - time since last real bet >= keep_alive_max_idle_minutes (default 3)
                            # The bet does NOT advance progression (a flag tells the
                            # result block to skip progression update).
                            self._consecutive_sitouts = getattr(self, '_consecutive_sitouts', 0) + 1
                            should_ka, ka_reason = self._keep_alive_due()
                            if should_ka:
                                placed = self._place_keep_alive_bet(driver, ka_reason)
                                if placed:
                                    current_bets = placed
                                    bet_placed_this_round = True
                                    waiting_for_result = True
                                    bet_placed_time = time.time()
                                    print(f"⏰ Keep-alive bet placed, waiting for result...")
                                    continue

                            time.sleep(1.5)  # avoid tight-looping while still in PLACE YOUR BETS
                            continue

                        # Safety: if balance is too low for the bet, reset to base bet
                        if current_balance is not None and current_balance > 0 and total_bet_amount > current_balance:
                            base = float(self.config.get("base_bet", 0.1))
                            print(f"⚠️ Bet ${total_bet_amount:.2f} exceeds balance ${current_balance:.2f} — resetting to base bet ${base}")
                            strategy.progression.reset()
                            bet_amounts = strategy.get_bet_amounts()
                            total_bet_amount = strategy.get_total_bet_amount()
                        elif current_balance is not None and current_balance <= 0:
                            print(f"🛑 Balance is ${current_balance:.2f} (zero or negative) — stopping session.")
                            break

                        print(f"💸 Placing bets with custom amounts: {bet_amounts}")
                        print(f"💸 Total bet amount: {total_bet_amount}")
                        
                        self.update_stats_display(
                            current_bet=total_bet_amount,
                            betting_on=", ".join(bet_labels)
                        )
                        
                        current_bets = []
                        
                        # Place bets with custom amounts - OPTIMIZED
                        # Group by chip type to minimize chip selections
                        chip_placement_plan = {}
                        for label in bet_labels:
                            label_amount = bet_amounts.get(label, current_bet_amount)
                            chip_breakdown = get_chip_breakdown(label_amount)
                            print(f"💸 Placing {label_amount} on {label} using {chip_breakdown}")
                            
                            for chip_label, count in chip_breakdown:
                                if chip_label not in chip_placement_plan:
                                    chip_placement_plan[chip_label] = []
                                chip_placement_plan[chip_label].extend([label] * count)
                        
                        # Execute placement plan with reduced pause for speed
                        old_pause = pyautogui.PAUSE
                        pyautogui.PAUSE = 0.074
                        try:
                            for chip_label, labels_to_place in chip_placement_plan.items():
                                driver.select_chip(chip_label)
                                chip_value = float(chip_label.replace('chip_', '').replace('.','0.') if chip_label.startswith('chip_.') else chip_label.replace('chip_', ''))
                                time.sleep(0.05)  # Pause after chip selection to register

                                for label in labels_to_place:
                                    driver.place_bet(label)
                                    current_bets.append({'label': label, 'amount': chip_value})
                        finally:
                            pyautogui.PAUSE = old_pause
                        bet_placed_this_round = True
                        waiting_for_result = True
                        bet_placed_time = time.time() # Record valid bet time
                        self._consecutive_sitouts = 0  # Real bet placed -> reset keep-alive counter
                        self._last_real_bet_at = time.time()  # idle timer reset for time-based keep-alive
                        print("⏳ Bet placed, waiting for result...")
                        if not self.has_placed_first_bet:
                            self.has_placed_first_bet = True
                            print("First bet placed, will start win/loss tracking from next round.")
                    else:
                        print("⚠️ Cannot place bet without a valid balance in config")
                
                # 2. Handle "BETS ACCEPTED" - only a gate, do not reset flags here
                elif "BETS ACCEPTED" in state_upper:
                    # No flag reset here; just a gate for next bet
                    pass

                # 3. Handle "NEXT GAME SOON" - just wait for next round
                elif "NEXT GAME SOON" in state_upper and waiting_for_result:
                    print("🎯 'NEXT GAME SOON' detected, waiting for result...")
                elif "NEXT GAME SOON" in state_upper:
                    pass
                
                # 4. Handle other states - just wait
                elif state_text and state_text != last_table_state:
                    # State changed but not one we care about - mark for scroll reset
                    scroll_reset_needed = True
                
                # Update time remaining
                # Use effective_duration calculated at start of loop (handles pausing)
                # If variable not available (e.g. error), fallback to old logic?
                # effective_duration is defined in the loop scope.
                
                # effective_duration is time running SO FAR.
                # remaining = duration - effective_duration
                remaining_seconds = max(0, int(duration - effective_duration))
                
                minutes = remaining_seconds // 60
                seconds = remaining_seconds % 60
                time_str = f"{minutes:02d}:{seconds:02d}"
                self.update_stats_display(
                    time_remaining=time_str
                )
                self.update_hud_safe(time_rem=time_str)
                
                # 5. Wait before next check (optimized timing)
                time.sleep(0.5)  # Reduced from 1.0 seconds to 0.5 seconds
                
                # Check max loss (Streak Drawdown Protection)
                if self.active_session_loss_limit > 0 and strategy.total_loss >= self.active_session_loss_limit:
                    print("🚑 Max loss reached.")
                    self.log_simulation("🚑 Session stopped: Max loss reached")
                    break

                # After each round, update session high and last bet result
                # Use payout-based profit instead of balance for session high tracking
                current_profit = getattr(self, 'cumulative_net_profit', 0.0)
                if current_profit > self.session_high:
                    self.session_high = current_profit
                    print(f"📈 New session high reached: ${self.session_high:.2f} (payout-based profit)")
                # Determine last bet result (example logic, adjust as needed)
                if hasattr(self, 'latest_winning_number') and hasattr(self, 'bet_history') and self.bet_history:
                    last_bet = self.bet_history[-1]
                    if last_bet.get('result') == 'win':
                        self.last_bet_result = 'win'
                    elif last_bet.get('result') == 'loss':
                        self.last_bet_result = 'loss'

            driver.close()
            
            # Session completion summary
            current_profit = getattr(self, 'cumulative_net_profit', 0.0)
            if session_high_point_reached:
                if (self.config.get("enable_profit_target", False) and 
                    self.active_session_profit_limit > 0 and 
                    current_profit >= self.active_session_profit_limit):
                    print(f"🎯 Session completed: Profit target reached (${current_profit:.2f} - payout-based)")
                    self.log_simulation(f"🎯 Session completed: Profit target reached (${current_profit:.2f} - payout-based)")
                elif self.config.get("enable_win_streak_target", False) and current_win_streak >= self.config.get("win_streak_target", 0):
                    print(f"🔥 Session completed: Win streak target reached ({current_win_streak} wins)")
                    self.log_simulation(f"🔥 Session completed: Win streak target reached ({current_win_streak} wins)")
            else:
                print("✅ Session completed: Time limit reached")
                self.log_simulation("✅ Session completed: Time limit reached")

            # Save session statistics to database
            try:
                session_end_time = datetime.now().isoformat()
                if isinstance(self.session_start_time, datetime):
                     session_start_time = self.session_start_time.isoformat()
                elif self.session_start_time:
                     session_start_time = datetime.fromtimestamp(self.session_start_time).isoformat()
                else:
                     session_start_time = session_end_time
                
                save_session_stats(
                    start_time=session_start_time,
                    end_time=session_end_time,
                    strategy=self.config["strategy"],
                    rounds_played=session_rounds,
                    wins=session_wins,
                    losses=session_losses,
                    profit=current_profit
                )
                print(f"📊 Session statistics saved to database")
                self.log_simulation(f"📊 Session statistics saved to database")
                
                # Update the last graph marker with session duration
                try:
                    if hasattr(self, 'graph_markers') and self.graph_markers:
                        duration_sec = time.time() - self.session_start_timestamp
                        duration_str = f"{int(duration_sec/60)}m"
                        # Improve resolution for short sessions
                        if duration_sec < 60:
                            duration_str = f"{int(duration_sec)}s"
                            
                        # Update the tuple (tuples are immutable, so replace)
                        last_marker = self.graph_markers[-1]
                        # Ensure we don't double-add if run multiple times (defensive)
                        if len(last_marker) == 3:
                            idx, label, strat = last_marker
                            self.graph_markers[-1] = (idx, label, strat, duration_str)
                            print(f"⏱️ Session duration recorded: {duration_str}")
                except Exception as e:
                    print(f"⚠️ Failed to update graph marker duration: {e}")

            except Exception as e:
                print(f"❌ Failed to save session statistics: {e}")
                self.log_simulation(f"❌ Failed to save session statistics: {e}")

            # At the end of the session, automatically update balance using payout table
            new_balance = self.session_start_balance + getattr(self, 'cumulative_net_profit', 0.0)
            
            # Update cumulative offset for the NEXT session so the graph continues
            if hasattr(self, 'cumulative_profit_offset'):
                self.cumulative_profit_offset += getattr(self, 'cumulative_net_profit', 0.0)
                
            with self._config_lock:
                self.config["current_balance"] = new_balance
                save_config(self.config)
            print(f"💾 Updated balance in config to: ${new_balance:.2f} (auto-calculated)")
            # Update stats display
            self.update_stats_display(starting_balance=new_balance, projected_balance=new_balance)

        except Exception as e:
            import traceback
            print("❌ Bot error:", e)
            traceback.print_exc()
            messagebox.showerror("Bot Error", str(e))
            # Don't set bot_running = False here for multiple sessions
            # Only stop if it's a critical error
            if "No table_state region configured" in str(e) or "No balance region configured" in str(e):
                with self._state_lock:
                    self.bot_running = False
        finally:
            self._live_strategy = None  # Clear live strategy reference
            # Only reset the start button if this is the last session or bot was stopped
            if not self.bot_running or self.current_session_num >= self.total_sessions:
                self.start_button.configure(state="normal")

    def winning_number_watcher(self):
        """
        Background thread that watches for the winning number using the table_state region.
        Records all numbers with timestamps but only processes current session numbers.
        """
        import time
        import difflib
        last_saved_number = None
        last_saved_color = None
        place_your_bets_seen = False
        
        def _set_ocr_status(is_active):
            if hasattr(self, 'dash_ocr_status_dot'):
                if is_active:
                    self.dash_ocr_status_dot.configure(text_color="#2ecc71") # Green
                    self.dash_ocr_status_text.configure(text="Scanning...", text_color="#2ecc71")
                else:
                    self.dash_ocr_status_dot.configure(text_color="gray50")
                    self.dash_ocr_status_text.configure(text="Idle", text_color="gray50")

        while self.winning_number_watcher_running:
            try:
                win = self.recorder.browser_win
                if win and "table_state" in self.coordinates:
                    region = self.coordinates["table_state"]
                    # Get current table state text (raw OCR)
                    self.root.after(0, lambda: _set_ocr_status(True))
                    table_state_text = extract_table_state(win, region)
                    self.root.after(0, lambda: _set_ocr_status(False))
                    table_state_upper = table_state_text.upper() if table_state_text else ""
                    # Fuzzy check for PLACE YOUR BETS
                    def fuzzy_place_your_bets(text):
                        # Accept if all of these words are present in any order, even partial
                        required = ["PLACE", "YOUR", "BET"]
                        return all(any(difflib.SequenceMatcher(None, word, t).ratio() > 0.7 for t in text.split()) for word in required)
                    if fuzzy_place_your_bets(table_state_upper):
                        place_your_bets_seen = True
                    
                    number, color = extract_winning_number_from_table_state(win, region)
                    


                    if number is not None and place_your_bets_seen:
                        # 🕐 RECORD ALL NUMBERS with timestamps for complete data collection
                        current_time = time.time()
                        display = f"{number} {color}" if color else str(number)
                        
                        # Always record the number with timestamp for debugging/logging
                        print(f"[Watcher] 📊 Number detected: {display} at {current_time:.2f}")
                        
                        # Check if this number is from current session OR passing recording is enabled
                        # Logic: Save if (In Session) OR (Passive Enabled)
                        
                        is_session_number = (self.session_start_timestamp is not None and current_time >= self.session_start_timestamp)
                        is_passive_mode = getattr(self, 'passive_recording_var', None) and self.passive_recording_var.get()
                        
                        if is_session_number or is_passive_mode:
                            
                            # ✅ Process number
                            if is_session_number:
                                print(f"[Watcher] ✅ Processing current session number: {display}")
                            else:
                                print(f"[Watcher] 💾 Passive recording number: {display}")

                            # Update latest only if it's a session number (to drive betting logic)
                            # OR if we want passive numbers to also trigger 'latest'?
                            # Usually betting logic depends on 'latest_winning_number'. 
                            # If we update it passively, the strategy might just recalculate but NOT bet if 'auto_roulette_running' is False.
                            # So it is safe to update 'latest_winning_number' always, 
                            # BUT 'run_bot' loop checks 'auto_roulette_running' before betting.
                            
                            self.latest_winning_number = number
                            self.latest_winning_color = color
                            self.latest_winning_timestamp = current_time
                            
                            # Update the new Winning Numbers tab
                            if hasattr(self, 'refresh_winning_numbers_tab'):
                                self.root.after(0, self.refresh_winning_numbers_tab)
                            
                            # Save to DB
                            save_winning_number(number, color, source='table_state')

                            # Per-wheel bias surveillance: feed every observed spin
                            # to the bias scout, tagged by the current table/window,
                            # and alert when a physical wheel's bias is confirmed.
                            try:
                                _tbl = (self.recorder.browser_win.title or "").strip()[:60] or "default"
                                self.bias_scout.set_table(_tbl)
                                self.bias_scout.on_spin(number)
                                self._bias_scout_check_alert()
                            except Exception:
                                pass

                            last_saved_number = number
                            last_saved_color = color
                            place_your_bets_seen = False  # Reset flag after saving
                            time.sleep(2)  # Debounce

                        else:
                            # 📝 Number is historical - record it but don't process for betting
                            time_diff = self.session_start_timestamp - current_time
                            print(f"[Watcher] 📝 Historical number recorded (not processed): {display} (appeared {time_diff:.1f}s before session start)")
                            # Don't save to latest_winning_number or trigger betting logic
                            # But we keep the timestamp for debugging purposes
                            place_your_bets_seen = False  # Reset flag to continue watching
                            time.sleep(0.5)  # Shorter debounce for historical numbers
                time.sleep(0.2)
            except Exception as e:
                print(f"[Watcher] Error: {e}")
                time.sleep(1)

    @property
    def bias_scout(self):
        """Lazy, persistent per-wheel BiasScoutManager. Accumulates every observed
        spin per physical table across restarts to detect a biased live wheel —
        the only honest roulette edge. Sits out everywhere until a wheel's bias
        is statistically confirmed."""
        mgr = getattr(self, "_bias_scout_mgr", None)
        if mgr is None:
            from core.profit.scout_manager import BiasScoutManager
            mgr = BiasScoutManager()
            self._bias_scout_mgr = mgr
        return mgr

    def _bias_scout_check_alert(self):
        """Log + broadcast a one-line alert when a NEW wheel/pocket bias is
        confirmed (throttled so it fires on change, not every spin)."""
        try:
            opp = self.bias_scout.opportunity()
            key = (opp["wheel_id"], opp["bets"][0]["label"]) if opp else None
            if key and key != getattr(self, "_last_bias_alert", None):
                self._last_bias_alert = key
                bankroll = float(self.config.get("current_balance", 0) or 0)
                line = self.bias_scout.alert_line(bankroll)
                if line:
                    try:
                        self.log_to_dashboard(line)
                    except Exception:
                        pass
                    if hasattr(self, "_broadcast_alert"):
                        try:
                            self._broadcast_alert(line)
                        except Exception:
                            pass
            elif not opp:
                self._last_bias_alert = None
        except Exception:
            pass

    # ── Result-feed tap: one WebSocket → every table's spins, no OCR ───────────
    def _feed_tap_log(self, msg):
        """Thread-safe bridge from the capture thread to the dashboard log."""
        try:
            self.root.after(0, lambda: self.log_to_dashboard(str(msg)))
        except Exception:
            print(msg)

    @property
    def bias_feed_tap(self):
        """Lazy FeedTap that attaches to the live Chrome over CDP (:9222) and
        streams EVERY table's winning numbers straight into the bias scout — no
        OCR, many wheels from one socket. Catches far more tables than tiled
        screen-scraping and never injects OCR-misread phantom bias."""
        tap = getattr(self, "_bias_feed_tap_obj", None)
        if tap is None:
            from core.profit.feed_tap import FeedTap

            def _on_result(table_id, number):
                try:
                    self.bias_scout.on_spin(number, table_id=table_id)
                    self._bias_scout_check_alert()
                except Exception:
                    pass

            tap = FeedTap(on_result=_on_result, provider="auto",
                          log=self._feed_tap_log)
            self._bias_feed_tap_obj = tap
        return tap

    def start_bias_feed_tap(self):
        """Start tapping the live result feed for all visible tables."""
        try:
            self.bias_feed_tap.start()
            self.log_to_dashboard("[FeedTap] Tapping live result WebSocket for all "
                                  "tables (reload the lobby if nothing appears).")
        except Exception as e:
            self.log_to_dashboard(f"[FeedTap] Failed to start: {e}")

    def stop_bias_feed_tap(self):
        try:
            self.bias_feed_tap.stop()
            self.log_to_dashboard("[FeedTap] Stopped.")
        except Exception:
            pass

    # ── Standalone collector: tap the provider feed directly (no CDP/browser) ──
    def _feed_collector_config_path(self):
        import os
        return os.path.join(os.path.expanduser("~"), ".spinedge", "config",
                            "feed_endpoints.json")

    @property
    def bias_collector(self):
        """Lazy FeedCollector that connects DIRECTLY to the provider's result
        WebSocket / HTTP feed (the way trackers do — no browser, no CDP) and
        streams every table's spins into the in-process bias scout. Endpoints +
        auth are read from ~/.spinedge/config/feed_endpoints.json (an annotated
        template is written there on first use; fill it from one capture)."""
        col = getattr(self, "_bias_collector_obj", None)
        if col is None:
            import os
            from core.profit.feed_collector import (FeedCollector, load_config,
                                                    write_example_config)
            path = self._feed_collector_config_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                write_example_config(path)
                self.log_to_dashboard(f"[Collector] Wrote config template -> {path}. "
                                      "Fill it from one DevTools capture, then start.")
            try:
                cfg = load_config(path)
            except Exception as e:
                self.log_to_dashboard(f"[Collector] Bad config: {e}")
                cfg = {"sources": []}

            def _on_result(table_id, number):
                try:
                    self.bias_scout.on_spin(number, table_id=table_id)
                    self._bias_scout_check_alert()
                except Exception:
                    pass

            col = FeedCollector(cfg, on_result=_on_result, log=self._feed_tap_log)
            self._bias_collector_obj = col
        return col

    def start_bias_collector(self):
        """Connect to the configured provider feeds and record all tables."""
        try:
            self.bias_collector.start()
            self.log_to_dashboard("[Collector] " + self.bias_collector.status())
        except Exception as e:
            self.log_to_dashboard(f"[Collector] Failed to start: {e}")

    def stop_bias_collector(self):
        try:
            self.bias_collector.stop()
            self.log_to_dashboard("[Collector] Stopped.")
        except Exception:
            pass

    def get_latest_winning_number(self):
        """
        Get the latest winning number for win/loss determination.
        Returns (number, color) or (None, None) if no new number.
        Uses timestamp-based filtering to ensure only current session numbers are processed.
        Allows consecutive SAME numbers if they have a newer timestamp (meaning detected after 'PLACE BETS').
        """
        # Initialize last_processed_winning_timestamp if not exists
        if not hasattr(self, 'last_processed_winning_timestamp'):
            self.last_processed_winning_timestamp = 0
            
        # Check if we have a number and if it's NEW based on timestamp
        has_new_data = False
        
        if self.latest_winning_number is not None and self.latest_winning_timestamp is not None:
            # Check if this timestamp is newer than what we last processed
            if self.latest_winning_timestamp > getattr(self, 'last_processed_winning_timestamp', 0):
                has_new_data = True
        
        if has_new_data:
            # 🔍 TIMESTAMP VALIDATION: Ensure number is from current session
            if (self.session_start_timestamp is not None):
                if self.latest_winning_timestamp < self.session_start_timestamp:
                    print(f"[get_latest_winning_number] ❌ Historical number filtered: {self.latest_winning_number} (detected at {self.latest_winning_timestamp:.2f}, session started at {self.session_start_timestamp:.2f})")
                    # Clear the number to prevent processing
                    self.latest_winning_number = None
                    self.latest_winning_color = None
                    self.latest_winning_timestamp = None
                    return None, None
                else:
                    time_since_session = self.latest_winning_timestamp - self.session_start_timestamp
                    print(f"[get_latest_winning_number] ✅ Current session number: {self.latest_winning_number} (detected {time_since_session:.1f}s after session start)")
            
            # ✅ Number is valid - process it
            self.last_processed_winning_number = self.latest_winning_number
            self.last_processed_winning_timestamp = self.latest_winning_timestamp
            print(f"[get_latest_winning_number] ✅ Returning NEW (timestamp validated): {self.latest_winning_number} {self.latest_winning_color or ''}")
            return self.latest_winning_number, self.latest_winning_color
        
        print(f"[get_latest_winning_number] No new number (Timestamp check: latest={getattr(self, 'latest_winning_timestamp', 'None')} <= last_processed={getattr(self, 'last_processed_winning_timestamp', 0)}). Returning None.")
        return None, None

    def reset_session_timestamp(self):
        """
        Reset the session start timestamp to the current time.
        This ensures that only winning numbers from the current session are processed.
        """
        import time
        self.session_start_timestamp = time.time()
        print(f"🕐 Session timestamp reset: {self.session_start_timestamp}")
        self.log_simulation(f"🕐 Session timestamp reset: {self.session_start_timestamp}")
        
        # Also clear any existing winning numbers and timestamps to prevent processing historical data
        if self.latest_winning_number is not None:
            print(f"🧹 Clearing previous winning number {self.latest_winning_number} to prevent historical data processing")
            self.latest_winning_number = None
            self.latest_winning_color = None
            self.latest_winning_timestamp = None
            self.last_processed_winning_number = None

    def run_simulation(self):
        try:
            strategy = StrategyEngine(
                strategy_name=self.config["strategy"],
                base_bet=float(self.config["base_bet"]),
                max_loss=float(self.config["max_loss"]),
                progression_type=self.config.get("progression_type", "flat"),
                custom_strategies=self.custom_strategies,
                observation_trigger=int(self.config.get("observation_trigger", 0))
            )

            sim_balance = float(self.config.get("balance", 1000.0))
            self.log_simulation("🔍 Starting strategy simulation...\n")
            session_seconds = int(self.config["session_duration_minutes"]) * 60
            end_time = time.time() + session_seconds
            round_num = 0

            while time.time() < end_time and self.simulation_running:
                amount = strategy.get_next_bet()
                round_num += 1
                msg = f"🧪 Round {round_num}: Bet {amount:.2f} on {self.config['bet_color']}"
                self.log_simulation(msg)

                simulated_win = round_num % 2 == 0
                self.log_simulation(f"   → {'Win' if simulated_win else 'Loss'}")
                
                if simulated_win:
                    sim_balance += amount # simplified 1:1 payout mock
                else:
                    sim_balance -= amount

                strategy.record_result(simulated_win, current_balance=sim_balance)

                if strategy.total_loss >= self.config["max_loss"]:
                    self.log_simulation("🚑 Simulation stopped: Max loss reached.")
                    break

                time.sleep(1)

            self.log_simulation(f"✅ Simulation complete. Total rounds: {round_num}")

        except Exception as e:
            messagebox.showerror("Simulation Error", str(e))
        finally:
            self.simulation_running = False
            self.simulate_button.configure(state="normal")
            self.stop_sim_button.configure(state="disabled")

    def cleanup_debug_screens(self):
        """Manually trigger debug screen cleanup"""
        try:
            from core.ocr_utils import cleanup_debug_screens
            cleanup_debug_screens()
            self.log_simulation("🧹 Manual debug screen cleanup completed")
        except Exception as e:
            self.log_simulation(f"❌ Debug cleanup error: {e}")

    def clear_log(self):
        """Clear the activity log"""
        self.clear_activity_log()
        self.log_message("📝 Log cleared", "INFO")

    def stop_simulation(self):
        if self.simulation_running:
            self.simulation_running = False
            self.log_message("⛔ Stopping simulation...", "INFO")

    def log_simulation(self, message):
        """Redirect legacy log calls to new logger"""
        self.log_message(message, "INFO")

    def handle_table_reset(self):
        """Handler for Table Reset button: calls reset_scroll_keyboard on the browser automation."""
        try:
            from automation.roulette_browser import RouletteBrowserAutomation
            if not self.recorder.browser_win:
                messagebox.showerror("Error", "No browser window selected.")
                return
            driver = RouletteBrowserAutomation(
                coordinates=self.config["coordinates"],
                window_title=self.recorder.browser_win.title
            )
            driver.reset_scroll_keyboard()
            messagebox.showinfo("Table Reset", "Table reset (keyboard scroll) triggered successfully.")
        except Exception as e:
            messagebox.showerror("Table Reset Failed", f"Error: {e}")



    def refresh_history_listbox(self):
        self.history_listbox.delete(0, tk.END)
        for entry in get_recent_winning_numbers(20):
            display = f"{entry['timestamp'][:19]} | {entry['number']} {entry['color'] or ''}".strip()
            self.history_listbox.insert(tk.END, display)
        # Schedule next refresh
        self.root.after(5000, self.refresh_history_listbox)

    def on_progression_changed(self, event=None):
        # Hide all special fields first
        self.dalembert_step_label.grid_forget()
        self.dalembert_step_entry.grid_forget()
        self.custom_sequence_label.grid_forget()
        self.custom_sequence_entry.grid_forget()
        if self.dynamic_rules_frame is not None:
            self.dynamic_rules_frame.grid_forget()
        prog = self.progression_var.get()
        if prog == "dalembert":
            self.dalembert_step_label.grid(row=3, column=0, sticky="w", pady=2)
            self.dalembert_step_entry.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=2)
        elif prog == "custom_sequence":
            self.custom_sequence_label.grid(row=3, column=0, sticky="w", pady=2)
            self.custom_sequence_entry.grid(row=3, column=1, sticky="ew", padx=(10, 0), pady=2)
        elif prog == "dynamic" and self.dynamic_rules_frame is not None:
            self.dynamic_rules_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
            self.dalembert_step_label.grid(row=4, column=0, sticky="w", pady=2)
            self.dalembert_step_entry.grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=2)
            self.custom_sequence_label.grid(row=5, column=0, sticky="w", pady=2)
            self.custom_sequence_entry.grid(row=5, column=1, sticky="ew", padx=(10, 0), pady=2)

    def _build_dynamic_rule_dialog(self, parent_window=None):
        """Shared CTk dialog for adding a dynamic rule. Returns the rule dict or None."""
        from gui.theme import (BG_DARK, BG_CARD, BG_INPUT, BORDER_DEFAULT, BORDER_SUBTLE,
                                TEXT_PRIMARY, TEXT_SECONDARY, GOLD, GOLD_HOVER, FONT_BODY,
                                FONT_BODY_BOLD, FONT_SMALL, CORNER_RADIUS, CORNER_SMALL)

        dialog = ctk.CTkToplevel(parent_window or self.root)
        dialog.title("Add Dynamic Rule")
        dialog.grab_set()
        dialog.geometry("460x420")
        dialog.resizable(False, False)
        dialog.configure(fg_color=BG_DARK)

        _lbl = dict(font=FONT_SMALL, text_color=TEXT_SECONDARY)
        _lbl_bold = dict(font=FONT_BODY_BOLD, text_color=TEXT_PRIMARY)
        _combo_kw = dict(fg_color=BG_INPUT, border_color=BORDER_DEFAULT, border_width=1,
                         corner_radius=CORNER_SMALL, font=FONT_BODY, text_color=TEXT_PRIMARY,
                         button_color=BG_CARD, button_hover_color="#3F3F46", dropdown_fg_color=BG_CARD,
                         dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color="#3F3F46")
        _entry_kw = dict(fg_color=BG_INPUT, border_color=BORDER_DEFAULT, border_width=1,
                         corner_radius=CORNER_SMALL, font=FONT_BODY, text_color=TEXT_PRIMARY)

        content = ctk.CTkFrame(dialog, fg_color=BG_DARK)
        content.pack(fill="both", expand=True, padx=20, pady=16)

        # Event
        ctk.CTkLabel(content, text="Event", **_lbl_bold).pack(anchor="w", pady=(0, 4))
        event_var = tk.StringVar(value="win")
        ctk.CTkComboBox(content, variable=event_var, values=["win", "loss", "session_high"],
                        state="readonly", **_combo_kw).pack(fill="x", pady=(0, 12))

        # Action
        ctk.CTkLabel(content, text="Action", **_lbl_bold).pack(anchor="w", pady=(0, 4))
        action_var = tk.StringVar(value="martingale")
        action_combo = ctk.CTkComboBox(content, variable=action_var,
                        values=["martingale", "flat", "reset_to_base", "custom_sequence",
                                "dalembert", "step_up", "step_down", "keep"],
                        state="readonly", **_combo_kw)
        action_combo.pack(fill="x", pady=(0, 12))

        # Condition
        ctk.CTkLabel(content, text="Condition (optional)", **_lbl_bold).pack(anchor="w", pady=(0, 4))
        condition_var = tk.StringVar(value="")
        ctk.CTkComboBox(content, variable=condition_var,
                        values=["", "profit_below_session_high", "profit_at_or_above_session_high"],
                        state="readonly", **_combo_kw).pack(fill="x", pady=(0, 12))

        # Parameters frame (shown/hidden based on action)
        params_frame = ctk.CTkFrame(content, fg_color=BG_CARD, corner_radius=8, border_width=1, border_color=BORDER_SUBTLE)
        params_label = ctk.CTkLabel(content, text="Parameters", **_lbl_bold)

        # Custom sequence row
        seq_row = ctk.CTkFrame(params_frame, fg_color="transparent")
        ctk.CTkLabel(seq_row, text="Sequence:", width=90, **_lbl).pack(side="left")
        seq_var = tk.StringVar(value="1,2,3,4,5")
        ctk.CTkEntry(seq_row, textvariable=seq_var, **_entry_kw).pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Step type row
        step_type_row = ctk.CTkFrame(params_frame, fg_color="transparent")
        ctk.CTkLabel(step_type_row, text="Step Type:", width=90, **_lbl).pack(side="left")
        step_type_var = tk.StringVar(value="Base Bet Multiplier")
        ctk.CTkComboBox(step_type_row, variable=step_type_var,
                        values=["Base Bet Multiplier", "Custom Unit ($)"],
                        state="readonly", **_combo_kw).pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Step value row
        step_val_row = ctk.CTkFrame(params_frame, fg_color="transparent")
        ctk.CTkLabel(step_val_row, text="Step Value:", width=90, **_lbl).pack(side="left")
        step_var = tk.StringVar(value="1.0")
        ctk.CTkEntry(step_val_row, textvariable=step_var, width=80, **_entry_kw).pack(side="left")

        def _update_params(*_):
            for w in (seq_row, step_type_row, step_val_row):
                w.pack_forget()
            action = action_var.get()
            if action == "custom_sequence":
                params_label.pack(anchor="w", pady=(0, 4))
                params_frame.pack(fill="x", pady=(0, 12))
                seq_row.pack(fill="x", padx=8, pady=6)
            elif action in ("dalembert", "step_up", "step_down"):
                params_label.pack(anchor="w", pady=(0, 4))
                params_frame.pack(fill="x", pady=(0, 12))
                step_type_row.pack(fill="x", padx=8, pady=4)
                step_val_row.pack(fill="x", padx=8, pady=(0, 6))
            else:
                params_label.pack_forget()
                params_frame.pack_forget()

        action_combo.configure(command=_update_params)
        _update_params()

        # Buttons
        btn_frame = ctk.CTkFrame(content, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(8, 0))

        result = {}

        def _on_ok():
            rule = {"on": event_var.get(), "action": action_var.get()}
            cond = condition_var.get()
            if cond:
                rule["condition"] = cond
            if action_var.get() == "custom_sequence":
                try:
                    rule["sequence"] = [int(x.strip()) for x in seq_var.get().split(",") if x.strip()]
                except Exception:
                    rule["sequence"] = [1]
            if action_var.get() in ("dalembert", "step_up", "step_down"):
                if step_type_var.get() == "Base Bet Multiplier":
                    try:
                        mult = float(step_var.get())
                        if mult == 1.0:
                            rule["step"] = "base_bet"
                        else:
                            rule["step"] = f"base_bet_{mult}x"
                    except ValueError:
                        rule["step"] = "base_bet"
                else:
                    try:
                        rule["step"] = float(step_var.get())
                    except ValueError:
                        rule["step"] = 1.0
            result["rule"] = rule
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Add Rule", width=100, fg_color=GOLD, hover_color=GOLD_HOVER,
                       text_color="#0F1117", font=FONT_BODY_BOLD, corner_radius=CORNER_SMALL,
                       command=_on_ok).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_frame, text="Cancel", width=80, fg_color=BG_CARD, hover_color="#3F3F46",
                       text_color=TEXT_SECONDARY, font=FONT_BODY, corner_radius=CORNER_SMALL,
                       command=dialog.destroy).pack(side="left")

        dialog.wait_window()
        return result.get("rule")

    def add_dynamic_rule(self):
        rule = self._build_dynamic_rule_dialog()
        if rule:
            self.dynamic_rules.append(rule)
            self.config["dynamic_rules"] = self.dynamic_rules
            save_config(self.config)
            self.refresh_dynamic_rules_listbox()

    def remove_dynamic_rule(self):
        # Remove the last rule (no selection in CTkTextbox)
        if self.dynamic_rules:
            self.dynamic_rules.pop()
            self.config["dynamic_rules"] = self.dynamic_rules
            save_config(self.config)
            self.refresh_dynamic_rules_listbox()

    def refresh_dynamic_rules_listbox(self):
        if self.dynamic_rules_listbox is None:
            return
        self.dynamic_rules_listbox.configure(state="normal")
        self.dynamic_rules_listbox.delete("1.0", "end")
        for rule in self.dynamic_rules:
            desc = f"On {rule['on']}: {rule['action']}"
            if rule.get('condition'):
                desc += f" [if {rule['condition']}]"
            if rule.get('action') == 'custom_sequence':
                desc += f" {rule.get('sequence', [])}"
            if rule.get('action') == 'dalembert':
                desc += f" (step={rule.get('step', 1)})"
            self.dynamic_rules_listbox.insert("end", desc + "\n")
        self.dynamic_rules_listbox.configure(state="disabled")

    def view_dynamic_rules_dialog(self, rules_var):
        """Displays the parsed dynamic rules from the Strategy Builder string variable in a clean list."""
        from gui.theme import (BG_DARK, BG_CARD, BG_INPUT, BORDER_SUBTLE, BORDER_DEFAULT,
                                TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED, GOLD, GOLD_HOVER,
                                DANGER, DANGER_HOVER, FONT_BODY, FONT_BODY_BOLD, FONT_SMALL,
                                FONT_MONO_SMALL, CORNER_SMALL)

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("View Dynamic Rules")
        dialog.geometry("480x340")
        dialog.grab_set()
        dialog.resizable(False, True)
        dialog.configure(fg_color=BG_DARK)

        ctk.CTkLabel(dialog, text="Current Dynamic Rules", font=FONT_BODY_BOLD,
                      text_color=TEXT_PRIMARY).pack(pady=(16, 8), padx=16, anchor="w")

        # Rules display area
        rules_text = ctk.CTkTextbox(dialog, font=FONT_MONO_SMALL, fg_color=BG_CARD,
                                     border_color=BORDER_SUBTLE, border_width=1,
                                     corner_radius=8, text_color=TEXT_PRIMARY, wrap="word")
        rules_text.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        rules_str = rules_var.get().strip()
        if not rules_str:
            rules_text.insert("end", "No rules currently added.")
        else:
            rules_list = rules_str.split(";")
            for idx, rule_str in enumerate(rules_list, 1):
                parts = rule_str.split("|")
                main_def = parts[0]
                if ":" in main_def:
                    event, action = main_def.split(":", 1)
                    display = f"{idx}. On '{event}': {action.upper()}"
                    for param in parts[1:]:
                        if "=" in param:
                            k, v = param.split("=", 1)
                            display += f"  [{k}: {v}]"
                    rules_text.insert("end", display + "\n")
                else:
                    rules_text.insert("end", f"{idx}. [Invalid] {rule_str}\n")
        rules_text.configure(state="disabled")

        # Buttons
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(0, 16))

        def clear_rules():
            rules_var.set("")
            dialog.destroy()

        ctk.CTkButton(btn_frame, text="Clear All", width=90, fg_color=DANGER, hover_color=DANGER_HOVER,
                       text_color="#FFFFFF", font=FONT_BODY_BOLD, corner_radius=CORNER_SMALL,
                       command=clear_rules).pack(side="left")
        ctk.CTkButton(btn_frame, text="Close", width=70, fg_color=BG_CARD, hover_color="#3F3F46",
                       text_color=TEXT_SECONDARY, font=FONT_BODY, corner_radius=CORNER_SMALL,
                       command=dialog.destroy).pack(side="right")

    def get_progression_params(self):
        prog = self.progression_var.get()
        params = {}
        if prog == "dalembert":
            params['dalembert_step'] = self.dalembert_step_var.get()
        elif prog == "custom_sequence":
            seq_str = self.custom_sequence_var.get()
            try:
                params['custom_sequence'] = [int(x.strip()) for x in seq_str.split(',') if x.strip()]
            except Exception:
                params['custom_sequence'] = [1]
        elif prog == "dynamic":
            # Apply the same rule filter the backtest runner uses so live + backtest
            # behave identically when session_ext_at_high is on. An unconditional
            # win:reset_to_base contradicts the "extend till session high" intent
            # (it cancels the recovery escalation), so we drop it before handing
            # rules to StrategyEngine.
            _rules = list(self.dynamic_rules or [])
            try:
                _ext_at_high = bool(self.session_ext_at_high_var.get()) if hasattr(self, 'session_ext_at_high_var') else bool(self.config.get('session_ext_at_high', False))
                if _ext_at_high:
                    from core.backtesting_runner import filter_session_extension_conflicts
                    _rules = filter_session_extension_conflicts(_rules, True)
            except Exception:
                pass
            params['dynamic_rules'] = _rules
        return params

    def request_new_bet_type(self):
        new_type = self.simple_input("Enter the bet type you want to request:", "Request New Bet Type")
        if new_type:
            messagebox.showinfo("Request Sent", f"Your request for bet type '{new_type}' has been noted. Please contact support or check for updates in future versions.")


    def open_bet_type_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Select Bet Type")
        dialog.geometry("400x500")
        dialog.grab_set()
        dialog.resizable(False, False)
        # Search box
        search_var = tk.StringVar()
        search_entry = ttk.Entry(dialog, textvariable=search_var, width=40)
        search_entry.pack(pady=8, padx=8)
        # Listbox with scrollbar
        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=8, pady=8)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        listbox = tk.Listbox(list_frame, width=40, height=20, yscrollcommand=scrollbar.set, selectbackground="#ffe680", font=("Consolas", 11))
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=listbox.yview)
        # Grouped bet types
        grouped = group_bet_types(VALID_BET_TYPES)
        chip_labels = list(CHIP_DENOMINATIONS.keys())
        self._bet_type_listbox_map = []  # (is_header, value)
        def populate_listbox(filter_text=""):
            listbox.delete(0, tk.END)
            self._bet_type_listbox_map.clear()
            # Add chips group at the top
            filtered_chips = [chip for chip in chip_labels if filter_text.lower() in chip.lower() or filter_text.lower() in "chips"]
            if filtered_chips:
                listbox.insert(tk.END, f"--- Chips ---")
                self._bet_type_listbox_map.append((True, "Chips"))
                for chip in filtered_chips:
                    listbox.insert(tk.END, chip)
                    self._bet_type_listbox_map.append((False, chip))
            for group, items in grouped.items():
                filtered = [bt for bt in items if filter_text.lower() in bt.lower() or filter_text.lower() in group.lower()]
                if not filtered:
                    continue
                # Insert group header
                listbox.insert(tk.END, f"--- {group} ---")
                self._bet_type_listbox_map.append((True, group))
                for bt in filtered:
                    listbox.insert(tk.END, bt)
                    self._bet_type_listbox_map.append((False, bt))
        populate_listbox()
        def on_search(*_):
            populate_listbox(search_var.get())
        search_var.trace_add("write", on_search)
        # OK/Cancel buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=8)
        result = {"selected": None}
        def on_ok():
            sel = listbox.curselection()
            if not sel:
                dialog.destroy()
                return
            idx = sel[0]
            is_header, value = self._bet_type_listbox_map[idx]
            if is_header:
                return  # Don't allow selecting headers
            result["selected"] = value
            dialog.destroy()
        def on_cancel():
            dialog.destroy()
        ok_btn = ttk.Button(btn_frame, text="OK", command=on_ok)
        ok_btn.pack(side="left", padx=5)
        cancel_btn = ttk.Button(btn_frame, text="Cancel", command=on_cancel)
        cancel_btn.pack(side="left", padx=5)
        # Double-click or Enter to select
        def on_double_click(event):
            on_ok()
        listbox.bind("<Double-Button-1>", on_double_click)
        listbox.bind("<Return>", on_double_click)
        search_entry.focus_set()
        dialog.wait_window()
        if result["selected"]:
            self.bet_type_var.set(result["selected"])
            self.bet_type_button.configure(text=result["selected"])

    def add_custom_region(self):
        label = self.custom_region_entry.get().strip()
        if not label:
            messagebox.showerror("Error", "Please enter a custom region label.")
            return
        if label.lower() in ("balance", "table_state"):
            messagebox.showerror("Error", "This label is reserved for required regions.")
            return
        if not hasattr(self, 'custom_regions'):
            self.custom_regions = []
        if label in self.custom_regions:
            messagebox.showinfo("Info", f"Region '{label}' already exists.")
            return
        self.custom_regions.append(label)
        # Persist custom regions in config
        self.config["custom_regions"] = self.custom_regions
        save_config(self.config)
        # Update dropdown
        region_labels = ["balance*", "table_state*"] + self.custom_regions
        self.region_label_dropdown["values"] = region_labels
        self.region_label_dropdown.set(label)
        self.custom_region_entry.delete(0, tk.END)

        # ===== RECORDED REGIONS & COORDINATES DISPLAY =====
        display_frame = ttk.LabelFrame(frame, text="Recorded Regions & Coordinates", padding="8")
        display_frame.grid(row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        # Remove old coord_display Text widget
        # self.coord_display = tk.Text(display_frame, width=50, height=6, wrap="word")
        # self.coord_display.pack(fill="x", pady=(0, 5))
        # display_frame.columnconfigure(0, weight=1)
        # New: scrollable frame for interactive region/coordinate list
        self.coord_list_canvas = tk.Canvas(display_frame, height=180)
        self.coord_list_scrollbar = ttk.Scrollbar(display_frame, orient="vertical", command=self.coord_list_canvas.yview)
        self.coord_list_inner = ttk.Frame(self.coord_list_canvas)
        self.coord_list_inner.bind(
            "<Configure>", lambda e: self.coord_list_canvas.configure(scrollregion=self.coord_list_canvas.bbox("all")))
        self.coord_list_canvas.create_window((0, 0), window=self.coord_list_inner, anchor="nw")
        self.coord_list_canvas.configure(yscrollcommand=self.coord_list_scrollbar.set)
        self.coord_list_canvas.pack(side="left", fill="both", expand=True)
        self.coord_list_scrollbar.pack(side="right", fill="y")
        display_frame.columnconfigure(0, weight=1)
        self.update_coord_list_display()

    def update_coord_list_display(self):
        # Clear previous widgets
        for widget in self.coord_list_inner.winfo_children():
            widget.destroy()
        row = 0
        for label, pos in self.coordinates.items():
            if all(k in pos for k in ("x1_pct", "y1_pct", "x2_pct", "y2_pct")):
                # It's a region
                x1, y1 = pos["x1_pct"] * 100, pos["y1_pct"] * 100
                x2, y2 = pos["x2_pct"] * 100, pos["y2_pct"] * 100
                desc = f"{label} (Region): ({x1:.1f}%, {y1:.1f}%) → ({x2:.1f}%, {y2:.1f}%)"
                is_region = True
            elif "x_pct" in pos and "y_pct" in pos:
                x, y = pos["x_pct"] * 100, pos["y_pct"] * 100
                desc = f"{label} (Coordinate): ({x:.1f}%, {y:.1f}%)"
                is_region = False
            else:
                desc = f"{label}: ❓ Unknown format"
                is_region = False
            lbl = ttk.Label(self.coord_list_inner, text=desc, anchor="w")
            lbl.grid(row=row, column=0, sticky="w", padx=(2, 5), pady=2)
            # Highlight on hover
            def make_highlight(label=label):
                def on_enter(_):
                    if self.recorder and self.recorder.browser_win:
                        self.recorder.flash_window_border()
                return on_enter
            lbl.bind("<Enter>", make_highlight(label))
            # Edit button
            edit_btn = ttk.Button(self.coord_list_inner, text="Edit", width=7, command=lambda l=label, r=is_region: self.edit_coordinate(l, r))
            edit_btn.grid(row=row, column=1, padx=2, pady=2)
            edit_btn_ttp = ttk.Label(self.coord_list_inner, text="✎", foreground="#888")
            edit_btn_ttp.grid_remove()  # Placeholder for tooltip
            edit_btn.bind("<Enter>", lambda e, b=edit_btn: b.configure(cursor="hand2"))
            edit_btn.bind("<Leave>", lambda e, b=edit_btn: b.configure(cursor=""))
            # Delete button
            btn = ttk.Button(self.coord_list_inner, text="Delete", width=7, command=lambda l=label: self.delete_coordinate(l))
            btn.grid(row=row, column=2, padx=2, pady=2)
            btn_ttp = ttk.Label(self.coord_list_inner, text="🗑", foreground="#888")
            btn_ttp.grid_remove()  # Placeholder for tooltip
            btn.bind("<Enter>", lambda e, b=btn: b.configure(cursor="hand2"))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(cursor=""))
            row += 1

    def delete_coordinate(self, label):
        # Save for undo
        if not hasattr(self, '_coord_undo_stack'):
            self._coord_undo_stack = []
        self._coord_undo_stack.append({'coordinates': self.coordinates.copy()})
        if label in self.coordinates:
            del self.coordinates[label]
            self.config["coordinates"] = self.coordinates
            save_config(self.config)
            self.update_coord_list_display()
            self.update_label_selector()

    def edit_coordinate(self, label, is_region):
        # Save for undo
        if not hasattr(self, '_coord_undo_stack'):
            self._coord_undo_stack = []
        self._coord_undo_stack.append({'coordinates': self.coordinates.copy()})
        import tkinter.simpledialog
        import tkinter as tk
        from tkinter import simpledialog, Toplevel, StringVar, ttk
        pos = self.coordinates[label]
        dialog = Toplevel(self.root)
        dialog.title(f"Edit {'Region' if is_region else 'Coordinate'}: {label}")
        dialog.grab_set()
        dialog.resizable(False, False)
        # Label edit
        tk.Label(dialog, text="Label:").grid(row=0, column=0, sticky="w", padx=10, pady=5)
        label_var = StringVar(value=label)
        label_entry = ttk.Entry(dialog, textvariable=label_var, width=30)
        label_entry.grid(row=0, column=1, padx=10, pady=5)
        # For coordinates, allow editing values
        if not is_region:
            tk.Label(dialog, text="X (%):").grid(row=1, column=0, sticky="w", padx=10, pady=5)
            x_var = StringVar(value=f"{pos['x_pct']*100:.2f}")
            x_entry = ttk.Entry(dialog, textvariable=x_var, width=10)
            x_entry.grid(row=1, column=1, padx=10, pady=5)
            tk.Label(dialog, text="Y (%):").grid(row=2, column=0, sticky="w", padx=10, pady=5)
            y_var = StringVar(value=f"{pos['y_pct']*100:.2f}")
            y_entry = ttk.Entry(dialog, textvariable=y_var, width=10)
            y_entry.grid(row=2, column=1, padx=10, pady=5)
        # OK/Cancel
        def on_ok():
            new_label = label_var.get().strip()
            if not new_label:
                dialog.destroy()
                return
            # Prevent duplicate labels
            if new_label != label and new_label in self.coordinates:
                messagebox.showerror("Error", f"Label '{new_label}' already exists.")
                return
            # Update label and values
            if is_region:
                self.coordinates[new_label] = pos.copy()
            else:
                try:
                    x = float(x_var.get()) / 100.0
                    y = float(y_var.get()) / 100.0
                except Exception:
                    messagebox.showerror("Error", "Invalid coordinate values.")
                    return
                self.coordinates[new_label] = {"x_pct": x, "y_pct": y}
            if new_label != label:
                del self.coordinates[label]
            self.config["coordinates"] = self.coordinates
            save_config(self.config)
            self.update_coord_list_display()
            self.update_label_selector()
            dialog.destroy()
        def on_cancel():
            dialog.destroy()
        ok_btn = ttk.Button(dialog, text="OK", command=on_ok)
        ok_btn.grid(row=10, column=0, padx=10, pady=10)
        cancel_btn = ttk.Button(dialog, text="Cancel", command=on_cancel)
        cancel_btn.grid(row=10, column=1, padx=10, pady=10)
        dialog.wait_window()

    def set_status(self, msg):
        self.status_var.set(msg)

    def log_message(self, msg, tag=None):
        """Log a message to the activity log with tagging"""
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {msg}\n"
            
            # Determine tag based on content if not provided
            if not tag:
                tag = "INFO"
                if "WIN" in msg.upper() or "PROFIT" in msg.upper():
                    tag = "WIN"
                elif "LOSS" in msg.upper():
                    tag = "LOSS"
                elif "ERROR" in msg.upper() or "EXCEPTION" in msg.upper():
                    tag = "ERROR"
            
            # Add to activity log if it exists
            if hasattr(self, 'activity_log'):
                self.activity_log.configure(state="normal")
                # Insert with tag
                self.activity_log.insert(tk.END, log_entry, tag)
                self.activity_log.see(tk.END)
                self.activity_log.configure(state="disabled")
            
            # Add to Activity Stream (Mini Log on Main Dashboard)
            if hasattr(self, 'activity_stream_list'):
                # Insert at top for "Latest" view
                self.activity_stream_list.insert(0, f"{timestamp} {msg}")
                # Keep only last 50
                if self.activity_stream_list.size() > 50:
                    self.activity_stream_list.delete(50, tk.END)
                    
            # Update Regime Status (Real-time)
            if hasattr(self, 'regime_status_label') and hasattr(self, 'regime_detector'):
                try:
                    # Get recent history from db_utils directly to avoid dependency on passed args
                    from core.utils.db_utils import get_recent_winning_numbers
                    # We need just the numbers
                    hist_rows = get_recent_winning_numbers(limit=20)
                    hist_nums = [r['number'] for r in hist_rows]
                    
                    # Use Multi-Dimension Detection
                    if hasattr(self.regime_detector, 'detect_all_regimes'):
                         regimes = self.regime_detector.detect_all_regimes(hist_nums)
                         # Format: "Color:TRND | Doz:CHOP"
                         # Priority: Show significant ones (TREND or CHOP) first
                         summary = []
                         significant_count = 0
                         
                         short_names = {
                             "Colors": "Col", "Dozens": "Doz", "Columns": "Col", 
                             "EvenOdd": "E/O", "HighLow": "H/L"
                         }
                         
                         # Fixed order for consistency: Color, Dozen, Column, E/O, H/L
                         display_order = ["Colors", "Dozens", "Columns", "EvenOdd", "HighLow"]
                         
                         for dim in display_order:
                             state = regimes.get(dim, "NEUTRAL")
                             short = short_names.get(dim, dim)
                             
                             # Formatting: "Short:State"
                             # State codes: T(Trend), C(Chop), N(Neut) or Icons
                             if state == "TRENDING":
                                 val = "TRND" # or 🟢
                                 full = f"{short}:TRND"
                             elif state == "CHOPPY":
                                 val = "CHOP" # or 🔴
                                 full = f"{short}:CHOP"
                             else:
                                 val = "NEUT" # or ⚪
                                 full = f"{short}:NEUT"
                                 
                             summary.append(full)
                         
                         display_text = "  ".join(summary)
                         main_color = "white"
                             
                         self.regime_status_label.configure(text=display_text, text_color=main_color)
                         
                    else:
                        # Fallback legacy
                        state = self.regime_detector.detect_state(hist_nums)
                        color = "gray"
                        if state == "TRENDING": color = "#2ecc71"
                        elif state == "CHOPPY": color = "#e74c3c"
                        elif state == "NEUTRAL": color = "#f1c40f"
                        self.regime_status_label.configure(text=state, text_color=color)
                except Exception as e:
                    print(f"Regime UI Error: {e}")
                    pass
            
            # Also print to console for debugging
            print(log_entry.strip())
            
        except Exception as e:
            print(f"Error logging message: {e}")

    def filter_activity_log(self, *args):
        """Filter log content (placeholder implementation)"""
        # CTkTextbox is hard to filter in-place without clearing/refilling.
        # For now, we might just tag/highlight matching lines or implemented specific search.
        # A simpler approach for MVP:
        # 1. Clear text.
        # 2. Refill from a master list `self.log_history`.
        # This requires storing `self.log_history`.
        # Skipping complex filtering for now to avoid storing massive lists in memory for this MVP task.
        pass

    def clear_activity_log(self):
        """Clear all text from the log"""
        if hasattr(self, 'activity_log'):
            self.activity_log.configure(state="normal")
            self.activity_log.delete("1.0", tk.END)
            self.activity_log.configure(state="disabled")

    def save_activity_log_to_file(self):
        """Save log to text file"""
        if hasattr(self, 'activity_log'):
            content = self.activity_log.get("1.0", tk.END)
            filename = f"activity_log_{int(time.time())}.txt"
            with open(filename, "w") as f:
                f.write(content)
            messagebox.showinfo("Saved", f"Log saved to {filename}")

    def update_strategy_preview(self):
        strategy_name = self.strategy_selector_var.get()
        # Default-hide the convert-to-composite button; pattern_follower branch re-shows it.
        if hasattr(self, 'convert_to_composite_btn'):
            try:
                self.convert_to_composite_btn.pack_forget()
            except tk.TclError:
                pass
        if not strategy_name:
            self.strategy_preview_text.configure(state="normal")
            self.strategy_preview_text.delete("1.0", tk.END)
            self.strategy_preview_text.insert(tk.END, "No strategy selected. Please select a strategy from the dropdown above.")
            self.strategy_preview_text.configure(state="disabled")
            # Hide edit units button when no strategy selected
            self.edit_units_btn.grid_remove()
            return
            
        preview = f"📋 STRATEGY DETAILS\n"
        preview += f"{'='*50}\n\n"
        preview += f"🎯 Strategy Name: {strategy_name}\n\n"
        
        if strategy_name in self.custom_strategies:
            strategy_data = self.custom_strategies[strategy_name]

            # Dynamic Neighbors mode preview
            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'neighbors':
                neighbors = strategy_data.get('neighbors', 2)
                anchor_offsets = strategy_data.get('anchor_offsets', [1])
                hot_count = strategy_data.get('hot_count', 0)
                cold_count = strategy_data.get('cold_count', 0)
                lookback = strategy_data.get('lookback', 30)
                per_anchor = 1 + 2 * neighbors
                total_anchors = len(anchor_offsets) + hot_count + cold_count
                anchor_desc = self._describe_anchors(anchor_offsets, hot_count, cold_count)
                preview += f"Mode: Dynamic Neighbors\n"
                preview += f"Neighbors per side: {neighbors}\n"
                preview += f"Anchors: {anchor_desc} ({total_anchors} anchor{'s' if total_anchors > 1 else ''})\n"
                preview += f"Numbers per anchor: {per_anchor} (overlaps deduplicated)\n"
                if hot_count or cold_count:
                    preview += f"Lookback window: {lookback} spins\n"
                preview += f"\nHow it works:\n"
                # Nth-last explanation
                if len(anchor_offsets) == 1 and anchor_offsets[0] == 1 and not hot_count and not cold_count:
                    preview += f"   After each spin, bets on the last winning\n"
                    preview += f"   number + {neighbors} neighbors each side.\n"
                else:
                    preview += f"   After each spin, picks anchor numbers from:\n"
                    for o in anchor_offsets:
                        if o == 1:
                            preview += f"   - Last winning number\n"
                        else:
                            preview += f"   - {o}{'nd' if o == 2 else 'rd' if o == 3 else 'th'} last winning number\n"
                    if hot_count:
                        preview += f"   - Top {hot_count} most frequent in last {lookback} spins\n"
                    if cold_count:
                        preview += f"   - Top {cold_count} most overdue in last {lookback} spins\n"
                    preview += f"   Then bets on each anchor's {neighbors} neighbors\n"
                    preview += f"   on each side of the wheel (overlaps merged).\n"
                preview += f"   On loss, progression controls bet sizing.\n"
                preview += f"   Requires straight-up number coordinates (0-36).\n"

                self.strategy_preview_text.configure(state="normal")
                self.strategy_preview_text.delete("1.0", tk.END)
                self.strategy_preview_text.insert(tk.END, preview)
                self.strategy_preview_text.configure(state="disabled")
                self.edit_units_btn.grid_remove()
                return

            # Pattern Follower mode preview
            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'pattern_follower':
                rules = strategy_data.get('rules', [])
                history_size = strategy_data.get('history_size', 50)
                preview += f"Mode: Pattern Follower\n"
                preview += f"History size: {history_size} spins\n"
                preview += f"Rules ({len(rules)}):\n"
                for i, r in enumerate(rules, 1):
                    preview += f"   {i}. {self._describe_rule(r)}\n"
                preview += f"\nHow it works:\n"
                preview += f"   Each spin, walks rules in order.\n"
                preview += f"   The FIRST rule whose detector matches\n"
                preview += f"   the recent history fires; its labels become the bet.\n"
                preview += f"   No match → no bet that spin.\n"
                preview += f"   Progression handles bet sizing.\n"

                self.strategy_preview_text.configure(state="normal")
                self.strategy_preview_text.delete("1.0", tk.END)
                self.strategy_preview_text.insert(tk.END, preview)
                self.strategy_preview_text.configure(state="disabled")
                self.edit_units_btn.grid_remove()
                # Show convert-to-composite migration button for pattern_follower presets
                if hasattr(self, 'convert_to_composite_btn'):
                    self.convert_to_composite_btn.pack(side="left", fill="x", expand=True, padx=(6, 0))
                return

            # Composite mode preview (regime router etc.)
            if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'composite':
                rules = strategy_data.get('rules', [])
                history_size = strategy_data.get('history_size', 50)
                preview += f"Mode: Composite (advanced)\n"
                preview += f"History size: {history_size} spins\n"
                preview += f"Rules ({len(rules)}):\n"
                for i, r in enumerate(rules, 1):
                    preview += f"   {i}. {self._describe_rule(r)}\n"
                preview += f"\nHow it works:\n"
                preview += f"   Each spin, evaluates rules top-to-bottom.\n"
                preview += f"   A rule fires when ALL its 'when' conditions match.\n"
                preview += f"   First matching rule wins; the rest are skipped.\n"
                preview += f"   'delegate' actions hand off to another preset\n"
                preview += f"   (sub-strategies stay history-current).\n"

                self.strategy_preview_text.configure(state="normal")
                self.strategy_preview_text.delete("1.0", tk.END)
                self.strategy_preview_text.insert(tk.END, preview)
                self.strategy_preview_text.configure(state="disabled")
                self.edit_units_btn.grid_remove()
                return

            # Resolve labels and units from any format
            if isinstance(strategy_data, dict) and 'labels' in strategy_data:
                labels = strategy_data['labels']
                bet_units = strategy_data.get('bet_units', {})
                # Legacy fallback: convert bet_amounts → units
                if not bet_units and 'bet_amounts' in strategy_data:
                    base = float(self.config.get("base_bet", 1.0))
                    bet_units = {lbl: max(1, int(amt / base)) if base > 0 else 1
                                 for lbl, amt in strategy_data['bet_amounts'].items()}
            elif isinstance(strategy_data, list):
                labels = strategy_data
                bet_units = {}
            else:
                labels = []
                bet_units = {}

            preview += f"🎲 Bet Labels ({len(labels)}):\n"
            for i, label in enumerate(labels, 1):
                preview += f"   {i}. {label}\n"

            if bet_units:
                base_bet = float(self.config.get("base_bet", 1.0))
                preview += f"\n💰 Custom Bet Units:\n"
                for label, units in bet_units.items():
                    preview += f"   • {label}: {units} unit(s) = ${units * base_bet:.2f}\n"
            else:
                preview += f"\n💰 Bet Amount: 1 unit (base bet) per label\n"

            preview += f"\n📍 Required Coordinates:\n"
            for i, label in enumerate(labels, 1):
                if label in self.coordinates:
                    preview += f"   ✓ {label} (recorded)\n"
                else:
                    preview += f"   ❌ {label} (not recorded)\n"

            # Board highlighting — show units on chips
            bets_map = {}
            for lbl in labels:
                bets_map[lbl] = bet_units.get(lbl, "")
            if hasattr(self, 'roulette_board'):
                self.roulette_board.highlight_bets(bets_map)

        else:
            preview += f"🎲 Type: Built-in strategy\n"
            preview += f"💰 Bet Amount: Uses base bet from bot control\n"
            preview += f"\n📍 Required Coordinates:\n"
            preview += f"   All recorded coordinates\n"
            if hasattr(self, 'roulette_board'):
                self.roulette_board.clear_chips()
        
        self.strategy_preview_text.configure(state="normal")
        self.strategy_preview_text.delete("1.0", tk.END)
        self.strategy_preview_text.insert(tk.END, preview)
        self.strategy_preview_text.configure(state="disabled")
        
        # Show edit units button when strategy is selected
        self.edit_units_btn.grid()
        
        # Auto-expand if strategy has many labels (more than 10)
        if strategy_name in self.custom_strategies:
            strategy_data = self.custom_strategies[strategy_name]
            if isinstance(strategy_data, dict) and 'labels' in strategy_data:
                label_count = len(strategy_data['labels'])
            else:
                label_count = len(strategy_data)
            
            if label_count > 10 and not self.preview_expanded.get():
                # Auto-expand for large strategies
                self.toggle_preview_size()

    def import_strategies(self):
        from tkinter import filedialog
        import json
        file_path = filedialog.askopenfilename(title="Import Strategies", filetypes=[("JSON Files", "*.json")])
        if not file_path:
            return
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                messagebox.showerror("Import Error", "Invalid strategy file format.")
                return
            self.custom_strategies.update(data)
            self.config["custom_strategies"] = self.custom_strategies
            save_config(self.config)
            self.update_strategy_list_display()
            self.update_strategy_dropdown()
            self.update_strategy_selector()
            self.update_strategy_preview()
            messagebox.showinfo("Import Success", "Strategies imported successfully.")
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import strategies.\n\n{e}")

    def export_strategies(self):
        from tkinter import filedialog
        import json
        file_path = filedialog.asksaveasfilename(title="Export Strategies", defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if not file_path:
            return
        try:
            with open(file_path, "w") as f:
                json.dump(self.custom_strategies, f, indent=2)
            messagebox.showinfo("Export Success", "Strategies exported successfully.")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export strategies.\n\n{e}")

    def update_strategy(self):
        """Update the selected strategy with current label selection, preserving units"""
        try:
            strategy_name = self.strategy_selector_var.get()
            if not strategy_name:
                self.log_message("ERROR: Please select a strategy to update.")
                return

            if strategy_name not in self.custom_strategies:
                self.log_message(f"ERROR: Strategy '{strategy_name}' not found.")
                return

            # Get selected labels
            selected_indices = self.label_selector.curselection()
            if not selected_indices:
                self.log_message("ERROR: Please select at least one label to add to the strategy.")
                return

            selected_labels = [self.label_selector.get(i) for i in selected_indices]

            # Preserve existing units for labels that still exist
            old_data = self.custom_strategies[strategy_name]
            old_units = {}
            if isinstance(old_data, dict):
                old_units = old_data.get('bet_units', {})

            # Merge: keep old units for surviving labels, add default for new ones
            new_units = {}
            for lbl in selected_labels:
                if lbl in old_units:
                    new_units[lbl] = old_units[lbl]

            # Update with proper dict format
            self.custom_strategies[strategy_name] = {
                'labels': selected_labels,
                'bet_units': new_units
            }

            self.config["custom_strategies"] = self.custom_strategies
            save_config(self.config)

            # Update displays
            self.update_strategy_list_display()
            self.update_strategy_dropdown()
            self.update_strategy_selector()
            self.update_strategy_preview()

            self.log_message(f"Strategy '{strategy_name}' updated with {len(selected_labels)} labels.")
            self.set_status(f"Strategy '{strategy_name}' updated successfully.")

        except Exception as e:
            self.log_message(f"ERROR: Failed to update strategy: {str(e)}")
            self.set_status("Strategy update failed.")

    def load_strategy_into_builder(self, silent=False):
        """Load the selected strategy back into the builder for editing.

        Args:
            silent: If True, skip error dialogs and status messages (used by auto-load on selection).
        """
        self._loading_strategy_into_builder = True
        try:
            self._load_strategy_into_builder_inner(silent)
        finally:
            self._loading_strategy_into_builder = False

    def _load_strategy_into_builder_inner(self, silent=False):
        strategy_name = self.strategy_selector_var.get()
        if not strategy_name or strategy_name not in self.custom_strategies:
            if not silent:
                messagebox.showerror("Error", "Please select a strategy to edit.")
            else:
                # Still show preview for non-custom strategies
                self.update_strategy_preview()
            return

        strategy_data = self.custom_strategies[strategy_name]

        # Handle dynamic neighbors mode
        if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'neighbors':
            self.custom_strategy_var.set(strategy_name)
            self.bet_mode_var.set("Neighbors")
            self._on_bet_mode_change("Neighbors")
            self.neighbors_count_var.set(strategy_data.get('neighbors', 2))
            anchor_offsets = strategy_data.get('anchor_offsets', [1])
            self.neighbors_anchors_var.set(",".join(str(a) for a in anchor_offsets))
            self.neighbors_hot_var.set(strategy_data.get('hot_count', 0))
            self.neighbors_cold_var.set(strategy_data.get('cold_count', 0))
            self.neighbors_lookback_var.set(strategy_data.get('lookback', 30))
            self.label_selector.selection_clear(0, tk.END)
            self.enable_custom_bet_units_var.set(False)
            self.custom_bet_units_frame.grid_remove()
            self.update_strategy_preview()
            if not silent:
                messagebox.showinfo("Loaded", f"Dynamic Neighbors strategy '{strategy_name}' loaded into builder.")
            return

        # Handle pattern follower mode
        if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'pattern_follower':
            rules = strategy_data.get('rules', [])
            # If the preset has any composite-shape rules ({when, then}), it can't
            # round-trip through the flat-shape pattern_follower editor without
            # losing the action's group. Suggest opening it in composite mode.
            has_composite_shape = any(
                isinstance(r, dict) and ('when' in r or 'then' in r) for r in rules
            )
            if has_composite_shape:
                if not silent:
                    messagebox.showwarning(
                        "Composite-shape rules detected",
                        f"'{strategy_name}' has rules using the composite "
                        "shape ({when:..., then:...}). Edit it via Composite "
                        "mode (or in JSON) to preserve the structure."
                    )
                self.update_strategy_preview()
                return

            self.custom_strategy_var.set(strategy_name)
            self.bet_mode_var.set("Pattern Follower")
            self._on_bet_mode_change("Pattern Follower")
            self.pattern_follower_editor.set_rules(rules)
            self.pattern_follower_editor.set_history_size(strategy_data.get('history_size', 50))
            self.label_selector.selection_clear(0, tk.END)
            self.enable_custom_bet_units_var.set(False)
            self.custom_bet_units_frame.grid_remove()
            self.update_strategy_preview()
            if not silent:
                messagebox.showinfo("Loaded", f"Pattern Follower strategy '{strategy_name}' loaded into builder.")
            return

        # Composite mode — load into the composite editor
        if isinstance(strategy_data, dict) and strategy_data.get('mode') == 'composite':
            self.custom_strategy_var.set(strategy_name)
            self.bet_mode_var.set("Composite")
            self._on_bet_mode_change("Composite")
            self.composite_editor.set_rules(strategy_data.get('rules', []))
            self.composite_editor.set_history_size(strategy_data.get('history_size', 50))
            self.label_selector.selection_clear(0, tk.END)
            self.enable_custom_bet_units_var.set(False)
            self.custom_bet_units_frame.grid_remove()
            self.update_strategy_preview()
            if not silent:
                messagebox.showinfo("Loaded", f"Composite strategy '{strategy_name}' loaded into builder.")
            return

        # Resolve labels and units
        if isinstance(strategy_data, dict) and 'labels' in strategy_data:
            labels = strategy_data['labels']
            bet_units = strategy_data.get('bet_units', {})
            if not bet_units and 'bet_amounts' in strategy_data:
                base = float(self.config.get("base_bet", 1.0))
                bet_units = {lbl: max(1, int(amt / base)) if base > 0 else 1
                             for lbl, amt in strategy_data['bet_amounts'].items()}
        elif isinstance(strategy_data, list):
            labels = strategy_data
            bet_units = {}
        else:
            return

        # Set strategy name in builder
        self.custom_strategy_var.set(strategy_name)

        # Select labels in listbox
        self.label_selector.selection_clear(0, tk.END)
        if hasattr(self, '_global_label_selections'):
            self._global_label_selections.clear()
        for i in range(self.label_selector.size()):
            if self.label_selector.get(i) in labels:
                self.label_selector.selection_set(i)
                if hasattr(self, '_global_label_selections'):
                    self._global_label_selections.add(self.label_selector.get(i))

        # Enable custom units if strategy has them
        has_units = bool(bet_units)
        self.enable_custom_bet_units_var.set(has_units)
        if has_units:
            self.custom_bet_units_frame.grid()
        else:
            self.custom_bet_units_frame.grid_remove()

        # Build the unit entries from saved values (not defaults)
        self.update_bet_unit_entries(override_units=bet_units if has_units else None)

        # Sync board: set selections, unit edit mode, and unit chips
        if hasattr(self, 'roulette_board'):
            selected_indices = self.label_selector.curselection()
            selected_labels = [self.label_selector.get(i) for i in selected_indices]
            self.roulette_board.set_selected_labels(selected_labels)
            self.roulette_board.set_unit_edit_mode(has_units)
            self.roulette_board.set_label_units(bet_units if has_units else {})
            if has_units:
                self.roulette_board._refresh_unit_chips()
            else:
                self.roulette_board.highlight_bets({lbl: "" for lbl in selected_labels})

        # Update the preview text
        self.update_strategy_preview()

        if not silent:
            self.set_status(f"Loaded '{strategy_name}' into builder for editing.")

    def delete_strategy(self):
        """Delete the selected strategy with confirmation"""
        try:
            strategy_name = self.strategy_selector_var.get()
            if not strategy_name:
                self.log_message("ERROR: Please select a strategy to delete.")
                return
            
            if strategy_name not in self.custom_strategies:
                self.log_message(f"ERROR: Strategy '{strategy_name}' not found.")
                return
            
            # Confirmation dialog
            result = messagebox.askyesno(
                "Confirm Delete",
                f"Are you sure you want to delete the strategy '{strategy_name}'?\n\nThis action cannot be undone.",
                icon="warning"
            )
            
            if result:
                # Delete the strategy
                del self.custom_strategies[strategy_name]
                
                # Update displays
                self.update_strategy_list_display()
                self.update_strategy_dropdown()
                self.update_strategy_selector()
                
                # Clear selection
                self.strategy_selector_var.set("")
                
                self.log_message(f"Strategy '{strategy_name}' deleted successfully.")
                self.set_status(f"Strategy '{strategy_name}' deleted.")
                
        except Exception as e:
            self.log_message(f"ERROR: Failed to delete strategy: {str(e)}")
            self.set_status("Strategy deletion failed.")

    def clear_all_strategies(self):
        """Clear all custom strategies with confirmation"""
        try:
            if not self.custom_strategies:
                self.log_message("No custom strategies to clear.")
                return
            
            # Confirmation dialog
            result = messagebox.askyesno(
                "Confirm Clear All",
                f"Are you sure you want to delete ALL {len(self.custom_strategies)} custom strategies?\n\nThis action cannot be undone.",
                icon="warning"
            )
            
            if result:
                # Clear all strategies
                self.custom_strategies.clear()
                
                # Update displays
                self.update_strategy_list_display()
                self.update_strategy_dropdown()
                self.update_strategy_selector()
                
                # Clear selection
                self.strategy_selector_var.set("")
                
                self.log_message("All custom strategies cleared successfully.")
                self.set_status("All strategies cleared.")
                
        except Exception as e:
            self.log_message(f"ERROR: Failed to clear strategies: {str(e)}")
            self.set_status("Clear all failed.")

    def _on_bet_mode_change(self, value):
        """Show/hide mode-specific config frames based on bet mode selection."""
        # Hide all mode frames first
        self.neighbors_config_frame.grid_remove()
        if hasattr(self, 'pattern_follower_frame'):
            self.pattern_follower_frame.grid_remove()
        if hasattr(self, 'composite_frame'):
            self.composite_frame.grid_remove()
        # Show the relevant one
        if value == "Neighbors":
            self.neighbors_config_frame.grid()
        elif value == "Pattern Follower":
            if hasattr(self, 'pattern_follower_frame'):
                self.pattern_follower_frame.grid()
        elif value == "Composite":
            if hasattr(self, 'composite_frame'):
                self.composite_frame.grid()

    @staticmethod
    def _describe_anchors(anchor_offsets, hot_count=0, cold_count=0):
        """Human-readable description of anchor configuration."""
        parts = []
        for o in anchor_offsets:
            if o == 1:
                parts.append("last")
            elif o == 2:
                parts.append("2nd last")
            elif o == 3:
                parts.append("3rd last")
            else:
                parts.append(f"{o}th last")
        if hot_count > 0:
            parts.append(f"top {hot_count} hot")
        if cold_count > 0:
            parts.append(f"top {cold_count} cold")
        return " + ".join(parts) if parts else "last"

    @staticmethod
    def _flat_rule_to_composite_rule(flat):
        """Convert a pattern_follower flat-shape rule to composite when/then shape.

        Handles the 'regime' -> 'match' key remap (flat shape uses 'regime' on the
        condition; composite shape standardizes on 'match').
        Idempotent: if the rule is already composite-shaped, returns it unchanged.
        """
        if not isinstance(flat, dict):
            return flat
        if "when" in flat and "then" in flat:
            return flat

        # Build the condition: everything except action/target/strategy/labels keys
        condition = {k: v for k, v in flat.items()
                     if k not in ("action", "target", "strategy", "strategy_name", "labels")}

        # Remap legacy 'regime' key to 'match' for the regime detector
        if condition.get("detect") == "regime" and "regime" in condition:
            if "match" not in condition:
                condition["match"] = condition.pop("regime")
            else:
                condition.pop("regime")

        # Build the action
        action_type = flat.get("action", "follow")
        action = {"action": action_type}
        if action_type in ("follow", "contra"):
            action["group"] = flat.get("group", "color")
        elif action_type == "target":
            action["group"] = flat.get("group", "color")
            if "target" in flat:
                action["target"] = flat["target"]
        elif action_type == "labels":
            if "labels" in flat:
                action["labels"] = flat["labels"]
        elif action_type == "delegate":
            if "strategy" in flat:
                action["strategy"] = flat["strategy"]
            elif "strategy_name" in flat:
                action["strategy"] = flat["strategy_name"]

        return {"when": [condition], "then": action}

    def convert_pattern_follower_to_composite(self):
        """Migrate the currently-selected pattern_follower preset into a composite preset.

        - Asks the user for a name (default: '<original> (composite)')
        - Confirms overwrite if the target name is taken
        - Saves the new preset, switches to Composite mode, loads it into the editor
        - Original preset is preserved (not modified)
        """
        from tkinter import simpledialog

        name = self.strategy_selector_var.get()
        if not name or name not in self.custom_strategies:
            messagebox.showerror("No selection", "Select a pattern_follower preset first.")
            return

        data = self.custom_strategies[name]
        if not isinstance(data, dict) or data.get("mode") != "pattern_follower":
            messagebox.showinfo(
                "Not pattern_follower",
                "This action only converts pattern_follower presets to composite shape."
            )
            return

        flat_rules = data.get("rules", [])
        if not flat_rules:
            messagebox.showerror("Empty preset", "This preset has no rules to convert.")
            return

        try:
            composite_rules = [self._flat_rule_to_composite_rule(r) for r in flat_rules]
        except Exception as e:
            messagebox.showerror("Conversion failed", f"Could not convert rules: {e}")
            return

        suggested = f"{name} (composite)"
        new_name = simpledialog.askstring(
            "Convert to Composite",
            f"Save the converted preset as:",
            initialvalue=suggested, parent=self.master if hasattr(self, 'master') else None,
        )
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name:
            return

        if new_name in self.custom_strategies and new_name != name:
            if not messagebox.askyesno(
                "Overwrite?",
                f"'{new_name}' already exists. Overwrite it?"
            ):
                return

        new_data = {
            "mode": "composite",
            "rules": composite_rules,
            "history_size": data.get("history_size", 50),
        }
        if "custom_strategies" not in self.config:
            self.config["custom_strategies"] = {}
        self.config["custom_strategies"][new_name] = new_data
        save_config(self.config)
        self.custom_strategies = self.config["custom_strategies"]
        self.update_strategy_list_display()
        self.update_strategy_dropdown()
        self.update_strategy_selector()
        self.strategy_selector_var.set(new_name)
        self.update_strategy_preview()

        messagebox.showinfo(
            "Converted",
            f"Created composite preset '{new_name}' with {len(composite_rules)} "
            f"rule{'s' if len(composite_rules) != 1 else ''}.\n\n"
            f"Original '{name}' is unchanged. The new preset is selected — click "
            f"the Edit/Load button to open it in the Composite editor and add "
            f"compound conditions or delegate actions."
        )

    @staticmethod
    def _describe_condition(spec):
        """Render a single condition dict as a phrase, e.g. 'color streak ≥ 3' or
        'color regime=TRENDING (window=20)'."""
        if not isinstance(spec, dict):
            return "(invalid condition)"
        detect = spec.get("detect", "?")
        group = spec.get("group", "?")
        if detect == "streak":
            ml = spec.get("min_length", 1)
            return f"{group} streak ≥ {ml}"
        if detect == "dominance":
            window = spec.get("window", 20)
            thr = spec.get("threshold", 0.6)
            return f"{group} dominance ≥ {thr:.0%} in last {window}"
        if detect == "alternation":
            window = spec.get("window", 10)
            thr = spec.get("threshold", 0.7)
            return f"{group} alternation ≥ {thr:.0%} in last {window}"
        if detect == "regime":
            window = spec.get("window", 20)
            regime = spec.get("regime") or spec.get("match")
            regime_str = f"={regime}" if regime else ""
            return f"{group} regime{regime_str} (window={window})"
        if detect == "last_number_in":
            nums = spec.get("numbers", [])
            offset = spec.get("offset", 1)
            nums_str = ", ".join(str(n) for n in nums[:6]) + ("..." if len(nums) > 6 else "")
            slot = "last" if offset == 1 else f"#{offset}-back"
            return f"{slot} number ∈ [{nums_str}]"
        return f"{group} {detect}"

    @staticmethod
    def _describe_action(spec):
        """Render an action dict as a phrase, e.g. 'follow color' or 'delegate -> X'."""
        if not isinstance(spec, dict):
            return "(invalid action)"
        a = spec.get("action", "?")
        if a == "delegate":
            return f"delegate → {spec.get('strategy') or spec.get('strategy_name', '?')}"
        if a == "labels":
            return f"bet {spec.get('labels', [])}"
        if a == "target":
            return f"target {spec.get('target', '?')}"
        if a in ("follow", "contra"):
            group = spec.get("group", "?")
            return f"{a} {group}"
        return str(a)

    @staticmethod
    def _describe_rule(rule):
        """Render a pattern_follower or composite rule dict as one human-readable line.

        Handles both flat shape ({detect, group, action, ...}) and composite shape
        ({when: [...], then: {...}}). Picks the right fields per detector type so
        dominance/alternation/regime rules show their thresholds rather than '0'
        for an absent min_length.
        """
        if not isinstance(rule, dict):
            return f"(invalid rule: {rule!r})"

        # Composite shape — render compound conditions joined by AND
        if "when" in rule and "then" in rule:
            cond_parts = [RouletteBotGUI._describe_condition(c) for c in rule.get("when", [])]
            then = rule.get("then", {})
            cond_text = " AND ".join(cond_parts) if cond_parts else "?"
            return f"IF {cond_text} → {RouletteBotGUI._describe_action(then)}"

        # Flat shape — single condition with action keys at the same level
        if "detect" in rule and "action" in rule:
            cond_str = RouletteBotGUI._describe_condition(rule)
            action_str = RouletteBotGUI._describe_action({
                "action": rule.get("action"),
                "group": rule.get("group"),
                "target": rule.get("target"),
                "strategy": rule.get("strategy"),
                "labels": rule.get("labels"),
            })
            return f"IF {cond_str} → {action_str}"

        return f"(unrecognized rule shape: keys={sorted(rule.keys())})"

    def toggle_custom_bet_units(self):
        """Toggle the visibility of custom bet units interface"""
        if getattr(self, '_loading_strategy_into_builder', False):
            return
        enabled = self.enable_custom_bet_units_var.get()
        if enabled:
            self.custom_bet_units_frame.grid()
            self.update_bet_unit_entries()
        else:
            self.custom_bet_units_frame.grid_remove()
            # Clear bet unit entries
            for widget in self.bet_units_inner_frame.winfo_children():
                widget.destroy()
            self.bet_unit_entries.clear()
        # Sync board unit-edit mode
        if hasattr(self, 'roulette_board'):
            self.roulette_board.set_unit_edit_mode(enabled)
            if not enabled:
                self.roulette_board.set_label_units({})

    def update_bet_unit_entries(self, override_units=None):
        """Update the bet unit entry fields based on selected labels.

        Args:
            override_units: Optional dict {label: int} to pre-fill instead of default.
        """
        try:
            # Clear existing entries (destroy widgets properly)
            for widget in self.bet_units_inner_frame.winfo_children():
                widget.destroy()
            self.bet_unit_entries.clear()

            # Get selected labels
            selected_indices = self.label_selector.curselection()
            if not selected_indices:
                return

            selected_labels = [self.label_selector.get(i) for i in selected_indices]
            default_val = self.default_units_var.get()

            # Create entry for each selected label
            for i, label in enumerate(selected_labels):
                try:
                    label_widget = ttk.Label(self.bet_units_inner_frame, text=f"{label}:", font=("Arial", 8))
                    label_widget.grid(row=i, column=0, sticky="w", padx=(0, 5), pady=1)

                    # Use override value if provided, otherwise default
                    init_val = override_units.get(label, default_val) if override_units else default_val
                    units_var = tk.IntVar(value=init_val)
                    entry = ttk.Entry(self.bet_units_inner_frame, textvariable=units_var, width=8)
                    entry.grid(row=i, column=1, sticky="w", padx=(0, 10), pady=1)

                    self.bet_unit_entries[label] = (entry, units_var)
                except Exception as e:
                    pass
            
            # Force update of the canvas
            self.bet_units_inner_frame.update_idletasks()

            # Sync units to roulette board for chip display
            self._sync_units_to_board()

        except Exception as e:
            print(f"ERROR in update_bet_unit_entries: {e}")
            import traceback
            traceback.print_exc()

    def _add_unit_entry(self, label, init_val):
        """Add a single unit entry row without rebuilding the whole panel."""
        try:
            row = len(self.bet_unit_entries)
            label_widget = ttk.Label(self.bet_units_inner_frame, text=f"{label}:", font=("Arial", 8))
            label_widget.grid(row=row, column=0, sticky="w", padx=(0, 5), pady=1)
            units_var = tk.IntVar(value=init_val)
            entry = ttk.Entry(self.bet_units_inner_frame, textvariable=units_var, width=8)
            entry.grid(row=row, column=1, sticky="w", padx=(0, 10), pady=1)
            self.bet_unit_entries[label] = (entry, units_var)
        except Exception:
            pass

    def _remove_unit_entry(self, label):
        """Remove a single unit entry row."""
        if label in self.bet_unit_entries:
            entry, units_var = self.bet_unit_entries[label]
            # Destroy the entry and its sibling label widget
            parent = entry.master
            entry_row = entry.grid_info().get('row')
            if entry_row is not None:
                for widget in parent.grid_slaves(row=entry_row):
                    widget.destroy()
            del self.bet_unit_entries[label]

    def _reorder_unit_entries(self, ordered_labels):
        """Re-grid unit entries in the correct order after add/remove."""
        row = 0
        for label in ordered_labels:
            if label in self.bet_unit_entries:
                entry, units_var = self.bet_unit_entries[label]
                # Find the label widget (column 0 in same row)
                parent = entry.master
                for widget in parent.grid_slaves():
                    info = widget.grid_info()
                    if info.get('column') == 0 and isinstance(widget, ttk.Label) and widget.cget('text') == f"{label}:":
                        widget.grid(row=row, column=0, sticky="w", padx=(0, 5), pady=1)
                        break
                entry.grid(row=row, column=1, sticky="w", padx=(0, 10), pady=1)
                row += 1

    def get_custom_bet_units(self):
        """Get the custom bet units from the UI as a dictionary of integers."""
        if not self.enable_custom_bet_units_var.get():
            return {}
        
        bet_units = {}
        for label, (entry, units_var) in self.bet_unit_entries.items():
            try:
                units = units_var.get()
                if units > 0:
                    bet_units[label] = units
            except (tk.TclError, ValueError):
                # Handle cases where the entry might be empty or invalid
                pass
        return bet_units

    def _sync_units_to_board(self):
        """Push current bet_unit_entries values to the roulette board for chip display."""
        if not hasattr(self, 'roulette_board'):
            return
        units = {}
        for label, (entry, units_var) in self.bet_unit_entries.items():
            try:
                units[label] = units_var.get()
            except (tk.TclError, ValueError):
                units[label] = 1
        self.roulette_board.set_label_units(units)
        if self.enable_custom_bet_units_var.get():
            self.roulette_board._refresh_unit_chips()

    def _on_board_unit_edit(self, label, units):
        """Handle unit edit from clicking a chip on the roulette board."""
        # Sync back to the entry list
        if label in self.bet_unit_entries:
            _entry, units_var = self.bet_unit_entries[label]
            units_var.set(units)
        # If label exists but with different case, try case-insensitive match
        else:
            for lbl, (_entry, units_var) in self.bet_unit_entries.items():
                if lbl.lower() == label.lower():
                    units_var.set(units)
                    break

    def _on_board_cell_click(self, label, selected):
        """Handle click on a roulette board cell — toggle the label in the listbox."""
        if not hasattr(self, 'label_selector'):
            return

        # Find the label in the listbox (case-insensitive match)
        listbox = self.label_selector
        match_index = None
        for i in range(listbox.size()):
            if listbox.get(i).lower() == label.lower():
                match_index = i
                break

        if match_index is None:
            return  # Label not in listbox (e.g. not a valid bet type)

        if selected:
            listbox.selection_set(match_index)
            # Scroll to show the selected item
            listbox.see(match_index)
        else:
            listbox.selection_clear(match_index)

        # Trigger the same handler as manual listbox selection
        self.on_label_selection_change()

    def on_label_selection_change(self, event=None):
        """Handle label selection changes to update bet amount entries and board highlighting"""
        # Skip if we're programmatically loading a strategy into the builder
        if getattr(self, '_loading_strategy_into_builder', False):
            return

        selected_indices = self.label_selector.curselection()
        selected_labels = [self.label_selector.get(i) for i in selected_indices]
        selected_set = set(selected_labels)

        # Update custom units panel — only add/remove changed entries (no full rebuild)
        if self.enable_custom_bet_units_var.get():
            existing_labels = set(self.bet_unit_entries.keys())

            # Only do a full rebuild if labels actually changed
            if selected_set != existing_labels:
                existing_units = self.get_custom_bet_units()
                # Add entries for newly selected labels
                default_val = self.default_units_var.get()
                for label in selected_set - existing_labels:
                    init_val = existing_units.get(label, default_val)
                    self._add_unit_entry(label, init_val)
                # Remove entries for deselected labels
                for label in existing_labels - selected_set:
                    self._remove_unit_entry(label)
                # Re-grid remaining entries in order
                self._reorder_unit_entries(selected_labels)
                self._sync_units_to_board()

        # Update Visual Board
        if hasattr(self, 'roulette_board'):
            self.roulette_board.set_selected_labels(selected_labels)

            if self.enable_custom_bet_units_var.get():
                self._sync_units_to_board()
            else:
                bets_map = {lbl: "" for lbl in selected_labels}
                self.roulette_board.highlight_bets(bets_map)

    def filter_label_selector(self, event=None):
        """Filter the label selector based on search text"""
        search_text = self.label_search_var.get().lower()
        
        # Store current selections from visible items
        current_selections = list(self.label_selector.curselection())
        visible_selected_items = [self.label_selector.get(i) for i in current_selections]
        
        # Update global selection state
        if not hasattr(self, '_global_label_selections'):
            self._global_label_selections = set()
        
        # Add current visible selections to global state
        self._global_label_selections.update(visible_selected_items)
        
        # Clear current listbox
        self.label_selector.delete(0, tk.END)
        
        # Get all available labels
        all_labels = list(self.coordinates.keys())
        
        # Filter labels based on search text
        if search_text:
            filtered_labels = [label for label in all_labels if search_text in label.lower()]
        else:
            filtered_labels = all_labels
        
        # Add filtered labels to listbox
        for label in filtered_labels:
            self.label_selector.insert(tk.END, label)
        
        # Restore selections from global state for items that are visible
        for i, label in enumerate(filtered_labels):
            if label in self._global_label_selections:
                self.label_selector.selection_set(i)
    
    def clear_label_search(self):
        """Clear the search box and show all labels"""
        self.label_search_var.set("")
        self.filter_label_selector()
        
        # Ensure all globally selected items are selected in the full list
        if hasattr(self, '_global_label_selections'):
            for i in range(self.label_selector.size()):
                label = self.label_selector.get(i)
                if label in self._global_label_selections:
                    self.label_selector.selection_set(i)
    
    def clear_all_label_selections(self):
        """Clear all label selections from both the listbox and global state"""
        # Clear the global selection state
        if hasattr(self, '_global_label_selections'):
            self._global_label_selections.clear()
        
        # Clear all selections in the listbox
        self.label_selector.selection_clear(0, tk.END)
        
        # Update bet amount entries if custom bet amounts are enabled
        # Update bet amount entries if custom bet amounts are enabled
        if self.enable_custom_bet_units_var.get():
            self.update_bet_unit_entries()
        
        self.set_status("All label selections cleared.")

    def delete_selected_label(self):
        """Permanently delete the selected label(s) from coordinates and config"""
        selected_indices = self.label_selector.curselection()
        if not selected_indices:
            messagebox.showinfo("Select Label", "Please select a label to delete.")
            return
            
        selected_labels = [self.label_selector.get(i) for i in selected_indices]
        
        confirm = messagebox.askyesno("Confirm Deletion", 
                                      f"Are you sure you want to PERMANENTLY delete {len(selected_labels)} label(s)?\nLabels: {', '.join(selected_labels[:3])}...")
        
        if not confirm:
            return
            
        deleted_count = 0
        for label in selected_labels:
            if label in self.coordinates:
                del self.coordinates[label]
                deleted_count += 1
                
        if deleted_count > 0:
            # Update config
            self.config["coordinates"] = self.coordinates
            save_config(self.config)
            
            # Refresh list
            self.filter_label_selector()
            self.set_status(f"Deleted {deleted_count} label(s).")
        else:
            messagebox.showwarning("Warning", "Selected labels were not found in coordinates.")
    
    def on_strategy_selected(self, event=None):
        """Handle strategy selection — load into builder so board + listbox stay in sync"""
        self.load_strategy_into_builder(silent=True)
    
    def toggle_preview_size(self):
        """Toggle between compact and expanded preview sizes"""
        if self.preview_expanded.get():
            # Collapse to compact size
            self.strategy_preview_text.configure(height=6)
            self.expand_preview_btn.configure(text="🔽 Expand Preview")
            self.preview_size_label.configure(text="Size: Compact")
            self.preview_expanded.set(False)
        else:
            # Expand to large size
            self.strategy_preview_text.configure(height=20)
            self.expand_preview_btn.configure(text="🔼 Collapse Preview")
            self.preview_size_label.configure(text="Size: Expanded")
            self.preview_expanded.set(True)
    
    def toggle_units_editing(self):
        """Toggle the units editing interface"""
        if self.preview_units_frame.winfo_viewable():
            # Hide editing interface
            self.preview_units_frame.grid_remove()
            self.edit_units_btn.configure(text="✏️ Edit Stored Units")
        else:
            # Show editing interface
            self.preview_units_frame.grid()
            self.edit_units_btn.configure(text="👁️ Hide Editor")
            self.populate_units_editor()
    
    def populate_units_editor(self):
        """Populate the units editor with current strategy data"""
        # Clear existing entries
        for widget in self.units_inner_frame.winfo_children():
            widget.destroy()
        self.preview_units_entries.clear()
        
        strategy_name = self.strategy_selector_var.get()
        
        if not strategy_name or strategy_name not in self.custom_strategies:
            return
        
        strategy_data = self.custom_strategies[strategy_name]
        
        if not strategy_data:
            return
            
        # Handle legacy list format
        if isinstance(strategy_data, list):
            labels = strategy_data
            bet_units = {}
        elif isinstance(strategy_data, dict) and 'labels' in strategy_data:
            labels = strategy_data['labels']
            # Prefer bet_units; fall back to bet_amounts → convert to units
            bet_units = strategy_data.get('bet_units', {})
            if not bet_units and 'bet_amounts' in strategy_data:
                base = float(self.config.get("base_bet", 1.0))
                bet_units = {lbl: max(1, int(amt / base)) if base > 0 else 1
                             for lbl, amt in strategy_data['bet_amounts'].items()}
        else:
            return

        base_bet = float(self.config.get("base_bet", 1.0))

        # Create header
        header_label = ttk.Label(self.units_inner_frame, text="Bet Type | Units | $ at current base",
                                font=("Arial", 9, "bold"))
        header_label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 5))

        # Create entries for each label
        for i, label in enumerate(labels, 1):
            label_widget = ttk.Label(self.units_inner_frame, text=f"{label}:", font=("Arial", 9))
            label_widget.grid(row=i, column=0, sticky="w", padx=(0, 10), pady=2)

            current_units = bet_units.get(label, 1)
            units_var = tk.IntVar(value=current_units)
            units_entry = ttk.Entry(self.units_inner_frame, textvariable=units_var, width=8)
            units_entry.grid(row=i, column=1, sticky="w", padx=(0, 10), pady=2)

            # Live $ preview (computed, not saved)
            amount_label = ttk.Label(self.units_inner_frame, text=f"${current_units * base_bet:.2f}",
                                   font=("Arial", 8), foreground="gray")
            amount_label.grid(row=i, column=2, sticky="w", pady=2)

            self.preview_units_entries[label] = (units_entry, units_var, amount_label)
            units_var.trace_add("write", lambda *args, l=label: self.update_amount_display(l))
    
    def update_amount_display(self, label):
        """Update the amount display when units change"""
        if label in self.preview_units_entries:
            units_entry, units_var, amount_label = self.preview_units_entries[label]
            try:
                units = units_var.get()
                base_bet = float(self.config.get("base_bet", 1.0))
                amount = units * base_bet
                amount_label.configure(text=f"${amount:.2f}")
            except (ValueError, tk.TclError):
                amount_label.configure(text="Invalid")
    
    def save_strategy_units(self):
        """Save the edited units back to the strategy"""
        strategy_name = self.strategy_selector_var.get()
        if not strategy_name or strategy_name not in self.custom_strategies:
            return
        
        strategy_data = self.custom_strategies[strategy_name]
    
        # Handle legacy list format (convert to dict for saving)
        if isinstance(strategy_data, list):
            strategy_data = {'labels': strategy_data, 'bet_units': {}}
        elif not isinstance(strategy_data, dict) or 'labels' not in strategy_data:
            return

        # Collect units only — engine computes $ at runtime
        new_bet_units = {}
        for label, (units_entry, units_var, amount_label) in self.preview_units_entries.items():
            try:
                units = units_var.get()
                if units > 0:
                    new_bet_units[label] = units
            except (ValueError, tk.TclError):
                continue

        # Save only units, remove legacy bet_amounts if present
        strategy_data['bet_units'] = new_bet_units
        strategy_data.pop('bet_amounts', None)
        self.custom_strategies[strategy_name] = strategy_data
        
        # Save to config
        self.config["custom_strategies"] = self.custom_strategies
        save_config(self.config)
        
        # Update displays
        self.update_strategy_preview()
        self.update_strategy_list_display()
        
        # Hide editing interface
        self.toggle_units_editing()
        
        messagebox.showinfo("Success", f"Bet units for strategy '{strategy_name}' updated successfully!")
    
    def cancel_units_editing(self):
        """Cancel units editing and hide the interface"""
        self.toggle_units_editing()

    def update_strategy_selector(self):
        """Update the strategy selector dropdown with current strategies"""
        try:
            strategy_names = list(self.custom_strategies.keys())
            self.strategy_selector.configure(values=strategy_names)
            if strategy_names and not self.strategy_selector_var.get():
                self.strategy_selector_var.set(strategy_names[0])
        except Exception as e:
            pass

    def initialize_strategy_rotation(self):
        """Initialize strategy rotation variables and state"""
        rotation_strategies = self.rotation_strategies_var.get().strip()
        if not rotation_strategies:
            self.log_simulation("⚠️ No strategies specified for rotation. Disabling rotation.")
            self.enable_strategy_rotation_var.get()
            return
        
        # Parse strategies from comma-separated string
        strategy_entries = [s.strip() for s in rotation_strategies.split(",") if s.strip()]
        if not strategy_entries:
            self.log_simulation("⚠️ No valid strategies found in rotation list. Disabling rotation.")
            self.enable_strategy_rotation_var.set(False)
            return
        
        # Parse strategy:progression pairs with configuration
        self.rotation_strategy_progressions = {}
        self.rotation_strategy_configs = {}
        self.rotation_strategy_settings = {}  # For base bet and session length
        available_strategies = ["martingale", "flat"] + list(self.custom_strategies.keys())
        available_progressions = ["flat", "martingale", "fibonacci", "dalembert", "custom_sequence", "dynamic"]
        
        valid_entries = []
        invalid_entries = []
        
        for entry in strategy_entries:
            if ":" in entry:
                # Format: strategy:progression|config1|config2 or strategy:progression
                parts = entry.split(":", 1)
                strategy_base = parts[0].strip()
                progression_full = parts[1].strip()
                prog_type_val = progression_full.split("|")[0].strip().lower()
                
                if strategy_base in available_strategies and prog_type_val in available_progressions:
                    valid_entries.append(entry)
                    self.rotation_strategy_progressions[entry] = prog_type_val
                    
                    # Parse progression configuration into configs & settings
                    config = {}
                    settings = {}
                    if "|" in progression_full:
                        config_parts = progression_full.split("|")[1:]
                        for config_part in config_parts:
                            # Avoid parsing rules=... here since it contains nested | and = characters
                            # StrategyEngine handles native parsing inside its __init__ gracefully.
                            if "=" in config_part and not config_part.startswith("rules="):
                                key, value = config_part.split("=", 1)
                                key = key.strip()
                                value = value.strip()
                                
                                if key in ["base_bet", "session_length", "stop_wins", "stop_losses", "stop_profit", "stop_loss_limit", "stop_time"]:
                                    settings[key] = value
                                else:
                                    config[key] = value
                    
                    if config:
                        self.rotation_strategy_configs[entry] = config
                    if settings:
                        self.rotation_strategy_settings[entry] = settings
                else:
                    invalid_entries.append(entry)
            else:
                # Format: strategy (use default progression)
                strategy = entry
                if strategy in available_strategies:
                    valid_entries.append(strategy)
                    self.rotation_strategy_progressions[strategy] = self.progression_var.get()
                else:
                    invalid_entries.append(entry)
        
        if invalid_entries:
            self.log_simulation(f"⚠️ Invalid entries in rotation list: {invalid_entries}")
        
        if not valid_entries:
            self.log_simulation("⚠️ No valid strategies found. Disabling rotation.")
            self.enable_strategy_rotation_var.set(False)
            return
        
        # Initialize rotation state
        self.rotation_strategies = valid_entries
        self.rotation_mode = self.rotation_mode_var.get()
        self.rotation_trigger = self.rotation_trigger_var.get()
        self.current_rotation_index = 0

        # Build the conditional-trigger engine (no-op when the bundle isn't in
        # conditional mode). When active, the trigger engine drives swaps and
        # can request skip-rounds; the legacy switch-on-loss block is bypassed
        # in run_bot to avoid double-swapping.
        try:
            self._init_trigger_engine(valid_entries)
        except Exception as _t_err:
            logger.warning(f"[Triggers] init failed ({_t_err}) — falling back to plain rotation")
            self._trigger_engine = None
            self._trigger_engines_by_base = {}

        # Note: For on_loss mode, the session loop (run_random_sessions / run_scheduled_sessions)
        # calls apply_rotation_strategy() at the start of each session, including session 1.
        # No need to apply here — doing so would double-advance the ranking index.

        # Update rotation info
        _mode_labels = {"sequential": "Sequential", "random": "Random", "smart_ranking": "Smart Ranking", "smart_ranking_reverse": "Smart Ranking (Reverse)"}
        mode_text = _mode_labels.get(self.rotation_mode, self.rotation_mode)
        override_text = "with progression override" if self.rotation_progression_override_var.get() else "using main progression"
        trigger_text = "switch on loss" if self.rotation_trigger == "on_loss" else "switch on session end"
        self.rotation_info_var.set(f"Rotation: {mode_text} - {len(valid_entries)} strategies ({override_text}, {trigger_text})")
        

        self.log_simulation(f"🔄 Strategy rotation initialized: {mode_text} mode with {len(valid_entries)} strategies ({override_text})")
        logger.info(f"🔄 Strategy rotation initialized: {mode_text} mode with {len(valid_entries)} strategies ({override_text})")

    def _place_keep_alive_bet(self, driver, reason_label: str):
        """Place a min-chip keep-alive bet so the casino sees activity even
        during long sit-out / trigger-skip stretches. Returns the placed-bet
        list on success, None on failure. Callers still own the per-iteration
        flags (bet_placed_this_round / waiting_for_result / bet_placed_time)
        because the surrounding loop tracks them as locals.

        `driver` is the RouletteBrowserAutomation instance from run_bot's
        local scope — passed in rather than imported because it's bound to
        the live session's browser window. pyautogui is imported locally
        for the same reason: run_bot does the same.

        Shared by:
          - the sit-out path (strategy returned no labels)
          - the trigger-skip path (conditional trigger fallback=skip_round)
        """
        import pyautogui as _pag
        try:
            ka_amount = float(self.config.get('keep_alive_amount', 0.1))
            ka_label = str(self.config.get('keep_alive_label', 'red'))
            print(f"⏰ Keep-alive ({reason_label}): placing {ka_amount} on {ka_label}")
            chip_breakdown = get_chip_breakdown(ka_amount)
            placed_bets = []
            old_pause = _pag.PAUSE
            _pag.PAUSE = 0.074
            try:
                for chip_label, count in chip_breakdown:
                    driver.select_chip(chip_label)
                    chip_value = float(
                        chip_label.replace('chip_', '').replace('.', '0.')
                        if chip_label.startswith('chip_.')
                        else chip_label.replace('chip_', '')
                    )
                    time.sleep(0.05)
                    for _ in range(count):
                        driver.place_bet(ka_label)
                        placed_bets.append({'label': ka_label, 'amount': chip_value})
            finally:
                _pag.PAUSE = old_pause
            if placed_bets:
                self._keep_alive_pending = True
                self._consecutive_sitouts = 0
                # Reset the idle timer too — a keep-alive bet IS activity.
                self._last_real_bet_at = time.time()
                if not self.has_placed_first_bet:
                    self.has_placed_first_bet = True
                self.update_stats_display(
                    current_bet=ka_amount,
                    betting_on=f"⏰ {ka_label} (keep-alive)"
                )
                self.set_status(f"Keep-alive bet placed: {ka_amount} on {ka_label} ({reason_label})")
                return placed_bets
        except Exception as e:
            logger.error(f"[Keep-alive] Failed to place keep-alive bet: {e}")
        return None

    def _keep_alive_due(self) -> tuple[bool, str]:
        """Return (should_fire, reason_label) for the keep-alive checks.
        Fires when EITHER:
          - consecutive sit-outs ≥ keep_alive_after_n_sitouts (default 0 = off)
          - time since last real bet ≥ keep_alive_max_idle_minutes (default 3)

        The count-based arm is OFF by default because sit-outs and trigger-
        skips happen on a ~2-second cadence during normal operation — firing
        every 5 sit-outs meant a keep-alive bet every ~10 seconds, which is
        way more aggressive than needed. Set keep_alive_after_n_sitouts to a
        positive integer in config if you want the count-based arm back.
        The time-based arm (3-min default) is the right primary mechanism:
        it fires when the bot has truly been idle, not just thinking."""
        sit_threshold = int(self.config.get('keep_alive_after_n_sitouts', 0))
        idle_minutes  = float(self.config.get('keep_alive_max_idle_minutes', 3))
        sitouts = getattr(self, '_consecutive_sitouts', 0)
        last_bet = getattr(self, '_last_real_bet_at', None) or time.time()
        idle_secs = time.time() - last_bet
        if sit_threshold > 0 and sitouts >= sit_threshold:
            return True, f"after {sitouts} sit-outs"
        if idle_minutes > 0 and idle_secs >= idle_minutes * 60:
            return True, f"after {idle_secs/60:.1f}min idle"
        return False, ""

    def _build_parallel_round_plan(self):
        """Collect per-armed-strategy bet plans for a parallel-mode round.

        Returns (per_strat, merged_bets, total_bet):
          - per_strat:    list of {name, eng, bets:[{label,amount}], total_bet}
          - merged_bets:  flat list of {label,amount} across all strategies
                          (duplicates by label are kept as separate entries so
                          chip placement stacks them naturally on the table)
          - total_bet:    sum of all amounts
        Returns (None, [], 0.0) when nothing's armed or every armed candidate
        refused to bet (sit-out). Callers should treat that as a skip-round.
        """
        engine = getattr(self, '_trigger_engine', None)
        cache = getattr(self, '_trigger_engines_by_base', None) or {}
        if engine is None or not cache:
            return None, [], 0.0
        labels_by_name = {}
        for base, eng in cache.items():
            try:
                labels_by_name[base] = list(eng.get_bet_labels() or [])
            except Exception:
                labels_by_name[base] = []
        cands = engine.pick_all(labels_by_name)
        if not cands:
            return None, [], 0.0
        _stopped_legs = getattr(self, '_parallel_stopped_legs', None) or set()
        per_strat = []
        merged_bets = []
        for cand in cands:
            eng = cache.get(cand.name)
            if eng is None:
                continue
            if cand.name in _stopped_legs:
                continue  # this leg hit its per-strategy stop — sit it out

            try:
                bet_amount = eng.get_next_bet()
            except Exception:
                bet_amount = 0.0
            if not bet_amount or bet_amount <= 0:
                continue  # this strategy refused — others still bet
            try:
                bets_dict = eng.get_bet_amounts() or {}
            except Exception:
                bets_dict = {}
            cand_bets = [{'label': l, 'amount': float(a)}
                         for l, a in bets_dict.items() if float(a) > 0]
            if not cand_bets:
                continue
            cand_total = sum(b['amount'] for b in cand_bets)
            per_strat.append({'name': cand.name, 'eng': eng,
                              'bets': cand_bets, 'total_bet': cand_total})
            merged_bets.extend(cand_bets)
        if not merged_bets:
            return None, [], 0.0
        return per_strat, merged_bets, sum(b['amount'] for b in merged_bets)

    def _place_parallel_bets(self, driver, merged_bets):
        """Place chip clicks for a merged parallel-bet plan. Returns the
        flat list of placed-bet records (for result attribution later)."""
        import pyautogui as _pag
        chip_placement_plan = {}
        for b in merged_bets:
            label = b['label']
            amount = b['amount']
            for chip_label, count in get_chip_breakdown(amount):
                chip_placement_plan.setdefault(chip_label, []).extend([label] * count)
        placed = []
        old_pause = _pag.PAUSE
        _pag.PAUSE = 0.074
        try:
            for chip_label, labels_to_place in chip_placement_plan.items():
                driver.select_chip(chip_label)
                chip_value = float(
                    chip_label.replace('chip_', '').replace('.', '0.')
                    if chip_label.startswith('chip_.')
                    else chip_label.replace('chip_', '')
                )
                time.sleep(0.05)
                for label in labels_to_place:
                    driver.place_bet(label)
                    placed.append({'label': label, 'amount': chip_value})
        finally:
            _pag.PAUSE = old_pause
        return placed

    def _handle_parallel_result(self, winning_number, winning_color):
        """Per-strategy attribution for a parallel round. Each strategy's
        progression advances based on ITS OWN labels' win/loss — independent
        of the bundle aggregate. Bundle-level P&L is the sum.

        Reads self._parallel_round (list of per-strategy plans), records
        results, fires HUD updates, and returns (net_pnl, summary_str).
        """
        from core.strategy_engine import calculate_win_amount
        plans = self._parallel_round or []
        TOL = 1e-6
        results = []
        net_pnl = 0.0
        total_bet = 0.0
        for ps in plans:
            ps_bets = ps['bets']
            ps_win_amt, ps_details = calculate_win_amount(ps_bets, winning_number)
            ps_return = ps_win_amt + sum(b['amount'] for b, d in zip(ps_bets, ps_details) if d['win'])
            ps_pnl = ps_return - ps['total_bet']
            ps_is_win = ps_pnl > TOL
            # Update this strategy's own progression with its OWN result.
            try:
                eng = ps['eng']
                if hasattr(eng, 'progression') and hasattr(eng.progression, 'record_result'):
                    import inspect as _i
                    sig = _i.signature(eng.progression.record_result)
                    if 'current_profit' in sig.parameters:
                        # Dynamic progression needs cumulative session profit
                        # — pass the BUNDLE total so session_high tracking is
                        # bundle-wide, not per-strategy.
                        proj = (getattr(self, 'cumulative_net_profit', 0.0) + net_pnl + ps_pnl)
                        eng.record_result(ps_is_win, current_balance=proj,
                                          winning_number=winning_number, round_pnl=ps_pnl)
                    else:
                        eng.record_result(ps_is_win, winning_number=winning_number,
                                          round_pnl=ps_pnl)
            except Exception as _rec_err:
                logger.warning(f"[Parallel] {ps['name']} record_result failed: {_rec_err}")
            # Per-leg stop enforcement: if THIS leg hit one of its per-strategy
            # "Stops" (wins/losses/profit/loss/time), disarm it so it sits out
            # future rounds while the other legs keep running.
            try:
                if hasattr(eng, 'check_session_stop'):
                    _leg_stop = eng.check_session_stop()
                    if _leg_stop:
                        _stopped = getattr(self, '_parallel_stopped_legs', None)
                        if _stopped is None:
                            _stopped = set()
                            self._parallel_stopped_legs = _stopped
                        if ps['name'] not in _stopped:
                            _stopped.add(ps['name'])
                            logger.info(f"🛑 Parallel leg '{ps['name']}' stopped: {_leg_stop}")
                            self.log_simulation(f"🛑 Leg '{ps['name']}' stopped — {_leg_stop}")
            except Exception:
                pass
            net_pnl += ps_pnl
            total_bet += ps['total_bet']
            results.append({'name': ps['name'], 'pnl': ps_pnl,
                            'result': 'WIN' if ps_is_win else 'LOSS'})
        # Per-strategy summary line for the log
        summary = " | ".join(f"{r['name']}:{r['result']} ${r['pnl']:+.2f}" for r in results)
        return net_pnl, summary, total_bet, results

    def _init_trigger_engine(self, rotation_entries):
        """Construct the conditional TriggerEngine + per-strategy label cache.

        Only active when the loaded bundle has triggers_config.selection_mode
        == 'conditional'. Engines are instantiated read-only so we can ask
        each candidate `get_bet_labels()` without disturbing the bot's
        actively-betting strategy. Real swaps still go through
        request_strategy_swap → _apply_pending_strategy_swap; this cache only
        serves the trigger evaluator."""
        self._trigger_engine = None
        self._trigger_engines_by_base = {}
        self._trigger_skip_next_round = False

        tcfg = getattr(self, 'triggers_config', None) or {}
        sel_mode = (tcfg.get('selection_mode') or 'rotation').lower()
        # Both 'conditional' (tiebreaker picks one winner) and 'parallel'
        # (every armed strategy bets concurrently) are driven by the same
        # TriggerEngine — the live loop branches on engine.selection_mode.
        # Only plain 'rotation' skips the engine entirely.
        if sel_mode not in ('conditional', 'parallel'):
            # Loud-ish log so users can see WHY the trigger engine isn't
            # built when they expected it to be. Most common cause: bundle
            # wasn't re-loaded after editing, so triggers_config is stale.
            logger.info(f"[Triggers] engine NOT built — selection_mode={sel_mode!r} "
                        f"(needs 'conditional' or 'parallel')")
            return
        triggers = tcfg.get('triggers') or {}
        global_trigger = tcfg.get('global_trigger') or None
        # Engine should activate if EITHER per-strategy triggers OR a global
        # trigger is configured. Bundles using only a single global condition
        # shouldn't have to populate per-strategy rows just to wake it up.
        # Parallel mode in particular usually wants a global {"type":"always"}
        # so every strategy is armed every round.
        if not triggers and global_trigger is None:
            _sect = 'Parallel' if sel_mode == 'parallel' else '🎯 Conditional Selection'
            logger.info(f"[Triggers] engine NOT built — selection_mode={sel_mode!r} "
                        "but no per-strategy triggers and no global_trigger configured. "
                        f"Open Bundle Creator → expand '{_sect}' → set Global Trigger "
                        "(use 'always' so every strategy bets each round).")
            return

        rot_cfg = {
            'strategies':     list(rotation_entries),
            'selection_mode': sel_mode,
            'triggers':       triggers,
            'global_trigger': global_trigger,
            'tiebreaker':     tcfg.get('tiebreaker', 'coldest'),
            'fallback':       tcfg.get('fallback', 'stay'),
        }
        from core.triggers import build_trigger_engine_from_rotation_config
        from core.strategy_engine import StrategyEngine
        engine = build_trigger_engine_from_rotation_config(rot_cfg)
        if engine is None:
            return

        # Cache one read-only StrategyEngine per rotation base name so trigger
        # eval can call .get_bet_labels() without rebuilding on every round.
        base_bet = float(self.config.get("base_bet", 1.0))
        max_loss = float(self.parse_hybrid_value(self.config.get("max_loss", 100.0),
                                                 self.config.get("current_balance", 0)) or 100.0)
        # The global progression dropdown is only a FALLBACK. Each per-strategy
        # engine MUST be built from its FULL rotation entry (e.g.
        # 'ti1:fibonacci|base_bet=0.1|...') so StrategyEngine parses and applies
        # that strategy's OWN progression. Passing only the stripped base name +
        # the global progression made every parallel strategy inherit the global
        # dropdown (DynamicProgression with no rules = flat) — that's the
        # "fibonacci not being followed" bug, and it also silently flattened the
        # ds*:dynamic|rules=... strategies. We mirror _build_live_strategy so a
        # parallel engine is built identically to the single-strategy live path.
        progression_type = self.progression_var.get() if hasattr(self, 'progression_var') else "flat"
        progression_params = self.get_progression_params() if hasattr(self, 'get_progression_params') else {}
        for entry in rotation_entries:
            base = entry.split(":", 1)[0].strip()
            if base in self._trigger_engines_by_base:
                continue
            try:
                # IMPORTANT: max_consec_losses is deliberately NOT read from the
                # global config here. A global cap silently drops ONE leg mid-run
                # while the others keep betting ("only playing 1 strategy"), and
                # the config picks up a stray default of 5 elsewhere. In parallel
                # mode the cap is per-STRATEGY ONLY: encode it in the bundle entry
                # as `|max_consec_losses=N` (Bundle Builder → "Max Consec Losses"),
                # which StrategyEngine parses from the entry string below. Absent
                # ⇒ None ⇒ DISABLED (no cap). Whole-round risk is bounded by
                # per-bet max_bet, each leg's max_loss, and the session/global
                # stop-loss in the bot loop.
                eng = StrategyEngine(
                    strategy_name=entry,  # FULL entry → per-strategy progression/rules/max_consec_losses parsed
                    base_bet=base_bet,
                    max_loss=max_loss,
                    progression_type=progression_type,  # fallback only; entry suffix overrides
                    max_bet=float(self.config.get("max_bet", 100)),
                    custom_strategies=getattr(self, 'custom_strategies', {}),
                    dynamic_rules=progression_params.get('dynamic_rules', []),
                    custom_sequence=progression_params.get('custom_sequence'),
                    dalembert_step=progression_params.get('dalembert_step', 1),
                )
                eng._ranking_simulation = True  # don't gate on license check
                self._trigger_engines_by_base[base] = eng
                logger.info(f"[Parallel] built '{base}' engine: "
                            f"progression={eng.progression.__class__.__name__} "
                            f"(from entry '{entry}')")
            except Exception as e:
                logger.warning(f"[Triggers] could not instantiate '{base}' for label cache: {e}")
        self._trigger_engine = engine
        # Fresh per-leg stop bookkeeping for this bundle session (parallel mode).
        self._parallel_stopped_legs = set()
        logger.info(f"🎯 TriggerEngine active: triggers={list(triggers)}, "
                    f"global={global_trigger}, "
                    f"tiebreaker={engine.tiebreaker_name}, fallback={engine.fallback}")
        self.log_simulation(f"🎯 Conditional triggers active ({len(triggers)} per-strategy + "
                            f"global={'yes' if global_trigger else 'no'}, "
                            f"tiebreaker={engine.tiebreaker_name}, fallback={engine.fallback})")

        # Dump per-strategy label resolution so users can immediately see whether
        # the label-cache engines built with the wrong custom_strategies (in
        # which case labels degrade to the strategy NAME, coverage = 0 numbers,
        # and the trigger can NEVER arm). If you see "coverage 0/37" here, the
        # custom_strategies registry wasn't passed in correctly.
        from core.strategy_engine import ROULETTE_NUMBER_MAPPINGS as _RNM
        for _base, _eng in self._trigger_engines_by_base.items():
            try:
                _labels = list(_eng.get_bet_labels() or [])
                _covered = set()
                for _lbl in _labels:
                    _covered.update(_RNM.get(_lbl, []))
                _preview = ', '.join(_labels[:3]) + ('…' if len(_labels) > 3 else '')
                logger.info(f"[Triggers] {_base}: covers {len(_covered)}/37 numbers "
                            f"({len(_labels)} labels: {_preview})")
            except Exception as _diag_err:
                logger.warning(f"[Triggers] {_base}: label resolution failed: {_diag_err}")

    def _evaluate_trigger_engine(self, current_strategy_name):
        """Run a trigger pick using the cached per-strategy label engines.
        Returns the TriggerDecision, or None if the engine isn't active."""
        engine = getattr(self, '_trigger_engine', None)
        if engine is None:
            return None
        labels_by_name = {}
        for base, eng in (self._trigger_engines_by_base or {}).items():
            try:
                labels_by_name[base] = list(eng.get_bet_labels() or [])
            except Exception:
                labels_by_name[base] = []
        return engine.pick(labels_by_name, current_strategy=current_strategy_name)

    def open_triggers_editor(self):
        """Open the conditional-trigger configuration dialog.

        Edits `self.triggers_config` in place; persisted to the bundle the
        next time the bundle is saved via _build_bundle_data. If the bot is
        running, re-initializes the live trigger engine so changes take effect
        on the next round."""
        try:
            from gui.trigger_editor import TriggerEditorDialog
        except Exception as e:
            from tkinter import messagebox as _mb
            _mb.showerror("Triggers", f"Could not open editor: {e}")
            return

        # Source rotation entries from the current text in the rotation list
        # rather than a stale self.rotation_strategies so the dialog reflects
        # any uncommitted edits the user made to the entry.
        rot_str = self.rotation_strategies_var.get().strip() if hasattr(self, 'rotation_strategies_var') else ""
        entries = [s.strip() for s in rot_str.split(",") if s.strip()]
        if not hasattr(self, 'triggers_config') or not isinstance(self.triggers_config, dict):
            self.triggers_config = {"selection_mode": "rotation", "triggers": {},
                                    "tiebreaker": "coldest", "fallback": "stay"}
        TriggerEditorDialog(self, entries).open()

    def _trigger_feed_winning_number(self, winning_number):
        """Push the latest spin into the trigger history + each label-cache
        engine so adaptive strategies' labels stay in sync."""
        engine = getattr(self, '_trigger_engine', None)
        if engine is None or winning_number is None:
            return
        engine.update(winning_number)
        import inspect as _inspect
        for eng in (self._trigger_engines_by_base or {}).values():
            try:
                inner = getattr(eng, 'strategy', None)
                if inner and hasattr(inner, 'record_result'):
                    sig = _inspect.signature(inner.record_result)
                    if 'last_number' in sig.parameters:
                        inner.record_result(False, last_number=int(winning_number))
            except Exception:
                pass

    def get_next_rotation_strategy(self):
        """Get the next strategy for rotation"""
        if not hasattr(self, 'rotation_strategies') or not self.rotation_strategies:
            return None
        
        # --- SMART RANKING LOGIC (Standard & Reverse) ---
        elif self.rotation_mode in ["smart_ranking", "smart_ranking_reverse"]:
            try:
                # Use db_utils to get history
                from core.utils.db_utils import get_recent_winning_numbers
                history_rows = get_recent_winning_numbers(limit=50)
                history_data = [row['number'] for row in history_rows]
                
                # If insufficient history, fallback to random
                if len(history_data) < 10:
                    self.log_simulation("⚠️ Ranking: Not enough history (<10), falling back to Random.")
                    return random.choice(self.rotation_strategies)
                
                self.log_simulation(f"🧠 Ranking {len(self.rotation_strategies)} strategies against {len(history_data)} past results...")

                # Extract pure strategy names for ranking (strip :progression|config...)
                # Build a mapping from pure name back to the full rotation entry
                pure_names = []
                name_to_full_entry = {}
                for entry in self.rotation_strategies:
                    head = entry.split("|")[0]  # "romanvski1:dynamic"
                    pure_name = head.split(":")[0] if ":" in head else head  # "romanvski1"
                    pure_names.append(pure_name)
                    name_to_full_entry[pure_name] = entry

                # Deduplicate for ranking (same strategy may appear multiple times)
                unique_names = list(dict.fromkeys(pure_names))

                # Instantiate Engine
                engine = RankingEngine(custom_strategies=self.custom_strategies)

                # Get filter preference from UI
                filter_regime = False
                if hasattr(self, 'filter_regime_var'):
                    filter_regime = self.filter_regime_var.get()

                if filter_regime:
                    self.log_simulation("🛡️ Regime Filter ACTIVE. Checking for Compatible Strategies...")

                ranked_results = engine.rank_strategies(unique_names, history_data, filter_by_regime=filter_regime)
                
                if ranked_results:
                    # Log full ranking BEFORE reversing
                    log_msg = f"📊 Full Ranking ({len(ranked_results)} strategies, {self.rotation_mode}):\n"
                    for i, res in enumerate(ranked_results):
                        wr = res.get('win_rate', 0)
                        bets = res.get('bets', 0)
                        pnl = res.get('pnl', 0)
                        log_msg += f"   #{i+1}: {res['name']} | Score: {res['score']:.2f} | WR: {wr:.1%} | Bets: {bets} | PnL: {pnl:.2f}\n"
                    self.log_simulation(log_msg.strip())
                    print(log_msg.strip(), flush=True)

                    # If Reverse Mode: Reverse the list so WORST strategies are at the top (idx 0)
                    if self.rotation_mode == "smart_ranking_reverse":
                        ranked_results.reverse()
                        self.log_simulation("🔄 Reverse Mode: Inverting ranking (Worst -> Best)")

                    # CYCLIC RANKING LOGIC
                    # Initialize index if missing
                    if not hasattr(self, 'smart_ranking_index'):
                        self.smart_ranking_index = 0

                    # Get index
                    idx = self.smart_ranking_index % len(ranked_results)
                    selected_strategy_info = ranked_results[idx]
                    selected_strategy_name = selected_strategy_info['name']

                    # Map ranked name back to full rotation entry
                    full_entry = name_to_full_entry.get(selected_strategy_name, selected_strategy_name)

                    self.log_simulation(f"🔂 Smart Cycle: Picking Rank #{idx+1} of {len(ranked_results)} (index={self.smart_ranking_index})")
                    self.log_simulation(f"🏆 Selected: {selected_strategy_name} (Score: {selected_strategy_info['score']:.2f}) → {full_entry}")
                    print(f"🏆 Picked: {selected_strategy_name} (Rank #{idx+1}, Score: {selected_strategy_info['score']:.2f}, index={self.smart_ranking_index})", flush=True)

                    # Increment index for NEXT time
                    self.smart_ranking_index += 1

                    return full_entry
                else:
                    self.log_simulation("⚠️ Ranking failed to return results. Falling back to Random.")
                    return random.choice(self.rotation_strategies)
                    
            except Exception as e:
                self.log_simulation(f"❌ Ranking Error: {e}")
                print(f"Ranking Error: {e}")
                return random.choice(self.rotation_strategies)

        elif self.rotation_mode == "sequential":
            strategy = self.rotation_strategies[self.current_rotation_index]
            self.current_rotation_index = (self.current_rotation_index + 1) % len(self.rotation_strategies)
            return strategy
        else:  # random
            strategy = random.choice(self.rotation_strategies)
            return strategy

    def apply_rotation_strategy(self):
        """Apply the current rotation strategy to the bot configuration"""
        if not self.enable_strategy_rotation_var.get():
            print(f"[Rotation] apply_rotation_strategy SKIPPED: rotation not enabled")
            return

        # When conditional triggers are active they are the sole strategy
        # selector — both mid-session swaps AND session-start picks. Letting
        # session-end rotation_mode (sequential / random / smart_ranking)
        # pre-pick a strategy here would create two independent selectors
        # fighting each other and produce confusing "why did it switch?"
        # behavior, especially with fallback=stay.
        if getattr(self, '_trigger_engine', None) is not None:
            print("[Rotation] apply_rotation_strategy SKIPPED: TriggerEngine owns selection")
            return

        strategy = self.get_next_rotation_strategy()
        print(f"[Rotation] get_next_rotation_strategy returned: {strategy}")
        if strategy:
            old = self.config.get("strategy", "?")
            self.strategy_var.set(strategy)
            self.config["strategy"] = strategy
            print(f"[Rotation] Strategy changed: {old} → {strategy}")
            
            # Apply progression override if enabled
            if self.rotation_progression_override_var.get() and hasattr(self, 'rotation_strategy_progressions'):
                progression = self.rotation_strategy_progressions.get(strategy)
                if progression:
                    self.progression_var.set(progression)
                    self.config["progression_type"] = progression
                    
                    # Apply strategy-specific settings
                    if hasattr(self, 'rotation_strategy_settings'):
                        settings = self.rotation_strategy_settings.get(strategy, {})
                        if settings:
                            if "base_bet" in settings:
                                try:
                                    base_bet = float(settings["base_bet"])
                                    self.base_bet_var.set(base_bet)
                                    self.config["base_bet"] = base_bet
                                    self.log_simulation(f"📊 Applied strategy-specific base bet: {base_bet}")
                                except ValueError:
                                    pass
                            if "session_length" in settings:
                                try:
                                    session_length = int(settings["session_length"])
                                    self.session_duration_var.set(session_length)
                                    self.config["session_duration_minutes"] = session_length
                                    self.log_simulation(f"⏱️ Applied strategy-specific session length: {session_length} minutes")
                                except ValueError:
                                    pass
                            
                            # Apply stop conditions
                            if "stop_wins" in settings:
                                try:
                                    stop_wins = int(settings["stop_wins"])
                                    self.config["stop_after_wins"] = stop_wins
                                    self.log_simulation(f"🛑 Applied stop condition: {stop_wins} wins")
                                except ValueError:
                                    pass
                            
                            if "stop_losses" in settings:
                                try:
                                    stop_losses = int(settings["stop_losses"])
                                    self.config["stop_after_losses"] = stop_losses
                                    self.log_simulation(f"🛑 Applied stop condition: {stop_losses} losses")
                                except ValueError:
                                    pass
                            
                            if "stop_profit" in settings:
                                try:
                                    stop_profit = float(settings["stop_profit"])
                                    self.config["stop_profit_target"] = stop_profit
                                    self.log_simulation(f"🛑 Applied stop condition: ${stop_profit} profit target")
                                except ValueError:
                                    pass
                            
                            if "stop_loss_limit" in settings:
                                try:
                                    stop_loss_limit = float(settings["stop_loss_limit"])
                                    self.config["stop_loss_limit"] = stop_loss_limit
                                    self.log_simulation(f"🛑 Applied stop condition: ${stop_loss_limit} loss limit")
                                except ValueError:
                                    pass
                            
                            if "stop_time" in settings:
                                try:
                                    stop_time = int(settings["stop_time"])
                                    self.config["stop_time_limit"] = stop_time
                                    self.log_simulation(f"🛑 Applied stop condition: {stop_time} minutes")
                                except ValueError:
                                    pass
                    
                    # Apply progression-specific configuration
                    if hasattr(self, 'rotation_strategy_configs'):
                        config = self.rotation_strategy_configs.get(strategy, {})
                        if config:
                            if progression == "dalembert" and "step" in config:
                                try:
                                    step = float(config["step"])
                                    self.dalembert_step_var.set(step)
                                    self.config["dalembert_step"] = step
                                except ValueError:
                                    pass
                            elif progression == "custom_sequence" and "seq" in config:
                                sequence_str = config["seq"]
                                try:
                                    sequence = [float(x.strip()) for x in sequence_str.split(",") if x.strip()]
                                    self.custom_sequence_var.set(sequence_str)
                                    self.config["custom_sequence"] = sequence
                                except ValueError:
                                    pass
                            elif progression == "dynamic" and "rules" in config:
                                # Parse and apply dynamic rules
                                rules_str = config["rules"]
                                try:
                                    # Parse rules (format: rule1;rule2;rule3)
                                    rules = []
                                    for rule_str in rules_str.split(";"):
                                        if ":" in rule_str:
                                            # Parse individual rule
                                            rule_parts = rule_str.split("|")
                                            event_action = rule_parts[0]
                                            event, action = event_action.split(":", 1)
                                            
                                            rule = {"on": event, "action": action}
                                            
                                            # Parse additional parameters
                                            for part in rule_parts[1:]:
                                                if "=" in part:
                                                    key, value = part.split("=", 1)
                                                    if key == "condition":
                                                        rule["condition"] = value
                                                    elif key == "seq":
                                                        try:
                                                            rule["sequence"] = [float(x.strip()) for x in value.split(",") if x.strip()]
                                                        except ValueError:
                                                            rule["sequence"] = [1]
                                                    elif key == "step":
                                                        try:
                                                            rule["step"] = int(value)
                                                        except ValueError:
                                                            rule["step"] = 1
                                            
                                            rules.append(rule)
                                    
                                    # Update dynamic rules
                                    self.dynamic_rules = rules
                                    self.config["dynamic_rules"] = rules
                                    self.log_simulation(f"🔄 Applied dynamic rules: {len(rules)} rules")
                                except Exception as e:
                                    self.log_simulation(f"⚠️ Error parsing dynamic rules: {e}")
                                    print(f"⚠️ Error parsing dynamic rules: {e}")
                    
                    self.log_simulation(f"🔄 Applied rotation strategy: {strategy} with {progression} progression")
                    print(f"🔄 Applied rotation strategy: {strategy} with {progression} progression")
                else:
                    self.log_simulation(f"🔄 Applied rotation strategy: {strategy} (using main progression)")
                    print(f"🔄 Applied rotation strategy: {strategy} (using main progression)")
            else:
                self.log_simulation(f"🔄 Applied rotation strategy: {strategy} (using main progression)")
                print(f"🔄 Applied rotation strategy: {strategy} (using main progression)")

    def _on_switch_on_loss_toggled(self):
        """Show/hide the loss-count and carry-progression controls based on the checkbox."""
        enabled = self.switch_on_loss_var.get()
        self.rotation_trigger_var.set("on_loss" if enabled else "session_end")
        state = "normal" if enabled else "disabled"
        dim_color = "#A1A1AA" if enabled else "#52525B"
        self.switch_after_n_losses_entry.configure(state=state)
        self.carry_progression_check.configure(state=state)
        # Dim labels when disabled
        if hasattr(self, 'switch_after_n_label'):
            self.switch_after_n_label.configure(text_color=dim_color)
        if hasattr(self, 'switch_after_n_suffix'):
            self.switch_after_n_suffix.configure(text_color=dim_color)
        if hasattr(self, 'carry_progression_hint'):
            self.carry_progression_hint.configure(text_color="#64748B" if enabled else "#3F3F46")

    # ── Round-Boundary Strategy Swap (favorites pills / hotkeys / Telegram) ──
    # Triggers (pill click, hotkey, Telegram callback) set pending_strategy_swap;
    # the bot loop applies it right after a result resolves, so progression state
    # is never corrupted mid-bet. last_strategy_swap tracks the previous active
    # strategy so the Ctrl+` toggle can flip back to it.

    def request_strategy_swap(self, name: str):
        """Queue a strategy swap to apply at the next round boundary.

        Accepts both plain strategy names ("martingale", "petermaster") and the
        rotation-string format StrategyEngine understands
        ("expandingStreetP1:flat|base_bet=0.1|session_length=1"). Validation
        and comparison are done on the BASE name (everything before the first
        colon), matching how StrategyEngine.__init__ parses it.
        """
        if not name:
            return
        base_name = name.split(":", 1)[0].strip() if isinstance(name, str) else str(name)

        # Validate the target exists. Built-ins are always available; everything
        # else must be a registered custom strategy.
        if base_name not in {"martingale", "flat"} and base_name not in (self.config.get("custom_strategies") or {}):
            if hasattr(self, "log_to_dashboard"):
                self.log_to_dashboard(f"⚠️  Cannot swap to '{base_name}' — strategy not found.")
            return

        # Compare against the LIVE engine's base name, not config["strategy"].
        # The bundle-swap path updates config["strategy"] before calling us, so
        # comparing to config would always match and silently skip the rebuild —
        # leaving the bot running the old engine. Fall back to config only if
        # no engine is live. StrategyEngine.strategy_name is already the cleaned
        # base name (see __init__ parsing), so no split needed on that side.
        live = getattr(self, "_live_strategy", None)
        current = (getattr(live, "strategy_name", None) if live is not None
                   else (self.config.get("strategy") or "").split(":", 1)[0])
        if base_name == current:
            return  # Already running this strategy
        self.pending_strategy_swap = name  # keep full string — engine parses it
        if hasattr(self, "set_status"):
            self.set_status(f"🔄 Switching to {base_name} after this round...")
        if hasattr(self, "log_to_dashboard"):
            self.log_to_dashboard(f"⏳ Queued swap: {current} → {base_name} (applies end of round)")

    def _apply_pending_strategy_swap(self, old_strategy):
        """If a swap is pending, rebuild the StrategyEngine and return the new one.
        Returns old_strategy unchanged when nothing is queued or the rebuild fails."""
        target = getattr(self, "pending_strategy_swap", None)
        if not target:
            return old_strategy
        self.pending_strategy_swap = None  # Consume the request even on failure

        try:
            old_name = self.config.get("strategy")
            self.config["strategy"] = target
            if hasattr(self, "strategy_var"):
                try:
                    self.strategy_var.set(target)
                except Exception:
                    pass

            progression_type = self.progression_var.get() if hasattr(self, "progression_var") else "flat"
            progression_params = self.get_progression_params() if hasattr(self, "get_progression_params") else {}

            new_strategy = StrategyEngine(
                strategy_name=target,
                base_bet=float(self.config["base_bet"]),
                max_loss=getattr(self, "active_session_loss_limit", float(self.config.get("max_loss", 100))),
                progression_type=progression_type,
                max_bet=float(self.config.get("max_bet", 100)),
                max_consec_losses=None,  # per-strategy only: absent ⇒ disabled. A |max_consec_losses=N suffix in strategy_name still overrides.
                custom_strategies=self.custom_strategies,
                dynamic_rules=progression_params.get("dynamic_rules", []),
                custom_sequence=progression_params.get("custom_sequence"),
                dalembert_step=progression_params.get("dalembert_step", 1),
                observation_trigger=int(self.config.get("observation_trigger", 0)),
            )

            # Carry over progression state when the user has opted in. Default behavior
            # is reset-to-base — fresh strategy starts at its own base bet.
            carry = (self.carry_progression_var.get()
                     if hasattr(self, "carry_progression_var") else False)
            if carry and old_strategy is not None:
                try:
                    new_strategy.progression = old_strategy.progression
                    new_strategy.consecutive_losses = getattr(old_strategy, "consecutive_losses", 0)
                    new_strategy.total_loss = getattr(old_strategy, "total_loss", 0.0)
                    mode = "carried over"
                except Exception:
                    mode = "reset (carry failed)"
            else:
                mode = "reset to base"

            self._live_strategy = new_strategy
            self.last_strategy_swap = old_name  # for Ctrl+` toggle
            self.update_hud_safe(strategy_name=target)

            try:
                next_bet = new_strategy.progression.get_current_bet()
                bet_str = f"${next_bet:.2f}"
            except Exception:
                bet_str = "?"
            msg = f"🔄 Swapped: {old_name} → {target} | Progression {mode} (next bet: {bet_str})"
            if hasattr(self, "log_simulation"):
                self.log_simulation(msg)
            if hasattr(self, "log_to_dashboard"):
                self.log_to_dashboard(msg)
            logger.info(msg)
            return new_strategy
        except Exception as e:
            logger.error(f"Strategy swap to '{target}' failed: {e}")
            if hasattr(self, "log_to_dashboard"):
                self.log_to_dashboard(f"⚠️  Swap to '{target}' failed: {e}")
            return old_strategy

    def rebuild_strategy_on_loss(self, old_strategy):
        """
        Rebuild the StrategyEngine mid-session after a loss when rotation_trigger == 'on_loss'.
        Only swaps the strategy (bet labels/coverage) — the progression object is carried over
        so the bet sizing state (martingale step, fibonacci index, etc.) continues seamlessly.
        Returns the new StrategyEngine instance (or the old one if rotation is not applicable).
        """
        if not self.enable_strategy_rotation_var.get():
            return old_strategy
        if getattr(self, 'rotation_trigger', 'session_end') != 'on_loss':
            return old_strategy

        # Get next strategy via normal rotation logic (sequential/random/smart)
        self.apply_rotation_strategy()

        # Rebuild StrategyEngine with the new strategy name (changes coverage/labels only)
        new_strategy_name = self.config["strategy"]
        progression_type = self.progression_var.get()
        progression_params = self.get_progression_params()

        new_strategy = StrategyEngine(
            strategy_name=new_strategy_name,
            base_bet=float(self.config["base_bet"]),
            max_loss=self.active_session_loss_limit,
            progression_type=progression_type,
            max_bet=float(self.config.get("max_bet", 100)),
            max_consec_losses=None,  # per-strategy only: absent ⇒ disabled. A |max_consec_losses=N suffix in strategy_name still overrides.
            custom_strategies=self.custom_strategies,
            dynamic_rules=progression_params.get('dynamic_rules', []),
            custom_sequence=progression_params.get('custom_sequence'),
            dalembert_step=progression_params.get('dalembert_step', 1),
            observation_trigger=int(self.config.get("observation_trigger", 0))
        )

        # --- PROGRESSION HANDLING ---
        carry_progression = self.carry_progression_var.get()
        if carry_progression:
            # Carry over: keep the same progression object so bet sizing continues
            new_strategy.progression = old_strategy.progression
            new_strategy.consecutive_losses = old_strategy.consecutive_losses
            new_strategy.total_loss = old_strategy.total_loss
            mode_label = "carried over"
        else:
            # Fresh start: new strategy begins at its own base bet
            mode_label = "reset to base"

        self._live_strategy = new_strategy
        self.update_hud_safe(strategy_name=new_strategy_name)
        next_bet = new_strategy.progression.get_current_bet()
        self.log_simulation(f"🔀 Switch-on-loss: Rotated to '{new_strategy_name}' | Progression {mode_label} (next bet: ${next_bet:.2f}, consec losses: {new_strategy.consecutive_losses})")
        logger.info(f"🔀 Switch-on-loss: Rotated to '{new_strategy_name}' | Progression {mode_label}")

        return new_strategy

    def _searchable_bundle_picker(self, parent, title="Load Bundle"):
        """Modal type-to-search bundle picker.

        Returns the selected bundle file path, or None if cancelled. Typing in
        the search box filters the list live by THREE tiers (best first):
          0) prefix match           ("6st" -> 6streetstratbundle)
          1) substring match        ("street" -> 6streetstratbundle)
          2) fuzzy subsequence       ("6sb" / "ssb" -> 6streetstratbundle)
        Up/Down navigate, Enter / double-click loads, Esc cancels. Scans
        ~/.spinedge/bundles for *.json and *.spine, deduped by name (prefers
        the editable .json over the encrypted .spine).
        """
        import os
        import glob

        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        os.makedirs(bundles_dir, exist_ok=True)
        by_name = {}
        for ext in ("*.json", "*.spine"):
            for p in glob.glob(os.path.join(bundles_dir, ext)):
                name = os.path.splitext(os.path.basename(p))[0]
                # Prefer the editable .json when both exist for the same name.
                if name not in by_name or p.endswith(".json"):
                    by_name[name] = p
        items = sorted(by_name.items(), key=lambda kv: kv[0].lower())  # [(name, path)]

        result = {"path": None}

        win = ctk.CTkToplevel(parent)
        win.title(title)
        win.geometry("460x540")
        win.configure(fg_color="#09090B")
        win.transient(parent)
        try:
            win.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(win, text=title, font=("Segoe UI", 15, "bold"),
                     text_color="#EAB308").pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(win, text="Type to filter — initials or any letters (e.g. '6sb' → 6streetstratbundle)",
                     font=("Segoe UI", 10), text_color="#A1A1AA").pack(anchor="w", padx=16, pady=(0, 8))

        search_var = tk.StringVar()
        entry = ctk.CTkEntry(win, textvariable=search_var, height=34, corner_radius=8,
                             fg_color="#18181B", border_color="#3F3F46",
                             placeholder_text="Search bundles…")
        entry.pack(fill="x", padx=16, pady=(0, 8))

        list_frame = ctk.CTkFrame(win, fg_color="#18181B", corner_radius=8)
        list_frame.pack(fill="both", expand=True, padx=16, pady=(0, 4))
        scrollbar = ctk.CTkScrollbar(list_frame)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)
        listbox = tk.Listbox(list_frame, bg="#18181B", fg="#E4E4E7", borderwidth=0,
                             highlightthickness=0, selectbackground="#EAB308",
                             selectforeground="#09090B", font=("Segoe UI", 12),
                             activestyle="none", yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        scrollbar.configure(command=listbox.yview)

        count_lbl = ctk.CTkLabel(win, text="", font=("Segoe UI", 10), text_color="#64748B")
        count_lbl.pack(anchor="w", padx=16, pady=(0, 4))

        filtered = []  # (name, path) currently shown, in display order

        def _rank(query, name):
            """Lower rank = better match. None = no match."""
            q = query.lower().strip()
            if not q:
                return 3  # neutral: show everything
            n = name.lower()
            if n.startswith(q):
                return 0
            if q in n:
                return 1
            it = iter(n)  # subsequence test (fuzzy / initials)
            if all(ch in it for ch in q):
                return 2
            return None

        def _refresh(*_):
            q = search_var.get()
            ranked = []
            for name, path in items:
                r = _rank(q, name)
                if r is not None:
                    ranked.append((r, name.lower(), name, path))
            ranked.sort(key=lambda t: (t[0], t[1]))
            filtered.clear()
            listbox.delete(0, tk.END)
            for _r, _nl, name, path in ranked:
                filtered.append((name, path))
                listbox.insert(tk.END, name)
            count_lbl.configure(text=f"{len(filtered)} of {len(items)} bundles")
            if filtered:
                listbox.selection_clear(0, tk.END)
                listbox.selection_set(0)
                listbox.see(0)

        def _accept(*_):
            sel = listbox.curselection()
            if not sel and filtered:
                sel = (0,)
            if sel:
                result["path"] = filtered[sel[0]][1]
                win.destroy()
            return "break"

        def _cancel(*_):
            result["path"] = None
            win.destroy()
            return "break"

        def _move(delta):
            if not filtered:
                return "break"
            sel = listbox.curselection()
            i = (sel[0] if sel else 0) + delta
            i = max(0, min(len(filtered) - 1, i))
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(i)
            listbox.see(i)
            return "break"

        search_var.trace_add("write", _refresh)
        entry.bind("<Down>", lambda e: _move(1))
        entry.bind("<Up>", lambda e: _move(-1))
        entry.bind("<Return>", _accept)
        entry.bind("<Escape>", _cancel)
        listbox.bind("<Double-Button-1>", _accept)
        listbox.bind("<Return>", _accept)
        listbox.bind("<Escape>", _cancel)
        win.protocol("WM_DELETE_WINDOW", _cancel)

        footer = ctk.CTkFrame(win, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(footer, text="Load", width=90, command=_accept, fg_color="#2980b9",
                      hover_color="#3498db", height=32, corner_radius=6).pack(side="right", padx=(6, 0))
        ctk.CTkButton(footer, text="Cancel", width=80, command=_cancel, fg_color="#475569",
                      hover_color="#64748B", height=32, corner_radius=6).pack(side="right")

        _refresh()
        # CTkToplevel on Windows occasionally needs a beat before focus/lift land.
        win.after(120, lambda: (win.lift(), entry.focus_force()))
        parent.wait_window(win)
        return result["path"]

    def add_rotation_strategy_dialog(self):
        """Modern CTk dialog for building strategy rotation bundles."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Bundle & Rotation Builder")
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.geometry("820x760")
        dialog.configure(fg_color="#09090B")

        # ── Edit tracking ──────────────────────────────────────────────
        _editing_path = tk.StringVar(value="")   # File path of bundle being edited
        _editing_name = tk.StringVar(value="")   # Bundle name being edited

        # ── Shared state ────────────────────────────────────────────
        available_strategies = ["martingale", "flat"] + list(self.custom_strategies.keys())
        strategy_var = tk.StringVar(value=available_strategies[0] if available_strategies else "")
        progression_var = tk.StringVar(value="flat")
        base_bet_var = tk.DoubleVar(value=self.base_bet_var.get())
        session_length_var = tk.IntVar(value=self.session_duration_var.get())
        def _safe_float(val, default=0.0):
            """Parse a config value that might be a number, '2%', or other string."""
            if isinstance(val, (int, float)):
                return float(val)
            try:
                return float(str(val).replace("%", "").strip())
            except (ValueError, TypeError):
                return default

        max_bet_var = tk.DoubleVar(value=_safe_float(self.config.get("max_bet", 100.0), 100.0))
        max_loss_var = tk.StringVar(value=str(_safe_float(self.config.get("max_loss", 100.0), 100.0)))
        num_sessions_var = tk.IntVar(value=int(_safe_float(self.config.get("num_sessions", 1), 1)))
        min_gap_var = tk.IntVar(value=int(_safe_float(self.config.get("min_gap_minutes", 30), 30)))
        max_gap_var = tk.IntVar(value=int(_safe_float(self.config.get("max_gap_minutes", 120), 120)))
        profit_target_var = tk.DoubleVar(value=_safe_float(self.config.get("profit_target", 0)))
        trailing_stop_on = tk.BooleanVar(value=bool(self.config.get("enable_trailing_stop", False)))
        trailing_stop_amt = tk.DoubleVar(value=_safe_float(self.config.get("trailing_stop_amount", 0)))
        ext_after_win_var = tk.BooleanVar(value=bool(self.config.get("session_ext_after_win", False)))
        ext_at_high_var = tk.BooleanVar(value=bool(self.config.get("session_ext_at_high", False)))
        max_ext_rounds_var = tk.IntVar(value=int(_safe_float(self.config.get("max_extension_rounds", 20), 20)))
        ext_give_up_var = tk.DoubleVar(value=_safe_float(self.config.get("extension_give_up_amount", 50.0), 50.0))
        global_stop_on = tk.BooleanVar(value=bool(self.config.get("enable_global_stop", False)))
        global_profit_var = tk.DoubleVar(value=_safe_float(self.config.get("global_profit_stop", 0)))
        global_loss_var = tk.DoubleVar(value=_safe_float(self.config.get("global_stop_loss", 0)))
        # Escalation-on-loss (snapshot from current main config)
        escalation_on = tk.BooleanVar(value=bool(self.config.get("enable_escalation_on_loss", False)))
        escalation_mult_var = tk.DoubleVar(value=_safe_float(self.config.get("escalation_multiplier", 2.0), 2.0))
        escalation_steps_var = tk.IntVar(value=int(_safe_float(self.config.get("escalation_max_steps", 4), 4)))
        escalation_per_step_var = tk.StringVar(value=str(self.config.get("escalation_per_step", "") or ""))
        # Per-strategy consecutive-loss cap (0 = disabled). Serialized into the
        # entry as `|max_consec_losses=N`; StrategyEngine parses it and treats
        # 0/None as off. Distinct from the session "After consecutive losses"
        # stop — this silences just THIS strategy's progression at N losses.
        # Defaults to 0 (NOT the global config) — it's a per-strategy override,
        # so "no value" means disabled, and the global config's stale value must
        # not leak in (that made a saved 0 reappear as 5 on reopen).
        max_consec_losses_var = tk.IntVar(value=0)
        use_global_settings_var = tk.BooleanVar(value=True)
        use_global_stops_var = tk.BooleanVar(value=True)
        stop_wins_on = tk.BooleanVar(); stop_wins_val = tk.IntVar(value=0)
        stop_losses_on = tk.BooleanVar(); stop_losses_val = tk.IntVar(value=0)
        stop_profit_on = tk.BooleanVar(); stop_profit_val = tk.DoubleVar(value=0.0)
        stop_loss_on = tk.BooleanVar(); stop_loss_val = tk.DoubleVar(value=0.0)
        stop_time_on = tk.BooleanVar(); stop_time_val = tk.IntVar(value=0)
        dalembert_step_type_var = tk.StringVar(value="Base Bet Multiplier")
        dalembert_step_var = tk.DoubleVar(value=1.0)
        custom_sequence_var = tk.StringVar(value="1,2,3,4,5")
        dynamic_rules_var = tk.StringVar(value="")

        _lbl = dict(font=("Segoe UI", 11), text_color="#A1A1AA")
        _lbl_bold = dict(font=("Segoe UI", 11, "bold"), text_color="#E4E4E7")
        _entry_kw = dict(height=30, corner_radius=6, fg_color="#18181B", border_color="#3F3F46")
        _btn_sm = dict(height=30, corner_radius=6, font=("Segoe UI", 11))

        # ── Title ───────────────────────────────────────────────────
        ctk.CTkLabel(dialog, text="Bundle & Rotation Builder", font=("Segoe UI", 16, "bold"), text_color="#EAB308").pack(anchor="w", padx=20, pady=(12, 4))
        ctk.CTkLabel(dialog, text="Build a suite of strategies, configure progression per-strategy, then save as a bundle.", **_lbl).pack(anchor="w", padx=20, pady=(0, 8))

        # ── Content split ───────────────────────────────────────────
        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=15, pady=(0, 5))
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(content, fg_color="#18181B", corner_radius=10, label_text="Strategy Configuration", label_fg_color="#27272A", label_font=("Segoe UI", 12, "bold"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        right = ctk.CTkFrame(content, fg_color="#18181B", corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right.rowconfigure(1, weight=1)

        # ═══════════════ LEFT: Config ═══════════════════════════════

        # Step 1: Strategy + Progression
        ctk.CTkLabel(left, text="Step 1: Base Strategy", **_lbl_bold).pack(anchor="w", padx=10, pady=(8, 4))

        row1 = ctk.CTkFrame(left, fg_color="transparent")
        row1.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(row1, text="Strategy:", width=100, **_lbl).pack(side="left")
        strategy_combo = ctk.CTkComboBox(row1, variable=strategy_var, values=available_strategies, state="readonly", **_entry_kw)
        strategy_combo.pack(side="left", fill="x", expand=True, padx=(5, 0))
        # Type-to-search the (often long) custom-strategy list: prefix/substring/
        # initials. _make_combobox_searchable flips the box to editable and
        # filters the dropdown as you type.
        self._builder_strategy_master = list(available_strategies)
        self._make_combobox_searchable(strategy_combo, "_builder_strategy_master")

        row2 = ctk.CTkFrame(left, fg_color="transparent")
        row2.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(row2, text="Progression:", width=100, **_lbl).pack(side="left")
        progression_combo = ctk.CTkComboBox(row2, variable=progression_var, values=["flat", "martingale", "fibonacci", "dalembert", "custom_sequence", "dynamic"], state="readonly", **_entry_kw)
        progression_combo.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Step 2: Settings & Limits
        ctk.CTkLabel(left, text="Step 2: Settings & Limits", **_lbl_bold).pack(anchor="w", padx=10, pady=(12, 4))

        chk_global = ctk.CTkCheckBox(left, text="Use global base bet, session & max consec losses (uncheck to set per-strategy)", variable=use_global_settings_var, font=("Segoe UI", 11))
        chk_global.pack(anchor="w", padx=14, pady=2)

        settings_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)
        settings_frame.pack(fill="x", padx=10, pady=4)

        _hint = dict(font=("Segoe UI", 11), text_color="#64748B")

        def _make_setting_row(parent, label_text, var, width=80, hint=None):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=8, pady=3)
            ctk.CTkLabel(r, text=label_text, width=140, **_lbl).pack(side="left")
            ctk.CTkEntry(r, textvariable=var, width=width, **_entry_kw).pack(side="left")
            if hint:
                ctk.CTkLabel(r, text=hint, **_hint).pack(side="left", padx=(5, 0))
            return r

        _make_setting_row(settings_frame, "Base Bet ($):", base_bet_var)
        _make_setting_row(settings_frame, "Max Bet Cap ($):", max_bet_var, hint="(safety cap per spin)")
        _make_setting_row(settings_frame, "Session Stop Loss ($):", max_loss_var, hint="(max loss per session)")
        _make_setting_row(settings_frame, "Session (min):", session_length_var)
        _make_setting_row(settings_frame, "Max Consec Losses:", max_consec_losses_var, hint="(0 = off; stops THIS strategy after N losses)")
        _make_setting_row(settings_frame, "Num Sessions:", num_sessions_var)
        _make_setting_row(settings_frame, "Profit Target ($):", profit_target_var, hint="(0 = off)")

        # Gap row (two entries on one line)
        r_gap = ctk.CTkFrame(settings_frame, fg_color="transparent")
        r_gap.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(r_gap, text="Session Gap (min):", width=140, **_lbl).pack(side="left")
        ctk.CTkEntry(r_gap, textvariable=min_gap_var, width=55, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_gap, text=" – ", **_lbl).pack(side="left")
        ctk.CTkEntry(r_gap, textvariable=max_gap_var, width=55, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_gap, text="(random gap)", **_hint).pack(side="left", padx=(5, 0))

        def _toggle_settings():
            if use_global_settings_var.get():
                settings_frame.pack_forget()
            else:
                settings_frame.pack(fill="x", padx=10, pady=4, after=chk_global)
        chk_global.configure(command=_toggle_settings)
        _toggle_settings()

        # ── Session Behavior (collapsible, collapsed by default) ──────
        _behavior_expanded = tk.BooleanVar(value=False)

        behavior_header = ctk.CTkFrame(left, fg_color="transparent")
        behavior_header.pack(fill="x", padx=10, pady=(10, 0))
        behavior_toggle_btn = ctk.CTkButton(behavior_header, text="+ Session Behavior", anchor="w",
            fg_color="transparent", hover_color="#27272A", text_color="#E4E4E7",
            font=("Segoe UI", 11, "bold"), height=28, corner_radius=6)
        behavior_toggle_btn.pack(fill="x")

        behavior_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)
        # (not packed yet — collapsed by default)

        ctk.CTkLabel(behavior_frame, text="Controls when a session ends. Defaults are safe — leave as-is if unsure.", **_hint).pack(anchor="w", padx=8, pady=(6, 2))

        r_ts = ctk.CTkFrame(behavior_frame, fg_color="transparent")
        r_ts.pack(fill="x", padx=8, pady=3)
        ctk.CTkCheckBox(r_ts, text="Trailing Stop", variable=trailing_stop_on, width=150, font=("Segoe UI", 12)).pack(side="left")
        ctk.CTkEntry(r_ts, textvariable=trailing_stop_amt, width=70, **_entry_kw).pack(side="left", padx=(5, 0))
        ctk.CTkLabel(r_ts, text="$  (0 = off)", **_hint).pack(side="left", padx=(5, 0))

        r_ext1 = ctk.CTkFrame(behavior_frame, fg_color="transparent")
        r_ext1.pack(fill="x", padx=8, pady=3)
        ctk.CTkCheckBox(r_ext1, text="End only after a win", variable=ext_after_win_var, font=("Segoe UI", 12)).pack(side="left")

        r_ext2 = ctk.CTkFrame(behavior_frame, fg_color="transparent")
        r_ext2.pack(fill="x", padx=8, pady=3)
        ctk.CTkCheckBox(r_ext2, text="End only at session high", variable=ext_at_high_var, font=("Segoe UI", 12)).pack(side="left")

        r_ext3 = ctk.CTkFrame(behavior_frame, fg_color="transparent")
        r_ext3.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(r_ext3, text="Max Extension Rounds:", width=160, **_lbl).pack(side="left")
        ctk.CTkEntry(r_ext3, textvariable=max_ext_rounds_var, width=60, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_ext3, text="(default 20)", **_hint).pack(side="left", padx=(5, 0))

        r_ext4 = ctk.CTkFrame(behavior_frame, fg_color="transparent")
        r_ext4.pack(fill="x", padx=8, pady=(3, 8))
        ctk.CTkLabel(r_ext4, text="Extension Give-Up ($):", width=160, **_lbl).pack(side="left")
        ctk.CTkEntry(r_ext4, textvariable=ext_give_up_var, width=70, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_ext4, text="(default 50)", **_hint).pack(side="left", padx=(5, 0))

        def _toggle_behavior():
            if _behavior_expanded.get():
                behavior_frame.pack_forget()
                behavior_toggle_btn.configure(text="+ Session Behavior")
                _behavior_expanded.set(False)
            else:
                behavior_frame.pack(fill="x", padx=10, pady=4, after=behavior_header)
                behavior_toggle_btn.configure(text="- Session Behavior")
                _behavior_expanded.set(True)
        behavior_toggle_btn.configure(command=_toggle_behavior)

        # ── Global Safety Net (collapsible, collapsed by default) ─────
        _global_expanded = tk.BooleanVar(value=False)

        global_header = ctk.CTkFrame(left, fg_color="transparent")
        global_header.pack(fill="x", padx=10, pady=(6, 0))
        global_toggle_btn = ctk.CTkButton(global_header, text="+ Global Safety Net", anchor="w",
            fg_color="transparent", hover_color="#27272A", text_color="#E4E4E7",
            font=("Segoe UI", 11, "bold"), height=28, corner_radius=6)
        global_toggle_btn.pack(fill="x")

        global_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)
        # (not packed yet — collapsed by default)

        ctk.CTkLabel(global_frame, text="Stops across ALL sessions. Leave off unless you want a hard bankroll limit.", **_hint).pack(anchor="w", padx=8, pady=(6, 2))

        r_gen = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_gen.pack(fill="x", padx=8, pady=3)
        ctk.CTkCheckBox(r_gen, text="Enable Global Stops", variable=global_stop_on, font=("Segoe UI", 12)).pack(side="left")

        r_gp = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_gp.pack(fill="x", padx=8, pady=3)
        ctk.CTkLabel(r_gp, text="Profit Stop ($):", width=140, **_lbl).pack(side="left")
        ctk.CTkEntry(r_gp, textvariable=global_profit_var, width=70, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_gp, text="(0 = no limit)", **_hint).pack(side="left", padx=(5, 0))

        r_gl = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_gl.pack(fill="x", padx=8, pady=(3, 8))
        ctk.CTkLabel(r_gl, text="Stop Loss ($):", width=140, **_lbl).pack(side="left")
        ctk.CTkEntry(r_gl, textvariable=global_loss_var, width=70, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_gl, text="(0 = no limit)", **_hint).pack(side="left", padx=(5, 0))

        # ── Escalation on Session Stop-Loss ─────────────────────────────────
        ctk.CTkLabel(global_frame, text="After a session stop-loss, multiply base bet & SL.\nResets on global / session profit target.", **_hint).pack(anchor="w", padx=8, pady=(6, 2))

        r_esc = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_esc.pack(fill="x", padx=8, pady=3)
        ctk.CTkCheckBox(r_esc, text="Escalate on Session SL", variable=escalation_on, font=("Segoe UI", 12)).pack(side="left")

        r_esc2 = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_esc2.pack(fill="x", padx=8, pady=(3, 4))
        ctk.CTkLabel(r_esc2, text="× Multiplier:", width=140, **_lbl).pack(side="left")
        ctk.CTkEntry(r_esc2, textvariable=escalation_mult_var, width=60, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_esc2, text="Max steps:", width=80, **_lbl).pack(side="left", padx=(10, 0))
        ctk.CTkEntry(r_esc2, textvariable=escalation_steps_var, width=50, **_entry_kw).pack(side="left")

        r_esc3 = ctk.CTkFrame(global_frame, fg_color="transparent")
        r_esc3.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(r_esc3, text="Per-step ×:", width=140, **_lbl).pack(side="left")
        ctk.CTkEntry(r_esc3, textvariable=escalation_per_step_var, width=180, **_entry_kw).pack(side="left")
        ctk.CTkLabel(r_esc3, text='(CSV like "2,3,5,10" — overrides Multiplier)', **_hint).pack(side="left", padx=(8, 0))

        def _toggle_global():
            if _global_expanded.get():
                global_frame.pack_forget()
                global_toggle_btn.configure(text="+ Global Safety Net")
                _global_expanded.set(False)
            else:
                global_frame.pack(fill="x", padx=10, pady=4, after=global_header)
                global_toggle_btn.configure(text="- Global Safety Net")
                _global_expanded.set(True)
        global_toggle_btn.configure(command=_toggle_global)

        # ── Per-Strategy Stop Conditions ──────────────────────────────
        chk_stops = ctk.CTkCheckBox(left, text="Use global stop conditions (per-strategy)", variable=use_global_stops_var, font=("Segoe UI", 11))
        chk_stops.pack(anchor="w", padx=14, pady=(8, 2))

        stops_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)

        def _make_stop_row(parent, label, on_var, val_var, width=70):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=8, pady=2)
            ctk.CTkCheckBox(r, text=label, variable=on_var, width=180, font=("Segoe UI", 12)).pack(side="left")
            ctk.CTkEntry(r, textvariable=val_var, width=width, **_entry_kw).pack(side="left", padx=(5, 0))

        _make_stop_row(stops_frame, "After consecutive wins:", stop_wins_on, stop_wins_val)
        _make_stop_row(stops_frame, "After consecutive losses:", stop_losses_on, stop_losses_val)
        _make_stop_row(stops_frame, "At profit target ($):", stop_profit_on, stop_profit_val)
        _make_stop_row(stops_frame, "At loss limit ($):", stop_loss_on, stop_loss_val)
        _make_stop_row(stops_frame, "After time (min):", stop_time_on, stop_time_val)

        def _toggle_stops():
            if use_global_stops_var.get():
                stops_frame.pack_forget()
            else:
                stops_frame.pack(fill="x", padx=10, pady=4, after=chk_stops)
        chk_stops.configure(command=_toggle_stops)
        _toggle_stops()

        # Step 3: Progression Tuning
        ctk.CTkLabel(left, text="Step 3: Progression Tuning", **_lbl_bold).pack(anchor="w", padx=10, pady=(12, 4))
        tuning_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)
        tuning_frame.pack(fill="x", padx=10, pady=4)

        # D'Alembert widgets
        dal_type_row = ctk.CTkFrame(tuning_frame, fg_color="transparent")
        ctk.CTkLabel(dal_type_row, text="Step Type:", width=100, **_lbl).pack(side="left")
        dal_type_combo = ctk.CTkComboBox(dal_type_row, variable=dalembert_step_type_var, values=["Base Bet Multiplier", "Custom Unit ($)"], state="readonly", **_entry_kw)
        dal_type_combo.pack(side="left", fill="x", expand=True, padx=(5, 0))

        dal_val_row = ctk.CTkFrame(tuning_frame, fg_color="transparent")
        ctk.CTkLabel(dal_val_row, text="Step Value:", width=100, **_lbl).pack(side="left")
        ctk.CTkEntry(dal_val_row, textvariable=dalembert_step_var, width=80, **_entry_kw).pack(side="left")

        # Custom sequence widgets
        seq_row = ctk.CTkFrame(tuning_frame, fg_color="transparent")
        ctk.CTkLabel(seq_row, text="Sequence:", width=100, **_lbl).pack(side="left")
        ctk.CTkEntry(seq_row, textvariable=custom_sequence_var, **_entry_kw).pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Dynamic rules widgets
        dyn_row = ctk.CTkFrame(tuning_frame, fg_color="transparent")
        ctk.CTkLabel(dyn_row, text="Rules:", width=60, **_lbl).pack(side="left")
        ctk.CTkEntry(dyn_row, textvariable=dynamic_rules_var, **_entry_kw).pack(side="left", fill="x", expand=True, padx=(5, 5))
        ctk.CTkButton(dyn_row, text="Add", width=50, command=lambda: self.add_dynamic_rule_dialog(dynamic_rules_var), fg_color="#475569", **_btn_sm).pack(side="left", padx=2)
        ctk.CTkButton(dyn_row, text="View", width=50, command=lambda: self.view_dynamic_rules_dialog(dynamic_rules_var), fg_color="#475569", **_btn_sm).pack(side="left")

        dyn_seq_row = ctk.CTkFrame(tuning_frame, fg_color="transparent")
        ctk.CTkLabel(dyn_seq_row, text="Fallback Seq:", width=100, **_lbl).pack(side="left")
        ctk.CTkEntry(dyn_seq_row, textvariable=custom_sequence_var, **_entry_kw).pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Empty label for non-tunable progressions
        no_tuning_label = ctk.CTkLabel(tuning_frame, text="No extra tuning needed for this progression.", **_lbl)

        def _update_tuning(*_args):
            # Hide all
            for w in (dal_type_row, dal_val_row, seq_row, dyn_row, dyn_seq_row, no_tuning_label):
                w.pack_forget()
            prog = progression_var.get()
            if prog == "dalembert":
                dal_type_row.pack(fill="x", padx=8, pady=4)
                _update_dal_val()
            elif prog == "custom_sequence":
                seq_row.pack(fill="x", padx=8, pady=4)
            elif prog == "dynamic":
                dyn_row.pack(fill="x", padx=8, pady=4)
                dyn_seq_row.pack(fill="x", padx=8, pady=4)
            else:
                no_tuning_label.pack(padx=8, pady=8)

        def _update_dal_val(*_args):
            # Always show the value row when D'Alembert-type is visible —
            # user needs to enter either the multiplier or the custom unit.
            dal_val_row.pack(fill="x", padx=8, pady=4, after=dal_type_row)

        progression_combo.configure(command=_update_tuning)
        dal_type_combo.configure(command=lambda _: _update_dal_val())
        _update_tuning()

        # ── Step 4: Trigger Condition (optional) ────────────────────
        # Per-strategy trigger lives next to the rest of the strategy's config
        # so users edit "when this strategy fires" in the same place they edit
        # "what this strategy bets and with what progression." The spec is
        # keyed by base name in self.triggers_config["triggers"] (not embedded
        # in the rotation_list_str entry), and is read/written by the
        # Add/Update/Select callbacks below.
        ctk.CTkLabel(left, text="Step 4: Trigger Condition (optional)",
                     **_lbl_bold).pack(anchor="w", padx=10, pady=(12, 4))
        trigger_frame = ctk.CTkFrame(left, fg_color="#27272A", corner_radius=8)
        trigger_frame.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(trigger_frame, text=(
            "Decide WHEN this strategy is eligible to play. "
            "Leave (none) for plain rotation. Bundle-level Conditional Mode toggle is on the right."
        ), **_hint).pack(anchor="w", padx=8, pady=(6, 2))

        from gui.trigger_editor import InlineTriggerEditor

        def _autosave_current_trigger():
            """Push the inline editor's current spec into self.triggers_config
            for whichever strategy is currently in the Step 1 dropdown. Wired
            as InlineTriggerEditor.on_change so users no longer have to click
            'Update Selected' just to persist a trigger change. The helper
            `_save_trigger_for` is defined further down with the Add/Update
            handlers, so we look it up lazily by closure at call time."""
            try:
                _save_trigger_for(strategy_var.get())
            except NameError:
                pass  # helper not defined yet at module-init time

        builder_trigger_editor = InlineTriggerEditor(trigger_frame,
                                                    on_change=_autosave_current_trigger)
        builder_trigger_editor.frame.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(trigger_frame,
                     text="✓ Saved in dialog memory on change — click Save / Save As below to persist to bundle file.",
                     font=("Segoe UI", 10, "italic"),
                     text_color="#22c55e").pack(anchor="w", padx=8, pady=(0, 6))

        # ── Add / Update buttons ────────────────────────────────────
        action_row = ctk.CTkFrame(left, fg_color="transparent")
        action_row.pack(fill="x", padx=10, pady=(12, 8))

        def _build_config_string():
            """Build the pipe-separated config string from current form state."""
            strategy = strategy_var.get()
            progression = progression_var.get()
            if not strategy:
                messagebox.showwarning("Missing", "Please select a strategy.", parent=dialog)
                return None
            parts = [f"{strategy}:{progression}"]
            if not use_global_settings_var.get():
                parts.append(f"base_bet={base_bet_var.get()}")
                sl = session_length_var.get()
                if sl > 0:
                    parts.append(f"session_length={sl}")
                mcl = max_consec_losses_var.get()
                if mcl > 0:  # 0 = disabled → omit (no cap)
                    parts.append(f"max_consec_losses={mcl}")
            if not use_global_stops_var.get():
                if stop_wins_on.get() and stop_wins_val.get() > 0:
                    parts.append(f"stop_wins={stop_wins_val.get()}")
                if stop_losses_on.get() and stop_losses_val.get() > 0:
                    parts.append(f"stop_losses={stop_losses_val.get()}")
                if stop_profit_on.get() and stop_profit_val.get() > 0:
                    parts.append(f"stop_profit={stop_profit_val.get()}")
                if stop_loss_on.get() and stop_loss_val.get() > 0:
                    parts.append(f"stop_loss_limit={stop_loss_val.get()}")
                if stop_time_on.get() and stop_time_val.get() > 0:
                    parts.append(f"stop_time={stop_time_val.get()}")
            if progression == "dalembert":
                if dalembert_step_type_var.get() == "Base Bet Multiplier":
                    try:
                        m = float(dalembert_step_var.get())
                        parts.append("step=base_bet" if m == 1.0 else f"step=base_bet_{m}x")
                    except ValueError:
                        parts.append("step=base_bet")
                else:
                    parts.append(f"step={dalembert_step_var.get()}")
            elif progression == "custom_sequence":
                parts.append(f"seq={custom_sequence_var.get()}")
            elif progression == "dynamic":
                rules = dynamic_rules_var.get()
                if rules:
                    parts.append(f"rules={rules}")
            return "|".join(parts)

        def _ensure_triggers_dict():
            """Lazily seed triggers_config on the app so the helpers can write
            into it without crashing for fresh bundles."""
            if not hasattr(self, 'triggers_config') or not isinstance(self.triggers_config, dict):
                self.triggers_config = {
                    "selection_mode": "rotation", "triggers": {},
                    "tiebreaker": "coldest", "fallback": "stay",
                }
            self.triggers_config.setdefault("triggers", {})

        def _save_trigger_for(base_name):
            """Persist the inline editor's spec for `base_name` into triggers_config.
            None spec → remove any existing trigger for that strategy."""
            base = (base_name or "").strip()
            if not base:
                return
            _ensure_triggers_dict()
            spec = builder_trigger_editor.get_spec()
            if spec is None:
                self.triggers_config["triggers"].pop(base, None)
            else:
                self.triggers_config["triggers"][base] = spec

        def add_strategy():
            entry = _build_config_string()
            if entry is None:
                return
            current = self.rotation_strategies_var.get().strip()
            self.rotation_strategies_var.set((current + "," + entry) if current else entry)
            self.update_rotation_listbox()
            self.rotation_listbox.yview_moveto(1.0)
            # Select the just-added row so the form stays consistent with the
            # selection (keeps the Save auto-commit from acting on a stale row).
            try:
                _last = self.rotation_listbox.size() - 1
                if _last >= 0:
                    self.rotation_listbox.selection_clear(0, tk.END)
                    self.rotation_listbox.selection_set(_last)
                    self.rotation_listbox.activate(_last)
            except Exception:
                pass
            _save_trigger_for(strategy_var.get())

        def update_strategy():
            sel = self.rotation_listbox.curselection()
            if not sel:
                messagebox.showwarning("No Selection", "Select a strategy to update.", parent=dialog)
                return
            entry = _build_config_string()
            if entry is None:
                return
            entries = self.rotation_strategies_var.get().strip().split(",")
            # If the base name of the selected entry changed, drop the old key's trigger
            # so we don't orphan it.
            try:
                old_base = entries[sel[0]].split("|", 1)[0].split(":", 1)[0].strip()
                new_base = strategy_var.get().strip()
                if old_base and old_base != new_base and hasattr(self, 'triggers_config'):
                    self.triggers_config.get("triggers", {}).pop(old_base, None)
            except Exception:
                pass
            entries[sel[0]] = entry
            self.rotation_strategies_var.set(",".join(entries))
            self.update_rotation_listbox()
            self.rotation_listbox.selection_set(sel[0])
            _save_trigger_for(strategy_var.get())

        ctk.CTkButton(action_row, text="Add to Bundle", command=add_strategy, fg_color="#27ae60", hover_color="#2ecc71", **_btn_sm).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(action_row, text="Update Selected", command=update_strategy, fg_color="#2980b9", hover_color="#3498db", **_btn_sm).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ═══════════════ RIGHT: Bundle Suite ════════════════════════

        ctk.CTkLabel(right, text="Bundle Suite", font=("Segoe UI", 12, "bold"), text_color="#E4E4E7").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        list_frame = ctk.CTkFrame(right, fg_color="#27272A", corner_radius=8)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.rotation_listbox = tk.Listbox(
            list_frame, exportselection=False,
            font=("Segoe UI", 12), relief="flat", borderwidth=0,
            bg="#27272A", fg="#dce4ee", selectbackground="#EAB308", selectforeground="#09090B",
            highlightthickness=0, activestyle="none"
        )
        lb_scroll = ctk.CTkScrollbar(list_frame, command=self.rotation_listbox.yview)
        self.rotation_listbox.configure(yscrollcommand=lb_scroll.set)
        self.rotation_listbox.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        lb_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=6)

        # Keyboard shortcuts
        def on_key_press(event):
            if event.keysym == "Up" and event.state & 0x4:
                move_up()
            elif event.keysym == "Down" and event.state & 0x4:
                move_down()
            elif event.keysym == "Delete":
                remove_strategy()
        self.rotation_listbox.bind("<Key>", on_key_press)

        # Populate form when selecting from list
        def on_listbox_select(_event=None):
            sel = self.rotation_listbox.curselection()
            if not sel:
                return
            current = self.rotation_strategies_var.get().strip()
            if not current:
                return
            entries = current.split(",")
            idx = sel[0]
            if idx >= len(entries):
                return
            entry = entries[idx]

            # Reset all stops
            for v in (stop_wins_on, stop_losses_on, stop_profit_on, stop_loss_on, stop_time_on):
                v.set(False)
            use_global_settings_var.set(True)
            use_global_stops_var.set(True)
            # Per-strategy consec-loss cap: reset to 0 (disabled) so an entry with
            # no `max_consec_losses=` suffix shows 0, not a stale value from the
            # previously-edited strategy or the global config.
            max_consec_losses_var.set(0)

            if "|" in entry:
                parts = entry.split("|")
                strat_prog = parts[0]
                config_parts = parts[1:]
            else:
                strat_prog = entry
                config_parts = []

            if ":" in strat_prog:
                strat, prog = strat_prog.split(":", 1)
                if strat in available_strategies:
                    strategy_var.set(strat)
                progression_var.set(prog)
                _update_tuning()

            for i, cfg in enumerate(config_parts):
                if cfg.startswith("rules="):
                    dynamic_rules_var.set("|".join(config_parts[i:]).split("=", 1)[1])
                    break
                elif cfg.startswith("step="):
                    val = cfg.split("=", 1)[1]
                    if val.startswith("base_bet"):
                        dalembert_step_type_var.set("Base Bet Multiplier")
                        # Parse multiplier from base_bet_Nx format (e.g. base_bet_2.0x)
                        if val == "base_bet":
                            dalembert_step_var.set(1.0)
                        else:
                            try:
                                mult = float(val.replace("base_bet_", "").rstrip("x"))
                                dalembert_step_var.set(mult)
                            except ValueError:
                                dalembert_step_var.set(1.0)
                    else:
                        try:
                            dalembert_step_var.set(float(val))
                            dalembert_step_type_var.set("Custom Unit ($)")
                        except ValueError:
                            pass
                    _update_dal_val()
                elif cfg.startswith("seq="):
                    custom_sequence_var.set(cfg.split("=", 1)[1])
                elif cfg.startswith("base_bet="):
                    base_bet_var.set(float(cfg.split("=", 1)[1]))
                    use_global_settings_var.set(False)
                elif cfg.startswith("session_length="):
                    session_length_var.set(int(cfg.split("=", 1)[1]))
                    use_global_settings_var.set(False)
                elif cfg.startswith("max_consec_losses="):
                    max_consec_losses_var.set(int(cfg.split("=", 1)[1]))
                    use_global_settings_var.set(False)
                elif cfg.startswith("stop_wins="):
                    stop_wins_val.set(int(cfg.split("=", 1)[1])); stop_wins_on.set(True); use_global_stops_var.set(False)
                elif cfg.startswith("stop_losses="):
                    stop_losses_val.set(int(cfg.split("=", 1)[1])); stop_losses_on.set(True); use_global_stops_var.set(False)
                elif cfg.startswith("stop_profit="):
                    stop_profit_val.set(float(cfg.split("=", 1)[1])); stop_profit_on.set(True); use_global_stops_var.set(False)
                elif cfg.startswith("stop_loss_limit="):
                    stop_loss_val.set(float(cfg.split("=", 1)[1])); stop_loss_on.set(True); use_global_stops_var.set(False)
                elif cfg.startswith("stop_time="):
                    stop_time_val.set(int(cfg.split("=", 1)[1])); stop_time_on.set(True); use_global_stops_var.set(False)

            _toggle_settings()
            _toggle_stops()

            # Load this strategy's trigger spec (if any) into the inline editor.
            base = strategy_var.get().strip()
            spec = ((getattr(self, 'triggers_config', {}) or {}).get('triggers') or {}).get(base)
            try:
                builder_trigger_editor.set_spec(spec)
            except Exception:
                pass

        self.rotation_listbox.bind("<<ListboxSelect>>", on_listbox_select)
        self.update_rotation_listbox()

        # List management buttons
        mgmt = ctk.CTkFrame(right, fg_color="transparent")
        mgmt.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 4))

        def remove_strategy():
            sel = self.rotation_listbox.curselection()
            if not sel:
                return
            entries = self.rotation_strategies_var.get().strip().split(",")
            if 0 <= sel[0] < len(entries):
                entries.pop(sel[0])
                self.rotation_strategies_var.set(",".join(entries))
                self.update_rotation_listbox()

        def move_up():
            sel = self.rotation_listbox.curselection()
            if not sel or sel[0] == 0:
                return
            entries = self.rotation_strategies_var.get().strip().split(",")
            i = sel[0]
            entries[i], entries[i - 1] = entries[i - 1], entries[i]
            self.rotation_strategies_var.set(",".join(entries))
            self.update_rotation_listbox()
            self.rotation_listbox.selection_set(i - 1)

        def move_down():
            sel = self.rotation_listbox.curselection()
            if not sel or sel[0] >= self.rotation_listbox.size() - 1:
                return
            entries = self.rotation_strategies_var.get().strip().split(",")
            i = sel[0]
            entries[i], entries[i + 1] = entries[i + 1], entries[i]
            self.rotation_strategies_var.set(",".join(entries))
            self.update_rotation_listbox()
            self.rotation_listbox.selection_set(i + 1)

        def clear_all():
            if messagebox.askyesno("Clear All", "Clear all strategies from the bundle?", parent=dialog):
                self.rotation_strategies_var.set("")
                self.update_rotation_listbox()

        ctk.CTkButton(mgmt, text="Remove", width=70, command=remove_strategy, fg_color="#922b21", hover_color="#c0392b", **_btn_sm).pack(side="left", padx=2)
        ctk.CTkButton(mgmt, text="Up", width=50, command=move_up, fg_color="#475569", **_btn_sm).pack(side="left", padx=2)
        ctk.CTkButton(mgmt, text="Down", width=50, command=move_down, fg_color="#475569", **_btn_sm).pack(side="left", padx=2)
        ctk.CTkButton(mgmt, text="Clear All", width=70, command=clear_all, fg_color="#922b21", hover_color="#c0392b", **_btn_sm).pack(side="right", padx=2)

        # ── Rotation Settings (right panel, below list) ─────────────
        rot_settings = ctk.CTkFrame(right, fg_color="#27272A", corner_radius=8)
        rot_settings.grid(row=3, column=0, sticky="ew", padx=8, pady=(4, 4))

        ctk.CTkLabel(rot_settings, text="Rotation Settings", font=("Segoe UI", 11, "bold"), text_color="#E4E4E7").pack(anchor="w", padx=8, pady=(6, 4))

        # Rotation mode
        r_mode = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_mode.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(r_mode, text="Mode:", width=60, **_lbl).pack(side="left")
        builder_rotation_mode_var = tk.StringVar(value=self.rotation_mode_var.get())
        builder_mode_combo = ctk.CTkComboBox(r_mode, variable=builder_rotation_mode_var, values=["sequential", "random", "smart_ranking", "smart_ranking_reverse"], state="readonly", width=200, **_entry_kw)
        builder_mode_combo.pack(side="left")
        ToolTip(builder_mode_combo, "Sequential: Run strategies in listed order\nRandom: Pick a random strategy each time\nSmart Ranking: Best-performing strategy first\nSmart Ranking (Reverse): Worst-performing first (contrarian)")

        # Switch on loss checkbox
        r_sol = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_sol.pack(fill="x", padx=8, pady=2)
        builder_sol_var = tk.BooleanVar(value=self.switch_on_loss_var.get() if hasattr(self, 'switch_on_loss_var') else False)
        builder_sol_check = ctk.CTkCheckBox(r_sol, text="Switch on Loss", variable=builder_sol_var, font=("Segoe UI", 12))
        builder_sol_check.pack(side="left", padx=(0, 8))
        ToolTip(builder_sol_check, "Rotate to the next strategy mid-session after\nconsecutive losses instead of waiting for session end")

        builder_sol_after_label = ctk.CTkLabel(r_sol, text="after", **_lbl)
        builder_sol_after_label.pack(side="left", padx=(0, 3))
        builder_sol_n_var = tk.IntVar(value=self.switch_after_n_losses_var.get() if hasattr(self, 'switch_after_n_losses_var') else 1)
        builder_sol_n_entry = ctk.CTkEntry(r_sol, textvariable=builder_sol_n_var, width=40, justify="center", **_entry_kw)
        builder_sol_n_entry.pack(side="left", padx=(0, 3))
        ToolTip(builder_sol_n_entry, "How many losses in a row before switching (default: 1)")
        builder_sol_suffix_label = ctk.CTkLabel(r_sol, text="consecutive loss(es)", **_lbl)
        builder_sol_suffix_label.pack(side="left")

        # Carry progression checkbox
        r_carry = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_carry.pack(fill="x", padx=8, pady=2)
        builder_carry_var = tk.BooleanVar(value=self.carry_progression_var.get() if hasattr(self, 'carry_progression_var') else True)
        builder_carry_check = ctk.CTkCheckBox(r_carry, text="Carry Progression on Switch", variable=builder_carry_var, font=("Segoe UI", 12))
        builder_carry_check.pack(side="left")
        ToolTip(builder_carry_check, "ON: Continue bet sizing from previous strategy\n(e.g. martingale step 3 carries over)\nOFF: Reset to base bet when switching")
        builder_carry_hint = ctk.CTkLabel(r_carry, text="(OFF = reset to base bet)", font=("Segoe UI", 11), text_color="#64748B")
        builder_carry_hint.pack(side="left", padx=(8, 0))

        # Reset to 1st strategy on session end
        r_reset_rot = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_reset_rot.pack(fill="x", padx=8, pady=2)
        builder_reset_rot_var = tk.BooleanVar(value=self.reset_rotation_on_session_var.get() if hasattr(self, 'reset_rotation_on_session_var') else False)
        builder_reset_rot_check = ctk.CTkCheckBox(r_reset_rot, text="Reset to 1st Strategy on Session End", variable=builder_reset_rot_var, font=("Segoe UI", 12))
        builder_reset_rot_check.pack(side="left")
        ToolTip(builder_reset_rot_check, "ON: Always restart from the first strategy\nin the bundle after each session ends.\nOFF: Continue rotating to the next strategy.")

        # Per-strategy progressions
        r_override = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_override.pack(fill="x", padx=8, pady=2)
        builder_prog_override_var = tk.BooleanVar(value=self.rotation_progression_override_var.get() if hasattr(self, 'rotation_progression_override_var') else False)
        builder_prog_override_check = ctk.CTkCheckBox(r_override, text="Each strategy uses its own progression", variable=builder_prog_override_var, font=("Segoe UI", 12))
        builder_prog_override_check.pack(side="left")
        ToolTip(builder_prog_override_check, "ON: Each strategy uses the progression defined with it\n(e.g. strat1:martingale, strat2:fibonacci)\nOFF: All strategies share the main progression")

        # Smart Filter
        r_filter = ctk.CTkFrame(rot_settings, fg_color="transparent")
        r_filter.pack(fill="x", padx=8, pady=(2, 6))
        builder_filter_regime_var = tk.BooleanVar(value=self.filter_regime_var.get() if hasattr(self, 'filter_regime_var') else False)
        builder_filter_check = ctk.CTkCheckBox(r_filter, text="Smart Filter", variable=builder_filter_regime_var, font=("Segoe UI", 12))
        builder_filter_check.pack(side="left")
        ToolTip(builder_filter_check, "Only rotate to strategies that match the current\ntable regime (Trending / Choppy / Neutral)")

        # ── Conditional Mode (bundle-level trigger settings, collapsible) ─
        # Per-strategy trigger conditions live in Step 4 (left panel); these
        # are the bundle-wide knobs that decide HOW the matched candidates
        # are picked (tiebreaker) and WHAT to do when none match (fallback).
        # Collapsed by default so the right panel stays compact and the
        # dialog's footer (Save / Save As / Export) stays visible on small
        # windows.
        _trig_cfg_init = getattr(self, 'triggers_config', None) or {}
        _init_mode = (_trig_cfg_init.get('selection_mode') or 'rotation').lower()
        if _init_mode not in ('rotation', 'conditional', 'parallel'):
            _init_mode = 'rotation'
        # Held alongside the legacy BooleanVar so existing toggle-based code
        # (auto-expand, _sync_config) keeps working. The 3-way radio set in
        # the body writes to this StringVar; the checkbox-equivalent flag
        # mirrors it as "not rotation".
        builder_selection_mode_var = tk.StringVar(value=_init_mode)
        builder_cond_mode_var = tk.BooleanVar(value=_init_mode != 'rotation')
        builder_tiebreaker_var = tk.StringVar(value=_trig_cfg_init.get('tiebreaker', 'coldest'))
        builder_fallback_var = tk.StringVar(value=_trig_cfg_init.get('fallback', 'stay'))

        from gui.trigger_editor import (
            TIEBREAKER_OPTIONS as _TB_OPTS, FALLBACK_OPTIONS as _FB_OPTS,
            TIEBREAKER_HINTS as _TB_HINTS, FALLBACK_HINTS as _FB_HINTS,
        )
        from core.triggers import TIEBREAKER_REGISTRY as _TB_REG

        # Collapsible header — matches the "+ Session Behavior" pattern used
        # in the left panel.
        _cond_expanded = tk.BooleanVar(value=builder_cond_mode_var.get())  # auto-expand if already on

        cond_toggle_btn = ctk.CTkButton(
            rot_settings, text="+ 🎯 Conditional Selection (advanced)",
            anchor="w", fg_color="transparent", hover_color="#27272A",
            text_color="#a78bfa", font=("Segoe UI", 11, "bold"),
            height=28, corner_radius=6,
        )
        cond_toggle_btn.pack(fill="x", padx=8, pady=(8, 0))

        cond_body = ctk.CTkFrame(rot_settings, fg_color="transparent")
        # (not packed yet — collapsed by default)

        # ── Mode selector (3-way: Off / Conditional / Parallel) ────
        # Replaces the original boolean Enable checkbox so parallel mode is
        # discoverable inline. builder_cond_mode_var (bool) is kept in sync
        # via the radio command for backward compat with downstream code
        # that just checks "is conditional selection on?".
        cond_header = ctk.CTkFrame(cond_body, fg_color="transparent")
        cond_header.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(cond_header, text="Mode:",
                     font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 6))
        def _on_mode_change():
            builder_cond_mode_var.set(builder_selection_mode_var.get() != "rotation")
            try: _toggle_cond_mode()
            except Exception: pass
        for _label, _val, _color in [
            ("Off",         "rotation",    "#475569"),
            ("Conditional", "conditional", "#7c3aed"),
            ("Parallel",    "parallel",    "#0ea5e9"),
        ]:
            _rb = ctk.CTkRadioButton(
                cond_header, text=_label, variable=builder_selection_mode_var,
                value=_val, command=_on_mode_change,
                fg_color=_color, font=("Segoe UI", 11),
            )
            _rb.pack(side="left", padx=(0, 8))
        # Keep the variable that other code references; not a real widget anymore.
        builder_cond_check = None
        ctk.CTkLabel(cond_header, text="(Parallel: every armed strategy bets in the same round)",
                     font=("Segoe UI", 10, "italic"),
                     text_color="#0ea5e9").pack(side="left", padx=(8, 0))

        # ── Global Trigger ────────────────────────────────────────────
        # One condition that applies to every rotation entry that doesn't
        # have its own Step 4 override. Saves users from configuring 12
        # identical rows for "labels_cold lookback=5" across a 12-strategy
        # bundle. Per-strategy entries still win when present.
        ctk.CTkLabel(cond_body,
                     text="Global Trigger (applies to ALL strategies; Step 4 overrides per-strategy):",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#a78bfa").pack(anchor="w", padx=8, pady=(6, 2))

        def _autosave_global_trigger():
            _ensure_triggers_dict()
            spec = builder_global_trigger_editor.get_spec()
            self.triggers_config["global_trigger"] = spec  # None when (none)

        from gui.trigger_editor import InlineTriggerEditor as _InlineTrigEd
        builder_global_trigger_editor = _InlineTrigEd(
            cond_body, label_text="Global:",
            on_change=_autosave_global_trigger,
        )
        builder_global_trigger_editor.frame.pack(fill="x", padx=8, pady=(0, 4))
        # Seed from already-loaded bundle (if any)
        try:
            _gt_init = (_trig_cfg_init or {}).get('global_trigger') or None
            if _gt_init:
                builder_global_trigger_editor.set_spec(_gt_init)
        except Exception:
            pass

        def _open_examples_from_builder():
            from gui.trigger_editor import TriggerEditorDialog
            entries = [s.strip() for s in self.rotation_strategies_var.get().split(",") if s.strip()]

            def _after_save(_cfg):
                builder_cond_mode_var.set(
                    (self.triggers_config.get('selection_mode') or 'rotation').lower() == 'conditional')
                builder_tiebreaker_var.set(self.triggers_config.get('tiebreaker', 'coldest'))
                builder_fallback_var.set(self.triggers_config.get('fallback', 'stay'))
                sel = self.rotation_listbox.curselection() if hasattr(self, 'rotation_listbox') else None
                if sel:
                    base = strategy_var.get().strip()
                    spec = (self.triggers_config.get('triggers') or {}).get(base)
                    try:
                        builder_trigger_editor.set_spec(spec)
                    except Exception:
                        pass

            TriggerEditorDialog(self, entries, on_save=_after_save).open()

        ctk.CTkButton(cond_header, text="📚 Examples", width=110,
                      fg_color="#0ea5e9", hover_color="#0284c7",
                      command=_open_examples_from_builder).pack(side="right")

        # Tiebreaker
        r_tie = ctk.CTkFrame(cond_body, fg_color="transparent")
        r_tie.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(r_tie, text="Tiebreaker:", width=80, **_lbl).pack(side="left")
        builder_tb_combo = ctk.CTkComboBox(
            r_tie, variable=builder_tiebreaker_var,
            values=[t for t in _TB_OPTS if t in _TB_REG],
            state="readonly", width=170, **_entry_kw,
        )
        builder_tb_combo.pack(side="left", padx=(4, 0))
        # Static caption — distinguishes condition (arms a strategy) from
        # tiebreaker (picks among armed). Without it users assume "coldest"
        # carries a threshold and look for it on this dropdown.
        ctk.CTkLabel(cond_body,
                     text="ℹ Thresholds (cold ≥ N, streak ≥ N, etc.) are set per-strategy in "
                          "Step 4 on the left. Tiebreaker only ranks strategies that already qualify.",
                     font=("Segoe UI", 10, "italic"),
                     text_color="#a78bfa",
                     wraplength=320, justify="left").pack(anchor="w", padx=(8, 8), pady=(2, 0))
        builder_tb_hint = ctk.CTkLabel(cond_body, text="", **_hint,
                                       wraplength=320, justify="left")
        builder_tb_hint.pack(anchor="w", padx=(98, 8))

        # Fallback
        r_fb = ctk.CTkFrame(cond_body, fg_color="transparent")
        r_fb.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(r_fb, text="Fallback:", width=80, **_lbl).pack(side="left")
        builder_fb_combo = ctk.CTkComboBox(
            r_fb, variable=builder_fallback_var, values=_FB_OPTS,
            state="readonly", width=170, **_entry_kw,
        )
        builder_fb_combo.pack(side="left", padx=(4, 0))
        builder_fb_hint = ctk.CTkLabel(cond_body, text="", **_hint,
                                       wraplength=320, justify="left")
        builder_fb_hint.pack(anchor="w", padx=(98, 8), pady=(0, 6))

        def _update_cond_hints(*_):
            try:
                builder_tb_hint.configure(
                    text="💡 " + _TB_HINTS.get(builder_tiebreaker_var.get(), ""))
                builder_fb_hint.configure(
                    text="💡 " + _FB_HINTS.get(builder_fallback_var.get(), ""))
            except Exception:
                pass

        builder_tb_combo.configure(command=lambda _v: _update_cond_hints())
        builder_fb_combo.configure(command=lambda _v: _update_cond_hints())
        _update_cond_hints()

        def _toggle_cond_mode():
            enabled = builder_cond_mode_var.get()
            try:
                builder_tb_combo.configure(state="readonly" if enabled else "disabled")
                builder_fb_combo.configure(state="readonly" if enabled else "disabled")
            except Exception:
                pass
        # _toggle_cond_mode is now driven by the radio's _on_mode_change above.
        _toggle_cond_mode()

        def _toggle_cond_section():
            if _cond_expanded.get():
                cond_body.pack_forget()
                cond_toggle_btn.configure(text="+ 🎯 Conditional Selection (advanced)")
                _cond_expanded.set(False)
            else:
                cond_body.pack(fill="x", padx=8, pady=(0, 4), after=cond_toggle_btn)
                cond_toggle_btn.configure(text="- 🎯 Conditional Selection (advanced)")
                _cond_expanded.set(True)
        cond_toggle_btn.configure(command=_toggle_cond_section)
        # Auto-expand on open if the loaded bundle already has conditional mode on,
        # so users don't think their settings are gone.
        if builder_cond_mode_var.get():
            _toggle_cond_section()

        # Enable/disable sub-controls based on switch-on-loss toggle
        def _toggle_sol_controls():
            enabled = builder_sol_var.get()
            state = "normal" if enabled else "disabled"
            dim = "#A1A1AA" if enabled else "#52525B"
            builder_sol_n_entry.configure(state=state)
            builder_carry_check.configure(state=state)
            builder_sol_after_label.configure(text_color=dim)
            builder_sol_suffix_label.configure(text_color=dim)
            builder_carry_hint.configure(text_color="#64748B" if enabled else "#3F3F46")
        builder_sol_check.configure(command=_toggle_sol_controls)
        _toggle_sol_controls()

        # ── Footer ──────────────────────────────────────────────────
        # Two rows: a status line ON TOP of a dedicated button row. Keeping the
        # action buttons on their own full-width row stops a long
        # "Editing: <name>" status from crowding the right-side Save / Save As
        # buttons off-screen at the default window width (previously you had to
        # maximize the window to reach Save). Pinned to the bottom so the button
        # row is always visible regardless of content height.
        footer_wrap = ctk.CTkFrame(dialog, fg_color="transparent")
        footer_wrap.pack(side="bottom", fill="x", padx=15, pady=(4, 12))

        # Status line: strategy count + mode + editing indicator (own row).
        status_frame = ctk.CTkFrame(footer_wrap, fg_color="transparent")
        status_frame.pack(side="top", anchor="w", fill="x", pady=(0, 6))
        ctk.CTkLabel(status_frame, text="Strategies: ", **_lbl).pack(side="left")
        count_label = ctk.CTkLabel(status_frame, text="0", font=("Segoe UI", 11, "bold"), text_color="#EAB308")
        count_label.pack(side="left")
        mode_label = ctk.CTkLabel(status_frame, text="", font=("Segoe UI", 11), text_color="#A1A1AA")
        mode_label.pack(side="left", padx=(8, 0))
        editing_label = ctk.CTkLabel(status_frame, text="", font=("Segoe UI", 11), text_color="#64748B")
        editing_label.pack(side="left", padx=(8, 0))

        # Action buttons get their own full-width row below the status line.
        footer = ctk.CTkFrame(footer_wrap, fg_color="transparent")
        footer.pack(side="top", fill="x")

        _dialog_alive = [True]  # mutable flag for trace callbacks

        def _update_count(*_):
            if not _dialog_alive[0]:
                return
            try:
                n = len([s for s in self.rotation_strategies_var.get().split(",") if s.strip()])
                count_label.configure(text=str(n))
                # Show rotation mode + trigger summary
                mode = builder_rotation_mode_var.get()
                trigger = "on loss" if builder_sol_var.get() else "session end"
                mode_label.configure(text=f"| {mode} | switch: {trigger}")
            except Exception:
                pass

        _trace_ids = []
        _trace_ids.append(("rotation_strategies", self.rotation_strategies_var.trace_add("write", _update_count)))
        _trace_ids.append(("rotation_mode", builder_rotation_mode_var.trace_add("write", _update_count)))
        _trace_ids.append(("sol", builder_sol_var.trace_add("write", _update_count)))

        def _on_dialog_close():
            _dialog_alive[0] = False
            # Remove traces to prevent crashes after dialog is destroyed
            for name, tid in _trace_ids:
                try:
                    if name == "rotation_strategies":
                        self.rotation_strategies_var.trace_remove("write", tid)
                    elif name == "rotation_mode":
                        builder_rotation_mode_var.trace_remove("write", tid)
                    elif name == "sol":
                        builder_sol_var.trace_remove("write", tid)
                except Exception:
                    pass
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", _on_dialog_close)
        _update_count()

        def _sync_config():
            """Push builder local vars into self.config."""
            # Auto-commit the currently-selected rotation row from the form FIRST,
            # so per-row field edits (Max Consec Losses, Base Bet, per-leg stops,
            # progression tuning, ...) aren't silently dropped when the user clicks
            # Save without first clicking "Update Selected". Mirrors update_strategy
            # but stays quiet (no "select a strategy" popups) during sync.
            try:
                _sel = self.rotation_listbox.curselection()
                _form_base = strategy_var.get().strip()
                if _sel and _form_base:
                    _entries = self.rotation_strategies_var.get().strip().split(",")
                    if _sel[0] < len(_entries):
                        # Guard: only commit when the form still reflects the
                        # SELECTED row's strategy. Otherwise (e.g. right after
                        # "Add to Bundle" built a new row while an old row stays
                        # selected) committing would overwrite a different row.
                        _sel_base = _entries[_sel[0]].split("|", 1)[0].split(":", 1)[0].strip()
                        if _sel_base == _form_base:
                            _entry = _build_config_string()
                            if _entry:
                                _entries[_sel[0]] = _entry
                                self.rotation_strategies_var.set(",".join(_entries))
                                self.update_rotation_listbox()
                                self.rotation_listbox.selection_set(_sel[0])
                                _save_trigger_for(_form_base)
            except Exception as _ac_err:
                logger.debug(f"[Builder] auto-commit current row on save skipped: {_ac_err}")
            self.config["base_bet"] = base_bet_var.get()
            self.config["max_bet"] = max_bet_var.get()
            self.config["max_loss"] = _safe_float(max_loss_var.get(), 100.0)
            self.config["session_duration"] = session_length_var.get()
            self.config["session_duration_minutes"] = session_length_var.get()
            if hasattr(self, 'session_duration_var'):
                self.session_duration_var.set(str(session_length_var.get()))
            self.config["num_sessions"] = num_sessions_var.get()
            self.config["min_gap_minutes"] = min_gap_var.get()
            self.config["max_gap_minutes"] = max_gap_var.get()
            self.config["profit_target"] = profit_target_var.get()
            self.config["enable_trailing_stop"] = trailing_stop_on.get()
            self.config["trailing_stop_amount"] = trailing_stop_amt.get()
            self.config["session_ext_after_win"] = ext_after_win_var.get()
            self.config["session_ext_at_high"] = ext_at_high_var.get()
            # When 'End only at session high' is enabled, the SESSION extends
            # but the BET still resets on every win unless each rotation entry
            # carries conditional rules. Most users tick the checkbox expecting
            # the full behavior (extend + keep escalated until recovered), so
            # auto-inject the matching rules into rotation entries that don't
            # already have them. Updates rotation_strategies_var in place so
            # the listbox + saved bundle reflect what's about to run.
            if ext_at_high_var.get() and hasattr(self, 'rotation_strategies_var'):
                _cur_rotation = self.rotation_strategies_var.get().strip()
                _new_rotation, _n_changed = self._augment_rotation_for_extend_at_high(_cur_rotation)
                if _n_changed > 0:
                    self.rotation_strategies_var.set(_new_rotation)
                    try:
                        self.update_rotation_listbox()
                    except Exception:
                        pass
                    messagebox.showinfo(
                        "Auto-added conditional win rules",
                        f"'End only at session high' is enabled, so the bet should stay "
                        f"escalated until profit recovers to the session high — not "
                        f"reset on every win.\n\n"
                        f"Auto-added the conditional win rules to {_n_changed} rotation "
                        f"entry(s) so the bundle actually behaves that way end-to-end. "
                        f"Open Bundle Suite to inspect the updated rules.",
                        parent=dialog if 'dialog' in dir() else None)
            self.config["max_extension_rounds"] = max_ext_rounds_var.get()
            self.config["extension_give_up_amount"] = ext_give_up_var.get()
            self.config["enable_global_stop"] = global_stop_on.get()
            self.config["global_profit_stop"] = global_profit_var.get()
            self.config["global_stop_loss"] = global_loss_var.get()
            # Escalation on session stop-loss (also sync to main goals-frame vars
            # so the main UI reflects what the builder just configured).
            self.config["enable_escalation_on_loss"] = escalation_on.get()
            self.config["escalation_multiplier"] = float(escalation_mult_var.get() or 2.0)
            self.config["escalation_max_steps"] = int(escalation_steps_var.get() or 4)
            self.config["escalation_per_step"] = str(escalation_per_step_var.get() or "").strip()
            if hasattr(self, 'enable_escalation_on_loss_var'):
                self.enable_escalation_on_loss_var.set(escalation_on.get())
            if hasattr(self, 'escalation_multiplier_var'):
                self.escalation_multiplier_var.set(str(self.config["escalation_multiplier"]))
            if hasattr(self, 'escalation_max_steps_var'):
                self.escalation_max_steps_var.set(str(self.config["escalation_max_steps"]))
            if hasattr(self, 'escalation_per_step_var'):
                self.escalation_per_step_var.set(self.config["escalation_per_step"])
            # Rotation settings from builder
            self.rotation_mode_var.set(builder_rotation_mode_var.get())
            self.config["rotation_mode"] = builder_rotation_mode_var.get()
            trigger = "on_loss" if builder_sol_var.get() else "session_end"
            self.rotation_trigger_var.set(trigger)
            self.config["rotation_trigger"] = trigger
            if hasattr(self, 'switch_on_loss_var'):
                self.switch_on_loss_var.set(builder_sol_var.get())
            self.switch_after_n_losses_var.set(builder_sol_n_var.get())
            self.config["switch_after_n_losses"] = builder_sol_n_var.get()
            self.carry_progression_var.set(builder_carry_var.get())
            self.config["carry_progression_on_switch"] = builder_carry_var.get()
            self.reset_rotation_on_session_var.set(builder_reset_rot_var.get())
            self.config["reset_rotation_on_session"] = builder_reset_rot_var.get()
            self.rotation_progression_override_var.set(builder_prog_override_var.get())
            self.config["rotation_progression_override"] = builder_prog_override_var.get()
            if hasattr(self, 'filter_regime_var'):
                self.filter_regime_var.set(builder_filter_regime_var.get())
            self.config["filter_by_regime"] = builder_filter_regime_var.get()
            # Ensure observation/streak settings are preserved in config
            if "observation_trigger" not in self.config:
                self.config["observation_trigger"] = 0
            if "max_consec_losses" not in self.config:
                # 0 = disabled. (Was 5, which silently planted a global per-leg
                # cap that stopped parallel strategies after 5 losses. The cap is
                # now per-strategy only — set it in the Bundle Builder per row.)
                self.config["max_consec_losses"] = 0
            self._on_switch_on_loss_toggled()

            # ── Conditional-trigger bundle-level settings ─────────────
            # Per-strategy `triggers` was populated as users used Add/Update;
            # here we sync the bundle-level knobs (enable flag, tiebreaker,
            # fallback). The resulting triggers_config gets baked into the
            # bundle JSON the next time save_bundle / _build_bundle_data runs.
            _ensure_triggers_dict()
            # 3-way selection_mode: rotation / conditional / parallel.
            _selected_mode = (builder_selection_mode_var.get() or "rotation").strip().lower()
            if _selected_mode not in ("rotation", "conditional", "parallel"):
                _selected_mode = "rotation"
            self.triggers_config["selection_mode"] = _selected_mode
            self.triggers_config["tiebreaker"] = builder_tiebreaker_var.get() or "coldest"
            self.triggers_config["fallback"] = builder_fallback_var.get() or "stay"
            # Flush the Global Trigger editor's current state. Its auto-save only
            # fires on <FocusOut>/<Return> for entry params, so clicking Save with
            # focus still in the n-value entry would otherwise persist the OLD
            # value. Reading get_spec() here pulls whatever the entry's StringVar
            # currently holds — including unblurred edits.
            try:
                self.triggers_config["global_trigger"] = builder_global_trigger_editor.get_spec()
            except Exception:
                pass
            # Same flush for the currently-selected per-strategy trigger editor.
            try:
                _autosave_current_trigger()
            except Exception:
                pass

        def _save():
            """Save — overwrite if editing, else ask for name."""
            _sync_config()
            path = _editing_path.get() or None
            name = _editing_name.get() or None
            result = self.save_bundle(overwrite_path=path, overwrite_name=name)
            if result:
                _editing_path.set(result)
                _editing_name.set(os.path.splitext(os.path.basename(result))[0])
                editing_label.configure(text=f"Editing: {_editing_name.get()}")

        def _save_as():
            """Save As — always ask for new name."""
            _sync_config()
            result = self.save_bundle()
            if result:
                _editing_path.set(result)
                _editing_name.set(os.path.splitext(os.path.basename(result))[0])
                editing_label.configure(text=f"Editing: {_editing_name.get()}")

        def _export_spine():
            """Export as encrypted .spine for distribution."""
            _sync_config()
            self.export_bundle_spine()

        def _load_into_builder(preload_path=None):
            """Load an existing bundle and populate ALL builder fields."""
            if preload_path:
                filename = preload_path
            else:
                # In-app type-to-search picker (initials/fuzzy) instead of the
                # raw OS file dialog, so users can find a bundle by typing a few
                # letters of its name.
                filename = self._searchable_bundle_picker(dialog, title="Load Bundle into Builder")
                if not filename:
                    return
            try:
                data = None
                if filename.endswith(".spine"):
                    from core.encryption import decrypt_strategy_data
                    with open(filename, "rb") as f:
                        data = decrypt_strategy_data(f.read())
                    if data is None:
                        messagebox.showerror("Error", "Failed to decrypt bundle.", parent=dialog)
                        return
                else:
                    with open(filename, "r") as f:
                        data = json.load(f)

                # ── Populate rotation list ──────────────────────────
                strat_conf = data.get("strategy_config", {})
                rot_str = strat_conf.get("rotation_list_str", "")
                if rot_str:
                    self.rotation_strategies_var.set(rot_str)
                    self.update_rotation_listbox()
                    # Auto-select the first strategy so its saved per-row config
                    # (base bet, max consec losses, per-leg stops, trigger) loads
                    # into the form immediately — otherwise the form shows reset
                    # defaults (e.g. Max Consec Losses 0) until a row is clicked,
                    # which looks like the saved value didn't persist.
                    try:
                        if self.rotation_listbox.size() > 0:
                            self.rotation_listbox.selection_clear(0, tk.END)
                            self.rotation_listbox.selection_set(0)
                            self.rotation_listbox.activate(0)
                            on_listbox_select()
                    except Exception:
                        pass

                # ── Populate conditional-trigger config from the bundle ──
                # The per-strategy `triggers` map is the source of truth that
                # Step 4's inline editor reads when a row is selected; the
                # bundle-level flags drive the right-panel checkbox/dropdowns.
                self.triggers_config = {
                    "selection_mode": (strat_conf.get("selection_mode") or "rotation"),
                    "triggers":       dict(strat_conf.get("triggers") or {}),
                    "global_trigger": strat_conf.get("global_trigger") or None,
                    "tiebreaker":     (strat_conf.get("tiebreaker") or "coldest"),
                    "fallback":       (strat_conf.get("fallback") or "stay"),
                }
                try:
                    _loaded_mode = (self.triggers_config["selection_mode"] or "rotation").lower()
                    if _loaded_mode not in ("rotation", "conditional", "parallel"):
                        _loaded_mode = "rotation"
                    builder_selection_mode_var.set(_loaded_mode)
                    builder_cond_mode_var.set(_loaded_mode != "rotation")
                    builder_tiebreaker_var.set(self.triggers_config["tiebreaker"])
                    builder_fallback_var.set(self.triggers_config["fallback"])
                    builder_tb_combo.set(self.triggers_config["tiebreaker"])
                    builder_fb_combo.set(self.triggers_config["fallback"])
                    _update_cond_hints()
                    _toggle_cond_mode()
                    # Auto-expand the collapsible section so the user can see
                    # their loaded trigger settings instead of thinking they
                    # were lost.
                    if builder_cond_mode_var.get() and not _cond_expanded.get():
                        _toggle_cond_section()
                    builder_trigger_editor.reset()  # clear inline editor — populated on row select
                    # Seed the Global Trigger editor from the loaded bundle.
                    builder_global_trigger_editor.set_spec(
                        self.triggers_config.get("global_trigger") or None
                    )
                except Exception:
                    pass

                # Always set ALL rotation settings from bundle (default for missing keys)
                # Use both variable.set() AND direct widget methods to ensure
                # CustomTkinter widgets visually sync with the loaded values.
                rot_mode = strat_conf.get("rotation_mode", "sequential")
                is_on_loss = strat_conf.get("rotation_trigger", "session_end") == "on_loss"
                carry = bool(strat_conf.get("carry_progression_on_switch", True))
                reset_rot = bool(strat_conf.get("reset_rotation_on_session", False))
                prog_override = bool(strat_conf.get("rotation_progression_override", False))
                filter_regime = bool(strat_conf.get("filter_by_regime", False))
                n_losses = int(strat_conf.get("switch_after_n_losses", 1))
                trigger_val = strat_conf.get("rotation_trigger", "session_end")

# Set variables
                builder_rotation_mode_var.set(rot_mode)
                builder_sol_var.set(is_on_loss)
                builder_sol_n_var.set(n_losses)
                builder_carry_var.set(carry)
                builder_reset_rot_var.set(reset_rot)
                builder_prog_override_var.set(prog_override)
                builder_filter_regime_var.set(filter_regime)
                if hasattr(self, 'rotation_mode_var'):
                    self.rotation_mode_var.set(rot_mode)
                if hasattr(self, 'rotation_trigger_var'):
                    self.rotation_trigger_var.set(trigger_val)

                # Force widget visual sync (CTk widgets may not update from var.set alone)
                builder_mode_combo.set(rot_mode)
                builder_sol_check.select() if is_on_loss else builder_sol_check.deselect()
                builder_carry_check.select() if carry else builder_carry_check.deselect()
                builder_reset_rot_check.select() if reset_rot else builder_reset_rot_check.deselect()
                builder_prog_override_check.select() if prog_override else builder_prog_override_check.deselect()
                builder_filter_check.select() if filter_regime else builder_filter_check.deselect()

                _toggle_sol_controls()

                # Force GUI refresh
                dialog.update_idletasks()

                # ── Populate ALL betting config vars from bundle ────
                bc = data.get("betting_config", {})
                _sf = _safe_float  # local alias
                base_bet_var.set(_sf(bc.get("base_bet", 1.0), 1.0))
                max_bet_var.set(_sf(bc.get("max_bet", 100.0), 100.0))
                session_length_var.set(int(_sf(bc.get("session_duration", 15), 15)))
                num_sessions_var.set(int(_sf(bc.get("num_sessions", 1), 1)))
                min_gap_var.set(int(_sf(bc.get("min_gap_minutes", 30), 30)))
                max_gap_var.set(int(_sf(bc.get("max_gap_minutes", 120), 120)))
                profit_target_var.set(_sf(bc.get("profit_target", 0)))
                max_loss_val = _sf(bc.get("max_loss", 100.0), 100.0)
                max_loss_var.set(str(max_loss_val))
                # NOTE: do NOT write self.config["max_loss"] here. Opening a
                # bundle for editing must not mutate the running global config;
                # max_loss_var is the form's single source of truth and
                # _sync_config persists it from the var on an explicit Save.

                # Session behavior
                trailing_stop_on.set(bool(bc.get("enable_trailing_stop", False)))
                trailing_stop_amt.set(_sf(bc.get("trailing_stop_amount", 0)))
                ext_after_win_var.set(bool(bc.get("session_ext_after_win", False)))
                ext_at_high_var.set(bool(bc.get("session_ext_at_high", False)))
                max_ext_rounds_var.set(int(_sf(bc.get("max_extension_rounds", 20), 20)))
                ext_give_up_var.set(_sf(bc.get("extension_give_up_amount", 50.0), 50.0))

                # Global stops
                global_stop_on.set(bool(bc.get("enable_global_stop", False)))
                global_profit_var.set(_sf(bc.get("global_profit_stop", 0)))
                global_loss_var.set(_sf(bc.get("global_stop_loss", 0)))

                # Escalation on session stop-loss
                escalation_on.set(bool(bc.get("enable_escalation_on_loss", False)))
                escalation_mult_var.set(_sf(bc.get("escalation_multiplier", 2.0), 2.0))
                escalation_steps_var.set(int(_sf(bc.get("escalation_max_steps", 4), 4)))
                escalation_per_step_var.set(str(bc.get("escalation_per_step", "") or ""))

                # Observation / streak settings. These have no builder form var,
                # so self.config is the carrier that _build_bundle_data reads on
                # save — keep writing them here for round-trip. Default
                # max_consec_losses to 0 (disabled): a bundle that omitted the
                # key must NOT resurrect a global 5-loss cap.
                self.config["observation_trigger"] = int(_sf(bc.get("observation_trigger", 0), 0))
                self.config["max_consec_losses"] = int(_sf(bc.get("max_consec_losses", 0), 0))

                # Uncheck "use global" since we loaded bundle-specific values
                use_global_settings_var.set(False)
                _toggle_settings()

                # Auto-expand Session Behavior if any behavior setting is active
                if (trailing_stop_on.get() or ext_after_win_var.get() or ext_at_high_var.get()):
                    if not _behavior_expanded.get():
                        _toggle_behavior()

                # Auto-expand Global Safety Net if global stops are active
                if global_stop_on.get():
                    if not _global_expanded.get():
                        _toggle_global()

                # Dynamic rules
                if "dynamic_rules" in data:
                    self.dynamic_rules = data["dynamic_rules"]
                    self.config["dynamic_rules"] = self.dynamic_rules

                # Track editing state
                bundle_name = data.get("name") or data.get("meta", {}).get("name") or os.path.splitext(os.path.basename(filename))[0]
                _editing_path.set(filename)
                _editing_name.set(bundle_name)
                editing_label.configure(text=f"Editing: {bundle_name}")
                dialog.title(f"Bundle Builder — {bundle_name}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load bundle: {e}", parent=dialog)

        # Left side: Load
        ctk.CTkButton(footer, text="Load Bundle", width=100, command=_load_into_builder, fg_color="#2980b9", hover_color="#3498db", **_btn_sm).pack(side="left", padx=(8, 0))
        # Right side: Save actions + Close
        ctk.CTkButton(footer, text="Close", width=70, command=_on_dialog_close, fg_color="#475569", **_btn_sm).pack(side="right", padx=(4, 0))
        ctk.CTkButton(footer, text="Export .spine", width=100, command=_export_spine, fg_color="#8e44ad", hover_color="#9b59b6", **_btn_sm).pack(side="right", padx=(4, 0))
        ctk.CTkButton(footer, text="Save As", width=80, command=_save_as, fg_color="#475569", hover_color="#64748B", **_btn_sm).pack(side="right", padx=(4, 0))
        ctk.CTkButton(footer, text="Save", width=70, command=_save, fg_color="#27ae60", hover_color="#2ecc71", **_btn_sm).pack(side="right", padx=(4, 0))

        # ── Auto-load: if a bundle is selected in any dropdown, load it ──
        def _resolve_selected_bundle_path():
            """Find the file path of the currently selected bundle from any dropdown."""
            bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
            skip = ("Select Bundle...", "No Bundles Found", "Select List...", "No Lists Found", "")

            # 1. Check rotation preset dropdown (Strategy Rotation section)
            if hasattr(self, 'rotation_preset_var'):
                selected = self.rotation_preset_var.get()
                if selected and selected not in skip:
                    # Use the path mapping built by refresh_rotation_presets_dropdown
                    mapped = getattr(self, "_rotation_preset_paths", {}).get(selected)
                    if mapped and os.path.exists(mapped):
                        return mapped

            # 2. Check dashboard bundle dropdown
            if hasattr(self, 'dashboard_bundle_var'):
                selected = self.dashboard_bundle_var.get()
                if selected and selected not in skip:
                    json_path = os.path.join(bundles_dir, f"{selected}.json")
                    spine_path = os.path.join(bundles_dir, f"{selected}.spine")
                    if os.path.exists(json_path):
                        return json_path
                    elif os.path.exists(spine_path):
                        return spine_path
            return None

        _auto_path = _resolve_selected_bundle_path()
        if _auto_path:
            dialog.after(100, lambda: _load_into_builder(preload_path=_auto_path))

    def update_rotation_listbox(self):
        """Update the rotation listbox with current entries"""
        try:
            self.rotation_listbox.delete(0, tk.END)
            current = self.rotation_strategies_var.get().strip()
            if current:
                entries = current.split(",")
                for i, entry in enumerate(entries):
                    if "|" in entry:
                        # Parse configuration
                        parts = entry.split("|")
                        strategy_prog = parts[0]
                        config_parts = parts[1:]
                        
                        # Format display
                        display = strategy_prog
                        for config in config_parts:
                            if config.startswith("step="):
                                display += f" (step={config.split('=', 1)[1]})"
                            elif config.startswith("seq="):
                                display += f" (seq={config.split('=', 1)[1]})"
                            elif config.startswith("rules="):
                                display += f" (rules={config.split('=', 1)[1]})"
                            elif config.startswith("base_bet="):
                                display += f" (bet={config.split('=', 1)[1]})"
                            elif config.startswith("session_length="):
                                display += f" (time={config.split('=', 1)[1]}min)"
                            elif config.startswith("max_consec_losses="):
                                display += f" (maxCL={config.split('=', 1)[1]})"
                            elif config.startswith("stop_wins="):
                                display += f" (stop: {config.split('=', 1)[1]} wins)"
                            elif config.startswith("stop_losses="):
                                display += f" (stop: {config.split('=', 1)[1]} losses)"
                            elif config.startswith("stop_profit="):
                                display += f" (stop: ${config.split('=', 1)[1]} profit)"
                            elif config.startswith("stop_loss_limit="):
                                display += f" (stop: ${config.split('=', 1)[1]} loss)"
                            elif config.startswith("stop_time="):
                                display += f" (stop: {config.split('=', 1)[1]}min)"
                        
                        self.rotation_listbox.insert(tk.END, display)
                    else:
                        self.rotation_listbox.insert(tk.END, entry)
        except Exception as e:
            print(f"Error updating rotation listbox: {e}")

    def add_dynamic_rule_dialog(self, rules_var):
        """Dialog for adding dynamic rules in the rotation strategy dialog.
        Reuses the shared CTk dialog, then converts the dict rule to a pipe-separated string."""
        rule = self._build_dynamic_rule_dialog()
        if not rule:
            return

        # Convert rule dict to pipe-separated string format
        rule_parts = [f"{rule['on']}:{rule['action']}"]
        if rule.get("condition"):
            rule_parts.append(f"condition={rule['condition']}")
        if rule["action"] == "custom_sequence" and "sequence" in rule:
            rule_parts.append(f"seq={','.join(str(x) for x in rule['sequence'])}")
        if rule["action"] in ("dalembert", "step_up", "step_down") and "step" in rule:
            rule_parts.append(f"step={rule['step']}")

        current = rules_var.get().strip()
        new_rule_str = "|".join(rule_parts)
        rules_var.set(f"{current};{new_rule_str}" if current else new_rule_str)

    def toggle_dark_mode(self):
        """Toggle between Dark and Light mode using CustomTkinter's native manager"""
        if self.dark_mode_var.get():
            ctk.set_appearance_mode("Dark")
            # Optional: Update RouletteBoard background if we wanted to support light mode there
            # self.roulette_board.configure(bg="#1a1a1a") 
        else:
            ctk.set_appearance_mode("Light")
            # self.roulette_board.configure(bg="#f0f0f0") # Example
            
        # Force update to ensure immediate repaint
        self.root.update()

    def handle_remote_config(self, config_key, value, var_name=None):
        """
        Thread-safe method to update configuration from remote sources (Telegram).
        Must be called via root.after if initiating from another thread, or logic handles it.
        """
        try:
            # 1. Update Config Dictionary
            self.config[config_key] = value
            
            # 2. Update GUI Variable if provided
            if var_name:
                # Find variable either by name string or check self attributes
                var = getattr(self, var_name, None)
                if var:
                    try:
                        # Handle boolean toggles vs numbers
                        if isinstance(var, tk.BooleanVar):
                            var.set(bool(value))
                        elif isinstance(var, tk.DoubleVar):
                            var.set(float(value))
                        elif isinstance(var, tk.IntVar):
                            var.set(int(value))
                        else:
                            var.set(str(value)) # StringVar
                    except Exception as e:
                        print(f"⚠️ Error setting GUI var {var_name}: {e}")
            
            # 3. Save to disk
            save_config(self.config)
            
            # 4. Log
            print(f"🔄 Remote Config Update: {config_key} = {value}")
            self.log_simulation(f"⚙️ Remote Update: Set {config_key} to {value}")
            
            # 5. Trigger HUD Update if relevant
            if "global" in config_key or "profit" in config_key or "loss" in config_key:
                from core.utils.scaling_utils import get_scaling_factor # Lazy import
                self.update_hud_safe()
                
        except Exception as e:
            print(f"❌ Error in handle_remote_config: {e}")

    # --- Undo/Clear All logic ---
    def undo_last_coord_action(self):
        if not hasattr(self, '_coord_undo_stack') or not self._coord_undo_stack:
            self.set_status("Nothing to undo.")
            return
        last = self._coord_undo_stack.pop()
        self.coordinates = last['coordinates'].copy()
        self.config["coordinates"] = self.coordinates
        save_config(self.config)
        self.update_coord_list_display()
        self.update_label_selector()
        self.set_status("Undo successful.")
    def clear_all_coordinates(self):
        if messagebox.askyesno("Clear All", "Are you sure you want to remove all regions and coordinates?"):
            # Save for undo
            if not hasattr(self, '_coord_undo_stack'):
                self._coord_undo_stack = []
            self._coord_undo_stack.append({'coordinates': self.coordinates.copy()})
            self.coordinates.clear()
            self.config["coordinates"] = self.coordinates
            save_config(self.config)
            self.update_coord_list_display()
            self.update_label_selector()
            self.set_status("All regions and coordinates cleared.")

    def filter_log(self):
        search = self.log_search_var.get().lower()
        self.sim_log.configure(state="normal")
        lines = self.sim_log.get("1.0", tk.END).splitlines()
        self.sim_log.delete("1.0", tk.END)
        for line in lines:
            if search in line.lower():
                self.sim_log.insert(tk.END, line + "\n")
        self.sim_log.configure(state="disabled")
    def export_log(self):
        from tkinter import filedialog
        file_path = filedialog.asksaveasfilename(title="Export Log", defaultextension=".txt", filetypes=[("Text Files", "*.txt")])
        if not file_path:
            return
        try:
            log_text = self.sim_log.get("1.0", tk.END)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(log_text)
            messagebox.showinfo("Export Success", "Log exported successfully.")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export log.\n\n{e}")

    # =================================================================
    # DASHBOARD HELPER METHODS (Appended)
    # =================================================================
    def create_kpi_card(self, parent, col, title, initial_value, color, attr_name):
        """Helper to create consistent KPI cards on Dashboard"""
        card = ctk.CTkFrame(parent, fg_color="#2b2b2b", corner_radius=10)
        card.grid(row=0, column=col, padx=5, pady=10, sticky="ew")
        
        ctk.CTkLabel(card, text=title, font=("Arial", 10, "bold"), text_color="gray").pack(pady=(10,0))
        lbl = ctk.CTkLabel(card, text=initial_value, font=("Arial", 18, "bold"), text_color=color)
        lbl.pack(pady=(0, 10))
        setattr(self, attr_name, lbl)



    def format_time(self, seconds):
        """Helper to format seconds into MM:SS or HH:MM:SS"""
        if seconds < 3600:
            return time.strftime("%M:%S", time.gmtime(seconds))
        else:
            return time.strftime("%H:%M:%S", time.gmtime(seconds))



    def init_advanced_strategy_tab(self, parent_frame):
        """
        Initialize the Advanced "Logic Engine" Builder tab.
        """
        parent_frame.columnconfigure(0, weight=1)
        parent_frame.columnconfigure(1, weight=1)
        parent_frame.columnconfigure(2, weight=1)
        parent_frame.rowconfigure(0, weight=1)

        # === COLUMN 1: VARIABLES ===
        var_frame = ctk.CTkFrame(parent_frame)
        var_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        var_frame.columnconfigure(0, weight=1)
        var_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(var_frame, text="1. Variables (Triggers)", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Variable List
        self.adv_var_list = tk.Listbox(var_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.adv_var_list.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        # Add Variable Form
        form_frame = ctk.CTkFrame(var_frame)
        form_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(form_frame, text="Name:").pack(anchor="w", padx=5)
        self.adv_var_name = ctk.CTkEntry(form_frame)
        self.adv_var_name.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Type:").pack(anchor="w", padx=5)
        self.adv_var_type = ctk.CTkComboBox(form_frame, values=["gap_since_last", "streak_count", "strategy_metric", "last_outcome", "statistical_rank"])
        self.adv_var_type.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Target (e.g. red, Martingale):").pack(anchor="w", padx=5)
        self.adv_var_target = ctk.CTkEntry(form_frame)
        self.adv_var_target.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Property/Metric (Optional):").pack(anchor="w", padx=5)
        self.adv_var_prop = ctk.CTkEntry(form_frame, placeholder_text="e.g. loss_streak")
        self.adv_var_prop.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkButton(form_frame, text="Add Variable", command=self.adv_add_variable, fg_color="#27ae60").pack(fill="x", padx=5, pady=10)
        ctk.CTkButton(form_frame, text="Remove Selected", command=self.adv_remove_variable, fg_color="#c0392b").pack(fill="x", padx=5, pady=(0,5))

        # === COLUMN 2: RULES ===
        rule_frame = ctk.CTkFrame(parent_frame)
        rule_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        rule_frame.columnconfigure(0, weight=1)
        rule_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(rule_frame, text="2. Rules (Logic)", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Rule List
        self.adv_rule_list = tk.Listbox(rule_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.adv_rule_list.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        # Add Rule Form
        r_form_frame = ctk.CTkFrame(rule_frame)
        r_form_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(r_form_frame, text="Condition (IF):").pack(anchor="w", padx=5)
        
        cond_row = ctk.CTkFrame(r_form_frame, fg_color="transparent")
        cond_row.pack(fill="x", padx=5, pady=2)
        # Variable Dropdown (will need to refresh)
        self.adv_cond_left = ctk.CTkComboBox(cond_row, values=["(No Vars)"], width=100)
        self.adv_cond_left.pack(side="left", fill="x", expand=True)
        self.adv_cond_op = ctk.CTkComboBox(cond_row, values=[">", "<", "=", "!=", ">=", "<="], width=60)
        self.adv_cond_op.pack(side="left", padx=2)
        self.adv_cond_right = ctk.CTkEntry(cond_row, width=60, placeholder_text="Value")
        self.adv_cond_right.pack(side="left", fill="x", expand=True)
        
        ctk.CTkLabel(r_form_frame, text="Action (THEN):").pack(anchor="w", padx=5)
        act_row = ctk.CTkFrame(r_form_frame, fg_color="transparent")
        act_row.pack(fill="x", padx=5, pady=2)
        
        self.adv_act_type = ctk.CTkComboBox(act_row, values=["Bet", "Activate Strategy"], width=100)
        self.adv_act_type.pack(side="left", padx=(0,2))
        self.adv_act_target = ctk.CTkEntry(act_row, placeholder_text="Target (Red / Martingale)")
        self.adv_act_target.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(r_form_frame, text="Add Rule", command=self.adv_add_rule, fg_color="#2980b9").pack(fill="x", padx=5, pady=10)
        ctk.CTkButton(r_form_frame, text="Remove Selected", command=self.adv_remove_rule, fg_color="#c0392b").pack(fill="x", padx=5, pady=(0,5))

        # === COLUMN 3: PREVIEW & SAVE ===
        preview_frame = ctk.CTkFrame(parent_frame)
        preview_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(preview_frame, text="3. Strategy JSON", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        self.adv_json_preview = ctk.CTkTextbox(preview_frame, font=("Consolas", 11))
        self.adv_json_preview.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        ctrl_frame = ctk.CTkFrame(preview_frame)
        ctrl_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(ctrl_frame, text="Strategy Name:").pack(anchor="w", padx=5)
        self.adv_strat_name_entry = ctk.CTkEntry(ctrl_frame)
        self.adv_strat_name_entry.pack(fill="x", padx=5, pady=5)
        
        ctk.CTkButton(ctrl_frame, text="Refresh Preview", command=self.adv_update_preview).pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(ctrl_frame, text="Save Strategy", command=self.adv_save_strategy, fg_color="#2ecc71").pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(ctrl_frame, text="Load Strategy", command=self.adv_load_strategy, fg_color="#8e44ad").pack(fill="x", padx=5, pady=5)

        # Runtime Data
        self.adv_variables_data = {} # {name: {type:..., target:...}}
        self.adv_rules_data = [] # [{condition:..., action:...}]
        
    def adv_add_variable(self):
        name = self.adv_var_name.get().strip()
        vtype = self.adv_var_type.get()
        target = self.adv_var_target.get().strip()
        prop = self.adv_var_prop.get().strip()
        
        if not name or not target:
            messagebox.showerror("Error", "Name and Target are required.")
            return

        if name in self.adv_variables_data:
            messagebox.showerror("Error", "Variable name already exists.")
            return
            
        var_def = {"type": vtype, "target": target}
        if prop:
            if vtype == "strategy_metric":
                var_def["metric"] = prop
            elif vtype == "statistical_rank":
                var_def["metric"] = prop # coldest/hottest
            else:
                var_def["property"] = prop
                
        self.adv_variables_data[name] = var_def
        self.adv_var_list.insert(tk.END, f"{name} ({vtype} -> {target})")
        
        # Update dropdowns
        current_vals = list(self.adv_variables_data.keys())
        self.adv_cond_left.configure(values=current_vals)
        self.adv_update_preview()

    def adv_remove_variable(self):
        sel = self.adv_var_list.curselection()
        if not sel: return
        idx = sel[0]
        full_txt = self.adv_var_list.get(idx)
        name = full_txt.split(" ")[0]
        
        del self.adv_variables_data[name]
        self.adv_var_list.delete(idx)
        
        current_vals = list(self.adv_variables_data.keys())
        self.adv_cond_left.configure(values=current_vals if current_vals else ["(No Vars)"])
        self.adv_update_preview()

    def adv_add_rule(self):
        left = self.adv_cond_left.get()
        if not left or left == "(No Vars)":
            messagebox.showerror("Error", "Select a variable first.")
            return
            
        op = self.adv_cond_op.get()
        right = self.adv_cond_right.get().strip()
        
        act_type = self.adv_act_type.get()
        act_target = self.adv_act_target.get().strip()
        
        if not right or not act_target:
             messagebox.showerror("Error", "Condition Value and Action Target are required.")
             return
             
        # Normalize operator mapping
        op_map = {">": "gt", "<": "lt", "=": "eq", "!=": "neq", ">=": "gte", "<=": "lte"}
        
        rule = {
            "condition": {
                "left": f"@{left}",
                "operator": op_map.get(op, "eq"),
                "right": right
            },
            "action": {
                "type": "bet" if act_type == "Bet" else "activate_strategy",
                "target": act_target
            }
        }
        
        self.adv_rules_data.append(rule)
        self.adv_rule_list.insert(tk.END, f"IF {left} {op} {right} THEN {act_type} {act_target}")
        self.adv_update_preview()

    def adv_remove_rule(self):
        sel = self.adv_rule_list.curselection()
        if not sel: return
        idx = sel[0]
        self.adv_rule_list.delete(idx)
        self.adv_rules_data.pop(idx)
        self.adv_update_preview()

    def adv_update_preview(self):
        data = {
            "name": self.adv_strat_name_entry.get() or "MyStrategy",
            "variables": self.adv_variables_data,
            "rules": self.adv_rules_data
        }
        self.adv_json_preview.configure(state="normal")
        self.adv_json_preview.delete("1.0", tk.END)
        self.adv_json_preview.insert("1.0", json.dumps(data, indent=4))
        self.adv_json_preview.configure(state="disabled")

    def adv_save_strategy(self):
        import json
        name = self.adv_strat_name_entry.get().strip()
        if not name:
             messagebox.showerror("Error", "Enter a strategy name.")
             return
             
        data = {
            "name": name,
            "variables": self.adv_variables_data,
            "rules": self.adv_rules_data,
            "type": "advanced_logic"
        }
        
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "advanced_strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        filename = os.path.join(strategies_dir, f"{safe_name}.json")
        
        try:
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("Success", f"Advanced strategy saved:\n{filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def adv_load_strategy(self):
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "advanced_strategies")
        if not os.path.exists(strategies_dir):
            os.makedirs(strategies_dir)
            
        filename = filedialog.askopenfilename(initialdir=strategies_dir, filetypes=[("JSON Files", "*.json")])
        if not filename: return
        
        try:
            with open(filename, "r") as f:
                data = json.load(f)
                
            self.adv_strat_name_entry.delete(0, tk.END)
            self.adv_strat_name_entry.insert(0, data.get("name", "LoadedStrategy"))
            
            self.adv_variables_data = data.get("variables", {})
            self.adv_rules_data = data.get("rules", [])
            
            self.adv_var_list.delete(0, tk.END)
            for name, defense in self.adv_variables_data.items():
                self.adv_var_list.insert(tk.END, f"{name} ({defense.get('type')} -> {defense.get('target')})")
                
            self.adv_rule_list.delete(0, tk.END)
            for rule in self.adv_rules_data:
                c = rule.get("condition", {})
                a = rule.get("action", {})
                self.adv_rule_list.insert(tk.END, f"IF {c.get('left')} {c.get('operator')} {c.get('right')} THEN {a.get('type')} {a.get('target')}")
                
            # Update Dropdowns
            self.adv_cond_left.configure(values=list(self.adv_variables_data.keys()))
            self.adv_update_preview()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}")

# --- Add a simple tooltip helper class ---
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)
    def show_tip(self, event=None):
        if self.tipwindow or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert") if hasattr(self.widget, 'bbox') else (0, 0, 0, 0)
        x = x + self.widget.winfo_rootx() + 25
        y = y + self.widget.winfo_rooty() + 20
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        # Dark mode styling: Dark grey background, white text
        label = tk.Label(tw, text=self.text, justify=tk.LEFT, 
                       background="#2b2b2b", foreground="#ffffff", 
                       relief=tk.SOLID, borderwidth=1, 
                       font=("Arial", "9", "normal"))
        label.pack(ipadx=4, ipady=2)
    def hide_tip(self, event=None):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()

    def init_advanced_strategy_tab(self, parent_frame):
        """
        Initialize the Advanced "Logic Engine" Builder tab.
        """
        parent_frame.columnconfigure(0, weight=1)
        parent_frame.columnconfigure(1, weight=1)
        parent_frame.columnconfigure(2, weight=1)
        parent_frame.rowconfigure(0, weight=1)

        # === COLUMN 1: VARIABLES ===
        var_frame = ctk.CTkFrame(parent_frame)
        var_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        var_frame.columnconfigure(0, weight=1)
        var_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(var_frame, text="1. Variables (Triggers)", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Variable List
        self.adv_var_list = tk.Listbox(var_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.adv_var_list.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        # Add Variable Form
        form_frame = ctk.CTkFrame(var_frame)
        form_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(form_frame, text="Name:").pack(anchor="w", padx=5)
        self.adv_var_name = ctk.CTkEntry(form_frame)
        self.adv_var_name.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Type:").pack(anchor="w", padx=5)
        self.adv_var_type = ctk.CTkComboBox(form_frame, values=["gap_since_last", "streak_count", "strategy_metric", "last_outcome", "statistical_rank"])
        self.adv_var_type.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Target (e.g. red, Martingale):").pack(anchor="w", padx=5)
        self.adv_var_target = ctk.CTkEntry(form_frame)
        self.adv_var_target.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkLabel(form_frame, text="Property/Metric (Optional):").pack(anchor="w", padx=5)
        self.adv_var_prop = ctk.CTkEntry(form_frame, placeholder_text="e.g. loss_streak")
        self.adv_var_prop.pack(fill="x", padx=5, pady=2)
        
        ctk.CTkButton(form_frame, text="Add Variable", command=self.adv_add_variable, fg_color="#27ae60").pack(fill="x", padx=5, pady=10)
        ctk.CTkButton(form_frame, text="Remove Selected", command=self.adv_remove_variable, fg_color="#c0392b").pack(fill="x", padx=5, pady=(0,5))

        # === COLUMN 2: RULES ===
        rule_frame = ctk.CTkFrame(parent_frame)
        rule_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        rule_frame.columnconfigure(0, weight=1)
        rule_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(rule_frame, text="2. Rules (Logic)", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Rule List
        self.adv_rule_list = tk.Listbox(rule_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.adv_rule_list.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        # Add Rule Form
        r_form_frame = ctk.CTkFrame(rule_frame)
        r_form_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(r_form_frame, text="Condition (IF):").pack(anchor="w", padx=5)
        
        cond_row = ctk.CTkFrame(r_form_frame, fg_color="transparent")
        cond_row.pack(fill="x", padx=5, pady=2)
        # Variable Dropdown (will need to refresh)
        self.adv_cond_left = ctk.CTkComboBox(cond_row, values=["(No Vars)"], width=100)
        self.adv_cond_left.pack(side="left", fill="x", expand=True)
        self.adv_cond_op = ctk.CTkComboBox(cond_row, values=[">", "<", "=", "!=", ">=", "<="], width=60)
        self.adv_cond_op.pack(side="left", padx=2)
        self.adv_cond_right = ctk.CTkEntry(cond_row, width=60, placeholder_text="Value")
        self.adv_cond_right.pack(side="left", fill="x", expand=True)
        
        ctk.CTkLabel(r_form_frame, text="Action (THEN):").pack(anchor="w", padx=5)
        act_row = ctk.CTkFrame(r_form_frame, fg_color="transparent")
        act_row.pack(fill="x", padx=5, pady=2)
        
        self.adv_act_type = ctk.CTkComboBox(act_row, values=["Bet", "Activate Strategy"], width=100)
        self.adv_act_type.pack(side="left", padx=(0,2))
        self.adv_act_target = ctk.CTkEntry(act_row, placeholder_text="Target (Red / Martingale)")
        self.adv_act_target.pack(side="left", fill="x", expand=True)
        
        ctk.CTkButton(r_form_frame, text="Add Rule", command=self.adv_add_rule, fg_color="#2980b9").pack(fill="x", padx=5, pady=10)
        ctk.CTkButton(r_form_frame, text="Remove Selected", command=self.adv_remove_rule, fg_color="#c0392b").pack(fill="x", padx=5, pady=(0,5))

        # === COLUMN 3: PREVIEW & SAVE ===
        preview_frame = ctk.CTkFrame(parent_frame)
        preview_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)
        
        ctk.CTkLabel(preview_frame, text="3. Strategy JSON", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        self.adv_json_preview = ctk.CTkTextbox(preview_frame, font=("Consolas", 11))
        self.adv_json_preview.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        
        ctrl_frame = ctk.CTkFrame(preview_frame)
        ctrl_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(ctrl_frame, text="Strategy Name:").pack(anchor="w", padx=5)
        self.adv_strat_name_entry = ctk.CTkEntry(ctrl_frame)
        self.adv_strat_name_entry.pack(fill="x", padx=5, pady=5)
        
        ctk.CTkButton(ctrl_frame, text="Refresh Preview", command=self.adv_update_preview).pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(ctrl_frame, text="Save Strategy", command=self.adv_save_strategy, fg_color="#2ecc71").pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(ctrl_frame, text="Load Strategy", command=self.adv_load_strategy, fg_color="#8e44ad").pack(fill="x", padx=5, pady=5)

        # Runtime Data
        self.adv_variables_data = {} # {name: {type:..., target:...}}
        self.adv_rules_data = [] # [{condition:..., action:...}]
        
    def adv_add_variable(self):
        name = self.adv_var_name.get().strip()
        vtype = self.adv_var_type.get()
        target = self.adv_var_target.get().strip()
        prop = self.adv_var_prop.get().strip()
        
        if not name or not target:
            messagebox.showerror("Error", "Name and Target are required.")
            return

        if name in self.adv_variables_data:
            messagebox.showerror("Error", "Variable name already exists.")
            return
            
        var_def = {"type": vtype, "target": target}
        if prop:
            if vtype == "strategy_metric":
                var_def["metric"] = prop
            elif vtype == "statistical_rank":
                var_def["metric"] = prop # coldest/hottest
            else:
                var_def["property"] = prop
                
        self.adv_variables_data[name] = var_def
        self.adv_var_list.insert(tk.END, f"{name} ({vtype} -> {target})")
        
        # Update dropdowns
        current_vals = list(self.adv_variables_data.keys())
        self.adv_cond_left.configure(values=current_vals)
        self.adv_update_preview()

    def adv_remove_variable(self):
        sel = self.adv_var_list.curselection()
        if not sel: return
        idx = sel[0]
        full_txt = self.adv_var_list.get(idx)
        name = full_txt.split(" ")[0]
        
        del self.adv_variables_data[name]
        self.adv_var_list.delete(idx)
        
        current_vals = list(self.adv_variables_data.keys())
        self.adv_cond_left.configure(values=current_vals if current_vals else ["(No Vars)"])
        self.adv_update_preview()

    def adv_add_rule(self):
        left = self.adv_cond_left.get()
        if not left or left == "(No Vars)":
            messagebox.showerror("Error", "Select a variable first.")
            return
            
        op = self.adv_cond_op.get()
        right = self.adv_cond_right.get().strip()
        
        act_type = self.adv_act_type.get()
        act_target = self.adv_act_target.get().strip()
        
        if not right or not act_target:
             messagebox.showerror("Error", "Condition Value and Action Target are required.")
             return
             
        # Normalize operator mapping
        op_map = {">": "gt", "<": "lt", "=": "eq", "!=": "neq", ">=": "gte", "<=": "lte"}
        
        rule = {
            "condition": {
                "left": f"@{left}",
                "operator": op_map.get(op, "eq"),
                "right": right
            },
            "action": {
                "type": "bet" if act_type == "Bet" else "activate_strategy",
                "target": act_target
            }
        }
        
        self.adv_rules_data.append(rule)
        self.adv_rule_list.insert(tk.END, f"IF {left} {op} {right} THEN {act_type} {act_target}")
        self.adv_update_preview()

    def adv_remove_rule(self):
        sel = self.adv_rule_list.curselection()
        if not sel: return
        idx = sel[0]
        self.adv_rule_list.delete(idx)
        self.adv_rules_data.pop(idx)
        self.adv_update_preview()

    def adv_update_preview(self):
        data = {
            "name": self.adv_strat_name_entry.get() or "MyStrategy",
            "variables": self.adv_variables_data,
            "rules": self.adv_rules_data
        }
        self.adv_json_preview.configure(state="normal")
        self.adv_json_preview.delete("1.0", tk.END)
        self.adv_json_preview.insert("1.0", json.dumps(data, indent=4))
        self.adv_json_preview.configure(state="disabled")

    def adv_save_strategy(self):
        import json
        name = self.adv_strat_name_entry.get().strip()
        if not name:
             messagebox.showerror("Error", "Enter a strategy name.")
             return
             
        data = {
            "name": name,
            "variables": self.adv_variables_data,
            "rules": self.adv_rules_data,
            "type": "advanced_logic"
        }
        
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "advanced_strategies")
        os.makedirs(strategies_dir, exist_ok=True)
        
        safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '-', '_')]).strip()
        filename = os.path.join(strategies_dir, f"{safe_name}.json")
        
        try:
            with open(filename, "w") as f:
                json.dump(data, f, indent=4)
            messagebox.showinfo("Success", f"Advanced strategy saved:\n{filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")

    def adv_load_strategy(self):
        strategies_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "advanced_strategies")
        if not os.path.exists(strategies_dir):
            os.makedirs(strategies_dir)
            
        filename = filedialog.askopenfilename(initialdir=strategies_dir, filetypes=[("JSON Files", "*.json")])
        if not filename: return
        
        try:
            with open(filename, "r") as f:
                data = json.load(f)
                
            self.adv_strat_name_entry.delete(0, tk.END)
            self.adv_strat_name_entry.insert(0, data.get("name", "LoadedStrategy"))
            
            self.adv_variables_data = data.get("variables", {})
            self.adv_rules_data = data.get("rules", [])
            
            self.adv_var_list.delete(0, tk.END)
            for name, defense in self.adv_variables_data.items():
                self.adv_var_list.insert(tk.END, f"{name} ({defense.get('type')} -> {defense.get('target')})")
                
            self.adv_rule_list.delete(0, tk.END)
            for rule in self.adv_rules_data:
                c = rule.get("condition", {})
                a = rule.get("action", {})
                self.adv_rule_list.insert(tk.END, f"IF {c.get('left')} {c.get('operator')} {c.get('right')} THEN {a.get('type')} {a.get('target')}")
                
            # Update Dropdowns
            self.adv_cond_left.configure(values=list(self.adv_variables_data.keys()))
            self.adv_update_preview()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = RouletteBotGUI(root)
    root.mainloop()
