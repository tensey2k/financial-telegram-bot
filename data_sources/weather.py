"""Weather for the configured city.

Open-Meteo: free, no key, no rate limit, and it publishes alerts. That is
the whole reason this is the primary source rather than a fallback.

Coordinates come from config, so the city is not hard-coded here: change
WEATHER_LATITUDE/WEATHER_LONGITUDE in .env and the report moves.

The forecast is fetched twice per day at most (1800s TTL), which is
generous for a 7-day outlook and keeps the 07:00 briefing fast.
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

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL = 1800.0


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
) -> WeatherReport | DataUnavailable:
    data, freshness = await fetch_json(
        OPEN_METEO_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": "7",
            "alerts": "yes",
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

        forecast = [
            DailyForecast(
                date=dates[i],
                high=float(highs[i]) if i < len(highs) else 0.0,
                low=float(lows[i]) if i < len(lows) else 0.0,
                precipitation_prob=int(probs[i]) if i < len(probs) else 0,
                code=0,
            )
            for i in range(len(dates))
        ]

        alerts = []
        raw_alerts = data.get("alerts", {}).get("alerts", [])
        for alert in raw_alerts:
            event = alert.get("event") or "Weather alert"
            desc = alert.get("description") or ""
            alerts.append(f"{event}: {desc}".strip())

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
            as_of=datetime.fromisoformat(current["time"]).replace(tzinfo=timezone.utc) if current.get("time") else None,
            freshness=freshness,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Failed to parse weather payload: %s", exc)
        return DataUnavailable(source="open-meteo", reason=str(exc))