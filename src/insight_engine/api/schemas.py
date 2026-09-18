"""Request and response models for the HTTP API.

Declared explicitly so the generated OpenAPI document is accurate: consumers
generate clients from it, and a schema that lies about the payload is worse
than no schema.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from insight_engine.domain.spec import AnalysisSpec


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]
    narrative_provider: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    context: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str | None = None


class ColumnProfileResponse(BaseModel):
    name: str
    dtype: str
    role: Literal["date", "dimension", "metric", "identifier", "ignored"]
    null_fraction: float
    distinct_count: int
    distinct_fraction: float
    samples: list[str] = Field(default_factory=list)
    confidence: float
    reason: str
    suggested_aggregation: str
    suggested_unit: str


class DateRange(BaseModel):
    start: str
    end: str


class ComparisonSuggestion(BaseModel):
    current_start: str
    current_end: str
    previous_start: str
    previous_end: str


class ProfileResponse(BaseModel):
    """What the engine inferred about an uploaded dataset."""

    model_config = ConfigDict(
        json_schema_extra={"description": "Inferred locally; no data leaves the deployment."}
    )

    upload_id: str = Field(description="Reference the upload in a later /reports call.")
    filename: str
    row_count: int
    date_column: str | None = None
    date_range: DateRange | None = None
    suggested_comparison: ComparisonSuggestion | None = None
    suggested_dimensions: list[str] = Field(default_factory=list)
    suggested_metrics: list[str] = Field(default_factory=list)
    columns: list[ColumnProfileResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(default_factory=list)


class MetricSelection(BaseModel):
    """A metric chosen in the UI."""

    name: str
    aggregation: Literal["sum", "avg", "min", "max", "count", "count_distinct", "median"] = "sum"
    unit: Literal["count", "currency", "percent", "ratio", "duration_seconds"] = "count"
    higher_is_better: bool = True


class DerivedMetricSelection(BaseModel):
    name: str
    expression: str
    unit: Literal["count", "currency", "percent", "ratio", "duration_seconds"] = "ratio"
    higher_is_better: bool = True


class ReportRequest(BaseModel):
    """Generate a report from a previously profiled upload."""

    upload_id: str
    title: str = Field(default="Performance Analysis", max_length=200)
    date_column: str
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[MetricSelection] = Field(min_length=1, max_length=40)
    derived_metrics: list[DerivedMetricSelection] = Field(default_factory=list, max_length=20)
    current_start: str
    current_end: str
    previous_start: str
    previous_end: str
    kpi_priority: list[str] = Field(default_factory=list)
    include_audio: bool = True


class SpecReportRequest(BaseModel):
    """Generate a report from a complete specification document."""

    spec: AnalysisSpec
    upload_id: str | None = Field(
        default=None, description="Resolve relative CSV paths against this upload."
    )
    include_audio: bool = True


class JobAccepted(BaseModel):
    job_id: str
    status: str
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    progress: float
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DashboardResponse(BaseModel):
    session_id: str
    title: str
    created_at: str
    expires_at: str
    analysis: dict[str, Any]
    briefing: dict[str, Any] | None = None
    report_url: str | None = None
