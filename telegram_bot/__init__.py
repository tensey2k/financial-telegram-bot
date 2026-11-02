"""Telegram bot package.

The bot is a thin orchestrator: it owns the Application, wires up the
authorization gate and the command handlers, and runs the scheduler that
delivers the 07:00 briefing. It does not fetch data itself - every market,
macro, news, and weather value comes from a connector in data_sources/.
"""

from telegram_bot.authorization import (
    authorized,
    authorized_message_handler,
    is_authorized,
    reject_unauthorized,
    send_welcome,
)

__all__ = [
    "authorized",
    "authorized_message_handler",
    "is_authorized",
    "reject_unauthorized",
    "send_welcome",
]