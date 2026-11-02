"""Telegram authorization: the single allow-list gate for the whole bot.

The bot is personal. Exactly the numeric ids in Settings.authorized_user_ids
may talk to it. Everything else - groups, channels, callbacks from unknown
users, messages from strangers - is dropped at the gate before any command
runs, before any data source is touched, and before any API call is made.

Nothing here touches data sources, so authorization is cheap and side-effect
free: a rejected message costs one update object and one log line.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, TypeVar

from telegram import Chat, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from config import Settings

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def is_authorized(update: Update, settings: Settings) -> bool:
    """Return True only when the update comes from an allowed user in a DM.

    Groups, supergroups, channels, and callbacks from unknown users are all
    rejected. An update with no effective user (some channel posts) is also
    rejected, because there is no identity to check.
    """
    user = update.effective_user
    chat = update.effective_chat

    if user is None or chat is None:
        return False

    # Personal bot: group and channel interactions are never permitted.
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        return False

    return user.id in settings.authorized_user_ids


async def reject_unauthorized(update: Update) -> None:
    """Silently drop an unauthorized update.

    Deliberately does not reply. A visible "you are not authorized" message is
    a social-engineering surface and leaks nothing useful while also inviting
    the sender to probe. The update is simply discarded.
    """
    chat = update.effective_chat
    user = update.effective_user
    if chat is not None:
        log.warning(
            "Rejected unauthorized interaction",
            extra={
                "chat_id": chat.id,
                "chat_type": chat.type,
                "user_id": user.id if user is not None else None,
            },
        )
    else:
        log.warning("Rejected interaction with no resolvable chat/user")


def authorized(settings: Settings) -> Callable[[F], F]:
    """Decorator that gates a command or callback handler on the allow-list.

    Usage:
        @authorized(settings)
        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            ...

    A rejected update is dropped silently and the wrapped handler is never
    invoked, so no data source is touched and no API call is made.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(
            update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
        ) -> Any:
            if not is_authorized(update, settings):
                await reject_unauthorized(update)
                return None
            return await func(update, context, *args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def authorized_message_handler(
    settings: Settings,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Gates the catch-all message handler (for /help, /status, etc.).

    Same semantics as :func:`authorized`, kept as a separate factory so the
    handler signature and the decorator can stay distinct.
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(
            update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
        ) -> Any:
            if not is_authorized(update, settings):
                await reject_unauthorized(update)
                return None
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to /start with the bot's identity and command list.

    Kept here because it is the first thing an authorized user sees, and it
    depends only on the authorization gate, not on any data source.
    """
    chat = update.effective_chat
    if chat is None:
        return
    await context.bot.send_message(
        chat_id=chat.id,
        text=(
            "Personal Financial & Global Intelligence Bot is online.\n\n"
            "All data comes from free public APIs and RSS feeds - no AI, no "
            "paid services, no accounts. Sources and freshness are labelled on "
            "every figure.\n\n"
            "Commands:\n"
            "/briefing - full 09:00-style report on demand\n"
            "/markets - oil, crypto, indices, bonds, rates, forex\n"
            "/stocks - global stock indices\n"
            "/crypto - bitcoin, ethereum, solana\n"
            "/oil - brent and wti crude\n"
            "/rates - policy rates and bond yields\n"
            "/economy - Canada, US, Morocco, Europe indicators\n"
            "/news - global and regional headlines\n"
            "/canada - Canadian economy, mortgages, housing\n"
            "/morocco - Bank Al-Maghrib, HCP, trade, tourism\n"
            "/weather - Montreal forecast and alerts\n"
            "/settings - current configuration\n"
            "/status - API availability, cache, scheduler state\n"
            "/help - this list"
        ),
        parse_mode=None,
    )