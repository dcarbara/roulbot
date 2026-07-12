import os
from cryptography.fernet import Fernet
import base64
import hashlib
import logging
import json

logger = logging.getLogger(__name__)

# Env var override for production; built-in default for desktop distribution
TRADE_SECRET = os.environ.get("SPINEDGE_TRADE_SECRET", "SPINEDGE_STRATEGY_MASTER_KEY_2025").encode()

def get_fernet_key():
    """Derive a 32-byte URL-safe base64-encoded key from the trade secret."""
    # Use SHA256 to get 32 bytes, then base64 encode it for Fernet
    digest = hashlib.sha256(TRADE_SECRET).digest()
    return base64.urlsafe_b64encode(digest)

def encrypt_strategy_data(strategy_dict):
    """
    Encrypts a strategy dictionary into bytes.
    Returns: Encrypted bytes
    """
    try:
        key = get_fernet_key()
        f = Fernet(key)
        
        # Convert dict to JSON string, then bytes
        json_str = json.dumps(strategy_dict)
        data_bytes = json_str.encode('utf-8')
        
        # Encrypt
        encrypted = f.encrypt(data_bytes)
        return encrypted
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return None

def decrypt_strategy_data(crypto_bytes):
    """
    Decrypts bytes into a strategy dictionary.
    Returns: Dict or None
    """
    try:
        key = get_fernet_key()
        f = Fernet(key)
        
        # Decrypt
        decrypted_bytes = f.decrypt(crypto_bytes)
        
        # Parse JSON
        strategy_dict = json.loads(decrypted_bytes.decode('utf-8'))
        return strategy_dict
    except Exception as e:
        logger.error(f"Decryption failed (Invalid Key or Corrupt File): {e}")
        return None
