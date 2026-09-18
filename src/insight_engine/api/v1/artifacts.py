"""Artifact downloads.

Artifacts are served through a handler rather than a mounted static directory.
The previous build mounted the whole working directory at ``/tmp``, which also
exposed ``tmp/sessions/*.json`` — every live session's access token, readable by
anyone who guessed the path.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from insight_engine.api.deps import ServiceDep
from insight_engine.api.schemas import ErrorResponse
from insight_engine.core.errors import NotFoundError

router = APIRouter(tags=["Artifacts"])


@router.get(
    "/artifacts/{key}",
    summary="Download a generated report or audio briefing",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        404: {"model": ErrorResponse, "description": "Unknown or expired artifact"},
    },
)
def download_artifact(key: str, service: ServiceDep) -> Response:
    """Stream a stored artifact.

    Keys are 128-bit random and validated against a strict pattern by the store,
    so a key cannot address anything outside it.
    """
    artifact = service.fetch_artifact(key)
    if artifact is None or artifact.local_path is None:
        raise NotFoundError("That file is no longer available.")

    return FileResponse(
        artifact.local_path,
        media_type=artifact.media_type,
        filename=artifact.download_name,
        headers={
            "Cache-Control": "private, max-age=3600",
            # Belt and braces: a .pptx is never script, and a browser must not
            # be talked into treating it as one.
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{artifact.download_name}"',
        },
    )
