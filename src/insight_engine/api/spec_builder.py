"""Build an :class:`AnalysisSpec` from the UI's report form."""

from __future__ import annotations

from insight_engine.api.schemas import ReportRequest
from insight_engine.core.errors import ConfigurationError
from insight_engine.domain.spec import AnalysisSpec, spec_from_mapping

#: Path is relative and resolved against the upload's own directory, which is
#: the connector's base path. It can therefore never address anything else.
UPLOAD_RELATIVE_PATH = "data.csv"


def spec_from_report_request(request: ReportRequest) -> AnalysisSpec:
    """Translate the form payload into a validated specification."""
    selected = [metric.name for metric in request.metrics]
    overlap = sorted(set(request.dimensions) & set(selected))
    if overlap:
        raise ConfigurationError(
            "A column cannot be both a dimension and a metric: " + ", ".join(overlap),
            context={"columns": overlap},
        )

    derived_names = [derived.name for derived in request.derived_metrics]
    kpi_priority = request.kpi_priority or [*selected, *derived_names]

    raw = {
        "dataset": {
            "primary_source": "upload",
            "sources": {
                "upload": {
                    "type": "csv",
                    "path": UPLOAD_RELATIVE_PATH,
                    "date_column": request.date_column,
                    "dimensions": request.dimensions,
                    "metrics": [
                        {
                            "name": metric.name,
                            "aggregation": metric.aggregation,
                            "unit": metric.unit,
                            "higher_is_better": metric.higher_is_better,
                        }
                        for metric in request.metrics
                    ],
                }
            },
        },
        "derived_metrics": [
            {
                "name": derived.name,
                "expression": derived.expression,
                "unit": derived.unit,
                "higher_is_better": derived.higher_is_better,
            }
            for derived in request.derived_metrics
        ],
        "report": {
            "title": request.title,
            "date_column": request.date_column,
            "comparison": {
                "current_start": request.current_start,
                "current_end": request.current_end,
                "previous_start": request.previous_start,
                "previous_end": request.previous_end,
            },
            "dimensions": request.dimensions,
            "kpi_priority": kpi_priority,
        },
    }
    return spec_from_mapping(raw)
