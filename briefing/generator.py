"""Shared data builder and renderers for the daily intelligence briefing."""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from urllib.parse import urlsplit

from config import Settings
from data_sources.base import DataUnavailable
from data_sources import bonds, crypto, economic, forex, news, oil, stocks, weather
from telegram.helpers import escape_markdown

from telegram_bot.formatter import (
    format_bp,
    format_number,
    format_percent,
    format_pct,
    format_unavailable,
    MAX_MESSAGE_LENGTH,
)

log = logging.getLogger(__name__)

ECONOMIC_REGIONS = (
    ("canada", "CA"),
    ("us", "US"),
    ("morocco", "MA"),
    ("europe", "EU"),
)
PERSONAL_ETFS = ("XEQT", "VFV", "VCN", "VDY", "HXQ")
WATCHLIST_ETFS = ("VEQT", "XGRO", "XBB", "XIU", "XRE", "XEC", "XEF", "VGRO")
BOND_TENORS = ("2Y", "3Y", "5Y", "7Y", "10Y")


async def _capture(tasks: dict) -> dict:
    """Run source tasks concurrently and isolate individual failures."""
    if not tasks:
        return {}
    names = list(tasks)
    values = await asyncio.gather(*tasks.values(), return_exceptions=True)
    results = {}
    for name, value in zip(names, values):
        if isinstance(value, Exception):
            log.error("Briefing source %s failed: %s", name, type(value).__name__)
            results[name] = DataUnavailable(name, str(value) or "source error")
        else:
            results[name] = value
    return results


async def build_briefing_data(settings: Settings) -> dict:
    """Fetch all enabled briefing data once for all delivery formats."""
    tasks = {}

    if settings.enable_oil:
        tasks["oil"] = oil.get_oil()
    if settings.enable_crypto:
        tasks["crypto"] = crypto.get_crypto_quotes(settings.coingecko_api_key)
    if settings.enable_stocks:
        tasks["indices"] = stocks.get_indices(settings.finnhub_api_key)
        tasks["etfs"] = stocks.get_etfs()
    if settings.enable_bonds:
        tasks["bonds"] = bonds.get_all(settings.fred_key)
    if settings.enable_forex:
        tasks["forex"] = forex.get_forex(settings.fxapi_key)
    if settings.enable_economic:
        for name, region in ECONOMIC_REGIONS:
            if getattr(settings, f"enable_{name}", True):
                tasks[f"economic_{name}"] = economic.get_region(
                    settings.fred_key, region
                )
    if settings.enable_weather:
        tasks["weather_montreal"] = weather.get_weather(
            settings.weather_latitude,
            settings.weather_longitude,
            "Montreal, QC",
            "Canada",
            "auto",
        )
        tasks["weather_rabat"] = weather.get_weather(
            34.0209, -6.8416, "Rabat", "Morocco", "GMT"
        )
        tasks["weather_alerts"] = weather.get_montreal_alerts()
    if settings.enable_news:
        tasks["news"] = news.get_news()

    results = await _capture(tasks)
    return {
        "generated_at": datetime.now(settings.zone),
        "sections": results,
    }


def _unavailable(value) -> str:
    return format_unavailable(value)


CURRENCY_NAMES = {
    "CAD": "Canadian dollar", "USD": "US dollar", "EUR": "Euro",
    "MAD": "Moroccan dirham", "GBP": "British pound", "JPY": "Japanese yen",
    "CNY": "Chinese yuan", "CHF": "Swiss franc", "AUD": "Australian dollar",
}


def _currency_names(pair: str) -> str:
    base, quote = pair.split("/", 1)
    return f"{CURRENCY_NAMES.get(base, base)} / {CURRENCY_NAMES.get(quote, quote)}"


def _asset_line(label: str, quote, price_currency: str = "") -> str:
    if isinstance(quote, DataUnavailable) or quote is None:
        return f"{label}: {_unavailable(quote)}"
    price = format_number(quote.price)
    currency = f"{price_currency or getattr(quote, 'currency', 'USD')} "
    change = getattr(quote, "change_pct", None)
    if change is None:
        change = getattr(quote, "change_24h_pct", None)
    freshness = getattr(getattr(quote, "freshness", None), "label", "UNKNOWN")
    return f"{label}: {currency}{price} ({format_pct(change)}) [{freshness}]"


def _section(title: str, lines: list[str]) -> str:
    return f"*{title}*\n" + ("\n".join(lines) if lines else "No data returned.")


