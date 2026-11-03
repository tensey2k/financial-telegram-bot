"""Economic indicators for Canada, the US, Morocco, and Europe."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from data_sources.base import DataUnavailable, Freshness, fetch_json, fetch_text

log = logging.getLogger(__name__)

FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
BOC_OBSERVATIONS = "https://www.bankofcanada.ca/valet/observations"
STATCAN_LATEST = (
    "https://www150.statcan.gc.ca/t1/wds/rest/"
    "getDataFromCubePidCoordAndLatestNPeriods"
)

OBSERVATIONS_TTL = 86400.0

# FRED series used by the regions not yet migrated to their official sources.
# Canada's FRED indicators are limited to verified unemployment and GDP series.
SERIES: dict[str, dict[str, str]] = {
    "CA": {
        "Unemployment": "LRUNTTTTCAM156S",
        "GDP (YoY)": "CANGDPRQPSMEI",
    },
    "US": {
        "CPI (YoY)": "CPIAUCSL",
        "Core PCE (YoY)": "PCEPILFE",
        "Unemployment": "UNRATE",
        "Payrolls (monthly)": "PAYEMS",
        "GDP (YoY)": "GDPC1",
        "Fed funds": "FEDFUNDS",
    },
    "MA": {
        "CPI (YoY)": "CPIMOR01GPM659N",
        "GDP (YoY)": "MORRGDPNQDSMEI",
        "Policy rate": "INTDSRMORM193N",
    },
    "EU": {},
}

REGION_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "MA": "Morocco",
    "EU": "Eurozone",
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
    source: str = "unknown"
    freshness: Freshness = Freshness.LIVE


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value[:10], "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )


def _unavailable(source: str, reason: str) -> DataUnavailable:
    return DataUnavailable(source=source, reason=reason)


async def _fetch_fred_series(
    series_id: str,
    api_key: str | None,
    region: str,
    label: str,
    unit: str,
    change_label: str,
) -> Indicator | DataUnavailable:
    if not api_key:
        return _unavailable("fred", "No FRED API key configured")

    data, freshness = await fetch_json(
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
        # Do not expose request URLs, which may contain the API key.
        return _unavailable(f"fred:{series_id}", "FRED request failed")

    rows = [
        row for row in data.get("observations", [])
        if row.get("value") not in (None, ".")
    ]
    rows.sort(key=lambda row: row.get("date", ""), reverse=True)

    if not rows:
        return _unavailable(f"fred:{series_id}", "No observations available")

    try:
        value = float(rows[0]["value"])
        as_of = _parse_date(rows[0]["date"])
        change = (
            round(value - float(rows[1]["value"]), 2)
            if len(rows) > 1 else None
        )
    except (KeyError, TypeError, ValueError):
        log.warning("Could not parse FRED observations for %s", series_id)
        return _unavailable(f"fred:{series_id}", "Invalid observation data")

    return Indicator(
        region=region,
        label=label,
        value=round(value, 2),
        change=change,
        change_label=change_label,
        unit=unit,
        as_of=as_of,
        source=f"fred:{series_id}",
        freshness=freshness,
    )


async def _fetch_fred_yoy(
    series_id: str,
    api_key: str | None,
    label: str,
    quarterly: bool = False,
) -> Indicator | DataUnavailable:
    """Calculate year-over-year growth from a FRED index or GDP level."""
    if not api_key:
        return _unavailable("fred", "No FRED API key configured")

    limit = 30 if not quarterly else 12

    data, freshness = await fetch_json(
        FRED_OBSERVATIONS,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": str(limit),
        },
        cache_key=f"economic:{series_id}:yoy",
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable(f"fred:{series_id}", "FRED request failed")

    rows = [
        row for row in data.get("observations", [])
        if row.get("value") not in (None, ".")
    ]
    rows.sort(key=lambda row: row.get("date", ""), reverse=True)

    try:
        if not rows:
            return _unavailable(f"fred:{series_id}", "No observations available")

        current_date = datetime.strptime(rows[0]["date"][:10], "%Y-%m-%d")
        year_ago_date = current_date.replace(year=current_date.year - 1)
        months_back = 3 if quarterly else 1
        previous_month_index = (
            current_date.year * 12 + current_date.month - 1 - months_back
        )
        previous_date = datetime(
            previous_month_index // 12,
            previous_month_index % 12 + 1,
            1,
        )

        def find_nearest(target):
            candidates = [
                row for row in rows
                if datetime.strptime(row["date"][:10], "%Y-%m-%d") <= target
            ]
            return candidates[0] if candidates else None

        current_row = rows[0]
        year_ago_row = find_nearest(year_ago_date)
        previous_row = find_nearest(previous_date)

        if year_ago_row is None:
            return _unavailable(f"fred:{series_id}", "No year-ago observation available")

        current = float(current_row["value"])
        year_ago = float(year_ago_row["value"])

        if year_ago == 0:
            raise ValueError("Zero year-ago comparison value")

        value = (current / year_ago - 1) * 100
        change = None

        if previous_row is not None:
            previous_row_date = datetime.strptime(previous_row["date"][:10], "%Y-%m-%d")
            previous_year_date = previous_row_date.replace(year=previous_row_date.year - 1)
            previous_year_row = find_nearest(previous_year_date)

            if previous_year_row is not None:
                previous = float(previous_row["value"])
                previous_year_ago = float(previous_year_row["value"])
                if previous_year_ago != 0:
                    previous_yoy = (previous / previous_year_ago - 1) * 100
                    change = round(value - previous_yoy, 3)

        as_of = _parse_date(current_row["date"])
    except (KeyError, TypeError, ValueError):
        log.warning("Could not calculate YoY for FRED series %s", series_id)
        return _unavailable(f"fred:{series_id}", "Invalid YoY observation data")

    return Indicator(
        region="US",
        label=label,
        value=round(value, 2),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous quarter" if quarterly else "pp vs previous month",
        unit="%",
        as_of=as_of,
        source=f"fred:{series_id}",
        freshness=freshness,
    )


async def _fetch_us_payrolls(
    api_key: str | None,
) -> Indicator | DataUnavailable:
    """Return the monthly change in nonfarm payrolls, in thousands of jobs."""
    if not api_key:
        return _unavailable("fred", "No FRED API key configured")

    data, freshness = await fetch_json(
        FRED_OBSERVATIONS,
        params={
            "series_id": "PAYEMS",
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": "3",
        },
        cache_key="economic:PAYEMS:monthly-change",
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable("fred:PAYEMS", "FRED request failed")

    rows = [
        row for row in data.get("observations", [])
        if row.get("value") not in (None, ".")
    ]
    rows.sort(key=lambda row: row.get("date", ""), reverse=True)

    if len(rows) < 3:
        return _unavailable("fred:PAYEMS", "Insufficient observations")

    try:
        value = float(rows[0]["value"]) - float(rows[1]["value"])
        previous_value = float(rows[1]["value"]) - float(rows[2]["value"])
        change = value - previous_value
        as_of = _parse_date(rows[0]["date"])
    except (KeyError, TypeError, ValueError):
        return _unavailable("fred:PAYEMS", "Invalid payroll observation data")

    return Indicator(
        region="US",
        label="Payrolls (monthly)",
        value=round(value, 1),
        change=round(change, 1),
        change_label="thousand jobs vs previous month",
        unit=" thousand jobs",
        as_of=as_of,
        source="fred:PAYEMS",
        freshness=freshness,
    )



EUROSTAT_BASE = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
)
ECB_DATA_BASE = "https://data-api.ecb.europa.eu/service/data/"


def _europe_period_date(period: str) -> datetime:
    """Convert Eurostat monthly or quarterly periods to a UTC date."""
    if "-Q" in period:
        year, quarter = period.split("-Q")
        month = (int(quarter) - 1) * 3 + 1
        return datetime(int(year), month, 1, tzinfo=timezone.utc)

    return datetime.strptime(period, "%Y-%m").replace(
        day=1, tzinfo=timezone.utc
    )


async def _fetch_eurostat_rows(
    dataset: str,
    params: dict[str, str],
    cache_key: str,
) -> tuple[list[tuple[str, float]], Freshness] | DataUnavailable:
    """Fetch a single Eurostat time series from its JSON-stat response."""
    data, freshness = await fetch_json(
        f"{EUROSTAT_BASE}{dataset}",
        params=params,
        cache_key=cache_key,
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable(f"eurostat:{dataset}", "Eurostat request failed")

    try:
        time_index = (
            data["dimension"]["time"]["category"]["index"]
        )
        values = data["value"]
        rows = []

        for period, index in time_index.items():
            if isinstance(values, dict):
                raw_value = values.get(str(index))
            else:
                raw_value = values[int(index)]

            if raw_value is not None:
                rows.append((period, float(raw_value)))

        rows.sort(key=lambda row: row[0])
        if not rows:
            return _unavailable(
                f"eurostat:{dataset}", "No observations available"
            )

        return rows, freshness

    except (KeyError, IndexError, TypeError, ValueError):
        log.warning("Could not parse Eurostat dataset %s", dataset)
        return _unavailable(
            f"eurostat:{dataset}", "Invalid Eurostat observation data"
        )


async def _fetch_ecb_csv_rows(
    series_path: str,
    cache_key: str,
    observations: int = 5,
) -> tuple[list[tuple[str, float]], Freshness] | DataUnavailable:
    """Fetch and parse observations from an ECB CSV data series."""
    url = f"{ECB_DATA_BASE}{series_path}"
    data, freshness = await fetch_text(
        url,
        params={
            "format": "csvdata",
            "detail": "dataonly",
            "lastNObservations": str(observations),
        },
        headers={"Accept": "text/csv"},
        cache_key=cache_key,
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable(f"ecb:{cache_key}", "ECB request failed")

    try:
        reader = csv.DictReader(io.StringIO(data))
        rows = [
            (row["TIME_PERIOD"], float(row["OBS_VALUE"]))
            for row in reader
            if row.get("TIME_PERIOD") and row.get("OBS_VALUE") not in ("", None)
        ]
        rows.sort(key=lambda row: row[0])

        if not rows:
            return _unavailable(
                f"ecb:{cache_key}", "No ECB observations available"
            )

        return rows, freshness

    except (KeyError, TypeError, ValueError):
        log.warning("Could not parse ECB series %s", cache_key)
        return _unavailable(f"ecb:{cache_key}", "Invalid ECB observation data")


async def _fetch_europe_hicp() -> Indicator | DataUnavailable:
    """Fetch Eurozone annual HICP inflation from the ECB."""
    result = await _fetch_ecb_csv_rows(
        "HICP/M.U2.N.000000.4D0.ANR",
        "economic:ecb:eurozone-hicp",
        observations=5,
    )
    if isinstance(result, DataUnavailable):
        return result

    rows, freshness = result
    value = rows[-1][1]
    change = value - rows[-2][1] if len(rows) > 1 else None

    return Indicator(
        region="EU",
        label="HICP inflation (YoY)",
        value=round(value, 2),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous month",
        unit="%",
        as_of=_europe_period_date(rows[-1][0]),
        source="ecb:hicp",
        freshness=freshness,
    )


async def _fetch_europe_policy_rate() -> Indicator | DataUnavailable:
    """Fetch the ECB deposit facility rate."""
    result = await _fetch_ecb_csv_rows(
        "FM/B.U2.EUR.4F.KR.DFR.LEV",
        "economic:ecb:deposit-facility-rate",
        observations=5,
    )
    if isinstance(result, DataUnavailable):
        return result

    rows, freshness = result
    value = rows[-1][1]
    change = value - rows[-2][1] if len(rows) > 1 else None

    return Indicator(
        region="EU",
        label="ECB deposit facility rate",
        value=round(value, 3),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous observation",
        unit="%",
        as_of=_parse_date(rows[-1][0]),
        source="ecb:deposit-facility-rate",
        freshness=freshness,
    )


async def _fetch_europe_unemployment() -> Indicator | DataUnavailable:
    """Fetch the Eurozone monthly unemployment rate from Eurostat."""
    result = await _fetch_eurostat_rows(
        "une_rt_m",
        {
            "freq": "M",
            "unit": "PC_ACT",
            "age": "TOTAL",
            "sex": "T",
            "geo": "EA21",
            "sinceTimePeriod": "2025-01",
        },
        "economic:eurostat:eurozone-unemployment",
    )
    if isinstance(result, DataUnavailable):
        return result

    rows, freshness = result
    value = rows[-1][1]
    change = value - rows[-2][1] if len(rows) > 1 else None

    return Indicator(
        region="EU",
        label="Unemployment",
        value=round(value, 2),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous month",
        unit="%",
        as_of=_europe_period_date(rows[-1][0]),
        source="eurostat:une_rt_m",
        freshness=freshness,
    )


async def _fetch_europe_gdp() -> Indicator | DataUnavailable:
    """Fetch Eurozone year-over-year GDP growth and its quarterly change."""
    result = await _fetch_eurostat_rows(
        "namq_10_gdp",
        {
            "freq": "Q",
            "unit": "CLV_PCH_SM",
            "s_adj": "SCA",
            "na_item": "B1GQ",
            "geo": "EA20",
            "sinceTimePeriod": "2024-Q1",
        },
        "economic:eurostat:eurozone-gdp",
    )
    if isinstance(result, DataUnavailable):
        return result

    rows, freshness = result
    value = rows[-1][1]
    change = value - rows[-2][1] if len(rows) > 1 else None

    return Indicator(
        region="EU",
        label="GDP growth (YoY)",
        value=round(value, 2),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous quarter",
        unit="%",
        as_of=_europe_period_date(rows[-1][0]),
        source="eurostat:namq_10_gdp",
        freshness=freshness,
    )


async def _fetch_europe_industrial_production() -> Indicator | DataUnavailable:
    """Calculate Eurozone industrial production growth from its index."""
    result = await _fetch_eurostat_rows(
        "sts_inpr_m",
        {
            "freq": "M",
            "indic_bt": "PRD",
            "nace_r2": "B-D",
            "s_adj": "SCA",
            "unit": "I21",
            "geo": "EA20",
            "sinceTimePeriod": "2025-01",
        },
        "economic:eurostat:eurozone-industrial-production",
    )
    if isinstance(result, DataUnavailable):
        return result

    rows, freshness = result
    values = dict(rows)

    def year_ago_period(period: str) -> str:
        year, month = period.split("-")
        return f"{int(year) - 1:04d}-{month}"

    latest_period, latest_index = rows[-1]
    previous_period, previous_index = rows[-2] if len(rows) > 1 else (None, None)
    year_ago = values.get(year_ago_period(latest_period))

    if year_ago is None or year_ago == 0:
        return _unavailable(
            "eurostat:sts_inpr_m", "Insufficient year-over-year observations"
        )

    current_yoy = (latest_index / year_ago - 1) * 100
    change = None

    if previous_period is not None:
        previous_year_ago = values.get(year_ago_period(previous_period))
        if previous_year_ago not in (None, 0):
            previous_yoy = (previous_index / previous_year_ago - 1) * 100
            change = current_yoy - previous_yoy

    return Indicator(
        region="EU",
        label="Industrial production (YoY)",
        value=round(current_yoy, 2),
        change=round(change, 3) if change is not None else None,
        change_label="pp vs previous month",
        unit="%",
        as_of=_europe_period_date(latest_period),
        source="eurostat:sts_inpr_m",
        freshness=freshness,
    )


async def _get_europe() -> dict[str, Indicator | DataUnavailable]:
    """Fetch key Eurozone economic indicators from official sources."""
    jobs = [
        _fetch_europe_hicp(),
        _fetch_europe_policy_rate(),
        _fetch_europe_unemployment(),
        _fetch_europe_gdp(),
        _fetch_europe_industrial_production(),
    ]
    labels = [
        "HICP inflation (YoY)",
        "ECB deposit facility rate",
        "Unemployment",
        "GDP growth (YoY)",
        "Industrial production (YoY)",
    ]
    results = await asyncio.gather(*jobs)
    return dict(zip(labels, results))


async def _fetch_world_bank_annual(
    indicator_id: str,
    label: str,
    unit: str = "%",
    scale: float = 1.0,
    change_label: str = "pp vs previous year",
) -> Indicator | DataUnavailable:
    """Fetch an annual Morocco indicator from the World Bank API."""
    url = (
        f"https://api.worldbank.org/v2/country/MAR/"
        f"indicator/{indicator_id}"
    )
    data, freshness = await fetch_json(
        url,
        params={"format": "json", "per_page": "5"},
        cache_key=f"economic:worldbank:MAR:{indicator_id}",
        ttl=OBSERVATIONS_TTL,
    )

    source = f"world-bank:{indicator_id}"
    if isinstance(data, DataUnavailable):
        return _unavailable(source, "World Bank request failed")

    try:
        records = data[1]
        rows = [
            row for row in records
            if row.get("value") is not None
        ]
        rows.sort(key=lambda row: int(row["date"]), reverse=True)

        if not rows:
            return _unavailable(source, "No annual observations available")

        value = float(rows[0]["value"]) / scale
        as_of = _parse_date(f"{rows[0]['date']}-01-01")
        change = (
            (float(rows[0]["value"]) - float(rows[1]["value"])) / scale
            if len(rows) > 1 else None
        )
    except (IndexError, KeyError, TypeError, ValueError):
        log.warning("Could not parse World Bank data for %s", indicator_id)
        return _unavailable(source, "Invalid World Bank observation data")

    return Indicator(
        region="MA",
        label=label,
        value=round(value, 2),
        change=round(change, 3) if change is not None else None,
        change_label=change_label,
        unit=unit,
        as_of=as_of,
        source=source,
        freshness=freshness,
    )

async def _fetch_bis_policy_rate() -> Indicator | DataUnavailable:
    """Fetch Morocco's central-bank policy rate from the BIS."""
    url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.MA"
    source = "bis:morocco-policy-rate"

    data, freshness = await fetch_json(
        url,
        params={"lastNObservations": "3"},
        headers={
            "Accept": "application/vnd.sdmx.data+json;version=1.0.0"
        },
        cache_key="economic:bis:morocco-policy-rate",
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable(source, "BIS request failed")

    try:
        dataset = data["data"]["dataSets"][0]
        dimensions = data["data"]["structure"]["dimensions"]["observation"]

        time_dimension = next(
            dimension for dimension in dimensions
            if dimension.get("id") == "TIME_PERIOD"
        )
        periods = time_dimension["values"]

        series_list = list(dataset["series"].values())
        if not series_list:
            return _unavailable(source, "No policy-rate series available")

        observations = series_list[0]["observations"]
        rows = []
        for index, observation in observations.items():
            period = periods[int(index)]["id"]
            value = observation[0]
            if value is not None:
                rows.append((period, float(value)))

        rows.sort(key=lambda row: row[0], reverse=True)
        if not rows:
            return _unavailable(source, "No policy-rate observations available")

        value = rows[0][1]
        as_of = _parse_date(rows[0][0])
        change = (
            round(value - rows[1][1], 3)
            if len(rows) > 1 else None
        )
    except (KeyError, IndexError, StopIteration, TypeError, ValueError):
        log.warning("Could not parse BIS Morocco policy-rate data")
        return _unavailable(source, "Invalid BIS observation data")

    return Indicator(
        region="MA",
        label="Policy rate",
        value=round(value, 3),
        change=change,
        change_label="pp vs previous observation",
        unit="%",
        as_of=as_of,
        source=source,
        freshness=freshness,
    )


async def _get_morocco() -> dict[str, Indicator | DataUnavailable]:
    """Fetch Morocco's key annual economic indicators and policy rate."""
    jobs = [
        _fetch_world_bank_annual(
            "FP.CPI.TOTL.ZG",
            "CPI inflation (annual)",
        ),
        _fetch_world_bank_annual(
            "NY.GDP.MKTP.KD.ZG",
            "GDP growth (annual)",
        ),
        _fetch_bis_policy_rate(),
        _fetch_world_bank_annual(
            "SL.UEM.TOTL.ZS",
            "Unemployment (ILO estimate)",
        ),
        _fetch_world_bank_annual(
            "NE.RSB.GNFS.CD",
            "Trade balance (goods & services)",
            unit=" USD bn",
            scale=1_000_000_000,
            change_label="USD bn vs previous year",
        ),
        _fetch_world_bank_annual(
            "BN.CAB.XOKA.GD.ZS",
            "Current account (% GDP)",
        ),
        _fetch_world_bank_annual(
            "FI.RES.TOTL.CD",
            "Foreign-exchange reserves",
            unit=" USD bn",
            scale=1_000_000_000,
            change_label="USD bn vs previous year",
        ),
        _fetch_world_bank_annual(
            "BX.TRF.PWKR.CD.DT",
            "Remittances received",
            unit=" USD bn",
            scale=1_000_000_000,
            change_label="USD bn vs previous year",
        ),
    ]

    results = await asyncio.gather(*jobs)
    labels = [
        "CPI inflation (annual)",
        "GDP growth (annual)",
        "Policy rate",
        "Unemployment (ILO estimate)",
        "Trade balance (goods & services)",
        "Current account (% GDP)",
        "Foreign-exchange reserves",
        "Remittances received",
    ]
    return dict(zip(labels, results))

async def _get_us(
    fred_api_key: str | None,
) -> dict[str, Indicator | DataUnavailable]:
    """Fetch US indicators with the correct units and growth calculations."""
    jobs = [
        _fetch_fred_yoy("CPIAUCSL", fred_api_key, "CPI (YoY)"),
        _fetch_fred_yoy("PCEPILFE", fred_api_key, "Core PCE (YoY)"),
        _fetch_fred_series(
            "UNRATE", fred_api_key, "US", "Unemployment", "%",
            "pp vs previous month",
        ),
        _fetch_us_payrolls(fred_api_key),
        _fetch_fred_yoy("GDPC1", fred_api_key, "GDP (YoY)", quarterly=True),
        _fetch_fred_series(
            "FEDFUNDS", fred_api_key, "US", "Fed funds", "%",
            "pp vs previous observation",
        ),
    ]

    results = await asyncio.gather(*jobs)
    labels = [
        "CPI (YoY)",
        "Core PCE (YoY)",
        "Unemployment",
        "Payrolls (monthly)",
        "GDP (YoY)",
        "Fed funds",
    ]
    return dict(zip(labels, results))


async def _fetch_boc_series(
    series_id: str,
    label: str,
    change_label: str,
) -> Indicator | DataUnavailable:
    data, freshness = await fetch_json(
        f"{BOC_OBSERVATIONS}/{series_id}/json",
        params={"recent": "3"},
        cache_key=f"economic:boc:{series_id}",
        ttl=OBSERVATIONS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return _unavailable(f"bank-of-canada:{series_id}", "Bank of Canada request failed")

    rows = data.get("observations", [])
    rows = [
        row for row in rows
        if isinstance(row.get(series_id), dict)
        and row[series_id].get("v") not in (None, "")
    ]
    rows.sort(key=lambda row: row.get("d", ""), reverse=True)

    if not rows:
        return _unavailable(f"bank-of-canada:{series_id}", "No observations available")

    try:
        value = float(rows[0][series_id]["v"])
        as_of = _parse_date(rows[0]["d"])
        change = (
            round(value - float(rows[1][series_id]["v"]), 2)
            if len(rows) > 1 else None
        )
    except (KeyError, TypeError, ValueError):
        return _unavailable(f"bank-of-canada:{series_id}", "Invalid observation data")

    return Indicator(
        region="CA",
        label=label,
        value=round(value, 2),
        change=change,
        change_label=change_label,
        unit="%",
        as_of=as_of,
        source=f"bank-of-canada:{series_id}",
        freshness=freshness,
    )


async def _fetch_statcan_housing() -> Indicator | DataUnavailable:
    data, freshness = await fetch_json(
        STATCAN_LATEST,
        method="POST",
        json_body=[{
            "productId": 34100158,
            "coordinate": "1.0.0.0.0.0.0.0.0.0",
            "latestN": 3,
        }],
        cache_key="economic:statcan:housing-starts:canada",
        ttl=OBSERVATIONS_TTL,
    )

    source = "statistics-canada:housing-starts"

    if isinstance(data, DataUnavailable):
        return _unavailable(source, "Statistics Canada request failed")

    try:
        result = data[0]
        if result.get("status") != "SUCCESS":
            return _unavailable(source, "Statistics Canada returned an unsuccessful response")

        points = result["object"]["vectorDataPoint"]
        points.sort(key=lambda point: point.get("refPer", ""), reverse=True)

        if not points:
            return _unavailable(source, "No observations available")

        value = float(points[0]["value"])
        as_of = _parse_date(points[0]["refPer"])
        change = (
            round(value - float(points[1]["value"]), 2)
            if len(points) > 1 else None
        )
    except (IndexError, KeyError, TypeError, ValueError):
        return _unavailable(source, "Invalid observation data")

    return Indicator(
        region="CA",
        label="Housing starts",
        value=round(value, 3),
        change=change,
        change_label="thousand vs previous month",
        unit=" thousand SAAR",
        as_of=as_of,
        source=source,
        freshness=freshness,
    )


async def _get_canada(
    fred_api_key: str | None,
) -> dict[str, Indicator | DataUnavailable]:
    """Fetch Canadian indicators from the appropriate official providers."""
    jobs = [
        _fetch_boc_series(
            "STATIC_TOTALCPICHANGE",
            "CPI (YoY)",
            "pp vs previous month",
        ),
        _fetch_fred_series(
            "LRUNTTTTCAM156S",
            fred_api_key,
            "CA",
            "Unemployment",
            "%",
            "pp vs previous month",
        ),
        _fetch_boc_series(
            "V39079",
            "Policy rate",
            "pp vs previous observation",
        ),
        _fetch_fred_series(
            "CANGDPRQPSMEI",
            fred_api_key,
            "CA",
            "GDP (YoY)",
            "%",
            "pp vs previous quarter",
        ),
        _fetch_statcan_housing(),
    ]

    results = await asyncio.gather(*jobs)
    labels = [
        "CPI (YoY)",
        "Unemployment",
        "Policy rate",
        "GDP (YoY)",
        "Housing starts",
    ]
    return dict(zip(labels, results))


async def get_indicators(
    fred_api_key: str | None,
    regions: list[str] | None = None,
) -> dict[str, dict[str, Indicator | DataUnavailable]]:
    """Fetch indicators for the requested regions."""
    target = regions or list(SERIES)
    out: dict[str, dict[str, Indicator | DataUnavailable]] = {}

    for region in target:
        if region == "CA":
            out[region] = await _get_canada(fred_api_key)
            continue
        if region == "US":
            out[region] = await _get_us(fred_api_key)
            continue
        if region == "MA":
            out[region] = await _get_morocco()
            continue
        if region == "EU":
            out[region] = await _get_europe()
            continue

        labels = SERIES.get(region, {})
        region_out: dict[str, Indicator | DataUnavailable] = {}

        for label, series_id in labels.items():
            region_out[label] = await _fetch_fred_series(
                series_id,
                fred_api_key,
                region,
                label,
                "%",
                "vs previous observation",
            )

        out[region] = region_out

    return out


async def get_region(
    fred_api_key: str | None,
    region: str,
) -> dict[str, Indicator | DataUnavailable]:
    """Convenience wrapper for a single region."""
    return (await get_indicators(fred_api_key, [region])).get(region, {})
