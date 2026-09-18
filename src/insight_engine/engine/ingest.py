"""Load every source in a spec and join them into one frame."""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import polars as pl

from insight_engine.connectors import SourcePolicy, load_source
from insight_engine.core.errors import DataError
from insight_engine.core.logging import get_logger
from insight_engine.domain.spec import AnalysisSpec

logger = get_logger("engine.ingest")


@dataclass
class IngestResult:
    frame: pl.DataFrame
    row_count: int
    warnings: list[str] = field(default_factory=list)


def ingest(spec: AnalysisSpec, policy: SourcePolicy) -> IngestResult:
    """Load and join every source into a single analysis frame."""
    loaded = {
        name: load_source(name, source, policy) for name, source in spec.dataset.sources.items()
    }
    warnings = [warning for source in loaded.values() for warning in source.warnings]

    frames = {name: source.frame for name, source in loaded.items()}
    frame = _join(frames, spec) if len(frames) > 1 else next(iter(frames.values()))

    if spec.report.date_column not in frame.columns:
        raise DataError(
            f"Date column {spec.report.date_column!r} is not present after joining sources",
            context={"available": frame.columns[:50]},
        )

    logger.info("ingest complete", extra={"rows": frame.height, "columns": len(frame.columns)})
    return IngestResult(frame=frame, row_count=frame.height, warnings=warnings)


def _join_keys_for(spec: AnalysisSpec, source_name: str, available: set[str]) -> list[str]:
    """Columns to join a secondary source on.

    An explicit ``join_keys`` wins. Otherwise fall back to the report
    dimensions plus the date column, intersected with what the source actually
    has — joining on a key a source lacks would silently produce a cross join.
    """
    source = spec.dataset.sources[source_name]
    if source.join_keys:
        return [key for key in source.join_keys if key in available]
    candidates = [*spec.report.dimensions, spec.report.date_column]
    return [key for key in candidates if key in available]


def _join(frames: dict[str, pl.DataFrame], spec: AnalysisSpec) -> pl.DataFrame:
    """Join with DuckDB, falling back to Polars.

    DuckDB handles heterogeneous key types and spills to disk on large joins;
    Polars is the fallback so a DuckDB problem degrades rather than fails.
    """
    try:
        return _join_duckdb(frames, spec)
    except Exception as error:
        logger.warning("duckdb join failed, falling back to polars", extra={"error": str(error)})
        return _join_polars(frames, spec)


def _join_duckdb(frames: dict[str, pl.DataFrame], spec: AnalysisSpec) -> pl.DataFrame:
    primary_name = spec.dataset.primary_source
    primary_columns = list(frames[primary_name].columns)

    connection = duckdb.connect(":memory:")
    try:
        for name, frame in frames.items():
            connection.register(_safe_relation(name), frame.to_arrow())

        select_parts = [
            f'"{_safe_relation(primary_name)}"."{column}" AS "{column}"'
            for column in primary_columns
        ]
        taken = set(primary_columns)

        joins: list[str] = []
        for name, frame in frames.items():
            if name == primary_name:
                continue
            relation = _safe_relation(name)
            keys = _join_keys_for(spec, name, set(frame.columns) & taken)
            if not keys:
                logger.warning("skipping source with no shared join key", extra={"source": name})
                continue
            for column in frame.columns:
                if column not in taken:
                    select_parts.append(f'"{relation}"."{column}" AS "{column}"')
                    taken.add(column)
            condition = " AND ".join(
                f'"{_safe_relation(primary_name)}"."{key}" = "{relation}"."{key}"' for key in keys
            )
            joins.append(f'LEFT JOIN "{relation}" ON {condition}')

        # Fragments are built from column and relation names that already passed
        # spec validation, and every one is double-quoted.
        sql = f'SELECT {", ".join(select_parts)} FROM "{_safe_relation(primary_name)}" ' + " ".join(  # noqa: S608
            joins
        )
        result: pl.DataFrame = connection.execute(sql).pl()
        return result
    finally:
        connection.close()


def _join_polars(frames: dict[str, pl.DataFrame], spec: AnalysisSpec) -> pl.DataFrame:
    primary_name = spec.dataset.primary_source
    result = frames[primary_name]

    for name, frame in frames.items():
        if name == primary_name:
            continue
        keys = _join_keys_for(spec, name, set(frame.columns) & set(result.columns))
        if not keys:
            logger.warning("skipping source with no shared join key", extra={"source": name})
            continue
        new_columns = [c for c in frame.columns if c not in result.columns or c in keys]
        result = result.join(frame.select(new_columns), on=keys, how="left")

    return result


def _safe_relation(name: str) -> str:
    """Quote-safe relation alias. Source names come from a validated spec."""
    return name.replace('"', "")
