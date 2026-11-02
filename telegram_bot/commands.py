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
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Settings, load_settings
from data_sources.base import DataUnavailable, get_client
from data_sources import bonds, crypto, economic, forex, news, oil, stocks, weather
from telegram_bot.authorization import authorized
from telegram_bot.formatter import (
    format_bp,
    format_number,
    format_pct,
    format_quote_line,
    format_unavailable,
    split_for_telegram,
)

log = logging.getLogger(__name__)


def _settings(ctx: ContextTypes.DEFAULT_TYPE) -> Settings:
    return ctx.bot_data["settings"]


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
        "/briefing - full daily intelligence report\n"
        "/oil - Brent and WTI crude\n"
        "/crypto - BTC, ETH, SOL\n"
        "/stocks - major global indices\n"
        "/bonds - US yield curve and 2s10s spread\n"
        "/forex - CAD crosses and ECB reference\n"
        "/canada - Canadian economic indicators\n"
        "/us - US economic indicators\n"
        "/morocco - Moroccan economic indicators\n"
        "/europe - European economic indicators\n"
        "/weather - local forecast and alerts\n"
        "/news - regional headlines\n"
        "/status - API availability and cache state\n"
        "/settings - current configuration",
        parse_mode=ParseMode.MARKDOWN,
    )


@authorized
async def cmd_briefing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from briefing.generator import build_briefing

    settings = _settings(ctx)
    await update.message.reply_text("Building briefing...")
    try:
        chunks = await build_briefing(settings)
    except Exception as exc:
        log.exception("Briefing generation failed")
        await update.message.reply_text(f"Briefing failed: {exc}")
        return
    for chunk in chunks:
        await update.message.reply_text(
            chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
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
    for sym in ("BTC", "ETH", "SOL"):
        quote = data.get(sym)
        if isinstance(quote, DataUnavailable):
            lines.append(f"  {sym}: {format_unavailable(quote)}")
        else:
            lines.append(
                f"  {sym}: ${format_number(quote.price)} "
                f"({format_pct(quote.change_24h_pct)})"
            )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_stocks(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    data = await stocks.get_indices(settings.finnhub_api_key)
    lines = ["*Global Indices*"]
    for name, idx in data.items():
        if isinstance(idx, DataUnavailable):
            lines.append(f"  {name}: {format_unavailable(idx)}")
        else:
            lines.append(
                f"  {name}: {format_number(idx.value)} "
                f"({format_pct(idx.change_pct)}) [{idx.freshness.label}]"
            )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_bonds(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    data = await bonds.get_all(settings.fred_api_key)
    lines = ["*US Yield Curve*"]
    curve = data.get("us_curve", {})
    for tenor in ("3m", "2y", "10y", "30y"):
        y = curve.get(tenor)
        if isinstance(y, DataUnavailable):
            lines.append(f"  {tenor.upper()}: {format_unavailable(y)}")
        else:
            lines.append(f"  {tenor.upper()}: {format_number(y.value, 3)}%")
    spread = data.get("spread")
    if isinstance(spread, bonds.CurveSpread):
        lines.append(
            f"  2s10s spread: {format_number(spread.spread_bps, 1)} bps "
            f"({spread.signal})"
        )
    elif isinstance(spread, DataUnavailable):
        lines.append(f"  2s10s: {format_unavailable(spread)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_forex(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await forex.get_forex()
    lines = ["*FX (CAD crosses)*"]
    crosses = data.get("cad_crosses", {})
    for pair in ("EUR/CAD", "GBP/CAD", "CAD/USD"):
        rate = crosses.get(pair)
        if isinstance(rate, DataUnavailable):
            lines.append(f"  {pair}: {format_unavailable(rate)}")
        else:
            lines.append(f"  {pair}: {format_number(rate.rate, 4)} [{rate.freshness.label}]")
    problems = data.get("discrepancies", [])
    if problems:
        lines.append(f"  _ECB cross-check mismatch: {', '.join(problems)}_")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_economic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    region = ctx.matches[0].group(1) if ctx.matches else "canada"
    settings = _settings(ctx)
    data = await economic.get_region(settings.fred_api_key, region)
    lines = [f"*{region.title()} indicators*"]
    if not data:
        lines.append("  No indicators configured for this region.")
    for label, ind in data.items():
        if isinstance(ind, DataUnavailable):
            lines.append(f"  {label}: {format_unavailable(ind)}")
        else:
            change = f" ({ind.change})" if ind.change else ""
            lines.append(f"  {label}: {ind.value}{ind.unit} {change}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_weather(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    report = await weather.get_weather(settings)
    if isinstance(report, DataUnavailable):
        await update.message.reply_text(f"Weather: {format_unavailable(report)}")
        return
    lines = [f"*{report.city} weather*"]
    if report.current_temp is not None:
        lines.append(f"  Now: {report.current_temp:.0f}°C, {report.condition}")
    if report.forecast:
        lines.append("  _Forecast:_")
        for day in report.forecast[:3]:
            lines.append(
                f"    {day.date}: {day.temp_min:.0f}–{day.temp_max:.0f}°C "
                f"{day.condition}"
            )
    if report.alerts:
        lines.append(f"  ⚠ {len(report.alerts)} active alert(s)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@authorized
async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    data = await news.get_news()
    lines = ["*Headlines*"]
    for region, items in data.items():
        if isinstance(items, DataUnavailable):
            lines.append(f"  {region}: {format_unavailable(items)}")
            continue
        lines.append(f"\n_{region}_")
        for item in items[:5]:
            lines.append(f"  • [{item.title}]({item.link}) - {item.source}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
    )


@authorized
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    client = get_client()
    stats = client.cache_stats()
    lines = [
        "*Status*",
        f"  Cache entries: {stats['entries']}",
        f"  Hit rate: {format_pct(stats['hit_rate_pct'])}",
        f"  Uptime: {stats['uptime_seconds']:.0f}s",
    ]
    settings = _settings(ctx)
    keys = {
        "FRED": bool(settings.fred_api_key),
        "CoinGecko": bool(settings.coingecko_api_key),
        "Finnhub": bool(settings.finnhub_api_key),
        "Alpha Vantage": bool(settings.alpha_vantage_api_key),
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
        f"  Delivery: {s.delivery_time} {', '.join(s.delivery_days)}",
        f"  City: {s.weather_city}",
        f"  Sections: oil={s.enable_oil}, crypto={s.enable_crypto}, "
        f"stocks={s.enable_stocks}, bonds={s.enable_bonds}, "
        f"forex={s.enable_forex}, weather={s.enable_weather}, "
        f"news={s.enable_news}",
        f"  Regions: canada={s.enable_canada}, us={s.enable_us}, "
        f"morocco={s.enable_morocco}, europe={s.enable_europe}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)