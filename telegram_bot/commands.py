"""Telegram command handlers.

Every handler is wrapped with @authorized so only the configured user IDs
can invoke them. Handlers never fetch data directly - they call a connector
from data_sources/ and pass the result through telegram_bot.formatter.

Handlers catch connector failures at the boundary and reply with a short
'unavailable' message rather than letting the exception bubble up and
silence the bot.
"""

from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Settings, load_settings
from data_sources.base import DataUnavailable, get_client
from data_sources import bonds, crypto, economic, forex, news, oil, stocks, weather
from telegram_bot.authorization import is_authorized, reject_unauthorized
from telegram_bot.formatter import (
    format_bp,
    format_number,
    format_percent,
    format_pct,
    format_quote_line,
    format_unavailable,
    split_for_telegram,
)

log = logging.getLogger(__name__)


def _settings(ctx: ContextTypes.DEFAULT_TYPE) -> Settings:
    return ctx.bot_data["settings"]

def authorized(func):
    async def wrapper(update, ctx, *args, **kwargs):
        settings = _settings(ctx)

        if not is_authorized(update, settings):
            await reject_unauthorized(update)
            return None

        return await func(update, ctx, *args, **kwargs)

    return wrapper

@authorized
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Personal financial & global intelligence bot.\n\n"
        "Use /help to see available commands.",
        parse_mode=None,
    )


@authorized
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Commands*\n"
        "/briefing - comprehensive daily financial intelligence briefing\n"
        "/oil - Brent and WTI crude oil prices and daily performance\n"
        "/crypto - Bitcoin, Ethereum, Solana and Monero market prices\n"
        "/stocks - major global indices and ETF performance\n"
        "/bonds - US and Canadian government yields and yield spreads\n"
        "/forex - CAD currency pairs and ECB reference exchange rates\n"
        "/canada - Canadian economic data and monetary policy\n"
        "/us - US economic data, inflation and employment\n"
        "/morocco - Moroccan economic indicators and monetary policy\n"
        "/europe - European economic data, inflation and growth\n"
        "/weather - local weather forecasts and active alerts\n"
        "/news - regional and international headlines\n"
        "/status - data-source availability and cache information\n"
        "/settings - current bot configuration",
        parse_mode=ParseMode.MARKDOWN,
    )


@authorized
async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from briefing.generator import (
        build_briefing_data,
        format_briefing_html,
        format_briefing_text,
    )
    from briefing.mailer import send_briefing_email

    settings = _settings(ctx)

    try:
        data = await build_briefing_data(settings)
    except Exception as exc:
        log.exception("Briefing generation failed")
        await update.message.reply_text(f"Briefing failed: {exc}")
        return

    if not settings.brevo_enabled:
        await update.message.reply_text(
            "Email delivery is disabled. The briefing was not sent."
        )
        return

    try:
        generated = data["generated_at"].strftime("%Y-%m-%d")
        sent = await send_briefing_email(
            settings,
            f"Daily Financial Intelligence — {generated}",
            format_briefing_html(data),
            format_briefing_text(data),
        )
        if sent:
            await update.message.reply_text(
                "📧 Your full daily financial briefing has been sent to your email."
            )
        else:
            await update.message.reply_text(
                "⚠️ The briefing email could not be sent."
            )
    except Exception:
        log.exception("Manual briefing email delivery failed")
        await update.message.reply_text(
            "⚠️ An error occurred while sending the briefing email."
        )


