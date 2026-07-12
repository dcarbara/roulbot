"""
Compatibility shim — the full implementation lives in core/telegram/.
`from core.telegram_bot import RouletteTelegramBot` in main_gui.py keeps working
without any changes.
"""
from core.telegram import RouletteTelegramBot, NotifType, SessionData  # noqa: F401

try:
    import importlib.util
    TELEGRAM_AVAILABLE = importlib.util.find_spec("telegram") is not None
except Exception:
    TELEGRAM_AVAILABLE = False
