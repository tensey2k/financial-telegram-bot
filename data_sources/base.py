"""Async HTTP foundation, bounded retries, and in-memory TTL cache.

Every data connector delegates network activity to CachedClient. The client
owns:
  - an httpx.AsyncClient with connection pooling and explicit timeouts
  - an asyncio.Semaphore for bounded concurrency (protects free-tier APIs)
  - exponential backoff retries with HTTP 429 Retry-After handling
  - an in-memory TTL cache that returns fresh data without touching network
  - explicit freshness labeling (LIVE, CACHED, STALE, UNAVAILABLE)

Connectors never raise network exceptions to their callers; they return a
DataUnavailable sentinel or None so the briefing formatter can label the
missing section without crashing the report.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

_start_time = time.time()
from enum import Enum
from typing import Any, Callable, TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")


class Freshness(str, Enum):
    """Data freshness indicator attached to every connector response."""

    LIVE = "LIVE"
    CACHED = "CACHED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


    @property
    def label(self) -> str:
        return {
            Freshness.LIVE: "LIVE",
            Freshness.CACHED: "CACHED",
            Freshness.STALE: "STALE",
            Freshness.UNAVAILABLE: "UNAVAILABLE",
        }.get(self, "UNKNOWN")


@dataclass(frozen=True)
class DataUnavailable:
    """Sentinel returned when a data source fails after all retries.

    Carries the source name, error reason, and optional last-known value
    from cache so the formatter can decide whether to render stale data or
    an explicit 'unavailable' label.
    """

    source: str
    reason: str
    last_known_value: Any = None
    last_known_timestamp: float | None = None

    @property
    def is_stale(self) -> bool:
        return self.last_known_value is not None


@dataclass
class CacheEntry:
    value: Any
    created_at: float
    ttl: float

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.created_at) < self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class FetchError(Exception):
    """Internal exception raised during retry loop; caught before caller."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TTLKeyedCache:
    """Thread-safe, async-friendly in-memory TTL cache."""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[Any, Freshness] | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_fresh:
                return (entry.value, Freshness.CACHED)
            # Expired: keep entry for stale fallback, return None for cache-hit
            return None

    async def get_stale(self, key: str) -> tuple[Any, float] | None:
        """Return (value, created_at) even if expired, for stale fallback."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                return (entry.value, entry.created_at)
            return None

    async def set(self, key: str, value: Any, ttl: float) -> None:
        async with self._lock:
            self._store[key] = CacheEntry(
                value=value, created_at=time.time(), ttl=ttl
            )

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    async def size(self) -> int:
        async with self._lock:
            return len(self._store)


# Singleton cache shared across all connectors
cache = TTLKeyedCache()

CACHE_TTL = 900  # Default 15 minutes


class CachedClient:
    """Async HTTP client wrapper with retries, caching, and concurrency bounds."""

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 2,
        backoff_factor: float = 2.0,
        max_concurrency: int = 4,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._cache_hits = 0
        self._cache_misses = 0
        self._network_attempts = 0
        self._network_successes = 0
        self._network_failures = 0

    async def cache_stats(self) -> dict[str, Any]:
        total = self._cache_hits + self._cache_misses
        return {
            "entries": await cache.size(),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate_pct": (self._cache_hits / total * 100) if total else 0.0,
            "network_attempts": self._network_attempts,
            "network_successes": self._network_successes,
            "network_failures": self._network_failures,
            "uptime_seconds": time.time() - _start_time,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                headers = {
                    "User-Agent": (
                        "PersonalFinancialBot/1.0 (+https://github.com/TENSEy)"
                    ),
                    "Accept": "application/json, text/plain, */*",
                }
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout),
                    headers=headers,
                    follow_redirects=True,
                )
            return self._client

    async def close(self) -> None:
        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                await self._client.aclose()
                self._client = None

    async def fetch(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        cache_key: str | None = None,
        ttl: float = CACHE_TTL,
        parse_json: bool = True,
        force_refresh: bool = False,
        method: str = "GET",
        json_body: Any = None,
    ) -> tuple[Any, Freshness]:
        """Fetch URL with caching, retries, and bounded concurrency.

        Returns (data, Freshness). Never raises; on total failure returns
        (DataUnavailable(...), Freshness.UNAVAILABLE).
        """
        ckey = cache_key or f"{url}:{sorted((params or {}).items())}"

        # 1. Check fresh cache unless this request forces a refresh
        if not force_refresh:
            cached = await cache.get(ckey)
            if cached is not None:
                self._cache_hits += 1
                return cached
            self._cache_misses += 1

        # 2. Network fetch inside concurrency limit
        async with self._semaphore:
            client = await self._get_client()
            last_err: Exception | None = None

            for attempt in range(1 + self.max_retries):
                try:
                    self._network_attempts += 1
                    resp = await client.request(
                        method.upper(),
                        url,
                        params=params,
                        json=json_body,
                        headers=headers,
                    )

                    if resp.status_code == 429:
                        retry_after = float(
                            resp.headers.get("Retry-After", self.backoff_factor)
                        )
                        log.warning(
                            "Rate limited by %s, backing off %.1fs",
                            url,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    resp.raise_for_status()
                    data = resp.json() if parse_json else resp.text

                    # Cache successful result
                    await cache.set(ckey, data, ttl)
                    self._network_successes += 1
                    return (data, Freshness.LIVE)

                except httpx.HTTPStatusError as exc:
                    last_err = exc
                    status = exc.response.status_code

                    log.error(
                        "HTTP error details: status=%s url=%s response=%s",
                        status,
                        url,
                        exc.response.text[:1000],
                    )

                    if 400 <= status < 500 and status != 429:
                        log.error("Client error %d fetching %s", status, url)
                        break

                    log.warning(
                        "HTTP %d fetching %s (attempt %d/%d)",
                        status,
                        url,
                        attempt + 1,
                        1 + self.max_retries,
                    )

                except (httpx.RequestError, asyncio.TimeoutError) as exc:
                    last_err = exc
                    log.warning(
                        "Network error fetching %s: %s (attempt %d/%d)",
                        url,
                        exc,
                        attempt + 1,
                        1 + self.max_retries,
                    )

                if attempt < self.max_retries:
                    delay = self.backoff_factor * (2**attempt)
                    await asyncio.sleep(delay)

        # All network attempts failed.
        self._network_failures += 1

        # 3. All retries failed: prefer a fresh cached fallback when forced
        if force_refresh:
            cached = await cache.get(ckey)
            if cached is not None:
                return cached

        # 4. Check for an expired cached fallback
        stale = await cache.get_stale(ckey)
        if stale is not None:
            value, timestamp = stale
            log.warning("Serving STALE data for %s", url)
            return (value, Freshness.STALE)

        # 4. Total failure: return DataUnavailable sentinel
        reason = str(last_err) if last_err else "All retries exhausted"
        return (
            DataUnavailable(source=url, reason=reason),
            Freshness.UNAVAILABLE,
        )


# Convenience global client functions
_client_instance: CachedClient | None = None


def get_client(timeout: float = 15.0, max_retries: int = 2) -> CachedClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = CachedClient(timeout=timeout, max_retries=max_retries)
    return _client_instance


async def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache_key: str | None = None,
    ttl: float = CACHE_TTL,
    force_refresh: bool = False,
    method: str = "GET",
    json_body: Any = None,
) -> tuple[Any, Freshness]:
    """Fetch JSON with caching and retries."""
    return await get_client().fetch(
        url,
        params=params,
        headers=headers,
        cache_key=cache_key,
        ttl=ttl,
        parse_json=True,
        force_refresh=force_refresh,
        method=method,
        json_body=json_body,
    )


async def fetch_text(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    cache_key: str | None = None,
    ttl: float = CACHE_TTL,
) -> tuple[str | DataUnavailable, Freshness]:
    """Fetch raw text (for RSS/XML parsing) with caching and retries."""
    return await get_client().fetch(
        url,
        params=params,
        headers=headers,
        cache_key=cache_key,
        ttl=ttl,
        parse_json=False,
    )