def _weather_lines(report) -> list[str]:
    if isinstance(report, DataUnavailable):
        return [_unavailable(report)]
    if not isinstance(report, weather.WeatherReport):
        return ["unavailable (weather report not returned)"]

    lines = []
    if report.current_temp is not None:
        lines.append(f"Now: {report.current_temp:.0f}°C, {report.condition}")
    else:
        lines.append(f"Now: {report.condition}")
    if report.wind_kmh is not None:
        lines.append(f"Wind: {report.wind_kmh:.0f} km/h")
    if report.high is not None and report.low is not None:
        precip = (
            f"{report.precipitation_prob}%"
            if report.precipitation_prob is not None else "n/a"
        )
        lines.append(
            f"Today: High {report.high:.0f}°C / Low {report.low:.0f}°C"
            f" · Precipitation: {precip}"
        )

    lines.append("3-day forecast:")
    for day in (report.forecast or [])[:3]:
        precip = (
            f"{day.precipitation_prob}%"
            if day.precipitation_prob is not None else "n/a"
        )
        lines.append(
            f"  • {day.date}: {day.high:.0f}–{day.low:.0f}°C, "
            f"{weather._weather_code(day.code)}, "
            f"{precip} precipitation"
        )
    updated = (
        report.as_of.strftime("%Y-%m-%d %H:%M %Z")
        if report.as_of else "time unavailable"
    )
    freshness = getattr(report.freshness, "label", str(report.freshness))
    lines.append(f"{freshness} · Updated {updated}")
    return lines


def _news_url_allowed(link: str) -> bool:
    """Only allow absolute HTTPS article links without embedded credentials."""
    try:
        parsed = urlsplit(str(link))
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
        )
    except (TypeError, ValueError):
        return False


def _news_title(item) -> str:
    """Return a plain headline; Telegram escaping is applied at final rendering."""
    return str(item.title).replace("\n", " ").strip()


def _format_news_lines(value) -> list[str]:
    if isinstance(value, DataUnavailable):
        return [_unavailable(value)]
    if not isinstance(value, dict):
        return ["unavailable (news data not returned)"]

    lines = []
    for region, items in value.items():
        display_region = str(region).replace("_", " ")
        lines.append(f"\n{display_region}")
        if isinstance(items, DataUnavailable):
            lines.append(_unavailable(items))
            continue
        if not isinstance(items, list) or not items:
            lines.append("No recent headlines returned.")
            continue
        for item in items[:5]:
            published = (
                item.published.strftime("%Y-%m-%d %H:%M UTC")
                if item.published else "Time unavailable"
            )
            article_freshness = getattr(item.freshness, "value", "unknown")
            feed_freshness = getattr(item.fetch_freshness, "value", "unknown")
            lines.append(
                f"• {_news_title(item)} — {item.source}\n"
                f"  Published: {published} | Article: {article_freshness} "
                f"| Feed: {feed_freshness}"
            )
            if _news_url_allowed(getattr(item, "link", "")):
                lines.append(f"  {item.link}")
    return lines or ["No recent headlines returned."]


