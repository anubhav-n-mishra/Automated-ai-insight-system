"""Shareable dashboard sessions."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from insight_engine.api.deps import ServiceDep
from insight_engine.api.schemas import DashboardResponse, ErrorResponse
from insight_engine.core.errors import NotFoundError

router = APIRouter(tags=["Dashboards"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "model": ErrorResponse,
        "description": "The session does not exist, has expired, or the token is wrong",
    }
}


@router.get(
    "/dashboards/{session_id}",
    response_model=DashboardResponse,
    summary="Read a dashboard session",
    responses=NOT_FOUND,
)
def get_dashboard(
    session_id: str,
    service: ServiceDep,
    response: Response,
    token: Annotated[str, Query(min_length=16, max_length=128, description="Session access token")],
) -> DashboardResponse:
    """Return a session's analysis payload.

    The token is **required**. The previous implementation declared it
    ``token: str = None`` and only compared it when present, so omitting the
    parameter skipped the check entirely and any session id granted access.

    A wrong token and a missing session are both reported as 404, so the
    endpoint cannot be used to enumerate which session ids exist.
    """
    session = service.load_session(session_id, token)
    if session is None:
        raise NotFoundError("This dashboard link is invalid or has expired.")

    response.headers["Cache-Control"] = "private, no-store"
    # Share links are secrets; keep them out of third-party referrer logs.
    response.headers["Referrer-Policy"] = "no-referrer"

    return DashboardResponse(
        session_id=session.session_id,
        title=session.title,
        created_at=session.created_at.isoformat(),
        expires_at=session.expires_at.isoformat(),
        analysis=session.payload,
        briefing=session.briefing,
        report_url=(f"/api/v1/artifacts/{session.report_key}" if session.report_key else None),
    )
