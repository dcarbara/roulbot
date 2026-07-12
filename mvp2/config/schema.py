# config/schema.py

import json
import logging
import os
import sys
import shutil

logger = logging.getLogger(__name__)

# Determine base path for persistent storage
# MVP3: Use user's home directory to ensure persistence even if executable moves/updates
USER_HOME = os.path.expanduser("~")
APP_DATA_DIR = os.path.join(USER_HOME, ".spinedge")
CONFIG_DIR = os.path.join(APP_DATA_DIR, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# Ensure config directory exists
os.makedirs(CONFIG_DIR, exist_ok=True)

default_config = {
    "strategy": "red_odd",
    "base_bet": 0.1,
    "max_loss": 1,
    "session_duration_minutes": 15,
    "bet_color": "red",
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "coordinates": {}
}

def get_bundled_config_path():
    """Return path to config inside the bundle if frozen"""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            # OneFile Bundle
            return os.path.join(sys._MEIPASS, "config", "config.json")
        else:
            # OneDir Bundle
            return os.path.join(os.path.dirname(sys.executable), "_internal", "config", "config.json")
    return None

def save_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    
    # SAFETY: Ensure we don't accidentally wipe the license key if it's missing from the incoming config object
    # but exists in the file.
    current_file_data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                current_file_data = json.load(f)
        except Exception: pass
    
    if "license_key" in current_file_data and "license_key" not in config:
        # print("DEBUG: Preserving existing license key from file.")
        config["license_key"] = current_file_data["license_key"]

    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.debug("Error saving config: %s", e)

def load_config():
    logger.debug("Loading config from %s", CONFIG_PATH)
    # 1. Try to load user config from disk
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                logger.debug("Config loaded successfully. Balance: %s", data.get('current_balance', 'Not Set'))
                return data
        except Exception as e:
            logger.debug("Failed to load config from %s: %s", CONFIG_PATH, e)
            # CRITICAL FIX: Do NOT fall through to bundled config if file exists but is corrupt.
            # Only fall through if file DOES NOT EXIST.
            # If we fall through, we overwrite the user's data with defaults.
            # Instead, return default config but DO NOT overwrite yet.
            logger.debug("Returning default config due to load error (Not overwriting file).")
            return default_config.copy()

    logger.debug("User config not found. Trying bundle/defaults.")
    
    # 2. If no user config, try to extract initial config from bundle
    bundled_path = get_bundled_config_path()
    if bundled_path and os.path.exists(bundled_path):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            # Only copy if destination doesn't exist (double check)
            if not os.path.exists(CONFIG_PATH):
                shutil.copy2(bundled_path, CONFIG_PATH)
                logger.debug("Copied bundled config from %s", bundled_path)
                with open(CONFIG_PATH, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug("Failed to copy/load bundled config: %s", e)
            pass 

    # 3. Fallback to default dictionary
    return default_config.copy()
