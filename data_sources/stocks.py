"""Global stock indices.

Primary: Yahoo Finance chart API (free, no key).
Secondary: Finnhub (optional API key) as a fallback when Yahoo fails.

Each index is labelled LIVE only while its exchange is open; off-hours
quotes are labelled CACHED so the report does not pretend an index moved
when only the last close is available.
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

# Yahoo Finance chart symbols for major indices
INDICES = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^IXIC",
    "Dow Jones": "^DJI",
    "TSX": "^GSPTSE",
    "Euro Stoxx 50": "^STOXX50E",
    "CAC 40": "^FCHI",
    "FTSE 100": "^FTSE",
    "Nikkei 225": "^N225",
    "Shanghai Composite": "000001.SS",
}

INDEX_COUNTRIES = {
    "S&P 500": "USA",
    "NASDAQ": "USA",
    "Dow Jones": "USA",
    "TSX": "Canada",
    "Euro Stoxx 50": "Eurozone",
    "CAC 40": "France",
    "FTSE 100": "UK",
    "Nikkei 225": "Japan",
    "Shanghai Composite": "China",
}

ETFS = {
    "XEQT": "iShares Core Equity ETF Portfolio",
    "VFV": "Vanguard S&P 500 Index ETF",
    "VCN": "Vanguard FTSE Canada All Cap Index ETF",
    "VDY": "Vanguard FTSE Canadian High Dividend Yield Index ETF",
    "HXQ": "Global X NASDAQ-100 Index Corporate Class ETF",
    "VEQT": "Vanguard All-Equity ETF Portfolio",
    "XGRO": "iShares Core Growth ETF Portfolio",
    "XBB": "iShares Core Canadian Universe Bond Index ETF",
    "XIU": "iShares S&P/TSX 60 Index ETF",
    "XRE": "iShares S&P/TSX Capped REIT Index ETF",
    "XEC": "iShares Core MSCI Emerging Markets IMI Index ETF",
    "XEF": "iShares Core MSCI EAFE IMI Index ETF",
    "VGRO": "Vanguard Growth ETF Portfolio",
}

# Approximate UTC open/close hours per index exchange (24h clock).
# Used only to label freshness; never to gate data collection.
EXCHANGE_HOURS = {
    "^GSPC": (14, 21),   # NYSE 09:30-16:00 ET
    "^IXIC": (14, 21),   # NASDAQ
    "^DJI": (14, 21),    # NYSE
    "^GSPTSE": (14, 21), # Toronto
    "^STOXX50E": (8, 16),# Eurex
    "^FCHI": (8, 16),    # Paris
    "^FTSE": (8, 16),    # London
    "^GDAXI": (8, 16),   # Frankfurt
    "^N225": (0, 6),     # Tokyo 09:00-15:00 JST
    "^HSI": (1, 8),      # Hong Kong
    "000001.SS": (1, 7), # Shanghai
    "^AXJO": (0, 6),     # Sydney
}


def _is_exchange_open(symbol: str, now: datetime | None = None) -> bool:
    hours = EXCHANGE_HOURS.get(symbol)
    if hours is None:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    open_h, close_h = hours
    hour = now.hour
    if open_h < close_h:
        return open_h <= hour < close_h
    # Overnight session (e.g. Tokyo): open from open_h to 24, then 0 to close_h
    return hour >= open_h or hour < close_h


@dataclass
class StockIndex:
    name: str
    symbol: str
    price: float
    change: float
    change_pct: float
    currency: str = "USD"
    timestamp: datetime | None = None
    freshness: Freshness = Freshness.LIVE

    @property
    def value(self) -> float:
        return self.price


def _parse_chart(data: dict, name: str, symbol: str) -> StockIndex | None:
    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta["previousClose"])
        change = price - prev
        pct = (change / prev) * 100 if prev else 0.0
        ts = meta.get("regularMarketTime")
        timestamp = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        return StockIndex(
            name=name,
            symbol=symbol,
            price=price,
            change=round(change, 2),
            change_pct=round(pct, 2),
            currency=meta.get("currency", "USD"),
            timestamp=timestamp,
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Failed to parse index %s: %s", symbol, exc)
        return None


async def _fetch_yahoo(symbol: str, name: str) -> StockIndex | DataUnavailable:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    data, freshness = await fetch_json(
        url, cache_key=f"stocks:{symbol}", ttl=900
    )
    if isinstance(data, DataUnavailable):
        return data
    index = _parse_chart(data, name, symbol)
    if index is None:
        return DataUnavailable(source=f"stocks:{symbol}", reason="Malformed response")
    index.freshness = freshness
    return index


async def _fetch_finnhub(symbol: str, name: str, api_key: str) -> StockIndex | DataUnavailable:
    """Fallback quote via Finnhub when Yahoo fails."""
    data, _ = await fetch_json(
        "https://finnhub.io/api/v1/quote",
        params={"symbol": symbol},
        headers={"X-Finnhub-API-Key": api_key},
        cache_key=f"finnhub:{symbol}",
        ttl=900,
    )
    if isinstance(data, DataUnavailable):
        return data
    try:
        price = float(data["c"])
        prev = float(data["pc"])
        change = price - prev
        pct = (change / prev) * 100 if prev else 0.0
        return StockIndex(
            name=name,
            symbol=symbol,
            price=price,
            change=round(change, 2),
            change_pct=round(pct, 2),
            currency="USD",
            freshness=Freshness.LIVE,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Failed to parse Finnhub quote for %s: %s", symbol, exc)
        return DataUnavailable(source=f"finnhub:{symbol}", reason=str(exc))


async def get_indices(
    api_key: str | None = None,
    symbols: list[str] | None = None,
) -> dict[str, StockIndex | DataUnavailable]:
    """Fetch a subset of indices. Default: all."""
    target = set(symbols) if symbols else None
    pairs = [
        (name, sym)
        for name, sym in INDICES.items()
        if target is None or sym in target
    ]
    tasks = [_fetch_index(name, sym, api_key) for name, sym in pairs]
    raw = await asyncio.gather(*tasks)
    return {name: value for (name, _sym), value in zip(pairs, raw)}


async def get_etfs() -> dict[str, StockIndex | DataUnavailable]:
    """Fetch the selected Canadian-listed ETFs from Yahoo Finance."""
    pairs = [
        (ticker, f"{ticker}.TO", name)
        for ticker, name in ETFS.items()
    ]
    tasks = [
        _fetch_yahoo(symbol, f"{ticker} | {name}")
        for ticker, symbol, name in pairs
    ]
    raw = await asyncio.gather(*tasks)
    return {ticker: value for (ticker, _symbol, _name), value in zip(pairs, raw)}


async def _fetch_index(name: str, symbol: str, api_key: str | None) -> StockIndex | DataUnavailable:
    primary = await _fetch_yahoo(symbol, name)
    if not isinstance(primary, DataUnavailable):
        return primary
    if api_key:
        return await _fetch_finnhub(symbol, name, api_key)
    return primary


async def get_index(symbol: str, api_key: str | None = None) -> StockIndex | DataUnavailable:
    """Fetch a single index by Yahoo symbol."""
    name = next((n for n, s in INDICES.items() if s == symbol), symbol)
    return await _fetch_index(name, symbol, api_key)