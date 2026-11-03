"""Business, economics, financial markets and geopolitics news."""

from __future__ import annotations

import asyncio
import difflib
import logging
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser

from data_sources.base import DataUnavailable, Freshness, fetch_text

log = logging.getLogger(__name__)

NEWS_TTL = 900.0
STALE_AFTER_SECONDS = 86400.0
MAX_ITEMS_PER_REGION = 3

def _google_news(query: str) -> str:
    params = urllib.parse.urlencode({
        "q": f"{query} when:1d",
        "hl": "en-US",
        "gl": "CA",
        "ceid": "CA:en",
    })
    return f"https://news.google.com/rss/search?{params}"


FEEDS: dict[str, list[tuple[str, str]]] = {
    "Global": [
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Reuters Business", _google_news(
            'site:reuters.com (economy OR markets OR trade OR energy)'
        )),
        ("Global Geopolitics", _google_news(
            '(site:reuters.com OR site:bbc.com OR site:apnews.com) '
            '(sanctions OR tariffs OR "trade war" OR "geopolitical risk")'
        )),
    ],
    "Canada": [
        ("Canadian Business and Markets", _google_news(
            '(site:bnnbloomberg.ca OR site:financialpost.com OR '
            'site:theglobeandmail.com OR site:bankofcanada.ca) '
            '(Canadian economy OR business OR markets OR trade OR inflation)'
        )),
    ],
    "US": [
        ("US Financial Markets", _google_news(
            '(site:reuters.com OR site:cnbc.com OR site:wsj.com) '
            '("Federal Reserve" OR "Treasury yields" OR Wall Street OR '
            '"stock market" OR inflation)'
        )),
        ("US Economy and Policy", _google_news(
            '(site:reuters.com OR site:apnews.com OR site:wsj.com OR '
            'site:bea.gov OR site:bls.gov) '
            '("US economy" OR GDP OR employment OR trade OR budget)'
        )),
    ],
    "Morocco": [
        ("Morocco Economy and Markets", _google_news(
            '(site:medias24.com OR site:lematin.ma OR site:telquel.ma OR '
            'site:lavieeco.com OR site:northafricapost.com) '
            '(economy OR business OR investment OR exports OR inflation OR '
            '"Casablanca Stock Exchange" OR "Bank Al-Maghrib")'
        )),
        ("Morocco Broader Business News", _google_news(
            '(Morocco OR Moroccan) '
            '(economy OR business OR markets OR investment OR inflation OR '
            'exports OR trade OR "central bank" OR "Casablanca Stock Exchange")'
        )),
    ],
    "Europe": [
        ("ECB Press Releases", "https://www.ecb.europa.eu/rss/press.html"),
        ("Europe Economy and Markets", _google_news(
            '(site:reuters.com OR site:ft.com OR site:euronews.com OR '
            'site:ecb.europa.eu OR site:ec.europa.eu) '
            '(Eurozone OR European economy OR inflation OR markets OR trade)'
        )),
    ],
}


@dataclass
class NewsItem:
    title: str
    source: str
    link: str
    published: datetime | None
    region: str
    freshness: Freshness = Freshness.LIVE
    fetch_freshness: Freshness = Freshness.LIVE


def _parse_published(parsed: object) -> datetime | None:
    if not isinstance(parsed, tuple) or len(parsed) < 6:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_feed(
    parsed: "feedparser.FeedParserDict",
    region: str,
    fallback_source: str,
    fetch_freshness: Freshness,
    now: datetime,
) -> list[NewsItem]:
    items: list[NewsItem] = []

    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue

        link = (entry.get("link") or "").strip()
        source_data = entry.get("source") or {}
        source = (
            source_data.get("title", "")
            if isinstance(source_data, dict)
            else ""
        ) or fallback_source

        published = (
            _parse_published(entry.get("published_parsed"))
            or _parse_published(entry.get("updated_parsed"))
        )

        age = (now - published).total_seconds() if published else None
        article_freshness = (
            Freshness.LIVE
            if age is not None and 0 <= age < STALE_AFTER_SECONDS
            else Freshness.STALE
        )

        items.append(NewsItem(
            title=title,
            source=source,
            link=link,
            published=published,
            region=region,
            freshness=article_freshness,
            fetch_freshness=fetch_freshness,
        ))

    return items


