"""Government bond yields, the US curve, and the 2s10s spread.

Primary: Yahoo Finance index symbols for the US curve. Three of the four
are quoted as yield x 10 (^FVX, ^TNX, ^TYX) and are divided here so callers
always receive a true percentage; ^IRX (13-week bill) is already a percent.

Secondary: FRED (optional key) supplies the official constant-maturity
series, fills in the 2Y that Yahoo's index set does not carry cleanly, and
provides the international benchmarks.

The international series are OECD monthly long-term rates. They are real
numbers with a month-old date, so they are labelled STALE on purpose rather
than passed off as today's close.

The spread is derived from two yields we already fetched. It costs no
extra request, and computing it here keeps the recession-signal arithmetic
in one place instead of repeating it in the formatter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_json,
)

log = logging.getLogger(__name__)

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"

BOC_BENCHMARK_URL = (
    "https://www.bankofcanada.ca/valet/"
    "observations/group/bond_yields_benchmark/json"
)

CA_SERIES = {
    "2Y": "BD.CDN.2YR.DQ.YLD",
    "3Y": "BD.CDN.3YR.DQ.YLD",
    "5Y": "BD.CDN.5YR.DQ.YLD",
    "7Y": "BD.CDN.7YR.DQ.YLD",
    "10Y": "BD.CDN.10YR.DQ.YLD",
}

# Tenor -> (Yahoo symbol, quoted_as_x10)

# Official constant-maturity series. Used to fill 2Y and to backstop Yahoo.
US_FRED = {
    "2Y": "DGS2",
    "3Y": "DGS3",
    "5Y": "DGS5",
    "7Y": "DGS7",
    "10Y": "DGS10",
}

# ISO country -> FRED OECD monthly long-term (10Y) rate series id.
# Verified against the FRED browser before deploying; a wrong id returns
# HTTP 400, which degrades to DataUnavailable rather than crashing.
# US and Canadian government bond yields.

# Yields move in basis points over an hour; a 15-minute cache is plenty for
# a 07:00 briefing and keeps every provider well inside its free limit.
YIELD_TTL = 900.0


@dataclass
class YieldPoint:
    tenor: str
    country: str
    rate: float                      # annual yield, percent
    change_bp: float | None = None   # vs previous observation, basis points
    as_of: datetime | None = None
    source: str = "yahoo"
    freshness: Freshness = Freshness.LIVE

    @property
    def value(self) -> float:
        return self.rate


@dataclass
class CurveSpread:
    name: str
    short_tenor: str
    long_tenor: str
    spread_bp: float
    inverted: bool
    freshness: Freshness = Freshness.LIVE

    @property
    def spread_bps(self) -> float:
        return self.spread_bp

    @property
    def signal(self) -> str:
        return "Inverted" if self.inverted else "Normal"


def _parse_yahoo(data: dict, scaled: bool) -> tuple[float, float | None, datetime | None]:
    """Return (rate_percent, previous_rate_percent, quote_time) from a chart payload."""
    result = data["chart"]["result"][0]
    meta = result["meta"]
    divisor = 10.0 if scaled else 1.0
    rate = float(meta["regularMarketPrice"]) / divisor
    prev_raw = meta.get("previousClose")
    prev = float(prev_raw) / divisor if prev_raw is not None else None
    ts = meta.get("regularMarketTime")
    as_of = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
    return rate, prev, as_of


async def _yahoo_yield(tenor: str, symbol: str, scaled: bool) -> YieldPoint | DataUnavailable:
    data, freshness = await fetch_json(
        YAHOO_CHART.format(symbol=symbol),
        params={"range": "1d", "interval": "1d"},
        cache_key=f"bonds:us:{tenor}",
        ttl=YIELD_TTL,
    )
    if isinstance(data, DataUnavailable):
        return data
    try:
        rate, prev, as_of = _parse_yahoo(data, scaled)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("Malformed Yahoo yield payload for %s: %s", symbol, exc)
        return DataUnavailable(source=f"yahoo:{symbol}", reason="Malformed response")

    change_bp = round((rate - prev) * 100, 1) if prev is not None else None
    return YieldPoint(
        tenor=tenor,
        country="US",
        rate=round(rate, 3),
        change_bp=change_bp,
        as_of=as_of,
        source=f"yahoo:{symbol}",
        freshness=freshness,
    )


async def _fred_yield(series_id: str, api_key: str, country: str, tenor: str) -> YieldPoint | DataUnavailable:
    """One FRED series, last two observations, so the change is computable."""
    data, _ = await fetch_json(
        FRED_OBSERVATIONS,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": "2",
        },
        cache_key=f"bonds:fred:{series_id}",
        ttl=YIELD_TTL,
    )
    if isinstance(data, DataUnavailable):
        return data

    rows = [r for r in data.get("observations", []) if r.get("value") not in (None, ".")]
    if not rows:
        return DataUnavailable(source=f"fred:{series_id}", reason="No observations")

    try:
        rate = float(rows[0]["value"])
        as_of = datetime.strptime(rows[0]["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Unparseable FRED observation for %s: %s", series_id, exc)
        return DataUnavailable(source=f"fred:{series_id}", reason=str(exc))

    change_bp = None
    if len(rows) > 1:
        try:
            change_bp = round((rate - float(rows[1]["value"])) * 100, 1)
        except (KeyError, TypeError, ValueError):
            change_bp = None

    # Monthly series are stale by construction, not by fetch failure.
    monthly = series_id.endswith("M156N")
    return YieldPoint(
        tenor=tenor,
        country=country,
        rate=round(rate, 3),
        change_bp=change_bp,
        as_of=as_of,
        source=f"fred:{series_id}",
        freshness=Freshness.STALE if monthly else Freshness.LIVE,
    )


async def get_us_curve(
    fred_api_key: str | None = None,
) -> dict[str, YieldPoint | DataUnavailable]:

    if not fred_api_key:
        unavailable = DataUnavailable(
            source="fred",
            reason="No FRED_API_KEY configured",
        )
        return {tenor: unavailable for tenor in US_FRED}

    out = {}

    for tenor, series_id in US_FRED.items():
        out[tenor] = await _fred_yield(
            series_id,
            fred_api_key,
            "US",
            tenor,
        )

    return out

async def get_canada_curve() -> dict[str, YieldPoint | DataUnavailable]:
    """Fetch Canadian benchmark yields from Bank of Canada Valet."""

    data, freshness = await fetch_json(
        BOC_BENCHMARK_URL,
        params={"recent": "2"},
        cache_key="bonds:canada:benchmark",
        ttl=YIELD_TTL,
    )

    if isinstance(data, DataUnavailable):
        return {
            tenor: data
            for tenor in ["2Y", "3Y", "5Y", "7Y", "10Y"]
        }

    rows = sorted(
        data.get("observations", []),
        key=lambda row: row.get("d", ""),
        reverse=True,
    )

    out = {}

    for tenor, series_id in CA_SERIES.items():
        valid = []

        for row in rows:
            observation = row.get(series_id)

            if not isinstance(observation, dict):
                continue

            value = observation.get("v")

            if value in (None, "", "."):
                continue

            try:
                valid.append((
                    float(value),
                    datetime.strptime(
                        row["d"], "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc),
                ))
            except (ValueError, KeyError, TypeError):
                continue

        if not valid:
            out[tenor] = DataUnavailable(
                source=f"bankofcanada:{series_id}",
                reason="No observations available",
            )
            continue

        rate, as_of = valid[0]

        change_bp = None
        if len(valid) > 1:
            change_bp = round(
                (valid[0][0] - valid[1][0]) * 100,
                1,
            )

        out[tenor] = YieldPoint(
            tenor=tenor,
            country="CA",
            rate=round(rate, 3),
            change_bp=change_bp,
            as_of=as_of,
            source=f"bankofcanada:{series_id}",
            freshness=freshness,
        )

    return out

def compute_spread(curve: dict[str, YieldPoint | DataUnavailable]) -> CurveSpread | DataUnavailable:
    """2s10s in basis points, from whatever the curve fetch returned."""
    short = curve.get("2Y")
    long = curve.get("10Y")
    if not isinstance(short, YieldPoint) or not isinstance(long, YieldPoint):
        missing = "2Y" if not isinstance(short, YieldPoint) else "10Y"
        return DataUnavailable(source="bonds:spread", reason=f"{missing} unavailable")

    spread_bp = round((long.rate - short.rate) * 100, 1)
    worst = Freshness.LIVE
    for point in (short, long):
        if point.freshness is Freshness.STALE:
            worst = Freshness.STALE
        elif point.freshness is Freshness.CACHED and worst is Freshness.LIVE:
            worst = Freshness.CACHED

    return CurveSpread(
        name="2s10s",
        short_tenor="2Y",
        long_tenor="10Y",
        spread_bp=spread_bp,
        inverted=spread_bp < 0,
        freshness=worst,
    )


async def get_all(
    fred_api_key: str | None = None,
) -> dict[str, object]:

    curve = await get_us_curve(fred_api_key)
    canada = await get_canada_curve()

    return {
        "us_curve": curve,
        "canada_curve": canada,
        "spread": compute_spread(curve),
        "canada_spread": compute_spread(canada),
    }
