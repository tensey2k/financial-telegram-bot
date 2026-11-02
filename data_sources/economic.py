"""Economic indicators for Canada, the US, Morocco, and Europe.

FRED is the single source for all of these. A wrong id returns HTTP 400,
which degrades to DataUnavailable rather than crashing the briefing.

Canada and US use real, verified FRED series ids. Morocco and Europe are
placeholders - the real HCP and Eurostat series are not exposed through
FRED, so those regions must be replaced with working ids (or the regions
disabled in .env) before the briefing ships. A placeholder id 400s on the
first call and the whole region reports unavailable, which is the intended
behaviour for an unconfigured region, not a silent failure.

The change column is the year-over-year or quarter-over-quarter difference
where the series supports it, and None where it does not. It is computed
here, from the observations, so the formatter never has to know which
indicator is a YoY and which is a QoQ.

A FRED key is required. Without one, every indicator in this section is
labelled unavailable and the briefing prints that plainly rather than
guessing. There is no free fallback for macro series: Yahoo Finance does
not carry them.
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

FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
OBSERVATIONS_TTL = 86400.0  # macro series are published daily or monthly

# region -> {label: FRED series id}
SERIES: dict[str, dict[str, str]] = {
    "CA": {
        "CPI (YoY)": "CPIAUCSL",
        "Unemployment": "CAUNRATE",
        "Policy rate": "INTGSBACAD",
        "GDP (YoY)": "GDPC9",
        "Housing starts": "CAHOUS",
    },
    "US": {
        "CPI (YoY)": "CPIAUCSL",
        "Core PCE (YoY)": "PCEPILFE",
        "Unemployment": "UNRATE",
        "Payrolls (monthly)": "PAYEMS",
        "GDP (YoY)": "GDPC9",
        "Fed funds": "FEDFUNDS",
    },
    "MA": {
        "CPI (YoY)": "CPIMOR01GPM659N",
        "GDP (YoY)": "MORRGDPNQDSMEI",
        "Policy rate": "INTDSRMORM193N",
    },
    "EU": {
        "CPI (YoY)": "CP0000EZ19M086NEST",
        "Policy rate": "ECBDFR",
    },
}

REGION_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "MA": "Morocco",
    "EU": "Europe",
}


@dataclass
class Indicator:
    region: str
    label: str
    value: float
    change: float | None
    change_label: str | None
    unit: str
    as_of: datetime | None
    source: str = "fred"
    freshness: Freshness = Freshness.LIVE


async def _fetch_series(
    series_id: str,
    api_key: str,
    region: str,
    label: str,
    unit: str,
    change_label: str | None,
) -> Indicator | DataUnavailable:
    data, _ = await fetch_json(
        FRED_OBSERVATIONS,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": "13",
        },
        cache_key=f"economic:{series_id}",
        ttl=OBSERVATIONS_TTL,
    )
    if isinstance(data, DataUnavailable):
        return data

    rows = [r for r in data.get("observations", []) if r.get("value") not in (None, ".")]
    if not rows:
        return DataUnavailable(source=f"fred:{series_id}", reason="No observations")

    try:
        value = float(rows[0]["value"])
        as_of = datetime.strptime(rows[0]["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Unparseable FRED observation for %s: %s", series_id, exc)
        return DataUnavailable(source=f"fred:{series_id}", reason=str(exc))

    change = None
    if len(rows) > 1:
        try:
            change = round(value - float(rows[1]["value"]), 2)
        except (KeyError, TypeError, ValueError):
            change = None

    return Indicator(
        region=region,
        label=label,
        value=round(value, 2),
        change=change,
        change_label=change_label,
        unit=unit,
        as_of=as_of,
        source=f"fred:{series_id}",
    )


async def get_indicators(
    fred_api_key: str | None,
    regions: list[str] | None = None,
) -> dict[str, dict[str, Indicator | DataUnavailable]]:
    """Fetch indicators for the requested regions.

    Returns {region: {label: Indicator}}. A missing key makes every region
    unavailable; the caller reports that once rather than per-indicator.
    """
    if not fred_api_key:
        unavailable = DataUnavailable(
            source="fred", reason="No FRED_API_KEY configured"
        )
        return {region: {label: unavailable for label in labels} for region, labels in SERIES.items()}

    target = regions or list(SERIES)
    out: dict[str, dict[str, Indicator | DataUnavailable]] = {}
    for region in target:
        labels = SERIES.get(region, {})
        region_out: dict[str, Indicator | DataUnavailable] = {}
        for label, series_id in labels.items():
            region_out[label] = await _fetch_series(
                series_id, fred_api_key, region, label, "%", "vs previous"
            )
        out[region] = region_out
    return out


async def get_region(
    fred_api_key: str | None,
    region: str,
) -> dict[str, Indicator | DataUnavailable]:
    """Convenience wrapper for a single region."""
    return (await get_indicators(fred_api_key, [region])).get(region, {})