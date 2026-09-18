"""What the engine produces: totals, insights, drivers, narrative, result.

These models are the serialisation contract for the API, the dashboard and the
deck alike. The previous generation had three slightly different shapes for the
same numbers — the deck read ``current_totals``, the dashboard read ``change``
and the engine wrote ``delta_pct`` — so the deck chart and the dashboard table
silently rendered nothing. One model, one set of field names, everywhere.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["up", "down", "flat"]
Sentiment = Literal["positive", "negative", "neutral"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def percent_change(current: float, previous: float) -> float | None:
    """Relative change, or ``None`` when the baseline is zero.

    Returning ``None`` rather than the old ``±100.0`` sentinel matters: growth
    from nothing is undefined, not 100%, and a fabricated number here propagates
    into ranking, the deck and the spoken briefing.
    """
    if previous == 0 or not math.isfinite(previous):
        return None
    change = ((current - previous) / abs(previous)) * 100.0
    return change if math.isfinite(change) else None


def direction_of(delta: float, *, tolerance: float = 1e-9) -> Direction:
    if delta > tolerance:
        return "up"
    if delta < -tolerance:
        return "down"
    return "flat"


class MetricTotals(_Model):
    """One metric, aggregated over each period."""

    metric: str
    label: str
    unit: str = "count"
    current: float
    previous: float
    delta: float
    delta_pct: float | None = None
    direction: Direction = "flat"
    sentiment: Sentiment = "neutral"
    precision: int = 2

    @classmethod
    def build(
        cls,
        *,
        metric: str,
        label: str,
        unit: str,
        current: float,
        previous: float,
        higher_is_better: bool = True,
        precision: int = 2,
    ) -> MetricTotals:
        delta = current - previous
        direction = direction_of(delta)
        if direction == "flat":
            sentiment: Sentiment = "neutral"
        else:
            good = (direction == "up") == higher_is_better
            sentiment = "positive" if good else "negative"
        return cls(
            metric=metric,
            label=label,
            unit=unit,
            current=current,
            previous=previous,
            delta=delta,
            delta_pct=percent_change(current, previous),
            direction=direction,
            sentiment=sentiment,
            precision=precision,
        )


class Insight(_Model):
    """A single ranked movement, optionally scoped to a segment."""

    id: str
    metric: str
    label: str
    unit: str = "count"
    segment: dict[str, str] = Field(
        default_factory=dict, description="Dimension values. Empty means the overall total."
    )
    current_value: float
    previous_value: float
    delta: float
    delta_pct: float | None = None
    direction: Direction = "flat"
    sentiment: Sentiment = "neutral"

    impact_score: float = Field(
        description="Ranking weight. Comparable within a run, not across runs."
    )
    contribution_pct: float | None = Field(
        default=None,
        description="Share of the metric's total movement this segment accounts for.",
    )
    share_of_total: float | None = Field(
        default=None, description="Segment's share of the current-period metric total."
    )
    is_new_segment: bool = False
    is_lost_segment: bool = False
    precision: int = 2

    @property
    def segment_label(self) -> str:
        if not self.segment:
            return "Overall"
        return ", ".join(f"{key}: {value}" for key, value in self.segment.items())


class DriverAttribution(_Model):
    """Why a metric moved, decomposed across segments.

    Two different denominators matter here, and conflating them produces
    nonsense figures like "460% explained":

    ``total_delta``
        The **net** movement. Gains and losses cancel inside it.
    ``gross_movement``
        The sum of every segment's absolute movement. This is what the listed
        drivers are a share of, so ``explained_pct`` always lands between 0 and 100.
    ``offsetting``
        True when gross movement materially exceeds the net, meaning segments
        moved in opposite directions and a headline net figure understates the
        churn underneath it. Saying so is the difference between a report and a
        reassuring average.
    """

    metric: str
    label: str
    total_delta: float
    gross_movement: float = 0.0
    drivers: list[Insight] = Field(default_factory=list)
    explained_pct: float = 0.0
    segment_count: int = 0
    offsetting: bool = False


class PeriodSummary(_Model):
    """Coverage facts about the two windows, so a reader can trust the numbers."""

    current_start: str
    current_end: str
    previous_start: str
    previous_end: str
    current_rows: int
    previous_rows: int
    current_days: int
    previous_days: int
    like_for_like: bool = True

    def describe(self) -> str:
        return (
            f"{self.current_start}..{self.current_end} vs "
            f"{self.previous_start}..{self.previous_end}"
        )


class Narrative(_Model):
    """Prose layer over the numbers."""

    title: str
    headline: str
    bullets: list[str] = Field(default_factory=list)
    recommendation: str = ""
    provider: str = "template"
    model: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_ai_generated(self) -> bool:
        return self.provider not in {"template", "none"}


class AnalysisResult(_Model):
    """Everything one run produces. Renderers consume this and nothing else."""

    spec_title: str
    period: PeriodSummary
    dimensions: list[str] = Field(default_factory=list)
    totals: list[MetricTotals] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    drivers: list[DriverAttribution] = Field(default_factory=list)
    narrative: Narrative | None = None
    warnings: list[str] = Field(default_factory=list)
    row_count: int = 0
    segment_count: int = 0
    duration_ms: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def total_for(self, metric: str) -> MetricTotals | None:
        return next((total for total in self.totals if total.metric == metric), None)

    @property
    def headline_insight(self) -> Insight | None:
        return self.insights[0] if self.insights else None

    def top_movers(self, limit: int = 3) -> list[MetricTotals]:
        """Metrics whose relative movement was largest, undefined baselines last."""
        return sorted(
            self.totals,
            key=lambda total: abs(total.delta_pct) if total.delta_pct is not None else -1.0,
            reverse=True,
        )[:limit]

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-ready payload for the dashboard and API."""
        return self.model_dump(mode="json")
