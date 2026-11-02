"""Configuration schema and validation.

Every variable declared in .env.example appears exactly once here. Unknown
variables in .env are rejected at startup, so a misspelled flag fails loudly
instead of silently leaving a section enabled.
"""

from __future__ import annotations

import re
from datetime import time as dtime
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved against this file, not the process CWD: systemd may start the bot
# from a different directory than the project root.
PROJECT_ROOT = Path(__file__).resolve().parent

TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{25,}$")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

Source = Literal[
    "weather",
    "crypto",
    "stocks",
    "oil",
    "bonds",
    "forex",
    "rates",
    "economic",
    "news",
    "calendar",
]


class CachePolicy(BaseModel):
    """Per-source TTLs in seconds, keyed to how often each provider updates."""

    model_config = ConfigDict(frozen=True)

    weather: int = 1800
    crypto: int = 300
    stocks_intraday: int = 900
    stocks_closed: int = 3600
    oil_intraday: int = 900
    oil_closed: int = 3600
    bonds: int = 21600
    forex: int = 3600
    rates: int = 86400
    economic: int = 86400
    news: int = 900
    calendar: int = 43200
    default: int = 900

    def ttl(self, source: Source, *, market_open: bool = True) -> int:
        if source == "stocks":
            return self.stocks_intraday if market_open else self.stocks_closed
        if source == "oil":
            return self.oil_intraday if market_open else self.oil_closed
        return getattr(self, source)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )

    # --- Telegram ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_user_id: int = 0
    telegram_extra_user_id: int | None = None

    # --- Scheduling ---
    delivery_time: dtime = dtime(9, 0)
    timezone: str = "America/Toronto"
    delivery_days: frozenset[int] = Field(default_factory=lambda: frozenset(range(7)))

    # --- Location ---
    weather_latitude: float = 45.5017
    weather_longitude: float = -73.5673
    weather_city: str = "Montreal"
    weather_country: str = "Canada"

    # --- Optional API keys ---
    fred_api_key: SecretStr = SecretStr("")
    coingecko_api_key: SecretStr = SecretStr("")
    finnhub_api_key: SecretStr = SecretStr("")
    alpha_vantage_api_key: SecretStr = SecretStr("")

    # --- Network ---
    request_timeout: int = Field(default=15, ge=1, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff: float = Field(default=2.0, ge=0.1, le=30.0)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    cache_ttl: int = Field(default=900, ge=30, le=86400)

    # --- Report sections ---
    enable_oil: bool = True
    enable_crypto: bool = True
    enable_stocks: bool = True
    enable_bonds: bool = True
    enable_rates: bool = True
    enable_forex: bool = True
    enable_economic: bool = True
    enable_news: bool = True
    enable_calendar: bool = True
    enable_weather: bool = True
    enable_canada: bool = True
    enable_us: bool = True
    enable_morocco: bool = True
    enable_europe: bool = True
    enable_ca_mortgage: bool = True

    # --- Local state ---
    state_dir: Path = Path("state")

    # --- Logging ---
    log_level: LogLevel = "INFO"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("telegram_bot_token", mode="before")
    @classmethod
    def _check_token(cls, value: object) -> object:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is empty. Get it from @BotFather."
            )
        if not TOKEN_PATTERN.match(raw):
            raise ValueError(
                "TELEGRAM_BOT_TOKEN does not look like '<bot_id>:<secret>'."
            )
        return raw

    @field_validator("telegram_user_id")
    @classmethod
    def _check_user_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError(
                "TELEGRAM_USER_ID must be your numeric id (see @userinfobot)."
            )
        return value

    @field_validator("telegram_extra_user_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("telegram_extra_user_id")
    @classmethod
    def _check_extra_user_id(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("TELEGRAM_EXTRA_USER_ID must be positive when set.")
        return value

    @field_validator("delivery_time", mode="before")
    @classmethod
    def _parse_delivery_time(cls, value: object) -> dtime:
        if isinstance(value, dtime):
            return value
        try:
            hour, minute = str(value).strip().split(":")
            return dtime(int(hour), int(minute))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "DELIVERY_TIME must be HH:MM on a 24-hour clock, e.g. 09:00."
            ) from exc

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        candidate = value.strip()
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"TIMEZONE {candidate!r} is not a valid IANA zone "
                "(Montreal is America/Toronto)."
            ) from exc
        return candidate

    @field_validator("delivery_days", mode="before")
    @classmethod
    def _parse_delivery_days(cls, value: object) -> frozenset[int]:
        if isinstance(value, (frozenset, set, list, tuple)):
            items: list[object] = list(value)
        else:
            items = [p for p in str(value).replace(" ", "").split(",") if p]
        parsed: set[int] = set()
        for item in items:
            try:
                day = int(str(item))
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "DELIVERY_DAYS must be comma-separated digits 0-6 "
                    "(0=Sunday, 6=Saturday)."
                ) from exc
            if not 0 <= day <= 6:
                raise ValueError("DELIVERY_DAYS entries must be between 0 and 6.")
            parsed.add(day)
        if not parsed:
            raise ValueError("DELIVERY_DAYS cannot be empty.")
        return frozenset(parsed)

    @field_validator("weather_latitude")
    @classmethod
    def _check_latitude(cls, value: float) -> float:
        if not -90.0 <= value <= 90.0:
            raise ValueError("WEATHER_LATITUDE must be between -90 and 90.")
        return value

    @field_validator("weather_longitude")
    @classmethod
    def _check_longitude(cls, value: float) -> float:
        if not -180.0 <= value <= 180.0:
            raise ValueError("WEATHER_LONGITUDE must be between -180 and 180.")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return str(value).strip().upper() if isinstance(value, str) else value

    @field_validator(
        "fred_api_key",
        "coingecko_api_key",
        "finnhub_api_key",
        "alpha_vantage_api_key",
        mode="before",
    )
    @classmethod
    def _strip_optional_key(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _check_cross_fields(self) -> Self:
        if self.enable_ca_mortgage and not self.enable_canada:
            raise ValueError(
                "ENABLE_CA_MORTGAGE requires ENABLE_CANADA=true."
            )
        if self.max_concurrency > 8:
            raise ValueError(
                "MAX_CONCURRENCY above 8 risks tripping free-tier rate limits "
                "during the 09:00 briefing fan-out."
            )
        return self

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def bot_token(self) -> str:
        return self.telegram_bot_token.get_secret_value()

    @property
    def authorized_user_ids(self) -> frozenset[int]:
        ids = {self.telegram_user_id}
        if self.telegram_extra_user_id is not None:
            ids.add(self.telegram_extra_user_id)
        return frozenset(ids)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def fred_key(self) -> str | None:
        return self.fred_api_key.get_secret_value() or None

    @property
    def coingecko_key(self) -> str | None:
        return self.coingecko_api_key.get_secret_value() or None

    @property
    def finnhub_key(self) -> str | None:
        return self.finnhub_api_key.get_secret_value() or None

    @property
    def alpha_vantage_key(self) -> str | None:
        return self.alpha_vantage_api_key.get_secret_value() or None

    @property
    def state_path(self) -> Path:
        path = self.state_dir
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def delivery_marker(self) -> Path:
        return self.state_path / "last_delivery.date"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache(self) -> CachePolicy:
        return CachePolicy(default=self.cache_ttl)


def load_settings(**overrides: object) -> Settings:
    """Build the singleton Settings. Raises pydantic.ValidationError on bad input."""
    return Settings(**overrides)  # type: ignore[arg-type]