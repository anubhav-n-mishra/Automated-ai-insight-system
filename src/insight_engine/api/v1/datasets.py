"""Dataset upload and profiling."""

from __future__ import annotations

from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, File, UploadFile, status

from insight_engine.api.deps import RateLimitedDep, SettingsDep, UploadsDep
from insight_engine.api.schemas import ErrorResponse, ProfileResponse
from insight_engine.api.uploads import sanitize_filename
from insight_engine.connectors.csv import sniff_delimiter
from insight_engine.core.errors import SourceError
from insight_engine.core.logging import get_logger
from insight_engine.engine.profiling import profile_dataframe

logger = get_logger("api.datasets")

router = APIRouter(tags=["Datasets"])

PREVIEW_ROWS = 8
PROFILE_ROWS = 20_000


@router.post(
    "/datasets",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a dataset and profile it",
    responses={
        400: {"model": ErrorResponse, "description": "The file is not readable delimited text"},
        413: {"model": ErrorResponse, "description": "The file exceeds the upload limit"},
    },
)
async def upload_dataset(
    _: RateLimitedDep,
    uploads: UploadsDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="CSV or TSV file")],
) -> ProfileResponse:
    """Stage a dataset and infer its column roles and a usable date range.

    Profiling is local: dtypes, cardinality and name patterns. Nothing is sent
    to a model provider, so this works offline and does not put customer data in
    front of a third party just to classify headers.
    """
    data = await _read_upload(file, limit=settings.max_upload_bytes)
    staged = uploads.save(data, original_name=file.filename)

    delimiter = sniff_delimiter(staged.path)
    try:
        frame = pl.read_csv(
            staged.path,
            separator=delimiter,
            try_parse_dates=True,
            infer_schema_length=10_000,
            n_rows=PROFILE_ROWS,
        )
    except pl.exceptions.PolarsError as error:
        uploads.delete(staged.upload_id)
        raise SourceError(
            "Could not parse the file as delimited text. Check the delimiter and quoting.",
            internal_detail=str(error),
            context={"detected_delimiter": delimiter},
        ) from error

    frame = frame.rename({column: column.lstrip("﻿").strip() for column in frame.columns})
    if frame.height == 0:
        uploads.delete(staged.upload_id)
        raise SourceError("The file has a header but no data rows.")

    profile = profile_dataframe(frame)
    payload = profile.as_dict()
    logger.info(
        "dataset profiled",
        extra={
            "upload_id": staged.upload_id,
            "rows": frame.height,
            "columns": len(frame.columns),
            "date_column": profile.date_column,
        },
    )

    return ProfileResponse(
        upload_id=staged.upload_id,
        filename=sanitize_filename(staged.original_name),
        row_count=payload["row_count"],
        date_column=payload["date_column"],
        date_range=payload["date_range"],
        suggested_comparison=payload["suggested_comparison"],
        suggested_dimensions=payload["suggested_dimensions"],
        suggested_metrics=payload["suggested_metrics"],
        columns=payload["columns"],
        notes=payload["notes"],
        preview=_preview(frame),
    )


async def _read_upload(file: UploadFile, *, limit: int) -> bytes:
    """Read an upload in chunks, aborting as soon as it exceeds ``limit``.

    ``await file.read()`` with no bound buffers the whole body first and only
    then discovers it was too large, which is the wrong order when the point is
    to avoid holding it in memory.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > limit:
            from insight_engine.core.errors import PayloadTooLargeError

            raise PayloadTooLargeError(
                f"The uploaded file is larger than the {limit // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _preview(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """First rows, stringified so the response is always JSON-safe."""
    return [
        {key: (None if value is None else str(value)) for key, value in row.items()}
        for row in frame.head(PREVIEW_ROWS).to_dicts()
    ]
