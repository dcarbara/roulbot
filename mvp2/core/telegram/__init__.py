"""
core.telegram — SpinEdge Telegram remote-control package.

Public API
----------
  RouletteTelegramBot   — the main bot class (drop-in replacement for the old
                          core.telegram_bot.RouletteTelegramBot)

  NotifType             — enum for typed notifications
  SessionData           — typed snapshot of live session state (for testing)
"""
from .bot           import RouletteTelegramBot
from .notifications import NotifType
from .bridge        import SessionData

__all__ = ["RouletteTelegramBot", "NotifType", "SessionData"]
