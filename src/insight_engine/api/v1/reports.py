"""Report generation.

Generation is queued, not awaited. A run reads files, calls a model and renders
a deck — seconds to minutes of blocking work — so the handler returns ``202``
with a job id and the client polls. The previous design did all of it inside an
``async def``, which blocked the event loop for the duration and made one report
enough to stall every other request on the process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response, status

from insight_engine.api.deps import JobsDep, RateLimitedDep, ServiceDep, UploadsDep
from insight_engine.api.schemas import ErrorResponse, JobAccepted, ReportRequest, SpecReportRequest
from insight_engine.api.spec_builder import spec_from_report_request
from insight_engine.core.errors import NotFoundError
from insight_engine.core.logging import get_logger
from insight_engine.domain.spec import AnalysisSpec
from insight_engine.jobs.manager import ProgressReporter

logger = get_logger("api.reports")

router = APIRouter(tags=["Reports"])

ACCEPTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "The specification is invalid"},
    404: {"model": ErrorResponse, "description": "The referenced upload has expired"},
    422: {"model": ErrorResponse, "description": "The request payload failed validation"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
}


@router.post(
    "/reports",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a report from an uploaded dataset",
    responses=ACCEPTED_RESPONSES,
)
def create_report(
    request: ReportRequest,
    principal: RateLimitedDep,
    service: ServiceDep,
    jobs: JobsDep,
    uploads: UploadsDep,
    response: Response,
) -> JobAccepted:
    """Queue a report described by the UI's form payload."""
    staged = uploads.resolve(request.upload_id)
    if staged is None:
        raise NotFoundError(
            "That upload has expired or does not exist. Upload the dataset again.",
            context={"upload_id": request.upload_id},
        )

    spec = spec_from_report_request(request)
    return _queue(
        spec,
        base_path=staged.path.parent,
        include_audio=request.include_audio,
        service=service,
        jobs=jobs,
        owner=principal.key_id,
        response=response,
    )


@router.post(
    "/reports/from-spec",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a report from a full specification document",
    responses=ACCEPTED_RESPONSES,
)
def create_report_from_spec(
    request: SpecReportRequest,
    principal: RateLimitedDep,
    service: ServiceDep,
    jobs: JobsDep,
    uploads: UploadsDep,
    response: Response,
) -> JobAccepted:
    """Queue a report from a complete spec, as version-controlled in a repository.

    Relative CSV paths resolve against ``upload_id`` when given. Without one the
    spec may only reference sources the deployment itself can reach, which is
    what keeps the endpoint from being used to read arbitrary server files.
    """
    base_path: Path | None = None
    if request.upload_id:
        staged = uploads.resolve(request.upload_id)
        if staged is None:
            raise NotFoundError(
                "That upload has expired or does not exist.",
                context={"upload_id": request.upload_id},
            )
        base_path = staged.path.parent

    return _queue(
        request.spec,
        base_path=base_path,
        include_audio=request.include_audio,
        service=service,
        jobs=jobs,
        owner=principal.key_id,
        response=response,
    )


def _queue(
    spec: AnalysisSpec,
    *,
    base_path: Path | None,
    include_audio: bool,
    service: ServiceDep,
    jobs: JobsDep,
    owner: str,
    response: Response,
) -> JobAccepted:
    def body(report_progress: ProgressReporter) -> dict[str, object]:
        outcome = service.generate_report(
            spec,
            base_path=base_path,
            on_progress=report_progress,
            include_audio=include_audio,
        )
        return outcome.to_public_dict()

    job = jobs.submit(body, owner=owner)
    poll_url = f"/api/v1/jobs/{job.id}"
    response.headers["Location"] = poll_url
    logger.info("report queued", extra={"job_id": job.id, "title": spec.report.title})
    return JobAccepted(job_id=job.id, status=job.status.value, poll_url=poll_url)
