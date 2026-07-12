import json
import os
import tkinter as tk
from tkinter import simpledialog, messagebox
import logging

logger = logging.getLogger(__name__)

import hashlib
import hmac

# Env var override for production; built-in default for desktop distribution
MASTER_SECRET = os.environ.get("SPINEDGE_MASTER_SECRET", "SPINEDGE_MASTER_SECRET_2025_V1")

def validate_license_key(key):
    """
    Validates the license key signature.
    Format: SPIN-{PAYLOAD}-{SIGNATURE}
    """
    if not key or not isinstance(key, str):
        return False
        
    key = key.strip().upper()
    parts = key.split('-')
    
    # Must have 3 parts: SPIN, PAYLOAD, SIGNATURE
    if len(parts) != 3:
        return False
        
    prefix, payload, signature = parts
    
    if prefix != "SPIN":
        return False
        
    # Re-calculate expected signature
    try:
        # Create HMAC-SHA256
        message = payload.encode('utf-8')
        secret = MASTER_SECRET.encode('utf-8')
        
        expected_sig = hmac.new(secret, message, hashlib.sha256).hexdigest()
        expected_sig_short = expected_sig[:8].upper()
        
        # Constant time comparison to prevent timing attacks (overkill here but good practice)
        return hmac.compare_digest(signature, expected_sig_short)
        
    except Exception as e:
        logger.error(f"License verification error: {e}")
        return False

def get_license_tier(key):
    """
    Validates the key and extracts the tier (e.g. 'FREE', 'GOLD').
    Returns 'FREE' if invalid or not specified.
    """
    if not validate_license_key(key):
        return "FREE"
        
    try:
        # Format: SPIN-{PAYLOAD}-{SIGNATURE}
        # Payload: {TIER}_{RANDOM}
        parts = key.strip().upper().split('-')
        if len(parts) >= 2:
            payload = parts[1]
            if '_' in payload:
                tier = payload.split('_')[0]
                # Validate against known tiers
                if tier in ["BASIC", "PLUS", "PRO", "ADMIN", "GOLD", "PLATINUM", "FREE"]:
                    return tier
                return "FREE" # Unknown tier
            else:
                # Fallback for old USR... keys
                return "FREE"
    except Exception:
        pass
        
    return "FREE"

from config.schema import CONFIG_PATH, load_config, save_config

def load_license_from_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        data = load_config()
        return data.get('license_key')
    except Exception as e:
        logger.error(f"Failed to load config for license: {e}")
        return None

def save_license_to_config(key):
    try:
        data = load_config()
        data['license_key'] = key
        logger.debug(f"Saving license key to {CONFIG_PATH}")
        save_config(data)
    except Exception as e:
        logger.error(f"Failed to save license to config: {e}")

def check_license_gui(root_tk_instance=None):
    """
    Checks for a valid license. If not found, prompts user.
    Returns True if licensed, False otherwise.
    """
    stored_key = load_license_from_config()
    
    if stored_key:
        logger.debug(f"Checking stored license")
        if validate_license_key(stored_key):
            logger.info("Valid license found in config.")
            return True
        else:
            logger.warning("Stored license key is invalid.")
    else:
        logger.debug("No stored license found in config.")
    
    # Prompt user
    # We need a temporary root if one isn't provided, but main.py creates one.
    # If main.py calls this BEFORE app init, we can pass the root or create a hidden one.
    
    should_destroy_root = False
    if root_tk_instance is None:
        root_tk_instance = tk.Tk()
        root_tk_instance.withdraw()
        should_destroy_root = True
        
    while True:
        key = simpledialog.askstring(
            "Spinedge Activation", 
            "Please enter your License Key:", 
            parent=root_tk_instance
        )
        
        if key is None:
            # User cancelled
            return False
            
        if validate_license_key(key):
            save_license_to_config(key)
            messagebox.showinfo("Success", "License Activated! Welcome to Spinedge.", parent=root_tk_instance)
            if should_destroy_root:
                root_tk_instance.destroy()
            return True
        else:
            retry = messagebox.askretrycancel(
                "Activation Failed", 
                "Invalid License Key.\nKey must start with 'SPIN-'.",
                parent=root_tk_instance
            )
            if not retry:
                if should_destroy_root:
                    root_tk_instance.destroy()
                return False
