"""Job records."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass
class Job:
    """One unit of background work and everything a poller needs to see."""

    id: str
    status: JobStatus = JobStatus.QUEUED
    stage: str = "queued"
    progress: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    owner: str | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(timezone.utc)
        return int((end - self.started_at).total_seconds() * 1000)

    def to_public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.id,
            "status": self.status.value,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
        }
        if self.status is JobStatus.SUCCEEDED:
            payload["result"] = self.result
        if self.status is JobStatus.FAILED:
            payload["error"] = {"code": self.error_code, "message": self.error_message}
        return payload
