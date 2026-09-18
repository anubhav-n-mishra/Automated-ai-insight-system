"""Job status polling."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from insight_engine.api.deps import JobsDep, PrincipalDep
from insight_engine.api.schemas import ErrorResponse, JobStatusResponse
from insight_engine.core.errors import NotFoundError

router = APIRouter(tags=["Jobs"])


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll a report job",
    responses={404: {"model": ErrorResponse, "description": "Unknown or expired job"}},
)
def get_job(
    job_id: str, principal: PrincipalDep, jobs: JobsDep, response: Response
) -> JobStatusResponse:
    """Current state of a queued report.

    ``Cache-Control: no-store`` matters here: a caching proxy that served a
    stale "running" would leave a client polling forever. While the job is in
    flight the response also carries ``Retry-After`` so a client has a sensible
    interval without hard-coding one.
    """
    job = jobs.get(job_id, owner=principal.key_id if principal.authenticated else None)
    if job is None:
        raise NotFoundError("No such job. It may have completed and been cleaned up.")

    response.headers["Cache-Control"] = "no-store"
    if not job.status.is_terminal:
        response.headers["Retry-After"] = "2"
    return JobStatusResponse(**job.to_public_dict())


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a queued report",
    responses={404: {"model": ErrorResponse, "description": "Unknown job, or already running"}},
)
def cancel_job(job_id: str, principal: PrincipalDep, jobs: JobsDep) -> Response:
    """Cancel a job that has not started executing yet."""
    owner = principal.key_id if principal.authenticated else None
    if not jobs.cancel(job_id, owner=owner):
        raise NotFoundError("That job cannot be cancelled; it may already be running or finished.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
