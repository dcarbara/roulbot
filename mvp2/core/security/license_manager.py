import os
import platform
import subprocess
import hashlib
import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import Optional, Dict

# Conditional imports since they need to be installed
try:
    from supabase import create_client, Client
except ImportError:
    pass

try:
    from gotrue.errors import AuthApiError
except ImportError:
    AuthApiError = Exception

try:
    import keyring
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Env var override for production; built-in defaults for desktop distribution
SUPABASE_URL = os.environ.get("SPINEDGE_SUPABASE_URL", "https://jskknfdfufpamodzzqtu.supabase.co")
SUPABASE_KEY = os.environ.get("SPINEDGE_SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Impza2tuZmRmdWZwYW1vZHp6cXR1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE3ODUzNTgsImV4cCI6MjA4NzM2MTM1OH0.tCEXePaawZUaHYfwLyUoISiMkiRxYcLHh_eu5m6ti1I")
APP_NAME = "SpinEdge"

class LicenseManager:
    """
    Manages user authentication, hardware fingerprinting, and backend license verification.
    """
    def __init__(self):
        self.supabase: Optional['Client'] = None
        self._init_supabase()
        
        self.hwid = self._generate_hwid()
        self.current_user = None
        self.license_data = None
        self.session_token = None  # Active session token for this login

        self.is_authenticated = False
        self.is_licensed = False
        self.entitlements = [] # List of bundle_ids purchased by user
        
        # In-memory caching for performance in tight loops
        self._last_heartbeat_time = 0
        self._heartbeat_interval = 3600  # 1 hour
        # self.DEBUG_BYPASS = True

    def _init_supabase(self):
        """Initialize the Supabase client safely."""
        try:
            logger.debug(f"Attempting to init Supabase with URL: {SUPABASE_URL[:15] if SUPABASE_URL else 'None'}...")
            if SUPABASE_URL and SUPABASE_KEY:
                self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                logger.info("Supabase client initialized successfully.")
            else:
                logger.warning("Supabase URL/KEY not set. License management disabled.")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {e}")

    def _generate_hwid(self) -> str:
        """
        Generates a unique, spoof-proof Machine ID based on hardware serials.
        Combines Motherboard UUID and OS UUID.
        """
        hwid_string = platform.node() # Fallback
        
        try:
            if platform.system() == "Windows":
                # Get Motherboard UUID
                output = subprocess.check_output(["wmic", "csproduct", "get", "uuid"]).decode().split('\n')
                if len(output) > 1:
                    hwid_string += output[1].strip()

                # Get system drive volume serial
                output_vol = subprocess.check_output(["cmd", "/c", "vol", "c:"]).decode()
                hwid_string += output_vol.split()[-1].strip()
            elif platform.system() == "Darwin":
                output = subprocess.check_output(["bash", "-c", "ioreg -rd1 -c IOPlatformExpertDevice | awk '/IOPlatformUUID/ { split($0, line, \"\\\"\"); printf(\"%s\\n\", line[4]); }'"]).decode().strip()
                hwid_string += output
        except Exception as e:
            logger.warning(f"Could not retrieve strict HWID, using fallback: {e}")
            
        # Hash the concatenated identifiers to create a clean, uniform length ID
        hashed_hwid = hashlib.sha256(hwid_string.encode()).hexdigest()
        return hashed_hwid

    def login(self, email: str, password: str) -> tuple[bool, str]:
        """
        Authenticates with Supabase and stores the session token.
        """
        if not self.supabase:
            return False, "Backend service is not configured or unavailable."
            
        try:
            response = self.supabase.auth.sign_in_with_password({"email": email, "password": password})
            self.current_user = response.user
            self.is_authenticated = True
            
            # Save token to Windows Credential Manager securely
            token_data = {
                "access_token": response.session.access_token,
                "refresh_token": response.session.refresh_token
            }
            self._save_token(json.dumps(token_data))
            
            # Immediately validate the license after login
            valid, msg = self.validate_license()
            if not valid:
                self.logout() # Force logout if no valid license to clear invalid states
                return False, msg

            # Register a new session — this invalidates any other active session
            self._register_session()

            return True, "Login successful."
        except AuthApiError as e:
            return False, f"Auth Error: {e.message}"
        except Exception as e:
            return False, f"Unexpected Error: {e}"

    def logout(self):
        """Clears local session and remote auth."""
        if self.supabase:
            try:
                self.supabase.auth.sign_out()
            except Exception: pass
            
        self.current_user = None
        self.license_data = None
        self.entitlements = []
        self.session_token = None
        self.is_authenticated = False
        self.is_licensed = False
        self._clear_token()
        self._clear_session_token()

    def validate_license(self, force_refresh=False) -> tuple[bool, str]:
        """
        Checks the `licenses` table in Supabase for this user's hardware.
        Returns (is_valid, status_message).
        """
        if getattr(self, "DEBUG_BYPASS", False): # Local dev bypass
            self.is_licensed = True
            return True, "Debug Bypass Active"

        if not self.supabase or not self.current_user:
            self.is_licensed = False
            return False, "Not logged in."

        # Auto-recovery: reload session_token from keyring before validating.
        # Another instance (web app login, second desktop instance, in-GUI
        # relogin while the bot was running) may have rotated the token in
        # both keyring and DB. Without this refresh, the bot's in-memory
        # token goes stale and validation fails until process restart.
        try:
            fresh_local = self._load_session_token()
            if fresh_local and fresh_local != self.session_token:
                logger.info(
                    "[License] session_token refreshed from keyring (in-memory was stale)"
                )
                self.session_token = fresh_local
        except Exception as e:
            logger.debug(f"[License] Could not refresh session_token from keyring: {e}")

        try:
            # Query the licenses table for this user
            result = self.supabase.table('licenses').select('*').eq('user_id', self.current_user.id).execute()
            
            if not result.data:
                self.is_licensed = False
                return False, "No active license found for this account."
                
            lic = result.data[0]
            
            # Check session token — one active session per account
            db_token = lic.get('active_session_token')
            if self.session_token and db_token and db_token != self.session_token:
                self.is_licensed = False
                return False, "Your session was replaced by a login from another device. Please sign in again."
            
            # Optional: Check expiry dates if you have them
            # if lic.get('valid_until') and datetime.fromisoformat(lic['valid_until']) < datetime.now():...
            
            # Fetch Entitlements (Strategy DLCs)
            self.entitlements = []
            try:
                bundle_result = self.supabase.table('user_bundles').select('bundle_id').eq('user_id', self.current_user.id).execute()
                if bundle_result and bundle_result.data:
                    self.entitlements = [b['bundle_id'] for b in bundle_result.data]
            except Exception as e:
                logger.warning(f"Could not fetch user entitlements: {e}")
            
            self.license_data = lic
            self.is_licensed = True
            return True, "License Valid."
            
        except Exception as e:
            logger.error(f"License verification failed: {e}")
            return False, "Could not verify license with server."

    def get_announcements(self) -> list:
        """
        Fetches active announcements for this user (global + user-specific).
        Returns list of dicts with keys: id, title, message, type.
        """
        if not self.supabase or not self.current_user:
            return []
        try:
            result = self.supabase.table('announcements').select(
                'id, title, message, type'
            ).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"Could not fetch announcements: {e}")
            return []

    def refresh_entitlements(self):
        """Re-fetch entitlements from Supabase without full license re-validation."""
        if not self.supabase or not self.current_user:
            return
        try:
            bundle_result = self.supabase.table('user_bundles').select('bundle_id').eq('user_id', self.current_user.id).execute()
            if bundle_result and bundle_result.data:
                self.entitlements = [b['bundle_id'] for b in bundle_result.data]
            else:
                self.entitlements = []
            logger.info(f"Refreshed entitlements: {self.entitlements}")
        except Exception as e:
            logger.warning(f"Could not refresh entitlements: {e}")

    def try_auto_login(self) -> bool:
        """Attempts to log in using a stored JWT token."""
        token_str = self._load_token()
        if not token_str or not self.supabase:
            return False
            
        try:
            tokens = json.loads(token_str)

            # Re-hydrate the session
            res = self.supabase.auth.set_session(tokens.get("access_token"), tokens.get("refresh_token"))

            if res and res.user:
                self.current_user = res.user
                self.is_authenticated = True
                self.session_token = self._load_session_token()
                valid, _ = self.validate_license()
                return valid
            return False
        except Exception as e:
            logger.debug(f"Auto-login failed: {e}")
            self._clear_token() # Token probably expired or invalid JSON
            return False

    # --- Session Management ---
    def _register_session(self):
        """Generates a new session token and writes it to the DB, kicking any previous session."""
        if not self.supabase or not self.current_user:
            return
        try:
            token = secrets.token_hex(32)  # 256-bit random token
            now = datetime.now(timezone.utc).isoformat()
            result = self.supabase.table('licenses').update({
                'active_session_token': token,
                'session_started_at': now,
            }).eq('user_id', self.current_user.id).execute()
            if result.data:
                self.session_token = token
                self._save_session_token(token)
                logger.info("Session registered — previous session invalidated.")
        except Exception as e:
            logger.error(f"Session registration error: {e}")

    def _save_session_token(self, token: str):
        try:
            keyring.set_password(APP_NAME, "session_token", token)
        except Exception as e:
            logger.warning(f"Could not save session token: {e}")

    def _load_session_token(self) -> Optional[str]:
        try:
            return keyring.get_password(APP_NAME, "session_token")
        except Exception:
            return None

    def _clear_session_token(self):
        try:
            keyring.delete_password(APP_NAME, "session_token")
        except Exception:
            pass

    # --- Secure Storage (Credential Manager) ---
    def _save_token(self, token: str):
        try:
            keyring.set_password(APP_NAME, "auth_token", token)
        except Exception as e:
             logger.warning(f"Could not use keyring, fallback needed: {e}")

    def _load_token(self) -> Optional[str]:
        try:
            return keyring.get_password(APP_NAME, "auth_token")
        except Exception:
            return None

    def _clear_token(self):
        try:
            keyring.delete_password(APP_NAME, "auth_token")
        except Exception:
            pass

_global_lm = None
def get_license_manager() -> LicenseManager:
    global _global_lm
    if _global_lm is None:
        _global_lm = LicenseManager()
    return _global_lm
