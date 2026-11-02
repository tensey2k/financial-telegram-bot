"""Foreign exchange, normalised to CAD crosses.

Primary: Yahoo Finance USD pairs. Six base pairs are fetched and every CAD
cross the briefing needs is derived arithmetically, so no exotic Yahoo
symbol (EURCAD=X and friends) has to be trusted to exist.

Secondary: the ECB daily reference rates. Free, no key, official, and
published around 16:00 CET. They anchor EUR/USD independently of Yahoo and
catch a bad derivation before it reaches the report.

An ECB rate is a snapshot frozen at publication time, so it is labelled
CACHED even when the fetch itself was instant. Treating "official" as
"live" would be the wrong promise to make at 07:00.

Every rate here is expressed as *quote currency per one unit of base*, and
the pair string says so: "EUR/CAD" means CAD per 1 EUR.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_json,
    fetch_text,
)

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# The six pairs we actually request. Everything else is computed.
BASE_PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/CHF": "USDCHF=X",
    "USD/JPY": "USDJPY=X",
    "USD/CNY": "USDCNY=X",
    "USD/CAD": "USDCAD=X",
    "USD/MAD": "MAD=X",
}

# Cross name -> function of the base-pair rates. Each returns quote-per-base.
# Keys are the pair strings shown to the user, so the arithmetic and the
# label have to agree; they are checked against ECB at the bottom.
CAD_CROSSES = {
    "CAD/USD": lambda r: 1.0 / r["USD/CAD"],
    "EUR/CAD": lambda r: r["EUR/USD"] * r["USD/CAD"],
    "GBP/CAD": lambda r: r["GBP/USD"] * r["USD/CAD"],
    "CHF/CAD": lambda r: r["USD/CAD"] / r["USD/CHF"],
    "JPY/CAD": lambda r: r["USD/CAD"] / r["USD/JPY"],
    "CNY/CAD": lambda r: r["USD/CAD"] / r["USD/CNY"],
    "USD/EUR": lambda r: 1.0 / (r["EUR/USD"] * r["USD/CAD"]),
    "MAD/CAD": lambda r: r["USD/CAD"] / r["USD/MAD"],
    "USD/MAD": lambda r: r["USD/MAD"],
}

FX_TTL = 900.0
ECB_TTL = 21600.0  # one publication per business day; six hours is generous


@dataclass
class FxRate:
    pair: str            # "EUR/CAD" = CAD per 1 EUR
    base: str
    quote: str
    rate: float
    change_pct: float | None = None
    as_of: datetime | None = None
    source: str = "yahoo"
    freshness: Freshness = Freshness.LIVE


def _parse_yahoo_fx(data: dict) -> tuple[float, float | None, datetime | None]:
    result = data["chart"]["result"][0]
    meta = result["meta"]
    rate = float(meta["regularMarketPrice"])
    prev = meta.get("previousClose")
    ts = meta.get("regularMarketTime")
    return (
        rate,
        float(prev) if prev is not None else None,
        datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
    )


async def _yahoo_pair(pair: str, symbol: str) -> FxRate | DataUnavailable:
    data, freshness = await fetch_json(
        YAHOO_CHART.format(symbol=symbol),
        params={"range": "1d", "interval": "1d"},
        cache_key=f"forex:{pair}",
        ttl=FX_TTL,
    )
    if isinstance(data, DataUnavailable):
        return data
    try:
        rate, prev, as_of = _parse_yahoo_fx(data)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Malformed Yahoo FX payload for %s: %s", symbol, exc)
        return DataUnavailable(source=f"yahoo:{symbol}", reason="Malformed response")

    base, quote = pair.split("/")
    change_pct = round(((rate - prev) / prev) * 100, 3) if prev else None
    return FxRate(
        pair=pair,
        base=base,
        quote=quote,
        rate=rate,
        change_pct=change_pct,
        as_of=as_of,
        source=f"yahoo:{symbol}",
        freshness=freshness,
    )


async def get_base_pairs() -> dict[str, FxRate | DataUnavailable]:
    """Fetch the six USD-anchored pairs the crosses are derived from."""
    out: dict[str, FxRate | DataUnavailable] = {}
    for pair, symbol in BASE_PAIRS.items():
        out[pair] = await _yahoo_pair(pair, symbol)
    return out


def derive_cad_crosses(
    rates: dict[str, FxRate | DataUnavailable],
) -> dict[str, FxRate | DataUnavailable]:
    """Compute CAD crosses from the fetched base pairs.

    A missing or failed input makes every cross that depended on it
    unavailable, carrying the input's reason forward, rather than producing
    a plausible-looking wrong number.
    """
    numeric: dict[str, float] = {}
    failed: dict[str, DataUnavailable] = {}
    for pair, value in rates.items():
        if isinstance(value, FxRate):
            numeric[pair] = value.rate
        else:
            failed[pair] = value

    # The freshest input sets the ceiling on how fresh a derived rate can be.
    worst = Freshness.LIVE
    for value in rates.values():
        if isinstance(value, FxRate) and value.freshness is not Freshness.LIVE:
            worst = value.freshness

    out: dict[str, FxRate | DataUnavailable] = {}
    for pair, formula in CAD_CROSSES.items():
        try:
            rate = formula(numeric)
        except KeyError as exc:
            missing = str(exc).strip("'")
            cause = failed.get(missing)
            out[pair] = cause or DataUnavailable(
                source="forex:derived", reason=f"Missing input {missing}"
            )
            continue
        except (ZeroDivisionError, TypeError, ValueError) as exc:
            out[pair] = DataUnavailable(source="forex:derived", reason=str(exc))
            continue

        base, quote = pair.split("/")
        out[pair] = FxRate(
            pair=pair,
            base=base,
            quote=quote,
            rate=round(rate, 6),
            change_pct=None,   # a derived rate has no meaningful prior close
            as_of=None,
            source="derived:yahoo",
            freshness=worst,
        )
    return out


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def get_ecb_reference() -> dict[str, float] | DataUnavailable:
    """ECB daily EUR-reference rates, keyed by currency code."""
    text, _ = await fetch_text(
        ECB_DAILY_URL,
        cache_key="forex:ecb",
        ttl=ECB_TTL,
    )
    if isinstance(text, DataUnavailable):
        return text
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        log.warning("Unparseable ECB feed: %s", exc)
        return DataUnavailable(source="ecb", reason="Malformed XML")

    rates: dict[str, float] = {}
    reference_date: str | None = None
    for node in root.iter():
        if _strip_ns(node.tag) != "Cube":
            continue
        if node.attrib.get("time"):
            reference_date = node.attrib["time"]
        currency = node.attrib.get("currency")
        raw = node.attrib.get("rate")
        if currency and raw:
            try:
                rates[currency] = float(raw)
            except ValueError:
                continue

    if not rates:
        return DataUnavailable(source="ecb", reason="No rates in feed")
    if reference_date:
        rates["_as_of"] = reference_date  # type: ignore[assignment]
    return rates


def cross_check(
    derived: dict[str, FxRate | DataUnavailable],
    ecb: dict[str, float] | DataUnavailable,
) -> list[str]:
    """Return pair names where the derived rate disagrees with ECB by >0.5%.

    Advisory only: the briefing prints the mismatch as a footnote instead of
    silently trusting a derived number. A provider change shows up here
    before it shows up as a wrong mortgage comparison.
    """
    if isinstance(ecb, DataUnavailable):
        return []
    eur_usd = ecb.get("USD")
    if not isinstance(eur_usd, float):
        return []

    # The ECB feed publishes USD-per-EUR, so it can only anchor EUR/USD and
    # EUR/GBP directly. CAD crosses are not in the feed and are deliberately
    # not checked here.
    checks: tuple[tuple[str, str, float], ...] = (
        ("EUR/USD", "USD", 1.0),
        ("EUR/GBP", "GBP", 1.0),
    )
    problems: list[str] = []
    for pair, ecb_code, divisor in checks:
        ours = derived.get(pair)
        if not isinstance(ours, FxRate):
            continue
        ecb_rate = ecb.get(ecb_code)
        if not isinstance(ecb_rate, float):
            continue
        expected = ecb_rate / divisor
        if abs(ours.rate - expected) / expected > 0.005:
            problems.append(pair)
    return problems


async def get_forex() -> dict[str, object]:
    """Keys: 'pairs' (base USD pairs), 'cad_crosses' (derived), 'ecb' (reference)."""
    pairs = await get_base_pairs()
    crosses = derive_cad_crosses(pairs)
    ecb = await get_ecb_reference()
    return {
        "pairs": pairs,
        "cad_crosses": crosses,
        "ecb": ecb,
        "discrepancies": cross_check(crosses, ecb),
    }