"""A restricted arithmetic evaluator for derived metrics.

Derived-metric expressions come from user-supplied YAML that may be uploaded
over HTTP, so :func:`eval` is out of the question — ``__import__('os').system``
is a one-line expression. Instead the expression is parsed with :mod:`ast` and
walked against an allowlist of node types; anything else is rejected before a
single operation runs.

The previous implementation used a regular expression that matched exactly
``operand OP operand``, so ``(a + b) / c`` and ``revenue - cost - tax`` were
simply not expressible.

Expressions are compiled into Polars expressions so evaluation stays vectorised
and inside the query engine.
"""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Final

import polars as pl

from insight_engine.core.errors import FormulaError

MAX_EXPRESSION_LENGTH: Final = 512
MAX_AST_NODES: Final = 200

# Unary/binary operators we are willing to execute.
_BINARY_OPS: Final = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_UNARY_OPS: Final = (ast.UAdd, ast.USub)

#: Functions callable from an expression, mapped to a Polars builder.
_FUNCTIONS: Final[dict[str, int]] = {
    "abs": 1,
    "min": 2,
    "max": 2,
    "coalesce": 2,
    "safe_div": 2,
    "log": 1,
    "sqrt": 1,
    "round": 2,
}


@dataclass(frozen=True)
class ParsedFormula:
    """A validated expression plus the identifiers it depends on."""

    expression: str
    dependencies: frozenset[str]


def parse(expression: str) -> ParsedFormula:
    """Validate an expression and report the columns it references.

    Raises :class:`~insight_engine.core.errors.FormulaError` on anything the
    evaluator will not execute, so a bad spec fails at validation time rather
    than halfway through a report.
    """
    if not expression or not expression.strip():
        raise FormulaError("Expression must not be empty")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise FormulaError(
            f"Expression is longer than {MAX_EXPRESSION_LENGTH} characters",
            context={"length": len(expression)},
        )

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise FormulaError(
            f"Cannot parse expression {expression!r}: {error.msg}",
            context={"expression": expression},
        ) from error

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise FormulaError("Expression is too complex", context={"nodes": len(nodes)})

    dependencies: set[str] = set()
    _check(tree.body, dependencies, expression)
    return ParsedFormula(expression=expression, dependencies=frozenset(dependencies))


def _check(node: ast.AST, dependencies: set[str], expression: str) -> None:
    """Depth-first allowlist walk. Anything unrecognised is a hard error."""
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise FormulaError(
                "Only numeric literals are allowed in expressions",
                context={"expression": expression},
            )
        return

    if isinstance(node, ast.Name):
        dependencies.add(node.id)
        return

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BINARY_OPS):
            raise FormulaError(
                f"Operator {type(node.op).__name__} is not allowed",
                context={"expression": expression},
            )
        _check(node.left, dependencies, expression)
        _check(node.right, dependencies, expression)
        return

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UNARY_OPS):
            raise FormulaError(
                f"Unary operator {type(node.op).__name__} is not allowed",
                context={"expression": expression},
            )
        _check(node.operand, dependencies, expression)
        return

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise FormulaError("Only direct function calls are allowed")
        name = node.func.id
        if name not in _FUNCTIONS:
            raise FormulaError(
                f"Unknown function {name!r}. Available: {', '.join(sorted(_FUNCTIONS))}",
                context={"expression": expression},
            )
        if node.keywords:
            raise FormulaError("Keyword arguments are not supported in expressions")
        expected = _FUNCTIONS[name]
        if len(node.args) != expected:
            raise FormulaError(
                f"{name}() takes {expected} argument(s), got {len(node.args)}",
                context={"expression": expression},
            )
        for argument in node.args:
            _check(argument, dependencies, expression)
        return

    raise FormulaError(
        f"Expression element {type(node).__name__} is not allowed",
        context={"expression": expression},
    )


