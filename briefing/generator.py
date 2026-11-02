"""Daily intelligence briefing orchestrator and text builder.

Gathers market quotes, macro indicators, RSS headlines, and local weather
concurrently using the data connectors, formats each enabled section, and
returns a list of message chunks ready for Telegram delivery.

Every section header is prefixed with its emoji anchor. Optional API keys
are checked at gather time, and any section whose key is missing or whose
connector fails is rendered with an explicit 'unavailable' label rather
than silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from config import Settings
from data_sources.base import DataUnavailable, Freshness
from data_sources import (
    bonds,
    crypto,
    economic,
    forex,
    news,
    oil,
    stocks,
    weather,
)
from telegram_bot.formatter import (
    format_bp,
    format_number,
    format_percent,
    format_section,
    freshness_suffix,
    split_for_telegram,
)

log = logging.getLogger(__name__)


async def build_briefing(settings: Settings) -> list[str]:
    """Gather all enabled data sources and return formatted Telegram chunks."""
    now = datetime.now(timezone.utc)
    sections: list[str] = [
        f"DAILY INTELLIGENCE BRIEFING\n{now.strftime('%A, %B %d, %Y - %H:%M UTC')}\n"
    ]

    # Concurrent gather for speed during the 07:00 delivery window
    tasks = {}
    if settings.enable_weather:
        tasks["weather"] = weather.get_weather(
            settings.weather_latitude,
            settings.weather_longitude,
            settings.weather_city,
            settings.weather_country,
        )
    if settings.enable_oil:
        tasks["oil"] = oil.get_oil()
    if settings.enable_crypto:
        tasks["crypto"] = crypto.get_crypto_quotes(settings.coingecko_key)
    if settings.enable_stocks:
        tasks["stocks"] = stocks.get_indices(settings.finnhub_key)
    if settings.enable_bonds:
        tasks["bonds"] = bonds.get_bonds(settings.fred_key)
    if settings.enable_forex:
        tasks["forex"] = forex.get_forex()
    if settings.enable_economic:
        tasks["economic"] = economic.get_indicators(settings.fred_key)
    if settings.enable_news:
        tasks["news"] = news.get_news()

    results = {}
    if tasks:
        keys = list(tasks.keys())
        raw_results = await asyncio.gather(
            *tasks.values(), return_exceptions=True
        )
        for k, res in zip(keys, raw_results):
            if isinstance(res, Exception):
                log.error("Error gathering section %s: %s", k, res)
                results[k] = DataUnavailable(source=k, reason=str(res))
            else:
                results[k] = res

    # 1. Weather
    if "weather" in results:
        w_res = results["weather"]
        if isinstance(w_res, weather.WeatherReport):
            w_text = (
                f"*Weather - {w_res.city}, {w_res.country}*{freshness_suffix(w_res.freshness)}\n"
                f"Current: {w_res.current_temp}°C, {w_res.condition}\n"
                f"Today: High {w_res.high}°C / Low {w_res.low}°C  ·  Precip: {w_res.precipitation_prob}%\n"
            )
            if w_res.alerts:
                w_text += "Alerts:\n" + "\n".join(f"  • {a}" for a in w_res.alerts) + "\n"
            sections.append(w_text)
        else:
            sections.append(f"*Weather*\nUnavailable - {getattr(w_res, 'reason', 'Failed to load')}\n")

    # 2. Markets (Oil, Crypto, Stocks)
    market_lines = []
    if "oil" in results:
        o_res = results["oil"]
        if isinstance(o_res, dict):
            for k, q in o_res.items():
                if isinstance(q, oil.OilQuote):
                    suffix = freshness_suffix(q.freshness)
                    market_lines.append(
                        f"  • {q.name}: ${format_number(q.price)} ({format_percent(q.change_pct)}){suffix}"
                    )
    if "crypto" in results:
        c_res = results["crypto"]
        if isinstance(c_res, dict):
            for sym, q in c_res.items():
                if isinstance(q, crypto.CryptoQuote):
                    suffix = freshness_suffix(q.freshness)
                    market_lines.append(
                        f"  • {sym}: ${format_number(q.price)} ({format_percent(q.change_24h_pct)}){suffix}"
                    )
    if "stocks" in results:
        s_res = results["stocks"]
        if isinstance(s_res, dict):
            for name, idx in s_res.items():
                if isinstance(idx, stocks.StockIndex):
                    suffix = freshness_suffix(idx.freshness)
                    market_lines.append(
                        f"  • {name}: {format_number(idx.price)} ({format_percent(idx.change_pct)}){suffix}"
                    )

    if market_lines:
        sections.append("*Markets*\n" + "\n".join(market_lines) + "\n")

    # 3. Bonds & Yields
    if "bonds" in results:
        b_res = results["bonds"]
        if isinstance(b_res, dict):
            b_lines = ["*Rates & Bond Yields*"]
            curve = b_res.get("us_curve", {})
            if isinstance(curve, dict):
                c_pts = []
                for tenor in ["13W", "2Y", "5Y", "10Y", "30Y"]:
                    pt = curve.get(tenor)
                    if isinstance(pt, bonds.YieldPoint):
                        c_pts.append(f"{tenor}: {pt.rate:.2f}%")
                if c_pts:
                    b_lines.append("US Curve: " + "  ·  ".join(c_pts))
            spread = b_res.get("spread")
            if isinstance(spread, bonds.CurveSpread):
                inv_flag = " (INVERTED - Recession Warning)" if spread.inverted else ""
                b_lines.append(f"2s10s Spread: {format_bp(spread.spread_bp)}{inv_flag}")
            sections.append("\n".join(b_lines) + "\n")

    # 4. Forex
    if "forex" in results:
        fx_res = results["forex"]
        if isinstance(fx_res, dict):
            crosses = fx_res.get("cad_crosses", {})
            if isinstance(crosses, dict):
                fx_lines = ["*Foreign Exchange (CAD)*"]
                for pair, q in crosses.items():
                    if isinstance(q, forex.FxRate):
                        fx_lines.append(f"  • {pair}: {q.rate:.4f}")
                sections.append("\n".join(fx_lines) + "\n")

    # 5. Economic Indicators
    if "economic" in results:
        e_res = results["economic"]
        if isinstance(e_res, dict):
            e_lines = ["*Macroeconomic Indicators*"]
            for region, ind_dict in e_res.items():
                if isinstance(ind_dict, dict):
                    reg_name = economic.REGION_NAMES.get(region, region)
                    e_lines.append(f"\n_{reg_name}_")
                    for label, ind in ind_dict.items():
                        if isinstance(ind, economic.Indicator):
                            chg = f" ({format_percent(ind.change)})" if ind.change is not None else ""
                            e_lines.append(f"  • {label}: {ind.value}{ind.unit}{chg}")
                        elif isinstance(ind, DataUnavailable):
                            e_lines.append(f"  • {label}: unavailable ({ind.reason})")
            sections.append("\n".join(e_lines) + "\n")

    # 6. Headlines / News
    if "news" in results:
        n_res = results["news"]
        if isinstance(n_res, dict):
            n_lines = ["*Global Headlines*"]
            for reg, items in n_res.items():
                if isinstance(items, list) and items:
                    n_lines.append(f"\n_{reg}_")
                    for item in items[:3]:  # Top 3 headlines per region to keep it concise
                        n_lines.append(f"  • [{item.title}]({item.link}) - {item.source}")
            sections.append("\n".join(n_lines) + "\n")

    full_text = "\n".join(sections)
    return split_for_telegram(full_text)