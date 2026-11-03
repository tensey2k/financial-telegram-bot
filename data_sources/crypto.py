"""Cryptocurrency: bitcoin, ethereum, monero, and solana.

Primary: CoinGecko (free, optional Demo key for higher rate limits).
The keyless public endpoint works; a Demo key simply raises the rate limit
and stabilises market-cap and volume fields.

All crypto trades 24/7, so quotes are always LIVE when the fetch succeeds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_json,
)

log = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

COINS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XMR": "monero",
}

COIN_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XMR": "Monero",
}


@dataclass
class CryptoQuote:
    symbol: str
    name: str
    price: float
    change_24h_pct: float
    market_cap: float | None = None
    volume_24h: float | None = None
    currency: str = "USD"
    freshness: Freshness = Freshness.LIVE


async def _fetch_simple_price(api_key: str | None) -> tuple[dict[str, Any] | DataUnavailable, Freshness]:
    params = {
        "ids": ",".join(COINS.values()),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "precision": "2",
    }
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-demo-key"] = api_key
    data, freshness = await fetch_json(
        COINGECKO_URL,
        params=params,
        headers=headers,
        cache_key="crypto:simple",
        ttl=300,
    )
    return data, freshness


async def _fetch_markets(api_key: str | None) -> dict[str, Any] | DataUnavailable:
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-cg-demo-key"] = api_key
    data, _ = await fetch_json(
        COINGECKO_MARKETS_URL,
        params={
            "vs_currency": "usd",
            "ids": ",".join(COINS.values()),
            "order": "market_cap_desc",
            "sparkline": "false",
            "price_change_percentage": "24h",
        },
        headers=headers,
        cache_key="crypto:markets",
        ttl=300,
    )
    if isinstance(data, DataUnavailable):
        return data
    return data


def _parse_simple(simple: dict[str, Any], freshness: Freshness) -> dict[str, CryptoQuote | DataUnavailable]:
    """Parse the CoinGecko simple-price response into per-symbol quotes."""
    result: dict[str, CryptoQuote | DataUnavailable] = {}
    for symbol, coin_id in COINS.items():
        entry = simple.get(coin_id)
        if not isinstance(entry, dict):
            result[symbol] = DataUnavailable(
                source=f"crypto:{symbol}", reason="Missing from response"
            )
            continue
        try:
            result[symbol] = CryptoQuote(
                symbol=symbol,
                name=COIN_NAMES[symbol],
                price=float(entry.get("usd", 0.0)),
                change_24h_pct=float(entry.get("usd_24h_change", 0.0)),
                market_cap=float(entry.get("usd_market_cap", 0.0)) or None,
                volume_24h=float(entry.get("usd_24h_vol", 0.0)) or None,
                freshness=freshness,
            )
        except (TypeError, ValueError) as exc:
            log.warning("Failed to parse crypto quote for %s: %s", symbol, exc)
            result[symbol] = DataUnavailable(
                source=f"crypto:{symbol}", reason=str(exc)
            )
    return result


async def get_crypto_quotes(api_key: str | None = None) -> dict[str, CryptoQuote | DataUnavailable]:
    """Fetch BTC/ETH/SOL/XMR quotes with market data and freshness."""
    simple, freshness = await _fetch_simple_price(api_key)
    if isinstance(simple, DataUnavailable):
        return {sym: simple for sym in COINS}
    return _parse_simple(simple, freshness)