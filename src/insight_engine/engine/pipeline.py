"""The analysis pipeline.

One function, :func:`run_analysis`, takes a validated spec and returns an
:class:`~insight_engine.domain.results.AnalysisResult`. It performs no I/O
beyond reading the sources, knows nothing about HTTP, and is what the CLI, the
API and the tests all call. Rendering (deck, dashboard, audio) consumes the
result and is deliberately downstream.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from insight_engine.connectors import SourcePolicy
from insight_engine.core.logging import get_logger
from insight_engine.core.telemetry import METRICS
from insight_engine.domain.results import AnalysisResult, Narrative, PeriodSummary
from insight_engine.domain.spec import AnalysisSpec
from insight_engine.engine.aggregate import (
    coerce_date_column,
    grand_totals,
    slice_period,
    split_and_aggregate,
)
from insight_engine.engine.ingest import ingest
from insight_engine.engine.insights import attribute_drivers, build_insights, compare_totals

logger = get_logger("engine.pipeline")

#: Called with ``(stage, fraction_complete)``. Lets the API stream honest
#: progress instead of the animated guess the old UI showed.
ProgressCallback = Callable[[str, float], None]

STAGES: tuple[tuple[str, float], ...] = (
    ("loading", 0.15),
    ("aggregating", 0.35),
    ("comparing", 0.55),
    ("ranking", 0.70),
    ("narrating", 0.90),
    ("complete", 1.0),
)


def _noop(stage: str, fraction: float) -> None:
    return None


def run_analysis(
    spec: AnalysisSpec,
    *,
    policy: SourcePolicy,
    narrator: Callable[[AnalysisResult], Narrative] | None = None,
    on_progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Execute a full analysis.

    Args:
        spec: Validated specification.
        policy: What the connectors are allowed to reach.
        narrator: Optional prose generator. Its failure is non-fatal: the
            numbers are the product, the narrative is a layer over them.
        on_progress: Stage callback for UI progress.
    """
    progress = on_progress or _noop
    started = time.perf_counter()
    warnings = list(spec.warnings())

    progress("loading", 0.05)
    with METRICS.timer("insight_engine_stage_seconds", {"stage": "ingest"}):
        ingested = ingest(spec, policy)
    warnings.extend(ingested.warnings)
    progress("loading", 0.15)

    with METRICS.timer("insight_engine_stage_seconds", {"stage": "aggregate"}):
        periods = split_and_aggregate(ingested.frame, spec)
    progress("aggregating", 0.35)

    dated = coerce_date_column(ingested.frame, spec.report.date_column)
    comparison = spec.report.comparison
    current_rows = slice_period(
        dated, spec.report.date_column, comparison.current_start, comparison.current_end
    )
    previous_rows = slice_period(
        dated, spec.report.date_column, comparison.previous_start, comparison.previous_end
    )

    with METRICS.timer("insight_engine_stage_seconds", {"stage": "compare"}):
        totals = compare_totals(
            spec,
            grand_totals(current_rows, spec),
            grand_totals(previous_rows, spec),
        )
    progress("comparing", 0.55)

    if previous_rows.height == 0:
        warnings.append(
            f"The previous period ({comparison.previous_start}..{comparison.previous_end}) "
            "contains no rows; every movement is measured against zero and percentage "
            "changes are undefined."
        )

    with METRICS.timer("insight_engine_stage_seconds", {"stage": "rank"}):
        ranked = build_insights(periods, spec, totals)
        insights = ranked.insights
        drivers = attribute_drivers(ranked, totals, spec)
    progress("ranking", 0.70)

    if not insights:
        warnings.append(
            "No segment moved enough to rank. Widen the date range, lower "
            "report.min_segment_share, or check that the dimensions have variation."
        )

    period_summary = PeriodSummary(
        current_start=comparison.current_start.isoformat(),
        current_end=comparison.current_end.isoformat(),
        previous_start=comparison.previous_start.isoformat(),
        previous_end=comparison.previous_end.isoformat(),
        current_rows=periods.current_rows,
        previous_rows=periods.previous_rows,
        current_days=comparison.current_days,
        previous_days=comparison.previous_days,
        like_for_like=comparison.is_like_for_like,
    )

    result = AnalysisResult(
        spec_title=spec.report.title,
        period=period_summary,
        dimensions=list(spec.report.dimensions),
        totals=totals,
        insights=insights,
        drivers=drivers,
        warnings=warnings,
        row_count=ingested.row_count,
        segment_count=periods.current.height,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )

    if narrator is not None:
        progress("narrating", 0.75)
        try:
            with METRICS.timer("insight_engine_stage_seconds", {"stage": "narrate"}):
                narrative = narrator(result)
            result = result.model_copy(update={"narrative": narrative})
        except Exception as error:
            logger.warning("narrative generation failed", extra={"error": str(error)})
            METRICS.counter("insight_engine_narrative_failures_total")
            result = result.model_copy(
                update={
                    "warnings": [
                        *result.warnings,
                        "Narrative generation failed; showing the computed summary instead.",
                    ]
                }
            )
    progress("complete", 1.0)

    result = result.model_copy(update={"duration_ms": int((time.perf_counter() - started) * 1000)})
    METRICS.observe("insight_engine_analysis_seconds", result.duration_ms / 1000.0)
    METRICS.counter("insight_engine_analyses_total")
    logger.info(
        "analysis complete",
        extra={
            "rows": result.row_count,
            "segments": result.segment_count,
            "insights": len(result.insights),
            "duration_ms": result.duration_ms,
        },
    )
    return result
