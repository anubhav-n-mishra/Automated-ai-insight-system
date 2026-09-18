"""Declarative analysis specification and the objects the engine produces.

Pure data: no I/O, no framework imports. Everything here is safe to import
from a notebook, a CLI, a worker or the HTTP layer.
"""

from insight_engine.domain.results import (
    AnalysisResult,
    DriverAttribution,
    Insight,
    MetricTotals,
    Narrative,
    PeriodSummary,
)
from insight_engine.domain.spec import (
    AggregationKind,
    AnalysisSpec,
    ComparisonSpec,
    CsvSourceSpec,
    DatabaseSourceSpec,
    DatasetSpec,
    DerivedMetricSpec,
    MetricSpec,
    ReportSpec,
    SourceSpec,
    SqlSourceSpec,
)

__all__ = [
    "AggregationKind",
    "AnalysisResult",
    "AnalysisSpec",
    "ComparisonSpec",
    "CsvSourceSpec",
    "DatabaseSourceSpec",
    "DatasetSpec",
    "DerivedMetricSpec",
    "DriverAttribution",
    "Insight",
    "MetricSpec",
    "MetricTotals",
    "Narrative",
    "PeriodSummary",
    "ReportSpec",
    "SourceSpec",
    "SqlSourceSpec",
]
