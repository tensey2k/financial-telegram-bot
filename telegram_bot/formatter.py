"""Telegram message formatting helpers.

Pure formatting: no I/O, no async, no network. Everything here takes typed
connector output and returns strings, so it is trivially testable.

The contract is that every section has a header, every figure carries a
freshness label, and every unavailable section says so plainly. A report
that quietly drops a section is worse than one that prints
"unavailable - no API key", because the reader cannot tell the difference
between "not configured" and "broken".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from data_sources.base import DataUnavailable, Freshness

log = logging.getLogger(__name__)

# Telegram caps messages at 4096 characters. The briefing is assembled
# section by section and truncated at this boundary so a long report is
# delivered in chunks rather than silently cut mid-sentence.
MAX_MESSAGE_LENGTH = 4096


def freshness_suffix(freshness: Freshness) -> str:
    """Human label for a data point's freshness."""
    return {
        Freshness.LIVE: "",
        Freshness.CACHED: " (cached)",
        Freshness.STALE: " (stale)",
        Freshness.UNAVAILABLE: " (unavailable)",
    }.get(freshness, "")


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number with thousands separators and a fixed precision."""
    return f"{value:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """Format a percentage, signed, with a fixed precision."""
    return f"{value:+.{decimals}f}%"


def format_bp(value: float) -> str:
    """Format basis points as a signed integer-ish string."""
    if value == 0.0:
        return "0 bp"
    return f"{value:+.0f} bp"


def _change_str(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return ""
    return f" {format_percent(value, decimals)}"


def _header(title: str, as_of: datetime | None) -> str:
    line = f"*{title}*"
    if as_of is not None:
        line += f"  ·  {as_of.strftime('%Y-%m-%d %H:%M UTC')}"
    return line


def _separator() -> str:
    return "—" * 40


def _unavailable(reason: str) -> str:
    return f"Unavailable — {reason}"


def _ensure(value: object, source: str) -> str:
    """Format a connector return value, handling the DataUnavailable sentinel."""
    if isinstance(value, DataUnavailable):
        return _unavailable(value.reason)
    return str(value)


def format_section(items: Iterable[tuple[str, str]]) -> str:
    """Render a list of (label, value) pairs as a Telegram section."""
    lines: list[str] = []
    for label, value in items:
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def split_for_telegram(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message at paragraph boundaries, under the Telegram cap."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, max_len)
        if cut == -1:
            cut = remaining.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def format_timestamp(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M UTC")


def format_pct(value: float | None, decimals: int = 2) -> str:
    """Format a percentage with sign, handling None gracefully."""
    if value is None:
        return "N/A"
    return format_percent(value, decimals)


def format_unavailable(data: object) -> str:
    """Format a DataUnavailable object or error into a readable status."""
    if isinstance(data, DataUnavailable):
        return f"unavailable ({data.reason})"
    return "unavailable"


def format_quote_line(name: str, price: float | None, change_pct: float | None, suffix: str = "") -> str:
    """Standardized single quote line for financial assets."""
    p_str = format_number(price) if price is not None else "N/A"
    c_str = format_pct(change_pct) if change_pct is not None else "N/A"
    return f"  • {name}: ${p_str} ({c_str}){suffix}"