def to_polars(
    expression: str,
    *,
    available: set[str] | None = None,
    alias: str | None = None,
) -> pl.Expr:
    """Compile an expression into a Polars expression.

    Args:
        expression: The formula source.
        available: Column names present in the frame. When given, unknown
            identifiers are rejected here rather than surfacing as an opaque
            Polars ``ColumnNotFound`` several frames later.
        alias: Name for the resulting column.
    """
    parsed = parse(expression)
    if available is not None:
        missing = sorted(parsed.dependencies - available)
        if missing:
            raise FormulaError(
                f"Expression {expression!r} references unknown columns: {', '.join(missing)}",
                context={"missing": missing, "available": sorted(available)},
            )

    tree = ast.parse(expression, mode="eval")
    compiled = _compile(tree.body)
    return compiled.alias(alias) if alias else compiled


def _compile(node: ast.AST) -> pl.Expr:
    if isinstance(node, ast.Constant):
        return pl.lit(float(node.value))  # type: ignore[arg-type]

    if isinstance(node, ast.Name):
        return pl.col(node.id).cast(pl.Float64)

    if isinstance(node, ast.UnaryOp):
        operand = _compile(node.operand)
        return -operand if isinstance(node.op, ast.USub) else operand

    if isinstance(node, ast.BinOp):
        left, right = _compile(node.left), _compile(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            # A zero denominator is data, not an error: emit null so the row is
            # visibly missing rather than silently reported as 0.0.
            return _safe_div(left, right)
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left**right

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        args = [_compile(argument) for argument in node.args]
        if name == "abs":
            return args[0].abs()
        if name == "min":
            return pl.min_horizontal(args[0], args[1])
        if name == "max":
            return pl.max_horizontal(args[0], args[1])
        if name == "coalesce":
            return pl.coalesce(args[0], args[1])
        if name == "safe_div":
            return _safe_div(args[0], args[1])
        if name == "log":
            return args[0].log()
        if name == "sqrt":
            return args[0].sqrt()
        if name == "round":
            return args[0].round(2)

    raise FormulaError(f"Cannot compile expression element {type(node).__name__}")


def _safe_div(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return pl.when(denominator != 0).then(numerator / denominator).otherwise(None)


def evaluate_scalars(expression: str, values: dict[str, float]) -> float | None:
    """Evaluate against plain numbers, for aggregated totals.

    Derived metrics must be recomputed from *aggregated* inputs rather than
    averaged from per-row values, and at that point there is no frame left to
    operate on — just a handful of scalars.
    """
    parsed = parse(expression)
    missing = sorted(parsed.dependencies - set(values))
    if missing:
        raise FormulaError(
            f"Expression {expression!r} references unknown metrics: {', '.join(missing)}",
            context={"missing": missing, "available": sorted(values)},
        )
    tree = ast.parse(expression, mode="eval")
    result = _eval_scalar(tree.body, values)
    if result is None or not math.isfinite(result):
        return None
    return result


def _eval_scalar(node: ast.AST, values: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant):
        return float(node.value)  # type: ignore[arg-type]

    if isinstance(node, ast.Name):
        value = values.get(node.id)
        return None if value is None else float(value)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_scalar(node.operand, values)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand

    if isinstance(node, ast.BinOp):
        left = _eval_scalar(node.left, values)
        right = _eval_scalar(node.right, values)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return None if right == 0 else left / right
            if isinstance(node.op, ast.Mod):
                return None if right == 0 else left % right
            if isinstance(node.op, ast.Pow):
                return float(left**right)
        except (OverflowError, ValueError, ZeroDivisionError):
            return None

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        args = [_eval_scalar(argument, values) for argument in node.args]
        if name == "coalesce":
            return next((value for value in args if value is not None), None)
        if any(value is None for value in args):
            return None
        numbers = [value for value in args if value is not None]
        try:
            if name == "abs":
                return abs(numbers[0])
            if name == "min":
                return min(numbers[0], numbers[1])
            if name == "max":
                return max(numbers[0], numbers[1])
            if name == "safe_div":
                return None if numbers[1] == 0 else numbers[0] / numbers[1]
            if name == "log":
                return None if numbers[0] <= 0 else math.log(numbers[0])
            if name == "sqrt":
                return None if numbers[0] < 0 else math.sqrt(numbers[0])
            if name == "round":
                return round(numbers[0], 2)
        except (OverflowError, ValueError):
            return None

    raise FormulaError(f"Cannot evaluate expression element {type(node).__name__}")
