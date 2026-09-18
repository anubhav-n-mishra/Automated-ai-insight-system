"""Ranking and attribution."""

from __future__ import annotations

import polars as pl
import pytest

from insight_engine.domain.results import percent_change
from insight_engine.domain.spec import AnalysisSpec, spec_from_mapping
from insight_engine.engine.aggregate import PeriodFrames
from insight_engine.engine.insights import attribute_drivers, build_insights, compare_totals


@pytest.fixture
def spec() -> AnalysisSpec:
    return spec_from_mapping(
        {
            "dataset": {
                "primary_source": "s",
                "sources": {
                    "s": {
                        "type": "csv",
                        "path": "x.csv",
                        "date_column": "d",
                        "dimensions": ["geo"],
                        "metrics": [{"name": "clicks"}],
                    }
                },
            },
            "report": {
                "date_column": "d",
                "dimensions": ["geo"],
                "kpi_priority": ["clicks"],
                "min_segment_share": 0.0,
                "comparison": {
                    "current_start": "2025-01-08",
                    "current_end": "2025-01-14",
                    "previous_start": "2025-01-01",
                    "previous_end": "2025-01-07",
                },
            },
        }
    )


def periods(current: dict[str, list], previous: dict[str, list]) -> PeriodFrames:
    return PeriodFrames(
        current=pl.DataFrame(current),
        previous=pl.DataFrame(previous),
        current_rows=len(next(iter(current.values()))),
        previous_rows=len(next(iter(previous.values()))),
    )


class TestPercentChange:
    def test_zero_baseline_is_undefined_not_a_hundred(self) -> None:
        # Growth from nothing is undefined. Reporting +100% invents a fact and
        # then ranks on it.
        assert percent_change(10.0, 0.0) is None

    def test_normal_case(self) -> None:
        assert percent_change(150.0, 100.0) == pytest.approx(50.0)

    def test_negative_baseline_uses_magnitude(self) -> None:
        assert percent_change(-50.0, -100.0) == pytest.approx(50.0)


class TestSegmentJoin:
    def test_segments_present_in_only_one_period_keep_their_labels(
        self, spec: AnalysisSpec
    ) -> None:
        """A full outer join without ``coalesce`` nulls the key columns.

        That is how every new or lost segment used to render as a blank row.
        """
        frames = periods(
            {"geo": ["US", "NEW"], "clicks": [100.0, 40.0]},
            {"geo": ["US", "GONE"], "clicks": [80.0, 25.0]},
        )
        totals = compare_totals(spec, {"clicks": 140.0}, {"clicks": 105.0})
        insights = build_insights(frames, spec, totals)

        labels = {insight.segment["geo"] for insight in insights}
        assert {"US", "NEW", "GONE"} <= labels
        assert any(insight.is_new_segment for insight in insights)
        assert any(insight.is_lost_segment for insight in insights)

    def test_new_segment_has_zero_baseline_and_undefined_percentage(
        self, spec: AnalysisSpec
    ) -> None:
        frames = periods({"geo": ["NEW"], "clicks": [40.0]}, {"geo": ["US"], "clicks": [10.0]})
        totals = compare_totals(spec, {"clicks": 40.0}, {"clicks": 10.0})
        new = next(i for i in build_insights(frames, spec, totals) if i.segment["geo"] == "NEW")
        assert new.previous_value == 0.0
        assert new.delta_pct is None


class TestRanking:
    def test_large_absolute_movement_outranks_a_tiny_percentage_spike(
        self, spec: AnalysisSpec
    ) -> None:
        frames = periods(
            {"geo": ["BIG", "TINY"], "clicks": [11_000.0, 30.0]},
            {"geo": ["BIG", "TINY"], "clicks": [10_000.0, 1.0]},
        )
        totals = compare_totals(spec, {"clicks": 11_030.0}, {"clicks": 10_001.0})
        ranked = build_insights(frames, spec, totals)
        assert ranked[0].segment["geo"] == "BIG"

    def test_immaterial_segments_are_dropped(self) -> None:
        spec = spec_from_mapping(
            {
                "dataset": {
                    "primary_source": "s",
                    "sources": {
                        "s": {
                            "type": "csv",
                            "path": "x.csv",
                            "date_column": "d",
                            "dimensions": ["geo"],
                            "metrics": [{"name": "clicks"}],
                        }
                    },
                },
                "report": {
                    "date_column": "d",
                    "dimensions": ["geo"],
                    "min_segment_share": 0.05,
                    "comparison": {
                        "current_start": "2025-01-08",
                        "current_end": "2025-01-14",
                        "previous_start": "2025-01-01",
                        "previous_end": "2025-01-07",
                    },
                },
            }
        )
        frames = periods(
            {"geo": ["BIG", "DUST"], "clicks": [10_000.0, 3.0]},
            {"geo": ["BIG", "DUST"], "clicks": [9_000.0, 1.0]},
        )
        totals = compare_totals(spec, {"clicks": 10_003.0}, {"clicks": 9_001.0})
        assert {i.segment["geo"] for i in build_insights(frames, spec, totals)} == {"BIG"}

    def test_flat_segments_are_not_reported_as_insights(self, spec: AnalysisSpec) -> None:
        frames = periods({"geo": ["A"], "clicks": [100.0]}, {"geo": ["A"], "clicks": [100.0]})
        totals = compare_totals(spec, {"clicks": 100.0}, {"clicks": 100.0})
        assert build_insights(frames, spec, totals) == []


