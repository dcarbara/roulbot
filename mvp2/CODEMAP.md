## Roulette Bot (mvp2) – Code Map

### Overview
Desktop CustomTkinter app for semi/auto roulette play. OCRs table state from a browser window, clicks bets via screen coordinates, and ranks/runs strategies. Now also ships with a Supabase-backed licensing layer, Fernet-encrypted strategy bundles, a Telegram remote-control bot, and an optional headless mode that pairs with the `spinedge-web` Next.js frontend.

### Two engines, side by side
The codebase contains two coexisting execution paths — most new work happens on the second:

1. **Legacy engine** — `core/strategy_engine.py` drives the progressions in `core/strategies/*` (martingale, fibonacci, d'Alembert, flat, custom, dynamic). Hard-coded label mappings (`ROULETTE_NUMBER_MAPPINGS`, `PAYOUT_TABLE`) live at the top of this file and are the source of truth for bet types.
2. **Signal → Decision → Advanced engine** — `core/signals/` produces structured `SignalReading`s from history; `core/decision/rules.py` evaluates `Condition → Rule → Action` (first-match-wins) from JSON specs; `core/advanced_strategy_engine.py` resolves runtime variables and dispatches to a progression. `core/virtual_strategy_manager.py` runs background "shadow" simulations whose state can be referenced as variables by other rules.

### Entry points
- `main.py` — sets DPI awareness, patches a CTkScrollableFrame bug, launches `gui.main_gui.RouletteBotGUI`. Honors `SPINEDGE_HEADLESS=1` (keeps process alive for FastAPI bridge) and polls `SPINEDGE_VERSION_URL` (defaults to `https://spinedge.pro/version.json`) for self-update prompts.
- `start_webapp.py` — headless launcher used by the Next.js frontend.
- `backtest_cli.py`, `backtest_sweep.py`, `benchmark_*.py` — non-GUI runners.
- `encrypt_bundle.py`, `generate_license.py`, `generate_key.py` — admin/dev tooling for the licensing scheme.

### Directory structure (high-signal)

#### `automation/`
- `roulette_browser.py` — window/pyautogui clicker with focus caching.
- `playwright_driver_v0.py` — optional Playwright CDP control + pyautogui fallback.

#### `config/`
- `config.json` — persisted UI settings and coordinates.
- `schema.py` — `save_config`, `load_config` with `default_config`.

#### `core/`
Top-level modules:
- `strategy_engine.py` — legacy engine; label mappings, chip denominations, payout table; `StrategyEngine` class wiring strategy + progression.
- `advanced_strategy_engine.py` — newer engine. Resolves variables (`strategy_metric`, `gap_since_last`, `last_outcome`, `streak_count`, `statistical_rank`) and evaluates decision rules each round.
- `strategy_base.py` — conceptual base interface for amount progressions.
- `backtesting.py` — `RouletteBacktester`, `BacktestResult` dataclass; simulate and analyze runs.
- `backtesting_runner.py` — orchestrates batched / sweep runs.
- `simulator.py` — thread-based live simulator around `StrategyEngine`.
- `ranking_engine.py` — `RankingEngine.rank_strategies(names, history, ...)`. Simulates candidate strategies on recent history and returns a multi-factor ranked list (PnL, win rate, max drawdown, volatility, bet count). Optional regime filtering.
- `virtual_strategy_manager.py` — `VirtualStrategyManager` runs strategies as background "virtual" sims; exposes `get_state(name)` and `get_metric(name, metric)` so other strategies can react to their state.
- `session_manager.py` — `SessionManager` owns stop conditions and session-extension policies (profit target, trailing stop, time limit, streak caps, extension windows). Decoupled from the GUI.
- `coordinate_recorder.py` — capture coordinates/regions from a chosen window; thin OCR helpers.
- `ocr_utils.py` — Tesseract helpers: table state, balance, recent numbers, winning-number extraction; debug-image saving.
- `auto_calibrator.py` — HSV thresholding + grid fitting to detect a roulette table layout from a screenshot and emit a coordinate preset (numbers, splits, corners, streets, chips).
- `telegram_bot.py` — legacy single-file bot (kept; new bot lives in `core/telegram/`).
- `licensing.py` — HMAC-SHA256 key scheme: `SPIN-{TIER_RANDOM}-{HMAC8}`. Tiers: FREE, BASIC, PLUS, PRO, ADMIN, GOLD, PLATINUM. Constant-time verify. `check_license_gui()` modal.
- `encryption.py` — Fernet symmetric crypto for `.spine` bundles. `encrypt_strategy_data(dict)` / `decrypt_strategy_data(bytes)`. Master key = SHA256(secret) base64.

Subpackages:
- `signals/` — pluggable pattern detectors.
  - `base.py` — `Signal` ABC, `SignalReading` (name, state, member, confidence, metadata), canonical `GROUPS` (color, parity, hilo, dozen, column).
  - `builtins.py` — `StreakSignal`, `DominanceSignal`, `AlternationSignal`, `RegimeSignal`, `LastNumberInSignal`.
  - `registry.py` — `SIGNAL_REGISTRY`, `make_signal(spec)` JSON factory.
- `decision/` — rule engine.
  - `rules.py` — `Condition`, `Rule`, actions (`BetLabelsAction`, `BetGroupAction` with mode follow/contra/target, `DelegateAction`); `parse_rule`, `evaluate_rules`, `labels_for_action`, `extract_delegate_names`.
- `analysis/`
  - `bias_detector.py` — chi-squared on all 37 pockets; binomial z-test on groups; uses scipy when available, Wilson–Hilferty fallback.
  - `regime_detector.py` — classifies each dimension (Colors, Dozens, Columns, EvenOdd, HighLow) as TRENDING / CHOPPY / NEUTRAL. Consumed by `RankingEngine`.
- `security/`
  - `license_manager.py` — Supabase auth, HWID fingerprint (Windows: WMIC UUID + drive serial; macOS: IORegistry), session token, 1-hour heartbeat. `LicenseManager.login/logout`, `.is_authenticated`, `.is_licensed`, `.entitlements`, `.hwid`.
- `telegram/` — modular Telegram bot (replaces `telegram_bot.py`).
  - `bot.py` — `RouletteTelegramBot` with daemon thread + asyncio loop. Public surface: `start/stop`, `wait_until_ready`, `update_live_dashboard`, `send_notification`, `send_smart_notification`, `request_input`, `request_confirmation`.
  - `bridge.py` — `GuiBridge` for thread-safe read-only GUI snapshots; `SessionData`.
  - `formatters.py`, `keyboards.py` — pure rendering helpers (no API calls).
  - `notifications.py` — `NotifType` enum, per-type rate limiting, flood-control circuit breaker (honors Telegram "Retry in N").
  - `state.py` — `BotState`, menu and input-mode tracking.
- `strategies/` — progressions and label providers loaded by both engines.
  - `flat.py`, `martingale.py`, `fibonacci.py`, `dalembert.py`.
  - `custom.py`, `custom_progression.py`, `custom_sequence.py`.
  - `dynamic_progression.py` — rule-driven progression (events: win/loss/session_high; actions: martingale/flat/reset_to_base/custom_sequence/dalembert/keep). Requires `current_profit` on `record_result`.
  - `dynamic_9street.py`, `dynamic_neighbors.py` — label providers for 9-street / neighbors layouts.
  - `pattern_follower.py` — pattern detection + bet placement.
  - `composite.py` — combines multiple sub-strategies (rotation, weighting).
- `utils/`
  - `db_utils.py` — sqlite at `winning_numbers.db`. Tables: `winning_numbers`, `session_stats` (and `round_audit` from `gui/round_audit.py`). `init_db`, `save_session_stats`, `get_aggregate_stats`, `save_winning_number`, `get_recent_winning_numbers`.
  - `image_capture.py` — pyautogui region capture.

#### `gui/`
- `main_gui.py` — **monolithic**, ~14k lines. `RouletteBotGUI` orchestrates strategy setup, OCR, automation, engine, DB, license check, telegram bot, session manager. The empty `tabs/` and `styles/` dirs are scaffolding for a decomposition that hasn't happened.
- `backtesting_gui.py` — `BacktestingGUI` tabbed wrapper around `RouletteBacktester`.
- `round_audit.py` — `RoundRecord` dataclass + `RoundHistoryView`; persists every round (bets, outcome, strategy state, pnl_after, streaks) to the `round_audit` table for forensic review; detail view renders chip placement on a synthetic European board.
- `dynamic_rules_editor.py` — rule composer UI.
- `theme.py` — central CustomTkinter dark theme.
- `components/` — factored widgets.
  - `overlay.py` — overlay/modal framework.
  - `roulette_board.py` — visual board + chip rendering.
  - `pattern_follower_editor.py`, `composite_editor.py` — strategy-config editors.
  - `condition_widget.py`, `action_widget.py` — building blocks for the rules editor.
  - `setup_wizard.py` — first-run flow.
  - `window_watermark.py` — overlay watermark on the browser window.
  - `auth_screen.py` — license / Supabase login.
  - `announcement_dialog.py` — update/news dialog.
  - `collapsible_frame.py` — generic collapsible section.

#### `strategies/` (top-level, not `core/strategies/`)
Encrypted `.spine` bundles distributed with the app: `tier_free.spine`, `tier_gold.spine`, `test_strategy.spine`. Decrypted at runtime via `core/encryption.py` using the master key. Decided by license tier from `core/licensing.py`.

#### `tools/`
- `drawdown_analyzer.py` — simulates a bundle's rotation + progression and reports the minimum stop-loss that survives intra-session dips.
- `packer.py` — builds `.spine` bundles.

#### `docs/`
- `SETUP_GUIDE.md`, `STRATEGIES_GUIDE.md`, `ROLES.md`.

#### `thoughts/`
- `advanced_betting_strategies.md`, `dynamic_9street.md`, `historical_betting_strategies.md` — research notes, not specs.

### Configuration
- `config/schema.py` — `default_config` keys: `strategy`, `base_bet`, `max_loss`, `session_duration_minutes`, `bet_color`, `coordinates`. `save_config(config)` / `load_config()` against `config/config.json`.

### Licensing / DRM (three layers)
1. **HMAC key validation** (`core/licensing.py`) — local, constant-time. `SPIN-{TIER_RANDOM}-{HMAC8}`.
2. **Supabase auth + HWID** (`core/security/license_manager.py`) — online, with 1-hour heartbeat. Supabase URL is currently hard-coded.
3. **Fernet-encrypted strategy bundles** (`core/encryption.py`, `encrypt_bundle.py`) — `.spine` files in `strategies/`; master key = SHA256(secret) base64. Admin tool: `encrypt_bundle.py <input.json> [output.spine]`.

### Headless mode + web frontend
- `SPINEDGE_HEADLESS=1` in `main.py` skips the GUI and keeps the process alive.
- `start_webapp.py` launches the engine for the Next.js `spinedge-web` frontend, which talks to it via a FastAPI bridge using `/api/internal/command` polling.
- `SPINEDGE_VERSION_URL` overrides the default `https://spinedge.pro/version.json` for self-update checks.

### Tests (high-level)
Tests live at the project root rather than in a `tests/` dir.
- `test_backtesting.py`, `test_backtest_parity.py` — sim correctness.
- `test_db_functions.py` — DB init / save / fetch.
- `test_custom_progression.py`, `test_win_reset.py`, `test_subsequent_patterns.py`, `test_immediate_pattern_detection.py` — progression and pattern behavior.
- `test_auto_roulette_*` — end-to-end strategy flows across patterns, wins, waits, resets.
- `test_timestamp_filtering.py` — time-based filtering / OCR watcher logic.
- `test_multi_regime.py`, `test_observation.py`, `test_ranking_engine_standalone.py` — partial coverage of the newer ranking/regime path.
- **No tests** for `core/signals/`, `core/decision/`, `core/security/`, `core/telegram/`, or `core/advanced_strategy_engine.py` — these are the new strategic core but currently rely on manual verification.

### Data flow (happy path)
1. User authenticates (`gui/components/auth_screen.py` → `core/security/license_manager.py`); license tier loaded.
2. User configures strategy/progression and records coordinates/regions in the GUI (or runs `core/auto_calibrator.py`).
3. Auto mode: GUI focuses the browser window and selects chip/labels via `automation/`.
4. `core/ocr_utils.py` reads table state and winning numbers from configured regions; spins land in `winning_numbers.db`.
5. The active engine — legacy (`strategy_engine.py`) or new (`advanced_strategy_engine.py` + `signals/` + `decision/`) — computes next bet and progression.
6. `session_manager.py` checks stop conditions; `round_audit.py` records the round; `telegram/` pushes notifications.

### Common hot spots
- **`gui/main_gui.py` is 14k lines.** Most cross-cutting bugs surface here. Empty `gui/tabs/` and `gui/styles/` directories suggest decomposition is planned.
- OCR sensitivity in `ocr_utils.py` — psm modes, preprocess thresholding, cached configs.
- Coordinate region accuracy from `coordinate_recorder.py` / `config.json` / `auto_calibrator.py`.
- Dynamic progressions require `current_profit` on `record_result`.
- Window focus/activation timing in `automation/`.
- Telegram flood-control: dashboard 8s rate, graph 30s rate, per-type bundling in `notifications.py`.

### Repo hygiene to be aware of
- Tests, benchmark `.txt` outputs, debug logs (`bot.log`, `ocr_debug.log`, `pyarmor.bug.log`, `validation_debug.log`), and the live `winning_numbers.db` all sit at the project root alongside source.
- `gui/main_gui.py.stub` exists next to the real file — likely stale.
- `core/telegram_bot.py` (legacy) coexists with `core/telegram/` (current).
- `__pycache__/` is present in checked-in directories.

### How to run
- GUI: `python main.py` from this directory.
- Headless (for the web frontend): `SPINEDGE_HEADLESS=1 python main.py`, then `python start_webapp.py`.
- Backtesting CLI: `python backtest_cli.py` (or `backtest_sweep.py`).
- Backtesting GUI: launched from inside the main GUI.
- Tests: `pytest` from this directory.
