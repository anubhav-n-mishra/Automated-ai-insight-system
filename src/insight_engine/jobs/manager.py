"""In-process job manager.

Report generation reads files, calls a model and renders a deck: seconds to
minutes of blocking work. Running it inside an ``async def`` handler — as the
previous version did — blocks the event loop, so a single report froze *every*
concurrent request, health checks included.

Work therefore runs on a bounded thread pool while the request thread returns a
job id immediately. The pool is bounded on purpose: unbounded concurrency
converts a traffic spike into memory exhaustion, and the queue is the back
pressure signal.

Single-node by design. :class:`JobManager` is the seam a Celery, RQ or ARQ
backend implements for horizontal scale; see ``docs/adr/0003-job-execution.md``.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from insight_engine.core.errors import InsightEngineError
from insight_engine.core.logging import get_logger, new_request_id, request_context
from insight_engine.core.telemetry import METRICS
from insight_engine.jobs.models import Job, JobStatus

logger = get_logger("jobs")

#: ``(stage, fraction)`` reporter handed to the job body.
ProgressReporter = Callable[[str, float], None]
JobBody = Callable[[ProgressReporter], dict[str, Any]]


class JobManager:
    """Submits jobs to a thread pool and tracks their state."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        retention_seconds: int = 24 * 3600,
        max_tracked: int = 2000,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="insight-job"
        )
        self._jobs: dict[str, Job] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.RLock()
        self._retention = retention_seconds
        self._max_tracked = max_tracked
        self._max_workers = max_workers
        self._closed = False

    # ------------------------------------------------------------------ submit
    def submit(self, body: JobBody, *, owner: str | None = None) -> Job:
        """Queue ``body`` and return its job record immediately."""
        if self._closed:
            raise InsightEngineError("The job manager is shutting down", internal_detail="closed")

        job = Job(id=uuid.uuid4().hex, owner=owner)
        correlation_id = new_request_id()

        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()

        future = self._executor.submit(self._run, job.id, body, correlation_id)
        with self._lock:
            self._futures[job.id] = future

        METRICS.counter("insight_engine_jobs_total", labels={"outcome": "submitted"})
        self._publish_depth()
        return job

    # --------------------------------------------------------------------- run
    def _run(self, job_id: str, body: JobBody, correlation_id: str) -> None:
        with request_context(correlation_id):
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.status is JobStatus.CANCELLED:
                    return
                job.status = JobStatus.RUNNING
                job.stage = "starting"
                job.started_at = datetime.now(timezone.utc)

            def report(stage: str, fraction: float) -> None:
                with self._lock:
                    tracked = self._jobs.get(job_id)
                    if tracked is None:
                        return
                    tracked.stage = stage
                    # Progress never goes backwards: a UI that rewinds looks broken.
                    tracked.progress = max(tracked.progress, min(max(fraction, 0.0), 1.0))

            try:
                result = body(report)
            except InsightEngineError as error:
                self._fail(job_id, error.code, error.message, internal=error.internal_detail)
            except Exception as error:
                logger.exception("job failed", extra={"job_id": job_id})
                self._fail(
                    job_id,
                    "internal_error",
                    "The report could not be generated. The failure has been logged.",
                    internal=f"{type(error).__name__}: {error}",
                )
            else:
                with self._lock:
                    tracked = self._jobs.get(job_id)
                    if tracked is None or tracked.status is JobStatus.CANCELLED:
                        return
                    tracked.status = JobStatus.SUCCEEDED
                    tracked.stage = "complete"
                    tracked.progress = 1.0
                    tracked.result = result
                    tracked.finished_at = datetime.now(timezone.utc)
                    duration = tracked.duration_ms or 0
                METRICS.counter("insight_engine_jobs_total", labels={"outcome": "succeeded"})
                METRICS.observe("insight_engine_job_seconds", duration / 1000.0)
                logger.info("job succeeded", extra={"job_id": job_id, "duration_ms": duration})
            finally:
                with self._lock:
                    self._futures.pop(job_id, None)
                self._publish_depth()

    def _fail(self, job_id: str, code: str, message: str, *, internal: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = JobStatus.FAILED
            job.stage = "failed"
            job.error_code = code
            job.error_message = message
            job.finished_at = datetime.now(timezone.utc)
        METRICS.counter("insight_engine_jobs_total", labels={"outcome": "failed"})
        logger.warning(
            "job failed", extra={"job_id": job_id, "code": code, "detail": internal or message}
        )

    # ------------------------------------------------------------------- query
    def get(self, job_id: str, *, owner: str | None = None) -> Job | None:
        """Fetch a job, scoped to ``owner`` when the deployment authenticates.

        Job ids are unguessable, but an authenticated deployment should still
        not let one caller poll another caller's job.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if owner is not None and job.owner is not None and job.owner != owner:
                return None
            return job

    def cancel(self, job_id: str, *, owner: str | None = None) -> bool:
        """Cancel a job that has not started yet."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status.is_terminal:
                return False
            if owner is not None and job.owner is not None and job.owner != owner:
                return False
            future = self._futures.get(job_id)
            if future is not None and not future.cancel():
                return False
            job.status = JobStatus.CANCELLED
            job.stage = "cancelled"
            job.finished_at = datetime.now(timezone.utc)
        METRICS.counter("insight_engine_jobs_total", labels={"outcome": "cancelled"})
        return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            counts = {status.value: 0 for status in JobStatus}
            for job in self._jobs.values():
                counts[job.status.value] += 1
            counts["tracked"] = len(self._jobs)
            counts["workers"] = self._max_workers
            return counts

    # ---------------------------------------------------------------- lifecycle
    def purge_expired(self) -> int:
        """Forget terminal jobs older than the retention window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._retention)
        with self._lock:
            # <=, not <: cutoff is computed strictly after finished_at is set, so
            # finished_at == cutoff only happens when the clock's resolution is
            # coarser than the gap between the two — a job that just finished
            # under retention_seconds=0 still needs to count as expired then.
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status.is_terminal and (job.finished_at or job.created_at) <= cutoff
            ]
            for job_id in stale:
                del self._jobs[job_id]
        return len(stale)

    def _prune_locked(self) -> None:
        """Bound the tracked set, dropping the oldest terminal jobs first."""
        if len(self._jobs) <= self._max_tracked:
            return
        terminal = sorted(
            (job for job in self._jobs.values() if job.status.is_terminal),
            key=lambda job: job.finished_at or job.created_at,
        )
        for job in terminal[: len(self._jobs) - self._max_tracked]:
            self._jobs.pop(job.id, None)

    def _publish_depth(self) -> None:
        stats = self.stats()
        METRICS.gauge("insight_engine_jobs_queued", stats[JobStatus.QUEUED.value])
        METRICS.gauge("insight_engine_jobs_running", stats[JobStatus.RUNNING.value])

    def shutdown(self, *, wait: bool = True, timeout: float | None = None) -> None:
        """Stop accepting work and drain the pool."""
        self._closed = True
        if timeout is not None and wait:
            with self._lock:
                futures = list(self._futures.values())
            for future in futures:
                # Draining: every failure was already recorded on its job record.
                with contextlib.suppress(Exception):
                    future.result(timeout=timeout)
            self._executor.shutdown(wait=False)
            return
        self._executor.shutdown(wait=wait)
