# GEMINI.md: Roulette Bot (mvp2)

## Project Overview

This project is a desktop GUI application for semi-automated and automated roulette gameplay. It uses Optical Character Recognition (OCR) to read the state of the game (like winning numbers and table status) from a browser window. It can then automate betting by clicking on screen coordinates.

The application features a backtesting utility to simulate and analyze betting strategies. The core logic separates strategy selection (which numbers or areas to bet on) from bet progression (how much to bet after a win or loss).

**Key Technologies:**
*   **Language:** Python
*   **GUI:** Tkinter
*   **Automation:** PyAutoGUI, Playwright
*   **OCR:** Tesseract (`pytesseract`), Pillow, OpenCV
*   **Data/Analysis:** pandas, scikit-learn, matplotlib
*   **Database:** SQLite

A very detailed map of the codebase exists in `CODEMAP.md`.

## Building and Running

### Dependencies

Install the required Python packages using pip:

```bash
pip install -r requirements.txt
```

### Running the Application

To launch the main GUI:

```bash
python main.py
```

or

```bash
python -m roulette_bot.src.mvp2.main
```

### Running Tests

The project uses `pytest` for testing. To run the test suite:

### Building from Source (Create Installer)

To generate a standalone `.exe` installer (bundling Tesseract and assets):

1.  **Use the Batch Script (Recommended):**
    Double-click `make_build.bat` in the `engine/mvp2` directory.

2.  **Manual Command:**
    ```bash
    python build_installer.py
    ```

The output executable will be located in `engine/mvp2/dist/SpineEdge.exe`.

## Development Conventions

### Core Architecture

The application is centered around a `StrategyEngine` that combines a betting strategy with a progression system.

*   **`gui/main_gui.py`**: The main entry point for the user interface, orchestrating the various components.
*   **`core/strategy_engine.py`**: The central logic for managing betting strategies and progressions.
*   **`core/ocr_utils.py`**: Handles all OCR-related tasks, such as extracting numbers, table state, and balance from the screen.
*   **`automation/roulette_browser.py`**: Manages browser automation and interaction (clicking).
*   **`core/backtesting.py`**: Provides tools for simulating strategy performance against historical or generated data.
*   **`core/utils/db_utils.py`**: Manages the SQLite database for storing winning numbers and session statistics.

### Configuration

Runtime configuration, including UI settings and screen coordinates for automation, is stored in `config/config.json`. The schema and default values are defined in `config/schema.py`.

### Data Flow

1.  The user configures a strategy and records screen coordinates using the GUI.
2.  In auto-mode, the application uses OCR to monitor the game state.
3.  Based on the game state and the selected strategy, the `StrategyEngine` determines the next bet.
4.  The `roulette_browser` automation module places the bet by simulating clicks.
5.  Game results are recorded in the `winning_numbers.db` SQLite database for analysis.

For a more detailed breakdown of modules, public APIs, and debugging hotspots, refer to `CODEMAP.md`.
