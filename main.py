"""Entry point: load config, build the Application, start the scheduler, run.

The bot runs as a single long-lived process. systemd owns restarts; the bot
owns polling. The Application is built with the async-native builder so the
whole stack - polling, command handlers, scheduler - shares one event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import Application, CommandHandler

from config import load_settings
from scheduler.daily import build_scheduler
from telegram_bot.commands import (
    cmd_bonds,
    cmd_briefing,
    cmd_crypto,
    cmd_economic,
    cmd_forex,
    cmd_help,
    cmd_news,
    cmd_oil,
    cmd_settings,
    cmd_start,
    cmd_status,
    cmd_stocks,
    cmd_weather,
)

log = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("oil", cmd_oil))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("stocks", cmd_stocks))
    app.add_handler(CommandHandler("bonds", cmd_bonds))
    app.add_handler(CommandHandler("forex", cmd_forex))
    app.add_handler(CommandHandler("canada", cmd_economic, pattern=r"^canada$"))
    app.add_handler(CommandHandler("us", cmd_economic, pattern=r"^us$"))
    app.add_handler(CommandHandler("morocco", cmd_economic, pattern=r"^morocco$"))
    app.add_handler(CommandHandler("europe", cmd_economic, pattern=r"^europe$"))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("settings", cmd_settings))


async def post_init(app: Application) -> None:
    settings = app.bot_data["settings"]
    scheduler = build_scheduler(app, settings)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    log.info("Scheduler started; polling begins.")


async def post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        log.info("Scheduler shut down.")


def main() -> int:
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    log.info("Starting bot for %d authorized user(s).", len(settings.authorized_user_ids))

    builder = Application.builder().token(settings.bot_token.get_secret_value())
    app = builder.post_init(post_init).post_shutdown(post_shutdown).build()
    app.bot_data["settings"] = settings
    register_handlers(app)

    app.run_polling(drop_pending_updates=True, allowed_updates=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())