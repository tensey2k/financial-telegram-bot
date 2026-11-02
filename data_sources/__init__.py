"""Financial and market data connectors.

Every connector in this package is a thin async function that returns a
typed value and never raises to its caller. Failures are surfaced as
``None`` or an "unavailable" sentinel so the formatter can label the data
instead of the whole report crashing.

Connectors share a single :class:`CachedClient` instance from
:data_sources.base. That instance owns the httpx client, the concurrency
semaphore, the TTL cache, and the retry policy, so each connector does not
re-implement timeouts, backoff, or rate-limit handling.
"""

from data_sources.base import (
    CACHE_TTL,
    CachedClient,
    DataUnavailable,
    FetchError,
    Freshness,
    cache,
    fetch_json,
    fetch_text,
)

__all__ = [
    "CACHE_TTL",
    "CachedClient",
    "DataUnavailable",
    "FetchError",
    "Freshness",
    "cache",
    "fetch_json",
    "fetch_text",
]