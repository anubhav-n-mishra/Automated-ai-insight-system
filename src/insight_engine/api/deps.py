"""Dependency wiring.

Long-lived collaborators are built once in the lifespan handler and hung off
``app.state``; request handlers receive them through ``Depends``. Nothing
reaches for a module-level global, so tests construct an app with substitutes
instead of monkeypatching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Header, Request

from insight_engine.api.security import (
    ANONYMOUS,
    Principal,
    SlidingWindowRateLimiter,
    authenticate,
)
from insight_engine.api.uploads import UploadStore
from insight_engine.core.config import Settings
from insight_engine.jobs import JobManager
from insight_engine.service import ReportService


@dataclass
class AppContext:
    """Everything the request layer needs, assembled at startup."""

    settings: Settings
    service: ReportService
    jobs: JobManager
    uploads: UploadStore
    rate_limiter: SlidingWindowRateLimiter


def get_context(request: Request) -> AppContext:
    # Starlette's `State` is dynamically typed by design.
    context = cast(AppContext | None, getattr(request.app.state, "context", None))
    if context is None:  # pragma: no cover - only reachable if startup failed
        raise RuntimeError("Application context is not initialised")
    return context


ContextDep = Annotated[AppContext, Depends(get_context)]


def get_settings_dep(context: ContextDep) -> Settings:
    return context.settings


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


def get_service(context: ContextDep) -> ReportService:
    return context.service


ServiceDep = Annotated[ReportService, Depends(get_service)]


def get_jobs(context: ContextDep) -> JobManager:
    return context.jobs


JobsDep = Annotated[JobManager, Depends(get_jobs)]


def get_uploads(context: ContextDep) -> UploadStore:
    return context.uploads


UploadsDep = Annotated[UploadStore, Depends(get_uploads)]


def get_principal(
    context: ContextDep,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    """Authenticate the caller. Open deployments resolve to the anonymous principal."""
    return authenticate(context.settings, x_api_key)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def rate_limited(request: Request, context: ContextDep, principal: PrincipalDep) -> Principal:
    """Charge this request against the caller's budget.

    Authenticated callers are bucketed by key fingerprint. Anonymous callers
    fall back to the peer address, which is imperfect behind a proxy — the
    deployment guide covers putting a real limiter at the ingress — but is far
    better than one shared global bucket.
    """
    bucket = (
        f"key:{principal.key_id}"
        if principal.authenticated
        else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    context.rate_limiter.check(bucket)
    return principal


RateLimitedDep = Annotated[Principal, Depends(rate_limited)]

__all__ = [
    "ANONYMOUS",
    "AppContext",
    "ContextDep",
    "JobsDep",
    "Principal",
    "PrincipalDep",
    "RateLimitedDep",
    "ServiceDep",
    "SettingsDep",
    "UploadsDep",
    "get_context",
]
