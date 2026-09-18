"""Specification validation catches the mistakes that produce silent nonsense."""

from __future__ import annotations

from datetime import date

import pytest

from insight_engine.core.errors import ConfigurationError
from insight_engine.domain.spec import ComparisonSpec, spec_from_mapping


def _minimal(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "dataset": {
            "primary_source": "s",
            "sources": {
                "s": {
                    "type": "csv",
                    "path": "x.csv",
                    "date_column": "d",
                    "dimensions": ["g"],
                    "metrics": [{"name": "m"}],
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
    base.update(overrides)
    return base


class TestComparison:
    def test_rejects_overlapping_periods(self) -> None:
        with pytest.raises(ConfigurationError, match="overlap"):
            spec_from_mapping(
                _minimal(
                    report={
                        "date_column": "d",
                        "comparison": {
                            "current_start": "2025-01-05",
                            "current_end": "2025-01-10",
                            "previous_start": "2025-01-08",
                            "previous_end": "2025-01-12",
                        },
                    }
                )
            )

    def test_rejects_inverted_window(self) -> None:
        with pytest.raises(ConfigurationError, match="precede"):
            spec_from_mapping(
                _minimal(
                    report={
                        "date_column": "d",
                        "comparison": {
                            "current_start": "2025-01-14",
                            "current_end": "2025-01-08",
                            "previous_start": "2025-01-01",
                            "previous_end": "2025-01-07",
                        },
                    }
                )
            )

    def test_trailing_builds_adjacent_windows(self) -> None:
        comparison = ComparisonSpec.trailing(anchor=date(2025, 1, 14), days=7)
        assert comparison.current_start == date(2025, 1, 8)
        assert comparison.previous_end == date(2025, 1, 7)
        assert comparison.is_like_for_like

    def test_unequal_windows_warn_but_are_allowed(self) -> None:
        spec = spec_from_mapping(
            _minimal(
                report={
                    "date_column": "d",
                    "comparison": {
                        "current_start": "2025-02-01",
                        "current_end": "2025-02-28",
                        "previous_start": "2025-01-01",
                        "previous_end": "2025-01-31",
                    },
                }
            )
        )
        assert any("unequal" in warning for warning in spec.warnings())


class TestCrossFieldValidation:
    def test_rejects_unknown_kpi(self) -> None:
        with pytest.raises(ConfigurationError, match="undefined metrics"):
            spec_from_mapping(
                _minimal(
                    report={
                        "date_column": "d",
                        "kpi_priority": ["nope"],
                        "comparison": {
                            "current_start": "2025-01-08",
                            "current_end": "2025-01-14",
                            "previous_start": "2025-01-01",
                            "previous_end": "2025-01-07",
                        },
                    }
                )
            )

    def test_rejects_unknown_dimension(self) -> None:
        with pytest.raises(ConfigurationError, match="no source declares"):
            spec_from_mapping(
                _minimal(
                    report={
                        "date_column": "d",
                        "dimensions": ["missing"],
                        "comparison": {
                            "current_start": "2025-01-08",
                            "current_end": "2025-01-14",
                            "previous_start": "2025-01-01",
                            "previous_end": "2025-01-07",
                        },
                    }
                )
            )

    def test_rejects_column_used_as_both_roles(self) -> None:
        with pytest.raises(ConfigurationError, match="both a dimension and a metric"):
            spec_from_mapping(
                _minimal(
                    dataset={
                        "primary_source": "s",
                        "sources": {
                            "s": {
                                "type": "csv",
                                "path": "x.csv",
                                "date_column": "d",
                                "dimensions": ["m"],
                                "metrics": [{"name": "m"}],
                            }
                        },
                    }
                )
            )

    def test_rejects_derived_shadowing_base(self) -> None:
        with pytest.raises(ConfigurationError, match="shadow"):
            spec_from_mapping(_minimal(derived_metrics=[{"name": "m", "expression": "m * 2"}]))


class TestSqlSafety:
    @pytest.mark.parametrize(
        "query",
        [
            "DROP TABLE users",
            "SELECT 1; DELETE FROM users",
            "UPDATE users SET admin = 1",
            "INSERT INTO t VALUES (1)",
            "CALL sp_do_something()",
        ],
    )
    def test_rejects_write_statements(self, query: str) -> None:
        with pytest.raises(ConfigurationError):
            spec_from_mapping(
                _minimal(
                    dataset={
                        "primary_source": "s",
                        "sources": {
                            "s": {
                                "type": "sql",
                                "connection_string": "postgresql://h/db",
                                "query": query,
                                "date_column": "d",
                                "metrics": [{"name": "m"}],
                            }
                        },
                    }
                )
            )

    def test_allows_a_read(self) -> None:
        spec = spec_from_mapping(
            _minimal(
                dataset={
                    "primary_source": "s",
                    "sources": {
                        "s": {
                            "type": "sql",
                            "connection_string": "postgresql://h/db",
                            "query": "WITH x AS (SELECT 1) SELECT * FROM x",
                            "date_column": "d",
                            "metrics": [{"name": "m"}],
                        }
                    },
                }
            )
        )
        assert spec.dataset.primary.type == "sql"

    def test_rejects_unsafe_table_name(self) -> None:
        with pytest.raises(ConfigurationError):
            spec_from_mapping(
                _minimal(
                    dataset={
                        "primary_source": "s",
                        "sources": {
                            "s": {
                                "type": "database",
                                "driver": "postgresql",
                                "database": "db",
                                "table": 'users"; DROP TABLE x --',
                                "date_column": "d",
                                "metrics": [{"name": "m"}],
                            }
                        },
                    }
                )
            )
