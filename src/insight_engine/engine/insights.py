"""Comparison, ranking and driver attribution.

Ranking is the part of an insight tool that decides what a human reads first,
so the score is defined explicitly rather than tuned by feel:

.. code-block:: text

    impact = contribution_share x priority_weight x materiality

``contribution_share``
    The segment's share of the metric's total *absolute* movement. A segment
    that moved by 40 out of 100 total movement scores 0.4. This is what makes
    scores comparable across metrics measured in different units — the previous
    generation multiplied raw deltas, so a metric denominated in impressions
    always outranked one denominated in currency.

``priority_weight``
    Position in ``kpi_priority``, decaying as ``1 / (1 + rank)`` and normalised
    so the first KPI scores 1.0.

``materiality``
    ``sqrt`` of the segment's share of the current-period total. This is the
    guard against the classic false headline: a segment with three impressions
    growing to nine is +200% and means nothing. The square root damps rather
    than eliminates, so a small-but-real segment can still surface.

Segments below ``report.min_segment_share`` are dropped before ranking.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

import polars as pl

from insight_engine.core.logging import get_logger
from insight_engine.domain.results import (
    DriverAttribution,
    Insight,
    MetricTotals,
    direction_of,
    percent_change,
)
from insight_engine.domain.spec import AnalysisSpec, DerivedMetricSpec, MetricSpec
from insight_engine.engine.aggregate import PeriodFrames

logger = get_logger("engine.insights")

CURRENT_SUFFIX = "__current"
PREVIOUS_SUFFIX = "__previous"

#: Presence markers, captured before missing values are zero-filled.
PRESENT_CURRENT = "__present_current"
PRESENT_PREVIOUS = "__present_previous"


@dataclass(frozen=True)
class _MetricView:
    """Everything ranking needs about a metric, base or derived."""

    name: str
    label: str
    unit: str
    precision: int
    higher_is_better: bool
    is_additive: bool


def _views(spec: AnalysisSpec) -> dict[str, _MetricView]:
    views: dict[str, _MetricView] = {}
    for metric in spec.base_metrics:
        views[metric.name] = _MetricView(
            name=metric.name,
            label=metric.display_label,
            unit=metric.unit,
            precision=metric.format_precision,
            higher_is_better=metric.higher_is_better,
            is_additive=metric.is_additive,
        )
    for derived in spec.derived_metrics:
        views[derived.name] = _MetricView(
            name=derived.name,
            label=derived.display_label,
            unit=derived.unit,
            precision=derived.format_precision,
            higher_is_better=derived.higher_is_better,
            # A ratio does not add up across segments, so its movements must not
            # be summed into a contribution share.
            is_additive=False,
        )
    return views


def _priority_weights(spec: AnalysisSpec) -> dict[str, float]:
    """Normalised ``1 / (1 + rank)`` weights; unlisted metrics get the tail weight."""
    ranked = spec.ranked_kpis
    if not ranked:
        return {}
    weights = {name: 1.0 / (1.0 + index) for index, name in enumerate(ranked)}
    tail = 1.0 / (1.0 + len(ranked))
    for name in spec.metric_names:
        weights.setdefault(name, tail)
    return weights


def compare_totals(
    spec: AnalysisSpec,
    current: dict[str, float | None],
    previous: dict[str, float | None],
) -> list[MetricTotals]:
    """Period-over-period movement for each metric at the grand-total level."""
    views = _views(spec)
    totals: list[MetricTotals] = []
    for name in spec.metric_names:
        view = views[name]
        totals.append(
            MetricTotals.build(
                metric=name,
                label=view.label,
                unit=view.unit,
                current=float(current.get(name) or 0.0),
                previous=float(previous.get(name) or 0.0),
                higher_is_better=view.higher_is_better,
                precision=view.precision,
            )
        )
    return totals


def join_periods(periods: PeriodFrames, spec: AnalysisSpec) -> pl.DataFrame:
    """Align the two aggregated periods on the dimension key.

    A full outer join with ``coalesce=True`` is what keeps the dimension columns
    populated for segments present in only one period; without it the key
    columns come back null on one side and every such segment renders as blank.

    Only additive metrics are zero-filled. Filling a ratio with 0 would assert
    "the CTR was zero" when the truth is "this segment did not exist".
    """
    dimensions = spec.report.dimensions
    views = _views(spec)
    metric_names = [name for name in spec.metric_names if name in periods.current.columns]

    current = periods.current.rename({name: f"{name}{CURRENT_SUFFIX}" for name in metric_names})
    previous = periods.previous.rename(
        {
            name: f"{name}{PREVIOUS_SUFFIX}"
            for name in metric_names
            if name in periods.previous.columns
        }
    )

    if not dimensions:
        if previous.height == 0:
            previous = pl.DataFrame({f"{name}{PREVIOUS_SUFFIX}": [0.0] for name in metric_names})
        joined = current.join(previous, how="cross")
    else:
        if previous.height == 0:
            previous = pl.DataFrame(
                schema={
                    **dict.fromkeys(dimensions, pl.String),
                    **{f"{name}{PREVIOUS_SUFFIX}": pl.Float64 for name in metric_names},
                }
            )
        joined = current.join(previous, on=dimensions, how="full", coalesce=True)

    # Record which side each segment appeared on *before* zero-filling.
    # Filling first destroys the distinction between "this segment had zero"
    # and "this segment did not exist", which is exactly what the new/lost
    # flags report.
    if dimensions and metric_names:
        probe = metric_names[0]
        joined = joined.with_columns(
            pl.col(f"{probe}{CURRENT_SUFFIX}").is_not_null().alias(PRESENT_CURRENT),
            (
                pl.col(f"{probe}{PREVIOUS_SUFFIX}").is_not_null()
                if f"{probe}{PREVIOUS_SUFFIX}" in joined.columns
                else pl.lit(False).alias(PRESENT_PREVIOUS)
            ).alias(PRESENT_PREVIOUS),
        )

    fills: list[pl.Expr] = []
    for name in metric_names:
        if not views[name].is_additive:
            continue
        for suffix in (CURRENT_SUFFIX, PREVIOUS_SUFFIX):
            column = f"{name}{suffix}"
            if column in joined.columns:
                fills.append(pl.col(column).fill_null(0.0).alias(column))
    if fills:
        joined = joined.with_columns(fills)

    return joined


def _insight_id(metric: str, segment: dict[str, str]) -> str:
    """Stable identifier, so a client can diff two runs of the same report."""
    material = metric + "|" + "|".join(f"{k}={v}" for k, v in sorted(segment.items()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_insights(
    periods: PeriodFrames,
    spec: AnalysisSpec,
    totals: list[MetricTotals],
) -> list[Insight]:
    """Rank every metric/segment movement by impact."""
    views = _views(spec)
    weights = _priority_weights(spec)
    dimensions = spec.report.dimensions
    joined = join_periods(periods, spec)

    if joined.height == 0:
        return []

    totals_by_metric = {total.metric: total for total in totals}
    rows = joined.to_dicts()

    #: Denominators for contribution share, per metric.
    movement_totals: dict[str, float] = {}
    current_totals: dict[str, float] = {}
    for name in spec.metric_names:
        current_key, previous_key = f"{name}{CURRENT_SUFFIX}", f"{name}{PREVIOUS_SUFFIX}"
        if current_key not in joined.columns:
            continue
        movement = 0.0
        magnitude = 0.0
        for row in rows:
            current_value = _number(row.get(current_key))
            previous_value = _number(row.get(previous_key))
            if current_value is None and previous_value is None:
                continue
            movement += abs((current_value or 0.0) - (previous_value or 0.0))
            magnitude += abs(current_value or 0.0)
        movement_totals[name] = movement
        current_totals[name] = magnitude

    insights: list[Insight] = []
    for row in rows:
        appeared = bool(row.get(PRESENT_CURRENT, True))
        existed_before = bool(row.get(PRESENT_PREVIOUS, True))
        segment = {
            dimension: str(row.get(dimension) if row.get(dimension) is not None else "(not set)")
            for dimension in dimensions
        }
        for name in spec.metric_names:
            current_key, previous_key = f"{name}{CURRENT_SUFFIX}", f"{name}{PREVIOUS_SUFFIX}"
            if current_key not in joined.columns:
                continue

            view = views[name]
            current_raw = _number(row.get(current_key))
            previous_raw = _number(row.get(previous_key))
            if current_raw is None and previous_raw is None:
                continue

            current_value = current_raw or 0.0
            previous_value = previous_raw or 0.0
            delta = current_value - previous_value
            direction = direction_of(delta)

            movement_total = movement_totals.get(name, 0.0)
            contribution = abs(delta) / movement_total if movement_total > 0 else 0.0

            magnitude_total = current_totals.get(name, 0.0)
            share = abs(current_value) / magnitude_total if magnitude_total > 0 else 0.0
            if dimensions and share < spec.report.min_segment_share and abs(delta) > 0:
                # Immaterial segment: keep it out of the ranking entirely rather
                # than letting a large percentage on a tiny base take the lead.
                continue

            materiality = math.sqrt(share) if share > 0 else 0.0
            if not dimensions:
                contribution, materiality = 1.0, 1.0

            weight = weights.get(name, 0.25)
            impact = contribution * weight * materiality

            good = (direction == "up") == view.higher_is_better
            sentiment = "neutral" if direction == "flat" else ("positive" if good else "negative")

            total = totals_by_metric.get(name)
            total_delta = total.delta if total else 0.0
            contribution_pct = (
                (delta / total_delta) * 100.0
                if view.is_additive and total_delta not in (0.0, None) and dimensions
                else None
            )

            insights.append(
                Insight(
                    id=_insight_id(name, segment),
                    metric=name,
                    label=view.label,
                    unit=view.unit,
                    segment=segment,
                    current_value=round(current_value, 6),
                    previous_value=round(previous_value, 6),
                    delta=round(delta, 6),
                    delta_pct=(
                        None
                        if (change := percent_change(current_value, previous_value)) is None
                        else round(change, 4)
                    ),
                    direction=direction,
                    sentiment=sentiment,
                    impact_score=round(impact, 6),
                    contribution_pct=None
                    if contribution_pct is None
                    else round(contribution_pct, 2),
                    share_of_total=round(share, 6) if dimensions else None,
                    is_new_segment=bool(dimensions and appeared and not existed_before),
                    is_lost_segment=bool(dimensions and existed_before and not appeared),
                    precision=view.precision,
                )
            )

    ranked = sorted(
        (
            i
            for i in insights
            if i.impact_score >= spec.report.min_impact_score and i.direction != "flat"
        ),
        key=lambda insight: (insight.impact_score, abs(insight.delta)),
        reverse=True,
    )
    logger.info(
        "insights ranked",
        extra={"candidates": len(insights), "kept": min(len(ranked), spec.report.top_insights)},
    )
    return ranked[: spec.report.top_insights]


def attribute_drivers(
    insights: list[Insight],
    totals: list[MetricTotals],
    spec: AnalysisSpec,
) -> list[DriverAttribution]:
    """Decompose each metric's total movement across its largest segments.

    Only additive metrics are decomposed: segment deltas of a ratio do not sum
    to the ratio's movement, so presenting them as "drivers" would be a lie
    dressed as arithmetic.
    """
    if not spec.report.include_drivers or not spec.report.dimensions:
        return []

    views = _views(spec)
    by_metric: dict[str, list[Insight]] = {}
    for insight in insights:
        by_metric.setdefault(insight.metric, []).append(insight)

    attributions: list[DriverAttribution] = []
    for total in totals:
        view = views.get(total.metric)
        if view is None or not view.is_additive or total.delta == 0:
            continue

        candidates = sorted(
            by_metric.get(total.metric, []), key=lambda i: abs(i.delta), reverse=True
        )
        if not candidates:
            continue

        drivers = candidates[: spec.report.max_drivers_per_metric]

        # Share of *gross* movement, not net. When a +1000 segment and a -200
        # segment sit inside a net +250, dividing by the net yields "460%
        # explained", which is arithmetically true and useless to a reader.
        gross = sum(abs(candidate.delta) for candidate in candidates)
        explained = sum(abs(driver.delta) for driver in drivers)
        explained_pct = round((explained / gross) * 100.0, 2) if gross > 0 else 0.0

        attributions.append(
            DriverAttribution(
                metric=total.metric,
                label=total.label,
                total_delta=round(total.delta, 6),
                gross_movement=round(gross, 6),
                drivers=drivers,
                explained_pct=explained_pct,
                segment_count=len(candidates),
                # A net that hides more than a quarter of the underlying churn
                # is a net worth flagging.
                offsetting=gross > abs(total.delta) * 1.25,
            )
        )

    attributions.sort(key=lambda item: abs(item.total_delta), reverse=True)
    return attributions


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def metric_spec_of(spec: AnalysisSpec, name: str) -> MetricSpec | DerivedMetricSpec | None:
    return spec.metric_by_name(name)
