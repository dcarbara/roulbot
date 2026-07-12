# SpinEdge — Developer Onboarding

How to set up, run, and start hacking on the SpinEdge engine from source. This is
the **developer** guide. If you just want to *use* the bot (calibrate a table,
place bets), read [SETUP_GUIDE.md](SETUP_GUIDE.md) instead.

> **TL;DR**
> ```bash
> cd spinedge/engine/mvp2
> python -m venv ../venv && ../venv/Scripts/activate   # team convention: venv lives at engine/venv
> pip install -r requirements.txt
> cp .env.example .env                                 # fill in secrets (see below)
> python main.py                                        # launch the GUI
> ```
> You can develop and run **backtests + tests without any license or secrets** —
> only the live GUI/auth and encrypted `.spine` bundles need them.

---

## 1. What you're working on

SpinEdge is a **Windows desktop app** (CustomTkinter GUI) that automates live
roulette: it OCR-reads the table state from a browser window and places bets by
clicking screen coordinates. It also ships a full **backtesting** engine to
simulate strategies against recorded spin history.

The core idea, everywhere in the code: a `StrategyEngine` = **a strategy** (*where*
to bet — which numbers/areas) **+ a progression** (*how much* to bet after a
win/loss). Keep that split in mind; it's the spine of the system.

Already-existing docs worth reading next:
- [CODEMAP.md](../CODEMAP.md) — detailed module map, public APIs, debugging hotspots.
- [GEMINI.md](../GEMINI.md) — short architecture overview.
- [STRATEGIES_GUIDE.md](STRATEGIES_GUIDE.md) — how strategies/progressions work.
- [SETUP_GUIDE.md](SETUP_GUIDE.md) — end-user setup (table calibration, running bets).

---

## 2. Repo orientation (read this before you `cd` anywhere)

The repository root (`spinedge/`) is messy — it contains a lot of unrelated
reverse-engineering scratch files (`*.js`, `decode_*`, `dump_*`, etc.). **Ignore
all of that.** The real project lives here:

