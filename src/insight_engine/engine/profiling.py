"""Dataset profiling and column-role inference.

When a user uploads a file, the tool has to guess which column is the time
axis, which are segments and which are measures. The previous implementation
shipped the file's contents to a hosted LLM to ask. That is slow, costs money
per upload, fails without an API key, and sends a customer's business data to a
third party purely to classify column headers.

This does it locally from dtypes, cardinality, null density and name patterns.
It runs in milliseconds, works offline, and — because it reads the actual
values — suggests a date range **the data actually covers**. The old flow
defaulted to "the last 14 days from today", which for any historical extract
selected two empty windows and produced an empty report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import polars as pl

from insight_engine.domain.spec import ComparisonSpec

ColumnRole = Literal["date", "dimension", "metric", "identifier", "ignored"]

# Cardinality above this fraction of rows means a column identifies rows rather
# than grouping them, so it is an id, not a segment.
IDENTIFIER_UNIQUENESS = 0.85

# The uniqueness rule needs enough rows to mean anything. In a 7-row sample
# every numeric column is "100% unique", which would classify impressions,
# clicks and spend as identifiers and leave the user with no metrics at all.
MIN_ROWS_FOR_UNIQUENESS = 30
MAX_DIMENSION_CARDINALITY = 500
MAX_SUGGESTED_DIMENSIONS = 4
MAX_SUGGESTED_METRICS = 8
SAMPLE_ROWS = 5

_DATE_NAME = re.compile(
    r"(^|_)(date|day|dt|time|timestamp|created|updated|occurred|period|week|month)($|_)|^dt_",
    re.IGNORECASE,
)
_ID_NAME = re.compile(
    r"(^|_)(id|uuid|guid|key|code|hash|ref|number|no)($|_)|_id$|^id$", re.IGNORECASE
)
_METRIC_NAME = re.compile(
    r"(revenue|sales|amount|cost|spend|price|total|count|qty|quantity|clicks|impressions|"
    r"views|visits|sessions|orders|conversions|profit|margin|income|value|score|duration|mnt)",
    re.IGNORECASE,
)
_YEAR_NAME = re.compile(r"(^|_)(year|yr|birth_?year|year_?birth|fy)($|_)", re.IGNORECASE)
_CURRENCY_NAME = re.compile(
    r"(revenue|spend|cost|price|amount|sales|income|profit|margin|budget)", re.IGNORECASE
)
_DIMENSION_NAME = re.compile(
    r"(country|region|geo|city|state|channel|campaign|source|medium|category|segment|type|"
    r"status|group|brand|product|platform|device|gender|education|marital)",
    re.IGNORECASE,
)


@dataclass
class ColumnProfile:
    """What is known about one column."""

    name: str
    dtype: str
    role: ColumnRole
    null_fraction: float
    distinct_count: int
    distinct_fraction: float
    samples: list[str] = field(default_factory=list)
    confidence: float = 0.5
    reason: str = ""
    suggested_aggregation: str = "sum"
    suggested_unit: str = "count"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "role": self.role,
            "null_fraction": round(self.null_fraction, 4),
            "distinct_count": self.distinct_count,
            "distinct_fraction": round(self.distinct_fraction, 4),
            "samples": self.samples,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "suggested_aggregation": self.suggested_aggregation,
            "suggested_unit": self.suggested_unit,
        }


@dataclass
class DatasetProfile:
    """Column roles plus a date range the data actually covers."""

    row_count: int
    columns: list[ColumnProfile]
    date_column: str | None = None
    date_min: date | None = None
    date_max: date | None = None
    suggested_comparison: ComparisonSpec | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def dimensions(self) -> list[str]:
        return [column.name for column in self.columns if column.role == "dimension"]

    @property
    def metrics(self) -> list[str]:
        return [column.name for column in self.columns if column.role == "metric"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "date_column": self.date_column,
            "date_range": (
                {"start": self.date_min.isoformat(), "end": self.date_max.isoformat()}
                if self.date_min and self.date_max
                else None
            ),
            "suggested_comparison": (
                {
                    "current_start": self.suggested_comparison.current_start.isoformat(),
                    "current_end": self.suggested_comparison.current_end.isoformat(),
                    "previous_start": self.suggested_comparison.previous_start.isoformat(),
                    "previous_end": self.suggested_comparison.previous_end.isoformat(),
                }
                if self.suggested_comparison
                else None
            ),
            "suggested_dimensions": self.dimensions[:MAX_SUGGESTED_DIMENSIONS],
            "suggested_metrics": self.metrics[:MAX_SUGGESTED_METRICS],
            "columns": [column.as_dict() for column in self.columns],
            "notes": self.notes,
        }


def profile_dataframe(frame: pl.DataFrame, *, max_sample_rows: int = 20_000) -> DatasetProfile:
    """Infer column roles and a usable comparison window."""
    sample = frame.head(max_sample_rows)
    rows = max(sample.height, 1)

    profiles: list[ColumnProfile] = []
    for name in sample.columns:
        series = sample[name]
        distinct = int(series.n_unique())
        nulls = int(series.null_count())
        profiles.append(
            _classify(
                name=name,
                series=series,
                rows=rows,
                distinct=distinct,
                null_fraction=nulls / rows,
            )
        )

    date_column = _pick_date_column(profiles)
    notes: list[str] = []
    date_min = date_max = None
    comparison: ComparisonSpec | None = None

    if date_column is not None:
        for profile in profiles:
            if profile.name == date_column:
                profile.role = "date"
            elif profile.role == "date":
                # Only one time axis; demote the rest to dimensions so they stay
                # selectable rather than disappearing.
                profile.role = "dimension"
                profile.reason = "secondary date column, offered as a dimension"

        date_min, date_max = _date_bounds(frame, date_column)
        if date_min and date_max:
            comparison = suggest_comparison(date_min, date_max)
            if comparison is None:
                notes.append(
                    f"The data spans {(date_max - date_min).days + 1} day(s) "
                    f"({date_min} to {date_max}), which is too short to split into two "
                    "non-overlapping periods. Choose the dates manually."
                )
    else:
        notes.append(
            "No date column was detected. Pick one manually, or add a date column to the data."
        )

    if not any(profile.role == "metric" for profile in profiles):
        notes.append("No numeric measures were detected; select at least one metric manually.")

    return DatasetProfile(
        row_count=frame.height,
        columns=profiles,
        date_column=date_column,
        date_min=date_min,
        date_max=date_max,
        suggested_comparison=comparison,
        notes=notes,
    )


def _classify(
    *, name: str, series: pl.Series, rows: int, distinct: int, null_fraction: float
) -> ColumnProfile:
    dtype = series.dtype
    distinct_fraction = distinct / rows
    uniqueness_is_meaningful = rows >= MIN_ROWS_FOR_UNIQUENESS
    samples = [str(value) for value in series.drop_nulls().head(SAMPLE_ROWS).to_list()]

    profile = ColumnProfile(
        name=name,
        dtype=str(dtype),
        role="ignored",
        null_fraction=null_fraction,
        distinct_count=distinct,
        distinct_fraction=distinct_fraction,
        samples=samples,
    )

    if dtype == pl.Date or isinstance(dtype, pl.Datetime):
        profile.role = "date"
        profile.confidence = 0.98
        profile.reason = "parsed as a temporal type"
        return profile

    if dtype == pl.String and _looks_like_dates(samples):
        profile.role = "date"
        profile.confidence = 0.85 if _DATE_NAME.search(name) else 0.7
        profile.reason = "values parse as dates"
        return profile

    if dtype.is_numeric():
        # A numeric column that is almost entirely unique is a key, not a
        # measure; summing customer ids is the classic first-run mistake.
        looks_unique = uniqueness_is_meaningful and distinct_fraction > IDENTIFIER_UNIQUENESS
        if _ID_NAME.search(name) or (looks_unique and not _METRIC_NAME.search(name)):
            profile.role = "identifier"
            profile.confidence = 0.8
            profile.reason = (
                "name looks like an identifier"
                if _ID_NAME.search(name)
                else f"{distinct_fraction:.0%} of values are unique across {rows} rows"
            )
            return profile

        # A year is a period label, not a quantity: summing birth years is
        # meaningless, and it is the single most common auto-detection mistake.
        if dtype.is_integer() and (_YEAR_NAME.search(name) or _looks_like_years(samples)):
            profile.role = "dimension"
            profile.confidence = 0.85
            profile.reason = "values look like calendar years, so this is a period label"
            return profile

        # Low-cardinality integers are usually codes or flags (is_active,
        # rating, tier) rather than quantities worth summing.
        if dtype.is_integer() and distinct <= 12 and not _METRIC_NAME.search(name):
            profile.role = "dimension"
            profile.confidence = 0.65
            profile.reason = f"only {distinct} distinct integer values; likely a code or flag"
            return profile

        profile.role = "metric"
        profile.confidence = 0.9 if _METRIC_NAME.search(name) else 0.7
        profile.reason = "numeric measure"
        profile.suggested_unit = "currency" if _CURRENCY_NAME.search(name) else "count"
        profile.suggested_aggregation = "sum"
        return profile

    if dtype == pl.Boolean:
        profile.role = "dimension"
        profile.confidence = 0.8
        profile.reason = "boolean flag"
        return profile

    if _ID_NAME.search(name) or (
        uniqueness_is_meaningful and distinct_fraction > IDENTIFIER_UNIQUENESS
    ):
        profile.role = "identifier"
        profile.confidence = 0.75
        profile.reason = "high-cardinality text; looks like an identifier"
        return profile

    if distinct <= MAX_DIMENSION_CARDINALITY:
        profile.role = "dimension"
        profile.confidence = 0.9 if _DIMENSION_NAME.search(name) else 0.7
        profile.reason = f"{distinct} distinct categorical values"
        return profile

    profile.role = "ignored"
    profile.confidence = 0.6
    profile.reason = f"{distinct} distinct values is too many to segment by"
    return profile


def _looks_like_years(samples: list[str]) -> bool:
    """Whether every sampled value is a plausible four-digit calendar year."""
    if len(samples) < 3:
        return False
    years = 0
    for value in samples:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return False
        if 1900 <= number <= 2100:
            years += 1
    return years == len(samples)


def _looks_like_dates(samples: list[str]) -> bool:
    if not samples:
        return False
    pattern = re.compile(r"^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\s*\d{1,2}[-/]\d{1,2}[-/]\d{4}")
    return sum(bool(pattern.match(value)) for value in samples) >= max(1, len(samples) // 2)


def _pick_date_column(profiles: list[ColumnProfile]) -> str | None:
    """Highest-confidence date column, preferring a date-like name on a tie."""
    candidates = [profile for profile in profiles if profile.role == "date"]
    if not candidates:
        return None
    candidates.sort(
        key=lambda profile: (profile.confidence, bool(_DATE_NAME.search(profile.name))),
        reverse=True,
    )
    return candidates[0].name


def _date_bounds(frame: pl.DataFrame, column: str) -> tuple[date | None, date | None]:
    from insight_engine.engine.aggregate import coerce_date_column

    try:
        dated = coerce_date_column(frame, column)
    except Exception:
        return None, None
    if dated.height == 0:
        return None, None
    bounds = dated.select(pl.col(column).min().alias("lo"), pl.col(column).max().alias("hi")).row(0)
    low, high = bounds
    return (low if isinstance(low, date) else None, high if isinstance(high, date) else None)


def suggest_comparison(
    date_min: date, date_max: date, *, preferred_days: int = 7
) -> ComparisonSpec | None:
    """Two adjacent, equal-length windows ending at the last day with data.

    Anchoring to ``date_max`` rather than today is what makes the suggestion
    usable on a historical extract.
    """
    span = (date_max - date_min).days + 1
    if span < 2:
        return None

    window = min(preferred_days, span // 2)
    if window < 1:
        return None

    try:
        return ComparisonSpec.trailing(anchor=date_max, days=window)
    except ValueError:
        return None
