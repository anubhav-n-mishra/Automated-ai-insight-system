"""Application factory.

``create_app`` accepts explicit collaborators so tests can substitute a fake
narrative provider or an in-memory store without patching module globals. The
lifespan handler owns construction and teardown of everything long-lived.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from insight_engine import __version__
from insight_engine.api.deps import AppContext
from insight_engine.api.errors import install_error_handlers
from insight_engine.api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from insight_engine.api.schemas import HealthResponse, ReadinessResponse
from insight_engine.api.security import SlidingWindowRateLimiter
from insight_engine.api.uploads import UploadStore
from insight_engine.api.v1 import api_router
from insight_engine.core.config import Settings, get_settings
from insight_engine.core.logging import configure_logging, get_logger
from insight_engine.core.telemetry import METRICS, instrument_app, setup_tracing
from insight_engine.jobs import JobManager
from insight_engine.llm.base import NarrativeProvider
from insight_engine.llm.registry import build_provider
from insight_engine.service import ReportService
from insight_engine.sessions import FileSessionStore, SessionStore
from insight_engine.storage import LocalArtifactStore
from insight_engine.storage.base import ArtifactStore

logger = get_logger("api.app")

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

DESCRIPTION = """\
Turn period-over-period metric movements into ranked, explainable insights,
a PowerPoint deck, a shareable dashboard and an audio briefing.

**How a report is produced**

1. `POST /api/v1/datasets` stages a CSV and profiles it locally — column roles
   and a date range the data actually covers. Nothing is sent to a model.
2. `POST /api/v1/reports` queues the run and returns `202` with a job id.
3. `GET /api/v1/jobs/{job_id}` reports honest stage-by-stage progress.
4. The finished job carries a deck download and a tokenised dashboard link.

