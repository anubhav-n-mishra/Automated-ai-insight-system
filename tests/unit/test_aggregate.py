"""Aggregation correctness: the bugs here produced plausible, wrong numbers."""

from __future__ import annotations

import polars as pl
import pytest

from insight_engine.core.errors import DataError
from insight_engine.domain.spec import AnalysisSpec, spec_from_mapping
from insight_engine.engine.aggregate import (
    aggregate,
    coerce_date_column,
    grand_totals,
    slice_period,
    split_and_aggregate,
)


@pytest.fixture
def frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": ["2025-01-01", "2025-01-01", "2025-01-02"],
            "geo": ["US", "UK", "US"],
            "clicks": [10, 20, 30],
            "impressions": [100, 100, 200],
        }
    )


def _spec(**report: object) -> AnalysisSpec:
    base_report: dict[str, object] = {
        "date_column": "date",
        "dimensions": ["geo"],
        "comparison": {
            "current_start": "2025-01-01",
            "current_end": "2025-01-02",
            "previous_start": "2024-12-30",
            "previous_end": "2024-12-31",
        },
    }
    base_report.update(report)
    return spec_from_mapping(
        {
            "dataset": {
                "primary_source": "s",
                "sources": {
                    "s": {
                        "type": "csv",
                        "path": "x.csv",
                        "date_column": "date",
                        "dimensions": ["geo"],
                        "metrics": [{"name": "clicks"}, {"name": "impressions"}],
                    }
                },
            },
            "derived_metrics": [
                {"name": "ctr", "expression": "clicks / impressions", "unit": "percent"}
            ],
            "report": base_report,
        }
    )


class TestDerivedMetrics:
    def test_ratio_is_computed_after_aggregation(self, frame: pl.DataFrame) -> None:
        """The headline correctness fix.

        Summing per-row CTR across the two US rows gives 0.10 + 0.15 = 0.25.
        The true period CTR is 40 clicks / 300 impressions = 0.1333.
        """
        result = aggregate(coerce_date_column(frame, "date"), _spec())
        us = result.filter(pl.col("geo") == "US").row(0, named=True)
        assert us["clicks"] == 40.0
        assert us["impressions"] == 300.0
        assert us["ctr"] == pytest.approx(40 / 300)

    def test_grand_total_ratio_uses_summed_inputs(self, frame: pl.DataFrame) -> None:
        totals = grand_totals(coerce_date_column(frame, "date"), _spec())
        assert totals["ctr"] == pytest.approx(60 / 400)


class TestAggregationKinds:
    @pytest.mark.parametrize(
        ("aggregation", "expected"),
        [
            ("sum", 60.0),
            ("avg", 20.0),
            ("min", 10.0),
            ("max", 30.0),
            ("count", 3.0),
            ("count_distinct", 3.0),
            ("median", 20.0),
        ],
    )
    def test_each_kind(self, frame: pl.DataFrame, aggregation: str, expected: float) -> None:
        spec = spec_from_mapping(
            {
                "dataset": {
                    "primary_source": "s",
                    "sources": {
                        "s": {
                            "type": "csv",
                            "path": "x.csv",
                            "date_column": "date",
                            "metrics": [{"name": "clicks", "aggregation": aggregation}],
                        }
                    },
                },
                "report": {
                    "date_column": "date",
                    "comparison": {
                        "current_start": "2025-01-01",
                        "current_end": "2025-01-02",
                        "previous_start": "2024-12-30",
                        "previous_end": "2024-12-31",
                    },
                },
            }
        )
        result = aggregate(coerce_date_column(frame, "date"), spec, dimensions=[])
        assert result.row(0, named=True)["clicks"] == pytest.approx(expected)


class TestDateHandling:
    @pytest.mark.parametrize(
        "values",
        [
            ["2025-01-01", "2025-01-02"],
            ["01/01/2025", "02/01/2025"],
            ["2025/01/01", "2025/01/02"],
        ],
    )
    def test_parses_common_formats(self, values: list[str]) -> None:
        frame = pl.DataFrame({"date": values, "m": [1, 2]})
        result = coerce_date_column(frame, "date")
        assert result.schema["date"] == pl.Date

    def test_rejects_a_numeric_year_column(self) -> None:
        # A year is a period label. Treating it as a time axis is how a report
        # silently compares nothing at all.
        frame = pl.DataFrame({"date": [2024, 2025], "m": [1, 2]})
        with pytest.raises(DataError, match="numbers, not dates"):
            coerce_date_column(frame, "date")

    def test_rejects_unparseable_values(self) -> None:
        frame = pl.DataFrame({"date": ["not a date", "also not"], "m": [1, 2]})
        with pytest.raises(DataError, match="Could not interpret"):
            coerce_date_column(frame, "date")

    def test_slice_is_inclusive_at_both_ends(self, frame: pl.DataFrame) -> None:
        from datetime import date

        dated = coerce_date_column(frame, "date")
        sliced = slice_period(dated, "date", date(2025, 1, 1), date(2025, 1, 1))
        assert sliced.height == 2


class TestPeriodSplitting:
    def test_empty_current_period_is_an_error_naming_the_data_range(
        self, frame: pl.DataFrame
    ) -> None:
        spec = _spec(
            comparison={
                "current_start": "2030-01-01",
                "current_end": "2030-01-07",
                "previous_start": "2029-12-01",
                "previous_end": "2029-12-31",
            }
        )
        with pytest.raises(DataError) as error:
            split_and_aggregate(frame, spec)
        assert "2025-01-01" in str(error.value.context)

    def test_null_dimension_becomes_an_explicit_bucket(self) -> None:
        frame = pl.DataFrame(
            {
                "date": ["2025-01-01", "2025-01-01"],
                "geo": ["US", None],
                "clicks": [10, 5],
                "impressions": [100, 50],
            }
        )
        result = aggregate(coerce_date_column(frame, "date"), _spec())
        assert "(not set)" in result["geo"].to_list()
