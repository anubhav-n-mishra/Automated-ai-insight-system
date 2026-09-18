"""Delimited-file connector."""

from __future__ import annotations

import csv as csv_module
from pathlib import Path

import polars as pl

from insight_engine.connectors.base import (
    LoadedSource,
    SourcePolicy,
    enforce_row_cap,
    require_columns,
)
from insight_engine.core.errors import SourceError
from insight_engine.core.logging import get_logger
from insight_engine.domain.spec import CsvSourceSpec

logger = get_logger("connectors.csv")

# Read at most this much when sniffing. A header line is tiny; anything larger
# is a malformed file we would rather fail on than buffer.
_SNIFF_BYTES = 64 * 1024
_CANDIDATE_DELIMITERS = ",;\t|"


def sniff_delimiter(path: Path, encoding: str = "utf-8") -> str:
    """Detect the delimiter from the header, falling back to a comma.

    :class:`csv.Sniffer` is tried first because it understands quoting; the
    frequency count is a backstop for headers it refuses to classify.
    """
    try:
        with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
            sample = handle.read(_SNIFF_BYTES)
    except OSError as error:
        raise SourceError(
            f"Cannot read {path.name}", internal_detail=str(error), context={"file": path.name}
        ) from error

    if not sample.strip():
        raise SourceError(f"File {path.name} is empty", context={"file": path.name})

    try:
        dialect = csv_module.Sniffer().sniff(sample, delimiters=_CANDIDATE_DELIMITERS)
    except csv_module.Error:
        pass
    else:
        return dialect.delimiter

    header = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {candidate: header.count(candidate) for candidate in _CANDIDATE_DELIMITERS}
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] > 0 else ","


class CsvConnector:
    """Loads CSV/TSV files with Polars."""

    source_type = "csv"

    def load(self, name: str, spec: object, policy: SourcePolicy) -> LoadedSource:
        if not isinstance(spec, CsvSourceSpec):
            raise SourceError(f"CsvConnector cannot load a {type(spec).__name__}")

        path = policy.resolve_path(spec.path)
        if not path.is_file():
            raise SourceError(
                f"Data file not found: {path.name}",
                internal_detail=f"resolved to {path}",
                context={"path": spec.path},
            )

        delimiter = spec.delimiter or sniff_delimiter(path, spec.encoding)
        logger.info(
            "loading csv source",
            extra={"source": name, "file": path.name, "delimiter": delimiter},
        )

        try:
            frame = pl.read_csv(
                path,
                separator=delimiter,
                null_values=spec.null_values,
                encoding=(
                    "utf8-lossy"
                    if spec.encoding.lower().replace("-", "") == "utf8"
                    else spec.encoding
                ),
                try_parse_dates=True,
                infer_schema_length=10_000,
                # BOM-prefixed headers otherwise produce a first column literally
                # named "﻿ID", which then fails every column lookup.
                truncate_ragged_lines=False,
            )
        except pl.exceptions.PolarsError as error:
            raise SourceError(
                f"Could not parse {path.name} as delimited text",
                internal_detail=str(error),
                context={"file": path.name, "delimiter": delimiter},
            ) from error

        frame = frame.rename({column: column.lstrip("﻿").strip() for column in frame.columns})

        if frame.height == 0:
            raise SourceError(
                f"File {path.name} contains no data rows", context={"file": path.name}
            )

        required = {spec.date_column, *spec.dimensions, *(m.source_column for m in spec.metrics)}
        require_columns(frame, required, source=name)

        frame, truncated = enforce_row_cap(frame, name=name, max_rows=policy.max_rows)
        warnings: list[str] = []
        if truncated:
            warnings.append(f"Source {name!r} was truncated to the first {policy.max_rows:,} rows.")

        return LoadedSource(
            name=name, frame=frame, row_count=frame.height, truncated=truncated, warnings=warnings
        )