@authorized
async def cmd_oil(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await oil.get_oil()
    lines = ["*Crude Oil*"]
    for key, label in (("brent", "Brent"), ("wti", "WTI")):
        quote = data.get(key)
        if isinstance(quote, DataUnavailable):
            lines.append(f"  {label}: {format_unavailable(quote)}")
        else:
            lines.append(
                f"  {label}: ${format_number(quote.price)} "
                f"({format_pct(quote.change_pct)}) [{quote.freshness.label}]"
            )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_crypto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    data = await crypto.get_crypto_quotes(settings.coingecko_api_key)
    lines = ["*Crypto*"]
    for sym in ("BTC", "ETH", "SOL", "XMR"):
        quote = data.get(sym)
        if isinstance(quote, DataUnavailable):
            lines.append(f"  {sym}: {format_unavailable(quote)}")
        else:
            lines.append(
                f"  {sym}: ${format_number(quote.price)} "
                f"({format_pct(quote.change_24h_pct)}) "
                f"[{quote.freshness.label}]"
            )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_stocks(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    indices = await stocks.get_indices(settings.finnhub_api_key)
    etfs = await stocks.get_etfs()

    lines = ["*GLOBAL INDICES*"]
    for name, idx in indices.items():
        country = stocks.INDEX_COUNTRIES.get(name, "")
        display_name = f"{name} ({country})" if country else name

        if isinstance(idx, DataUnavailable):
            lines.append(f"  {display_name}: {format_unavailable(idx)}")
        else:
            lines.append(
                f"  {display_name}: {format_number(idx.price)} "
                f"({format_pct(idx.change_pct)}) [{idx.freshness.label}]"
            )

    def append_etf_section(title: str, tickers: tuple[str, ...]) -> None:
        lines.extend(["", f"*{title}*"])
        for ticker in tickers:
            quote = etfs.get(ticker)
            name = stocks.ETFS[ticker]

            if quote is None or isinstance(quote, DataUnavailable):
                lines.append(
                    f"  `{ticker}` | {name}: "
                    f"{format_unavailable(quote)}"
                )
            else:
                lines.append(
                    f"  `{ticker}` | {name}: "
                    f"{quote.currency} {format_number(quote.price)} "
                    f"({format_pct(quote.change_pct)}) "
                    f"[{quote.freshness.label}]"
                )

    append_etf_section(
        "PERSONAL PORTFOLIO",
        ("XEQT", "VFV", "VCN", "VDY", "HXQ"),
    )
    append_etf_section(
        "ETF WATCHLIST",
        ("VEQT", "XGRO", "XBB", "XIU", "XRE", "XEC", "XEF", "VGRO"),
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


@authorized
async def cmd_bonds(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    data = await bonds.get_all(settings.fred_key)

    curves = [
        (
            "US Treasury Yield Curve",
            data.get("us_curve", {}),
            ("2Y", "3Y", "5Y", "7Y", "10Y"),
            "spread",
            "US",
        ),
        (
            "Canada Government Bond Yield Curve",
            data.get("canada_curve", {}),
            ("2Y", "3Y", "5Y", "7Y", "10Y"),
            "canada_spread",
            "Canada",
        ),
    ]

    lines = []

    for title, curve, tenors, spread_key, country in curves:
        lines.append(f"*{title}*")

        for tenor in tenors:
            y = curve.get(tenor) if isinstance(curve, dict) else None

            if y is None:
                lines.append(f"  {tenor}: Unavailable")
            elif isinstance(y, DataUnavailable):
                lines.append(f"  {tenor}: {format_unavailable(y)}")
            elif isinstance(y, bonds.YieldPoint):
                lines.append(
                    f"  {tenor}: {format_number(y.rate, 3)}%"
                )
            else:
                lines.append(f"  {tenor}: Unavailable")

        spread = data.get(spread_key)

        if isinstance(spread, bonds.CurveSpread):
            lines.append(
                f"  2s10s spread: {format_number(spread.spread_bps, 1)} bps "
                f"({spread.signal})"
            )
        elif isinstance(spread, DataUnavailable):
            lines.append(
                f"  2s10s spread: {format_unavailable(spread)}"
            )

        lines.append("")

    await update.message.reply_text(
        "\n".join(lines).rstrip(),
        parse_mode=ParseMode.MARKDOWN,
    )

@authorized
async def cmd_forex(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    data = await forex.get_forex(settings.fxapi_key)
    rates = data.get("rates", {})

    lines = ["*FOREIGN EXCHANGE*"]

    for pair in forex.PAIR_ORDER:
        rate = rates.get(pair)

        if isinstance(rate, DataUnavailable):
            lines.append(f"  {pair}: {format_unavailable(rate)}")
        elif rate is not None:
            change = (
                format_percent(rate.change_pct)
                if rate.change_pct is not None else "N/A"
            )
            lines.append(
                f"  {pair}: {format_number(rate.rate, 4)} "
                f"({change}) [{rate.freshness.label}]"
            )

    last_updated = data.get("last_updated")
    if last_updated:
        lines.append(
            f"\n_Last updated: {last_updated.strftime('%Y-%m-%d %H:%M UTC')}_"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


@authorized
async def cmd_economic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    command = update.message.text.split()[0].split("@")[0].lstrip("/").lower()
    region = {
        "canada": "CA",
        "us": "US",
        "morocco": "MA",
        "europe": "EU",
    }.get(command, command.upper())
    settings = _settings(ctx)
    data = await economic.get_region(settings.fred_key, region)
    display_region = economic.REGION_NAMES.get(region, region.title())
    lines = [f"*{display_region} Economic Indicators*"]
    if not data:
        lines.append("  No indicators configured for this region.")
    for label, ind in data.items():
        if isinstance(ind, DataUnavailable):
            lines.append(f"• {label}: {format_unavailable(ind)}")
        else:
            value = f"{ind.value:g}{ind.unit}"
            change = (
                f" | Change: {ind.change:+.3f} {ind.change_label}"
                if ind.change is not None and ind.change_label
                else ""
            )
            date = ind.as_of.strftime("%Y-%m-%d") if ind.as_of else "date n/a"
            freshness = ind.freshness.value.upper()
            lines.append(
                f"• {label}: {value}{change}\n"
                f"  {freshness} · {date}"
            )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_weather(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)

    locations = [
        (
            settings.weather_latitude,
            settings.weather_longitude,
            "Montreal, QC",
            "Canada",
            "auto",
        ),
        (34.0209, -6.8416, "Rabat", "Morocco", "GMT"),
    ]

    reports = await asyncio.gather(
        *[
            weather.get_weather(latitude, longitude, city, country, timezone_name)
            for latitude, longitude, city, country, timezone_name in locations
        ]
    )

    montreal_alerts = await weather.get_montreal_alerts()
    sections = []

    for report in reports:
        if isinstance(report, DataUnavailable):
            sections.append(f"*Weather unavailable*\n{format_unavailable(report)}")
            continue

        updated = (
            report.as_of.strftime("%Y-%m-%d %H:%M %Z")
            if report.as_of else "time unavailable"
        )

        lines = [
            f"*Weather — {report.city}, {report.country}*",
            (
                f"Now: {report.current_temp:.0f}°C, {report.condition}"
                if report.current_temp is not None
                else f"Now: {report.condition}"
            ),
        ]

        if report.wind_kmh is not None:
            lines.append(f"Wind: {report.wind_kmh:.0f} km/h")

        if report.high is not None and report.low is not None:
            precip = (
                f"{report.precipitation_prob}%"
                if report.precipitation_prob is not None else "n/a"
            )
            lines.append(
                f"Today: High {report.high:.0f}°C / Low {report.low:.0f}°C "
                f"· Precipitation: {precip}"
            )

        lines.append("_3-day forecast:_")
        for day in report.forecast[:3]:
            lines.append(
                f"• {day.date}: {day.high:.0f}–{day.low:.0f}°C, "
                f"{weather._weather_code(day.code)}, "
                f"{day.precipitation_prob}% precip."
            )

        if report.city == "Montreal, QC":
            if montreal_alerts.status == "UNAVAILABLE":
                lines.append(
                    "Official alerts: Status unavailable "
                    "(Environment Canada feed error)."
                )
            else:
                if montreal_alerts.status == "ACTIVE":
                    lines.append("_Environment Canada alerts:_")
                    for alert in montreal_alerts.alerts:
                        lines.append(f"⚠️ {alert}")
                else:
                    lines.append(
                        "Environment Canada: No active alerts listed "
                        "in the feed. This does not confirm a current all-clear."
                    )

                alert_updated = (
                    montreal_alerts.updated_at.strftime("%Y-%m-%d %H:%M UTC")
                    if montreal_alerts.updated_at else "unknown"
                )
                lines.append(
                    f"Alert feed checked: {montreal_alerts.freshness.value} "
                    f"· Last content update: {alert_updated}"
                )
        else:
            lines.append(
                "Official alerts: Not yet integrated for Rabat."
            )

        lines.append(
            f"_{report.freshness.value.upper()} · Updated {updated}_"
        )
        sections.append("\n".join(lines))

    await update.message.reply_text(
        "\n\n".join(sections),
        parse_mode=ParseMode.MARKDOWN,
    )

@authorized
async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await news.get_news()
    lines = ["*Business, Economics, Markets & Geopolitics*"]

    for region, items in data.items():
        if isinstance(items, DataUnavailable):
            lines.append(f"  {region}: {format_unavailable(items)}")
            continue

        lines.append(f"\n_{region}_")

        for item in items[:5]:
            published = (
                item.published.strftime("%Y-%m-%d %H:%M UTC")
                if item.published else "Time unavailable"
            )
            lines.append(
                f"  • [{item.title}]({item.link}) — {item.source}\n"
                f"    Published: {published} | "
                f"Article: {item.freshness.value} | "
                f"Feed: {item.fetch_freshness.value}"
            )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


@authorized
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    client = get_client()
    stats = await client.cache_stats()
    lines = [
        "*Status*",
        f"  Cache entries: {stats['entries']}",
        f"  Cache hits / misses: {stats['hits']} / {stats['misses']}",
        f"  Cache hit rate: {format_pct(stats['hit_rate_pct'])}",
        f"  Network requests: {stats['network_attempts']}",
        f"  Network successes / failures: {stats['network_successes']} / {stats['network_failures']}",
        f"  Uptime: {stats['uptime_seconds']:.0f}s",
    ]
    settings = _settings(ctx)
    keys = {
        "FRED": bool(settings.fred_key),
        "CoinGecko": bool(settings.coingecko_api_key),
        "Finnhub": bool(settings.finnhub_api_key),
        "Alpha Vantage": bool(settings.alpha_vantage_api_key),
        "FXAPI": bool(settings.fxapi_api_key),
    }
    lines.append("  _API keys:_")
    for name, present in keys.items():
        lines.append(f"    {name}: {'configured' if present else 'not set'}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    s = _settings(ctx)
    lines = [
        "*Configuration*",
        f"  Timezone: {s.timezone}",
        f"  Delivery: {s.delivery_time} {', '.join(('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[day] for day in sorted(s.delivery_days))}",
        f"  City: {s.weather_city}",
        f"  Sections: oil={s.enable_oil}, crypto={s.enable_crypto}, "
        f"stocks={s.enable_stocks}, bonds={s.enable_bonds}, "
        f"forex={s.enable_forex}, weather={s.enable_weather}, "
        f"news={s.enable_news}",
        f"  Regions: canada={s.enable_canada}, us={s.enable_us}, "
        f"morocco={s.enable_morocco}, europe={s.enable_europe}",
        f"  Additional flags: rates={s.enable_rates}, "
        f"economic={s.enable_economic}, calendar={s.enable_calendar}, "
        f"ca_mortgage={s.enable_ca_mortgage}",
    ]
    await update.message.reply_text("\n".join(lines))
