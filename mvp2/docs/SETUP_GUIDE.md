# SpinEdge Roulette Automation - Setup Guide

A step-by-step guide to get the roulette automation bot running on your machine.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Install Dependencies](#2-install-dependencies)
3. [Install Tesseract OCR](#3-install-tesseract-ocr)
4. [Windows Display Settings](#4-windows-display-settings)
5. [Launch the Application](#5-launch-the-application)
6. [Login & Licensing](#6-login--licensing)
7. [Select Your Browser Window](#7-select-your-browser-window)
8. [Calibrate the Table Layout](#8-calibrate-the-table-layout)
   - [Option A: Load a Preset](#option-a-load-a-preset)
   - [Option B: Auto-Detect from Screenshot](#option-b-auto-detect-from-screenshot)
   - [Option C: Manual Calibration](#option-c-manual-calibration)
9. [Calibrate OCR Regions](#9-calibrate-ocr-regions)
10. [Create a Strategy](#10-create-a-strategy)
11. [Build a Strategy Bundle](#11-build-a-strategy-bundle)
12. [Configure Betting Parameters](#12-configure-betting-parameters)
13. [Run the Bot](#13-run-the-bot)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. System Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows 10 or 11 |
| **Python** | 3.10 or later |
| **Display Scaling** | Must be set to **100%** (critical) |
| **Browser** | Any browser with the roulette table open (Chrome, Edge, Firefox) |
| **Tesseract OCR** | Required for reading numbers from the screen |
| **Screen Resolution** | 1920x1080 recommended (other resolutions work but need calibration) |

---

## 2. Install Dependencies

Open a terminal in the project directory and run:

```bash
cd spinedge/engine/mvp2
pip install -r requirements.txt
```

Key packages installed:
- `customtkinter` - Modern dark-themed GUI
- `pyautogui` - Mouse automation for chip placement
- `pygetwindow` - Window selection and tracking
- `pytesseract` + `opencv-python` - OCR for reading numbers
- `keyboard` - Hotkey detection (F8/F9 for calibration)

---

## 3. Install Tesseract OCR

Tesseract is required for the bot to read winning numbers, balance, and table state from the screen.

### Download & Install

1. Download the installer from: https://github.com/UB-Mannheim/tesseract/wiki
2. Run the installer. Use the default path: `C:\Program Files\Tesseract-OCR\`
3. During installation, make sure "Add to PATH" is checked

### Verify Installation

```bash
tesseract --version
```

If you installed to a non-default location, you can configure the path later in **Settings > OCR Settings** within the app.

---

## 4. Windows Display Settings

**This is critical.** The bot uses pixel coordinates to click on the roulette table. If your display scaling is not 100%, all clicks will land in the wrong place.

### Set Scaling to 100%

1. Right-click your Desktop > **Display settings**
2. Under **Scale and layout**, set **Scale** to **100%**
3. If using multiple monitors, set all monitors to 100%
4. **Restart your computer** after changing scaling

### Verify

- Open the roulette table in your browser
- Elements should look smaller than usual (this is normal at 100% on high-DPI screens)

---

## 5. Launch the Application

```bash
cd spinedge/engine/mvp2
python main.py
```

The application window will appear with a dark theme. On first launch, you'll see the **Setup Wizard**.

---

## 6. Login & Licensing

1. The **Auth Screen** appears first
2. Enter your email and password (registered at spinedge.pro)
3. Your license tier determines which features are available:

| Tier | Available Features |
|---|---|
| FREE | Dashboard, Statistics |
| BASIC | + Strategy Builder |
| PLUS | + Backtesting, Simulation |
| PRO | + Bot Control, Auto Roulette (full automation) |

---

## 7. Select Your Browser Window

Before the bot can interact with the roulette table, it needs to know which window to target.

1. **Open your roulette table** in a browser (keep it visible, not minimized)
2. In the app, go to **Bot Control** tab
3. Click **Select Window**
4. A dialog lists all open windows - find and select your browser window
5. Click **Select** (or double-click the window name)
6. A gold border will briefly flash around the selected window to confirm

**Tips:**
- Keep the browser window at a fixed size and position after selection
- Don't minimize the browser while the bot is running
- If you move/resize the browser, re-select the window

---

## 8. Calibrate the Table Layout

The bot needs to know where every bet position is on your screen. There are three ways to set this up:

### Option A: Load a Preset

Best for supported casino tables (e.g., Evolution Gaming Auto Roulette).

1. Go to **Settings & Setup > Region/Coordinate Setup**
2. Under **Quick Setup (Presets)**, select your casino from the dropdown
3. Click **Apply Preset**
4. Coordinates are loaded instantly

### Option B: Auto-Detect from Screenshot

Best when no preset exists for your table. Uses computer vision to find the betting grid automatically.

**From the main app (with browser window selected):**

1. Go to **Settings & Setup > Region/Coordinate Setup**
2. Click the green **Auto-Detect Table** button
3. The bot captures a screenshot of your browser window
4. It detects the number grid (red/black/green cells) and chip tray
5. A preview window shows the detected layout with:
   - Confidence score
   - Number of coordinates generated
   - Visual overlay of detected positions
6. Review the overlay - numbers should align with the actual cells
7. Click **Apply Coordinates** to use them, or **Save as Preset** for future use

**From the Setup Wizard (first-time):**

1. Take a screenshot of your roulette table (Win+Shift+S or Print Screen)
2. Save it as a PNG/JPG file
3. In the wizard, click **Auto-Detect from Screenshot**
4. Select your screenshot file
5. Review and apply

**What gets detected automatically:**
- All 37 number positions (0-36)
- Splits (adjacent number pairs)
- Corners (4-number intersections)
- Streets (3-number rows)
- Double streets (6-number blocks)
- Dozens (1st/2nd/3rd 12)
- Even chances (Red/Black, Odd/Even, 1-18/19-36)
- Column bets (2-to-1)
- Chip tray positions (if visible)

**What you may need to calibrate manually after auto-detect:**
- OCR regions (balance, table state, winning number)
- Chip positions (if not detected or if denominations changed)

### Option C: Manual Calibration

For maximum precision or unusual table layouts.

1. Go to **Settings & Setup > Region/Coordinate Setup**
2. Under **Coordinate Recording**:
   - Select a bet type from the dropdown (e.g., "1", "red", "chip_1")
   - Click **Record Coordinate**
   - Move your mouse over the target position on the roulette table
   - Press **F8** to record
3. Repeat for every bet position you plan to use
4. The coordinate list updates in real-time

**Priority order for manual calibration:**

| Priority | Labels | Why |
|---|---|---|
| 1 (Critical) | `chip_.1`, `chip_1`, `chip_5` | Bot can't bet without chips |
| 2 (Critical) | Numbers your strategy uses | e.g., all 37 numbers for neighbor bets |
| 3 (Important) | Outside bets | `red`, `black`, `odd`, `even`, `1to18`, `19to36` |
| 4 (Important) | Dozens & Columns | `1st12`, `2nd12`, `3rd12`, `col1`, `col2`, `col3` |
| 5 (Optional) | Splits, Corners, Streets | Only if your strategies use inside bets |

---

## 9. Calibrate OCR Regions

The bot reads text from specific screen areas to track the game state. These **must** be calibrated manually (auto-detect does not set these).

### Required Regions

| Region | What It Reads | How To Calibrate |
|---|---|---|
| `balance` | Your current balance number | Draw a box around the balance display |
| `table_state` | "PLACE YOUR BETS" / "NO MORE BETS" | Draw a box around the status text area |

### Optional Regions

| Region | What It Reads |
|---|---|
| `recent_winning_numbers` | The last N winning numbers sidebar |
| `winning_number_region` | The large winning number display |

### How to Record a Region

1. Go to **Settings & Setup > Region/Coordinate Setup**
2. Under **Region Recording**, select the region label (e.g., `balance*`)
3. Click **Record Region**
4. Move your mouse to the **top-left corner** of the text area and press **F8**
5. Move your mouse to the **bottom-right corner** and press **F9**
6. A preview pops up showing what the OCR reads from that area
7. If the preview is correct, click OK to save

**Tips for accurate OCR:**
- Draw the box tightly around the text (no extra space)
- Make sure the text is clearly visible (no overlapping elements)
- The balance region should only contain the number, not the currency symbol if possible
- Test by checking if the bot reads the correct number in the preview

---

## 10. Create a Strategy

Strategies define **where** to place bets. Go to **Strategy Lab > Strategy Builder**.

### Using the Visual Board

1. **Click** cells on the roulette board to select bet positions
2. Selected cells highlight in gold
3. **Right-click** a cell to deselect it
4. The selected labels appear in the strategy preview

### Custom Bet Units

If you want different amounts on different positions:

1. Enable **Custom Bet Units** checkbox
2. **Single-click** a selected cell to increment its units
3. **Double-click** a selected cell to type a specific unit value
4. **Right-click** to deselect

### Bet Modes

| Mode | Description |
|---|---|
| **Static** | Fixed labels - always bets on the same positions |
| **Neighbors** | Dynamic - bets on N neighbors of the last winning number on the wheel |

### Save the Strategy

1. Enter a name in the **Strategy Name** field
2. Click **Add to Strategies**
3. The strategy appears in your strategy list and is saved to your config

---

## 11. Build a Strategy Bundle

Bundles combine strategies with betting rules into a complete automation package.

1. Go to **Bot Control** tab
2. Configure:
   - **Strategy**: Select from your saved strategies
   - **Progression**: flat, martingale, fibonacci, d'alembert, or custom
   - **Base Bet**: Starting bet amount
   - **Max Loss**: Stop-loss per session
3. For **Strategy Rotation** (cycling through multiple strategies):
   - Add strategies to the rotation list
   - Set rotation mode: Sequential, Random, or Smart Ranking
   - Set trigger: On Loss, On Session End
   - Check **Reset to 1st Strategy on Session End** if desired
4. Click **Save Bundle** to save as a reusable `.json` file
5. Bundles are saved to `~/.spinedge/bundles/`

### Loading a Bundle

- Click **Load Bundle** and select a `.json` file from `~/.spinedge/bundles/`
- Or select from the **Quick Load** dropdown on the Dashboard

---

## 12. Configure Betting Parameters

Before starting, review these settings in **Bot Control**:

| Parameter | Description | Recommended Start |
|---|---|---|
| **Base Bet** | Amount per bet unit | 0.10 (minimum) |
| **Max Bet** | Maximum single bet | 100x base bet |
| **Max Loss** | Stop-loss per session | 20-50x base bet |
| **Session Duration** | Rounds per session | 10-50 |
| **Number of Sessions** | How many sessions to run | Start with 1-5 |
| **Min/Max Gap** | Minutes between sessions | 1-5 min |
| **Profit Target** | Stop session on profit | Optional |
| **Observation Trigger** | Watch N spins before betting | 0-5 |
| **Max Consecutive Losses** | Emergency stop | 5-10 |

### Dynamic Rules (Optional)

Add rules that modify behavior based on outcomes:
- **On Win**: Reset to base bet, skip next round, etc.
- **On Loss**: Increase bet, switch strategy, etc.

---

## 13. Run the Bot

1. Make sure:
   - Browser window is selected (gold border visible)
   - Table layout is calibrated
   - OCR regions are set (balance + table_state minimum)
   - Strategy/bundle is configured
   - Balance is entered correctly

2. Click **Start Bot** on the Dashboard or Bot Control tab

3. The bot will:
   - Wait for the table to show "PLACE YOUR BETS"
   - Select the correct chip denomination
   - Click on each bet position
   - Wait for the spin result
   - Read the winning number via OCR
   - Calculate win/loss and update progression
   - Repeat for the configured number of rounds/sessions

4. **Monitor** the bot via:
   - Dashboard: Live PnL graph, session stats
   - Activity Log: Detailed action log
   - Winning Numbers: Frequency analysis

5. To stop, click **Stop Bot** or press the configured hotkey

---

## 14. Troubleshooting

### Bot clicks in the wrong place

| Cause | Fix |
|---|---|
| Display scaling not 100% | Set Windows scaling to exactly 100% and restart |
| Browser window moved/resized | Re-select the window and re-apply the preset |
| Wrong preset loaded | Choose the correct casino preset or re-calibrate |
| High DPI monitor | Ensure DPI awareness is set (app does this automatically) |

### OCR reads wrong numbers

| Cause | Fix |
|---|---|
| Region too large | Re-record the region with a tighter box |
| Overlapping UI elements | Move the browser so nothing overlaps the number display |
| Low contrast text | Some table themes have poor contrast - try a different table |
| Tesseract not installed | Install Tesseract and set the path in OCR Settings |

### Bot too slow placing chips

The bot uses `pyautogui.PAUSE` to control click speed. Default is optimized at 0.074s. If you experience:
- **Misclicks** (too fast): Increase pause in settings
- **Timeout** (too slow): For strategies with many labels (30+), the bot temporarily reduces pause during chip placement

### Table state not detected

- Verify the `table_state` OCR region captures the text area that shows "PLACE YOUR BETS" or similar
- Check OCR Settings > test the region to see what text is detected
- Some tables use images instead of text - these may not work with OCR

### Auto-detect fails

- Ensure the full betting grid is visible in the browser (no overlapping chat windows or popups)
- Try with a clean screenshot file instead of live capture
- Check that the table has standard red/black/green coloring
- Low confidence (<50%) means some positions may be inaccurate - verify manually

### Balance not updating

- Re-record the `balance` OCR region
- Make sure the region captures only the numeric balance, not currency symbols
- Some casinos update balance with animations - the bot reads during the static state

---

## File Locations Reference

| What | Path |
|---|---|
| Main config | `~/.spinedge/config/config.json` |
| Strategy bundles | `~/.spinedge/bundles/*.json` |
| Custom presets | `spinedge/engine/mvp2/config/custom_presets/*.json` |
| Tesseract (default) | `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| Application logs | Visible in Activity Log tab |
| Custom strategies | Saved inside `config.json` under `custom_strategies` key |

---

## Quick Start Checklist

- [ ] Python 3.10+ installed
- [ ] Dependencies installed (`pip install -r requirements.txt`)
- [ ] Tesseract OCR installed
- [ ] Windows display scaling set to 100%
- [ ] App launched (`python main.py`)
- [ ] Logged in with valid account
- [ ] Browser window with roulette table selected
- [ ] Table layout calibrated (preset, auto-detect, or manual)
- [ ] OCR regions set (balance + table_state)
- [ ] Strategy created or bundle loaded
- [ ] Balance entered correctly
- [ ] Bot started and monitoring
