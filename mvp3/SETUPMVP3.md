# SETUPMVP3 — Running the mvp3 Bot UI on Another Machine

This guide covers everything needed to run the **SpinEdge mvp3 Bot Controller Panel**
(`bot_panel.py`) and its bot/overlay processes on a fresh Windows machine.

> **How it works (why the requirements are strict):** mvp3 does not talk to the
> casino through a browser API. It *screen-scrapes* — it takes screenshots (`mss`),
> reads numbers/balance with OCR (`pytesseract` + Tesseract), and clicks the betting
> table at fixed pixel coordinates (`pyautogui`). Everything is therefore tied to a
> **1920×1080 display with the game laid out exactly like the reference machine.**

---

## 1. Prerequisites (install these first)

| Requirement | Why | Default path the code expects |
|---|---|---|
| **Windows 10/11** | Uses `taskkill`, `powershell`, `pywin32`, `PyGetWindow`. Not cross-platform. | — |
| **Python 3.11** (64-bit) with Tcl/Tk | Runtime + the Tkinter GUI. Tested on 3.11.15. | see §3 |
| **Tesseract-OCR** | Reads the last number, balance, status text | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| **Google Chrome** | Opens the game (`stake.com` Evolution Roulette) | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| **1920×1080 monitor** | Click coordinates in `coords.json` are calibrated for this | — |

Install Tesseract from the UB-Mannheim build (Windows installer). Keep the default
install directory or you'll need to edit the paths in §4.

---

## 2. Files to copy from this machine

Copy the entire `mvp3/` folder. At minimum these must be present:

- `bot_panel.py`, `spinedge_bot.py`, `overlay_live.py`, `control.py` — code
- `config.json`, `bot_config.json` — strategy + bet settings
- **`coords.json`** — click/OCR coordinates (the single most machine-specific file)
- `config/`, `core/`, `strategies/` — supporting modules
- `requirements.txt`

Optional / runtime-generated (safe to leave behind — recreated automatically):
- `bet_history.db`, `bot_state.json`, `bot_cmd.json`, `bot_history.json`
- `winning_numbers.db` (~51 MB) — **only** needed for backtesting, not for live betting

---

## 3. Python environment

The launchers prefer a shared venv one level up: `..\venv\Scripts\python.exe`
(i.e. `spinedge-engine-main\venv`). Create it there:

```powershell
cd C:\path\to\spinedge-engine-main
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install dependencies. `requirements.txt` is a full `pip freeze` and is saved as
**UTF-16**, which older pip chokes on. Easiest is to install the runtime packages
directly:

```powershell
pip install opencv-python numpy pandas mss pyautogui pytesseract pillow `
            keyboard pywin32 pygetwindow python-dateutil pytz tzdata `
            requests scikit-learn scipy
```

(Tkinter is bundled with the standard python.org installer — no pip package needed.)

If you prefer the exact pinned set, re-save `requirements.txt` as UTF-8 first, then
`pip install -r requirements.txt`.

Verify:
```powershell
.\venv\Scripts\python.exe -c "import tkinter, cv2, mss, pyautogui, pytesseract; print('OK')"
```

---

## 4. Fix hardcoded paths (required)

Some files hardcode paths from the original machine. Edit these:

| File / line | Current value | Change to |
|---|---|---|
| `spinedge_bot.py:20` `tesseract_cmd` | `C:\Program Files\Tesseract-OCR\tesseract.exe` | your Tesseract path (leave if default) |
| `overlay_live.py:27` `tesseract_cmd` | same Tesseract path | your Tesseract path |
| `config.json` `chrome_path` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | your Chrome path |
| `config/schema.py:27` `tesseract_path` | same Tesseract path | your Tesseract path |

> **Python interpreter — no edit needed.** `bot_panel.py` and `spinedge_bot.py` now
> resolve the interpreter automatically: they prefer `..\venv\Scripts\python.exe`
> (relative to the script folder, so it follows the repo to any machine) and fall back
> to whatever interpreter is already running the panel. Just make sure the venv exists
> where §3 puts it, or launch the panel with the interpreter you want the bot to use.

`overlay_live.py:31` also has a hardcoded `SCRATCHPAD` path, but it's only a *fallback*
location for `coords.json`. Since `coords.json` sits next to the scripts, this is
harmless and can be ignored.

---

## 5. Recalibrate coordinates (if the layout differs)

`coords.json` maps every bet button/OCR region to pixels, referenced to a **1920×1080**
image (`_meta.image_w/image_h`). At runtime coordinates are scaled linearly by
`monitor_width/1920 × monitor_height/1080`.

This means:
- **Same 1920×1080 resolution + same Chrome/game layout → works as-is.**
- Different resolution → linear scaling *may* work but is fragile.
- Different game window size/position, browser zoom, or Chrome toolbar height → clicks
  land in the wrong place. You must re-measure and rewrite `coords.json`.

Keep Chrome fullscreen (F11) with the roulette table in the same spot as the source
machine to avoid recalibration.

---

## 6. Run it

```powershell
cd C:\path\to\spinedge-engine-main\mvp3
..\venv\Scripts\python.exe bot_panel.py
```

- Pin to the right edge of a 1920 display: `..\venv\Scripts\python.exe bot_panel.py --x 1648`
- The window is **frameless and always-on-top** — drag it by its body.
- Alternative CLI menu instead of the GUI: run `control.py` (this is what `run.bat`
  launches). Note `run.bat` also assumes `..\venv\Scripts\python.exe` exists.

From the panel you Start/Pause/Stop the bot and toggle the live overlay. Open the game
in Chrome (the URL in `config.json`) and place its window before starting the bot.

---

## 7. Quick verification checklist

- [ ] `python -c "import tkinter, cv2, mss, pyautogui, pytesseract"` succeeds
- [ ] `tesseract --version` runs (Tesseract on PATH or path edited in §4)
- [ ] `..\venv\Scripts\python.exe` exists (the bot auto-resolves to it), or you launch
      the panel with the interpreter you want the bot to use
- [ ] `coords.json` present; monitor is 1920×1080 (or coords recalibrated)
- [ ] `config.json` `chrome_path` valid; game opens at the expected layout
- [ ] Panel opens, and clicking **Start** actually spawns a `spinedge_bot.py` process
      (check Task Manager for a second `python.exe`)

---

## 8. Common failures

| Symptom | Cause | Fix |
|---|---|---|
| Panel opens, Start does nothing | venv missing and panel launched with an interpreter lacking the bot's deps | §3 (create venv / install deps) |
| `TesseractNotFoundError` | Tesseract not installed / wrong path | §1, §4 |
| Bot clicks empty space / wrong cells | resolution or layout mismatch | §5 |
| `ModuleNotFoundError` | dependency missing | §3 |
| Chrome won't launch | `chrome_path` wrong in `config.json` | §4 |
| Bot reads wrong numbers | game layout/zoom shifted OCR regions | §5 |
