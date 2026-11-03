"""Daily briefing scheduler.

One scheduled job builds the briefing data once and sends it by email only.
APScheduler handles the configured timezone.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from briefing.generator import (
    build_briefing_data,
    format_briefing_html,
    format_briefing_text,
)
from briefing.mailer import send_briefing_email
from config import Settings

log = logging.getLogger(__name__)


async def _deliver_briefing(app, settings: Settings) -> None:
    """Build once and deliver the scheduled briefing by email only."""
    try:
        data = await build_briefing_data(settings)
    except Exception:
        log.exception("Scheduled briefing build failed")
        return

    # No scheduled Telegram messages; manual /briefing remains unchanged.
    if settings.brevo_enabled:
        try:
            generated = data["generated_at"].strftime("%Y-%m-%d")
            await send_briefing_email(
                settings,
                f"Daily Financial Intelligence — {generated}",
                format_briefing_html(data),
                format_briefing_text(data),
            )
        except Exception:
            log.exception("Scheduled briefing email delivery failed")


def build_scheduler(app: Application, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.zone)
    trigger = CronTrigger(
        hour=settings.delivery_time.hour,
        minute=settings.delivery_time.minute,
        day_of_week=",".join(str(d) for d in settings.delivery_days),
        timezone=settings.zone,
    )
    scheduler.add_job(
        _deliver_briefing,
        trigger=trigger,
        args=[app, settings],
        id="daily_briefing",
        name="Daily intelligence briefing",
        misfire_grace_time=600,
        coalesce=True,
    )
    return scheduler
