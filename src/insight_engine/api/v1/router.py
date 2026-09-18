"""v1 route table."""

from __future__ import annotations

from fastapi import APIRouter

from insight_engine.api.v1 import artifacts, dashboards, datasets, jobs, reports

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(datasets.router)
api_router.include_router(reports.router)
api_router.include_router(jobs.router)
api_router.include_router(dashboards.router)
api_router.include_router(artifacts.router)

__all__ = ["api_router"]