def _render_sections(data: dict) -> list[tuple[str, list[str]]]:
    """Return ordered section titles and their plain-text/Markdown lines."""
    raw = data.get("sections", {})
    output = []

    if "oil" in raw:
        value = raw["oil"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            for key, quote in value.items():
                lines.append(_asset_line(getattr(quote, "name", key), quote, "USD"))
        output.append(("Energy Markets · Brent & WTI", lines))

    if "crypto" in raw:
        value = raw["crypto"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            for symbol in ("BTC", "ETH", "SOL", "XMR"):
                if symbol in value:
                    lines.append(_asset_line(symbol, value[symbol], "USD"))
            for symbol, quote in value.items():
                if symbol not in ("BTC", "ETH", "SOL", "XMR"):
                    lines.append(_asset_line(symbol, quote, "USD"))
        output.append(("Digital Assets", lines))

    if "indices" in raw:
        value = raw["indices"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            for name, quote in value.items():
                country = stocks.INDEX_COUNTRIES.get(name, "")
                label = f"{name} ({country})" if country else name
                if isinstance(quote, DataUnavailable):
                    lines.append(f"{label}: {_unavailable(quote)}")
                else:
                    lines.append(
                        f"{label}: {format_number(quote.price)} "
                        f"({format_pct(quote.change_pct)}) "
                        f"[{quote.freshness.label}]"
                    )
        output.append(("Global Equity Indices", lines))

    if "etfs" in raw:
        value = raw["etfs"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        else:
            for title, tickers in (
                ("PERSONAL PORTFOLIO", PERSONAL_ETFS),
                ("ETF WATCHLIST", WATCHLIST_ETFS),
            ):
                lines.append(f"\n{title}")
                for ticker in tickers:
                    quote = value.get(ticker) if isinstance(value, dict) else None
                    name = stocks.ETFS.get(ticker, ticker)
                    if quote is None or isinstance(quote, DataUnavailable):
                        lines.append(f"{ticker} | {name}: {_unavailable(quote)}")
                    else:
                        lines.append(
                            f"{ticker} | {name}: {quote.currency} "
                            f"{format_number(quote.price)} "
                            f"({format_pct(quote.change_pct)}) "
                            f"[{quote.freshness.label}]"
                        )
        output.append(("Canadian-Listed ETFs", lines))

    if "bonds" in raw:
        value = raw["bonds"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            for title, curve_key, spread_key in (
                ("US Treasury Yield Curve", "us_curve", "spread"),
                ("Canada Government Bond Yield Curve", "canada_curve", "canada_spread"),
            ):
                lines.append(f"\n{title}")
                curve = value.get(curve_key, {})
                for tenor in BOND_TENORS:
                    point = curve.get(tenor) if isinstance(curve, dict) else None
                    if isinstance(point, bonds.YieldPoint):
                        lines.append(f"{tenor}: {format_number(point.rate, 3)}%")
                    else:
                        lines.append(f"{tenor}: {_unavailable(point)}")
                spread = value.get(spread_key)
                if isinstance(spread, bonds.CurveSpread):
                    lines.append(
                        f"2s10s spread: {format_number(spread.spread_bps, 1)} bps "
                        f"({spread.signal})"
                    )
                else:
                    lines.append(f"2s10s spread: {_unavailable(spread)}")
        output.append(("Government Bond Yields", lines))

    if "forex" in raw:
        value = raw["forex"]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            rates = value.get("rates", {})
            for pair in forex.PAIR_ORDER:
                quote = rates.get(pair) if isinstance(rates, dict) else None
                if isinstance(quote, forex.FxRate):
                    lines.append(
                        f"{pair}: {format_number(quote.rate, 4)} "
                        f"({format_pct(quote.change_pct)}) [{quote.freshness.label}]"
                    )
                else:
                    lines.append(f"{pair}: {_unavailable(quote)}")
            updated = value.get("last_updated")
            if updated:
                lines.append(f"Last updated: {updated.strftime('%Y-%m-%d %H:%M UTC')}")
        output.append(("Foreign Exchange", lines))

    for name, region in ECONOMIC_REGIONS:
        key = f"economic_{name}"
        if key not in raw:
            continue
        value = raw[key]
        lines = []
        if isinstance(value, DataUnavailable):
            lines.append(_unavailable(value))
        elif isinstance(value, dict):
            if not value:
                lines.append("No indicators configured for this region.")
            for label, indicator in value.items():
                if isinstance(indicator, DataUnavailable):
                    lines.append(f"{label}: {_unavailable(indicator)}")
                else:
                    change = (
                        f" | Change: {indicator.change:+.3f} {indicator.change_label}"
                        if indicator.change is not None and indicator.change_label
                        else ""
                    )
                    date = (
                        indicator.as_of.strftime("%Y-%m-%d")
                        if indicator.as_of else "date n/a"
                    )
                    lines.append(
                        f"{label}: {indicator.value:g}{indicator.unit}{change}\n"
                        f"  {indicator.freshness.value.upper()} · {date}"
                    )
        output.append((f"{economic.REGION_NAMES.get(region, region)} Economic Indicators", lines))

    if "weather_montreal" in raw:
        output.append(("Weather · Montreal, Canada", _weather_lines(raw["weather_montreal"])))
    if "weather_rabat" in raw:
        rabat_lines = _weather_lines(raw["weather_rabat"])
        rabat_lines.append("Official alerts: Not yet integrated for Rabat.")
        output.append(("Weather · Rabat, Morocco", rabat_lines))
    if "weather_alerts" in raw:
        alerts = raw["weather_alerts"]
        lines = []
        if isinstance(alerts, DataUnavailable):
            lines.append(_unavailable(alerts))
        elif getattr(alerts, "status", "") == "UNAVAILABLE":
            lines.append("Environment Canada alert feed unavailable.")
        elif getattr(alerts, "status", "") == "ACTIVE":
            lines.extend(getattr(alerts, "alerts", []) or ["Active alert status; no details returned."])
        else:
            lines.append(
                "No active alerts listed in the Environment Canada feed. "
                "This does not confirm a current all-clear."
            )
        if not isinstance(alerts, DataUnavailable):
            freshness = getattr(getattr(alerts, "freshness", None), "value", "unknown")
            updated = getattr(alerts, "updated_at", None)
            updated_text = updated.strftime("%Y-%m-%d %H:%M UTC") if updated else "n/a"
            lines.append(f"Feed: {freshness.upper()} · Last content update: {updated_text}")
        output.append(("Montreal · Environment Canada Alerts", lines))

    if "news" in raw:
        output.append((
            "Business, Economics, Markets & Geopolitics",
            _format_news_lines(raw["news"]),
        ))

    return output


def _generated_at(data: dict) -> str:
    value = data.get("generated_at")
    if isinstance(value, datetime):
        return value.strftime("%A, %B %d, %Y · %H:%M %Z")
    return "Generation time unavailable"


def _split_sections(sections: list[str], max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split at safe boundaries, keeping each news article together."""
    chunks = []
    current = ""

    for section in sections:
        lines = section.splitlines() or [section]
        blocks = []
        index = 0

        while index < len(lines):
            block = [lines[index]]
            is_article = lines[index].startswith("• ")
            index += 1

            if is_article:
                while index < len(lines) and lines[index].startswith("  "):
                    block.append(lines[index])
                    index += 1

            blocks.append("\n".join(block))

        for block in blocks:
            if len(block) > max_len:
                block = block[: max_len - 1] + "…"

            candidate = f"{current}\n{block}" if current else block
            if len(candidate) > max_len and current:
                chunks.append(current)
                current = block
            else:
                current = candidate

    if current:
        chunks.append(current)
    return chunks or ["Daily briefing: no enabled sections."]


def format_briefing_telegram(data: dict) -> list[str]:
    """Render the original briefing sections with compact Telegram spacing."""
    icons = (
        ("Energy", "🛢️"),
        ("Digital Assets", "₿"),
        ("Global Equity", "📈"),
        ("ETF", "📊"),
        ("Government Bond", "🏦"),
        ("Foreign Exchange", "💱"),
        ("Economic Indicators", "🌐"),
        ("Weather", "🌤️"),
        ("Alerts", "⚠️"),
        ("Geopolitics", "📰"),
    )

    sections = [
        "📊 *DAILY INTELLIGENCE BRIEFING*\n"
        + escape_markdown(_generated_at(data), version=1)
    ]

    for title, lines in _render_sections(data):
        title_text = str(title)
        safe_title = escape_markdown(title_text, version=1)
        icon = next(
            (emoji for keyword, emoji in icons if keyword in title_text),
            "▪️",
        )

        blocks = []
        items = lines or ["No data returned."]
        index = 0

        while index < len(items):
            original = str(items[index])
            index += 1
            raw = original.strip()

            if not raw:
                continue

            if raw.startswith("• "):
                article_lines = [raw]
                while index < len(items) and str(items[index]).startswith("  "):
                    continuation = str(items[index])
                    article_lines.append(continuation)
                    index += 1

                safe_article = []
                for line in article_lines:
                    if _news_url_allowed(line.strip()):
                        safe_article.append(line)
                    else:
                        safe_article.append(escape_markdown(line, version=1))
                blocks.append("\n".join(safe_article))

            elif original.startswith("\n"):
                safe_subheading = escape_markdown(raw, version=1)
                blocks.append(f"▸ *{safe_subheading}*")

            else:
                blocks.append(escape_markdown(original, version=1))

        body = "\n\n".join(blocks) if blocks else escape_markdown(
            "No data returned.", version=1
        )
        sections.append(f"\n{icon} *{safe_title}*\n\n{body}")

    return _split_sections(sections)

def format_briefing_text(data: dict) -> str:
    parts = ["DAILY INTELLIGENCE BRIEFING", _generated_at(data)]
    for title, lines in _render_sections(data):
        parts.extend(["", title.upper(), *lines])
    return "\n".join(parts)


def _safe_html_link(item) -> str:
    title = html.escape(str(item.title), quote=True)
    link = str(getattr(item, "link", ""))
    if not _news_url_allowed(link):
        return title
    return f'<a href="{html.escape(link, quote=True)}">{title}</a>'



def format_briefing_html(data: dict) -> str:
    """Render a premium investor-style HTML newsletter from the shared data."""
    sections = data.get("sections", {})
    generated = html.escape(_generated_at(data), quote=True)
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        "<title>Adil's Daily Financial Intelligence</title></head>",
        '<body style="margin:0;padding:0;background:#0b1220;'
        'font-family:Arial,Helvetica,sans-serif;color:#e5edf7;">',
        '<div style="max-width:760px;margin:0 auto;padding:24px 14px 36px;">',
        '<div style="padding:30px 28px;background:#111e33;'
        'border:1px solid #263a56;border-radius:16px 16px 0 0;">',
        '<div style="font-size:11px;font-weight:bold;letter-spacing:2px;'
        'color:#55c7e8;text-transform:uppercase;margin-bottom:12px;">'
        'MARKET INTELLIGENCE · DAILY EDITION</div>',
        '<h1 style="font-size:28px;line-height:1.2;margin:0 0 10px;'
        'color:#ffffff;">Adil&#39;s Daily Financial Intelligence</h1>',
        f'<div style="font-size:13px;color:#a9bad0;">{generated}</div>',
        '</div>',
    ]

    def esc(value) -> str:
        return html.escape(str(value), quote=True)

    def freshness_badge(value) -> str:
        label = getattr(value, "label", None) or getattr(value, "value", None) or str(value or "UNKNOWN")
        label = str(label).upper()
        styles = {
            "LIVE": ("#123d31", "#55e0a0"),
            "CACHED": ("#493719", "#ffc66d"),
            "STALE": ("#49252b", "#ff8792"),
            "UNAVAILABLE": ("#3b3038", "#d6a7b4"),
        }
        bg, fg = styles.get(label, ("#293449", "#b7c4d8"))
        return (
            f'<span style="display:inline-block;margin-left:10px;vertical-align:middle;'
            f'padding:4px 8px;border-radius:20px;background:{bg};color:{fg};'
            f'font-size:10px;font-weight:bold;letter-spacing:.7px;">{esc(label)}</span>'
        )

    def change_badge(value) -> str:
        if value is None:
            return '<span style="color:#9aabc0;">—</span>'
        try:
            number = float(value)
        except (TypeError, ValueError):
            return f'<span style="color:#9aabc0;">{esc(value)}</span>'
        color = "#55e0a0" if number > 0 else "#ff7d89" if number < 0 else "#a9bad0"
        bg = "#12372f" if number > 0 else "#43252d" if number < 0 else "#293449"
        return (
            f'<span style="display:inline-block;padding:4px 8px;border-radius:7px;'
            f'background:{bg};color:{color};font-size:12px;font-weight:bold;">'
            f'{number:+.2f}%</span>'
        )

    def value_cell(value, unit="", color="#67d5f2") -> str:
        return (
            f'<span style="color:{color};font-weight:bold;font-size:15px;">'
            f'{esc(value)}{esc(unit)}</span>'
        )

    def card(title: str, content: str, subtitle: str = "") -> None:
        parts.append(
            '<section style="margin-top:14px;padding:20px 20px 18px;'
            'background:#142238;border:1px solid #263a56;border-radius:12px;">'
            f'<h2 style="font-size:16px;line-height:1.3;margin:0 0 5px;'
            f'color:#ffffff;">{esc(title)}</h2>'
            + (f'<div style="font-size:12px;color:#91a7c2;margin-bottom:14px;">{esc(subtitle)}</div>' if subtitle else '<div style="height:10px;"></div>')
            + content + '</section>'
        )

    def unavailable(value) -> str:
        return f'<div style="padding:10px 12px;color:#ffb0b7;background:#38242e;border-radius:8px;">{esc(_unavailable(value))}</div>'

    def row(label: str, value: str, meta: str = "") -> str:
        return (
            '<tr><td style="padding:11px 8px;border-bottom:1px solid #293b53;'
            'color:#c4d1e1;font-size:13px;">' + esc(label) + '</td>'
            '<td style="padding:11px 8px;border-bottom:1px solid #293b53;'
            'text-align:right;white-space:nowrap;">' + value + meta + '</td></tr>'
        )

    def row_html(label_html: str, value: str, meta: str = "") -> str:
        return (
            '<tr><td style="padding:11px 8px;border-bottom:1px solid #293b53;'
            'color:#c4d1e1;font-size:13px;">' + label_html + '</td>'
            '<td style="padding:11px 8px;border-bottom:1px solid #293b53;'
            'text-align:right;white-space:nowrap;">' + value + meta + '</td></tr>'
        )

    def basis_points_badge(value) -> str:
        if value is None:
            return '<span style="color:#9aabc0;">—</span>'
        number = float(value)
        color = "#55e0a0" if number > 0 else "#ff7d89" if number < 0 else "#a9bad0"
        bg = "#12372f" if number > 0 else "#43252d" if number < 0 else "#293449"
        return (
            f'<span style="display:inline-block;padding:4px 8px;border-radius:7px;'
            f'background:{bg};color:{color};font-size:12px;font-weight:bold;">'
            f'{number:+.1f} bps</span>'
        )

    def signal_badge(signal: str) -> str:
        inverted = str(signal).lower() == "inverted"
        bg, fg = ("#43252d", "#ff7d89") if inverted else ("#12372f", "#55e0a0")
        return (
            f'<span style="display:inline-block;padding:4px 8px;border-radius:7px;'
            f'background:{bg};color:{fg};font-size:12px;font-weight:bold;">'
            f'{esc(signal)}</span>'
        )

    def table(rows: str) -> str:
        return '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">' + rows + '</table>'

    # Energy markets
    oil_data = sections.get("oil")
    if oil_data is not None:
        if isinstance(oil_data, DataUnavailable):
            card("Energy Markets · Brent & WTI", unavailable(oil_data))
        elif isinstance(oil_data, dict):
            rows = ""
            for key, quote in oil_data.items():
                if isinstance(quote, DataUnavailable):
                    rows += row(key, unavailable(quote))
                    continue
                freshness = freshness_badge(getattr(quote, "freshness", None))
                rows += row(
                    getattr(quote, "name", key),
                    value_cell(f"{getattr(quote, 'currency', 'USD')} {format_number(quote.price)}")
                    + change_badge(getattr(quote, "change_pct", None))
                    + freshness,
                )
            card("Energy Markets · Brent & WTI", table(rows))

    # Digital assets
    crypto_data = sections.get("crypto")
    if crypto_data is not None:
        if isinstance(crypto_data, DataUnavailable):
            card("Cryptocurrencies", unavailable(crypto_data))
        elif isinstance(crypto_data, dict):
            rows = ""
            ordered = [s for s in ("BTC", "ETH", "SOL", "XMR") if s in crypto_data]
            ordered += [s for s in crypto_data if s not in ordered]
            for symbol in ordered:
                quote = crypto_data[symbol]
                if isinstance(quote, DataUnavailable):
                    rows += row(symbol, unavailable(quote))
                    continue
                rows += row(
                    f"{symbol} – {getattr(quote, 'name', symbol)}",
                    value_cell(f"{getattr(quote, 'currency', 'USD')} {format_number(quote.price)}")
                    + change_badge(getattr(quote, "change_24h_pct", None))
                    + freshness_badge(getattr(quote, "freshness", None)),
                )
            card("Cryptocurrencies", table(rows), "24-hour price performance")

    # Equity indices
    indices = sections.get("indices")
    if indices is not None:
        if isinstance(indices, DataUnavailable):
            card("Global Equity Indices", unavailable(indices))
        elif isinstance(indices, dict):
            rows = ""
            for name, quote in indices.items():
                country = stocks.INDEX_COUNTRIES.get(name, "")
                label = f"{name} ({country})" if country else name
                if isinstance(quote, DataUnavailable):
                    rows += row(label, unavailable(quote))
                else:
                    rows += row(
                        label,
                        value_cell(format_number(quote.price))
                        + change_badge(quote.change_pct)
                        + freshness_badge(quote.freshness),
                    )
            card("Global Equity Indices", table(rows))

    # Canadian-listed ETFs, with true subheadings.
    etfs = sections.get("etfs")
    if etfs is not None:
        if isinstance(etfs, DataUnavailable):
            card("Canadian-Listed ETFs", unavailable(etfs))
        else:
            content = ""
            for heading, tickers in (
                ("PERSONAL PORTFOLIO", PERSONAL_ETFS),
                ("ETF WATCHLIST", WATCHLIST_ETFS),
            ):
                content += (
                    f'<h3 style="margin:18px 0 6px;color:#55c7e8;font-size:12px;'
                    f'letter-spacing:1.2px;">{heading}</h3>'
                )
                rows = ""
                for ticker in tickers:
                    quote = etfs.get(ticker) if isinstance(etfs, dict) else None
                    name = stocks.ETFS.get(ticker, ticker)
                    if quote is None or isinstance(quote, DataUnavailable):
                        rows += row_html(f"{esc(ticker)} · <i>{esc(name)}</i>", unavailable(quote))
                    else:
                        rows += row_html(
                            f"{esc(ticker)} · <i>{esc(name)}</i>",
                            value_cell(f"{quote.currency} {format_number(quote.price)}")
                            + change_badge(quote.change_pct)
                            + freshness_badge(quote.freshness),
                        )
                content += table(rows)
            card("Canadian-Listed ETFs", content)

    # Government bond yields
    bond_data = sections.get("bonds")
    if bond_data is not None:
        if isinstance(bond_data, DataUnavailable):
            card("Government Bond Yields", unavailable(bond_data))
        elif isinstance(bond_data, dict):
            content = ""
            for heading, curve_key, spread_key, country_name in (
                ("US TREASURY", "us_curve", "spread", "Treasury"),
                ("CANADA GOVERNMENT", "canada_curve", "canada_spread", "Government of Canada"),
            ):
                content += (
                    f'<h3 style="margin:18px 0 6px;color:#55c7e8;font-size:12px;'
                    f'letter-spacing:1.2px;">{heading}</h3>'
                )
                curve = bond_data.get(curve_key, {})
                rows = ""
                for tenor in BOND_TENORS:
                    point = curve.get(tenor) if isinstance(curve, dict) else None
                    if isinstance(point, bonds.YieldPoint):
                        rows += row(
                            f"{tenor.replace('Y', '-Year')} {country_name} Yield",
                            value_cell(format_number(point.rate, 3), "%")
                            + basis_points_badge(point.change_bp)
                            + freshness_badge(point.freshness),
                        )
                    else:
                        rows += row(tenor, unavailable(point))
                spread = bond_data.get(spread_key)
                if isinstance(spread, bonds.CurveSpread):
                    spread_text = (
                        value_cell(f"{format_number(spread.spread_bps, 1)} bps")
                        + signal_badge(spread.signal)
                        + freshness_badge(spread.freshness)
                    )
                else:
                    spread_text = unavailable(spread)
                rows += row("2s10s spread", spread_text)
                content += table(rows)
            card("Government Bond Yields", content)

    # Foreign exchange
    forex_data = sections.get("forex")
    if forex_data is not None:
        if isinstance(forex_data, DataUnavailable):
            card("Foreign Exchange", unavailable(forex_data))
        elif isinstance(forex_data, dict):
            rates = forex_data.get("rates", {})
            rows = ""
            for pair in forex.PAIR_ORDER:
                quote = rates.get(pair) if isinstance(rates, dict) else None
                if isinstance(quote, forex.FxRate):
                    change = (
                        change_badge(quote.change_pct)
                        if quote.change_pct is not None else ""
                    )
                    rows += row_html(
                        f"{esc(pair)} <i>({esc(_currency_names(pair))})</i>",
                        value_cell(format_number(quote.rate, 4))
                        + change + freshness_badge(quote.freshness),
                    )
                else:
                    rows += row(pair, unavailable(quote))
            updated = forex_data.get("last_updated")
            content = table(rows)
            if updated:
                content += f'<div style="font-size:11px;color:#91a7c2;margin-top:12px;">Last updated: {esc(updated.strftime("%Y-%m-%d %H:%M UTC"))}</div>'
            card("Foreign Exchange", content)

    # Economic indicators
    for name, region in ECONOMIC_REGIONS:
        key = f"economic_{name}"
        if key not in sections:
            continue
        value = sections[key]
        title = f"{economic.REGION_NAMES.get(region, region)} Economic Indicators"
        if isinstance(value, DataUnavailable):
            card(title, unavailable(value))
            continue
        if not isinstance(value, dict) or not value:
            card(title, '<div style="color:#a9bad0;">No indicators configured for this region.</div>')
            continue
        content = ""
        for label, indicator in value.items():
            if isinstance(indicator, DataUnavailable):
                content += f'<div style="padding:12px 0;border-bottom:1px solid #293b53;"><div style="color:#c4d1e1;font-size:13px;">{esc(label)}</div>{unavailable(indicator)}</div>'
                continue
            date = indicator.as_of.strftime("%Y-%m-%d") if indicator.as_of else "date n/a"
            change_text = ""
            if indicator.change is not None and indicator.change_label:
                change_color = "#55e0a0" if indicator.change > 0 else "#ff7d89" if indicator.change < 0 else "#a9bad0"
                change_text = f'<span style="margin-left:8px;color:{change_color};font-size:12px;">Change: {esc(f"{indicator.change:+.3f} {indicator.change_label}")}</span>'
            content += (
                '<div style="padding:12px 0;border-bottom:1px solid #293b53;">'
                f'<div style="color:#c4d1e1;font-size:13px;margin-bottom:7px;">{esc(label)}</div>'
                '<div>'
                f'{value_cell(f"{indicator.value:g}", indicator.unit)}{change_text}'
                f'{freshness_badge(indicator.freshness)}'
                '</div>'
                f'<div style="font-size:11px;color:#91a7c2;margin-top:6px;">As of {esc(date)}</div>'
                '</div>'
            )
        card(title, content)

    # Weather reports, retaining all current conditions, forecast and freshness details.
    def weather_emoji(condition: str) -> str:
        text = condition.lower()
        if any(word in text for word in ("thunder", "storm")):
            return "⛈️"
        if any(word in text for word in ("snow", "sleet", "ice")):
            return "❄️"
        if any(word in text for word in ("rain", "drizzle", "shower")):
            return "🌧️"
        if any(word in text for word in ("cloud", "overcast")):
            return "☁️"
        if any(word in text for word in ("fog", "mist")):
            return "🌫️"
        if any(word in text for word in ("clear", "sun")):
            return "☀️"
        return "🌤️"

    for key, title in (
        ("weather_montreal", "Weather · Montreal, Canada"),
        ("weather_rabat", "Weather · Rabat, Morocco"),
    ):
        if key not in sections:
            continue
        report = sections[key]
        if isinstance(report, DataUnavailable):
            card(title, unavailable(report))
            continue
        if not isinstance(report, weather.WeatherReport):
            card(title, '<div style="color:#ffb0b7;">Weather report unavailable.</div>')
            continue
        condition = esc(report.condition)
        content = (
            '<div style="padding:14px;background:#1b3049;border-radius:10px;margin-bottom:12px;">'
            f'<div style="font-size:22px;margin-bottom:6px;">{weather_emoji(report.condition)} '
            f'<span style="font-size:16px;color:#ffffff;font-weight:bold;">{condition}</span></div>'
        )
        if report.current_temp is not None:
            content += f'<div style="font-size:30px;color:#67d5f2;font-weight:bold;">{report.current_temp:.0f}°C</div>'
        if report.wind_kmh is not None:
            content += f'<div style="font-size:12px;color:#c4d1e1;margin-top:5px;">Wind: {report.wind_kmh:.0f} km/h</div>'
        if report.high is not None and report.low is not None:
            precip = f"{report.precipitation_prob}%" if report.precipitation_prob is not None else "n/a"
            content += f'<div style="font-size:12px;color:#c4d1e1;margin-top:5px;">Today: High {report.high:.0f}°C / Low {report.low:.0f}°C · Precipitation: {esc(precip)}</div>'
        content += '</div><div style="font-size:12px;color:#55c7e8;font-weight:bold;letter-spacing:1px;margin:12px 0 5px;">3-DAY FORECAST</div>'
        for day in (report.forecast or [])[:3]:
            condition_text = weather._weather_code(day.code)
            precip = f"{day.precipitation_prob}%" if day.precipitation_prob is not None else "n/a"
            content += (
                '<div style="padding:10px 0;border-bottom:1px solid #293b53;'
                'font-size:13px;color:#c4d1e1;">'
                f'<strong style="color:#ffffff;">{esc(day.date)}</strong> · '
                f'{weather_emoji(condition_text)} {esc(condition_text)} · '
                f'{day.high:.0f}°/{day.low:.0f}°C · {esc(precip)} precipitation</div>'
            )
        if key == "weather_rabat":
            content += (
                '<div style="font-size:12px;color:#c4d1e1;margin-top:12px;">'
                'Official alerts: Not yet integrated for Rabat.</div>'
            )
        updated = report.as_of.strftime("%Y-%m-%d %H:%M %Z") if report.as_of else "time unavailable"
        content += (
            '<div style="font-size:11px;color:#91a7c2;margin-top:12px;">'
            f'Updated: {esc(updated)}{freshness_badge(report.freshness)}</div>'
        )
        card(title, content)

    # Environment Canada alerts
    alerts = sections.get("weather_alerts")
    if alerts is not None:
        if isinstance(alerts, DataUnavailable):
            card("Montreal · Environment Canada Alerts", unavailable(alerts))
        else:
            status = getattr(alerts, "status", "")
            if status == "UNAVAILABLE":
                content = '<div style="color:#ffb0b7;">Environment Canada alert feed unavailable.</div>'
            elif status == "ACTIVE":
                entries = getattr(alerts, "alerts", []) or ["Active alert status; no details returned."]
                content = "".join(f'<div style="padding:8px 0;border-bottom:1px solid #293b53;">⚠️ {esc(item)}</div>' for item in entries)
            else:
                content = '<div style="color:#c4d1e1;">No active alerts listed in the Environment Canada feed. This does not confirm a current all-clear.</div>'
            updated = getattr(alerts, "updated_at", None)
            updated_text = updated.strftime("%Y-%m-%d %H:%M UTC") if updated else "n/a"
            content += f'<div style="font-size:11px;color:#91a7c2;margin-top:12px;">Feed: {esc(getattr(getattr(alerts, "freshness", None), "value", "unknown").upper())} · Last content update: {esc(updated_text)}</div>'
            card("Montreal · Environment Canada Alerts", content)

    # News articles remain individually escaped and links remain HTTPS-only.
    raw_news = sections.get("news")
    if raw_news is not None:
        if isinstance(raw_news, DataUnavailable):
            card("Business, Economics, Markets & Geopolitics", unavailable(raw_news))
        elif not isinstance(raw_news, dict):
            card("Business, Economics, Markets & Geopolitics", '<div style="color:#ffb0b7;">News data unavailable.</div>')
        else:
            content = ""
            for region, items in raw_news.items():
                content += f'<h3 style="margin:18px 0 8px;color:#55c7e8;font-size:12px;letter-spacing:1px;">{esc(str(region).replace("_", " "))}</h3>'
                if isinstance(items, DataUnavailable):
                    content += unavailable(items)
                    continue
                if not isinstance(items, list) or not items:
                    content += '<div style="color:#a9bad0;font-size:13px;">No recent headlines returned.</div>'
                    continue
                for item in items[:5]:
                    published = item.published.strftime("%Y-%m-%d %H:%M UTC") if item.published else "Time unavailable"
                    metadata = (
                        f'{esc(item.source)} · {esc(published)} · Article: '
                        f'{esc(getattr(item.freshness, "value", "unknown"))} · Feed: '
                        f'{esc(getattr(item.fetch_freshness, "value", "unknown"))}'
                    )
                    content += (
                        '<div style="padding:12px 0;border-bottom:1px solid #293b53;'
                        'font-size:13px;line-height:1.5;">'
                        f'<div style="font-weight:bold;color:#ffffff;">{_safe_html_link(item)}</div>'
                        f'<div style="font-size:11px;color:#91a7c2;margin-top:5px;">{metadata}</div>'
                        '</div>'
                    )
            card("Business, Economics, Markets & Geopolitics", content)

    parts.append(
        '<div style="padding:22px 12px;text-align:center;color:#91a7c2;'
        'font-size:12px;line-height:1.6;">'
        '<div style="color:#dce8f6;font-size:13px;">Prepared with care, just for you. ❤️</div>'
        '<div style="margin-top:8px;">Adil&#39;s Daily Financial Intelligence</div>'
        '</div></div></body></html>'
    )
    return "".join(parts)


async def build_briefing(settings: Settings) -> list[str]:
    """Backward-compatible Telegram briefing builder."""
    data = await build_briefing_data(settings)
    return format_briefing_telegram(data)
