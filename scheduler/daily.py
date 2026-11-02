"""Daily briefing scheduler.

APScheduler runs a single CronTrigger job at the configured time in the
configured timezone. The job builds the briefing and delivers it to every
authorized user. It does not retry on failure - a failed delivery is logged
and the next day's job runs on schedule.

DST is handled by APScheduler through the ZoneInfo object: 09:00
America/Toronto stays 09:00 America/Toronto whether the clock is EST or EDT.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.constants import ParseMode
from telegram.ext import Application

from briefing.generator import build_briefing
from config import Settings

log = logging.getLogger(__name__)


async def _deliver_briefing(app: Application, settings: Settings) -> None:
    try:
        chunks = await build_briefing(settings)
    except Exception:
        log.exception("Scheduled briefing build failed")
        return
    for user_id in settings.authorized_user_ids:
        try:
            for chunk in chunks:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
        except Exception:
            log.exception("Failed to deliver briefing to user %s", user_id)


def build_scheduler(app: Application, settings: Settings) -> AsyncIOScheduler:
    """Construct the scheduler with the daily job. Does not start it."""
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
    log.info(
        "Scheduled daily briefing at %02d:%02d %s on %s",
        settings.delivery_time.hour,
        settings.delivery_time.minute,
        settings.timezone,
        ", ".join(settings.delivery_days),
    )
    return scheduler