"""Shared number and label formatting.

One implementation, used by the deck, the dashboard payload and the spoken
briefing, so the same figure never appears three different ways in three
places.
"""

from __future__ import annotations

from insight_engine.domain.results import Insight, MetricTotals

_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}


def compact_number(value: float, *, precision: int = 1) -> str:
    """Human-scale magnitude: ``1.2M``, ``34.5K``, ``871``."""
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1_000_000_000:
        return f"{sign}{magnitude / 1_000_000_000:.{precision}f}B"
    if magnitude >= 1_000_000:
        return f"{sign}{magnitude / 1_000_000:.{precision}f}M"
    if magnitude >= 1_000:
        return f"{sign}{magnitude / 1_000:.{precision}f}K"
    if magnitude >= 10:
        return f"{sign}{magnitude:,.0f}"
    if magnitude == 0:
        return "0"
    return f"{sign}{magnitude:,.{max(precision, 2)}f}"


def format_value(
    value: float | None, unit: str = "count", *, precision: int = 2, currency: str = "USD"
) -> str:
    """Render a metric value in its own unit."""
    if value is None:
        return "—"
    if unit == "percent":
        # Ratios are stored as fractions; a CTR of 0.0287 reads as 2.87%.
        return f"{value * 100:,.{precision}f}%"
    if unit == "ratio":
        return f"{value:,.{precision}f}"
    if unit == "currency":
        symbol = _CURRENCY_SYMBOLS.get(currency.upper(), "")
        return f"{symbol}{compact_number(value)}" if abs(value) >= 1000 else f"{symbol}{value:,.2f}"
    if unit == "duration_seconds":
        return format_duration(value)
    return compact_number(value)


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def format_delta_pct(delta_pct: float | None, *, precision: int = 1) -> str:
    """Signed percentage, or an honest dash when the baseline was zero."""
    if delta_pct is None:
        return "n/a"
    return f"{delta_pct:+.{precision}f}%"


def arrow(direction: str) -> str:
    return {"up": "↑", "down": "↓"}.get(direction, "→")


def describe_total(total: MetricTotals) -> str:
    """One-line movement summary suitable for a deck bullet or a log line."""
    change = format_delta_pct(total.delta_pct)
    current = format_value(total.current, total.unit, precision=total.precision)
    previous = format_value(total.previous, total.unit, precision=total.precision)
    if total.delta_pct is None:
        return f"{total.label}: {previous} to {current} (no comparable baseline)"
    return f"{total.label}: {previous} to {current} ({change})"


def describe_insight(insight: Insight) -> str:
    """Segment-level movement summary."""
    current = format_value(insight.current_value, insight.unit, precision=insight.precision)
    previous = format_value(insight.previous_value, insight.unit, precision=insight.precision)
    change = format_delta_pct(insight.delta_pct)
    scope = insight.segment_label
    if insight.is_new_segment:
        return f"{insight.label} for {scope} appeared this period at {current}"
    if insight.is_lost_segment:
        return f"{insight.label} for {scope} disappeared this period (was {previous})"
    contribution = (
        f", {abs(insight.contribution_pct):.0f}% of the total movement"
        if insight.contribution_pct is not None
        else ""
    )
    return f"{insight.label} for {scope}: {previous} to {current} ({change}{contribution})"


def spoken_number(value: float | None, unit: str = "count") -> str:
    """A form a text-to-speech engine reads naturally.

    ``$1.2M`` is read as "dollar one point two em"; "1.2 million dollars" is not.
    """
    if value is None:
        return "not available"
    magnitude = abs(value)
    if unit == "percent":
        return f"{value * 100:.1f} percent"
    if magnitude >= 1_000_000_000:
        number = f"{magnitude / 1_000_000_000:.1f} billion"
    elif magnitude >= 1_000_000:
        number = f"{magnitude / 1_000_000:.1f} million"
    elif magnitude >= 1_000:
        number = f"{magnitude / 1_000:.1f} thousand"
    else:
        number = f"{magnitude:,.0f}" if magnitude >= 10 else f"{magnitude:.2f}"
    if value < 0:
        number = f"negative {number}"
    if unit == "currency":
        number = f"{number} dollars"
    return number


def spoken_change(delta_pct: float | None, direction: str) -> str:
    if delta_pct is None:
        return "changed from a zero baseline"
    verb = {"up": "increased", "down": "decreased"}.get(direction, "held steady")
    if direction == "flat":
        return verb
    return f"{verb} {abs(delta_pct):.1f} percent"
