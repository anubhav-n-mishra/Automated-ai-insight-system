"""Period splitting and aggregation.

Two rules govern this module, and both were broken in the previous generation:

1. Each metric is reduced with **its own** aggregation. Summing an average, a
   ratio or a distinct count produces a number with no meaning.
2. **Derived metrics are computed after aggregation**, from aggregated inputs.
   Summing per-row ``clicks / impressions`` is not the period CTR; it is the
   sum of daily ratios, which is not a quantity anybody wants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from insight_engine.core.errors import DataError
from insight_engine.core.logging import get_logger
from insight_engine.domain.spec import AnalysisSpec, MetricSpec
from insight_engine.engine import formula

logger = get_logger("engine.aggregate")

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y%m%d")


@dataclass(frozen=True)
class PeriodFrames:
    """Aggregated current and previous periods, plus raw row counts."""

    current: pl.DataFrame
    previous: pl.DataFrame
    current_rows: int
    previous_rows: int


def coerce_date_column(frame: pl.DataFrame, column: str) -> pl.DataFrame:
    """Normalise ``column`` to a Polars ``Date``.

    Tries the declared formats before letting Polars infer, because inference
    resolves ``03/04/2025`` differently depending on the rows it happens to see.
    """
    if column not in frame.columns:
        raise DataError(f"Date column {column!r} is not present in the data")

    dtype = frame.schema[column]
    if dtype == pl.Date:
        return frame
    if isinstance(dtype, pl.Datetime):
        return frame.with_columns(pl.col(column).dt.date().alias(column))
    if dtype.is_numeric():
        raise DataError(
            f"Date column {column!r} holds numbers, not dates. "
            "A year column is a dimension, not a time axis.",
            context={"column": column, "dtype": str(dtype)},
        )

    text = pl.col(column).cast(pl.String).str.strip_chars()
    for fmt in _DATE_FORMATS:
        parsed = frame.with_columns(text.str.to_date(fmt, strict=False).alias(column))
        if parsed[column].null_count() < frame.height:
            null_count = parsed[column].null_count()
            if null_count:
                logger.warning(
                    "some dates failed to parse",
                    extra={"column": column, "unparsed": null_count, "format": fmt},
                )
            return parsed.drop_nulls(subset=[column])

    sample = [str(value) for value in frame[column].head(3).to_list()]
    unparseable = DataError(
        f"Could not interpret {column!r} as dates. Give the dates as YYYY-MM-DD, or point "
        "the report at a different column.",
        context={"column": column, "sample": sample},
    )

    # `strict=False` suppresses per-row failures, not a failure to infer any
    # format at all, so inference still raises when nothing in the column looks
    # like a date. That is a data problem, not a crash.
    try:
        parsed = frame.with_columns(
            pl.coalesce(
                text.str.to_datetime(strict=False).dt.date(),
                text.str.to_date(strict=False),
            ).alias(column)
        )
    except pl.exceptions.PolarsError as error:
        raise unparseable from error

    if parsed[column].null_count() == frame.height:
        raise unparseable
    return parsed.drop_nulls(subset=[column])


def _aggregation_expr(metric: MetricSpec) -> pl.Expr:
    column = pl.col(metric.source_column)
    numeric = column.cast(pl.Float64, strict=False)
    match metric.aggregation:
        case "sum":
            return numeric.sum().alias(metric.name)
        case "avg":
            return numeric.mean().alias(metric.name)
        case "min":
            return numeric.min().alias(metric.name)
        case "max":
            return numeric.max().alias(metric.name)
        case "median":
            return numeric.median().alias(metric.name)
        case "count":
            return column.count().cast(pl.Float64).alias(metric.name)
        case "count_distinct":
            return column.n_unique().cast(pl.Float64).alias(metric.name)
    raise DataError(f"Unsupported aggregation {metric.aggregation!r} for metric {metric.name!r}")


def slice_period(frame: pl.DataFrame, column: str, start: date, end: date) -> pl.DataFrame:
    """Rows within an inclusive date window."""
    return frame.filter((pl.col(column) >= start) & (pl.col(column) <= end))


def aggregate(
    frame: pl.DataFrame,
    spec: AnalysisSpec,
    *,
    dimensions: list[str] | None = None,
) -> pl.DataFrame:
    """Group by ``dimensions`` and reduce each metric with its own aggregation.

    Derived metrics are appended afterwards, computed from the aggregated
    columns. Passing ``dimensions=[]`` produces the single-row grand total.
    """
    group_by = spec.report.dimensions if dimensions is None else dimensions
    metrics = spec.base_metrics
    if not metrics:
        raise DataError("The specification defines no base metrics to aggregate")

    exprs = [_aggregation_expr(metric) for metric in metrics]

    if group_by:
        missing = sorted(set(group_by) - set(frame.columns))
        if missing:
            raise DataError(
                f"Dimension column(s) not present in the data: {', '.join(missing)}",
                context={"missing": missing, "available": frame.columns[:50]},
            )
        # Null dimension values become an explicit bucket instead of vanishing
        # from the join, which would otherwise look like a lost segment.
        normalised = frame.with_columns(
            [pl.col(dimension).cast(pl.String).fill_null("(not set)") for dimension in group_by]
        )
        aggregated = normalised.group_by(group_by).agg(exprs).sort(group_by)
    else:
        aggregated = frame.select(exprs)

    return _append_derived(aggregated, spec)


def _append_derived(frame: pl.DataFrame, spec: AnalysisSpec) -> pl.DataFrame:
    """Add derived metrics, each computed from already-aggregated columns."""
    if not spec.derived_metrics:
        return frame

    result = frame
    for derived in spec.derived_metrics:
        available = set(result.columns)
        expression = formula.to_polars(derived.expression, available=available, alias=derived.name)
        result = result.with_columns(expression)
    return result


def split_and_aggregate(frame: pl.DataFrame, spec: AnalysisSpec) -> PeriodFrames:
    """Slice both windows out of ``frame`` and aggregate each to segment level."""
    comparison = spec.report.comparison
    column = spec.report.date_column

    dated = coerce_date_column(frame, column)
    current_rows = slice_period(dated, column, comparison.current_start, comparison.current_end)
    previous_rows = slice_period(dated, column, comparison.previous_start, comparison.previous_end)

    if current_rows.height == 0 and previous_rows.height == 0:
        # Both aggregations default to the same output name, so they must be
        # aliased apart before selecting them together.
        observed = dated.select(
            pl.col(column).min().alias("observed_start"),
            pl.col(column).max().alias("observed_end"),
        ).row(0)
        raise DataError(
            "Neither period contains any rows. The configured date range does not "
            f"overlap the data, which spans {observed[0]} to {observed[1]}.",
            context={
                "requested": comparison.describe(),
                "data_start": str(observed[0]),
                "data_end": str(observed[1]),
            },
        )
    if current_rows.height == 0:
        raise DataError(
            f"The current period ({comparison.current_start}..{comparison.current_end}) "
            "contains no rows.",
            context={"requested": comparison.describe()},
        )

    cardinality = _segment_count(current_rows, spec.report.dimensions)
    logger.info(
        "period split",
        extra={
            "current_rows": current_rows.height,
            "previous_rows": previous_rows.height,
            "segments": cardinality,
        },
    )

    return PeriodFrames(
        current=aggregate(current_rows, spec),
        previous=aggregate(previous_rows, spec),
        current_rows=current_rows.height,
        previous_rows=previous_rows.height,
    )


def grand_totals(frame: pl.DataFrame, spec: AnalysisSpec) -> dict[str, float | None]:
    """Metric totals over a whole period, ignoring dimensions."""
    if frame.height == 0:
        return dict.fromkeys(spec.metric_names)

    aggregated = aggregate(frame, spec, dimensions=[])
    if aggregated.height == 0:
        return dict.fromkeys(spec.metric_names)

    row = aggregated.row(0, named=True)
    return {name: _as_float(row.get(name)) for name in spec.metric_names}


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _segment_count(frame: pl.DataFrame, dimensions: list[str]) -> int:
    if not dimensions or frame.height == 0:
        return 1
    return frame.select(dimensions).unique().height