**Authentication** — when `INSIGHT_ENGINE_API_KEYS` is set, every endpoint
except `/health` requires an `X-API-Key` header. Production refuses to start
without it.
"""

TAGS_METADATA = [
    {"name": "Datasets", "description": "Upload and profile a dataset."},
    {"name": "Reports", "description": "Queue report generation."},
    {"name": "Jobs", "description": "Track queued work."},
    {"name": "Dashboards", "description": "Read a shared, tokenised session."},
    {"name": "Artifacts", "description": "Download decks and audio briefings."},
    {"name": "Operations", "description": "Health, readiness and metrics."},
]


def create_app(
    *,
    settings: Settings | None = None,
    artifacts: ArtifactStore | None = None,
    sessions: SessionStore | None = None,
    provider: NarrativeProvider | None = None,
    configure_logs: bool = True,
) -> FastAPI:
    """Build the ASGI application."""
    resolved = settings or get_settings()
    if configure_logs:
        configure_logging(resolved.log_level, resolved.log_format)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved.ensure_directories()

        artifact_store = artifacts or LocalArtifactStore(
            resolved.reports_dir, retention_seconds=resolved.artifact_ttl_hours * 3600
        )
        session_store = sessions or FileSessionStore(resolved.sessions_dir)
        narrative_provider = provider if provider is not None else build_provider(resolved)

        uploads = UploadStore(resolved.uploads_dir, max_bytes=resolved.max_upload_bytes)
        jobs = JobManager(
            max_workers=resolved.worker_threads,
            retention_seconds=resolved.job_retention_seconds,
        )
        service = ReportService(
            settings=resolved,
            artifacts=artifact_store,
            sessions=session_store,
            provider=narrative_provider,
        )

        app.state.context = AppContext(
            settings=resolved,
            service=service,
            jobs=jobs,
            uploads=uploads,
            rate_limiter=SlidingWindowRateLimiter(
                limit=resolved.rate_limit_requests,
                window_seconds=resolved.rate_limit_window_seconds,
            ),
        )

        if resolved.tracing_enabled:
            if setup_tracing(resolved.service_name, resolved.otlp_endpoint, __version__):
                instrument_app(app)
            else:
                logger.warning("tracing requested but the 'otel' extra is not installed")

        # Sessions, uploads and artifacts all expire. Without a sweeper they
        # accumulate until the volume fills, which is how the previous build
        # eventually failed in place rather than loudly.
        janitor = asyncio.create_task(_janitor(service, uploads, jobs, resolved))

        logger.info(
            "application started",
            extra={
                "version": __version__,
                "environment": resolved.environment,
                "auth": "enabled" if resolved.api_keys else "disabled",
                "narrative_provider": getattr(narrative_provider, "name", "template"),
                "workers": resolved.worker_threads,
            },
        )
        try:
            yield
        finally:
            janitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await janitor
            jobs.shutdown(wait=True, timeout=30.0)
            logger.info("application stopped")

    app = FastAPI(
        title="Insight Engine",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        contact={
            "name": "Insight Engine",
            "url": "https://github.com/anubhav-n-mishra/Automated-ai-insight-system",
        },
    )

    # Order matters: the outermost middleware runs first, so oversized bodies
    # are rejected before anything else touches them.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=resolved.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=resolved.is_production)
    app.add_middleware(RequestContextMiddleware)

    if resolved.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
            max_age=600,
        )

    install_error_handlers(app)
    app.include_router(api_router)
    _install_operations(app, resolved)
    _install_ui(app)
    return app


async def _janitor(
    service: ReportService, uploads: UploadStore, jobs: JobManager, settings: Settings
) -> None:
    """Periodically drop expired sessions, uploads, artifacts and job records."""
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)
            # Sweeps touch the filesystem, so they run off the event loop.
            removed = await asyncio.to_thread(service.purge)
            removed["uploads"] = await asyncio.to_thread(uploads.purge_expired)
            removed["jobs"] = await asyncio.to_thread(jobs.purge_expired)
            if any(value for key, value in removed.items() if key != "checked_at"):
                logger.info("cleanup complete", extra=removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cleanup failed")


def _install_operations(app: FastAPI, settings: Settings) -> None:
    @app.get("/health", response_model=HealthResponse, tags=["Operations"], summary="Liveness")
    def health() -> HealthResponse:
        """Liveness probe. Never authenticated, never touches disk."""
        return HealthResponse(status="ok", version=__version__, environment=settings.environment)

    @app.get(
        "/health/ready", response_model=ReadinessResponse, tags=["Operations"], summary="Readiness"
    )
    def readiness(response: Response) -> ReadinessResponse:
        """Readiness probe: can this instance actually serve a report?"""
        context = getattr(app.state, "context", None)
        checks = {
            "context": context is not None,
            "storage_writable": _writable(settings.reports_dir),
            "sessions_writable": _writable(settings.sessions_dir),
        }
        ready = all(checks.values())
        response.status_code = 200 if ready else 503
        provider = None
        if context is not None:
            provider = getattr(context.service._provider, "name", "template")
        return ReadinessResponse(
            status="ready" if ready else "not_ready",
            checks=checks,
            narrative_provider=provider,
        )

    if settings.metrics_enabled:

        @app.get(
            "/metrics", tags=["Operations"], summary="Prometheus metrics", include_in_schema=False
        )
        def metrics() -> PlainTextResponse:
            return PlainTextResponse(
                METRICS.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
            )


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".readiness"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _install_ui(app: FastAPI) -> None:
    """Serve the bundled single-page UI."""
    if not WEB_ROOT.is_dir():  # pragma: no cover - only if the package is stripped
        logger.warning("web assets are missing; the UI will not be served")
        return

    app.mount("/assets", StaticFiles(directory=WEB_ROOT / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html", media_type="text/html")

    @app.get("/d/{session_id}", include_in_schema=False)
    def dashboard_page(session_id: str) -> FileResponse:
        return FileResponse(WEB_ROOT / "dashboard.html", media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        icon = WEB_ROOT / "assets" / "favicon.svg"
        if icon.is_file():
            return FileResponse(icon, media_type="image/svg+xml")
        return Response(status_code=204)
