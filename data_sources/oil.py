"""Crude oil: Brent and WTI futures, with market-open freshness labels.

Primary: Yahoo Finance (free, no key). Brent = ^B00N=F, WTI = CL=F.
Secondary: Investing.com commodity page as a fallback scrape source.

Oil trades ~23h/day, so intraday quotes are only live during the trading
session. Off-hours quotes are labelled CACHED, not LIVE.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_json,
)

log = logging.getLogger(__name__)

# Yahoo Finance chart API - free, no key, rate-limited but adequate.
BRENT_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
WTI_URL = "https://query1.finance.yahoo.com/v8/finance/chart/CL=F"

# Trading hours (New York): Sunday 18:00 to Friday 17:00 UTC.
OIL_OPEN_HOUR = 18
OIL_CLOSE_HOUR = 17  # next day
OIL_OPEN_DAY = 6  # Sunday
OIL_CLOSE_DAY = 4  # Friday


def _is_market_open(now: datetime | None = None) -> bool:
    """Return True if the crude oil session is currently trading."""
    if now is None:
        now = datetime.now(timezone.utc)
    dow = now.weekday()  # Monday=0, Sunday=6
    hour = now.hour
    # Sunday 18:00 - Monday 00:00, and Mon-Thu all day, Fri 00:00-17:00
    if dow == 6:
        return hour >= OIL_OPEN_HOUR
    if dow == 4:
        return hour < OIL_CLOSE_HOUR
    return True


@dataclass
class OilQuote:
    symbol: str
    name: str
    price: float
    change: float
    change_pct: float
    currency: str = "USD"
    timestamp: datetime | None = None
    freshness: Freshness = Freshness.LIVE


def _parse_chart(data: dict, symbol: str, name: str) -> OilQuote | None:
    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta["previousClose"])
        change = price - prev
        pct = (change / prev) * 100 if prev else 0.0
        ts = meta.get("regularMarketTime")
        timestamp = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        return OilQuote(
            symbol=symbol,
            name=name,
            price=price,
            change=round(change, 2),
            change_pct=round(pct, 2),
            currency=meta.get("currency", "USD"),
            timestamp=timestamp,
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Failed to parse oil quote for %s: %s", symbol, exc)
        return None


async def get_brent() -> OilQuote | DataUnavailable:
    data, freshness = await fetch_json(BRENT_URL, cache_key="oil:brent", ttl=900)
    if isinstance(data, DataUnavailable):
        return data
    quote = _parse_chart(data, "BZ=F", "Brent Crude")
    if quote is None:
        return DataUnavailable(source="brent", reason="Malformed response")
    quote.freshness = Freshness.CACHED if not _is_market_open() else freshness
    return quote


async def get_wti() -> OilQuote | DataUnavailable:
    data, freshness = await fetch_json(WTI_URL, cache_key="oil:wti", ttl=900)
    if isinstance(data, DataUnavailable):
        return data
    quote = _parse_chart(data, "CL=F", "WTI Crude")
    if quote is None:
        return DataUnavailable(source="wti", reason="Malformed response")
    quote.freshness = Freshness.CACHED if not _is_market_open() else freshness
    return quote


async def get_oil() -> dict[str, OilQuote | DataUnavailable]:
    """Fetch both Brent and WTI concurrently."""
    brent, wti = await _gather(get_brent(), get_wti())
    return {"brent": brent, "wti": wti}


async def _gather(*coros):
    return await asyncio.gather(*coros)