async def _fetch_feed(
    region: str,
    source: str,
    url: str,
) -> tuple[list[NewsItem], DataUnavailable | None]:
    text, fetch_freshness = await fetch_text(
        url,
        cache_key=f"news:{region}:{source}:{url}",
        ttl=NEWS_TTL,
    )

    if isinstance(text, DataUnavailable):
        log.warning("News feed %s unavailable: %s", source, text.reason)
        return [], text

    try:
        parsed = feedparser.parse(text)
        items = _parse_feed(
            parsed,
            region,
            source,
            fetch_freshness,
            datetime.now(timezone.utc),
        )
        if not items:
            log.warning("News feed %s returned no usable headlines", source)
        return items, None
    except Exception as exc:
        log.exception("Could not parse news feed %s", source)
        return [], DataUnavailable(source=source, reason=str(exc))


async def get_news(
    regions: list[str] | None = None,
) -> dict[str, list[NewsItem] | DataUnavailable]:
    """Fetch regional headlines concurrently and return newest first."""
    target = regions or list(FEEDS)

    jobs = []
    job_regions = []

    for region in target:
        feeds = FEEDS.get(region, [])
        if not feeds:
            continue
        for source, url in feeds:
            jobs.append(_fetch_feed(region, source, url))
            job_regions.append(region)

    results = await asyncio.gather(*jobs, return_exceptions=True)

    grouped: dict[str, list[NewsItem]] = {region: [] for region in target}
    errors: dict[str, list[str]] = {region: [] for region in target}

    for region, result in zip(job_regions, results):
        if isinstance(result, Exception):
            log.error("News feed task failed for %s: %s", region, result)
            errors[region].append(str(result))
            continue

        items, error = result
        grouped[region].extend(items)
        if error is not None:
            errors[region].append(error.reason)

    out: dict[str, list[NewsItem] | DataUnavailable] = {}

    for region in target:
        items = grouped.get(region, [])

        # Rank recent headlines by economic and geopolitical relevance.
        import re

        impact_terms = {
            "central bank": 5, "interest rate": 5, "rate cut": 5,
            "rate hike": 5, "inflation": 5, "recession": 5,
            "gdp": 5, "financial crisis": 5, "default": 5,
            "sanctions": 5, "trade war": 5, "tariff": 4,
            "war": 4, "ceasefire": 4, "energy crisis": 5,
            "oil": 3, "gas": 3, "bond": 3, "treasury": 4,
            "stock market": 3, "markets": 2, "currency": 3,
            "unemployment": 4, "jobs": 3, "trade": 2,
            "exports": 2, "imports": 2, "budget": 3,
            "debt": 3, "investment": 2, "bank": 2,
            "business": 1, "earnings": 2, "economic": 2,
        }

        candidates = []
        now = datetime.now(timezone.utc)

        for item in items:
            if item.published is None:
                continue

            age_hours = (now - item.published).total_seconds() / 3600
            if age_hours < 0 or age_hours > 24:
                continue

            title = item.title.casefold()
            score = sum(
                weight for term, weight in impact_terms.items()
                if term in title
            )

            if score < 2:
                continue

            # Recency breaks ties between similarly relevant stories.
            score += max(0, 24 - age_hours) / 24
            candidates.append((score, item))

        candidates.sort(key=lambda pair: pair[0], reverse=True)

        ordered: list[NewsItem] = []
        normalized_titles: list[str] = []

        for _, item in candidates:
            key = " ".join(
                char if char.isalnum() else " "
                for char in item.title.casefold()
            )
            key = " ".join(key.split())

            duplicate = any(
                difflib.SequenceMatcher(None, key, existing).ratio() >= 0.85
                for existing in normalized_titles
            )

            if not duplicate:
                ordered.append(item)
                normalized_titles.append(key)

        if ordered:
            out[region] = ordered[:MAX_ITEMS_PER_REGION]
        elif region not in FEEDS:
            out[region] = DataUnavailable(
                source="news",
                reason=f"No feeds configured for {region}",
            )
        else:
            reason = "; ".join(errors.get(region, [])) or "No headlines returned"
            out[region] = DataUnavailable(source="news", reason=reason)

    return out


async def get_region(region: str) -> list[NewsItem] | DataUnavailable:
    return (await get_news([region])).get(
        region,
        DataUnavailable(source="news", reason="Unknown region"),
    )
