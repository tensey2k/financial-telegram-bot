"""Foreign exchange rates and daily changes from FXAPI."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from data_sources.base import DataUnavailable, Freshness, fetch_json

log = logging.getLogger(__name__)

FXAPI_URL = "https://api.fxapi.com/v1/latest"
FX_TTL = 86400.0

CURRENCIES = "USD,EUR,MAD,GBP,JPY,CNY,CHF,AUD"

PAIR_ORDER = (
    "CAD/USD",
    "CAD/EUR",
    "CAD/MAD",
    "CAD/GBP",
    "CAD/JPY",
    "CAD/CNY",
    "CAD/CHF",
    "CAD/AUD",
    "USD/MAD",
    "EUR/MAD",
    "EUR/USD",
)


@dataclass
class FxRate:
    pair: str
    base: str
    quote: str
    rate: float
    change_pct: float | None = None
    as_of: datetime | None = None
    source: str = "fxapi"
    freshness: Freshness = Freshness.LIVE


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _unavailable(reason: str) -> dict[str, DataUnavailable]:
    return {
        pair: DataUnavailable(source="fxapi", reason=reason)
        for pair in PAIR_ORDER
    }


def _percentage(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return ((current / previous) - 1) * 100


async def get_forex(api_key: str | None = None) -> dict[str, object]:
    """Fetch CAD-based rates once and derive all requested currency pairs."""

    if not api_key:
        rates = _unavailable("FXAPI API key is not configured")
        return {"rates": rates, "cad_crosses": rates, "last_updated": None}

    payload, freshness = await fetch_json(
        FXAPI_URL,
        params={
            "base_currency": "CAD",
            "currencies": CURRENCIES,
            "include": "change",
        },
        headers={"apikey": api_key},
        cache_key="fxapi:cad-rates",
        ttl=FX_TTL,
        force_refresh=True,
    )

    if isinstance(payload, DataUnavailable):
        rates = _unavailable(payload.reason)
        return {"rates": rates, "cad_crosses": rates, "last_updated": None}

    try:
        raw_data = payload["data"]
        meta = payload.get("meta", {})
        last_updated = _timestamp(meta.get("last_updated_at"))

        current: dict[str, float] = {}
        previous: dict[str, float | None] = {}
        daily_changes: dict[str, float | None] = {}

        for currency in CURRENCIES.split(","):
            item = raw_data[currency]
            value = float(item["value"])
            change = float(item["change"]) if item.get("change") is not None else None

            current[currency] = value
            previous[currency] = value - change if change is not None else None

            pct = item.get("change_pct")
            daily_changes[currency] = (
                float(pct) if pct is not None
                else _percentage(value, previous[currency])
            )

    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Malformed FXAPI response: %s", exc)
        rates = _unavailable("Malformed FXAPI response")
        return {"rates": rates, "cad_crosses": rates, "last_updated": None}

    rates: dict[str, FxRate | DataUnavailable] = {}

    def add_rate(
        pair: str,
        value: float,
        change_pct: float | None,
    ) -> None:
        base, quote = pair.split("/")
        rates[pair] = FxRate(
            pair=pair,
            base=base,
            quote=quote,
            rate=value,
            change_pct=change_pct,
            as_of=last_updated,
            source="fxapi",
            freshness=freshness,
        )

    # Direct CAD-based rates.
    for currency in CURRENCIES.split(","):
        pair = f"CAD/{currency}"
        add_rate(pair, current[currency], daily_changes[currency])

    # Derived cross-rates and their daily percentage changes.
    derived_pairs = {
        "USD/MAD": ("MAD", "USD"),
        "EUR/MAD": ("MAD", "EUR"),
        "EUR/USD": ("USD", "EUR"),
    }

    for pair, (numerator, denominator) in derived_pairs.items():
        value = current[numerator] / current[denominator]

        previous_num = previous[numerator]
        previous_den = previous[denominator]

        previous_value = (
            previous_num / previous_den
            if previous_num is not None
            and previous_den is not None
            and previous_den != 0
            else None
        )

        add_rate(pair, value, _percentage(value, previous_value))

    # Preserve the old key as an alias while callers migrate to "rates".
    return {
        "rates": rates,
        "cad_crosses": rates,
        "last_updated": last_updated,
        "change_since": meta.get("change_since"),
    }
