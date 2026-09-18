"""The analysis specification: a versioned, validated description of the question.

This is the project's public contract. A spec is a YAML/JSON document held in
version control next to the data it describes, which is what makes a run
reproducible and reviewable. The JSON Schema emitted by
:func:`analysis_spec_json_schema` is published so editors can validate it.

Two modelling decisions carry most of the correctness weight:

1. A metric names its own **aggregation**. The previous generation summed every
   numeric column, which silently produced nonsense for anything that is not
   additive.
2. **Derived metrics are expressions over aggregated metrics**, never over raw
   rows. ``ctr = clicks / impressions`` is only true when both sides are summed
   first; a row-wise mean of per-row ratios is a different, wrong number
   (Simpson's paradox in miniature).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from insight_engine.core.errors import ConfigurationError

SPEC_VERSION = 1

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_ .\-]{0,127}$")

AggregationKind = Literal["sum", "avg", "min", "max", "count", "count_distinct", "median"]

# Aggregations whose result stays meaningful when a segment is missing from a
# period. Anything else must not be zero-filled on an outer join.
ADDITIVE_AGGREGATIONS: frozenset[str] = frozenset({"sum", "count", "count_distinct"})


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


def _validate_identifier(value: str, *, what: str) -> str:
    if not IDENTIFIER.match(value):
        raise ValueError(
            f"{what} {value!r} is not a valid column name "
            "(letters, digits, underscore, space, dot and dash; must not start with a digit)"
        )
    return value


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
class MetricSpec(_Base):
    """A measure read straight from the data and reduced with one aggregation."""

    name: str = Field(description="Name the metric is referred to by everywhere else.")
    column: str | None = Field(default=None, description="Source column. Defaults to ``name``.")
    aggregation: AggregationKind = "sum"
    label: str | None = Field(default=None, description="Human-facing name for reports.")
    unit: Literal["count", "currency", "percent", "ratio", "duration_seconds"] = "count"
    format_precision: int = Field(default=2, ge=0, le=10)
    higher_is_better: bool = Field(
        default=True,
        description=(
            "Direction of goodness. Cost per click going up is bad; revenue going "
            "up is good. Reports colour movements by this, not by sign."
        ),
    )

    @property
    def source_column(self) -> str:
        return self.column or self.name

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").title()

    @property
    def is_additive(self) -> bool:
        return self.aggregation in ADDITIVE_AGGREGATIONS

    @field_validator("name", "column")
    @classmethod
    def _check_identifier(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, what="metric column")


class DerivedMetricSpec(_Base):
    """A metric computed from other metrics *after* aggregation.

    ``expression`` is evaluated by :mod:`insight_engine.engine.formula`, which
    parses a restricted arithmetic grammar rather than calling :func:`eval`.
    """

    name: str
    expression: str = Field(description="e.g. ``clicks / impressions`` or ``(a + b) / 2``")
    label: str | None = None
    unit: Literal["count", "currency", "percent", "ratio", "duration_seconds"] = "ratio"
    format_precision: int = Field(default=4, ge=0, le=10)
    higher_is_better: bool = True

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").title()

    @field_validator("name")
    @classmethod
    def _check_identifier(cls, value: str) -> str:
        return _validate_identifier(value, what="derived metric")

    @field_validator("expression")
    @classmethod
    def _check_expression(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("expression must not be empty")
        if len(value) > 512:
            raise ValueError("expression must be at most 512 characters")
        return value


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
class _SourceBase(_Base):
    date_column: str = Field(description="Column carrying the event date or timestamp.")
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
    join_keys: list[str] = Field(
        default_factory=list,
        description="Columns used to join this source onto the primary source.",
    )

    @field_validator("date_column")
    @classmethod
    def _check_date_column(cls, value: str) -> str:
        return _validate_identifier(value, what="date column")

    @field_validator("dimensions", "join_keys")
    @classmethod
    def _check_columns(cls, value: list[str]) -> list[str]:
        return [_validate_identifier(item, what="column") for item in value]

    @model_validator(mode="after")
    def _no_column_used_twice(self) -> _SourceBase:
        metric_names = {metric.name for metric in self.metrics}
        clash = metric_names & set(self.dimensions)
        if clash:
            raise ValueError(
                "a column cannot be both a dimension and a metric: " + ", ".join(sorted(clash))
            )
        duplicates = {
            name for name in metric_names if [m.name for m in self.metrics].count(name) > 1
        }
        if duplicates:
            raise ValueError("duplicate metric names: " + ", ".join(sorted(duplicates)))
        return self


class CsvSourceSpec(_SourceBase):
    """A delimited text file. The delimiter is sniffed when not given."""

    type: Literal["csv"] = "csv"
    path: str
    delimiter: str | None = Field(default=None, max_length=1)
    encoding: str = "utf-8"
    null_values: list[str] = Field(default_factory=lambda: ["", "NA", "N/A", "null", "NULL"])


class SqlSourceSpec(_SourceBase):
    """An arbitrary read-only query against a SQLAlchemy-style URL."""

    type: Literal["sql"] = "sql"
    connection_string: str = Field(
        description=(
            "SQLAlchemy URL. Prefer ``${ENV_VAR}`` placeholders over literal "
            "credentials so the spec can live in version control."
        )
    )
    query: str

    @field_validator("query")
    @classmethod
    def _read_only(cls, value: str) -> str:
        return _reject_write_statements(value)


class DatabaseSourceSpec(_SourceBase):
    """A table read through a structured connection definition."""

    type: Literal["database"] = "database"
    driver: Literal["postgresql", "mysql", "sqlite", "mssql"]
    host: str = "localhost"
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str
    schema_name: str | None = None
    table: str
    username: str | None = None
    password: str | None = Field(
        default=None, description="Use ``${ENV_VAR}``; literal secrets are discouraged."
    )

    @field_validator("table", "schema_name")
    @classmethod
    def _check_object_name(cls, value: str | None) -> str | None:
        # Quoted and interpolated into SQL by the connector, so keep it boring.
        if value is None:
            return None
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$", value):
            raise ValueError(f"unsafe database object name: {value!r}")
        return value


_WRITE_STATEMENT = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|merge|"
    r"call|execute|copy|vacuum|attach|pragma)\b",
    re.IGNORECASE,
)


def _reject_write_statements(query: str) -> str:
    """Refuse anything that is not obviously a read.

    This is defence in depth, not a security boundary: the real control is a
    read-only database role. It catches the common accident of pasting a
    migration into a reporting spec.
    """
    stripped = re.sub(r"--[^\n]*|/\*.*?\*/", " ", query, flags=re.DOTALL).strip()
    if not stripped:
        raise ValueError("query must not be empty")
    if ";" in stripped.rstrip(";"):
        raise ValueError("query must be a single statement")
    if _WRITE_STATEMENT.search(stripped):
        raise ValueError("only read-only queries are allowed in a source")
    if not re.match(r"^\s*(select|with)\b", stripped, re.IGNORECASE):
        raise ValueError("query must start with SELECT or WITH")
    return query


SourceSpec = Annotated[
    CsvSourceSpec | SqlSourceSpec | DatabaseSourceSpec,
    Field(discriminator="type"),
]


class DatasetSpec(_Base):
    """The set of sources and which one anchors the join."""

    primary_source: str
    sources: dict[str, SourceSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _primary_exists(self) -> DatasetSpec:
        if self.primary_source not in self.sources:
            raise ValueError(
                f"primary_source {self.primary_source!r} is not one of {sorted(self.sources)}"
            )
        return self

    @property
    def primary(self) -> SourceSpec:
        return self.sources[self.primary_source]


# --------------------------------------------------------------------------- #
# Comparison and report
# --------------------------------------------------------------------------- #
class ComparisonSpec(_Base):
    """The two windows being compared."""

    current_start: date
    current_end: date
    previous_start: date
    previous_end: date

    @model_validator(mode="after")
    def _check_windows(self) -> ComparisonSpec:
        if self.current_end < self.current_start:
            raise ValueError("current_end must not precede current_start")
        if self.previous_end < self.previous_start:
            raise ValueError("previous_end must not precede previous_start")
        if self.previous_start <= self.current_end and self.current_start <= self.previous_end:
            raise ValueError(
                "current and previous periods overlap; a movement computed from "
                "overlapping windows compares a period against part of itself"
            )
        return self

    @property
    def current_days(self) -> int:
        return (self.current_end - self.current_start).days + 1

    @property
    def previous_days(self) -> int:
        return (self.previous_end - self.previous_start).days + 1

    @property
    def is_like_for_like(self) -> bool:
        """Whether the windows are the same length.

        Unequal windows make absolute deltas misleading; callers surface this as
        a warning rather than refusing the run, because month-over-month
        comparisons are legitimately ragged.
        """
        return self.current_days == self.previous_days

    def describe(self) -> str:
        return (
            f"{self.current_start.isoformat()}..{self.current_end.isoformat()} vs "
            f"{self.previous_start.isoformat()}..{self.previous_end.isoformat()}"
        )

    @classmethod
    def trailing(cls, *, anchor: date, days: int = 7) -> ComparisonSpec:
        """Two adjacent, equal-length windows ending at ``anchor`` (inclusive)."""
        if days < 1:
            raise ValueError("days must be at least 1")
        current_start = anchor - timedelta(days=days - 1)
        previous_end = current_start - timedelta(days=1)
        return cls(
            current_start=current_start,
            current_end=anchor,
            previous_start=previous_end - timedelta(days=days - 1),
            previous_end=previous_end,
        )


class ReportSpec(_Base):
    """What to analyse and how much of it to surface."""

    title: str = Field(default="Performance Analysis", max_length=200)
    date_column: str
    comparison: ComparisonSpec
    dimensions: list[str] = Field(
        default_factory=list,
        description="Segments to break the movement down by. Empty analyses the total only.",
    )
    kpi_priority: list[str] = Field(
        default_factory=list,
        description="Metric names, most important first. Drives ranking weight.",
    )
    top_insights: int = Field(default=20, ge=1, le=500)
    min_impact_score: float = Field(default=0.0, ge=0.0)
    min_segment_share: float = Field(
        default=0.001,
        ge=0.0,
        le=1.0,
        description=(
            "Drop segments contributing less than this share of a metric before "
            "ranking, so a rounding-error segment with a 900% swing cannot "
            "outrank a real one."
        ),
    )
    include_drivers: bool = Field(
        default=True, description="Attribute each total movement to its largest segments."
    )
    max_drivers_per_metric: int = Field(default=5, ge=1, le=50)

    @field_validator("date_column")
    @classmethod
    def _check_date_column(cls, value: str) -> str:
        return _validate_identifier(value, what="date column")


class AnalysisSpec(_Base):
    """Root document. This is what a user writes and what the engine consumes."""

    version: int = Field(default=SPEC_VERSION, ge=1, le=SPEC_VERSION)
    dataset: DatasetSpec
    derived_metrics: list[DerivedMetricSpec] = Field(default_factory=list)
    report: ReportSpec

    # ------------------------------------------------------------- convenience
    @property
    def base_metrics(self) -> list[MetricSpec]:
        """Metrics across all sources, de-duplicated by name (primary source wins)."""
        seen: dict[str, MetricSpec] = {}
        primary_name = self.dataset.primary_source
        ordered = [primary_name, *(n for n in self.dataset.sources if n != primary_name)]
        for source_name in ordered:
            for metric in self.dataset.sources[source_name].metrics:
                seen.setdefault(metric.name, metric)
        return list(seen.values())

    @property
    def metric_names(self) -> list[str]:
        return [m.name for m in self.base_metrics] + [d.name for d in self.derived_metrics]

    def metric_by_name(self, name: str) -> MetricSpec | DerivedMetricSpec | None:
        for metric in self.base_metrics:
            if metric.name == name:
                return metric
        for derived in self.derived_metrics:
            if derived.name == name:
                return derived
        return None

    @property
    def ranked_kpis(self) -> list[str]:
        """KPI priority, defaulting to every metric when the user gave no order."""
        if self.report.kpi_priority:
            return list(self.report.kpi_priority)
        return self.metric_names

    # -------------------------------------------------------------- validation
    @model_validator(mode="after")
    def _cross_field_checks(self) -> AnalysisSpec:
        known_metrics = {m.name for m in self.base_metrics}
        derived_names = [d.name for d in self.derived_metrics]

        clash = known_metrics & set(derived_names)
        if clash:
            raise ValueError("derived metrics shadow base metrics: " + ", ".join(sorted(clash)))
        duplicate_derived = {n for n in derived_names if derived_names.count(n) > 1}
        if duplicate_derived:
            raise ValueError(
                "duplicate derived metric names: " + ", ".join(sorted(duplicate_derived))
            )

        all_metrics = known_metrics | set(derived_names)
        if not all_metrics:
            raise ValueError("at least one metric must be defined")

        unknown_kpis = [k for k in self.report.kpi_priority if k not in all_metrics]
        if unknown_kpis:
            raise ValueError(
                "kpi_priority references undefined metrics: "
                + ", ".join(sorted(unknown_kpis))
                + f" (defined: {', '.join(sorted(all_metrics))})"
            )

        declared_dimensions = {
            dimension for source in self.dataset.sources.values() for dimension in source.dimensions
        }
        unknown_dimensions = [d for d in self.report.dimensions if d not in declared_dimensions]
        if unknown_dimensions:
            raise ValueError(
                "report.dimensions references columns no source declares: "
                + ", ".join(sorted(unknown_dimensions))
            )

        metric_dimension_clash = set(self.report.dimensions) & all_metrics
        if metric_dimension_clash:
            raise ValueError(
                "a column cannot be analysed as both dimension and metric: "
                + ", ".join(sorted(metric_dimension_clash))
            )
        return self

    def warnings(self) -> list[str]:
        """Non-fatal problems worth telling the user about."""
        notes: list[str] = []
        if not self.report.comparison.is_like_for_like:
            notes.append(
                f"Periods are unequal ({self.report.comparison.current_days}d vs "
                f"{self.report.comparison.previous_days}d); absolute deltas are not "
                "directly comparable."
            )
        if not self.report.dimensions:
            notes.append(
                "No dimensions configured: the report will show totals only, with no "
                "segment-level drivers."
            )
        for source_name, source in self.dataset.sources.items():
            if source_name != self.dataset.primary_source and not source.join_keys:
                notes.append(
                    f"Source {source_name!r} declares no join_keys; it will be joined on "
                    "whatever dimensions and date column it happens to share."
                )
        return notes


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        lines.append(f"{location}: {item['msg']}")
    return "; ".join(lines)


def _serialisable_errors(error: ValidationError) -> list[dict[str, str]]:
    """Reduce Pydantic's error list to plain strings.

    Pydantic puts the original exception object in ``ctx``, which is not JSON
    serialisable. Passing the raw list into an error response turns a clean 400
    into a 500 at render time — the client sees an internal error for what was
    simply a bad date range.
    """
    return [
        {
            "field": ".".join(str(part) for part in item.get("loc", ())) or "<root>",
            "message": str(item.get("msg", "invalid value")),
            "type": str(item.get("type", "value_error")),
        }
        for item in error.errors()
    ]


def spec_from_mapping(raw: dict[str, Any]) -> AnalysisSpec:
    """Validate a plain mapping into an :class:`AnalysisSpec`."""
    try:
        return AnalysisSpec.model_validate(raw)
    except ValidationError as error:
        raise ConfigurationError(
            f"Invalid analysis specification: {_format_validation_error(error)}",
            context={"errors": _serialisable_errors(error)},
        ) from error


def analysis_spec_json_schema() -> dict[str, Any]:
    """JSON Schema for editor integration and ``insight-engine schema``."""
    schema = AnalysisSpec.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://raw.githubusercontent.com/anubhav-n-mishra/"
        "Automated-ai-insight-system/main/schemas/analysis-spec-v1.json"
    )
    schema["title"] = "Insight Engine analysis specification v1"
    return schema
