"""Weather for the configured city.

Open-Meteo provides current conditions and daily forecasts. Its standard
forecast endpoint does not provide official Canadian weather alerts.

Coordinates come from config, so the city is not hard-coded here: change
WEATHER_LATITUDE/WEATHER_LONGITUDE in .env and the report moves.

The forecast is fetched twice per day at most (1800s TTL), which is
generous for a 7-day outlook and keeps the 07:00 briefing fast.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_json,
    fetch_text,
)

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL = 1800.0


MONTREAL_ALERTS_URL = (
    "https://weather.gc.ca/rss/battleboard/qcrm2_e.xml"
)
ALERTS_TTL = 300.0
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class AlertResult:
    alerts: list[str]
    status: str
    updated_at: datetime | None
    freshness: Freshness


async def get_montreal_alerts() -> AlertResult:
    """Fetch official Environment Canada alerts for Montréal Island."""
    data, freshness = await fetch_text(
        MONTREAL_ALERTS_URL,
        cache_key="environment-canada:montreal-alerts",
        ttl=ALERTS_TTL,
    )

    if isinstance(data, DataUnavailable):
        return AlertResult([], "UNAVAILABLE", None, Freshness.UNAVAILABLE)

    try:
        root = ET.fromstring(data)

        updated_text = root.findtext("atom:updated", namespaces=ATOM_NS)
        updated_at = (
            datetime.fromisoformat(updated_text.replace("Z", "+00:00"))
            if updated_text else None
        )

        entries = root.findall("atom:entry", ATOM_NS)
        alerts = []

        for entry in entries:
            title = entry.findtext("atom:title", default="", namespaces=ATOM_NS)
            summary = entry.findtext(
                "atom:summary", default="", namespaces=ATOM_NS
            )
            if "no alerts in effect" not in title.lower() and summary.strip():
                alerts.append(f"{title}: {summary.strip()}")

        if not entries:
            return AlertResult(
                [], "UNAVAILABLE", updated_at, Freshness.UNAVAILABLE
            )

        return AlertResult(
            alerts,
            "ACTIVE" if alerts else "NONE",
            updated_at,
            freshness,
        )

    except (ET.ParseError, ValueError, TypeError) as exc:
        log.warning("Failed to parse Environment Canada alerts: %s", exc)
        return AlertResult([], "UNAVAILABLE", None, Freshness.UNAVAILABLE)


@dataclass
class WeatherReport:
    city: str
    country: str
    current_temp: float | None
    condition: str
    high: float | None
    low: float | None
    precipitation_prob: int | None
    wind_kmh: float | None
    alerts: list[str]
    forecast: list["DailyForecast"]
    as_of: datetime | None
    freshness: Freshness = Freshness.LIVE


@dataclass
class DailyForecast:
    date: str
    high: float
    low: float
    precipitation_prob: int
    code: int


def _weather_code(code: int) -> str:
    """Map WMO codes to a short human condition."""
    return {
        0: "Clear",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Heavy drizzle",
        56: "Freezing drizzle",
        57: "Freezing drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        66: "Freezing rain",
        67: "Freezing rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        77: "Snow grains",
        80: "Light showers",
        81: "Showers",
        82: "Violent showers",
        85: "Snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm w/ hail",
        99: "Severe thunderstorm",
    }.get(code, f"Code {code}")


async def get_weather(
    latitude: float,
    longitude: float,
    city: str,
    country: str,
    timezone_name: str = "auto",
) -> WeatherReport | DataUnavailable:
    data, freshness = await fetch_json(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "timezone": timezone_name,
            "forecast_days": "7",
        },
        cache_key=f"weather:{city}",
        ttl=WEATHER_TTL,
    )
    if isinstance(data, DataUnavailable):
        return data

    try:
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        dates = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        probs = daily.get("precipitation_probability_max", [])
        codes = daily.get("weather_code", [])

        forecast = [
            DailyForecast(
                date=dates[i],
                high=float(highs[i]) if i < len(highs) else 0.0,
                low=float(lows[i]) if i < len(lows) else 0.0,
                precipitation_prob=int(probs[i]) if i < len(probs) else 0,
                code=int(codes[i]) if i < len(codes) else 0,
            )
            for i in range(len(dates))
        ]

        # Official alerts require a separate government alert feed.
        alerts: list[str] = []

        return WeatherReport(
            city=city,
            country=country,
            current_temp=float(current.get("temperature_2m")) if current.get("temperature_2m") is not None else None,
            condition=_weather_code(int(current.get("weather_code", 0))),
            high=float(highs[0]) if highs else None,
            low=float(lows[0]) if lows else None,
            precipitation_prob=int(probs[0]) if probs else None,
            wind_kmh=float(current.get("wind_speed_10m")) if current.get("wind_speed_10m") is not None else None,
            alerts=alerts,
            forecast=forecast,
            as_of=(
                datetime.fromisoformat(current["time"]).replace(
                    tzinfo=timezone(timedelta(seconds=int(data.get("utc_offset_seconds", 0))))
                )
                if current.get("time") else None
            ),
            freshness=freshness,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Failed to parse weather payload: %s", exc)
        return DataUnavailable(source="open-meteo", reason=str(exc))