| Path | What it is |
|---|---|
| `engine/mvp2/` | **The canonical Python app — you work here.** |
| `engine/launcher/` | Bootstrapper for the packaged build |
| `engine/venv/` | Team-shared virtualenv (see [`.agent/rules/venv.md`](../../../.agent/rules/venv.md)) |
| `engine/requirements.txt` | Engine-wide deps (mvp2 has its own pinned set) |
| `spinedge-web/`, `web/`, `webv2/` | Web frontends (separate stacks) |
| `engine/mvp2/start_webapp.py` | Optional FastAPI + pywebview web shell (imports an `api.server` package that isn't checked in here — see §6). |

> ⚠️ **`engine/mvp2/gui/main_gui.py` is the single source of truth** for the GUI +
> bot loop (~16.8k lines). If you ever find a second copy in an older layout (e.g. a
> `roulette_bot/` tree), treat it as a stale fork — don't edit it; tooling line
> numbers and behavior only match the canonical `engine/mvp2` tree.

### Inside `engine/mvp2/`

| Dir / file | Responsibility |
|---|---|
| `main.py` | **Entry point.** Sets DPI awareness, builds the CTk root, launches `RouletteBotGUI`. |
| `gui/main_gui.py` | The monolith: GUI **and** the live bot loop (~16.8k lines). |
| `gui/backtesting_gui.py` | Backtesting tab UI. |
| `core/strategy_engine.py` | `StrategyEngine` — strategy + progression, the central unit. |
| `core/strategies/` | Strategy implementations (martingale, fibonacci, dynamic, composite, …). |
| `core/triggers.py` | `TriggerEngine` — rotation / conditional / **parallel** strategy selection. |
| `core/session_manager.py` | Session lifecycle + stop conditions (stop-loss, profit target, time, streaks). |
| `core/backtesting.py` | Single-campaign simulator (`backtest_strategy`). |
| `core/backtesting_runner.py` | `bundle_to_campaign_config`, `run_campaign`, `backtest_bundle` (bundle → sim). |
| `core/ocr_utils.py` | Tesseract OCR: winning number, table state, balance. |
| `automation/roulette_browser.py` | Browser window automation (clicks). |
| `core/utils/db_utils.py` | SQLite store of winning numbers / stats. |
| `core/security/license_manager.py` | License validation (Supabase + HMAC). |
| `core/telegram/` | Telegram remote control. |
| `core/profit/` | Profit layer (bias scouting, +EV math). |
| `config/` | `schema.py` (config schema/defaults), `presets.py`, table presets. |

---

## 3. Prerequisites

| Requirement | Notes |
|---|---|
| **OS** | Windows 10/11. The app is Windows-only (uses `ctypes.windll`, `pygetwindow`, `pyautogui`, win32). It will not fully run on macOS/Linux. |
| **Python** | **3.11 or 3.12** (numpy 2.3 / scipy pins require ≥3.11). |
| **Tesseract OCR** | Required for OCR. Install to `C:\Program Files\Tesseract-OCR\` (the app auto-detects this path) or point at it in **Settings → OCR**. |
| **Git** | To clone. |
| **Display scaling** | Set Windows scaling to **100%** for live automation (pixel-accurate clicks). Irrelevant for backtests/tests. |

---

## 4. First-time setup

```bash
# 1. From the repo, enter the canonical app
cd spinedge/engine/mvp2

# 2. Create + activate the virtualenv (team convention puts it at engine/venv)
python -m venv ../venv
../venv/Scripts/activate          # PowerShell: ..\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure secrets
cp .env.example .env              # then edit .env (see §5)
```

> **Gotcha:** `requirements.txt` is saved as **UTF‑16**. If `pip` errors with an
> encoding/`null byte` complaint, re-save it as UTF‑8 (or `pip install` the
> packages individually). The heavy hitters: `customtkinter`, `opencv-python`,
> `pytesseract`, `pyautogui`, `playwright`, `numpy/scipy/scikit-learn/pandas`,
> `matplotlib/seaborn`, `cryptography`, `keyboard`, `mss`, `websocket-client`.
> `pyarmor` + `pyinstaller` are only needed to build the installer.

### Install Tesseract
Download from <https://github.com/UB-Mannheim/tesseract/wiki>, install to the
default `C:\Program Files\Tesseract-OCR\`, and verify with `tesseract --version`.
Resolution order in `core/ocr_utils.py`: bundled (in a packaged build) →
user-configured path → default install path.

---

## 5. Environment variables (`.env`)

Copy `.env.example` → `.env`. Four secrets exist:

| Var | Needed for | If missing |
|---|---|---|
| `SPINEDGE_SUPABASE_URL` | License/auth (the GUI's AuthScreen) | Can't log in / run the live GUI |
| `SPINEDGE_SUPABASE_KEY` | License/auth (Supabase anon key) | Same |
| `SPINEDGE_MASTER_SECRET` | License HMAC validation | License verification fails |
| `SPINEDGE_TRADE_SECRET` | Decrypting encrypted `.spine` strategy bundles | `.spine` bundles won't load (plain `.json` bundles still work) |

**You do not need any of these to develop the engine.** Backtests and the
`test_*`/`verify_*` scripts bypass the license (the engine sets
`_ranking_simulation = True` for simulations, and the license manager honors a
`DEBUG_BYPASS`). Get real Supabase creds + a dev license from the project owner
only when you need to exercise the **live** GUI/auth path. Never commit `.env`.

---

## 6. Running it

```bash
cd spinedge/engine/mvp2
../venv/Scripts/activate
```

| Goal | Command |
|---|---|
| **Live GUI** (needs Tesseract + login) | `python main.py` |
| **Headless engine** (for the web bridge) | `set SPINEDGE_HEADLESS=1 && python main.py` |
| **Web/FastAPI + pywebview shell** | `python start_webapp.py` (dev: `set SPINEDGE_DEV=1`). ⚠️ imports an `api.server` package not present in this checkout — ask the owner for the `api/` tree if you need the web shell. |
| **Backtest from CLI** | `python backtest_cli.py` (see `--help`) |
| **Parameter sweep** | `python backtest_sweep.py` |
| **Import spin history into the DB** | `python import_history.py` |
| **License tooling** (owner only) | `generate_license.py`, `generate_key.py`, `diagnose_license.py` |
| **Encrypt a bundle to `.spine`** | `python encrypt_bundle.py` |

Logs stream to stdout and to `bot.log` (in the working dir).

---

## 7. Runtime data & config locations

Everything user/runtime lives under `~/.spinedge/` (i.e.
`C:\Users\<you>\.spinedge\`):

| What | Path |
|---|---|
| Main config (UI settings, coordinates, **custom strategies**) | `~/.spinedge/config/config.json` |
| Strategy bundles | `~/.spinedge/bundles/*.json` (encrypted: `*.spine`) |
| Saved backtest runs | `~/.spinedge/backtest_runs/*.json` |
| Last backtest config (incl. custom-strategy registry snapshot) | `~/.spinedge/backtest_last_config.json` |
| Winning-numbers DB | SQLite (`winning_numbers.db`, via `core/utils/db_utils.py`) |
| Config schema / defaults | `engine/mvp2/config/schema.py` |
| Table presets | `engine/mvp2/config/` + `~/.spinedge/.../custom_presets/` |

---

## 8. Common dev tasks

### Run a backtest of a bundle in code
```python
import os, json
from core.backtesting_runner import backtest_bundle
custom = json.load(open(os.path.expanduser("~/.spinedge/backtest_last_config.json")))["custom_strategies"]
camp = backtest_bundle(
    os.path.expanduser("~/.spinedge/bundles/<name>.json"),
    initial_balance=1000, rounds=100, sims=50, sim_mode="sequential",
    custom_strategies=custom,            # ← REQUIRED or custom strategies fall back to a placeholder
)
print(camp.sessions[0].stop_reason)
```

### Run the test/verify scripts
The `test_*.py` and `verify_*.py` files in `engine/mvp2/` are runnable scripts
(not all wired into a pytest suite). Run them directly from the `mvp2` dir, e.g.:
```bash
python test_backtesting.py
python verify_win_logic.py
```

### Build the Windows installer
`make_build.bat` (recommended) or `python build_installer.py` → PyInstaller +
pyarmor obfuscation, bundling Tesseract and assets, output at `dist/SpineEdge.exe`.

---

## 9. Gotchas & conventions (save yourself a day)

- **`engine/mvp2` is the only tree to edit.** If you encounter an older
  `roulette_bot/` copy of `main_gui.py`, it's a stale fork — ignore it.
- **Custom strategies must be registered** in the global `custom_strategies`
  map (in `config.json`) and passed into the engine. Bundles reference strategies
  *by name*; they do **not** carry the definitions. In backtests, pass
  `custom_strategies=...` or every leg silently degrades to a never-winning
  placeholder (looks like a flat-lining strategy).
- **`max_consec_losses` is per-strategy only** now: `0` = disabled. Set it per row
  in the Bundle Builder (serialized into the entry suffix `|max_consec_losses=N`),
  not globally. The live run loop and the backtest both honor the per-strategy
  value; the old bundle-level field is inert. (If a run mysteriously "stops at 5",
  this is why — check for a stale global cap.)
- **Backtest Monte-Carlo caveat:** in generated + *independent* mode, all sims can
  reuse one RNG dataset (zero variance). For honest Monte Carlo, drive
  `StrategyEngine` directly or use real historical data.
- **`gui/main_gui.py` is a 16.8k-line monolith** mixing GUI and the live bot loop.
  Use search; don't try to read it top-to-bottom. Entry into the bot loop is via
  `RouletteBotGUI`.
- **Live automation is pixel-based:** display scaling must be 100%, the browser
  window must stay put, and OCR regions must be calibrated. None of this matters
  for backtests.
- **Encrypted `.spine` bundles** need `SPINEDGE_TRADE_SECRET`; prefer plain `.json`
  bundles during development.

---

## 10. Troubleshooting (dev)

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: core...` | Run from the `engine/mvp2` dir (imports are relative to it), with the venv active. |
| `pip` fails on `requirements.txt` | It's UTF‑16; re-save as UTF‑8 or install packages individually. |
| `TesseractNotFound` | Install Tesseract to the default path or set it in **Settings → OCR**. |
| GUI won't get past login | You need Supabase creds + a license — or work via backtests/tests, which bypass auth. |
| Backtest shows 0% wins / instant bust | You forgot `custom_strategies=` → strategies fell back to placeholders. |
| Clicks land in the wrong place | Windows scaling ≠ 100%, or the browser window moved — re-select + recalibrate. |

---

*New gotcha or setup step bit you? Add it here — this doc is the first thing the
next dev reads.*