class TestSentiment:
    def test_direction_of_goodness_follows_the_metric_not_the_sign(self) -> None:
        spec = spec_from_mapping(
            {
                "dataset": {
                    "primary_source": "s",
                    "sources": {
                        "s": {
                            "type": "csv",
                            "path": "x.csv",
                            "date_column": "d",
                            "metrics": [
                                {"name": "revenue"},
                                {"name": "cost", "higher_is_better": False},
                            ],
                        }
                    },
                },
                "report": {
                    "date_column": "d",
                    "comparison": {
                        "current_start": "2025-01-08",
                        "current_end": "2025-01-14",
                        "previous_start": "2025-01-01",
                        "previous_end": "2025-01-07",
                    },
                },
            }
        )
        totals = compare_totals(
            spec, {"revenue": 120.0, "cost": 120.0}, {"revenue": 100.0, "cost": 100.0}
        )
        by_name = {total.metric: total for total in totals}
        assert by_name["revenue"].sentiment == "positive"
        assert by_name["cost"].sentiment == "negative"


class TestDrivers:
    def test_explained_share_is_reported(self, spec: AnalysisSpec) -> None:
        frames = periods(
            {"geo": ["A", "B", "C"], "clicks": [500.0, 300.0, 210.0]},
            {"geo": ["A", "B", "C"], "clicks": [400.0, 250.0, 200.0]},
        )
        totals = compare_totals(spec, {"clicks": 1010.0}, {"clicks": 850.0})
        insights = build_insights(frames, spec, totals)
        drivers = attribute_drivers(insights, totals, spec)

        assert drivers
        attribution = drivers[0]
        assert attribution.metric == "clicks"
        assert attribution.explained_pct == pytest.approx(100.0, abs=0.5)

    def test_ratio_metrics_are_not_decomposed(self) -> None:
        """Segment deltas of a ratio do not sum to the ratio's movement."""
        spec = spec_from_mapping(
            {
                "dataset": {
                    "primary_source": "s",
                    "sources": {
                        "s": {
                            "type": "csv",
                            "path": "x.csv",
                            "date_column": "d",
                            "dimensions": ["geo"],
                            "metrics": [{"name": "clicks"}, {"name": "impressions"}],
                        }
                    },
                },
                "derived_metrics": [
                    {"name": "ctr", "expression": "clicks / impressions", "unit": "percent"}
                ],
                "report": {
                    "date_column": "d",
                    "dimensions": ["geo"],
                    "kpi_priority": ["ctr", "clicks", "impressions"],
                    "comparison": {
                        "current_start": "2025-01-08",
                        "current_end": "2025-01-14",
                        "previous_start": "2025-01-01",
                        "previous_end": "2025-01-07",
                    },
                },
            }
        )
        frames = periods(
            {
                "geo": ["A", "B"],
                "clicks": [60.0, 40.0],
                "impressions": [600.0, 500.0],
                "ctr": [0.1, 0.08],
            },
            {
                "geo": ["A", "B"],
                "clicks": [50.0, 30.0],
                "impressions": [550.0, 480.0],
                "ctr": [0.09, 0.06],
            },
        )
        totals = compare_totals(
            spec,
            {"clicks": 100.0, "impressions": 1100.0, "ctr": 100 / 1100},
            {"clicks": 80.0, "impressions": 1030.0, "ctr": 80 / 1030},
        )
        drivers = attribute_drivers(build_insights(frames, spec, totals), totals, spec)
        assert "ctr" not in {attribution.metric for attribution in drivers}
