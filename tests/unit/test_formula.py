"""The derived-metric evaluator must never execute arbitrary code."""

from __future__ import annotations

import polars as pl
import pytest

from insight_engine.core.errors import FormulaError
from insight_engine.engine import formula


class TestParsing:
    def test_reports_dependencies(self) -> None:
        parsed = formula.parse("(revenue - cost) / impressions")
        assert parsed.dependencies == {"revenue", "cost", "impressions"}

    def test_supports_nested_arithmetic(self) -> None:
        # The previous regex evaluator matched exactly "a OP b", so anything
        # with parentheses or three terms was simply not expressible.
        assert formula.parse("(a + b) / (c * 2)").dependencies == {"a", "b", "c"}

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('id')",
            "().__class__.__bases__",
            "clicks.__class__",
            "lambda: 1",
            "[1, 2, 3]",
            "{'a': 1}",
            "a if b else c",
            "exec('x=1')",
            "open('/etc/passwd')",
            "a and b",
            "a << b",
            "f'{a}'",
        ],
    )
    def test_rejects_code_execution(self, expression: str) -> None:
        with pytest.raises(FormulaError):
            formula.parse(expression)

    def test_rejects_unknown_function(self) -> None:
        with pytest.raises(FormulaError, match="Unknown function"):
            formula.parse("eval(a)")

    def test_rejects_empty(self) -> None:
        with pytest.raises(FormulaError):
            formula.parse("   ")

    def test_rejects_overlong(self) -> None:
        with pytest.raises(FormulaError, match="longer than"):
            formula.parse("a + " * 200 + "b")


class TestPolarsCompilation:
    def test_computes_ratio(self) -> None:
        frame = pl.DataFrame({"clicks": [10, 20], "impressions": [100, 50]})
        result = frame.with_columns(
            formula.to_polars("clicks / impressions", available=set(frame.columns), alias="ctr")
        )
        assert result["ctr"].to_list() == [0.1, 0.4]

    def test_division_by_zero_is_null_not_zero(self) -> None:
        # Emitting 0.0 would assert "the rate was zero" when the truth is
        # "the rate is undefined", and that lie propagates into the ranking.
        frame = pl.DataFrame({"a": [1.0], "b": [0.0]})
        result = frame.with_columns(formula.to_polars("a / b", alias="ratio"))
        assert result["ratio"].to_list() == [None]

    def test_unknown_column_fails_early(self) -> None:
        with pytest.raises(FormulaError, match="unknown columns"):
            formula.to_polars("a / missing", available={"a"})


class TestScalarEvaluation:
    def test_evaluates_aggregated_inputs(self) -> None:
        assert (
            formula.evaluate_scalars("clicks / impressions", {"clicks": 30.0, "impressions": 150.0})
            == 0.2
        )

    def test_zero_denominator_returns_none(self) -> None:
        assert formula.evaluate_scalars("a / b", {"a": 1.0, "b": 0.0}) is None

    def test_missing_metric_is_an_error(self) -> None:
        with pytest.raises(FormulaError, match="unknown metrics"):
            formula.evaluate_scalars("a / b", {"a": 1.0})

    def test_coalesce_short_circuits_on_none(self) -> None:
        assert formula.evaluate_scalars("coalesce(a, b)", {"a": 3.0, "b": 9.0}) == 3.0

    def test_non_finite_results_are_none(self) -> None:
        assert formula.evaluate_scalars("a ** b", {"a": 1e308, "b": 5.0}) is None
