import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import threading
import json
import os
from datetime import datetime
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError:
    plt = None
    FigureCanvasTkAgg = None
from core.backtesting import RouletteBacktester
from gui.dynamic_rules_editor import DynamicRulesEditor

class BacktestingGUI:
    # Persisted config — survives bot restarts. Same shape as the JSON the
    # Export Config button writes and backtest_cli.py reads.
    _LAST_CONFIG_PATH = os.path.join(
        os.path.expanduser("~"), ".spinedge", "backtest_last_config.json"
    )

    def __init__(self, parent_frame, app=None):
        self.parent_frame = parent_frame
        self.app = app
        self.backtester = RouletteBacktester()
        self.results = {}
        self.analysis = {}
        # Tracks live matplotlib figures/canvases per chart "slot" so each
        # re-render can deterministically close the previous one. Without this,
        # repeated backtests leaked a Figure + Agg buffer + mplcursors hover
        # handler every run (destroying the Tk widget alone never frees them),
        # which made the whole app progressively laggy. See _register_fig /
        # _teardown_figs.
        self._fig_registry = {}
        self.create_widgets()
        # Restore the user's previous backtest config (if any) so they
        # don't lose their setup on a bot restart.
        self._load_last_config()

    def create_widgets(self):
        # Configure parent grid
        self.parent_frame.grid_columnconfigure(0, weight=1)
        self.parent_frame.grid_rowconfigure(0, weight=1)

        # Main Frame (non-scrollable to avoid nesting issues)
        self.main_scroll = ctk.CTkFrame(self.parent_frame)
        self.main_scroll.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.main_scroll.grid_columnconfigure(0, weight=1)

        # --- Configuration Section ---
        self.config_frame = ctk.CTkFrame(self.main_scroll)
        self.config_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        self.config_frame.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(self.config_frame, text="Configuration", font=("Roboto", 16, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=10)

        # Mode Selection
        self.mode_var = ctk.StringVar(value="single")
        ctk.CTkLabel(self.config_frame, text="Mode:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        
        mode_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        mode_frame.grid(row=1, column=1, columnspan=3, sticky="w")
        
        ctk.CTkRadioButton(mode_frame, text="Single Strategy", variable=self.mode_var, value="single", command=self._toggle_mode).pack(side="left", padx=5)
        ctk.CTkRadioButton(mode_frame, text="Rotation List", variable=self.mode_var, value="rotation", command=self._toggle_mode).pack(side="left", padx=10)

        # Strategy Selection
        ctk.CTkLabel(self.config_frame, text="Strategy:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        
        strategies = ["martingale", "fibonacci", "dalembert", "flat"]
        if self.app:
            try:
                strategies = self.app.get_all_strategy_names()
            except Exception as e:
                print(f"Error fetching strategies: {e}")
                
        self.strategy_var = ctk.StringVar(value="martingale" if "martingale" in strategies else strategies[0])
        # Stash master list so the search filter can restore entries when
        # the user clears their query.
        self._strategy_master_list = list(strategies)
        self.strategy_combo = ctk.CTkComboBox(self.config_frame, variable=self.strategy_var, values=strategies, state="normal")
        self.strategy_combo.grid(row=2, column=1, sticky="ew", padx=5, pady=5)
        self._wire_searchable_combo(self.strategy_combo, "_strategy_master_list")

        # Rotation Selection
        ctk.CTkLabel(self.config_frame, text="Rotation Preset:").grid(row=2, column=2, sticky="w", padx=5, pady=5)
        self.rotation_var = ctk.StringVar(value="")
        
        rotation_presets = []
        try:
            import os, glob, json
            base_dir = os.path.dirname(os.path.dirname(__file__)) 
            presets_dir = os.path.join(base_dir, "config", "rotation_presets")
            if os.path.exists(presets_dir):
                files = glob.glob(os.path.join(presets_dir, "*.json"))
                
                # Fetch entitlements from main app
                user_tier = "BASIC"
                entitlements = []
                if self.app and hasattr(self.app, "license_manager") and self.app.license_manager.license_data:
                    user_tier = self.app.license_manager.license_data.get("subscription_tier", "BASIC")
                    entitlements = self.app.license_manager.entitlements
                TIER_LEVELS = {"FREE": 0, "BASIC": 1, "PLUS": 2, "PRO": 3, "ADMIN": 99}
                user_level = TIER_LEVELS.get(user_tier.upper(), 0)
                
                for f in files:
                    try:
                        with open(f, "r") as json_file:
                            data = json.load(json_file)
                            bundle_id = data.get("bundle_id")
                            if bundle_id and user_level < TIER_LEVELS.get("ADMIN", 99):
                                if bundle_id not in entitlements:
                                    continue # Skip locked
                        rotation_presets.append(os.path.splitext(os.path.basename(f))[0])
                    except Exception as e:
                        print(f"Failed to read preset {f}: {e}")
                        
        except Exception as e:
            print(f"Error fetching rotation presets: {e}")

        if not rotation_presets:
            rotation_presets = ["No Lists Found"]

        self._rotation_master_list = list(rotation_presets)
        self.rotation_combo = ctk.CTkComboBox(self.config_frame, variable=self.rotation_var, values=rotation_presets, state="disabled")
        self.rotation_combo.grid(row=2, column=3, sticky="ew", padx=5, pady=5)
        if rotation_presets and rotation_presets[0] != "No Lists Found":
             self.rotation_combo.set(rotation_presets[0])
        # Searchable too — gets enabled when the user picks "Rotation" mode below.
        self._wire_searchable_combo(self.rotation_combo, "_rotation_master_list")

        # Rotation Mode
        ctk.CTkLabel(self.config_frame, text="Rotation Order:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.rotation_mode_var = ctk.StringVar(value="sequential")
        self.rotation_mode_combo = ctk.CTkComboBox(self.config_frame, variable=self.rotation_mode_var, 
                                               values=["sequential", "random", "smart_rank"], state="disabled")
        self.rotation_mode_combo.grid(row=3, column=1, sticky="ew", padx=5, pady=5)

        # Base Bet
        ctk.CTkLabel(self.config_frame, text="Base Bet ($):").grid(row=3, column=2, sticky="w", padx=5, pady=5)
        self.base_bet_str = ctk.StringVar(value="1.0")
        ctk.CTkEntry(self.config_frame, textvariable=self.base_bet_str).grid(row=3, column=3, sticky="ew", padx=5, pady=5)

        # Initial Balance
        ctk.CTkLabel(self.config_frame, text="Initial Balance ($):").grid(row=4, column=0, sticky="w", padx=5, pady=5)
        self.init_bal_str = ctk.StringVar(value="1000.0")
        ctk.CTkEntry(self.config_frame, textvariable=self.init_bal_str).grid(row=4, column=1, sticky="ew", padx=5, pady=5)

        # Max Loss
        ctk.CTkLabel(self.config_frame, text="Max Loss ($):").grid(row=4, column=2, sticky="w", padx=5, pady=5)
        self.max_loss_str = ctk.StringVar(value="500.0")
        ctk.CTkEntry(self.config_frame, textvariable=self.max_loss_str).grid(row=4, column=3, sticky="ew", padx=5, pady=5)

        # Progression
        ctk.CTkLabel(self.config_frame, text="Progression:").grid(row=5, column=0, sticky="w", padx=5, pady=5)
        self.progression_var = ctk.StringVar(value="martingale")
        
        prog_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        prog_frame.grid(row=5, column=1, sticky="ew")
        
        self.progression_combo = ctk.CTkComboBox(prog_frame, variable=self.progression_var,
                        values=["martingale", "fibonacci", "dalembert", "flat", "custom_sequence", "dynamic"],
                        command=self._toggle_progression, width=140)
        self.progression_combo.pack(side="left", padx=(5, 5))
        
        self.dynamic_rules_btn = ctk.CTkButton(prog_frame, text="⚙", width=30, command=self._open_dynamic_rules_dialog, state="disabled", fg_color="gray")
        self.dynamic_rules_btn.pack(side="left")

        # Rounds
        ctk.CTkLabel(self.config_frame, text="Rounds per Sim:").grid(row=5, column=2, sticky="w", padx=5, pady=5)
        self.rounds_str = ctk.StringVar(value="100")
        ctk.CTkEntry(self.config_frame, textvariable=self.rounds_str).grid(row=5, column=3, sticky="ew", padx=5, pady=5)

        # Sims
        self.sims_label = ctk.CTkLabel(self.config_frame, text="Num Simulations:")
        self.sims_label.grid(row=6, column=0, sticky="w", padx=5, pady=5)
        self.sims_str = ctk.StringVar(value="1")
        ctk.CTkEntry(self.config_frame, textvariable=self.sims_str).grid(row=6, column=1, sticky="ew", padx=5, pady=5)

        # Max Consec Losses
        ctk.CTkLabel(self.config_frame, text="Max Consec Losses:").grid(row=6, column=2, sticky="w", padx=5, pady=5)
        self.max_consec_str = ctk.StringVar(value="10")
        ctk.CTkEntry(self.config_frame, textvariable=self.max_consec_str).grid(row=6, column=3, sticky="ew", padx=5, pady=5)
        


        # --- Session Control Section ---
        ctk.CTkLabel(self.config_frame, text="Session Control", font=("Roboto", 14, "bold")).grid(row=7, column=0, columnspan=4, sticky="w", padx=10, pady=(15, 5))
        
        # Enable Toggle
        self.enable_session_stops_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.config_frame, text="Enable Session Stops", variable=self.enable_session_stops_var).grid(row=8, column=0, columnspan=2, sticky="w", padx=5, pady=2)

        # Profit Target & Streaks
        ctk.CTkLabel(self.config_frame, text="Profit Target ($):").grid(row=9, column=0, sticky="w", padx=5, pady=5)
        self.profit_target_str = ctk.StringVar(value="0") 
        ctk.CTkEntry(self.config_frame, textvariable=self.profit_target_str).grid(row=9, column=1, sticky="ew", padx=5, pady=5)
        
        ctk.CTkLabel(self.config_frame, text="Max Win Streak:").grid(row=9, column=2, sticky="w", padx=5, pady=5)
        self.max_win_streak_str = ctk.StringVar(value="0") 
        ctk.CTkEntry(self.config_frame, textvariable=self.max_win_streak_str).grid(row=9, column=3, sticky="ew", padx=5, pady=5)

        # Trailing Stop Setup
        self.enable_trailing_stop_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.config_frame, text="Enable Trailing Stop", variable=self.enable_trailing_stop_var).grid(row=10, column=0, columnspan=2, sticky="w", padx=5, pady=2)

        ctk.CTkLabel(self.config_frame, text="Trailing Stop ($):").grid(row=10, column=2, sticky="w", padx=5, pady=5)
        self.trailing_stop_str = ctk.StringVar(value="0")
        ctk.CTkEntry(self.config_frame, textvariable=self.trailing_stop_str).grid(row=10, column=3, sticky="ew", padx=5, pady=5)

        # Extensions
        ext_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        ext_frame.grid(row=11, column=0, columnspan=4, sticky="w", padx=5)
        
        self.ext_win_var = ctk.BooleanVar(value=False)
        self.ext_high_var = ctk.BooleanVar(value=False)
        
        ctk.CTkCheckBox(ext_frame, text="Extend until Win", variable=self.ext_win_var).pack(side="left", padx=5)
        ctk.CTkCheckBox(ext_frame, text="Extend until High", variable=self.ext_high_var).pack(side="left", padx=10)
        
        ctk.CTkLabel(ext_frame, text="Max Ext Rounds:").pack(side="left", padx=(20, 5))
        self.max_ext_rounds_str = ctk.StringVar(value="20")
        ctk.CTkEntry(ext_frame, textvariable=self.max_ext_rounds_str, width=60).pack(side="left", padx=5)

        ctk.CTkLabel(ext_frame, text="Give Up Amt ($):").pack(side="left", padx=(10, 5))
        self.ext_give_up_str = ctk.StringVar(value="50")
        ctk.CTkEntry(ext_frame, textvariable=self.ext_give_up_str, width=60).pack(side="left", padx=5)


        # --- Timing Simulation ---
        ctk.CTkLabel(self.config_frame, text="Timing Simulation", font=("Roboto", 14, "bold")).grid(row=12, column=0, columnspan=4, sticky="w", padx=10, pady=(15, 5))
        
        # Avg Spin Time
        ctk.CTkLabel(self.config_frame, text="Avg Spin Time (s):").grid(row=13, column=0, sticky="w", padx=5, pady=5)
        self.spin_time_str = ctk.StringVar(value="20")
        ctk.CTkEntry(self.config_frame, textvariable=self.spin_time_str).grid(row=13, column=1, sticky="ew", padx=5, pady=5)
        
        # Session Duration (Overrides Rounds)
        ctk.CTkLabel(self.config_frame, text="Session Duration (min):").grid(row=13, column=2, sticky="w", padx=5, pady=5)
        self.sess_duration_str = ctk.StringVar(value="60")
        ctk.CTkEntry(self.config_frame, textvariable=self.sess_duration_str).grid(row=13, column=3, sticky="ew", padx=5, pady=5)

        # Break Duration
        ctk.CTkLabel(self.config_frame, text="Break Duration (min):").grid(row=14, column=0, sticky="w", padx=5, pady=5)
        self.break_duration_str = ctk.StringVar(value="15")
        ctk.CTkEntry(self.config_frame, textvariable=self.break_duration_str).grid(row=14, column=1, sticky="ew", padx=5, pady=5)

        # --- Campaign / Global Settings ---
        ctk.CTkLabel(self.config_frame, text="Campaign / Global Limit", font=("Roboto", 14, "bold")).grid(row=15, column=0, columnspan=4, sticky="w", padx=10, pady=(15, 5))
        
        # Simulation Mode
        ctk.CTkLabel(self.config_frame, text="Simulation Mode:").grid(row=16, column=0, sticky="w", padx=5, pady=5)
        self.sim_mode_var = ctk.StringVar(value="independent")
        sim_mode_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        sim_mode_frame.grid(row=16, column=1, columnspan=3, sticky="w")
        ctk.CTkRadioButton(sim_mode_frame, text="Independent (Monte Carlo)", variable=self.sim_mode_var, value="independent", command=self._update_sim_labels).pack(side="left", padx=5)
        ctk.CTkRadioButton(sim_mode_frame, text="Sequential (Campaign)", variable=self.sim_mode_var, value="sequential", command=self._update_sim_labels).pack(side="left", padx=10)

        # Global Limits
        self.enable_global_limits_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self.config_frame, text="Enable Global Limits", variable=self.enable_global_limits_var).grid(row=17, column=0, columnspan=4, sticky="w", padx=5, pady=2)
        
        ctk.CTkLabel(self.config_frame, text="Global Profit Target ($):").grid(row=18, column=0, sticky="w", padx=5, pady=5)
        self.global_profit_str = ctk.StringVar(value="0")
        ctk.CTkEntry(self.config_frame, textvariable=self.global_profit_str).grid(row=18, column=1, sticky="ew", padx=5, pady=5)

        ctk.CTkLabel(self.config_frame, text="Global Stop Loss ($):").grid(row=18, column=2, sticky="w", padx=5, pady=5)
        self.global_loss_str = ctk.StringVar(value="0")
        ctk.CTkEntry(self.config_frame, textvariable=self.global_loss_str).grid(row=18, column=3, sticky="ew", padx=5, pady=5)

        # --- Data Source Section ---
        ctk.CTkLabel(self.config_frame, text="Data Source", font=("Roboto", 14, "bold")).grid(row=19, column=0, columnspan=4, sticky="w", padx=10, pady=(15, 5))
        
        self.data_source_var = ctk.StringVar(value="simulation")
        source_frame = ctk.CTkFrame(self.config_frame, fg_color="transparent")
        source_frame.grid(row=20, column=0, columnspan=4, sticky="w", padx=5)
        
        ctk.CTkRadioButton(source_frame, text="Random Simulation", variable=self.data_source_var, 
                       value="simulation", command=self._toggle_data_source).pack(side="left", padx=5)
        ctk.CTkRadioButton(source_frame, text="Historical DB", variable=self.data_source_var, 
                       value="db", command=self._toggle_data_source).pack(side="left", padx=10)
        
        self.db_limit_label = ctk.CTkLabel(source_frame, text="Use latest K spins:")
        self.db_limit_label.pack(side="left", padx=(20, 5))

        self.db_limit_str = ctk.StringVar(value="1000")
        self.db_limit_entry = ctk.CTkEntry(source_frame, textvariable=self.db_limit_str, width=80)
        self.db_limit_entry.pack(side="left")

        # Data offset — lets users backtest different windows of history.
        # Defaults to 0 = "most recent N". Set to e.g. 1000 to test the slice
        # 1000-2000 spins ago. This makes "different starts" actually mean
        # different data each run instead of always replaying the latest slice.
        ctk.CTkLabel(source_frame, text="Skip latest:").pack(side="left", padx=(20, 5))
        self.db_offset_str = ctk.StringVar(value="0")
        self.db_offset_entry = ctk.CTkEntry(source_frame, textvariable=self.db_offset_str, width=80)
        self.db_offset_entry.pack(side="left")

        # Preview button: resolves the actual slice that will be replayed and
        # shows it in a status line below. Answers "am I really playing the
        # newest spins?" without the user having to read the runner log.
        ctk.CTkButton(
            source_frame, text="🔍 Preview Slice", width=130, height=26,
            command=self._preview_db_slice,
            fg_color="#0ea5e9", hover_color="#0284c7",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(20, 0))

        # Lock-slice toggle. When on, the slice is pinned to a snapshot of
        # DB max(id) at the moment of enable, so subsequent runs replay the
        # SAME rows even while the spin-watcher keeps appending to the table
        # in the background. Critical for reproducibility — without it, two
        # runs seconds apart can use different data and produce different
        # results, breaking before/after comparisons.
        self._db_lock_var = tk.BooleanVar(value=False)
        self._db_locked_anchor_id = None
        ctk.CTkCheckBox(
            source_frame, text="🔒 Lock slice", variable=self._db_lock_var,
            command=self._on_db_lock_toggle,
            font=("Segoe UI", 11, "bold"), fg_color="#a855f7",
        ).pack(side="left", padx=(10, 0))

        # Status line — populated by _preview_db_slice (and after a run
        # completes via _populate_data_slice_status). Wraps so a long
        # "playing oldest→newest from X to Y" message stays readable.
        self._db_slice_status = ctk.CTkLabel(
            self.config_frame,
            text="ℹ DB pulls newest K rows (ORDER BY timestamp DESC) then "
                 "replays oldest→newest. Click Preview to see the exact slice.",
            font=("Segoe UI", 11, "italic"), text_color="#94a3b8",
            anchor="w", justify="left", wraplength=900,
        )
        self._db_slice_status.grid(row=21, column=0, columnspan=4,
                                   sticky="ew", padx=10, pady=(2, 4))

        # --- Action Buttons (grouped: primary on left, secondary on right) ---
        button_frame = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        button_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=10)

        # Left group — PRIMARY actions (kick off a backtest)
        _primary = ctk.CTkFrame(button_frame, fg_color="transparent")
        _primary.pack(side="left", fill="x", expand=True)

        self.run_button = ctk.CTkButton(
            _primary, text="🚀 Run Backtest", command=self.run_backtest,
            font=("Roboto", 14, "bold"), height=40,
            fg_color="#16a34a", hover_color="#15803d",
        )
        self.run_button.pack(side="left", padx=(0, 6), fill="x", expand=True)

        # 📦 Bundle Backtest — load a bundle JSON and run with its FULL config
        # (rotation, dynamic_rules, all risk limits). Bypasses the GUI fields
        # so there's no GUI-translation step where things can silently drift.
        self.bundle_button = ctk.CTkButton(
            _primary, text="📦 Backtest Bundle", command=self._open_bundle_backtest_dialog,
            fg_color="#0e7490", hover_color="#0891b2",
            font=("Roboto", 14, "bold"), height=40,
        )
        self.bundle_button.pack(side="left", padx=6, fill="x", expand=True)

        # 🔬 Sweep — run many backtests across a parameter grid and rank
        # them by PnL. Opens a dialog where you pick 1-2 params + values.
        self.sweep_button = ctk.CTkButton(
            _primary, text="🔬 Sweep", command=self._open_sweep_dialog,
            fg_color="#5b21b6", hover_color="#7c3aed",
            font=("Roboto", 12), height=40,
        )
        self.sweep_button.pack(side="left", padx=6, fill="x", expand=True)

        # 🏆 Batch — pick N bundles, run them all against the same data slice,
        # rank them by composite score. Best way to find the strongest bundle
        # in your library without running them one-by-one and eyeballing.
        self.batch_button = ctk.CTkButton(
            _primary, text="🏆 Batch", command=self._open_batch_bundles_dialog,
            fg_color="#b45309", hover_color="#d97706",
            font=("Roboto", 12), height=40,
        )
        self.batch_button.pack(side="left", padx=6, fill="x", expand=True)

        # Visual divider between primary and secondary clusters
        ctk.CTkFrame(button_frame, fg_color="#27272a", width=2, height=36).pack(side="left", padx=12)

        # Right group — SECONDARY actions (work with results / config)
        _secondary = ctk.CTkFrame(button_frame, fg_color="transparent")
        _secondary.pack(side="left", fill="x")

        self.save_button = ctk.CTkButton(
            _secondary, text="💾 Save", command=self.save_results, state="disabled",
            fg_color="#475569", hover_color="#64748b", height=40, width=90,
        )
        self.save_button.pack(side="left", padx=4)

        self.export_button = ctk.CTkButton(
            _secondary, text="📊 Report", command=self.export_report, state="disabled",
            fg_color="#475569", hover_color="#64748b", height=40, width=90,
        )
        self.export_button.pack(side="left", padx=4)

        self.export_csv_button = ctk.CTkButton(
            _secondary, text="📋 CSV", command=self._export_detailed_csv, state="disabled",
            fg_color="#475569", hover_color="#64748b", height=40, width=90,
        )
        self.export_csv_button.pack(side="left", padx=4)

        # Export Config → JSON file that backtest_cli.py can replay for
        # bit-identical results. The button is always enabled — users can
        # save the config before running.
        self.export_config_button = ctk.CTkButton(
            _secondary, text="📤 Config", command=self._export_config_json,
            fg_color="#374151", hover_color="#4b5563", height=40, width=90,
        )
        self.export_config_button.pack(side="left", padx=4)

        # ── Recent Runs bar ──────────────────────────────────────────────
        # Every completed run auto-saves to ~/.spinedge/backtest_runs/.
        # Pick one here to re-load its summary / detailed log / graph and
        # Round Audit modal — survives bot restarts.
        recent_frame = ctk.CTkFrame(self.main_scroll, fg_color="#0f172a", corner_radius=8)
        recent_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(recent_frame, text="📂  Recent Runs:",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#cbd5e1").pack(side="left", padx=(10, 6), pady=8)
        self._recent_run_var = tk.StringVar(value="(none)")
        self._recent_runs_combo = ctk.CTkComboBox(
            recent_frame, variable=self._recent_run_var, values=["(none)"],
            state="readonly", width=420,
            command=self._on_recent_run_selected,
        )
        self._recent_runs_combo.pack(side="left", padx=4, pady=8)
        ctk.CTkButton(recent_frame, text="🔄", width=34,
                      command=self._refresh_recent_runs_dropdown,
                      fg_color="#334155", hover_color="#475569").pack(side="left", padx=(2, 0), pady=8)
        ctk.CTkButton(recent_frame, text="🔀 Compare Runs", width=140,
                      command=self._open_compare_dialog,
                      fg_color="#7c3aed", hover_color="#9333ea",
                      font=("Segoe UI", 11, "bold")).pack(side="left", padx=(6, 0), pady=8)
        ctk.CTkButton(recent_frame, text="📁 Open Folder", width=120,
                      command=self._open_runs_folder,
                      fg_color="#334155", hover_color="#475569").pack(side="left", padx=(6, 10), pady=8)
        self._recent_runs_hint = ctk.CTkLabel(
            recent_frame, text="(no saved runs yet — finish a backtest to populate this)",
            font=("Segoe UI", 11), text_color="#64748b",
        )
        self._recent_runs_hint.pack(side="left", padx=(4, 0), pady=8)

        # Progress
        self.progress_bar = ctk.CTkProgressBar(self.main_scroll)
        self.progress_bar.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        self.progress_bar.set(0)

        # --- Results Section ---
        self.results_frame = ctk.CTkTabview(self.main_scroll)
        self.results_frame.grid(row=4, column=0, sticky="nsew", padx=5, pady=5)
        self.results_frame.grid_columnconfigure(0, weight=1)
        self.results_frame.add("Summary")
        self.results_frame.add("📊 Metrics")
        self.results_frame.add("🔬 Analytics")
        self.results_frame.add("🔍 Session Audit")
        self.results_frame.add("Detailed Log")
        self.results_frame.add("Graph")

        # Summary Tab
        self.summary_text = ctk.CTkTextbox(self.results_frame.tab("Summary"), height=300)
        self.summary_text.pack(fill="both", expand=True, padx=5, pady=5)

        # ── Pro Metrics Tab ───────────────────────────────────────────────
        # Dashboard of derived metrics computed from per-session BacktestResult.
        # Filled by _populate_metrics_panel after each run / on load.
        _metrics_tab = self.results_frame.tab("📊 Metrics")
        _metrics_tab.grid_columnconfigure(0, weight=1)
        _metrics_tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(_metrics_tab,
                     text="Performance Metrics",
                     font=("Roboto", 16, "bold"),
                     text_color="#facc15").grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))

        self._metrics_grid = ctk.CTkScrollableFrame(_metrics_tab, fg_color="transparent")
        self._metrics_grid.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        for c in range(3):
            self._metrics_grid.grid_columnconfigure(c, weight=1, uniform="metrics_cols")
        # Card refs populated by _create_metric_cards (lazy init)
        self._metric_card_widgets = {}
        self._create_metric_cards()

        # ── Analytics Tab ────────────────────────────────────────────────
        # Visual diagnostics: distribution of outcomes, roulette-specific
        # group / number breakdown, top losers/winners with click-to-jump,
        # underwater drawdown plot, and parallel-mode attribution table.
        # All panels rebuild on every run via _populate_analytics_tab.
        _analytics_tab = self.results_frame.tab("🔬 Analytics")
        _analytics_tab.grid_columnconfigure(0, weight=1)
        _analytics_tab.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(_analytics_tab, text="Analytics",
                     font=("Roboto", 16, "bold"),
                     text_color="#a78bfa").grid(row=0, column=0, sticky="w",
                                                padx=14, pady=(14, 4))
        self._analytics_status_lbl = ctk.CTkLabel(
            _analytics_tab, text="(run a backtest to populate)",
            font=("Segoe UI", 11, "italic"), text_color="#64748b",
        )
        self._analytics_status_lbl.grid(row=0, column=0, sticky="e",
                                        padx=14, pady=(18, 0))
        # The scrollable host where every panel lives. Re-built end-to-end
        # on each run to avoid stale charts/widgets.
        self._analytics_scroll = ctk.CTkScrollableFrame(_analytics_tab)
        self._analytics_scroll.grid(row=1, column=0, sticky="nsew",
                                    padx=10, pady=8)
        self._analytics_scroll.grid_columnconfigure((0, 1), weight=1)

        # ── Session Audit Tab ─────────────────────────────────────────────
        # One row per session with the audit trail: PnL, rounds, what stopped
        # it, what escalated base_bet/max_loss it ran with. Double-click a
        # session row to jump straight to its FIRST round in Round Audit.
        _audit_tab = self.results_frame.tab("🔍 Session Audit")
        _audit_tab.grid_columnconfigure(0, weight=1)
        _audit_tab.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(_audit_tab, text="Per-Session Audit Trail",
                     font=("Roboto", 16, "bold"),
                     text_color="#22d3ee").grid(row=0, column=0, sticky="w",
                                                padx=14, pady=(14, 4))
        self._audit_summary_lbl = ctk.CTkLabel(
            _audit_tab, text="(no run loaded)",
            font=("Segoe UI", 12), text_color="#94a3b8",
        )
        self._audit_summary_lbl.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 8))

        # Treeview for one-row-per-session
        _audit_container = tk.Frame(_audit_tab, bg="#0b1220")
        _audit_container.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        _audit_container.grid_rowconfigure(0, weight=1)
        _audit_container.grid_columnconfigure(0, weight=1)

        _style = ttk.Style()
        _style.configure("Audit.Treeview", background="#0f172a",
                         foreground="#e2e8f0", fieldbackground="#0f172a",
                         rowheight=24, font=("Consolas", 12), borderwidth=0)
        _style.configure("Audit.Treeview.Heading", background="#1e293b",
                         foreground="#22d3ee", font=("Segoe UI", 10, "bold"))

        # `skips` and `trig` columns are zero for plain-rotation runs and
        # non-zero only when the bundle was configured with conditional
        # selection — they let users see at a glance how often the
        # TriggerEngine sat out or fired during each session.
        audit_cols = ("sess", "start_bal", "end_bal", "pnl", "rounds", "wins",
                      "losses", "skips", "trig", "max_dd", "base_bet",
                      "max_loss", "esc", "stop_reason", "stop_message")
        audit_widths = (50, 80, 80, 90, 60, 50, 55, 55, 55, 80, 75, 75, 50, 130, 280)
        audit_align  = ("center", "e", "e", "e", "e", "e", "e", "e", "e",
                        "e", "e", "e", "center", "w", "w")
        audit_titles = ("Sess#", "Start $", "End $", "PnL", "Rounds", "W", "L",
                        "⏸ Skip", "🎯 Trig",
                        "Max DD", "Base $", "SL $", "Esc",
                        "Stop reason", "Detail")
        self._audit_tree = ttk.Treeview(
            _audit_container, columns=audit_cols, show="headings",
            style="Audit.Treeview", height=18, selectmode="browse",
        )
        for c, w, a, t in zip(audit_cols, audit_widths, audit_align, audit_titles):
            self._audit_tree.heading(c, text=t,
                                     command=lambda col=c: self._sort_audit_tree(col, False))
            self._audit_tree.column(c, width=w, anchor=a, stretch=(c == "stop_message"))
        # Row-color tags by outcome
        self._audit_tree.tag_configure("win_sess",  background="#0f1d17")
        self._audit_tree.tag_configure("loss_sess", background="#1d0f0f")
        self._audit_tree.tag_configure("bankrupt",  background="#350f0f", foreground="#fca5a5")
        self._audit_tree.tag_configure("escalated", background="#2b1a0a", foreground="#fbbf24")
        self._audit_tree.tag_configure("neutral",   background="#0f172a")

        _avbar = ttk.Scrollbar(_audit_container, orient="vertical", command=self._audit_tree.yview)
        self._audit_tree.configure(yscrollcommand=_avbar.set)
        self._audit_tree.grid(row=0, column=0, sticky="nsew")
        _avbar.grid(row=0, column=1, sticky="ns")
        # Double-click → jump to that session's first round in Round Audit
        self._audit_tree.bind("<Double-1>", self._on_audit_session_double_click)

        # ── Escalation log strip below the table ──
        ctk.CTkLabel(_audit_tab, text="Escalation Log:",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#fbbf24").grid(row=3, column=0, sticky="w",
                                                 padx=14, pady=(2, 2))
        self._audit_esc_log = ctk.CTkTextbox(_audit_tab, height=110,
                                              font=("Consolas", 12),
                                              fg_color="#0b1220",
                                              text_color="#fde68a")
        self._audit_esc_log.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 10))

        # Detailed Tab — streaming log up top + sortable Treeview below
        _detail_tab = self.results_frame.tab("Detailed Log")

        # Filter / search row
        _filter_row = ctk.CTkFrame(_detail_tab, fg_color="transparent")
        _filter_row.pack(fill="x", padx=5, pady=(4, 2))
        ctk.CTkLabel(_filter_row, text="Filter:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").pack(side="left", padx=(0, 6))
        self._detail_filter_var = tk.StringVar(value="ALL")
        for label, val, color in [
            ("All", "ALL", "#475569"),
            ("WINS only", "WIN", "#16a34a"),
            ("LOSSES only", "LOSS", "#dc2626"),
            ("Rotations", "ROT", "#7c3aed"),
            # New filters for conditional-trigger rows. `SKIP` = trigger
            # fallback sat the round out; `TRIG` = trigger fired with a
            # reason (covers both swaps and "stay" picks).
            ("Skips", "SKIP", "#a78bfa"),
            ("Triggered", "TRIG", "#0ea5e9"),
            ("Parallel", "PAR", "#5eead4"),
        ]:
            ctk.CTkRadioButton(
                _filter_row, text=label, variable=self._detail_filter_var, value=val,
                command=self._apply_detail_filter, fg_color=color,
                font=("Segoe UI", 12),
            ).pack(side="left", padx=4)
        # Right side: tiny streaming-log toggle
        self._show_stream_log_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            _filter_row, text="Show streaming log",
            variable=self._show_stream_log_var,
            command=self._toggle_stream_log_pane,
            font=("Segoe UI", 12),
        ).pack(side="right", padx=4)
        # Search box (substring match on Strategy column)
        ctk.CTkLabel(_filter_row, text="  Search:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").pack(side="right", padx=(8, 4))
        self._detail_search_var = tk.StringVar(value="")
        _search_entry = ctk.CTkEntry(_filter_row, textvariable=self._detail_search_var, width=160)
        _search_entry.pack(side="right")
        self._detail_search_var.trace_add("write", lambda *_: self._apply_detail_filter())

        # Jump-to-event quick-nav row. Each button computes the matching
        # round from the loaded bet_history and routes through the same
        # _jump_to_detail_row helper the graph uses — auto-clears filters so
        # the surrounding spins are visible for context.
        _jump_row = ctk.CTkFrame(_detail_tab, fg_color="transparent")
        _jump_row.pack(fill="x", padx=5, pady=(0, 2))
        ctk.CTkLabel(_jump_row, text="Jump to:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").pack(side="left", padx=(0, 6))
        for _label, _color, _cb in [
            ("💥 Worst Round",       "#dc2626", lambda: self._jump_to_event("worst_round")),
            ("🏆 Best Round",        "#16a34a", lambda: self._jump_to_event("best_round")),
            ("📉 Longest Loss Run",  "#f97316", lambda: self._jump_to_event("longest_loss_streak")),
            ("☠️ First Bankruptcy",  "#ef4444", lambda: self._jump_to_event("first_bankruptcy")),
            ("📊 Worst Session",     "#a855f7", lambda: self._jump_to_event("worst_session")),
            ("⚡ Max Escalation",    "#fbbf24", lambda: self._jump_to_event("max_escalation")),
        ]:
            ctk.CTkButton(
                _jump_row, text=_label, command=_cb, height=26, width=140,
                fg_color=_color, hover_color="#1e293b",
                font=("Segoe UI", 11, "bold"),
            ).pack(side="left", padx=3)

        # Streaming log (initially hidden; kept for run-time progress lines)
        self.detailed_text = ctk.CTkTextbox(_detail_tab, height=80)
        # Don't pack yet — toggled by checkbox above

        # Treeview for the per-round structured table (the main view)
        _tree_container = tk.Frame(_detail_tab, bg="#0b1220")
        _tree_container.pack(fill="both", expand=True, padx=5, pady=(2, 0))

        # ttk styling so the Treeview blends with the CTk dark theme
        _style = ttk.Style()
        try:
            _style.theme_use("default")
        except Exception:
            pass
        _style.configure("Spinedge.Treeview",
                         background="#0f172a", foreground="#e2e8f0",
                         fieldbackground="#0f172a", rowheight=22,
                         font=("Consolas", 12), borderwidth=0)
        _style.configure("Spinedge.Treeview.Heading",
                         background="#1e293b", foreground="#facc15",
                         font=("Segoe UI", 10, "bold"))
        _style.map("Spinedge.Treeview",
                   background=[("selected", "#0e7490")],
                   foreground=[("selected", "white")])

        # `trigger` column surfaces the TriggerEngine's per-round decision
        # ("stratA via coldest (score=4.0, 2 armed)" / "no candidate armed →
        # skip round") when conditional selection is active in the bundle.
        # Empty for plain rotation runs.
        cols = ("sess", "round", "strategy", "spin", "bet", "chip", "lbls",
                "result", "payout", "pnl", "balance", "trigger")
        col_widths = (50, 60, 220, 50, 75, 65, 50, 65, 75, 80, 90, 280)
        col_align  = ("center", "center", "w", "center", "e", "e", "center",
                      "center", "e", "e", "e", "w")
        col_titles = ("Sess#", "Round", "Strategy", "Spin", "Bet ($)", "Chip ($)",
                      "Lbls", "Result", "Payout ($)", "PnL ($)", "Balance ($)",
                      "🎯 Trigger")
        self.detail_tree = ttk.Treeview(
            _tree_container, columns=cols, show="headings",
            style="Spinedge.Treeview", height=14, selectmode="browse",
        )
        for c, w, a, t in zip(cols, col_widths, col_align, col_titles):
            self.detail_tree.heading(c, text=t,
                                     command=lambda col=c: self._sort_detail_tree(col, False))
            self.detail_tree.column(c, width=w, anchor=a, stretch=False)
        # Strategy + trigger columns allowed to stretch on resize
        self.detail_tree.column("strategy", stretch=True)
        self.detail_tree.column("trigger", stretch=True)

        # Row color tags by result.  `skip` = trigger fallback sat the round
        # out (no bet); `trigger_swap` = trigger picked a different strategy
        # than was previously active (mid-session change driven by triggers).
        self.detail_tree.tag_configure("win", background="#0f1d17")
        self.detail_tree.tag_configure("loss", background="#1d0f0f")
        self.detail_tree.tag_configure("rotation", background="#1a1230")
        self.detail_tree.tag_configure("breakeven", background="#0f172a")
        self.detail_tree.tag_configure("skip", background="#1e1b3a", foreground="#a78bfa")
        self.detail_tree.tag_configure("trigger_swap", background="#1a2540", foreground="#7dd3fc")
        # Parallel rounds — multiple strategies bet together. Distinct teal so
        # they're scannable. Win/loss is determined by the bundle-level net,
        # but the visual cue tells the user this row aggregates several strats.
        self.detail_tree.tag_configure("parallel", background="#0f2e2a", foreground="#5eead4")

        _vbar = ttk.Scrollbar(_tree_container, orient="vertical", command=self.detail_tree.yview)
        self.detail_tree.configure(yscrollcommand=_vbar.set)
        self.detail_tree.grid(row=0, column=0, sticky="nsew")
        _vbar.grid(row=0, column=1, sticky="ns")
        _tree_container.grid_rowconfigure(0, weight=1)
        _tree_container.grid_columnconfigure(0, weight=1)

        # Click row → open Round Audit modal for that exact row (no typing)
        self.detail_tree.bind("<Double-1>", self._on_detail_row_double_click)
        self.detail_tree.bind("<Return>",   self._on_detail_row_double_click)
        # Right-click → context menu (copy row / open audit)
        self.detail_tree.bind("<Button-3>", self._on_detail_row_right_click)

        # Footer hint + status (row count after filter etc.)
        _footer = ctk.CTkFrame(_detail_tab, fg_color="transparent")
        _footer.pack(fill="x", padx=5, pady=(2, 6))
        self._detail_footer = ctk.CTkLabel(
            _footer, text="(double-click a row to view the board · sortable column headers · right-click for more)",
            font=("Segoe UI", 11), text_color="#64748b",
        )
        self._detail_footer.pack(side="left")
        # Direct round# / session# audit still available as a fallback for huge tables
        self._audit_round_var = tk.StringVar(value="1")
        self._audit_session_var = tk.StringVar(value="1")
        ctk.CTkLabel(_footer, text="  Direct:",
                     font=("Segoe UI", 11), text_color="#64748b").pack(side="right", padx=(4, 0))
        ctk.CTkButton(_footer, text="Board", width=70,
                      command=self._open_round_audit_modal,
                      fg_color="#334155", hover_color="#475569",
                      font=("Segoe UI", 11)).pack(side="right", padx=(4, 0))
        ctk.CTkEntry(_footer, textvariable=self._audit_session_var, width=40,
                     font=("Segoe UI", 11)).pack(side="right", padx=(2, 0))
        ctk.CTkLabel(_footer, text=" Sess#",
                     font=("Segoe UI", 11), text_color="#64748b").pack(side="right")
        ctk.CTkEntry(_footer, textvariable=self._audit_round_var, width=50,
                     font=("Segoe UI", 11)).pack(side="right", padx=(2, 0))
        ctk.CTkLabel(_footer, text="Round#",
                     font=("Segoe UI", 11), text_color="#64748b").pack(side="right")

        # Graph Tab
        self.graph_frame = ctk.CTkFrame(self.results_frame.tab("Graph"), fg_color="transparent")
        self.graph_frame.pack(fill="both", expand=True)

        # ── Live Status Bar (bottom of UI) ────────────────────────────────
        # Updates during a run with structured info: session#, round#, PnL,
        # elapsed/ETA. Replaces the silent progress bar as the primary
        # feedback channel. Idle when no run is active.
        self._status_frame = ctk.CTkFrame(self.main_scroll, fg_color="#0b1220",
                                          corner_radius=6, border_width=1,
                                          border_color="#1e293b")
        self._status_frame.grid(row=5, column=0, sticky="ew", padx=10, pady=(4, 0))
        self._status_label = ctk.CTkLabel(
            self._status_frame, text="● Ready",
            font=("Consolas", 11), text_color="#64748b",
            anchor="w",
        )
        self._status_label.pack(side="left", fill="x", expand=True, padx=10, pady=6)
        # Right-side mini-metrics that update from log parsing during runs
        self._status_pnl_label = ctk.CTkLabel(
            self._status_frame, text="", font=("Consolas", 11, "bold"),
            text_color="#facc15", width=180, anchor="e",
        )
        self._status_pnl_label.pack(side="right", padx=10, pady=6)
        # Run-time tracking state used by _update_status_*
        self._status_run_start_time = None
        self._status_current_session = 0
        self._status_total_sessions = 0
        self._status_running_pnl = 0.0

        # Refresh recent runs dropdown now that the widget exists
        try:
            self._refresh_recent_runs_dropdown()
        except Exception as _e:
            pass

        # Initialize State
        self.dynamic_rules = [
            {'on': 'win', 'action': 'reset_to_base'},
            {'on': 'loss', 'action': 'martingale'}
        ]
        self._toggle_mode()
        self._toggle_data_source()
        self._toggle_progression(self.progression_var.get())
        self._update_sim_labels()

    def _toggle_progression(self, choice):
        if choice == "dynamic":
            self.dynamic_rules_btn.configure(state="normal", fg_color="#3B8ED0") # Standard Blue
        else:
            self.dynamic_rules_btn.configure(state="disabled", fg_color="gray")

    def _build_runner_config(self) -> dict:
        """Snapshot every GUI input into the canonical config dict consumed
        by core.backtesting_runner.run_campaign(). The CLI script accepts
        the exact same dict shape — that's what makes "GUI matches CLI"
        achievable. Single source of truth.
        """
        def _f(s, default=0.0):
            try:
                v = s.get() if hasattr(s, "get") else s
                if v in ("", None): return float(default)
                return float(v)
            except (TypeError, ValueError):
                return float(default)
        def _i(s, default=0):
            try:
                v = s.get() if hasattr(s, "get") else s
                if v in ("", None): return int(default)
                return int(v)
            except (TypeError, ValueError):
                return int(default)

        enable_stops = bool(self.enable_session_stops_var.get())
        enable_trail = bool(self.enable_trailing_stop_var.get())
        enable_globl = bool(self.enable_global_limits_var.get())

        # Rotation preset → embedded {"strategies": [...], "mode": "..."}
        rotation_cfg = None
        rotation_used = False
        strategy_name = self.strategy_var.get()
        if self.mode_var.get() == "rotation":
            preset_name = self.rotation_var.get()
            if preset_name and preset_name != "No Lists Found":
                try:
                    import os, json
                    base_dir = os.path.dirname(os.path.dirname(__file__))
                    preset_path = os.path.join(base_dir, "config", "rotation_presets",
                                               f"{preset_name}.json")
                    with open(preset_path, 'r') as f:
                        data = json.load(f)
                    if "strategies_string" in data:
                        rotation_cfg = {
                            "strategies": [s.strip() for s in data["strategies_string"].split(",")],
                            "mode": self.rotation_mode_var.get(),
                        }
                        strategy_name = f"Rotation: {preset_name}"
                        rotation_used = True
                except Exception:
                    pass

        # Escalation settings from the live app config (same place Bot Control reads)
        app_cfg = self.app.config if (self.app and hasattr(self.app, 'config')) else {}
        custom_strats = app_cfg.get("custom_strategies", {})

        # "Rounds per session" is authoritative. Session Duration + Spin Time
        # fields are informational — they previously OVERRODE rounds with
        # (duration_min * 60 / spin_seconds), which with the default 60min /
        # 20s yielded 180 and clamped every backtest to 180 rounds regardless
        # of what the user typed. Now we only fall back to duration when the
        # user explicitly leaves rounds blank or zero.
        rounds = _i(self.rounds_str, 0)
        if rounds <= 0:
            try:
                sd = _f(self.sess_duration_str, 0); st = _f(self.spin_time_str, 0)
                if sd > 0 and st > 0:
                    rounds = int((sd * 60) / st)
            except Exception:
                pass
            if rounds <= 0:
                rounds = 100  # last-resort default

        cfg = {
            "strategy_name":            strategy_name,
            "base_bet":                 _f(self.base_bet_str, 1.0),
            "progression_type":         self.progression_var.get(),
            "dynamic_rules":            list(self.dynamic_rules or []),
            "max_consec_losses":        _i(self.max_consec_str, 0),
            "custom_sequence":          None,
            "dalembert_step":           1,

            "initial_balance":          _f(self.init_bal_str, 100.0),
            "max_loss":                 _f(self.max_loss_str, 50.0),
            "profit_target":            _f(self.profit_target_str, 0.0) if enable_stops else 0.0,
            "enable_profit_target":     enable_stops and _f(self.profit_target_str, 0.0) > 0,
            "trailing_stop_amount":     _f(self.trailing_stop_str, 0.0) if enable_trail else 0.0,
            "enable_trailing_stop":     enable_trail,
            "max_session_wins_streak":  _i(self.max_win_streak_str, 0) if enable_stops else 0,
            "max_session_losses_streak":_i(self.max_consec_str, 0),
            "session_ext_after_win":    bool(self.ext_win_var.get()),
            "session_ext_at_high":      bool(self.ext_high_var.get()),
            "max_extension_rounds":     _i(self.max_ext_rounds_str, 20),
            "extension_give_up_amount": _f(self.ext_give_up_str, 50.0),

            "sim_mode":                 self.sim_mode_var.get(),
            "rounds":                   rounds,
            "sims":                     _i(self.sims_str, 10),
            "seed":                     None,

            "historical_data_source":   self.data_source_var.get(),
            "db_limit":                 _i(self.db_limit_str, 1000),
            "db_offset":                _i(self.db_offset_str, 0),
            "db_anchor_id":             self._current_anchor_id(),
            "historical_data":          None,

            "enable_global_limits":     enable_globl,
            "global_profit_target":     _f(self.global_profit_str, 0.0) if enable_globl else 0.0,
            "global_stop_loss":         _f(self.global_loss_str, 0.0) if enable_globl else 0.0,

            "rotation_config":          rotation_cfg,
            "custom_strategies":        custom_strats,

            "enable_escalation_on_loss":bool(app_cfg.get("enable_escalation_on_loss", False)),
            "escalation_multiplier":    float(app_cfg.get("escalation_multiplier", 2.0) or 2.0),
            "escalation_max_steps":     int(app_cfg.get("escalation_max_steps", 4) or 4),
            "escalation_per_step":      str(app_cfg.get("escalation_per_step", "") or ""),
        }
        # Tag whether rotation was actually wired (used for status messages)
        cfg["_rotation_used"] = rotation_used
        return cfg

    def _wire_searchable_combo(self, combobox, master_list_attr: str,
                               prefer_prefix: bool = True) -> None:
        """Make a CTkComboBox filter its visible values by what the user
        types. Master list (the full set of options) lives on a named
        attribute of `self` so callers can refresh it elsewhere without
        re-wiring the widget.
        """
        try:
            combobox.configure(state="normal")
        except Exception:
            return
        entry = getattr(combobox, "_entry", None)
        if entry is None:
            return

        if getattr(combobox, "_search_wired", False):
            return

        def _on_key(event=None):
            if event is not None and getattr(event, "keysym", "") in (
                    "Up", "Down", "Return", "Escape", "Tab", "Left", "Right"):
                return
            query = (combobox.get() or "").strip().lower()
            master = list(getattr(self, master_list_attr, []) or [])
            if not query:
                filtered = master
            else:
                # Three tiers, best first: prefix → substring → fuzzy
                # subsequence (initials, e.g. '6sb' → 6streetstratbundle).
                prefix, contains, fuzzy = [], [], []
                for s in master:
                    sl = str(s).lower()
                    if sl.startswith(query):
                        prefix.append(s)
                    elif query in sl:
                        contains.append(s)
                    else:
                        it = iter(sl)
                        if all(ch in it for ch in query):
                            fuzzy.append(s)
                filtered = prefix + contains + fuzzy
            try:
                combobox.configure(values=filtered or master)
            except Exception:
                pass

        entry.bind("<KeyRelease>", _on_key)
        combobox._search_wired = True

    def _open_dynamic_rules_dialog(self):
        # Check if already open
        if hasattr(self, 'rules_dialog') and self.rules_dialog is not None:
             try:
                 if self.rules_dialog.winfo_exists():
                     self.rules_dialog.lift()
                     self.rules_dialog.focus_force()
                     return
             except Exception:
                 self.rules_dialog = None

        # Use the specific Editor Class
        self.rules_dialog = DynamicRulesEditor(self.parent_frame, self.dynamic_rules, self.save_dynamic_rules)

    def save_dynamic_rules(self, rules):
        self.dynamic_rules = rules
        # Log update
        try:
             count = len(rules)
             self._log(f"Dynamic Rules Updated: {count} rules loaded.")
        except Exception: pass

    def _update_sim_labels(self):
        mode = self.sim_mode_var.get()
        if mode == "sequential":
            self.sims_label.configure(text="Total Sessions:")
        else:
            self.sims_label.configure(text="Num Simulations:")

    def _toggle_data_source(self):
        source = self.data_source_var.get()
        if source == "simulation":
            self.db_limit_entry.configure(state="disabled")
            self.db_limit_label.configure(state="disabled")
            self.sims_str.set("10")
        else:
            self.db_limit_entry.configure(state="normal")
            self.db_limit_label.configure(state="normal")
            self.sims_str.set("1")

    # ── DB Slice Preview ─────────────────────────────────────────────────
    def _db_total_count(self) -> int:
        """Total rows in winning_numbers — needed to tell the user how much
        of the DB they're actually using."""
        try:
            from core.utils.db_utils import get_db_connection, init_db
            init_db()
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM winning_numbers")
            n = int(c.fetchone()[0])
            conn.close()
            return n
        except Exception:
            return 0

    def _resolve_db_slice(self, limit: int, offset: int,
                          anchor_id: int = None) -> list:
        """Return the exact list of DB rows a backtest with these
        (limit, offset, anchor_id) values would replay, in chronological
        order (oldest→newest) — the same order the runner uses.

        anchor_id: when set, fetch is bounded to `id <= anchor_id` so the
        slice is reproducible even after new spins land in the table.
        """
        from core.utils.db_utils import get_recent_winning_numbers
        if offset > 0:
            raw = get_recent_winning_numbers(limit=limit + offset,
                                             max_id=anchor_id)
            # DB returns newest-first; drop the most-recent `offset` THEN
            # keep at most `limit` rows from the remaining tail.
            trimmed = raw[offset:]
            sliced = trimmed[:limit]
        else:
            sliced = get_recent_winning_numbers(limit=limit, max_id=anchor_id)
        # Reverse to chronological (oldest→newest) — matches the runner.
        sliced = list(reversed(sliced))
        return sliced

    def _format_slice_summary(self, sliced: list, total: int,
                              limit: int, offset: int,
                              anchor_id: int = None) -> tuple[str, str]:
        """Build the status line shown next to the DB controls. Returns
        (text, color). Color is amber if data is short of what the run will
        need, green otherwise."""
        if not sliced:
            return ("⚠ DB returned 0 rows. Either the DB is empty or your "
                    "Skip-latest is larger than the total row count.", "#f59e0b")
        first_ts = sliced[0].get("timestamp") or "?"
        last_ts = sliced[-1].get("timestamp") or "?"
        first_nums = [str(r.get("number")) for r in sliced[:5]]
        last_nums = [str(r.get("number")) for r in sliced[-5:]]
        offset_note = f" · skipping latest {offset:,}" if offset > 0 else ""
        anchor_note = f" · 🔒 anchored at id ≤ {anchor_id:,}" if anchor_id else ""
        # Color = amber when slice is shorter than what was asked for
        color = "#22c55e" if len(sliced) >= limit else "#f59e0b"
        short_note = (f"  ⚠ only {len(sliced):,} available (asked for {limit:,})"
                      if len(sliced) < limit else "")
        text = (
            f"📊 Replaying {len(sliced):,} of {total:,} DB rows — "
            f"oldest→newest{offset_note}{anchor_note}{short_note}\n"
            f"   from {first_ts}  →  to {last_ts}\n"
            f"   first 5: [{', '.join(first_nums)}]   ·   "
            f"last 5: [{', '.join(last_nums)}]"
        )
        return text, color

    def _current_anchor_id(self) -> int:
        """Resolve the active slice anchor for the main backtest page.

        Returns the pinned id when 'Lock slice' is enabled, otherwise None.
        Captures a fresh anchor (current MAX(id)) on first Preview after
        Lock is toggled on, so the user doesn't have to type an id."""
        if not getattr(self, "_db_lock_var", None) or not self._db_lock_var.get():
            return None
        if not getattr(self, "_db_locked_anchor_id", None):
            from core.utils.db_utils import get_max_winning_number_id
            try:
                self._db_locked_anchor_id = get_max_winning_number_id()
            except Exception:
                self._db_locked_anchor_id = None
        return self._db_locked_anchor_id

    def _on_db_lock_toggle(self):
        """Lock-slice checkbox handler: snap a fresh anchor on enable,
        clear on disable, then refresh the status line."""
        if self._db_lock_var.get():
            from core.utils.db_utils import get_max_winning_number_id
            try:
                self._db_locked_anchor_id = get_max_winning_number_id()
            except Exception:
                self._db_locked_anchor_id = None
        else:
            self._db_locked_anchor_id = None
        self._preview_db_slice()

    def _preview_db_slice(self):
        """Fetch the exact slice the next run would use and show it in the
        status label so the user can verify which spins are being replayed."""
        if self.data_source_var.get() != "db":
            try:
                self._db_slice_status.configure(
                    text="ℹ Data source is 'Random Simulation' — DB preview "
                         "only applies when 'Historical DB' is selected.",
                    text_color="#94a3b8")
            except Exception:
                pass
            return
        try:
            limit = max(1, int(self.db_limit_str.get() or "1000"))
        except (ValueError, TypeError):
            limit = 1000
        try:
            offset = max(0, int(self.db_offset_str.get() or "0"))
        except (ValueError, TypeError):
            offset = 0
        try:
            anchor_id = self._current_anchor_id()
            total = self._db_total_count()
            sliced = self._resolve_db_slice(limit, offset, anchor_id=anchor_id)
            text, color = self._format_slice_summary(
                sliced, total, limit, offset, anchor_id=anchor_id)
            self._db_slice_status.configure(text=text, text_color=color)
        except Exception as e:
            self._db_slice_status.configure(
                text=f"⚠ DB preview failed: {e}", text_color="#ef4444")

    def _refresh_data_slice_status_post_run(self):
        """Called after a run completes — replaces the status line with the
        slice that was actually used. Same logic as _preview_db_slice but
        labeled to show this is post-run (✅) not pre-run prediction."""
        if self.data_source_var.get() != "db":
            try:
                self._db_slice_status.configure(
                    text="ℹ Last run used a Random Simulation (no DB data).",
                    text_color="#94a3b8")
            except Exception:
                pass
            return
        try:
            limit = max(1, int(self.db_limit_str.get() or "1000"))
            offset = max(0, int(self.db_offset_str.get() or "0"))
            anchor_id = self._current_anchor_id()
            total = self._db_total_count()
            sliced = self._resolve_db_slice(limit, offset, anchor_id=anchor_id)
            text, color = self._format_slice_summary(
                sliced, total, limit, offset, anchor_id=anchor_id)
            # "✅ Last run" prefix replaces "📊 Replaying" so the user can tell
            # this reflects what already happened, not a forecast.
            text = "✅ Last run " + text[2:]
            self._db_slice_status.configure(text=text, text_color=color)
        except Exception:
            pass

    def _toggle_mode(self):
        mode = self.mode_var.get()
        if mode == "single":
            self.strategy_combo.configure(state="normal")
            self.rotation_combo.configure(state="disabled")
            self.rotation_mode_combo.configure(state="disabled")
        else:
            self.strategy_combo.configure(state="disabled")
            self.rotation_combo.configure(state="normal")
            self.rotation_mode_combo.configure(state="normal")

    def run_backtest(self):
        if self.run_button.cget("text") == "🚀 Run Backtest":
            self.run_button.configure(text="⏸️ Stop", state="disabled") # Stop not impl yet
            self.progress_bar.set(0)
            self.summary_text.delete("1.0", "end")
            self.detailed_text.delete("1.0", "end")

            # Close the prior equity figure (frees matplotlib objects), then
            # remove any leftover non-canvas children (e.g. error labels).
            self._teardown_figs("graph")
            for widget in self.graph_frame.winfo_children():
                widget.destroy()

            thread = threading.Thread(target=self._run_backtest_thread)
            thread.daemon = True
            thread.start()
        else:
            self.run_button.configure(text="🚀 Run Backtest", state="normal")

    def _run_backtest_thread(self):
        try:
            # Parse inputs
            # Helper for safe parsing
            def float_or_zero(str_var):
                try:
                    val = str_var.get()
                    if not val: return 0.0
                    return float(val)
                except ValueError:
                    return 0.0

            def int_or_zero(str_var):
                try:
                    val = str_var.get()
                    if not val: return 0
                    return int(val)
                except ValueError:
                    return 0

            # Parse inputs
            try:
                base_bet = float_or_zero(self.base_bet_str)
                init_bal = float_or_zero(self.init_bal_str)
                max_loss = float_or_zero(self.max_loss_str)
                rounds = int_or_zero(self.rounds_str)
                sims = int_or_zero(self.sims_str)
                db_limit = int_or_zero(self.db_limit_str)
                
                # Session Control (Respect Toggles)
                enable_stops = self.enable_session_stops_var.get()
                profit_target = float_or_zero(self.profit_target_str) if enable_stops else 0.0
                max_win_streak = int_or_zero(self.max_win_streak_str) if enable_stops else 0
                max_consec = int_or_zero(self.max_consec_str) # This is usually on main config, let's keep it
                
                # Trailing Stop
                enable_trailing = self.enable_trailing_stop_var.get()
                trailing_stop = float_or_zero(self.trailing_stop_str) if enable_trailing else 0.0
                
                # Extensions
                max_ext_rounds = int_or_zero(self.max_ext_rounds_str)
                ext_give_up = float_or_zero(self.ext_give_up_str)

                # Timing
                spin_time = float_or_zero(self.spin_time_str)
                sess_duration_min = float_or_zero(self.sess_duration_str)
                break_duration_min = float_or_zero(self.break_duration_str)
                
                # Global / Campaign
                sim_mode = self.sim_mode_var.get()
                enable_global = self.enable_global_limits_var.get()
                global_profit_target = float_or_zero(self.global_profit_str) if enable_global else 0.0
                global_stop_loss = float_or_zero(self.global_loss_str) if enable_global else 0.0
                
                # Calculate Rounds based on timing if Duration > 0
                if sess_duration_min > 0 and spin_time > 0:
                    calculated_rounds = int((sess_duration_min * 60) / spin_time)
                    if rounds != calculated_rounds:
                         rounds = calculated_rounds
                         self.parent_frame.after(0, lambda r=rounds: self._log(f"🕒 Calculated {r} rounds based on {sess_duration_min}m duration."))
                
            except Exception as e:
                self.parent_frame.after(0, lambda: messagebox.showerror("Configuration Error", f"Invalid input: {str(e)}"))
                self.parent_frame.after(0, lambda: self.run_button.configure(text="🚀 Run Backtest", state="normal"))
                return

            # Build canonical config dict (same shape used by the CLI script).
            # All input parsing lives in _build_runner_config now, so the GUI
            # and CLI cannot drift.
            runner_cfg = self._build_runner_config()
            strategy_name = runner_cfg["strategy_name"]
            self.results = {strategy_name: []}
            self._last_runner_config = dict(runner_cfg)  # snapshot for Export Config
            # Auto-persist so a server restart restores this exact setup.
            self._save_last_config()

            # ── Delegate the campaign to the shared runner ────────────────
            # Single source of truth: same code path as backtest_cli.py.
            # Marshal logs / progress back onto the Tk thread.
            from core.backtesting_runner import run_campaign

            def _ui_log(msg):
                self.parent_frame.after(0, lambda m=msg: self._log(m))
            def _ui_progress(p):
                self.parent_frame.after(0, lambda v=p: self.progress_bar.set(v))

            # Start status bar tracking
            self.parent_frame.after(
                0,
                lambda total=int(runner_cfg.get("sims", 1)),
                       label=strategy_name: self._begin_status_run(total, label),
            )

            campaign = run_campaign(runner_cfg, on_log=_ui_log, on_progress=_ui_progress)
            self._last_campaign = campaign

            # Hand the per-session BacktestResult objects to the existing
            # analysis/plot pipeline (it expects a dict {strategy: [results]}).
            self.results[strategy_name] = list(campaign.sessions)

            # Analyze + display
            self.analysis = self.backtester.analyze_results(self.results)
            self.parent_frame.after(0, lambda: self._display_summary(strategy_name))
            self.parent_frame.after(0, self._plot_results)
            # Refresh the data-slice status so the user sees the exact window
            # that was just replayed (or "simulation" mode confirmation).
            self.parent_frame.after(0, self._refresh_data_slice_status_post_run)
            self.parent_frame.after(0, lambda: self.save_button.configure(state="normal"))
            self.parent_frame.after(0, lambda: self.export_button.configure(state="normal"))
            self.parent_frame.after(0, lambda: self.export_csv_button.configure(state="normal"))
            # Auto-save the completed run for the Recent Runs dropdown
            self.parent_frame.after(
                0,
                lambda n=strategy_name, c=dict(runner_cfg): self._save_run_to_disk(n, c, label=n),
            )
            self.parent_frame.after(
                0,
                lambda p=campaign.campaign_pnl: self._end_status_run(p, ok=True),
            )
            _sessions_run = campaign.sessions_run
            self.parent_frame.after(0, lambda: messagebox.showinfo(
                "Backtesting Complete",
                f"Backtesting completed successfully!\n"
                f"Ran {_sessions_run} sessions for {strategy_name}.\n"
                f"Campaign PnL: ${campaign.campaign_pnl:+.2f}",
            ))

        except Exception as e:
            err_msg = str(e)
            self.parent_frame.after(0, lambda msg=f"Critical Error: {err_msg}": self._log(msg))
            self.parent_frame.after(0, lambda msg=err_msg: messagebox.showerror("Error", f"Backtesting failed: {msg}"))
            self.parent_frame.after(0, lambda: self._end_status_run(0.0, ok=False))
        finally:
            self.parent_frame.after(0, lambda: self.run_button.configure(text="🚀 Run Backtest", state="normal"))

    def _log(self, message):
        self.detailed_text.insert("end", f"{message}\n")
        self.detailed_text.see("end")
        # Parse structured info out of runner log lines and reflect it on the
        # status bar (mini-metrics). Cheap regex match — only fires on lines
        # the runner emits so noise from other sources is ignored.
        try:
            self._update_status_from_log(str(message))
        except Exception:
            pass

    # ── Detailed Log Treeview helpers ─────────────────────────────────────
    def _toggle_stream_log_pane(self):
        """Show/hide the streaming runner-log textbox above the Treeview."""
        try:
            if self._show_stream_log_var.get():
                self.detailed_text.pack(fill="x", padx=5, pady=(0, 4),
                                        before=None)
                # Re-pack so it sits between the filter row and the Treeview
                self.detailed_text.pack_forget()
                # Find the tree container's parent (the detail tab) — pack
                # the textbox at the top by inserting before the tree.
                parent = self.detailed_text.master
                self.detailed_text.pack(in_=parent, fill="x", padx=5, pady=(2, 4))
                # Move it visually to the top by re-packing siblings — Tk
                # doesn't have a clean "insert at index" for pack, but since
                # we add it last on toggle-on it lands at the bottom of the
                # tab content. Force it up by re-packing the existing widgets.
            else:
                self.detailed_text.pack_forget()
        except Exception:
            pass

    def _populate_detail_tree(self):
        """Rebuild the Treeview from the currently-loaded results."""
        try:
            self.detail_tree.delete(*self.detail_tree.get_children())
        except Exception:
            return
        strat = getattr(self, "_displayed_strategy_name", None)
        if not strat or strat not in self.results:
            return
        sessions = self.results.get(strat) or []
        prev_strategy_per_session = {}
        total_rows_inserted = 0
        for sess_idx, sim in enumerate(sessions, start=1):
            history = getattr(sim, "bet_history", None) or []
            prev_strat_name = None
            for rec in history:
                r = rec.get("round", 0)
                strategy = str(rec.get("strategy", strat))
                spin = str(rec.get("spin_result", "-"))
                chip = float(rec.get("bet_amount", 0.0) or 0.0)
                bet = float(rec.get("total_bet", chip) or chip)
                labels = len(rec.get("bets", []) or []) or 1
                result = str(rec.get("result", "-"))
                payout = float(rec.get("payout", 0.0) or 0.0)
                pnl = float(rec.get("pnl", 0.0) or 0.0)
                balance = float(rec.get("balance_after", 0.0) or 0.0)
                # trigger_reason is set by backtest_strategy whenever the
                # TriggerEngine made a per-round decision (skip / swap / stay).
                trig_reason = str(rec.get("trigger_reason") or "")
                # Detect a rotation event (strategy name changed mid-session)
                is_rotation = (prev_strat_name is not None and strategy != prev_strat_name)
                prev_strat_name = strategy
                # Tag priority: parallel > skip > trigger_swap > rotation > result.
                # Parallel rounds (multiple strategies bet together) get their own
                # distinct tag so users can scan for them in the log.
                is_parallel = bool(rec.get("parallel_strategies"))
                if is_parallel:
                    tag = "parallel"
                elif result == "SKIP":
                    tag = "skip"
                elif is_rotation and trig_reason:
                    tag = "trigger_swap"
                elif is_rotation:
                    tag = "rotation"
                elif result == "WIN":
                    tag = "win"
                elif result == "LOSS":
                    tag = "loss"
                else:
                    tag = "breakeven"
                # iid = "sess:round" so the row click can resolve back to a record
                iid = f"{sess_idx}:{r}"
                self.detail_tree.insert(
                    "", "end", iid=iid,
                    values=(
                        sess_idx, r, strategy[:32], spin,
                        f"{bet:.2f}", f"{chip:.2f}", labels,
                        result,
                        f"{payout:.2f}", f"{pnl:+.2f}", f"{balance:.2f}",
                        trig_reason[:64],
                    ),
                    tags=(tag,),
                )
                total_rows_inserted += 1
        self._all_tree_rows_count = total_rows_inserted
        self._apply_detail_filter()  # respect current filter selection

    def _apply_detail_filter(self):
        """Filter rows by current filter chip + search box. Cheap re-iterate."""
        try:
            mode = self._detail_filter_var.get()
            query = (self._detail_search_var.get() or "").strip().lower()
        except Exception:
            mode, query = "ALL", ""
        try:
            # Two-phase: detach all, then reinsert matches. detach preserves
            # the children dict so we can re-show them later without losing
            # any data.
            all_iids = list(self.detail_tree.get_children())
            if not all_iids:
                # Maybe rows are currently detached — recover them
                # ttk doesn't expose detached items by parent, so we keep a
                # parallel cache.
                pass
            # Re-attach all then filter — simplest stable behavior
            for iid in (getattr(self, "_detached_tree_iids", []) or []):
                try:
                    self.detail_tree.reattach(iid, "", "end")
                except Exception:
                    pass
            self._detached_tree_iids = []
            kept = 0
            for iid in self.detail_tree.get_children():
                vals = self.detail_tree.item(iid, "values")
                if not vals:
                    continue
                result = vals[7] if len(vals) > 7 else ""
                strategy = vals[2] if len(vals) > 2 else ""
                trig_col = vals[11] if len(vals) > 11 else ""
                tags = self.detail_tree.item(iid, "tags") or ()
                keep = True
                if mode == "WIN" and result != "WIN":
                    keep = False
                elif mode == "LOSS" and result != "LOSS":
                    keep = False
                elif mode == "ROT" and ("rotation" not in tags and "trigger_swap" not in tags):
                    keep = False
                elif mode == "SKIP" and result != "SKIP":
                    keep = False
                elif mode == "TRIG" and not str(trig_col).strip():
                    keep = False
                elif mode == "PAR" and "parallel" not in tags:
                    keep = False
                if keep and query and query not in str(strategy).lower():
                    keep = False
                if not keep:
                    self.detail_tree.detach(iid)
                    self._detached_tree_iids.append(iid)
                else:
                    kept += 1
            total = kept + len(self._detached_tree_iids)
            try:
                self._detail_footer.configure(
                    text=f"Showing {kept} of {total} rows  ·  double-click a row to view the board"
                )
            except Exception:
                pass
        except Exception as e:
            print(f"[BacktestGUI] detail filter failed: {e}")

    def _sort_detail_tree(self, col: str, descending: bool):
        """Click a column header to sort rows by that column. Toggles dir."""
        try:
            items = [(self.detail_tree.set(k, col), k) for k in self.detail_tree.get_children("")]
            def _key(v):
                s = v[0]
                # Try numeric first (strips $, +, commas)
                try:
                    return float(s.replace("$", "").replace("+", "").replace(",", ""))
                except (ValueError, AttributeError):
                    return str(s).lower()
            items.sort(key=_key, reverse=descending)
            for idx, (_v, k) in enumerate(items):
                self.detail_tree.move(k, "", idx)
            # Flip the sort direction for next click
            self.detail_tree.heading(col, command=lambda: self._sort_detail_tree(col, not descending))
        except Exception as e:
            print(f"[BacktestGUI] sort failed: {e}")

    def _on_detail_row_double_click(self, _event=None):
        """Double-click (or Enter) on a Treeview row → open Round Audit modal."""
        sel = self.detail_tree.selection()
        if not sel:
            return
        iid = sel[0]  # format "sess:round"
        try:
            sess_str, round_str = iid.split(":", 1)
            self._audit_session_var.set(sess_str)
            self._audit_round_var.set(round_str)
            self._open_round_audit_modal()
        except Exception as e:
            messagebox.showerror("Round Audit", f"Couldn't resolve row: {e}")

    def _on_detail_row_right_click(self, event):
        """Right-click on a Treeview row → context menu."""
        try:
            iid = self.detail_tree.identify_row(event.y)
            if not iid:
                return
            self.detail_tree.selection_set(iid)
            menu = tk.Menu(self.parent_frame, tearoff=0)
            menu.add_command(label="🎯  View Round Audit  (board view)",
                             command=self._on_detail_row_double_click)
            menu.add_separator()
            menu.add_command(label="📋  Copy row as CSV",
                             command=lambda: self._copy_row_csv(iid))
            menu.tk_popup(event.x_root, event.y_root)
        except Exception as e:
            print(f"[BacktestGUI] row context-menu failed: {e}")

    def _copy_row_csv(self, iid):
        try:
            vals = self.detail_tree.item(iid, "values")
            line = ",".join(str(v) for v in vals)
            self.parent_frame.clipboard_clear()
            self.parent_frame.clipboard_append(line)
        except Exception:
            pass

    # ── Pro Metrics panel ─────────────────────────────────────────────────
    # Cards are created once (lazy); values get updated each time results
    # change via _populate_metrics_panel.
    METRIC_LAYOUT = [
        # (key, title, fmt, hint, accent_color, positive_is_good)
        ("roi_pct",       "ROI",                "{:+.2f}%",  "Final equity vs initial",          "#22d3ee", True),
        ("campaign_pnl",  "Total PnL",          "${:+,.2f}", "Net across the campaign",          "#22c55e", True),
        ("win_rate",      "Win rate",           "{:.1f}%",   "Wins ÷ total rounds",              "#a78bfa", True),
        ("profit_factor", "Profit factor",      "{:.2f}",    "Σ wins ÷ |Σ losses| · > 1 is +ev", "#fbbf24", True),
        ("expectancy",    "Expectancy / round", "${:+.4f}",  "Avg PnL per round",                "#84cc16", True),
        ("sharpe",        "Sharpe (sessions)",  "{:.2f}",    "Mean ÷ stdev across sessions",     "#0ea5e9", True),
        ("kelly",         "Kelly fraction",     "{:.2%}",    "Optimal stake fraction",            "#ec4899", True),
        ("max_dd_abs",    "Max drawdown",       "${:,.2f}",  "Largest equity dip",               "#ef4444", False),
        ("max_dd_pct",    "Max DD %",           "{:.1f}%",   "Drawdown vs initial balance",      "#ef4444", False),
        ("max_loss_streak", "Longest loss streak", "{}",     "Most consecutive losses",          "#f87171", False),
        ("max_win_streak",  "Longest win streak",  "{}",     "Most consecutive wins",            "#4ade80", True),
        ("avg_session_pnl", "Avg session PnL",  "${:+,.2f}", "Mean per-session result",          "#22c55e", True),
        ("bankruptcies",  "Bankruptcies",       "{}",        "Sessions ending at $0",            "#dc2626", False),
        ("total_rounds",  "Total rounds",       "{:,}",      "Spins executed in the campaign",   "#94a3b8", True),
        ("total_wagered", "Total wagered",      "${:,.2f}",  "Sum of every round's stake",       "#94a3b8", True),
        # ── Audit-flavored metrics (filled by _compute_metrics from stop_reason
        # and escalation_step on each session) ─────────────────────────────
        ("sessions_stopped_loss", "Stopped by max_loss", "{}",
            "# of sessions that hit the session-level stop loss", "#ef4444", False),
        ("sessions_completed_rounds", "Completed all rounds", "{}",
            "# of sessions that played their full round budget", "#a78bfa", True),
        ("max_escalation_step", "Max escalation step", "{}",
            "Highest escalation step reached during the campaign", "#fb923c", False),
    ]

    def _create_metric_cards(self):
        for idx, (key, title, _fmt, hint, accent, _pos) in enumerate(self.METRIC_LAYOUT):
            row, col = divmod(idx, 3)
            card = ctk.CTkFrame(self._metrics_grid, fg_color="#0f172a",
                                corner_radius=8, border_width=1, border_color="#1e293b")
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            # Accent strip on the left
            ctk.CTkFrame(card, fg_color=accent, width=4, corner_radius=0).pack(
                side="left", fill="y", padx=(0, 8))
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=8)
            title_lbl = ctk.CTkLabel(inner, text=title,
                                     font=("Segoe UI", 12),
                                     text_color="#94a3b8", anchor="w")
            title_lbl.pack(fill="x", anchor="w")
            value_lbl = ctk.CTkLabel(inner, text="—",
                                     font=("Roboto", 20, "bold"),
                                     text_color="#e2e8f0", anchor="w")
            value_lbl.pack(fill="x", anchor="w", pady=(2, 0))
            hint_lbl = ctk.CTkLabel(inner, text=hint,
                                    font=("Segoe UI", 11),
                                    text_color="#475569", anchor="w")
            hint_lbl.pack(fill="x", anchor="w", pady=(2, 0))
            self._metric_card_widgets[key] = (value_lbl, accent)

    def _compute_metrics(self, strategy_name: str) -> dict:
        """Compute derived metrics from self.results[strategy_name].
        Returns a dict keyed by METRIC_LAYOUT keys. Missing values omitted."""
        import math
        sessions = self.results.get(strategy_name) or []
        if not sessions:
            return {}
        camp = getattr(self, "_last_campaign", None)
        initial_balance = sessions[0].initial_balance if sessions else 0.0

        # Pull per-session pnl and per-round records
        session_pnls = [s.final_balance - s.initial_balance for s in sessions]
        all_records = []
        total_wagered = 0.0
        for s in sessions:
            history = getattr(s, "bet_history", None) or []
            for rec in history:
                total_wagered += float(rec.get("total_bet", rec.get("bet_amount", 0.0)) or 0.0)
                all_records.append(rec)

        total_rounds = sum(getattr(s, "total_rounds", 0) for s in sessions)
        total_wins   = sum(getattr(s, "total_wins", 0) for s in sessions)
        win_rate = (total_wins / total_rounds * 100.0) if total_rounds else 0.0

        # Round-level PnL stats
        winning = [float(r.get("pnl", 0.0)) for r in all_records if float(r.get("pnl", 0.0)) > 1e-9]
        losing  = [float(r.get("pnl", 0.0)) for r in all_records if float(r.get("pnl", 0.0)) < -1e-9]
        avg_win  = (sum(winning) / len(winning))  if winning else 0.0
        avg_loss = (sum(losing)  / len(losing))   if losing  else 0.0  # negative
        profit_factor = (sum(winning) / abs(sum(losing))) if losing and sum(losing) != 0 else float('inf') if winning else 0.0
        expectancy = (sum(float(r.get("pnl", 0.0)) for r in all_records) / len(all_records)) if all_records else 0.0

        # Kelly: f* = (p × b - q) / b  where b = avg_win / |avg_loss|, p = win_rate, q = 1 - p.
        kelly = 0.0
        if avg_win > 0 and avg_loss < 0:
            p = total_wins / total_rounds if total_rounds else 0.0
            b = avg_win / abs(avg_loss)
            kelly = (p * b - (1 - p)) / b if b > 0 else 0.0

        # Sharpe across sessions (sample stdev, scaled by √N for batch comparison)
        sharpe = 0.0
        if len(session_pnls) > 1:
            mean_pnl = sum(session_pnls) / len(session_pnls)
            variance = sum((x - mean_pnl) ** 2 for x in session_pnls) / (len(session_pnls) - 1)
            stdev = math.sqrt(variance)
            if stdev > 1e-9:
                sharpe = mean_pnl / stdev * math.sqrt(len(session_pnls))

        # Drawdown across the campaign (treat as concatenated equity curve)
        max_dd_abs = 0.0
        if sessions and len(sessions) >= 1:
            equity = [initial_balance]
            for s in sessions:
                # Use balance_history if present for finer-grained DD, else
                # fall back to start->end of session
                bh = getattr(s, "balance_history", None) or []
                if bh:
                    for b in bh:
                        if isinstance(b, dict) and "balance" in b:
                            equity.append(float(b["balance"]))
                        elif isinstance(b, (int, float)):
                            equity.append(float(b))
                else:
                    equity.append(s.final_balance)
            peak = equity[0]
            for v in equity:
                if v > peak:
                    peak = v
                dd = peak - v
                if dd > max_dd_abs:
                    max_dd_abs = dd
        # Drawdown is a magnitude — always render as a positive number. In
        # degenerate runs the campaign equity can sink below initial_balance
        # (initial_balance becomes the divisor and the ratio looks weird with
        # a sign); abs() keeps the displayed number intuitive.
        max_dd_pct = abs((max_dd_abs / initial_balance * 100.0)) if initial_balance else 0.0
        max_dd_abs = abs(max_dd_abs)

        # Streaks
        max_loss_streak = max((getattr(s, "max_consecutive_losses", 0) for s in sessions), default=0)
        max_win_streak  = max((getattr(s, "max_consecutive_wins", 0)  for s in sessions), default=0)
        bankruptcies = sum(1 for s in sessions if s.final_balance <= 0.01)
        avg_session_pnl = (sum(session_pnls) / len(session_pnls)) if session_pnls else 0.0

        # Campaign PnL (sequential) or sum of sessions (independent)
        if camp is not None:
            campaign_pnl = float(getattr(camp, "campaign_pnl", sum(session_pnls)))
        else:
            campaign_pnl = sum(session_pnls)

        roi_pct = (campaign_pnl / initial_balance * 100.0) if initial_balance else 0.0

        # Audit-flavored stats from per-session stop_reason + escalation_step
        sessions_stopped_loss = sum(
            1 for s in sessions
            if (getattr(s, "stop_reason", "") or "").upper() in ("STOP_LOSS", "INSUFFICIENT_BALANCE")
        )
        sessions_completed_rounds = sum(
            1 for s in sessions
            if (getattr(s, "stop_reason", "") or "").upper() == "ROUNDS_EXHAUSTED"
        )
        max_escalation_step = max(
            (int(getattr(s, "escalation_step", 0) or 0) for s in sessions),
            default=0,
        )

        return {
            "roi_pct":         roi_pct,
            "campaign_pnl":    campaign_pnl,
            "win_rate":        win_rate,
            "profit_factor":   profit_factor,
            "expectancy":      expectancy,
            "sharpe":          sharpe,
            "kelly":           kelly,
            "max_dd_abs":      max_dd_abs,
            "max_dd_pct":      max_dd_pct,
            "max_loss_streak": max_loss_streak,
            "max_win_streak":  max_win_streak,
            "avg_session_pnl": avg_session_pnl,
            "bankruptcies":    bankruptcies,
            "total_rounds":    total_rounds,
            "total_wagered":   total_wagered,
            "sessions_stopped_loss":    sessions_stopped_loss,
            "sessions_completed_rounds": sessions_completed_rounds,
            "max_escalation_step":      max_escalation_step,
        }

    # ── CSV export of the per-round detailed log ─────────────────────────
    def _export_detailed_csv(self):
        """Dump all bet_history rows from current results to CSV.
        Includes session#, round#, all numeric columns, AND the per-label
        breakdown joined as a 'bets' column (so an analyst can rebuild
        chip placement in a spreadsheet)."""
        if not self.results:
            messagebox.showinfo("Export CSV", "No results to export. Run a backtest first.")
            return
        strat = next(iter(self.results.keys()), None)
        if not strat or not self.results[strat]:
            messagebox.showinfo("Export CSV", "No sessions in current results.")
            return
        default_name = f"backtest_{strat.replace(':', '_')[:32]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Detailed Log as CSV",
        )
        if not path:
            return
        try:
            import csv as _csv
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = _csv.writer(f)
                w.writerow([
                    "session", "round", "strategy", "spin", "total_bet",
                    "chip_bet", "labels_count", "result", "payout", "pnl",
                    "balance_after", "bets_breakdown",
                    # trigger_reason is empty for plain rotation runs; carries
                    # the TriggerEngine's per-round decision for conditional
                    # selection runs (skip / swap / stay).
                    "trigger_reason",
                    # Audit columns — repeated on every row so a downstream
                    # filter ("only rows where sess_stop_reason=STOP_LOSS")
                    # works without a session-summary JOIN.
                    "sess_stop_reason", "sess_stop_message",
                    "sess_effective_base_bet", "sess_effective_max_loss",
                    "sess_escalation_step",
                ])
                for sess_idx, sim in enumerate(self.results[strat], start=1):
                    sess_stop_reason = getattr(sim, "stop_reason", "") or ""
                    sess_stop_message = getattr(sim, "stop_message", "") or ""
                    sess_base_bet = float(getattr(sim, "effective_base_bet", 0.0) or 0.0)
                    sess_max_loss = float(getattr(sim, "effective_max_loss", 0.0) or 0.0)
                    sess_esc_step = int(getattr(sim, "escalation_step", 0) or 0)
                    for rec in (getattr(sim, "bet_history", None) or []):
                        bets = rec.get("bets") or []
                        # bets_breakdown column: "label=amount(win|loss);..."
                        parts = []
                        for b in bets:
                            wf = "W" if b.get("win") else "L"
                            parts.append(f"{b.get('label','?')}={b.get('amount',0):.4f}({wf})")
                        bets_str = ";".join(parts)
                        w.writerow([
                            sess_idx,
                            rec.get("round", 0),
                            rec.get("strategy", strat),
                            rec.get("spin_result", ""),
                            f"{float(rec.get('total_bet', rec.get('bet_amount', 0.0)) or 0.0):.4f}",
                            f"{float(rec.get('bet_amount', 0.0) or 0.0):.4f}",
                            len(bets),
                            rec.get("result", ""),
                            f"{float(rec.get('payout', 0.0) or 0.0):.4f}",
                            f"{float(rec.get('pnl', 0.0) or 0.0):+.4f}",
                            f"{float(rec.get('balance_after', 0.0) or 0.0):.4f}",
                            bets_str,
                            rec.get("trigger_reason", "") or "",
                            sess_stop_reason, sess_stop_message,
                            f"{sess_base_bet:.4f}", f"{sess_max_loss:.4f}",
                            sess_esc_step,
                        ])
            messagebox.showinfo("Export CSV", f"Wrote detailed log to:\n{path}")
            self._set_status(f"📋 CSV exported: {os.path.basename(path)}", color="#22c55e")
        except Exception as e:
            messagebox.showerror("Export CSV", f"Couldn't write CSV: {e}")

    # ── Compare Two Runs ─────────────────────────────────────────────────
    # Pick a baseline + a candidate from the Recent Runs directory and show
    # side-by-side metrics with a delta column, plus overlay equity curves.
    def _open_compare_dialog(self):
        runs = self._load_recent_runs(limit=50)
        if len(runs) < 2:
            messagebox.showinfo(
                "Compare Runs",
                "Need at least 2 saved runs to compare. Run a few backtests first.",
            )
            return

        dialog = ctk.CTkToplevel(self.parent_frame)
        dialog.title("Compare Backtest Runs")
        dialog.transient(self.parent_frame)
        dialog.grab_set()
        dialog.geometry("980x760")
        dialog.configure(fg_color="#09090b")

        ctk.CTkLabel(dialog, text="🔀  Compare Two Runs",
                     font=("Roboto", 16, "bold"),
                     text_color="#a78bfa").pack(padx=14, pady=(14, 4), anchor="w")
        ctk.CTkLabel(dialog,
                     text="Pick a baseline and a candidate. Δ = candidate − baseline. Green = candidate beats baseline.",
                     font=("Segoe UI", 12), text_color="#94a3b8",
                     wraplength=940, justify="left").pack(padx=14, pady=(0, 10), anchor="w")

        labels_to_path = {}
        labels = []
        for r in runs:
            short = r["saved_at"].replace("T", " ")[:16]
            disp = f"{short}  ·  {r['label'][:32]:<32}  ·  {r['sessions']}s  ·  ${r['pnl_total']:+,.2f}"
            labels_to_path[disp] = r["path"]
            labels.append(disp)

        # Two pickers side-by-side
        pickers = ctk.CTkFrame(dialog, fg_color="#0f172a", corner_radius=8)
        pickers.pack(fill="x", padx=14, pady=6)
        pickers.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(pickers, text="Baseline", font=("Segoe UI", 11, "bold"),
                     text_color="#0ea5e9").grid(row=0, column=0, sticky="w", padx=12, pady=(8, 2))
        baseline_var = tk.StringVar(value=labels[1] if len(labels) > 1 else labels[0])
        baseline_combo = ctk.CTkComboBox(pickers, variable=baseline_var, values=labels,
                                         state="readonly", width=440)
        baseline_combo.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(pickers, text="Candidate", font=("Segoe UI", 11, "bold"),
                     text_color="#22c55e").grid(row=0, column=1, sticky="w", padx=12, pady=(8, 2))
        candidate_var = tk.StringVar(value=labels[0])
        candidate_combo = ctk.CTkComboBox(pickers, variable=candidate_var, values=labels,
                                          state="readonly", width=440)
        candidate_combo.grid(row=1, column=1, sticky="ew", padx=12, pady=(0, 10))

        # Metrics comparison table
        ctk.CTkLabel(dialog, text="Metrics:",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#cbd5e1").pack(padx=14, pady=(6, 2), anchor="w")
        table_container = tk.Frame(dialog, bg="#0b1220")
        table_container.pack(fill="both", expand=False, padx=14, pady=(0, 6))

        _style = ttk.Style()
        _style.configure("Compare.Treeview", background="#0f172a",
                         foreground="#e2e8f0", fieldbackground="#0f172a",
                         rowheight=24, font=("Consolas", 12), borderwidth=0)
        _style.configure("Compare.Treeview.Heading", background="#1e293b",
                         foreground="#facc15", font=("Segoe UI", 10, "bold"))

        cmp_tree = ttk.Treeview(table_container,
                                columns=("metric", "baseline", "candidate", "delta", "verdict"),
                                show="headings", style="Compare.Treeview", height=14,
                                selectmode="browse")
        for c, t, w, a in [
            ("metric", "Metric", 220, "w"),
            ("baseline", "Baseline", 160, "e"),
            ("candidate", "Candidate", 160, "e"),
            ("delta", "Δ", 160, "e"),
            ("verdict", "Verdict", 100, "center"),
        ]:
            cmp_tree.heading(c, text=t)
            cmp_tree.column(c, width=w, anchor=a, stretch=(c == "metric"))
        cmp_tree.tag_configure("better", background="#0f1d17", foreground="#86efac")
        cmp_tree.tag_configure("worse", background="#1d0f0f", foreground="#fca5a5")
        cmp_tree.tag_configure("neutral", background="#0f172a", foreground="#cbd5e1")
        cmp_tree.pack(side="left", fill="both", expand=True)
        cmp_vbar = ttk.Scrollbar(table_container, orient="vertical", command=cmp_tree.yview)
        cmp_tree.configure(yscrollcommand=cmp_vbar.set)
        cmp_vbar.pack(side="right", fill="y")

        # Overlay equity curve area
        ctk.CTkLabel(dialog, text="Equity curves (overlaid):",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#cbd5e1").pack(padx=14, pady=(6, 2), anchor="w")
        graph_frame = ctk.CTkFrame(dialog, fg_color="#2b2b2b", corner_radius=6, height=250)
        graph_frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        def _load_run_blob(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Compare] load {path} failed: {e}")
                return None

        def _sessions_from_blob(blob):
            from types import SimpleNamespace
            out = []
            for s in ((blob or {}).get("results") or {}).get("sessions", []):
                out.append(SimpleNamespace(
                    initial_balance=float(s.get("initial_balance", 0.0)),
                    final_balance=float(s.get("final_balance", 0.0)),
                    total_rounds=int(s.get("total_rounds", 0)),
                    total_wins=int(s.get("total_wins", 0)),
                    total_losses=int(s.get("total_losses", 0)),
                    max_drawdown=float(s.get("max_drawdown", 0.0)),
                    max_consecutive_wins=int(s.get("max_consecutive_wins", 0)),
                    max_consecutive_losses=int(s.get("max_consecutive_losses", 0)),
                    bet_history=list(s.get("bet_history") or []),
                    balance_history=list(s.get("balance_history") or []),
                    stop_reason=s.get("stop_reason", "") or "",
                    stop_message=s.get("stop_message", "") or "",
                    effective_base_bet=float(s.get("effective_base_bet", 0.0) or 0.0),
                    effective_max_loss=float(s.get("effective_max_loss", 0.0) or 0.0),
                    escalation_step=int(s.get("escalation_step", 0) or 0),
                ))
            return out

        def _compute_for_blob(blob, label="x"):
            """Compute metrics from a saved-run blob (reuses _compute_metrics)."""
            sessions = _sessions_from_blob(blob)
            from types import SimpleNamespace
            camp = None
            if blob and blob.get("campaign"):
                cd = blob["campaign"]
                camp = SimpleNamespace(
                    campaign_pnl=float(cd.get("campaign_pnl", 0.0)),
                    sessions_run=int(cd.get("sessions_run", len(sessions))),
                )
            mock = SimpleNamespace(results={label: sessions}, _last_campaign=camp)
            return type(self)._compute_metrics(mock, label), sessions

        def _format_delta(key, fmt, delta, candidate_better):
            try:
                if delta == 0:
                    return "—", "neutral"
                disp = fmt.format(delta) if not isinstance(delta, str) else delta
                tag = "better" if candidate_better else "worse"
                if "+" not in disp and "-" not in disp and isinstance(delta, (int, float)):
                    sign = "+" if delta > 0 else ""
                    disp = f"{sign}{fmt.format(delta)}"
                return disp, tag
            except Exception:
                return str(delta), "neutral"

        def _refresh_compare():
            cmp_tree.delete(*cmp_tree.get_children())
            b_blob = _load_run_blob(labels_to_path.get(baseline_var.get(), ""))
            c_blob = _load_run_blob(labels_to_path.get(candidate_var.get(), ""))
            if not b_blob or not c_blob:
                cmp_tree.insert("", "end", values=("(failed to load)", "", "", "", ""))
                return
            b_metrics, b_sessions = _compute_for_blob(b_blob, "baseline")
            c_metrics, c_sessions = _compute_for_blob(c_blob, "candidate")

            for key, title, fmt, _hint, _accent, positive_is_good in self.METRIC_LAYOUT:
                bv = b_metrics.get(key)
                cv = c_metrics.get(key)
                if bv is None and cv is None:
                    continue
                try:
                    bv_disp = (fmt.format(bv) if bv is not None and bv != float("inf") else "∞" if bv == float("inf") else "—")
                    cv_disp = (fmt.format(cv) if cv is not None and cv != float("inf") else "∞" if cv == float("inf") else "—")
                except Exception:
                    bv_disp, cv_disp = str(bv), str(cv)
                if isinstance(bv, (int, float)) and isinstance(cv, (int, float)) \
                        and bv != float("inf") and cv != float("inf"):
                    delta = cv - bv
                    candidate_better = (delta > 0) if positive_is_good else (delta < 0)
                    delta_disp, tag = _format_delta(key, fmt, delta, candidate_better)
                    verdict = "▲ better" if candidate_better else ("▼ worse" if abs(delta) > 1e-9 else "=")
                else:
                    delta_disp, tag, verdict = "—", "neutral", "—"
                cmp_tree.insert("", "end",
                                values=(title, bv_disp, cv_disp, delta_disp, verdict),
                                tags=(tag,))

            # Overlay equity curves — close the previous compare figure first.
            self._teardown_figs("compare")
            for w in graph_frame.winfo_children():
                w.destroy()
            if plt is None or FigureCanvasTkAgg is None:
                tk.Label(graph_frame, text="matplotlib not available", fg="orange",
                         bg="#2b2b2b").pack(pady=20)
                return
            fig = plt.Figure(figsize=(10, 3.2), dpi=90)
            fig.patch.set_facecolor('#2b2b2b')
            ax = fig.add_subplot(111)
            ax.set_facecolor('#2b2b2b')
            ax.tick_params(colors='white', which='both')
            ax.xaxis.label.set_color('white')
            ax.yaxis.label.set_color('white')
            for spine in ax.spines.values():
                spine.set_edgecolor('gray')

            def _curve(sessions):
                cum_r = 0
                xs, ys = [], []
                for s in sessions:
                    bh = getattr(s, "balance_history", None) or []
                    if not bh:
                        continue
                    for entry in bh:
                        local_r = entry.get('round', 0)
                        xs.append(cum_r + local_r)
                        ys.append(entry.get('balance', 0))
                    cum_r += max((h.get('round', 0) for h in bh), default=0)
                return xs, ys

            bx, by = _curve(b_sessions)
            cx, cy = _curve(c_sessions)
            if bx:
                ax.plot(bx, by, color="#0ea5e9", label="Baseline", linewidth=1.6, alpha=0.85)
            if cx:
                ax.plot(cx, cy, color="#22c55e", label="Candidate", linewidth=1.8, alpha=0.95)
            ax.set_xlabel("Round (campaign)")
            ax.set_ylabel("Balance ($)")
            ax.grid(True, linestyle="--", alpha=0.3, color="gray")
            legend = ax.legend(facecolor='#2b2b2b', edgecolor='gray')
            for text in legend.get_texts():
                text.set_color("white")
            canvas = FigureCanvasTkAgg(fig, master=graph_frame)
            canvas.draw()
            canvas.get_tk_widget().pack(fill="both", expand=True)
            self._register_fig("compare", fig, canvas)

        baseline_combo.configure(command=lambda _v: _refresh_compare())
        candidate_combo.configure(command=lambda _v: _refresh_compare())
        _refresh_compare()

        # Footer
        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(footer, text="Close", width=120, height=34,
                      fg_color="#334155", hover_color="#475569",
                      command=dialog.destroy).pack(side="right")

    # ── Batch backtest several bundles → ranked summary ──────────────────
    def _open_batch_bundles_dialog(self):
        """Pick N bundles + shared knobs (rounds/sims/balance). Runs each
        bundle through run_campaign in sequence on the SAME data slice and
        opens a ranked-results modal at the end. Each individual run also
        auto-saves to the Recent Runs folder so you can drill into any one."""
        import glob as _glob
        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        bundle_files = []
        if os.path.isdir(bundles_dir):
            bundle_files = sorted(
                os.path.splitext(os.path.basename(p))[0]
                for p in _glob.glob(os.path.join(bundles_dir, "*.json"))
            )
        if not bundle_files:
            messagebox.showinfo("Batch Backtest",
                                "No bundles found in ~/.spinedge/bundles/. "
                                "Save at least one bundle first.")
            return

        dialog = ctk.CTkToplevel(self.parent_frame)
        dialog.title("Batch Backtest Bundles")
        dialog.transient(self.parent_frame)
        dialog.grab_set()
        dialog.geometry("780x720")
        dialog.configure(fg_color="#09090b")

        ctk.CTkLabel(dialog, text="🏆  Batch Backtest Bundles",
                     font=("Roboto", 16, "bold"),
                     text_color="#fbbf24").pack(padx=14, pady=(14, 4), anchor="w")
        ctk.CTkLabel(dialog,
                     text="Pick any number of bundles. They'll run in sequence against the "
                          "same data slice with the same knobs, then a ranked summary opens. "
                          "Each individual run auto-saves to Recent Runs for drill-down.",
                     font=("Segoe UI", 12), text_color="#94a3b8",
                     wraplength=720, justify="left").pack(padx=14, pady=(0, 10), anchor="w")

        # Shared run knobs
        knobs = ctk.CTkFrame(dialog, fg_color="#0f172a", corner_radius=8)
        knobs.pack(fill="x", padx=14, pady=4)
        for c in range(4):
            knobs.grid_columnconfigure(c, weight=1)
        rounds_var = tk.StringVar(value="200")
        sims_var = tk.StringVar(value="20")
        init_bal_var = tk.StringVar(value="100")
        sim_mode_var = tk.StringVar(value="sequential")
        data_source_var = tk.StringVar(value="db")
        db_limit_var = tk.StringVar(value="5000")

        def _row(parent, r, col_lbl, col_ent, label, var, width=80):
            ctk.CTkLabel(parent, text=label, font=("Segoe UI", 12),
                         text_color="#cbd5e1").grid(row=r, column=col_lbl, sticky="w",
                                                    padx=(10, 4), pady=4)
            ctk.CTkEntry(parent, textvariable=var, width=width).grid(
                row=r, column=col_ent, sticky="w", pady=4)
        _row(knobs, 0, 0, 1, "Rounds / session:", rounds_var)
        _row(knobs, 0, 2, 3, "Sessions per bundle:", sims_var)
        _row(knobs, 1, 0, 1, "Initial balance ($):", init_bal_var)
        ctk.CTkLabel(knobs, text="Sim mode:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=1, column=2, sticky="w",
                                                padx=(10, 4), pady=4)
        ctk.CTkComboBox(knobs, variable=sim_mode_var,
                        values=["sequential", "independent"],
                        state="readonly", width=130).grid(row=1, column=3, sticky="w", pady=4)
        ctk.CTkLabel(knobs, text="Data source:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=2, column=0, sticky="w",
                                                padx=(10, 4), pady=4)
        ctk.CTkComboBox(knobs, variable=data_source_var,
                        values=["db", "generated"],
                        state="readonly", width=130).grid(row=2, column=1, sticky="w", pady=4)
        _row(knobs, 2, 2, 3, "Use latest K spins:", db_limit_var)

        # Row 3 — Skip-latest offset + Preview button + Lock toggle
        db_offset_var = tk.StringVar(value="0")
        _row(knobs, 3, 0, 1, "Skip latest:", db_offset_var)

        batch_lock_var = tk.BooleanVar(value=False)
        batch_locked_anchor = {"id": None}

        batch_slice_status = ctk.CTkLabel(
            knobs,
            text="ℹ Click Preview to confirm the exact DB window every bundle will share. "
                 "Tick 🔒 Lock to freeze the snapshot so all bundles + every re-run use "
                 "the SAME rows (otherwise the watcher's new spins shift the window).",
            font=("Segoe UI", 11, "italic"), text_color="#94a3b8",
            anchor="w", justify="left", wraplength=720,
        )
        batch_slice_status.grid(row=4, column=0, columnspan=4,
                                sticky="ew", padx=10, pady=(2, 6))

        def _resolve_batch_anchor():
            if not batch_lock_var.get():
                return None
            if batch_locked_anchor["id"] is None:
                from core.utils.db_utils import get_max_winning_number_id
                try:
                    batch_locked_anchor["id"] = get_max_winning_number_id()
                except Exception:
                    batch_locked_anchor["id"] = None
            return batch_locked_anchor["id"]

        def _batch_preview_slice():
            if data_source_var.get() != "db":
                batch_slice_status.configure(
                    text="ℹ Data source is 'generated' — DB preview only applies "
                         "when data source is 'db'.",
                    text_color="#94a3b8")
                return
            try:
                limit = max(1, int(db_limit_var.get() or "5000"))
                offset = max(0, int(db_offset_var.get() or "0"))
                anchor = _resolve_batch_anchor()
                total = self._db_total_count()
                sliced = self._resolve_db_slice(limit, offset, anchor_id=anchor)
                text, color = self._format_slice_summary(
                    sliced, total, limit, offset, anchor_id=anchor)
                batch_slice_status.configure(text=text, text_color=color)
            except Exception as e:
                batch_slice_status.configure(
                    text=f"⚠ DB preview failed: {e}", text_color="#ef4444")

        def _on_batch_lock_toggle():
            if batch_lock_var.get():
                from core.utils.db_utils import get_max_winning_number_id
                try:
                    batch_locked_anchor["id"] = get_max_winning_number_id()
                except Exception:
                    batch_locked_anchor["id"] = None
            else:
                batch_locked_anchor["id"] = None
            _batch_preview_slice()

        ctk.CTkButton(
            knobs, text="🔍 Preview Slice", width=130, height=28,
            command=_batch_preview_slice,
            fg_color="#0ea5e9", hover_color="#0284c7",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=3, column=2, sticky="w", padx=(20, 4), pady=4)
        ctk.CTkCheckBox(
            knobs, text="🔒 Lock slice", variable=batch_lock_var,
            command=_on_batch_lock_toggle, fg_color="#a855f7",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=3, column=3, sticky="w", pady=4)

        # Bundle picker — scrollable checkbox list
        ctk.CTkLabel(dialog, text="Bundles to include:",
                     font=("Segoe UI", 11, "bold"),
                     text_color="#cbd5e1").pack(padx=14, pady=(10, 2), anchor="w")
        pick_actions = ctk.CTkFrame(dialog, fg_color="transparent")
        pick_actions.pack(fill="x", padx=14)
        ctk.CTkButton(pick_actions, text="Select all", width=90,
                      command=lambda: _set_all(True),
                      fg_color="#334155", hover_color="#475569",
                      font=("Segoe UI", 12)).pack(side="left", padx=(0, 4))
        ctk.CTkButton(pick_actions, text="Select none", width=90,
                      command=lambda: _set_all(False),
                      fg_color="#334155", hover_color="#475569",
                      font=("Segoe UI", 12)).pack(side="left", padx=4)
        selected_count_lbl = ctk.CTkLabel(pick_actions, text="(0 selected)",
                                          font=("Segoe UI", 12),
                                          text_color="#94a3b8")
        selected_count_lbl.pack(side="left", padx=12)

        bundle_scroll = ctk.CTkScrollableFrame(dialog, fg_color="#0f172a", height=260)
        bundle_scroll.pack(fill="both", expand=True, padx=14, pady=(4, 6))

        bundle_vars = {}  # name -> BooleanVar
        def _update_count(*_a):
            n = sum(1 for v in bundle_vars.values() if v.get())
            selected_count_lbl.configure(text=f"({n} of {len(bundle_vars)} selected)")
        for name in bundle_files:
            v = tk.BooleanVar(value=False)
            v.trace_add("write", _update_count)
            ctk.CTkCheckBox(bundle_scroll, text=name, variable=v,
                            font=("Consolas", 12)).pack(anchor="w", padx=6, pady=2)
            bundle_vars[name] = v
        def _set_all(val):
            for v in bundle_vars.values():
                v.set(val)

        # Action row
        action_row = ctk.CTkFrame(dialog, fg_color="transparent")
        action_row.pack(fill="x", padx=14, pady=(4, 14))
        def _run():
            picked = [n for n, v in bundle_vars.items() if v.get()]
            if not picked:
                messagebox.showwarning("Batch Backtest",
                                       "Pick at least one bundle.")
                return
            try:
                rounds = max(1, int(rounds_var.get() or "200"))
                sims = max(1, int(sims_var.get() or "20"))
                init_bal = max(0.01, float(init_bal_var.get() or "100"))
                db_limit = max(1, int(db_limit_var.get() or "5000"))
                db_offset = max(0, int(db_offset_var.get() or "0"))
            except ValueError as e:
                messagebox.showerror("Batch Backtest", f"Bad numeric input: {e}")
                return
            shared_knobs = {
                "rounds": rounds, "sims": sims, "initial_balance": init_bal,
                "sim_mode": sim_mode_var.get(),
                "historical_data_source": data_source_var.get(),
                "db_limit": db_limit,
                "db_offset": db_offset,
                "db_anchor_id": _resolve_batch_anchor(),
            }
            dialog.destroy()
            self._run_batch_bundles_thread(picked, bundles_dir, shared_knobs)

        ctk.CTkButton(action_row, text="▶  Run Batch", height=38,
                      font=("Roboto", 12, "bold"),
                      fg_color="#b45309", hover_color="#d97706",
                      command=_run).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(action_row, text="Close", width=120, height=38,
                      fg_color="#334155", hover_color="#475569",
                      command=dialog.destroy).pack(side="right")

    def _run_batch_bundles_thread(self, bundle_names, bundles_dir, shared_knobs):
        """Spawn a worker that runs each bundle in series, then opens the
        ranked-results modal."""
        import threading
        if self.run_button.cget("text") != "🚀 Run Backtest":
            messagebox.showwarning("Busy", "A backtest is already running.")
            return
        # Disable primary buttons while batch is in-flight
        for btn in (self.run_button, self.bundle_button, self.sweep_button,
                    getattr(self, "batch_button", None)):
            if btn is not None:
                try:
                    btn.configure(state="disabled")
                except Exception:
                    pass

        self._begin_status_run(len(bundle_names), label=f"Batch ({len(bundle_names)} bundles)")
        results_by_bundle = {}   # bundle_name -> CampaignResult

        def _ui_log(msg):
            self.parent_frame.after(0, lambda m=msg: self._log(m))

        def _worker():
            try:
                from core.backtesting_runner import backtest_bundle
                custom = self._fetch_custom_strategies()
                for idx, name in enumerate(bundle_names, start=1):
                    _ui_log(f"━━━ [{idx}/{len(bundle_names)}] {name} ━━━")
                    path = os.path.join(bundles_dir, f"{name}.json")
                    if not os.path.isfile(path):
                        _ui_log(f"  ⚠ Skipping (file not found): {path}")
                        continue
                    try:
                        # db_offset / db_anchor_id go through extra_overrides
                        # because backtest_bundle doesn't accept them as kwargs.
                        # run_campaign reads them from cfg directly.
                        _offset = int(shared_knobs.get("db_offset", 0) or 0)
                        _anchor = shared_knobs.get("db_anchor_id")
                        _overrides = {}
                        if _offset > 0:
                            _overrides["db_offset"] = _offset
                        if _anchor is not None:
                            _overrides["db_anchor_id"] = _anchor
                        camp = backtest_bundle(
                            path,
                            initial_balance=shared_knobs["initial_balance"],
                            rounds=shared_knobs["rounds"],
                            sims=shared_knobs["sims"],
                            sim_mode=shared_knobs["sim_mode"],
                            historical_data_source=shared_knobs["historical_data_source"],
                            db_limit=shared_knobs["db_limit"],
                            custom_strategies=custom,
                            on_log=_ui_log,
                            extra_overrides=_overrides or None,
                        )
                        results_by_bundle[name] = camp
                        # Auto-save each run (so individual drill-down works)
                        try:
                            mock_results = {camp.sessions[0].bet_history[0].get("strategy", name)
                                            if camp.sessions and camp.sessions[0].bet_history else name: camp.sessions}
                            strat_key = next(iter(mock_results.keys()))
                            # Temporarily swap state so _save_run_to_disk uses
                            # this bundle's results
                            prev_results = self.results
                            prev_camp = getattr(self, "_last_campaign", None)
                            prev_analysis = self.analysis
                            self.results = {strat_key: camp.sessions}
                            self._last_campaign = camp
                            from core.backtesting import RouletteBacktester
                            self.analysis = RouletteBacktester().analyze_results(self.results)
                            cfg_used = {
                                "strategy_name": strat_key,
                                **shared_knobs,
                                "bundle": name,
                            }
                            self.parent_frame.after(
                                0,
                                lambda n=strat_key, c=cfg_used, lbl=f"batch_{name}":
                                    self._save_run_to_disk(n, c, label=lbl),
                            )
                            # Restore previous state immediately so we don't
                            # leave the GUI showing one batch member as "the"
                            # active results
                            self.results = prev_results
                            self._last_campaign = prev_camp
                            self.analysis = prev_analysis
                        except Exception as _save_err:
                            _ui_log(f"  (auto-save failed: {_save_err})")
                    except Exception as e:
                        _ui_log(f"  ✗ Error running {name}: {e}")
                    # Update status with running batch progress
                    self._status_current_session = idx
                self.parent_frame.after(
                    0, lambda r=results_by_bundle: self._show_batch_results_modal(r)
                )
                self.parent_frame.after(0, lambda: self._end_status_run(0.0, ok=True))
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                self.parent_frame.after(0, lambda: messagebox.showerror(
                    "Batch Backtest Failed", f"{e}\n\n{tb}"))
                self.parent_frame.after(0, lambda: self._end_status_run(0.0, ok=False))
            finally:
                for btn in (self.run_button, self.bundle_button, self.sweep_button,
                            getattr(self, "batch_button", None)):
                    if btn is not None:
                        self.parent_frame.after(0, lambda b=btn: b.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_batch_results_modal(self, results_by_bundle: dict):
        """Open a ranked-by-PnL table over the batch's results."""
        if not results_by_bundle:
            messagebox.showinfo("Batch Results", "No bundles produced results.")
            return
        dialog = ctk.CTkToplevel(self.parent_frame)
        dialog.title("Batch Results — Ranked")
        dialog.transient(self.parent_frame)
        dialog.grab_set()
        dialog.geometry("980x560")
        dialog.configure(fg_color="#09090b")

        ctk.CTkLabel(dialog, text="🏆  Batch Backtest Results",
                     font=("Roboto", 16, "bold"),
                     text_color="#fbbf24").pack(padx=14, pady=(14, 4), anchor="w")
        ctk.CTkLabel(dialog,
                     text=f"{len(results_by_bundle)} bundle(s) — ranked by Campaign PnL. "
                          f"Double-click a row to load that bundle's results into the main view.",
                     font=("Segoe UI", 12), text_color="#94a3b8").pack(padx=14, pady=(0, 10), anchor="w")

        # Compute metrics for each
        from types import SimpleNamespace
        rows = []
        for name, camp in results_by_bundle.items():
            sessions = list(camp.sessions or [])
            strat_key = "strat"
            if sessions and sessions[0].bet_history:
                strat_key = sessions[0].bet_history[0].get("strategy", "strat")
            mock = SimpleNamespace(results={strat_key: sessions}, _last_campaign=camp)
            try:
                m = type(self)._compute_metrics(mock, strat_key)
            except Exception:
                m = {}
            rows.append({
                "bundle": name,
                "pnl": m.get("campaign_pnl", 0.0),
                "roi": m.get("roi_pct", 0.0),
                "win_rate": m.get("win_rate", 0.0),
                "sharpe": m.get("sharpe", 0.0),
                "profit_factor": m.get("profit_factor", 0.0),
                "max_dd": m.get("max_dd_pct", 0.0),
                "rounds": m.get("total_rounds", 0),
                "bankruptcies": m.get("bankruptcies", 0),
                "_strategy_key": strat_key,
                "_sessions": sessions,
                "_campaign": camp,
            })
        rows.sort(key=lambda r: r["pnl"], reverse=True)

        # Treeview
        tree_container = tk.Frame(dialog, bg="#0b1220")
        tree_container.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        _style = ttk.Style()
        _style.configure("Batch.Treeview", background="#0f172a",
                         foreground="#e2e8f0", fieldbackground="#0f172a",
                         rowheight=26, font=("Consolas", 12), borderwidth=0)
        _style.configure("Batch.Treeview.Heading", background="#1e293b",
                         foreground="#facc15", font=("Segoe UI", 10, "bold"))

        tree = ttk.Treeview(tree_container, style="Batch.Treeview", show="headings",
                            columns=("rank", "bundle", "pnl", "roi", "wr", "sharpe",
                                     "pf", "dd", "rounds", "bk"),
                            height=14, selectmode="browse")
        for c, t, w, a in [
            ("rank",   "#",         40,  "center"),
            ("bundle", "Bundle",    240, "w"),
            ("pnl",    "PnL ($)",   100, "e"),
            ("roi",    "ROI %",     80,  "e"),
            ("wr",     "Win %",     70,  "e"),
            ("sharpe", "Sharpe",    80,  "e"),
            ("pf",     "PF",        70,  "e"),
            ("dd",     "Max DD %",  85,  "e"),
            ("rounds", "Rounds",    80,  "e"),
            ("bk",     "Bnkrpcy",   70,  "center"),
        ]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=a, stretch=(c == "bundle"))
        tree.tag_configure("winner", background="#0f1d17", foreground="#86efac")
        tree.tag_configure("loser",  background="#1d0f0f", foreground="#fca5a5")
        tree.tag_configure("neutral", background="#0f172a")
        tree.pack(side="left", fill="both", expand=True)
        vbar = ttk.Scrollbar(tree_container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")

        for rank, r in enumerate(rows, start=1):
            tag = "winner" if r["pnl"] > 0 else ("loser" if r["pnl"] < 0 else "neutral")
            try:
                pf_disp = "∞" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
            except Exception:
                pf_disp = "—"
            tree.insert(
                "", "end", iid=str(rank - 1),
                values=(rank, r["bundle"][:36],
                        f"${r['pnl']:+,.2f}", f"{r['roi']:+.2f}%",
                        f"{r['win_rate']:.1f}%", f"{r['sharpe']:.2f}",
                        pf_disp, f"{r['max_dd']:.1f}%",
                        f"{r['rounds']:,}", r["bankruptcies"]),
                tags=(tag,),
            )

        # Double-click a row → load that bundle's results into main UI
        def _load_into_main(_event=None):
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            r = rows[idx]
            self.results = {r["_strategy_key"]: r["_sessions"]}
            self._last_campaign = r["_campaign"]
            from core.backtesting import RouletteBacktester
            self.analysis = RouletteBacktester().analyze_results(self.results)
            try:
                self._display_summary(r["_strategy_key"])
                self._plot_results()
                self.save_button.configure(state="normal")
                self.export_button.configure(state="normal")
                self.export_csv_button.configure(state="normal")
                self._set_status(f"🏆 Loaded batch winner: {r['bundle']}", color="#fbbf24")
            except Exception as e:
                print(f"[Batch] load into main failed: {e}")
            dialog.destroy()

        tree.bind("<Double-1>", _load_into_main)
        tree.bind("<Return>",   _load_into_main)

        # Footer
        footer = ctk.CTkFrame(dialog, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkLabel(footer,
                     text="Tip: each bundle's run is also saved in Recent Runs for full drill-down.",
                     font=("Segoe UI", 11), text_color="#64748b").pack(side="left")
        ctk.CTkButton(footer, text="Close", width=120, height=34,
                      fg_color="#334155", hover_color="#475569",
                      command=dialog.destroy).pack(side="right")

    # ── Session Audit Tab populator ──────────────────────────────────────
    def _populate_session_audit(self, strategy_name: str):
        """One row per session: PnL, rounds, stop reason, effective base/SL,
        escalation step. Below the table, render the campaign's escalation log."""
        try:
            self._audit_tree.delete(*self._audit_tree.get_children())
        except Exception:
            return
        sessions = self.results.get(strategy_name) or []
        if not sessions:
            try:
                self._audit_summary_lbl.configure(text="(no run loaded)")
                self._audit_esc_log.configure(state="normal")
                self._audit_esc_log.delete("1.0", "end")
                self._audit_esc_log.insert("end", "(no run loaded)")
                self._audit_esc_log.configure(state="disabled")
            except Exception:
                pass
            return

        # Stop-reason breakdown for the summary line
        from collections import Counter
        reason_counts = Counter()
        bk_count = 0
        for i, sim in enumerate(sessions, start=1):
            reason = getattr(sim, 'stop_reason', '') or 'N/A'
            reason_counts[reason] += 1
            start_bal = float(getattr(sim, 'initial_balance', 0.0) or 0.0)
            end_bal = float(getattr(sim, 'final_balance', 0.0) or 0.0)
            pnl = end_bal - start_bal
            rounds = int(getattr(sim, 'total_rounds', 0) or 0)
            wins = int(getattr(sim, 'total_wins', 0) or 0)
            losses = int(getattr(sim, 'total_losses', 0) or 0)
            mdd = float(getattr(sim, 'max_drawdown', 0.0) or 0.0)
            base_bet = float(getattr(sim, 'effective_base_bet', 0.0) or 0.0)
            max_loss = float(getattr(sim, 'effective_max_loss', 0.0) or 0.0)
            esc = int(getattr(sim, 'escalation_step', 0) or 0)
            msg = getattr(sim, 'stop_message', '') or ''
            # Trigger-engine activity per session: count SKIP rows and rounds
            # that carry a trigger_reason. Both are zero for plain rotation.
            history = getattr(sim, 'bet_history', None) or []
            skip_count = sum(1 for rec in history if rec.get('result') == 'SKIP')
            trig_count = sum(1 for rec in history if (rec.get('trigger_reason') or '').strip())
            # Tag pick
            is_bankrupt = end_bal <= 0.01
            tag = ("bankrupt" if is_bankrupt
                   else ("escalated" if esc > 0
                         else ("win_sess" if pnl > 0
                               else "loss_sess" if pnl < 0 else "neutral")))
            if is_bankrupt:
                bk_count += 1
            self._audit_tree.insert(
                "", "end", iid=str(i),
                values=(
                    i,
                    f"${start_bal:,.2f}", f"${end_bal:,.2f}",
                    f"${pnl:+,.2f}",
                    rounds, wins, losses,
                    skip_count if skip_count > 0 else "—",
                    trig_count if trig_count > 0 else "—",
                    f"${mdd:,.2f}",
                    f"${base_bet:.2f}", f"${max_loss:.2f}",
                    esc if esc > 0 else "—",
                    reason,
                    msg[:120],
                ),
                tags=(tag,),
            )

        # Summary header
        try:
            top3 = ", ".join(f"{r}={n}" for r, n in reason_counts.most_common(3))
            self._audit_summary_lbl.configure(
                text=(f"{len(sessions)} sessions · "
                      f"top stop reasons: {top3} · "
                      f"{bk_count} bankrupt"),
                text_color="#cbd5e1",
            )
        except Exception:
            pass

        # Escalation log
        try:
            self._audit_esc_log.configure(state="normal")
            self._audit_esc_log.delete("1.0", "end")
            camp = getattr(self, "_last_campaign", None)
            log = list((camp.escalation_log if camp is not None else []) or [])
            if not log:
                self._audit_esc_log.insert(
                    "end",
                    "(escalation_on_loss disabled or no transitions yet)\n"
                )
            else:
                for line in log:
                    self._audit_esc_log.insert("end", f"{line}\n")
            self._audit_esc_log.configure(state="disabled")
        except Exception:
            pass

    def _sort_audit_tree(self, col: str, descending: bool):
        try:
            items = [(self._audit_tree.set(k, col), k) for k in self._audit_tree.get_children("")]
            def _key(v):
                s = v[0]
                try:
                    return float(str(s).replace("$", "").replace("+", "").replace(",", ""))
                except (ValueError, AttributeError):
                    return str(s).lower()
            items.sort(key=_key, reverse=descending)
            for idx, (_v, k) in enumerate(items):
                self._audit_tree.move(k, "", idx)
            self._audit_tree.heading(col, command=lambda: self._sort_audit_tree(col, not descending))
        except Exception:
            pass

    def _on_audit_session_double_click(self, _event=None):
        """Double-click a session row → jump to Round Audit on round 1 of that session."""
        sel = self._audit_tree.selection()
        if not sel:
            return
        try:
            sess_num = int(sel[0])
            self._audit_session_var.set(str(sess_num))
            self._audit_round_var.set("1")
            self._open_round_audit_modal()
        except Exception as e:
            messagebox.showerror("Session Audit", f"Couldn't open round: {e}")

    def _populate_metrics_panel(self, strategy_name: str):
        """Update the Metrics tab cards from the current results."""
        try:
            metrics = self._compute_metrics(strategy_name)
        except Exception as e:
            print(f"[BacktestGUI] _compute_metrics failed: {e}")
            return
        for key, title, fmt, _hint, accent, positive_is_good in self.METRIC_LAYOUT:
            widget_tuple = self._metric_card_widgets.get(key)
            if widget_tuple is None:
                continue
            value_lbl, default_accent = widget_tuple
            if key not in metrics:
                continue
            v = metrics[key]
            try:
                if v == float("inf"):
                    txt = "∞"
                else:
                    txt = fmt.format(v)
            except (ValueError, TypeError):
                txt = str(v)
            # Color the value: green if "good" direction, red if "bad" direction
            try:
                v_num = float(v) if not isinstance(v, str) else 0.0
            except (ValueError, TypeError):
                v_num = 0.0
            color = "#e2e8f0"  # neutral default
            if isinstance(v, (int, float)) and v != float("inf"):
                if positive_is_good:
                    color = "#22c55e" if v_num > 0 else ("#ef4444" if v_num < 0 else "#e2e8f0")
                else:
                    color = "#ef4444" if v_num > 0 else "#22c55e"
            try:
                value_lbl.configure(text=txt, text_color=color)
            except Exception:
                pass

    # ── Analytics Tab ────────────────────────────────────────────────────
    def _populate_analytics_tab(self, strategy_name: str):
        """Rebuild every panel on the Analytics tab from the loaded results.

        Panels (top → bottom):
          1) Summary banner with rollup counts
          2) Session P&L histogram + Round P&L histogram (side-by-side)
          3) Underwater drawdown plot (full width)
          4) Roulette group breakdown (color/parity/dozen/column) +
             hot/cold spin numbers (side-by-side)
          5) Worst 10 / Best 10 rounds tables — each row clickable to jump
          6) Parallel-mode per-strategy contribution table (only when
             parallel_strategies data is present in bet_history)
        """
        host = getattr(self, "_analytics_scroll", None)
        if host is None:
            return
        # Close the previous render's matplotlib figures FIRST (Tk widget
        # destroy alone leaks them), then wipe leftover widgets.
        self._teardown_figs("analytics")
        for w in host.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        sessions = self.results.get(strategy_name) or []
        if not sessions:
            try:
                self._analytics_status_lbl.configure(
                    text="(no sessions in current results)", text_color="#f59e0b")
            except Exception:
                pass
            return

        # Walk the campaign ONCE and pull everything we need for the panels —
        # cheaper than re-iterating per-panel for big runs.
        round_pnls: list[float] = []          # per-round pnl across all sessions
        round_lookup: list[tuple] = []        # (sess_num, round_num, pnl, spin, result, balance_after)
        spin_counts: dict[int, int] = {}      # spin number → hit count
        spin_pnl: dict[int, float] = {}       # spin number → cumulative pnl for bot
        group_pnl: dict[str, dict[str, dict]] = {
            "color":  {"red": {"n": 0, "pnl": 0.0}, "black": {"n": 0, "pnl": 0.0}, "zero": {"n": 0, "pnl": 0.0}},
            "parity": {"even": {"n": 0, "pnl": 0.0}, "odd": {"n": 0, "pnl": 0.0}, "zero": {"n": 0, "pnl": 0.0}},
            "dozen":  {"1st12": {"n": 0, "pnl": 0.0}, "2nd12": {"n": 0, "pnl": 0.0}, "3rd12": {"n": 0, "pnl": 0.0}, "zero": {"n": 0, "pnl": 0.0}},
        }
        try:
            from core.signals.base import GROUPS as _GROUPS
        except Exception:
            _GROUPS = None
        session_pnls: list[float] = []        # one per session — for the histogram
        parallel_contribution: dict[str, dict] = {}   # strategy → {wins, losses, pnl, rounds}
        has_parallel = False
        bankruptcy_count = 0

        for sess_idx, sim in enumerate(sessions, start=1):
            session_pnls.append(float(getattr(sim, "total_profit", 0.0) or 0.0))
            for rec in (getattr(sim, "bet_history", None) or []):
                pnl = float(rec.get("pnl", 0.0) or 0.0)
                spin = rec.get("spin_result")
                result = str(rec.get("result", ""))
                bal_after = float(rec.get("balance_after", 0.0) or 0.0)
                round_pnls.append(pnl)
                round_lookup.append((sess_idx, int(rec.get("round", 0)),
                                     pnl, spin, result, bal_after))
                if bal_after <= 0.01 and pnl < 0:
                    bankruptcy_count += 1
                # Per-spin and per-group attribution — only for actual numeric
                # spins. SKIP rounds have no outcome to attribute.
                try:
                    n = int(spin)
                except (TypeError, ValueError):
                    n = None
                if n is not None and 0 <= n <= 36:
                    spin_counts[n] = spin_counts.get(n, 0) + 1
                    spin_pnl[n] = spin_pnl.get(n, 0.0) + pnl
                    if _GROUPS:
                        for g_key in ("color", "parity", "dozen"):
                            member = _GROUPS[g_key]["fn"](n) or "zero"
                            cell = group_pnl[g_key].setdefault(
                                member, {"n": 0, "pnl": 0.0})
                            cell["n"] += 1
                            cell["pnl"] += pnl
                # Parallel-mode contribution — bet_history carries the joined
                # name + the per-strategy bet list. Each entry in ps_list is
                # a dict with its own pnl/result/total_bet, so attribution is
                # exact (not an equal-share approximation).
                ps_list = rec.get("parallel_strategies") or []
                if ps_list:
                    has_parallel = True
                    for ps in ps_list:
                        # Backward-compat: older runs may have stored ps_list
                        # as a list of name strings rather than dicts.
                        if isinstance(ps, dict):
                            ps_name = str(ps.get("name", "?"))
                            ps_pnl = float(ps.get("pnl", 0.0) or 0.0)
                            ps_result = str(ps.get("result", "")).upper()
                            ps_bet = float(ps.get("total_bet", 0.0) or 0.0)
                        else:
                            ps_name = str(ps)
                            ps_pnl = pnl / max(1, len(ps_list))
                            ps_result = result.upper()
                            ps_bet = 0.0
                        slot = parallel_contribution.setdefault(
                            ps_name, {"rounds": 0, "wins": 0, "losses": 0,
                                       "pnl": 0.0, "wagered": 0.0})
                        slot["rounds"] += 1
                        slot["pnl"] += ps_pnl
                        slot["wagered"] += ps_bet
                        if ps_result == "WIN":
                            slot["wins"] += 1
                        elif ps_result == "LOSS":
                            slot["losses"] += 1

        try:
            self._analytics_status_lbl.configure(
                text=f"{len(sessions)} sessions · {len(round_pnls)} rounds · "
                     f"{'parallel data ✓' if has_parallel else 'sequential / single'}",
                text_color="#94a3b8")
        except Exception:
            pass

        # ── 1) Top summary ribbon ────────────────────────────────────────
        ribbon = ctk.CTkFrame(host, fg_color="#1f2937", corner_radius=8)
        ribbon.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=(4, 8))
        for i in range(6):
            ribbon.grid_columnconfigure(i, weight=1, uniform="ribbon")

        def _badge(parent, col, label, value, color):
            cell = ctk.CTkFrame(parent, fg_color="transparent")
            cell.grid(row=0, column=col, sticky="ew", padx=8, pady=6)
            ctk.CTkLabel(cell, text=label, font=("Segoe UI", 10),
                         text_color="#94a3b8").pack(anchor="w")
            ctk.CTkLabel(cell, text=value, font=("Segoe UI", 16, "bold"),
                         text_color=color).pack(anchor="w")

        total_pnl = sum(session_pnls)
        positive_rounds = sum(1 for p in round_pnls if p > 0)
        skip_rounds = sum(1 for r in round_lookup if r[4] == "SKIP")
        win_rate_round = (positive_rounds / max(1, len(round_pnls))) * 100
        best_session = max(session_pnls) if session_pnls else 0
        worst_session = min(session_pnls) if session_pnls else 0
        _badge(ribbon, 0, "Total PnL",
               f"${total_pnl:+.2f}", "#22c55e" if total_pnl >= 0 else "#ef4444")
        _badge(ribbon, 1, "Round Win%", f"{win_rate_round:.1f}%", "#facc15")
        _badge(ribbon, 2, "Best Session",
               f"${best_session:+.2f}", "#22c55e")
        _badge(ribbon, 3, "Worst Session",
               f"${worst_session:+.2f}", "#ef4444")
        _badge(ribbon, 4, "Bankruptcies",
               str(bankruptcy_count), "#ef4444" if bankruptcy_count else "#94a3b8")
        _badge(ribbon, 5, "Skipped Rounds",
               str(skip_rounds), "#a78bfa" if skip_rounds else "#94a3b8")

        # ── 2) Histograms (Session PnL + Round PnL) ──────────────────────
        if plt is not None and FigureCanvasTkAgg is not None:
            self._build_analytics_hist_panels(host, session_pnls, round_pnls)
            # ── 3) Underwater drawdown ───────────────────────────────────
            self._build_analytics_underwater_panel(host, sessions)
        else:
            ctk.CTkLabel(
                host, text="matplotlib not available — charts disabled",
                text_color="#f59e0b").grid(row=1, column=0, columnspan=2,
                                           sticky="w", padx=8, pady=8)

        # ── 4) Group breakdown + Hot/Cold spin numbers ───────────────────
        self._build_analytics_group_panel(host, group_pnl, row=4)
        self._build_analytics_spins_panel(host, spin_counts, spin_pnl, row=4)

        # ── 5) Worst / Best round tables ─────────────────────────────────
        self._build_analytics_worst_best_tables(host, round_lookup, row=5)

        # ── 6) Parallel attribution (only when data exists) ──────────────
        if has_parallel and parallel_contribution:
            self._build_analytics_parallel_panel(host, parallel_contribution, row=6)

    # ── Analytics panel builders ─────────────────────────────────────────
    def _make_panel_card(self, host, row, col, title, colspan=1):
        """Standard card container used by every analytics panel."""
        card = ctk.CTkFrame(host, fg_color="#0f172a", corner_radius=8)
        card.grid(row=row, column=col, columnspan=colspan,
                  sticky="nsew", padx=4, pady=6)
        ctk.CTkLabel(card, text=title, font=("Segoe UI", 12, "bold"),
                     text_color="#a78bfa").pack(anchor="w", padx=10, pady=(8, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=8)
        return body

    def _register_fig(self, key, fig, canvas=None, cursor=None, cid=None):
        """Track a matplotlib figure/canvas under a chart slot so the next
        render of that slot can deterministically tear it down."""
        self._fig_registry.setdefault(key, []).append((fig, canvas, cursor, cid))

    def _teardown_figs(self, key):
        """Close + free every matplotlib figure/canvas tracked under `key`.

        Destroying the Tk widget alone does NOT release the matplotlib Figure,
        its Agg buffer, the mpl_connect click handler, or the mplcursors hover
        cursor (which keeps firing on mouse-move). We explicitly remove the
        cursor, disconnect callbacks, destroy the canvas widget, and plt.close()
        the figure so repeated backtests don't accumulate them.
        """
        for fig, canvas, cursor, cid in self._fig_registry.pop(key, []):
            try:
                if cursor is not None:
                    cursor.remove()
            except Exception:
                pass
            try:
                if cid is not None and canvas is not None:
                    canvas.mpl_disconnect(cid)
            except Exception:
                pass
            try:
                if canvas is not None:
                    canvas.get_tk_widget().destroy()
            except Exception:
                pass
            try:
                if fig is not None and plt is not None:
                    fig.clf()
                    plt.close(fig)
            except Exception:
                pass

    def _embed_mpl(self, host_widget, figure):
        """Embed a matplotlib figure inside a CTk widget consistently.

        Registers the figure/canvas under the 'analytics' slot so the next
        analytics render closes it (see _populate_analytics_tab)."""
        canvas = FigureCanvasTkAgg(figure, master=host_widget)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self._register_fig("analytics", figure, canvas)
        return canvas

    def _build_analytics_hist_panels(self, host, session_pnls, round_pnls):
        # Session histogram (left)
        body = self._make_panel_card(host, row=2, col=0,
                                     title="Distribution of Session PnL")
        fig = plt.Figure(figsize=(5, 3), dpi=92, facecolor="#0f172a")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#0f172a")
        if session_pnls:
            colors = ["#22c55e" if p >= 0 else "#ef4444" for p in sorted(session_pnls)]
            ax.bar(range(len(session_pnls)), sorted(session_pnls),
                   color=colors, edgecolor="#1e293b", linewidth=0.5)
            ax.axhline(0, color="#94a3b8", linestyle="--", linewidth=0.8, alpha=0.5)
            ax.set_xlabel("Session (sorted)", color="#cbd5e1", fontsize=9)
            ax.set_ylabel("PnL ($)", color="#cbd5e1", fontsize=9)
            ax.tick_params(colors="#94a3b8", labelsize=8)
            for spine in ax.spines.values():
                spine.set_edgecolor("#334155")
            ax.grid(True, axis="y", linestyle=":", alpha=0.25, color="#475569")
        fig.tight_layout()
        self._embed_mpl(body, fig)

        # Round PnL histogram (right) — clamp to a sensible bin count.
        body2 = self._make_panel_card(host, row=2, col=1,
                                      title="Distribution of Per-Round PnL")
        fig2 = plt.Figure(figsize=(5, 3), dpi=92, facecolor="#0f172a")
        ax2 = fig2.add_subplot(111)
        ax2.set_facecolor("#0f172a")
        if round_pnls:
            bins = min(40, max(8, int(len(round_pnls) ** 0.5)))
            ax2.hist(round_pnls, bins=bins, color="#0ea5e9",
                     edgecolor="#1e293b", linewidth=0.5, alpha=0.85)
            ax2.axvline(0, color="#94a3b8", linestyle="--", linewidth=0.8, alpha=0.6)
            ax2.set_xlabel("Per-round PnL ($)", color="#cbd5e1", fontsize=9)
            ax2.set_ylabel("Count", color="#cbd5e1", fontsize=9)
            ax2.tick_params(colors="#94a3b8", labelsize=8)
            for spine in ax2.spines.values():
                spine.set_edgecolor("#334155")
            ax2.grid(True, axis="y", linestyle=":", alpha=0.25, color="#475569")
        fig2.tight_layout()
        self._embed_mpl(body2, fig2)

    def _build_analytics_underwater_panel(self, host, sessions):
        """Underwater plot: % drawdown from running peak, over campaign rounds.
        Lets the user see WHEN they were in drawdown and HOW DEEP."""
        body = self._make_panel_card(host, row=3, col=0, colspan=2,
                                     title="Underwater — % below running peak")
        fig = plt.Figure(figsize=(10, 2.4), dpi=92, facecolor="#0f172a")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#0f172a")
        xs: list[float] = []
        dd_pct: list[float] = []
        cumulative = 0
        peak = None
        for sim in sessions:
            hist = getattr(sim, "balance_history", None) or []
            if not hist:
                continue
            for entry in hist:
                bal = float(entry.get("balance", 0))
                r = int(entry.get("round", 0))
                xs.append(cumulative + r)
                if peak is None or bal > peak:
                    peak = bal
                pct = ((bal - peak) / peak * 100) if peak and peak > 0 else 0
                dd_pct.append(pct)
            cumulative += max((h.get("round", 0) for h in hist), default=0)
        if xs:
            ax.fill_between(xs, dd_pct, 0, color="#ef4444", alpha=0.35,
                            edgecolor="#dc2626", linewidth=0.8)
            ax.set_xlabel("Campaign round", color="#cbd5e1", fontsize=9)
            ax.set_ylabel("Drawdown (%)", color="#cbd5e1", fontsize=9)
            ax.tick_params(colors="#94a3b8", labelsize=8)
            for spine in ax.spines.values():
                spine.set_edgecolor("#334155")
            ax.grid(True, linestyle=":", alpha=0.25, color="#475569")
            worst = min(dd_pct) if dd_pct else 0
            ax.set_title(f"Max DD: {worst:.1f}%", color="#facc15", fontsize=10)
        fig.tight_layout()
        self._embed_mpl(body, fig)

    def _build_analytics_group_panel(self, host, group_pnl, row):
        """Per-color / per-parity / per-dozen win count + bot PnL when each
        member of the group landed. Answers 'did one dozen kill me?'"""
        body = self._make_panel_card(host, row=row, col=0,
                                     title="Outcomes by group (when the wheel landed on…)")
        # Compact text grid since 3 groups × 3-4 members fits cleanly.
        for g_key, label in [("color", "Color"), ("parity", "Parity"),
                              ("dozen", "Dozen")]:
            cell_label = ctk.CTkLabel(
                body, text=label, font=("Segoe UI", 11, "bold"),
                text_color="#facc15", anchor="w")
            cell_label.pack(anchor="w", padx=4, pady=(6, 0))
            data = group_pnl.get(g_key, {})
            total_hits = sum(d.get("n", 0) for d in data.values()) or 1
            row_text = []
            for member, d in data.items():
                n = d.get("n", 0)
                pnl = d.get("pnl", 0.0)
                pct = (n / total_hits) * 100
                color = "#22c55e" if pnl >= 0 else "#ef4444"
                row_text.append((f"  {member:<8}", f"{n:>4} hits ({pct:>5.1f}%)",
                                 f"${pnl:>+8.2f}", color))
            for txt_l, txt_m, txt_r, color in row_text:
                line = ctk.CTkFrame(body, fg_color="transparent")
                line.pack(fill="x", padx=4)
                ctk.CTkLabel(line, text=txt_l, font=("Consolas", 11),
                             text_color="#cbd5e1", width=80, anchor="w").pack(side="left")
                ctk.CTkLabel(line, text=txt_m, font=("Consolas", 11),
                             text_color="#94a3b8", width=160, anchor="w").pack(side="left")
                ctk.CTkLabel(line, text=txt_r, font=("Consolas", 11, "bold"),
                             text_color=color, anchor="e").pack(side="left", padx=(8, 0))

    def _build_analytics_spins_panel(self, host, spin_counts, spin_pnl, row):
        """Hot/cold spin numbers. Top-5 by hit count and bottom-5 (cold) +
        the 5 numbers where the bot bled the most when they landed."""
        body = self._make_panel_card(host, row=row, col=1,
                                     title="Spin numbers — hot/cold + most painful")
        if not spin_counts:
            ctk.CTkLabel(body, text="(no spins recorded)",
                         text_color="#64748b").pack(padx=4, pady=4)
            return
        # Hottest
        hot = sorted(spin_counts.items(), key=lambda kv: -kv[1])[:6]
        # Coldest (only among numbers that did appear; truly absent numbers
        # are uninformative without an expected-frequency calc)
        cold = sorted(spin_counts.items(), key=lambda kv: kv[1])[:6]
        # Most expensive — sum-pnl when this number landed
        worst_pnl = sorted(spin_pnl.items(), key=lambda kv: kv[1])[:6]

        def _row(title, items, color, fmt):
            ctk.CTkLabel(body, text=title, font=("Segoe UI", 11, "bold"),
                         text_color=color, anchor="w").pack(anchor="w", padx=4, pady=(6, 0))
            line = ctk.CTkFrame(body, fg_color="transparent")
            line.pack(fill="x", padx=4)
            txt = "   ".join(fmt(n, v) for n, v in items)
            ctk.CTkLabel(line, text=txt, font=("Consolas", 11),
                         text_color="#cbd5e1", anchor="w",
                         justify="left", wraplength=560).pack(anchor="w")

        _row("Hottest (most-frequent landings)", hot, "#facc15",
             lambda n, v: f"{n:>2}:{v}")
        _row("Coldest (least-frequent landings)", cold, "#0ea5e9",
             lambda n, v: f"{n:>2}:{v}")
        _row("Most painful (worst PnL when landed)", worst_pnl, "#ef4444",
             lambda n, v: f"{n:>2}: ${v:+.2f}")

    def _build_analytics_worst_best_tables(self, host, round_lookup, row):
        """Two side-by-side tables of the worst-10 and best-10 single rounds.
        Each row clickable → jumps to that row in the Detailed Log."""
        for col, (title, sorter, accent) in enumerate([
            ("Worst 10 rounds (biggest losses)",
             lambda x: x[2], "#ef4444"),
            ("Best 10 rounds (biggest wins)",
             lambda x: -x[2], "#22c55e"),
        ]):
            body = self._make_panel_card(host, row=row, col=col, title=title)
            cols = ("sess", "round", "spin", "pnl", "result")
            tree = ttk.Treeview(body, columns=cols, show="headings",
                                style="Spinedge.Treeview", height=10,
                                selectmode="browse")
            for c, w, a, t in zip(cols, (50, 60, 50, 90, 80),
                                  ("center", "center", "center", "e", "center"),
                                  ("Sess", "Round", "Spin", "PnL ($)", "Result")):
                tree.heading(c, text=t)
                tree.column(c, width=w, anchor=a, stretch=False)
            tree.column("result", stretch=True)
            for s, r, pnl, spin, result, _bal in sorted(round_lookup, key=sorter)[:10]:
                tree.insert("", "end", iid=f"{s}:{r}",
                            values=(s, r, spin, f"{pnl:+.2f}", result))
            tree.pack(fill="both", expand=True)

            # Double-click or Enter → jump in Detailed Log
            def _jump(_e=None, _t=tree):
                sel = _t.selection()
                if not sel:
                    return
                try:
                    s_str, r_str = sel[0].split(":", 1)
                    self._jump_to_detail_row(int(s_str), int(r_str))
                except Exception:
                    pass
            tree.bind("<Double-1>", _jump)
            tree.bind("<Return>", _jump)
            ctk.CTkLabel(body, text="double-click a row to jump",
                         font=("Segoe UI", 10, "italic"),
                         text_color="#64748b").pack(anchor="w", pady=(4, 0))

    def _build_analytics_parallel_panel(self, host, contribution, row):
        """Per-strategy attribution table for parallel-mode rounds. Each
        column comes straight from the per-strategy bet record so PnL is
        exact, not an equal-share approximation."""
        body = self._make_panel_card(host, row=row, col=0, colspan=2,
                                     title="Parallel-mode: per-strategy contribution")
        cols = ("strat", "rounds", "wins", "losses", "winrate",
                "wagered", "pnl", "roi")
        tree = ttk.Treeview(body, columns=cols, show="headings",
                            style="Spinedge.Treeview", height=10,
                            selectmode="browse")
        for c, w, a, t in zip(cols,
                              (240, 70, 60, 60, 75, 100, 100, 70),
                              ("w", "center", "center", "center", "e", "e", "e", "e"),
                              ("Strategy", "Rounds", "Wins", "Losses",
                               "Win Rate", "Wagered ($)", "PnL ($)", "ROI%")):
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor=a, stretch=(c == "strat"))
        for name, d in sorted(contribution.items(), key=lambda kv: -kv[1]["pnl"]):
            wr = (d["wins"] / max(1, d["wins"] + d["losses"])) * 100
            wagered = d.get("wagered", 0.0)
            roi = (d["pnl"] / wagered * 100) if wagered > 0 else 0.0
            tag = "win" if d["pnl"] >= 0 else "loss"
            tree.insert("", "end",
                        values=(name, d["rounds"], d["wins"], d["losses"],
                                f"{wr:.1f}%",
                                f"{wagered:.2f}",
                                f"{d['pnl']:+.2f}",
                                f"{roi:+.2f}%"),
                        tags=(tag,))
        tree.tag_configure("win", background="#0f1d17", foreground="#86efac")
        tree.tag_configure("loss", background="#1d0f0f", foreground="#fca5a5")
        tree.pack(fill="both", expand=True)
        ctk.CTkLabel(
            body,
            text="Per-strategy PnL is read directly from each strategy's own "
                 "result in the parallel record — exact attribution, not "
                 "approximation. Sort by PnL to see which strategy carried "
                 "the bundle and which dragged it down.",
            font=("Segoe UI", 10, "italic"), text_color="#64748b",
            wraplength=900, justify="left").pack(anchor="w", pady=(6, 0))

    # ── Persistence: every run auto-saves to ~/.spinedge/backtest_runs/ ──
    # Keeps the full config + per-session BacktestResult fields + bet_history
    # so the GUI can re-render Summary/Detailed/Graph/Round Audit later.
    _RUNS_DIR = os.path.join(os.path.expanduser("~"), ".spinedge", "backtest_runs")

    def _runs_dir(self) -> str:
        try:
            os.makedirs(self._RUNS_DIR, exist_ok=True)
        except Exception:
            pass
        return self._RUNS_DIR

    # Where the Bundle Backtest dialog persists its last-used inputs
    # (bundle name + sims + init_bal + sim_mode + data source + db_limit
    # + spins_per_min). One small JSON file so reopens / restarts feel
    # like nothing was lost.
    _BUNDLE_DIALOG_STATE_PATH = os.path.join(
        os.path.expanduser("~"), ".spinedge", "backtest_bundle_dialog_state.json"
    )

    def _save_bundle_dialog_state(self, state: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self._BUNDLE_DIALOG_STATE_PATH), exist_ok=True)
            with open(self._BUNDLE_DIALOG_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[BacktestGUI] save bundle dialog state failed: {e}")

    def _load_bundle_dialog_state(self) -> dict:
        try:
            with open(self._BUNDLE_DIALOG_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            print(f"[BacktestGUI] load bundle dialog state failed: {e}")
            return {}

    def _serialize_results_for_save(self, strategy_name: str) -> dict:
        """Build a JSON-safe snapshot of self.results[strategy_name].
        Includes audit fields so Recent Runs replays show the exact reason
        each session ended + the effective base_bet / max_loss in effect."""
        sessions = self.results.get(strategy_name, []) or []
        out = []
        for sim in sessions:
            out.append({
                "initial_balance":   getattr(sim, "initial_balance", 0.0),
                "final_balance":     getattr(sim, "final_balance", 0.0),
                "total_rounds":      getattr(sim, "total_rounds", 0),
                "total_wins":        getattr(sim, "total_wins", 0),
                "total_losses":      getattr(sim, "total_losses", 0),
                "max_drawdown":      getattr(sim, "max_drawdown", 0.0),
                "max_consecutive_wins":   getattr(sim, "max_consecutive_wins", 0),
                "max_consecutive_losses": getattr(sim, "max_consecutive_losses", 0),
                # bet_history is the per-round data the Round Audit modal reads
                "bet_history":       list(getattr(sim, "bet_history", []) or []),
                "balance_history":   list(getattr(sim, "balance_history", []) or []),
                # Audit fields — explain HOW this session ended and what
                # escalated base_bet/max_loss it was running with.
                "stop_reason":        getattr(sim, "stop_reason", "") or "",
                "stop_message":       getattr(sim, "stop_message", "") or "",
                "effective_base_bet": float(getattr(sim, "effective_base_bet", 0.0) or 0.0),
                "effective_max_loss": float(getattr(sim, "effective_max_loss", 0.0) or 0.0),
                "escalation_step":    int(getattr(sim, "escalation_step", 0) or 0),
            })
        return {
            "strategy_name": strategy_name,
            "sessions":      out,
        }

    def _save_run_to_disk(self, strategy_name: str, cfg: dict, label: str = "") -> str | None:
        """Persist the just-finished run. Returns the saved file path."""
        try:
            payload = {
                "saved_at":     datetime.now().isoformat(timespec="seconds"),
                "label":        label or strategy_name,
                "config":       cfg or {},
                "results":      self._serialize_results_for_save(strategy_name),
                "analysis":     self.analysis.get(strategy_name, {}) or {},
                "campaign":     None,
            }
            camp = getattr(self, "_last_campaign", None)
            if camp is not None and hasattr(camp, "to_dict"):
                try:
                    payload["campaign"] = camp.to_dict()
                except Exception:
                    pass

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_label = "".join(c for c in (label or strategy_name)
                                 if c.isalnum() or c in ("-", "_"))[:48] or "run"
            path = os.path.join(self._runs_dir(), f"{ts}_{safe_label}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            # Prune old runs: each saved file carries full per-round bet_history,
            # so an unbounded directory bloats disk AND slows _load_recent_runs
            # (it scans + parses every file). Keep only the most-recent N.
            try:
                self._prune_runs_dir(keep=50)
            except Exception:
                pass
            # Refresh the dropdown so the new run appears at the top
            try:
                self._refresh_recent_runs_dropdown(select_path=path)
            except Exception:
                pass
            return path
        except Exception as e:
            print(f"[BacktestGUI] auto-save failed: {e}")
            return None

    def _prune_runs_dir(self, keep: int = 50) -> None:
        """Delete all but the `keep` most-recent saved-run JSON files so the
        directory (each file holds full per-round bet_history) stays bounded."""
        try:
            files = []
            for fn in os.listdir(self._runs_dir()):
                if fn.endswith(".json"):
                    fp = os.path.join(self._runs_dir(), fn)
                    try:
                        files.append((os.path.getmtime(fp), fp))
                    except OSError:
                        continue
            files.sort(reverse=True)  # newest first
            for _, fp in files[keep:]:
                try:
                    os.remove(fp)
                except OSError:
                    pass
        except Exception:
            pass

    def _load_recent_runs(self, limit: int = 30) -> list[dict]:
        """Return up to `limit` most-recent run metadata dicts."""
        out = []
        try:
            files = []
            for fn in os.listdir(self._runs_dir()):
                if fn.endswith(".json"):
                    fp = os.path.join(self._runs_dir(), fn)
                    try:
                        files.append((os.path.getmtime(fp), fp, fn))
                    except OSError:
                        continue
            files.sort(reverse=True)
            for _, fp, fn in files[:limit]:
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    sess = (meta.get("results") or {}).get("sessions") or []
                    pnl_total = sum((s.get("final_balance", 0.0) - s.get("initial_balance", 0.0))
                                    for s in sess)
                    out.append({
                        "path":      fp,
                        "filename":  fn,
                        "saved_at":  meta.get("saved_at", "?"),
                        "label":     meta.get("label", "?"),
                        "sessions":  len(sess),
                        "pnl_total": pnl_total,
                    })
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def _refresh_recent_runs_dropdown(self, select_path: str | None = None):
        """Repopulate the recent-runs combobox. Cheap to call — runs disk scan."""
        runs = self._load_recent_runs(limit=30)
        # Build display labels with PnL + timestamp + session count
        self._recent_runs_index = {}      # display string → path
        labels = []
        for r in runs:
            pnl = r["pnl_total"]
            short = r["saved_at"].replace("T", " ")[:16]
            disp = f"{short}  ·  {r['label'][:32]:<32}  ·  {r['sessions']}s  ·  ${pnl:+,.2f}"
            self._recent_runs_index[disp] = r["path"]
            labels.append(disp)
        if not labels:
            labels = ["(no saved runs yet)"]
        try:
            self._recent_runs_combo.configure(values=labels)
        except Exception:
            return
        # Choose what to display in the box
        if select_path:
            for disp, p in self._recent_runs_index.items():
                if p == select_path:
                    self._recent_run_var.set(disp)
                    break
        elif self._recent_run_var.get() not in labels:
            self._recent_run_var.set(labels[0])
        # Update the hint label
        try:
            n = len(self._recent_runs_index)
            if n == 0:
                self._recent_runs_hint.configure(
                    text="(no saved runs yet — finish a backtest to populate this)",
                    text_color="#64748b",
                )
            else:
                self._recent_runs_hint.configure(text=f"({n} saved)", text_color="#94a3b8")
        except Exception:
            pass

    def _on_recent_run_selected(self, display_name: str):
        """User picked a run from the dropdown — load it back into Summary /
        Detailed / Graph / Round Audit. Doesn't re-run the backtest."""
        path = (getattr(self, "_recent_runs_index", {}) or {}).get(display_name)
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            messagebox.showerror("Load Run", f"Couldn't read run file:\n{e}")
            return

        # Hydrate BacktestResult-like objects from the saved dicts. The GUI
        # only needs attribute access on these — full BacktestResult fidelity
        # isn't required to re-render results.
        from types import SimpleNamespace
        results_block = payload.get("results") or {}
        strat = results_block.get("strategy_name") or payload.get("label") or "loaded_run"
        sessions = []
        for s in results_block.get("sessions", []):
            sessions.append(SimpleNamespace(
                initial_balance=float(s.get("initial_balance", 0.0)),
                final_balance=float(s.get("final_balance", 0.0)),
                total_rounds=int(s.get("total_rounds", 0)),
                total_wins=int(s.get("total_wins", 0)),
                total_losses=int(s.get("total_losses", 0)),
                max_drawdown=float(s.get("max_drawdown", 0.0)),
                max_consecutive_wins=int(s.get("max_consecutive_wins", 0)),
                max_consecutive_losses=int(s.get("max_consecutive_losses", 0)),
                bet_history=list(s.get("bet_history") or []),
                balance_history=list(s.get("balance_history") or []),
                # Audit fields (back-compat: pre-audit runs default these to ""/0)
                stop_reason=s.get("stop_reason", "") or "",
                stop_message=s.get("stop_message", "") or "",
                effective_base_bet=float(s.get("effective_base_bet", 0.0) or 0.0),
                effective_max_loss=float(s.get("effective_max_loss", 0.0) or 0.0),
                escalation_step=int(s.get("escalation_step", 0) or 0),
            ))

        self.results = {strat: sessions}
        self.analysis = {strat: dict(payload.get("analysis") or {})}
        # Synthetic CampaignResult so _display_summary's campaign branch renders
        camp_dict = payload.get("campaign") or {}
        if camp_dict:
            self._last_campaign = SimpleNamespace(
                sessions_run=int(camp_dict.get("sessions_run", len(sessions))),
                total_rounds=int(camp_dict.get("total_rounds", 0)),
                initial_balance=float(camp_dict.get("initial_balance", 0.0)),
                final_balance=float(camp_dict.get("final_balance", 0.0)),
                campaign_pnl=float(camp_dict.get("campaign_pnl", 0.0)),
                stop_reason=str(camp_dict.get("stop_reason", "?")),
                escalation_log=list(camp_dict.get("escalation_log") or []),
                final_escalation_step=int(camp_dict.get("final_escalation_step", 0)),
            )
        else:
            self._last_campaign = None
        self._last_runner_config = dict(payload.get("config") or {})

        # Re-render
        try:
            self._display_summary(strat)
        except Exception as e:
            print(f"[BacktestGUI] re-render failed: {e}")
        try:
            self._plot_results()
        except Exception:
            pass
        try:
            self.save_button.configure(state="normal")
            self.export_button.configure(state="normal")
            self.export_csv_button.configure(state="normal")
        except Exception:
            pass
        self._set_status(f"📂 Loaded run: {os.path.basename(path)}", color="#22d3ee")

    def _open_runs_folder(self):
        """Open the saved-runs directory in the OS file explorer."""
        try:
            import subprocess, sys as _sys
            path = self._runs_dir()
            if _sys.platform.startswith("win"):
                os.startfile(path)
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showinfo("Open Folder", f"Path: {self._runs_dir()}\n\n({e})")

    # ── Live status bar ──────────────────────────────────────────────────
    def _set_status(self, text: str, color: str = "#94a3b8"):
        try:
            self._status_label.configure(text=text, text_color=color)
        except Exception:
            pass

    def _set_status_pnl(self, text: str, positive: bool = True):
        try:
            color = "#22c55e" if positive else "#ef4444"
            if not text:
                color = "#facc15"
            self._status_pnl_label.configure(text=text, text_color=color)
        except Exception:
            pass

    def _begin_status_run(self, total_sessions: int, label: str = "Backtest"):
        import time as _time
        self._status_run_start_time = _time.time()
        self._status_current_session = 0
        self._status_total_sessions = max(1, int(total_sessions))
        self._status_running_pnl = 0.0
        self._set_status(f"▶ {label} — preparing…", color="#22d3ee")
        self._set_status_pnl("PnL: $0.00", positive=True)

    def _end_status_run(self, final_pnl: float, ok: bool = True):
        import time as _time
        elapsed = (_time.time() - (self._status_run_start_time or _time.time()))
        msg = (f"✓ Done — {self._status_total_sessions} sessions in "
               f"{int(elapsed//60)}m {int(elapsed%60)}s" if ok else "✗ Failed")
        color = "#22c55e" if ok else "#ef4444"
        self._set_status(msg, color=color)
        self._set_status_pnl(f"PnL: ${final_pnl:+,.2f}", positive=(final_pnl >= 0))
        self._status_run_start_time = None

    def _update_status_from_log(self, line: str):
        """Parse runner emissions into status bar updates.
        The runner emits lines like:
          "Session 5: PnL=$+12.40, Bal=$112.40, Global=$+62.40"
          "Sim 3: PnL=$+2.10, rounds=100, W=44, L=56, MaxDD=$8.20"
        We pull session#, running PnL, ETA from those.
        """
        import re, time as _time
        if not line or self._status_run_start_time is None:
            return
        m_seq = re.search(r"Session\s+(\d+)\s*:\s*PnL=\$([+\-]?[0-9.]+).*Global=\$([+\-]?[0-9.]+)", line)
        m_ind = re.search(r"Sim\s+(\d+)\s*:\s*PnL=\$([+\-]?[0-9.]+)", line)
        if m_seq:
            sess = int(m_seq.group(1))
            global_pnl = float(m_seq.group(3))
            self._status_current_session = sess
            self._status_running_pnl = global_pnl
        elif m_ind:
            sess = int(m_ind.group(1))
            sim_pnl = float(m_ind.group(2))
            self._status_current_session = sess
            self._status_running_pnl += sim_pnl
        else:
            return
        elapsed = _time.time() - self._status_run_start_time
        frac = self._status_current_session / max(1, self._status_total_sessions)
        eta_str = "?"
        if frac > 0 and self._status_current_session < self._status_total_sessions:
            eta = elapsed / frac - elapsed
            eta_str = f"{int(eta//60)}m {int(eta%60)}s"
        self._set_status(
            f"▶ Session {self._status_current_session}/{self._status_total_sessions} "
            f"· elapsed {int(elapsed//60)}m {int(elapsed%60)}s · ETA {eta_str}",
            color="#22d3ee",
        )
        self._set_status_pnl(
            f"PnL: ${self._status_running_pnl:+,.2f}",
            positive=(self._status_running_pnl >= 0),
        )

    def _display_summary(self, strategy_name):
        if strategy_name not in self.analysis:
            return

        # Defensive: always reset before writing. run_backtest clears the
        # box before spawning the worker, but if anything has written to it
        # in the meantime (e.g. a stale callback from a previous run, or a
        # log message that misrouted here) the next run's summary would be
        # appended below the old text. Clear unconditionally.
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")

        stats = self.analysis[strategy_name]
        summary = f"Strategy: {strategy_name}\n"
        summary += f"Simulations: {stats['num_simulations']}\n"
        summary += f"\n--- Financials ---\n"
        summary += f"Avg PnL: ${stats['avg_pnl']:.2f}\n"
        summary += f"Total PnL (All Sims): ${stats['total_pnl_all_sims']:.2f}\n"
        summary += f"ROI: {stats['roi_pct']:.2f}%\n"
        summary += f"\n--- Risks ---\n"
        summary += f"Max Drawdown: ${stats['max_drawdown']:.2f}\n"
        summary += f"Bankruptcies: {stats['bankruptcies']} ({stats['bankruptcy_rate']:.1f}%)\n"
        summary += f"\n--- Gameplay ---\n"
        summary += f"Win Rate: {stats['win_rate']:.2f}%\n"
        summary += f"Avg Rounds: {stats['avg_rounds']}\n"

        # Include campaign-level totals from the runner so users see what
        # actually happened across all sessions (sequential mode bundles
        # sessions together; analyse_results aggregates them differently).
        camp = getattr(self, "_last_campaign", None)
        if camp is not None:
            summary += f"\n--- Campaign ---\n"
            summary += f"Sessions run: {camp.sessions_run}\n"
            summary += f"Total rounds:  {camp.total_rounds}\n"
            summary += f"Initial:       ${camp.initial_balance:,.2f}\n"
            summary += f"Final:         ${camp.final_balance:,.2f}\n"
            summary += f"Campaign PnL:  ${camp.campaign_pnl:+,.2f}\n"
            summary += f"Stop reason:   {camp.stop_reason}\n"
            if camp.escalation_log:
                summary += f"Escalation events: {len(camp.escalation_log)} "
                summary += f"(final step {camp.final_escalation_step})\n"

        self.summary_text.insert("end", summary)

        # Detailed Log Population
        self.detailed_text.configure(state="normal")
        self.detailed_text.delete("1.0", "end")

        # Remember which strategy is currently displayed so _open_round_audit_modal
        # knows which bet_history to look up when the user clicks "Show Board".
        self._displayed_strategy_name = strategy_name

        # Populate the Treeview with all per-round data (clicking a row opens
        # the Round Audit modal). The streaming-log textbox below is now only
        # for in-progress runner output, not a place to dump the table.
        try:
            self._populate_detail_tree()
        except Exception as e:
            print(f"[BacktestGUI] _populate_detail_tree failed: {e}")
        # Refresh the Pro Metrics tab too — single source of derived stats.
        try:
            self._populate_metrics_panel(strategy_name)
        except Exception as e:
            print(f"[BacktestGUI] _populate_metrics_panel failed: {e}")
        # Refresh the Analytics tab (histograms, group breakdowns, hot/cold
        # numbers, top winners/losers, parallel attribution).
        try:
            self._populate_analytics_tab(strategy_name)
        except Exception as e:
            print(f"[BacktestGUI] _populate_analytics_tab failed: {e}")
        # Refresh the Session Audit tab.
        try:
            self._populate_session_audit(strategy_name)
        except Exception as e:
            print(f"[BacktestGUI] _populate_session_audit failed: {e}")
        # Keep the textbox in sync as a fallback view, but only if the user
        # has opted into the streaming log pane.
        if strategy_name in self.results and self.results[strategy_name]:
            sessions = self.results[strategy_name]

            # Header — columns:
            #   Sess#  = which session this row belongs to (1-indexed)
            #   Round  = round number WITHIN that session
            #   Bet    = total stake placed THIS round (sum across all labels)
            #            ← this is what most users mean by "bet" and what
            #              actually moves with the strategy phase
            #   Chip   = per-chip unit (i.e. base_bet for flat, or the next
            #            bet from the progression for martingale/etc.)
            #   Lbls   = how many board positions the strategy bet on this round
            header = (f"{'Sess#':<5} | {'Round':<6} | {'Strategy':<22} | {'Spin':<6} | "
                      f"{'Bet':>9} | {'Chip':>7} | {'Lbls':>5} | {'Res':<4} | "
                      f"{'Payout':>9} | {'PnL':>9} | {'Balance':>11}\n")
            divider = "-" * 118 + "\n"
            self.detailed_text.insert("end", header)
            self.detailed_text.insert("end", divider)

            total_rows = 0
            # Massive runs (e.g. 1000 sessions × 200 rounds = 200k rows) would
            # freeze the textbox. Cap the visible log at MAX_ROWS and tell the
            # user the rest is in the saved results / round audit modal.
            MAX_ROWS = 5000
            truncated = False
            for sess_idx, sim in enumerate(sessions, start=1):
                history = getattr(sim, 'bet_history', None) or []
                if not history:
                    continue
                # Session separator with audit metadata so the user sees the
                # escalated base_bet / SL that THIS session ran with and what
                # ended the PREVIOUS session.
                if sess_idx > 1:
                    prev = sessions[sess_idx - 2]
                    prev_reason = getattr(prev, 'stop_reason', '') or 'N/A'
                    prev_msg = getattr(prev, 'stop_message', '') or ''
                    sep = (f"{'─' * 6} prev session {sess_idx - 1} ended: {prev_reason} "
                           f"({prev_msg[:60]}) {'─' * 6}\n")
                    self.detailed_text.insert("end", sep)
                bb = getattr(sim, 'effective_base_bet', 0.0) or 0.0
                ml = getattr(sim, 'effective_max_loss', 0.0) or 0.0
                es = getattr(sim, 'escalation_step', 0) or 0
                esc_tag = f", esc step {es}" if es > 0 else ""
                self.detailed_text.insert(
                    "end",
                    f"━━━ Session {sess_idx} starts: bal=${sim.initial_balance:.2f}, "
                    f"base_bet=${bb:.2f}, SL=${ml:.2f}{esc_tag} ━━━\n"
                )
                for record in history:
                    if total_rows >= MAX_ROWS:
                        truncated = True
                        break
                    r = record.get('round', 0)
                    s = record.get('strategy', strategy_name)[:22]
                    spin = str(record.get('spin_result', '-'))
                    chip = record.get('bet_amount', 0.0)
                    bet = record.get('total_bet', chip)
                    labels = len(record.get('bets', []) or []) or 1
                    res = record.get('result', '-')
                    payout = record.get('payout', 0.0)
                    pnl = record.get('pnl', 0.0)
                    bal = record.get('balance_after', 0.0)
                    line = (f"{sess_idx:<5} | {r:<6} | {s:<22} | {spin:<6} | "
                            f"${bet:>8.2f} | ${chip:>6.2f} | {labels:>5} | {res:<4} | "
                            f"${payout:>8.2f} | ${pnl:>+8.2f} | ${bal:>10.2f}\n")
                    self.detailed_text.insert("end", line)
                    total_rows += 1
                if truncated:
                    break

            # Footer
            total_rounds_all = sum(getattr(s, 'total_rounds', 0) for s in sessions)
            footer = f"\nShowed {total_rows} rows across {len(sessions)} session(s) (total rounds: {total_rounds_all})."
            if truncated:
                footer += (f"\n⚠ Log truncated at {MAX_ROWS} rows. Full data is in the saved "
                           f"results JSON — and the Round Audit modal works for any round.")
            footer += "\nUse the Round Audit button below to inspect chip placement for any round.\n"
            self.detailed_text.insert("end", footer)
        else:
            self.detailed_text.insert("end", "No detailed history available.")

        self.detailed_text.configure(state="disabled") # Make read-only

    def _open_round_audit_modal(self):
        """Open a modal with RouletteBoardCanvas showing chip placements for a
        specific round of the currently-displayed strategy. Honors the
        Session# input so users can audit any round across a multi-session
        sequential campaign (not just session 1)."""
        strat = getattr(self, "_displayed_strategy_name", None)
        if not strat or strat not in self.results or not self.results[strat]:
            messagebox.showinfo("Round Audit",
                                "No detailed log loaded. Run a backtest and pick a strategy first.")
            return
        try:
            round_num = int(self._audit_round_var.get() or "1")
        except ValueError:
            messagebox.showerror("Round Audit", "Round must be an integer.")
            return
        try:
            session_num = int(getattr(self, "_audit_session_var",
                                      tk.StringVar(value="1")).get() or "1")
        except ValueError:
            messagebox.showerror("Round Audit", "Session # must be an integer.")
            return

        sessions = self.results[strat]
        if session_num < 1 or session_num > len(sessions):
            messagebox.showinfo("Round Audit",
                                f"Session {session_num} not found. Campaign has {len(sessions)} session(s).")
            return
        sim = sessions[session_num - 1]
        record = next((r for r in sim.bet_history if r.get("round") == round_num), None)
        if record is None:
            avail = [r.get("round") for r in sim.bet_history] if sim.bet_history else []
            rng = f"{avail[0]}..{avail[-1]}" if avail else "<empty>"
            messagebox.showinfo("Round Audit",
                                f"Round {round_num} not found in session {session_num}. "
                                f"This session covers rounds {rng}.")
            return
        if not record.get("bets"):
            messagebox.showinfo("Round Audit",
                                "This round has no per-label bet breakdown. Re-run the backtest — older runs predate the per-label upgrade.")
            return

        try:
            from gui.round_audit import RouletteBoardCanvas
        except Exception as e:
            messagebox.showerror("Round Audit", f"Board view unavailable: {e}")
            return
        from types import SimpleNamespace

        dialog = ctk.CTkToplevel(self.parent_frame)
        dialog.title(f"Session {session_num}, Round {round_num} — Board")
        dialog.transient(self.parent_frame)
        dialog.grab_set()
        dialog.configure(fg_color="#09090b")

        # Summary header
        pnl = record.get("pnl", 0.0)
        pnl_color = "#10b981" if pnl >= 0 else "#ef4444"
        header_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        header_frame.pack(fill="x", padx=12, pady=(12, 4))
        # For parallel rounds the 'strategy' field is the joined name
        # (stratA+stratB+stratC). Use a clearer header in that case.
        ps_list = record.get("parallel_strategies") or []
        if ps_list:
            disp_strat = f"🎯 PARALLEL ({len(ps_list)} strategies)"
            title_color = "#5eead4"
        else:
            disp_strat = strat
            title_color = "#facc15"
        ctk.CTkLabel(header_frame,
                     text=f"Strategy: {disp_strat}   |   Session {session_num}   |   Round {round_num}",
                     font=("Segoe UI", 12, "bold"),
                     text_color=title_color).pack(side="left")
        stats = (f"Spin: {record.get('spin_result', '?')}   "
                 f"Total Bet: ${record.get('total_bet', 0.0):.2f}   "
                 f"Payout: ${record.get('payout', 0.0):.2f}   "
                 f"Net: ${pnl:+.2f}")
        ctk.CTkLabel(header_frame, text=stats, font=("Segoe UI", 12),
                     text_color=pnl_color).pack(side="left", padx=(12, 0))

        # Parallel-round breakdown — show each contributing strategy's bet,
        # win/loss, and P&L so the user can see exactly who carried the round
        # vs who dragged it. Sits between the header and the board so it's
        # the first thing you scan after the spin number.
        if ps_list:
            ps_frame = ctk.CTkFrame(dialog, fg_color="#0f172a", corner_radius=8)
            ps_frame.pack(fill="x", padx=12, pady=(0, 6))
            ctk.CTkLabel(ps_frame,
                         text="🎯 Per-strategy breakdown (each strategy advances its own progression)",
                         font=("Segoe UI", 11, "bold"),
                         text_color="#5eead4").pack(anchor="w", padx=10, pady=(8, 2))
            for ps in ps_list:
                row = ctk.CTkFrame(ps_frame, fg_color="transparent")
                row.pack(fill="x", padx=10, pady=1)
                ps_pnl = float(ps.get('pnl', 0.0) or 0.0)
                ps_color = "#10b981" if ps_pnl > 0 else ("#ef4444" if ps_pnl < 0 else "#94a3b8")
                ctk.CTkLabel(row, text=f"{ps.get('name','?'):<40}",
                             font=("Consolas", 11), text_color="#cbd5e1",
                             anchor="w").pack(side="left")
                ctk.CTkLabel(row, text=f"{ps.get('result','?'):<5}",
                             font=("Consolas", 11, "bold"),
                             text_color=ps_color, width=50).pack(side="left")
                ctk.CTkLabel(row, text=f"bet=${ps.get('total_bet',0.0):>6.2f}",
                             font=("Consolas", 11), text_color="#94a3b8",
                             width=110).pack(side="left")
                ctk.CTkLabel(row, text=f"pnl=${ps_pnl:>+7.2f}",
                             font=("Consolas", 11, "bold"),
                             text_color=ps_color, width=110).pack(side="left")
                lbls = ", ".join(b.get('label','?') for b in (ps.get('bets') or []))
                ctk.CTkLabel(row, text=f"on: {lbls[:60]}",
                             font=("Consolas", 10), text_color="#64748b",
                             anchor="w").pack(side="left", padx=(8, 0))
            # Small spacer
            ctk.CTkLabel(ps_frame, text="", height=2).pack()

        # Board — RouletteBoardCanvas.render_record reads .bets and .winning_number
        rec_ns = SimpleNamespace(
            bets=record.get("bets", []),
            winning_number=record.get("spin_result"),
        )
        board = RouletteBoardCanvas(dialog, fg_color="#0f172a")
        board.pack(padx=10, pady=10)
        try:
            unplaced = board.render_record(rec_ns)
        except Exception as e:
            unplaced = []
            ctk.CTkLabel(dialog, text=f"Board render error: {e}",
                         text_color="#ef4444").pack(padx=10)

        # Show any labels we couldn't position on the board (splits/corners
        # with non-standard names usually). Won/lost is annotated.
        if unplaced:
            ctk.CTkLabel(dialog, text="Unplaced chips:",
                         font=("Segoe UI", 10, "bold"),
                         text_color="#94a3b8").pack(padx=12, pady=(4, 0), anchor="w")
            ctk.CTkLabel(dialog, text="\n".join(unplaced),
                         font=("Consolas", 11), text_color="#cbd5e1",
                         justify="left").pack(padx=12, pady=(0, 6), anchor="w")

        # Round-by-round navigation so the user can scrub without closing the dialog
        nav_row = ctk.CTkFrame(dialog, fg_color="transparent")
        nav_row.pack(fill="x", padx=12, pady=(0, 12))

        def _go(delta):
            try:
                cur = int(self._audit_round_var.get() or "1")
            except ValueError:
                cur = 1
            self._audit_round_var.set(str(max(1, cur + delta)))
            dialog.destroy()
            self._open_round_audit_modal()

        ctk.CTkButton(nav_row, text="◀ Prev", width=80,
                      command=lambda: _go(-1)).pack(side="left")
        ctk.CTkButton(nav_row, text="Next ▶", width=80,
                      command=lambda: _go(1)).pack(side="left", padx=(6, 0))
        ctk.CTkButton(nav_row, text="Close", width=100, fg_color="#334155",
                      hover_color="#475569",
                      command=dialog.destroy).pack(side="right")

    # ── Bundle Backtest ──────────────────────────────────────────────────────
    # Loads a bundle JSON and runs it through the same run_campaign pipeline
    # the live bot uses for its config. All bundle fields apply verbatim —
    # rotation, dynamic_rules, every risk limit. The dialog shows the derived
    # campaign config in a preview pane so the user can see exactly what
    # will run before kicking it off.

    def _open_bundle_backtest_dialog(self):
        import os
        import glob
        import json as _json
        from core.backtesting_runner import bundle_to_campaign_config

        bundles_dir = os.path.join(os.path.expanduser("~"), ".spinedge", "bundles")
        local_bundles = []
        if os.path.isdir(bundles_dir):
            local_bundles = sorted(
                os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(os.path.join(bundles_dir, "*.json"))
            )

        dialog = ctk.CTkToplevel(self.parent_frame)
        dialog.title("Backtest Bundle")
        dialog.transient(self.parent_frame)
        dialog.grab_set()
        dialog.geometry("720x680")
        dialog.configure(fg_color="#09090b")

        ctk.CTkLabel(dialog, text="📦  Backtest a Bundle",
                     font=("Roboto", 16, "bold"),
                     text_color="#22d3ee").pack(padx=12, pady=(12, 4), anchor="w")
        ctk.CTkLabel(dialog,
                     text=("Picks up the bundle's full config (rotation, dynamic rules, "
                           "all risk limits) and runs it through the same campaign "
                           "pipeline as the live bot."),
                     font=("Segoe UI", 12), text_color="#94a3b8",
                     wraplength=680, justify="left").pack(padx=12, pady=(0, 8), anchor="w")

        # Bundle source row
        src_frame = ctk.CTkFrame(dialog, fg_color="#0f172a")
        src_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(src_frame, text="Bundle:", font=("Segoe UI", 11, "bold")).pack(side="left", padx=(10, 6), pady=8)

        bundle_var = tk.StringVar(value=local_bundles[0] if local_bundles else "")
        bundle_dropdown = ctk.CTkComboBox(src_frame, variable=bundle_var,
                                          values=local_bundles or ["(no local bundles)"],
                                          width=320, state="readonly")
        bundle_dropdown.pack(side="left", padx=4, pady=8)
        if not local_bundles:
            bundle_dropdown.configure(state="disabled")
        else:
            # Type-to-search the local bundle list (prefix/substring/initials).
            self._bt_bundle_master = list(local_bundles)
            self._wire_searchable_combo(bundle_dropdown, "_bt_bundle_master")

        # External-file picker — for .spine/.json bundles not in ~/.spinedge/bundles
        ext_path_var = tk.StringVar(value="")
        def _pick_external():
            path = filedialog.askopenfilename(
                title="Pick a bundle JSON",
                filetypes=[("Bundle JSON", "*.json"), ("All", "*.*")],
                initialdir=bundles_dir if os.path.isdir(bundles_dir) else os.path.expanduser("~"),
            )
            if path:
                ext_path_var.set(path)
                bundle_var.set(os.path.splitext(os.path.basename(path))[0])
                _refresh_preview()
        ctk.CTkButton(src_frame, text="…file", width=70,
                      command=_pick_external).pack(side="left", padx=(2, 10), pady=8)

        # Backtest knobs (rounds/sims/initial_balance/sim_mode) — the bundle
        # doesn't sensibly define these for a virtual sim.
        knobs = ctk.CTkFrame(dialog, fg_color="#0f172a")
        knobs.pack(fill="x", padx=12, pady=4)
        for c in range(4):
            knobs.grid_columnconfigure(c, weight=1)

        def _row(parent, r, label, var, width=90, tooltip=None):
            ctk.CTkLabel(parent, text=label, font=("Segoe UI", 12),
                         text_color="#cbd5e1").grid(row=r, column=0, sticky="w", padx=(10, 4), pady=4)
            ent = ctk.CTkEntry(parent, textvariable=var, width=width)
            ent.grid(row=r, column=1, sticky="w", pady=4)
            return ent

        # Note: "Rounds per session" is intentionally NOT an input here.
        # The bundle's own session_duration (minutes) drives session length —
        # asking the user to override would mean ignoring something the bundle
        # already specifies. Users can tune the spins-per-minute conversion
        # below if they want denser/sparser per-session round counts; the
        # final rounds-per-session value is shown in the preview pane.
        sims_var         = tk.StringVar(value="")          # filled from bundle's num_sessions
        init_bal_var     = tk.StringVar(value="100")
        db_limit_var     = tk.StringVar(value="5000")
        sim_mode_var     = tk.StringVar(value="sequential")
        data_source_var  = tk.StringVar(value="db")
        # spins_per_min default = 30 (one spin every 2 seconds) so a typical
        # bundle's session_duration=1min produces 30+ rounds — enough for
        # stop_loss / escalation logic to actually exercise. The previous
        # default (1.5, mirroring real-time online roulette) produced 2-round
        # sessions where stop_loss never had room to fire, making bundles
        # look "broken" in backtest. User can drop it back to 1.5 for
        # real-time pacing if they want a faithful live mirror.
        spins_per_min_var = tk.StringVar(value="30")
        # Minimum rounds per session — backstop so a 1-min bundle session at
        # the live rate (1.5 spins/min = 1.5 rounds) doesn't trickle past
        # risk controls without exercising them. Default 100 preserves the
        # old behavior; set to 0 to honor `session_duration × spins_per_min`
        # exactly with no floor (faithful bundle replay).
        min_rounds_floor_var = tk.StringVar(value="100")
        # Computed from bundle's session_duration × spins_per_min — displayed
        # read-only so the user sees what the backtest will actually use.
        rounds_var       = tk.StringVar(value="—")
        derived_rounds_lbl_var = tk.StringVar(
            value="(pick a bundle to derive rounds from session_duration)")

        # Row 0 — session length info from bundle
        ctk.CTkLabel(knobs, text="Session length:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").grid(
                         row=0, column=0, sticky="w", padx=(10, 4), pady=4)
        ctk.CTkLabel(knobs, textvariable=derived_rounds_lbl_var,
                     font=("Consolas", 12), text_color="#22d3ee",
                     anchor="w").grid(
                         row=0, column=1, columnspan=3, sticky="ew", pady=4)

        # Row 1 — sims + initial balance
        ctk.CTkLabel(knobs, text="Number of sessions:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").grid(
                         row=1, column=0, sticky="w", padx=(10, 4), pady=4)
        ctk.CTkEntry(knobs, textvariable=sims_var, width=90).grid(
            row=1, column=1, sticky="w", pady=4)
        ctk.CTkLabel(knobs, text="Initial balance ($):",
                     font=("Segoe UI", 12), text_color="#cbd5e1").grid(
                         row=1, column=2, sticky="w", padx=(20, 4), pady=4)
        ctk.CTkEntry(knobs, textvariable=init_bal_var, width=90).grid(
            row=1, column=3, sticky="w", pady=4)

        # Row 2 — sim mode + spins/min tuner
        ctk.CTkLabel(knobs, text="Sim mode:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=2, column=0, sticky="w",
                                                padx=(10, 4), pady=4)
        ctk.CTkComboBox(knobs, variable=sim_mode_var,
                        values=["sequential", "independent"],
                        state="readonly", width=120).grid(
                            row=2, column=1, sticky="w", pady=4)
        ctk.CTkLabel(knobs, text="Spins per minute:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").grid(
                         row=2, column=2, sticky="w", padx=(20, 4), pady=4)
        ctk.CTkEntry(knobs, textvariable=spins_per_min_var, width=90).grid(
            row=2, column=3, sticky="w", pady=4)

        # Min rounds floor — user-controllable. Default 100 keeps the old
        # 'sessions long enough for risk controls to fire' behavior. Set to
        # 0 to disable the floor and run the bundle's session_duration
        # faithfully (e.g. 1 min × 1.5 spins/min = 1.5 rounds, true live mirror).
        ctk.CTkLabel(knobs, text="Min rounds floor:",
                     font=("Segoe UI", 12), text_color="#cbd5e1").grid(
                         row=2, column=4, sticky="w", padx=(20, 4), pady=4)
        ctk.CTkEntry(knobs, textvariable=min_rounds_floor_var, width=70).grid(
            row=2, column=5, sticky="w", pady=4)
        ctk.CTkLabel(knobs, text="(0 = no floor)",
                     font=("Segoe UI", 10, "italic"),
                     text_color="#64748b").grid(
                         row=2, column=6, sticky="w", pady=4)

        # Row 3 — data source + DB limit
        ctk.CTkLabel(knobs, text="Data source:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=3, column=0, sticky="w",
                                                padx=(10, 4), pady=4)
        ctk.CTkComboBox(knobs, variable=data_source_var,
                        values=["db", "generated"],
                        state="readonly", width=120).grid(
                            row=3, column=1, sticky="w", pady=4)
        ctk.CTkLabel(knobs, text="Use latest K spins:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=3, column=2, sticky="w",
                                                padx=(20, 4), pady=4)
        ctk.CTkEntry(knobs, textvariable=db_limit_var, width=90).grid(
            row=3, column=3, sticky="w", pady=4)

        # Row 4 — Skip-latest offset + Preview button + Lock toggle
        ctk.CTkLabel(knobs, text="Skip latest:", font=("Segoe UI", 12),
                     text_color="#cbd5e1").grid(row=4, column=0, sticky="w",
                                                padx=(10, 4), pady=4)
        db_offset_var = tk.StringVar(value="0")
        ctk.CTkEntry(knobs, textvariable=db_offset_var, width=90).grid(
            row=4, column=1, sticky="w", pady=4)

        # Lock-slice state — scoped to this dialog so the main page's lock
        # doesn't get confused with bundle-dialog locks (they capture
        # different snapshots).
        bundle_lock_var = tk.BooleanVar(value=False)
        bundle_locked_anchor = {"id": None}

        # Status label fed by the Preview button. Independent from the main
        # page's _db_slice_status — this one belongs to the bundle dialog.
        bundle_slice_status = ctk.CTkLabel(
            knobs,
            text="ℹ Click Preview Slice to see the exact DB window this run will use. "
                 "Tick 🔒 Lock to freeze the snapshot so re-runs use the SAME rows.",
            font=("Segoe UI", 11, "italic"), text_color="#94a3b8",
            anchor="w", justify="left", wraplength=720,
        )
        bundle_slice_status.grid(row=5, column=0, columnspan=4,
                                 sticky="ew", padx=10, pady=(2, 6))

        def _resolve_bundle_anchor():
            if not bundle_lock_var.get():
                return None
            if bundle_locked_anchor["id"] is None:
                from core.utils.db_utils import get_max_winning_number_id
                try:
                    bundle_locked_anchor["id"] = get_max_winning_number_id()
                except Exception:
                    bundle_locked_anchor["id"] = None
            return bundle_locked_anchor["id"]

        def _bundle_preview_slice():
            """Show the exact DB window this bundle run will replay."""
            if data_source_var.get() != "db":
                bundle_slice_status.configure(
                    text="ℹ Data source is 'generated' — DB preview only "
                         "applies when data source is 'db'.",
                    text_color="#94a3b8")
                return
            try:
                limit = max(1, int(db_limit_var.get() or "5000"))
                offset = max(0, int(db_offset_var.get() or "0"))
                anchor = _resolve_bundle_anchor()
                total = self._db_total_count()
                sliced = self._resolve_db_slice(limit, offset, anchor_id=anchor)
                text, color = self._format_slice_summary(
                    sliced, total, limit, offset, anchor_id=anchor)
                bundle_slice_status.configure(text=text, text_color=color)
            except Exception as e:
                bundle_slice_status.configure(
                    text=f"⚠ DB preview failed: {e}", text_color="#ef4444")

        def _on_bundle_lock_toggle():
            if bundle_lock_var.get():
                from core.utils.db_utils import get_max_winning_number_id
                try:
                    bundle_locked_anchor["id"] = get_max_winning_number_id()
                except Exception:
                    bundle_locked_anchor["id"] = None
            else:
                bundle_locked_anchor["id"] = None
            _bundle_preview_slice()

        ctk.CTkButton(
            knobs, text="🔍 Preview Slice", width=130, height=28,
            command=_bundle_preview_slice,
            fg_color="#0ea5e9", hover_color="#0284c7",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=4, column=2, sticky="w", padx=(20, 4), pady=4)
        ctk.CTkCheckBox(
            knobs, text="🔒 Lock slice", variable=bundle_lock_var,
            command=_on_bundle_lock_toggle, fg_color="#a855f7",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=4, column=3, sticky="w", pady=4)

        # Preview pane — derived campaign config (read-only)
        ctk.CTkLabel(dialog, text="Derived campaign config (what will actually run):",
                     font=("Segoe UI", 10, "bold"),
                     text_color="#cbd5e1").pack(padx=12, pady=(10, 2), anchor="w")
        preview = ctk.CTkTextbox(dialog, height=230, font=("Consolas", 11))
        preview.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        def _resolve_bundle_path():
            """Path of the bundle currently chosen. External pick beats dropdown."""
            ext = ext_path_var.get().strip()
            if ext:
                return ext
            name = (bundle_var.get() or "").strip()
            if not name or name == "(no local bundles)":
                return None
            return os.path.join(bundles_dir, f"{name}.json")

        def _load_bundle():
            path = _resolve_bundle_path()
            if not path or not os.path.isfile(path):
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return _json.load(f)
            except Exception as e:
                preview.configure(state="normal")
                preview.delete("1.0", "end")
                preview.insert("end", f"⚠ Failed to load bundle:\n{e}")
                preview.configure(state="disabled")
                return None

        # Track which bundle was last loaded so we can auto-fill rounds/sims
        # from the bundle's session_duration/num_sessions when the user picks
        # a fresh bundle. Don't overwrite values the user has manually edited.
        _last_loaded_bundle_id = {"id": None}

        # Default floor — overrideable per-dialog via min_rounds_floor_var.
        # Kept at 100 for backwards-compat with prior backtest results.
        DEFAULT_MIN_ROUNDS = 100

        def _recompute_derived_rounds():
            """Update the read-only derived-rounds display from the currently
            loaded bundle's session_duration × the spins/min tuner. The user-
            controlled `min_rounds_floor_var` clamps the result; setting it
            to 0 disables the floor entirely (faithful bundle replay)."""
            bundle = _load_bundle()
            if bundle is None:
                derived_rounds_lbl_var.set(
                    "(pick a bundle to derive rounds from session_duration)")
                rounds_var.set("—")
                return
            try:
                bc = bundle.get("betting_config", {}) or {}
                sess_min = float(bc.get("session_duration", 0) or 0)
                try:
                    spm = float(spins_per_min_var.get() or "30")
                    if spm <= 0:
                        spm = 30
                except ValueError:
                    spm = 30
                try:
                    user_floor = max(0, int(float(min_rounds_floor_var.get() or "0")))
                except ValueError:
                    user_floor = DEFAULT_MIN_ROUNDS
                if sess_min > 0:
                    raw = max(1, int(round(sess_min * spm)))
                    rounds = max(raw, user_floor) if user_floor > 0 else raw
                    rounds_var.set(str(rounds))
                    if user_floor <= 0 or rounds == raw:
                        # No floor applied — show the faithful derivation.
                        floor_note = " · floor=off" if user_floor <= 0 else ""
                        derived_rounds_lbl_var.set(
                            f"{sess_min:g} min × {spm:g} spins/min = {rounds} rounds per session  "
                            f"(from bundle's session_duration{floor_note})")
                    else:
                        derived_rounds_lbl_var.set(
                            f"{sess_min:g} min × {spm:g} spins/min = {raw} → floored to "
                            f"{rounds} (Min rounds floor; set to 0 to honor bundle's "
                            f"{raw} faithfully)")
                else:
                    floor = user_floor or DEFAULT_MIN_ROUNDS
                    rounds_var.set(str(floor))
                    derived_rounds_lbl_var.set(
                        f"bundle has no session_duration — using {floor} rounds per session")
            except Exception as _e:
                derived_rounds_lbl_var.set(f"(derivation failed: {_e})")

        def _autofill_from_bundle(bundle):
            """Suggest reasonable defaults from the bundle ONLY for fields the
            user hasn't touched. After any user edit, that field stays put
            even when they pick a different bundle. Prevents the "I changed
            sims to 5 and it keeps snapping back to 50" frustration."""
            try:
                bid = bundle.get("bundle_id") or bundle.get("name") or "?"
                same_bundle = (_last_loaded_bundle_id["id"] == bid)
                _last_loaded_bundle_id["id"] = bid
                bc = bundle.get("betting_config", {}) or {}
                # Only suggest sims if the user hasn't edited that field yet.
                # First-ever load (sims_var empty) → fill from bundle's
                # num_sessions. Subsequent loads → leave alone.
                if not _user_edited["sims"]:
                    num_sess = int(bc.get("num_sessions", 0) or 0)
                    if num_sess > 0:
                        # cap at 50 so a bundle that ships with num_sessions=10000
                        # doesn't trigger a 5-hour backtest by default
                        sims_var.set(str(min(num_sess, 50)))
                    elif not sims_var.get():
                        sims_var.set("20")
                # Re-derive rounds whenever the bundle changes (rounds is a
                # bundle-derived display, not a user input — see the dialog
                # note explaining session_duration drives it).
                if not same_bundle:
                    _recompute_derived_rounds()
            except Exception:
                pass

        # Flags toggled the first time the user edits each input — used by
        # _autofill_from_bundle to know what to leave alone. Initialized False
        # so the FIRST bundle pick still populates sensible defaults.
        _user_edited = {
            "sims": False, "init_bal": False, "spins_per_min": False,
            "sim_mode": False, "data_source": False, "db_limit": False,
            "min_rounds_floor": False,
        }
        def _mark_edited(key):
            def _h(*_a):
                _user_edited[key] = True
            return _h
        sims_var.trace_add("write", _mark_edited("sims"))
        init_bal_var.trace_add("write", _mark_edited("init_bal"))
        spins_per_min_var.trace_add("write", _mark_edited("spins_per_min"))
        sim_mode_var.trace_add("write", _mark_edited("sim_mode"))
        data_source_var.trace_add("write", _mark_edited("data_source"))
        db_limit_var.trace_add("write", _mark_edited("db_limit"))

        # Recompute the derived-rounds display whenever the spins/min tuner
        # OR the Min rounds floor changes — gives instant feedback without
        # re-picking the bundle.
        spins_per_min_var.trace_add("write", lambda *_a: _recompute_derived_rounds())
        min_rounds_floor_var.trace_add("write", lambda *_a: _recompute_derived_rounds())
        min_rounds_floor_var.trace_add("write", _mark_edited("min_rounds_floor"))

        def _build_cfg_from_inputs():
            bundle = _load_bundle()
            if bundle is None:
                return None, None
            _autofill_from_bundle(bundle)
            try:
                # Rounds is now derived from the bundle's session_duration ×
                # spins/min — not user-entered. If derivation failed (no
                # session_duration in bundle), fall back to 100 like the
                # display label says.
                rounds_str = rounds_var.get()
                rounds = max(1, int(rounds_str)) if rounds_str and rounds_str != "—" else 100
                sims = max(1, int(sims_var.get() or "20"))
                init_bal = max(0.01, float(init_bal_var.get() or "100"))
                db_limit = max(1, int(db_limit_var.get() or "5000"))
                spm = float(spins_per_min_var.get() or "1.5")
                if spm <= 0:
                    spm = 1.5
            except ValueError as e:
                preview.configure(state="normal")
                preview.delete("1.0", "end")
                preview.insert("end", f"⚠ Bad numeric input: {e}")
                preview.configure(state="disabled")
                return None, None

            cfg = bundle_to_campaign_config(
                bundle,
                initial_balance=init_bal,
                rounds=rounds,
                sims=sims,
                sim_mode=sim_mode_var.get(),
                custom_strategies=self._fetch_custom_strategies(),
                historical_data_source=data_source_var.get(),
                db_limit=db_limit,
                spins_per_minute=spm,
            )
            # The bundle_to_campaign_config helper doesn't accept db_offset
            # or db_anchor_id directly — inject them here so the runner's
            # skip-latest + slice-lock paths (backtesting_runner.py:245+)
            # pick them up.
            try:
                cfg["db_offset"] = max(0, int(db_offset_var.get() or "0"))
            except (ValueError, TypeError):
                cfg["db_offset"] = 0
            cfg["db_anchor_id"] = _resolve_bundle_anchor()
            return cfg, bundle

        def _refresh_preview(*_args):
            cfg, bundle = _build_cfg_from_inputs()
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            if cfg is None:
                preview.insert("end", "(pick a bundle to preview)")
                preview.configure(state="disabled")
                return
            meta = (bundle.get("meta") or {})
            head = (
                f"Bundle: {meta.get('name', bundle.get('name', '?'))}\n"
                f"  Version : {meta.get('version', '?')}\n"
                f"  Created : {meta.get('created_at', '?')}\n\n"
            )
            preview.insert("end", head)
            keys = [
                "strategy_name", "progression_type", "base_bet", "max_bet",
                "max_loss", "profit_target", "enable_trailing_stop",
                "trailing_stop_amount", "max_consec_losses",
                "max_session_wins_streak", "max_session_losses_streak",
                "session_ext_after_win", "session_ext_at_high",
                "max_extension_rounds", "extension_give_up_amount",
                "enable_global_limits", "global_profit_target", "global_stop_loss",
                "rounds", "sims", "sim_mode", "historical_data_source", "db_limit",
                "initial_balance",
            ]
            for k in keys:
                v = cfg.get(k)
                preview.insert("end", f"  {k:<30}: {v}\n")
            rot = cfg.get("rotation_config")
            if rot:
                sel_mode = (rot.get("selection_mode") or "rotation")
                preview.insert("end", f"\n  rotation_config.mode : {rot.get('mode')}\n")
                # selection_mode + trigger config makes it obvious whether
                # parallel/conditional features are actually wired up before
                # running. Without these, users were running parallel bundles
                # without realizing the preview showed nothing different.
                mode_tag = ""
                if sel_mode == "parallel":
                    mode_tag = "  ← PARALLEL (every armed strategy bets together)"
                elif sel_mode == "conditional":
                    mode_tag = "  ← CONDITIONAL (tiebreaker picks one)"
                preview.insert("end", f"  selection_mode       : {sel_mode}{mode_tag}\n")
                if sel_mode != "rotation":
                    gt = rot.get("global_trigger")
                    preview.insert("end", f"  global_trigger       : {gt}\n")
                    trg = rot.get("triggers") or {}
                    if trg:
                        preview.insert("end", f"  per-strategy triggers ({len(trg)}):\n")
                        for k, v in trg.items():
                            preview.insert("end", f"     • {k}: {v}\n")
                    preview.insert("end", f"  tiebreaker           : {rot.get('tiebreaker')}\n")
                    preview.insert("end", f"  fallback             : {rot.get('fallback')}\n")
                preview.insert("end", f"  rotation_config.strategies ({len(rot.get('strategies', []))}):\n")
                for s in rot.get("strategies", []):
                    preview.insert("end", f"     • {s}\n")
            dr = cfg.get("dynamic_rules") or []
            if dr:
                preview.insert("end", f"\n  dynamic_rules ({len(dr)}):\n")
                for r in dr:
                    preview.insert("end", f"     • {r}\n")
            preview.configure(state="disabled")

        # Refresh preview on any input change. rounds_var is derived from
        # the bundle so it shouldn't trigger a refresh itself — but it does
        # change when the user adjusts spins/min, and we want the preview
        # to update for that. Listen to rounds_var too; it's idempotent.
        bundle_var.trace_add("write", _refresh_preview)
        for v in (rounds_var, sims_var, init_bal_var, db_limit_var,
                  sim_mode_var, data_source_var, spins_per_min_var):
            v.trace_add("write", _refresh_preview)
        _refresh_preview()

        # Action row
        action_row = ctk.CTkFrame(dialog, fg_color="transparent")
        action_row.pack(fill="x", padx=12, pady=(4, 12))

        def _snapshot_state() -> dict:
            """Grab the dialog's current values for persistence."""
            return {
                "bundle":           bundle_var.get(),
                "sims":             sims_var.get(),
                "init_bal":         init_bal_var.get(),
                "db_limit":         db_limit_var.get(),
                "sim_mode":         sim_mode_var.get(),
                "data_source":      data_source_var.get(),
                "spins_per_min":    spins_per_min_var.get(),
                "min_rounds_floor": min_rounds_floor_var.get(),
            }

        def _persist_state():
            try:
                self._save_bundle_dialog_state(_snapshot_state())
            except Exception:
                pass

        def _close_and_persist():
            """Close the dialog AFTER snapshotting state. Used by the Close
            button and the WM_DELETE_WINDOW protocol so edits-without-run
            still survive across opens (which was the user-reported bug)."""
            _persist_state()
            try:
                dialog.destroy()
            except Exception:
                pass

        def _run():
            cfg, bundle = _build_cfg_from_inputs()
            if cfg is None or bundle is None:
                messagebox.showerror("Bundle Backtest",
                                     "Pick a bundle first (and verify numeric inputs).")
                return
            # Persist whatever the user just confirmed so reopening the dialog
            # later (or after a bot restart) brings the same values back.
            _persist_state()
            dialog.destroy()
            self._run_bundle_backtest_thread(cfg, bundle)

        # X button on the window frame goes through the same persist path
        dialog.protocol("WM_DELETE_WINDOW", _close_and_persist)

        def _reset_to_bundle_defaults():
            """Wipe all user overrides so the next preview pull re-derives
            everything from the bundle. Useful when the user has tweaked
            many fields and just wants the bundle's intent back."""
            for k in _user_edited:
                _user_edited[k] = False
            _last_loaded_bundle_id["id"] = None
            spins_per_min_var.set("1.5")
            sim_mode_var.set("sequential")
            data_source_var.set("db")
            db_limit_var.set("5000")
            init_bal_var.set("100")
            min_rounds_floor_var.set("100")
            sims_var.set("")  # autofill will repopulate from bundle
            _refresh_preview()

        # Restore previously-saved state — survives dialog closes + bot restarts.
        # Done LAST so it overrides the initial trace-fired autofills.
        try:
            saved = self._load_bundle_dialog_state() or {}
            if saved:
                if saved.get("bundle") in (local_bundles or []):
                    bundle_var.set(saved["bundle"])
                for k, var in (("sims", sims_var), ("init_bal", init_bal_var),
                               ("db_limit", db_limit_var), ("sim_mode", sim_mode_var),
                               ("data_source", data_source_var),
                               ("spins_per_min", spins_per_min_var),
                               ("min_rounds_floor", min_rounds_floor_var)):
                    if saved.get(k) not in (None, ""):
                        var.set(str(saved[k]))
                        _user_edited[k] = True
        except Exception:
            pass

        ctk.CTkButton(action_row, text="▶  Run Bundle Backtest", height=36,
                      font=("Roboto", 12, "bold"),
                      fg_color="#0e7490", hover_color="#0891b2",
                      command=_run).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(action_row, text="↺ Reset to bundle defaults",
                      width=200, height=36,
                      fg_color="#475569", hover_color="#64748b",
                      command=_reset_to_bundle_defaults).pack(side="left", padx=(0, 6))
        ctk.CTkButton(action_row, text="Close", width=120, height=36,
                      fg_color="#334155", hover_color="#475569",
                      command=_close_and_persist).pack(side="right")

    def _fetch_custom_strategies(self):
        """Try to surface the live app's custom_strategies registry so bundle
        strategy names resolve. Falls back to empty dict if we're standalone."""
        try:
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "custom_strategies"):
                return dict(app.custom_strategies or {})
        except Exception:
            pass
        return {}

    def _run_bundle_backtest_thread(self, cfg, bundle):
        """Spawn the campaign in a worker thread so the GUI stays responsive."""
        import threading

        if self.run_button.cget("text") != "🚀 Run Backtest":
            messagebox.showwarning("Busy", "A backtest is already running.")
            return

        self.run_button.configure(state="disabled")
        self.bundle_button.configure(state="disabled")
        self.progress_bar.set(0)
        self.summary_text.delete("1.0", "end")
        self.detailed_text.delete("1.0", "end")
        for widget in self.graph_frame.winfo_children():
            widget.destroy()

        strategy_name = cfg["strategy_name"]
        self.results = {strategy_name: []}
        self._last_runner_config = dict(cfg)
        try:
            self._save_last_config()
        except Exception:
            pass

        def _ui_log(msg):
            self.parent_frame.after(0, lambda m=msg: self._log(m))
        def _ui_progress(p):
            self.parent_frame.after(0, lambda v=p: self.progress_bar.set(v))

        bundle_name = (bundle.get("meta") or {}).get("name") or bundle.get("name") or "<unnamed>"
        self.parent_frame.after(
            0,
            lambda total=int(cfg.get("sims", 1)),
                   label=f"Bundle: {bundle_name}": self._begin_status_run(total, label),
        )

        def _worker():
            try:
                from core.backtesting_runner import run_campaign
                _ui_log(f"📦 Backtesting bundle '{bundle_name}'")
                campaign = run_campaign(cfg, on_log=_ui_log, on_progress=_ui_progress)
                # Store sessions under the strategy name for display
                self.results[strategy_name] = campaign.sessions
                self._last_campaign = campaign
                from core.backtesting import RouletteBacktester
                self.analysis = RouletteBacktester().analyze_results({strategy_name: campaign.sessions})
                self.parent_frame.after(0, lambda: self._display_summary(strategy_name))
                self.parent_frame.after(0, self._plot_results)
                self.parent_frame.after(0, lambda: self.save_button.configure(state="normal"))
                self.parent_frame.after(0, lambda: self.export_button.configure(state="normal"))
                self.parent_frame.after(0, lambda: self.export_csv_button.configure(state="normal"))
                # Auto-save under a bundle-prefixed label so the Recent Runs
                # dropdown clearly differentiates bundle vs manual runs.
                self.parent_frame.after(
                    0,
                    lambda n=strategy_name, c=dict(cfg), lbl=f"bundle_{bundle_name}":
                        self._save_run_to_disk(n, c, label=lbl),
                )
                self.parent_frame.after(
                    0,
                    lambda p=campaign.campaign_pnl: self._end_status_run(p, ok=True),
                )
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                self.parent_frame.after(0, lambda: messagebox.showerror(
                    "Bundle Backtest Failed", f"{e}\n\n{tb}"))
                self.parent_frame.after(0, lambda: self._end_status_run(0.0, ok=False))
            finally:
                self.parent_frame.after(0, lambda: self.run_button.configure(state="normal"))
                self.parent_frame.after(0, lambda: self.bundle_button.configure(state="normal"))

        threading.Thread(target=_worker, daemon=True).start()

    def save_results(self):
        """Save backtesting results to file"""
        if not self.results:
            messagebox.showwarning("No Results", "No backtesting results to save.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save Backtesting Results"
        )

        if filename:
            try:
                saved_file = self.backtester.save_results(self.results, filename)
                messagebox.showinfo("Success", f"Results saved to {saved_file}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save results: {str(e)}")

    # ── Parameter sweep ──────────────────────────────────────────────────────

    _SWEEP_PARAMS = [
        "max_consec_losses", "base_bet", "max_loss", "profit_target",
        "max_session_losses_streak", "max_session_wins_streak",
        "escalation_multiplier", "escalation_max_steps",
        "trailing_stop_amount", "rounds", "sims",
        "escalation_per_step",      # CSV value
    ]

    def _open_sweep_dialog(self):
        """Open a modal-ish dialog to run a parameter sweep over the current
        Backtesting tab config. Same engine as backtest_sweep.py — the
        Cartesian product of two param lists is run through run_campaign
        and ranked by campaign PnL."""
        import threading
        import itertools
        from core.backtesting_runner import run_campaign, validate_config

        try:
            base_cfg = self._build_runner_config()
            base_cfg.pop("_rotation_used", None)
        except Exception as exc:
            messagebox.showerror("Sweep", f"Could not capture base config: {exc}")
            return

        dlg = ctk.CTkToplevel(self.parent_frame.winfo_toplevel())
        dlg.title("🔬 Parameter Sweep")
        dlg.geometry("980x640")
        dlg.transient(self.parent_frame.winfo_toplevel())

        # Header
        header = ctk.CTkFrame(dlg, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(header, text="🔬  Parameter Sweep",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#facc15").pack(side="left")
        ctk.CTkLabel(header,
                     text="Pick a parameter and values to try. Each combination runs through the same engine the main backtest uses.",
                     font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(side="left", padx=10)

        # Two parameter rows (param 2 optional — empty values disables it)
        form = ctk.CTkFrame(dlg, fg_color="#0f172a", corner_radius=8)
        form.pack(fill="x", padx=14, pady=6)

        def _param_row(label, default_param, default_values):
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=6)
            ctk.CTkLabel(row, text=label, width=130, anchor="w",
                         font=ctk.CTkFont(size=11),
                         text_color="#cbd5e1").pack(side="left")
            param_var = tk.StringVar(value=default_param)
            ctk.CTkComboBox(row, variable=param_var, values=self._SWEEP_PARAMS,
                            width=220, height=28,
                            font=ctk.CTkFont(size=10)).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text="values (CSV):", width=100, anchor="w",
                         text_color="#94a3b8",
                         font=ctk.CTkFont(size=10)).pack(side="left")
            values_var = tk.StringVar(value=default_values)
            ctk.CTkEntry(row, textvariable=values_var, width=380,
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=4)
            return param_var, values_var

        p1_var, v1_var = _param_row("Parameter 1:", "max_consec_losses", "5,10,15,20,25")
        p2_var, v2_var = _param_row("Parameter 2:", "base_bet", "")   # blank = single-param

        # Sort + actions row
        actions = ctk.CTkFrame(dlg, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(2, 6))
        ctk.CTkLabel(actions, text="Sort by:", text_color="#94a3b8",
                     font=ctk.CTkFont(size=10)).pack(side="left")
        sort_var = tk.StringVar(value="campaign_pnl")
        ctk.CTkComboBox(actions, variable=sort_var, width=160, height=26,
                        values=["campaign_pnl", "final_balance", "max_drawdown_min"],
                        font=ctk.CTkFont(size=10)).pack(side="left", padx=(4, 14))

        run_btn = ctk.CTkButton(actions, text="▶ Run Sweep", width=120, height=28,
                                fg_color="#1d4ed8", hover_color="#2563eb",
                                font=ctk.CTkFont(size=11, weight="bold"))
        run_btn.pack(side="left")
        export_btn = ctk.CTkButton(actions, text="📋 Export CSV", width=120, height=28,
                                   fg_color="#374151", hover_color="#4b5563",
                                   font=ctk.CTkFont(size=11), state="disabled")
        export_btn.pack(side="left", padx=(8, 0))
        status_var = tk.StringVar(value="ready.")
        ctk.CTkLabel(actions, textvariable=status_var, text_color="#94a3b8",
                     font=ctk.CTkFont(size=10)).pack(side="right", padx=8)

        # Leaderboard table
        table_frame = ctk.CTkFrame(dlg, fg_color="#0b1220", corner_radius=8)
        table_frame.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        try:
            _style = ttk.Style()
            _style.configure("Sweep.Treeview",
                             background="#0b1220", fieldbackground="#0b1220",
                             foreground="#e5e7eb", rowheight=22, borderwidth=0)
            _style.configure("Sweep.Treeview.Heading",
                             background="#1f2937", foreground="#cbd5e1",
                             font=("Segoe UI", 9, "bold"))
            _style.map("Sweep.Treeview",
                       background=[("selected", "#1d4ed8")],
                       foreground=[("selected", "white")])
        except Exception:
            pass

        cols = ("rank", "pnl", "final", "sessions", "rounds", "stop", "p1", "p2")
        tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                            style="Sweep.Treeview", height=18)
        for c, lbl, w, anchor in [
            ("rank", "#",       40, "center"),
            ("pnl", "PnL $",    100, "e"),
            ("final", "Final $", 110, "e"),
            ("sessions", "Sess", 60, "center"),
            ("rounds", "Rounds", 70, "center"),
            ("stop", "Stop",    140, "w"),
            ("p1", "Param 1",   140, "w"),
            ("p2", "Param 2",   140, "w"),
        ]:
            tree.heading(c, text=lbl)
            tree.column(c, width=w, anchor=anchor)
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sb.pack(side="right", fill="y")

        sweep_results: list = []  # list of (overrides, CampaignResult)

        def _parse_values(raw: str, key: str):
            if not raw or not raw.strip():
                return []
            if key == "escalation_per_step":
                # whole RHS is one CSV list value
                return [raw.strip()]
            out = []
            for tok in raw.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    out.append(int(tok))
                except ValueError:
                    try:
                        out.append(float(tok))
                    except ValueError:
                        out.append(tok)
            return out

        def _populate(items):
            for iid in tree.get_children():
                tree.delete(iid)
            for rank, (ov, res) in enumerate(items, 1):
                p1_val = ov.get(p1_var.get(), "")
                p2_val = ov.get(p2_var.get(), "")
                tree.insert(
                    "", "end",
                    values=(rank,
                            f"{res.campaign_pnl:+.2f}",
                            f"{res.final_balance:.2f}",
                            res.sessions_run,
                            res.total_rounds,
                            res.stop_reason,
                            f"{p1_var.get()}={p1_val}" if p1_val != "" else "",
                            f"{p2_var.get()}={p2_val}" if p2_val != "" else ""),
                )

        def _sort_results():
            sb_key = sort_var.get()
            if sb_key == "campaign_pnl":
                sweep_results.sort(key=lambda x: -x[1].campaign_pnl)
            elif sb_key == "final_balance":
                sweep_results.sort(key=lambda x: -x[1].final_balance)
            else:  # max_drawdown_min
                def _dd(item):
                    res = item[1]
                    if res.sessions:
                        return max(s.max_drawdown for s in res.sessions)
                    return 0
                sweep_results.sort(key=_dd)
            _populate(sweep_results)

        def _on_sort_change(_e=None):
            if sweep_results:
                _sort_results()
        # Re-sort on every sort_var change
        sort_var.trace_add("write", lambda *a: _on_sort_change())

        def _run_in_thread():
            run_btn.configure(state="disabled", text="Running…")
            export_btn.configure(state="disabled")
            sweep_results.clear()

            k1 = p1_var.get().strip()
            v1 = _parse_values(v1_var.get(), k1)
            k2 = p2_var.get().strip()
            v2 = _parse_values(v2_var.get(), k2) if v2_var.get().strip() else []

            if not v1:
                messagebox.showwarning("Sweep", "Parameter 1 needs at least one value.")
                run_btn.configure(state="normal", text="▶ Run Sweep")
                return

            if v2:
                combos = list(itertools.product(v1, v2))
            else:
                combos = [(v,) for v in v1]

            total = len(combos)
            status_var.set(f"0 / {total} …")

            def worker():
                for i, combo in enumerate(combos, 1):
                    overrides = {k1: combo[0]}
                    if v2:
                        overrides[k2] = combo[1]
                    try:
                        cfg = dict(base_cfg)
                        cfg.update(overrides)
                        res = run_campaign(validate_config(cfg), on_log=lambda _m: None)
                        sweep_results.append((overrides, res))
                    except Exception as exc:
                        print(f"[Sweep] Error on combo {overrides}: {exc}")
                    # Marshal UI update onto main thread
                    self.parent_frame.after(
                        0, lambda done=i, t=total: status_var.set(f"{done} / {t} …"),
                    )
                # Final UI update
                def _done():
                    _sort_results()
                    status_var.set(f"done — {len(sweep_results)} / {total} runs.")
                    run_btn.configure(state="normal", text="▶ Run Sweep")
                    if sweep_results:
                        export_btn.configure(state="normal")
                self.parent_frame.after(0, _done)

            threading.Thread(target=worker, daemon=True).start()

        run_btn.configure(command=_run_in_thread)

        def _do_export():
            if not sweep_results:
                return
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                initialfile="sweep_results.csv",
                filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
                title="Export sweep results",
            )
            if not path:
                return
            try:
                import csv as _csv
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = _csv.writer(f)
                    w.writerow(["rank", "campaign_pnl", "final_balance", "sessions_run",
                                "total_rounds", "stop_reason", p1_var.get(),
                                p2_var.get() if v2_var.get().strip() else ""])
                    for rank, (ov, res) in enumerate(sweep_results, 1):
                        w.writerow([
                            rank,
                            f"{res.campaign_pnl:.4f}",
                            f"{res.final_balance:.4f}",
                            res.sessions_run,
                            res.total_rounds,
                            res.stop_reason,
                            ov.get(p1_var.get(), ""),
                            ov.get(p2_var.get(), "") if v2_var.get().strip() else "",
                        ])
                messagebox.showinfo("Export CSV", f"Saved to {path}")
            except Exception as exc:
                messagebox.showerror("Export CSV", f"Failed: {exc}")

        export_btn.configure(command=_do_export)

    # ── Persistence: save current config across bot restarts ────────────────

    def _save_last_config(self) -> None:
        """Auto-save the current tab inputs to ~/.spinedge/backtest_last_config.json
        so the user doesn't lose their setup on restart. Called from
        _run_thread and _export_config_json. Failures are silent — we
        never want to crash a backtest just because the save failed."""
        try:
            cfg = self._build_runner_config()
            cfg.pop("_rotation_used", None)
            os.makedirs(os.path.dirname(self._LAST_CONFIG_PATH), exist_ok=True)
            with open(self._LAST_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception as exc:
            print(f"[Backtest] Could not save last config: {exc}")

    def _load_last_config(self) -> None:
        """Restore widget values from ~/.spinedge/backtest_last_config.json
        if it exists. Called once at the end of __init__ so all widgets
        already exist. Missing keys are skipped silently — a partial
        config still loads what it can."""
        path = self._LAST_CONFIG_PATH
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as exc:
            print(f"[Backtest] Could not read last config: {exc}")
            return

        # Map config keys → (widget attribute name, setter type)
        # "str" = string vars / combo vars (e.g. base_bet_str)
        # "bool" = BooleanVar
        # "int" / "float" = string-typed vars that accept numeric input
        widget_map = [
            ("strategy_name",            "strategy_var",                "str"),
            ("progression_type",         "progression_var",             "str"),
            ("base_bet",                 "base_bet_str",                "str"),
            ("initial_balance",          "init_bal_str",                "str"),
            ("max_loss",                 "max_loss_str",                "str"),
            ("max_consec_losses",        "max_consec_str",              "str"),
            ("profit_target",            "profit_target_str",           "str"),
            ("enable_profit_target",     "enable_session_stops_var",    "bool"),
            ("trailing_stop_amount",     "trailing_stop_str",           "str"),
            ("enable_trailing_stop",     "enable_trailing_stop_var",    "bool"),
            ("max_session_wins_streak",  "max_win_streak_str",          "str"),
            ("session_ext_after_win",    "ext_win_var",                 "bool"),
            ("session_ext_at_high",      "ext_high_var",                "bool"),
            ("max_extension_rounds",     "max_ext_rounds_str",          "str"),
            ("extension_give_up_amount", "ext_give_up_str",             "str"),
            ("sim_mode",                 "sim_mode_var",                "str"),
            ("rounds",                   "rounds_str",                  "str"),
            ("sims",                     "sims_str",                    "str"),
            ("historical_data_source",   "data_source_var",             "str"),
            ("db_limit",                 "db_limit_str",                "str"),
            ("db_offset",                "db_offset_str",               "str"),
            ("enable_global_limits",     "enable_global_limits_var",    "bool"),
            ("global_profit_target",     "global_profit_str",           "str"),
            ("global_stop_loss",         "global_loss_str",             "str"),
        ]
        # Also restore the timing fields (informational; not in runner_cfg
        # directly but persisted via _build_runner_config writing them through).
        # These come from the same widgets so they survive too if they're
        # present in the saved JSON.
        for cfg_key, var_name, kind in widget_map:
            if cfg_key not in cfg:
                continue
            var = getattr(self, var_name, None)
            if var is None:
                continue
            try:
                val = cfg[cfg_key]
                if kind == "bool":
                    var.set(bool(val))
                else:
                    var.set(str(val))
            except Exception:
                pass

        # Rotation-list block (set on top of the canonical key)
        rot = cfg.get("rotation_config")
        if rot:
            try:
                if "mode" in rot and hasattr(self, "rotation_mode_var"):
                    self.rotation_mode_var.set(rot["mode"])
            except Exception:
                pass
            try:
                # Switch into rotation mode if there's a preset
                if hasattr(self, "mode_var"):
                    self.mode_var.set("rotation")
                    if hasattr(self, "_toggle_mode"):
                        self._toggle_mode(self.mode_var.get())
            except Exception:
                pass

        # Dynamic rules survive too — they're stored on self.dynamic_rules
        if isinstance(cfg.get("dynamic_rules"), list):
            self.dynamic_rules = cfg["dynamic_rules"]

        print(f"[Backtest] Restored config from {path}")

    def _export_config_json(self):
        """Save the current tab inputs as a JSON config that backtest_cli.py
        can replay verbatim. Same config + same DB = identical results.
        """
        try:
            cfg = self._build_runner_config()
        except Exception as exc:
            messagebox.showerror("Export Config", f"Could not capture config: {exc}")
            return
        # Strip transient internal flags before writing
        cfg.pop("_rotation_used", None)
        # Also persist as the auto-saved "last config"
        self._save_last_config()

        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile="backtest_config.json",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            title="Export backtest config",
        )
        if not filename:
            return
        try:
            import json
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            messagebox.showinfo(
                "Export Config",
                f"Saved to {filename}\n\nReplay from the command line:\n"
                f"  python backtest_cli.py {filename}",
            )
        except Exception as exc:
            messagebox.showerror("Export Config", f"Failed to save: {exc}")

    def export_report(self):
        """Export detailed report to text file"""
        if not self.results:
            messagebox.showwarning("No Results", "No backtesting results to export.")
            return
            
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Export Backtesting Report"
        )
        
        if filename:
            try:
                # Generate comprehensive report
                report = self.backtester.generate_report(self.results, self.analysis)
                detailed_report = self._generate_detailed_report()
                full_report = report + "\n\n" + detailed_report
                
                with open(filename, 'w') as f:
                    f.write(full_report)
                
                messagebox.showinfo("Success", f"Report exported to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export report: {str(e)}")
    
    def get_results(self):
        """Get current backtesting results"""
        return self.results, self.analysis 

    def _plot_results(self):
        """Plot backtesting results on the Graph tab."""
        try:
            self._plot_results_impl()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            # Surface the failure on the Graph tab so users see WHY it's
            # blank instead of just an empty pane. Previously any error
            # (matplotlib quirk, empty balance_history, missing field on
            # a partial session result) would silently abort and leave
            # the tab looking broken.
            for widget in self.graph_frame.winfo_children():
                widget.destroy()
            tk.Label(
                self.graph_frame,
                text=f"Graph failed to render:\n{exc}\n\n"
                     f"Check the console for the full traceback.",
                fg="red", justify="left",
            ).pack(pady=20, padx=20)

    def _annotate_equity_events(self, ax, is_sequential: bool):
        """Overlay rotation / stop-loss / other-stop / escalation markers on
        the equity axes.

        Uses the new audit fields (sim.stop_reason / escalation_step) so the
        markers are precise:
          - Red X         : sessions ended via STOP_LOSS or INSUFFICIENT_BALANCE
          - Orange ◆      : sessions where escalation_step incremented for the
                            NEXT session (i.e. this session triggered escalation)
          - Yellow ●      : sessions ended for any other reason (PROFIT_TARGET,
                            ROUNDS_EXHAUSTED, MAX_CONSEC_LOSSES, etc.)
          - Purple dotted : on-loss rotation events (mid-session strategy swap)

        Sequential mode only — independent mode plots sessions on their own
        x-axis where event positions wouldn't be meaningful to overlay.
        """
        if not is_sequential or not self.results:
            return
        for _, sessions in self.results.items():
            cumulative = 0
            rotation_x = []
            stop_loss_x, stop_loss_y = [], []
            other_stop_x, other_stop_y = [], []
            esc_x, esc_y = [], []
            for idx, sim in enumerate(sessions):
                hist = getattr(sim, "bet_history", None) or []
                if not hist:
                    cumulative += int(getattr(sim, "total_rounds", 0) or 0)
                    continue
                prev_strat = None
                for rec in hist:
                    r = int(rec.get("round", 0))
                    strat = rec.get("strategy")
                    if prev_strat is not None and strat != prev_strat:
                        rotation_x.append(cumulative + r)
                    prev_strat = strat
                last_round_in_sess = max((int(h.get("round", 0)) for h in hist), default=0)
                end_x = cumulative + last_round_in_sess
                end_y = float(sim.final_balance)
                reason = (getattr(sim, "stop_reason", "") or "").upper()
                if reason in ("STOP_LOSS", "INSUFFICIENT_BALANCE"):
                    stop_loss_x.append(end_x)
                    stop_loss_y.append(end_y)
                elif reason and reason != "ROUNDS_EXHAUSTED":
                    other_stop_x.append(end_x)
                    other_stop_y.append(end_y)
                # Escalation triggered AFTER this session if NEXT session has a
                # higher escalation_step. Compare to the next session's value.
                if idx + 1 < len(sessions):
                    cur_step = int(getattr(sim, "escalation_step", 0) or 0)
                    nxt_step = int(getattr(sessions[idx + 1], "escalation_step", 0) or 0)
                    if nxt_step > cur_step:
                        esc_x.append(end_x)
                        esc_y.append(end_y)
                cumulative += last_round_in_sess
            # Draw rotation events: rate-limited purple dotted lines
            if rotation_x:
                step = max(1, len(rotation_x) // 50)
                drawn_any = False
                for i, x in enumerate(rotation_x):
                    if i % step != 0:
                        continue
                    ax.axvline(x, color="#a855f7", linestyle=":",
                               linewidth=0.7, alpha=0.45,
                               label="rotation" if not drawn_any else None)
                    drawn_any = True
            # STOP_LOSS / INSUFFICIENT_BALANCE markers — these are the ones
            # users care about most ("did my stop loss actually fire?")
            if stop_loss_x:
                ax.scatter(stop_loss_x, stop_loss_y, marker="x", color="#ef4444",
                           s=80, linewidths=1.8, alpha=0.95,
                           label="stop-loss / insufficient bal", zorder=5)
            # Other terminal reasons (PROFIT_TARGET, MAX_CONSEC, TIME_LIMIT…)
            if other_stop_x:
                ax.scatter(other_stop_x, other_stop_y, marker="o", color="#facc15",
                           s=60, alpha=0.85, edgecolors="#1f2937", linewidths=0.6,
                           label="other stop reason", zorder=5)
            # Escalation steps — sessions that triggered escalation for next
            if esc_x:
                ax.scatter(esc_x, esc_y, marker="D", color="#fb923c", s=90,
                           alpha=0.95, edgecolors="#7c2d12", linewidths=1.0,
                           label="escalation step ↑", zorder=6)
            break

    def _plot_results_impl(self):
        # Always free the previous equity figure/canvas/cursor first — even on
        # the early-return paths below — so repeated runs don't accumulate them.
        self._teardown_figs("graph")
        if not self.results:
            return

        if plt is None:
            # Show error label if matplotlib missing
            for widget in self.graph_frame.winfo_children():
                widget.destroy()
            tk.Label(self.graph_frame, text="Graphing requires 'matplotlib' and 'pandas'.\nPlease install them to view charts.", fg="red").pack(pady=20)
            return

        # Count plottable series. A session with only the initial entry is
        # still drawable as a single point — we plot it. But if there are
        # ZERO sessions across all strategies we show a clear message
        # instead of an empty figure.
        total_sessions = sum(len(v) for v in self.results.values())
        if total_sessions == 0:
            for widget in self.graph_frame.winfo_children():
                widget.destroy()
            tk.Label(
                self.graph_frame,
                text="No sessions produced. Check the Detailed Log tab —\n"
                     "the strategy may have stopped before placing any bets.",
                fg="orange", justify="left",
            ).pack(pady=20, padx=20)
            return

        # Create figure with dark theme. NOTE: the figure/axes are styled
        # manually below (facecolors, tick/spine colors), so we deliberately do
        # NOT call plt.style.use('dark_background') here — that mutates global
        # pyplot rcParams on every run (churn + cross-talk with other charts).
        fig = plt.Figure(figsize=(10, 6), dpi=100)
        fig.patch.set_facecolor('#2b2b2b') # Dark background match

        ax = fig.add_subplot(111)
        ax.set_facecolor('#2b2b2b')
        ax.tick_params(colors='white', which='both')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')
        for spine in ax.spines.values():
            spine.set_edgecolor('gray')

        # Plot the campaign.
        # Sequential mode: sessions chain together over time, so plot ONE
        # continuous balance line walking through all sessions and drop a
        # dashed vertical marker at each session boundary. Previously each
        # session's x-axis reset to 0, which overlaid 10 sessions on rounds
        # 0..100 even though the campaign actually spans 0..1000 — looked
        # like 10 parallel lines and was misleading.
        # Independent mode: sessions are isolated Monte Carlo samples, so
        # plot each on its own line (same x-axis is meaningful here —
        # comparing how different draws behave from the same start).
        lines = []
        # (x, y, session_num, round_num) tuples for the click-to-audit handler.
        # Built only for the currently-displayed strategy so the jump always
        # lands on a row that exists in the Detailed Log tree (which only
        # holds rows for `_displayed_strategy_name`).
        self._graph_click_index: list[tuple[float, float, int, int]] = []
        displayed_strat = getattr(self, "_displayed_strategy_name", None)
        campaign = getattr(self, "_last_campaign", None)
        is_sequential = (campaign is not None
                         and getattr(self, "_last_runner_config", {}).get("sim_mode") == "sequential")

        if is_sequential:
            for strategy_name, strategy_results in self.results.items():
                cumulative_rounds = 0
                campaign_rounds: list[float] = []
                campaign_balances: list[float] = []
                session_boundaries: list[int] = []
                for sess_idx, result in enumerate(strategy_results, start=1):
                    hist = getattr(result, "balance_history", None) or []
                    if not hist:
                        continue
                    for entry in hist:
                        local_r = entry.get('round', 0)
                        bal = entry.get('balance', 0)
                        x = cumulative_rounds + local_r
                        campaign_rounds.append(x)
                        campaign_balances.append(bal)
                        if displayed_strat is None or strategy_name == displayed_strat:
                            self._graph_click_index.append((float(x), float(bal), sess_idx, int(local_r)))
                    cumulative_rounds += max(h.get('round', 0) for h in hist) if hist else 0
                    session_boundaries.append(cumulative_rounds)
                if campaign_rounds:
                    line, = ax.plot(campaign_rounds, campaign_balances,
                                    label=strategy_name, alpha=0.9, linewidth=1.6,
                                    color="#facc15")
                    lines.append(line)
                    # Drawdown shading: any region where balance is below the
                    # running campaign peak is filled red. Lets the user SEE
                    # the underwater periods instead of squinting at the curve.
                    try:
                        peak = campaign_balances[0]
                        peak_curve = []
                        for b in campaign_balances:
                            if b > peak:
                                peak = b
                            peak_curve.append(peak)
                        ax.fill_between(
                            campaign_rounds, campaign_balances, peak_curve,
                            where=[b < p for b, p in zip(campaign_balances, peak_curve)],
                            color="#ef4444", alpha=0.18, interpolate=True,
                            label="Drawdown")
                    except Exception:
                        pass
                # Draw session boundaries (skip the last — it's the campaign end)
                for b in session_boundaries[:-1]:
                    ax.axvline(b, color="#64748b", linestyle="--", linewidth=0.8, alpha=0.45)
        else:
            for strategy_name, strategy_results in self.results.items():
                for i, result in enumerate(strategy_results):
                    hist = getattr(result, "balance_history", None) or []
                    rounds = [h.get('round', idx) for idx, h in enumerate(hist)]
                    balances = [h.get('balance', 0) for h in hist]
                    if not rounds:
                        continue
                    label = f"Sim {i+1} ({strategy_name})" if len(self.results) > 1 or len(strategy_results) > 1 else "Balance"
                    line, = ax.plot(rounds, balances, label=label, alpha=0.8, linewidth=1.5)
                    lines.append(line)
                    # Index: each line corresponds to session (i+1); rounds are
                    # per-session local. Nearest-neighbor by (x, y) so the click
                    # lands on the right session's line even when curves overlap.
                    if displayed_strat is None or strategy_name == displayed_strat:
                        sess_num = i + 1
                        for x_val, y_val in zip(rounds, balances):
                            self._graph_click_index.append(
                                (float(x_val), float(y_val), sess_num, int(x_val)))

        # Customize graph
        ax.set_title("Bankroll Evolution")
        ax.set_xlabel("Rounds")
        ax.set_ylabel("Balance ($)")
        ax.grid(True, linestyle='--', alpha=0.3, color='gray')

        # ── Event annotations on the equity curve ─────────────────────────
        # Overlays rotation events, stop-loss hits, and escalation steps so
        # the equity curve tells the full story without scrolling the log.
        # Sequential mode uses cumulative_rounds for x; independent mode
        # plots per-session so annotations would mis-align — skip there.
        try:
            self._annotate_equity_events(ax, is_sequential)
        except Exception as _annot_err:
            print(f"[BacktestGUI] equity annotations failed: {_annot_err}")

        # Add legend if not too many lines and lines exist
        if lines and len(lines) <= 10:
             legend = ax.legend(facecolor='#2b2b2b', edgecolor='gray')
             for text in legend.get_texts():
                 text.set_color("white")
             
        # Hint above the chart explaining the click-to-audit affordance.
        # Tucked in *before* the canvas pack so it sits at the top of the tab.
        try:
            tk.Label(
                self.graph_frame,
                text="💡 Left-click any point to jump to that round in the Detailed Log  ·  "
                     "Right-click to open the board view",
                bg="#2b2b2b", fg="#94a3b8", font=("Segoe UI", 10, "italic"),
                anchor="w", justify="left",
            ).pack(side=tk.TOP, fill="x", padx=8, pady=(4, 0))
        except Exception:
            pass

        # Embed in Tkinter
        canvas = FigureCanvasTkAgg(fig, master=self.graph_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)

        # ── Click-to-audit ────────────────────────────────────────────────
        # Left-click on a point in the equity curve → switch to Detailed Log
        # and select the matching row so the user can see the spins around
        # that region. Right-click → open the Round Audit board modal for the
        # same round. Saves having to eyeball the round number off the x-axis
        # and type it into the Direct Round# input.
        self._graph_canvas = canvas
        self._graph_ax = ax
        # Reset any leftover click marker from a previous render.
        self._graph_click_marker = None
        _click_cid = None
        try:
            _click_cid = canvas.mpl_connect('button_press_event', self._on_graph_click)
        except Exception as _click_err:
            print(f"[BacktestGUI] graph click wiring failed: {_click_err}")

        # Add interactive cursors
        _cursor = None
        try:
            import mplcursors
            _cursor = mplcursors.cursor(lines, hover=True)
            @_cursor.connect("add")
            def on_add(sel):
                # Custom tooltip text
                x, y = sel.target
                sel.annotation.set_text(f"Round: {int(x)}\nBalance: ${y:.2f}\n(click to audit)")
                sel.annotation.get_bbox_patch().set(fc="black", alpha=0.8)
                sel.annotation.set_color('white')
        except ImportError:
            print("mplcursors not installed, skipping interactive tooltips")

        # Track this figure so the NEXT render (_teardown_figs('graph') at the
        # top of this method) closes it, disconnects the click handler, and
        # removes the hover cursor — preventing the per-run leak/lag.
        self._register_fig("graph", fig, canvas, cursor=_cursor, cid=_click_cid)

    def _on_graph_click(self, event):
        """Map a matplotlib click on the equity curve to a (session, round)
        pair and either jump to it in the Detailed Log (left-click) or open
        the board view (right-click)."""
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return
        index = getattr(self, '_graph_click_index', None)
        if not index:
            return
        # Nearest-neighbor in data space. Normalize by axis range so a tall
        # y-axis doesn't drown out x differences (or vice-versa). Falls back
        # to pure x distance if the axis bounds are degenerate.
        try:
            x0, x1 = self._graph_ax.get_xlim()
            y0, y1 = self._graph_ax.get_ylim()
            xr = max(abs(x1 - x0), 1.0)
            yr = max(abs(y1 - y0), 1.0)
            cx, cy = event.xdata, event.ydata
            best = min(index, key=lambda t: ((t[0] - cx) / xr) ** 2 + ((t[1] - cy) / yr) ** 2)
        except Exception:
            cx = event.xdata
            best = min(index, key=lambda t: abs(t[0] - cx))
        x_match, _y_match, sess_num, round_num = best

        # Drop a transient marker so the user knows what their click resolved to.
        try:
            if self._graph_click_marker is not None:
                self._graph_click_marker.remove()
        except Exception:
            pass
        try:
            self._graph_click_marker = self._graph_ax.axvline(
                x_match, color="#22d3ee", linestyle=":", linewidth=1.2, alpha=0.8)
            self._graph_canvas.draw_idle()
        except Exception:
            pass

        # Right-click (button 3) → board modal. Left-click (1) or middle → jump.
        try:
            is_right = getattr(event, 'button', 1) == 3
        except Exception:
            is_right = False
        if is_right:
            try:
                self._audit_session_var.set(str(sess_num))
                self._audit_round_var.set(str(round_num))
                self._open_round_audit_modal()
            except Exception as e:
                messagebox.showerror("Round Audit", f"Failed to open board: {e}")
        else:
            self._jump_to_detail_row(sess_num, round_num)

    def _jump_to_detail_row(self, session_num: int, round_num: int) -> None:
        """Switch to the Detailed Log tab and select the row for
        (session_num, round_num). Clears any active filter that's hiding the
        row so the user can scroll the surrounding spins."""
        iid = f"{session_num}:{round_num}"
        # If the row is detached by the current filter, reset so it's visible
        # again — the whole point of click-to-audit is to see CONTEXT around
        # the click, which means we can't leave WIN-only / SKIP-only filters on.
        try:
            detached = set(getattr(self, '_detached_tree_iids', []) or [])
            if iid in detached:
                if hasattr(self, '_detail_filter_var'):
                    self._detail_filter_var.set("ALL")
                if hasattr(self, '_detail_search_var'):
                    self._detail_search_var.set("")
                # _apply_detail_filter runs via the search trace; for the
                # filter chip change we need to re-run it explicitly.
                try:
                    self._apply_detail_filter()
                except Exception:
                    pass
        except Exception:
            pass
        if not self.detail_tree.exists(iid):
            # Row not in tree (e.g. different strategy was displayed). Notify
            # rather than silently no-op so the user knows the click registered.
            try:
                self._detail_footer.configure(
                    text=f"Row {iid} not in current Detailed Log — switch displayed strategy.")
            except Exception:
                pass
            return
        try:
            self.results_frame.set("Detailed Log")
        except Exception:
            pass
        try:
            self.detail_tree.selection_set(iid)
            self.detail_tree.focus(iid)
            self.detail_tree.see(iid)
        except Exception:
            pass

    def _iter_loaded_rounds(self):
        """Yield (sess_num, record_dict) for every round of the currently
        displayed strategy. Convenience for analytics that need to walk the
        whole campaign without caring which session a round belongs to."""
        strat = getattr(self, "_displayed_strategy_name", None)
        if not strat or strat not in self.results:
            return
        for sess_idx, sim in enumerate(self.results[strat], start=1):
            for rec in (getattr(sim, "bet_history", None) or []):
                yield sess_idx, rec

    def _jump_to_event(self, event_kind: str) -> None:
        """Locate a notable round across the loaded campaign and jump to it.

        event_kind:
          worst_round           — biggest single-round loss (most negative pnl)
          best_round            — biggest single-round win (most positive pnl)
          longest_loss_streak   — first round of the longest consecutive-LOSS run
          first_bankruptcy      — first round where balance went to (≈) zero
          worst_session         — first round of the session with worst total PnL
          max_escalation        — first round at the highest escalation step reached
        """
        strat = getattr(self, "_displayed_strategy_name", None)
        if not strat or strat not in self.results or not self.results[strat]:
            messagebox.showinfo("Jump To",
                                "No backtest loaded. Run one first.")
            return

        target_sess, target_round, hint = None, None, ""

        if event_kind in ("worst_round", "best_round"):
            best_pnl, best_pair = None, None
            cmp = (lambda a, b: a < b) if event_kind == "worst_round" else (lambda a, b: a > b)
            for s, rec in self._iter_loaded_rounds():
                pnl = float(rec.get("pnl", 0.0) or 0.0)
                if best_pnl is None or cmp(pnl, best_pnl):
                    best_pnl = pnl
                    best_pair = (s, int(rec.get("round", 0)))
            if best_pair is None:
                messagebox.showinfo("Jump To", "No rounds in the log.")
                return
            target_sess, target_round = best_pair
            hint = f"PnL = ${best_pnl:+.2f}"

        elif event_kind == "longest_loss_streak":
            # Walk through all rounds tracking the longest LOSS run; remember
            # the starting (session, round) of that run.
            best_len, best_start = 0, None
            cur_len, cur_start = 0, None
            for s, rec in self._iter_loaded_rounds():
                if str(rec.get("result", "")).upper() == "LOSS":
                    if cur_len == 0:
                        cur_start = (s, int(rec.get("round", 0)))
                    cur_len += 1
                    if cur_len > best_len:
                        best_len, best_start = cur_len, cur_start
                else:
                    cur_len, cur_start = 0, None
            if not best_start:
                messagebox.showinfo("Jump To", "No LOSS rounds found.")
                return
            target_sess, target_round = best_start
            hint = f"{best_len} losses in a row"

        elif event_kind == "first_bankruptcy":
            for s, rec in self._iter_loaded_rounds():
                if float(rec.get("balance_after", 0.0) or 0.0) <= 0.01:
                    target_sess, target_round = s, int(rec.get("round", 0))
                    hint = "balance hit $0"
                    break
            if target_sess is None:
                messagebox.showinfo("Jump To", "No bankruptcy in this campaign.")
                return

        elif event_kind == "worst_session":
            sessions = self.results[strat]
            worst_idx, worst_pnl = None, None
            for i, sim in enumerate(sessions, start=1):
                pnl = float(getattr(sim, "total_profit", 0.0) or 0.0)
                if worst_pnl is None or pnl < worst_pnl:
                    worst_pnl, worst_idx = pnl, i
            if worst_idx is None:
                messagebox.showinfo("Jump To", "No sessions loaded.")
                return
            sim = sessions[worst_idx - 1]
            hist = getattr(sim, "bet_history", None) or []
            target_sess = worst_idx
            target_round = int(hist[0].get("round", 1)) if hist else 1
            hint = f"session PnL = ${worst_pnl:+.2f}"

        elif event_kind == "max_escalation":
            # Escalation step is per-session; jump to the first round of the
            # session with the highest reached escalation_step.
            sessions = self.results[strat]
            best_step, best_idx = -1, None
            for i, sim in enumerate(sessions, start=1):
                step = int(getattr(sim, "escalation_step", 0) or 0)
                if step > best_step:
                    best_step, best_idx = step, i
            if best_idx is None or best_step <= 0:
                messagebox.showinfo("Jump To",
                                    "No escalation steps were taken in this campaign.")
                return
            sim = sessions[best_idx - 1]
            hist = getattr(sim, "bet_history", None) or []
            target_sess = best_idx
            target_round = int(hist[0].get("round", 1)) if hist else 1
            hint = f"escalation step = {best_step}"
        else:
            return

        if target_sess is None or target_round is None:
            return
        self._jump_to_detail_row(target_sess, target_round)
        try:
            self._detail_footer.configure(
                text=f"↪ Jumped to {event_kind.replace('_', ' ')} "
                     f"(session {target_sess}, round {target_round}, {hint})")
        except Exception:
            pass

    def save_results(self):
        """Save backtesting results to file"""
        if not self.results:
            messagebox.showwarning("Warning", "No results to save.")
            return
            
        filename = filedialog.asksaveasfilename(defaultextension=".json",
                                                  filetypes=[("JSON files", "*.json")])
        if not filename:
            return
            
        try:
            # Convert results to serializable format
            export_data = {}
            for strat, results_list in self.results.items():
                export_data[strat] = []
                for res in results_list:
                    # Manually build dict to avoid object serialization issues if not handled
                    export_data[strat].append({
                        'total_rounds': res.total_rounds,
                        'total_wins': res.total_wins,
                        'total_losses': res.total_losses,
                        'total_profit': res.total_profit,
                        'final_balance': res.final_balance,
                        'max_drawdown': res.max_drawdown,
                        'balance_history': res.balance_history
                    })
            
            with open(filename, 'w') as f:
                json.dump(export_data, f, indent=4)
            messagebox.showinfo("Success", f"Results saved to {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save results: {e}")

    def export_report(self):
        """Export summary report to text file"""
        if not self.analysis:
            messagebox.showwarning("Warning", "No analysis to export.")
            return

        filename = filedialog.asksaveasfilename(defaultextension=".txt",
                                                  filetypes=[("Text files", "*.txt")])
        if not filename:
            return

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.summary_text.get("1.0", "end"))
            messagebox.showinfo("Success", f"Report saved to {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export report: {e}")
