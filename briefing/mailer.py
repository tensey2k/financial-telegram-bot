"""Brevo transactional email delivery for the daily briefing."""

from __future__ import annotations

import logging

import httpx

from config import Settings

log = logging.getLogger(__name__)

BREVO_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


async def send_briefing_email(
    settings: Settings,
    subject: str,
    html_content: str,
    text_content: str,
) -> bool:
    """Send a briefing email if enabled. Never raises delivery errors."""
    if not settings.brevo_enabled:
        log.info("Briefing email skipped: Brevo delivery is disabled")
        return False

    api_key = settings.brevo_api_key.get_secret_value().strip()
    if not api_key:
        log.error("Briefing email skipped: Brevo API key is missing")
        return False

    payload = {
        "sender": {
            "email": settings.brevo_sender_email,
            "name": "Daily Financial Intelligence",
        },
        "to": [{"email": settings.brevo_recipient_email}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                BREVO_EMAIL_URL,
                headers={
                    "accept": "application/json",
                    "api-key": api_key,
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()

        log.info("Briefing email accepted by Brevo")
        return True

    except httpx.HTTPStatusError as exc:
        log.error(
            "Brevo rejected briefing email (HTTP %s)",
            exc.response.status_code,
        )
    except httpx.RequestError as exc:
        log.error("Brevo email request failed: %s", type(exc).__name__)
    except Exception:
        log.exception("Unexpected briefing email delivery error")

    return False
