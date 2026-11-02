"""Global and regional news from RSS feeds.

No API key, no scraping, no interpretation. Each feed is polled by URL and
its items are returned verbatim - title, source, link, and publication
time - with the freshness of the feed itself attached.

Feedparser is the only dependency here, and it is on the approved list. The
bot does not summarise or score headlines; it lists them. That keeps the
report a data surface, not an editorial voice, and it removes the need for
any AI package.

A feed that has not published in 24 hours is labelled STALE rather than
LIVE, so a dead feed does not masquerade as a live one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser

from data_sources.base import (
    DataUnavailable,
    Freshness,
    fetch_text,
)

log = logging.getLogger(__name__)

FEEDS: dict[str, list[str]] = {
    "Global": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    ],
    "Canada": [
        "https://www.cbc.ca/webfeed/rss/rss-canada",
        "https://www.cbc.ca/webfeed/rss/rss-world",
    ],
    "US": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
    "Morocco": [
        "https://www.euronews.com/rss",
    ],
    "Europe": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.euronews.com/rss",
    ],
}

NEWS_TTL = 900.0
STALE_AFTER_SECONDS = 86400.0  # one day


@dataclass
class NewsItem:
    title: str
    source: str
    link: str
    published: datetime | None
    region: str
    freshness: Freshness = Freshness.LIVE


def _parse_feed(
    parsed: "feedparser.FeedParserDict",
    region: str,
    now: datetime | None = None,
) -> list[NewsItem]:
    if now is None:
        now = datetime.now(timezone.utc)

    items: list[NewsItem] = []
    for entry in parsed.get("entries", [])[:12]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        link = (entry.get("link") or "").strip()
        source = (entry.get("source", {}).get("title") or "").strip()
        published = _parse_published(entry.get("published_parsed"))

        # A feed is live only if its most recent item is within 24h.
        freshness = (
            Freshness.LIVE
            if published and (now - published).total_seconds() < STALE_AFTER_SECONDS
            else Freshness.STALE
        )
        items.append(
            NewsItem(
                title=title,
                source=source,
                link=link,
                published=published,
                region=region,
                freshness=freshness,
            )
        )
    return items


def _parse_published(parsed: object) -> datetime | None:
    if not isinstance(parsed, tuple) or len(parsed) < 6:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


async def get_news(
    regions: list[str] | None = None,
) -> dict[str, list[NewsItem] | DataUnavailable]:
    """Fetch headlines for the requested regions."""
    target = regions or list(FEEDS)
    out: dict[str, list[NewsItem] | DataUnavailable] = {}
    for region in target:
        urls = FEEDS.get(region, [])
        if not urls:
            out[region] = DataUnavailable(source="news", reason=f"No feeds configured for {region}")
            continue

        items: list[NewsItem] = []
        for url in urls:
            text, _ = await fetch_text(url, cache_key=f"news:{region}:{url}", ttl=NEWS_TTL)
            if isinstance(text, DataUnavailable):
                log.warning("Feed %s unavailable: %s", url, text.reason)
                continue
            parsed = feedparser.parse(text)
            items.extend(_parse_feed(parsed, region))

        if not items:
            out[region] = DataUnavailable(source="news", reason="No headlines from any feed")
        else:
            out[region] = items
    return out


async def get_region(region: str) -> list[NewsItem] | DataUnavailable:
    return (await get_news([region])).get(region, DataUnavailable(source="news", reason="Unknown region"))