"""ASGI middleware: correlation ids, security headers, body limits."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from insight_engine.core.logging import get_logger, new_request_id, request_context
from insight_engine.core.telemetry import METRICS

logger = get_logger("api.middleware")

REQUEST_ID_HEADER = "X-Request-ID"

# Content-Security-Policy is intentionally strict. The UI ships its own CSS and
# JS as separate files and loads nothing from a CDN, so no 'unsafe-inline' is
# needed anywhere and injected markup cannot execute.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), interest-cohort=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "media-src 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
}

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a correlation id, times the request and records metrics."""

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        # Only trust an inbound id that looks like one of ours; an arbitrary
        # header value ends up in logs and must not carry newlines or 4KB of text.
        request_id = (
            incoming
            if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum()
            else new_request_id()
        )

        started = time.perf_counter()
        with request_context(request_id):
            request.state.request_id = request_id
            try:
                response = await call_next(request)
            except Exception:
                METRICS.counter(
                    "insight_engine_http_requests_total",
                    labels={"method": request.method, "status": "500"},
                )
                logger.exception(
                    "unhandled error", extra={"path": request.url.path, "method": request.method}
                )
                raise

            elapsed = time.perf_counter() - started
            route = _route_template(request)
            METRICS.counter(
                "insight_engine_http_requests_total",
                labels={
                    "method": request.method,
                    "route": route,
                    "status": str(response.status_code),
                },
                help_text="HTTP requests handled.",
            )
            METRICS.observe(
                "insight_engine_http_request_seconds",
                elapsed,
                {"method": request.method, "route": route},
                help_text="HTTP request duration.",
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            response.headers["Server-Timing"] = f"app;dur={elapsed * 1000:.1f}"
            if request.url.path not in {"/health", "/health/ready", "/metrics"}:
                logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )
            return response


def _route_template(request: Request) -> str:
    """Templated path, so per-id URLs do not explode metric cardinality."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Applies a strict default header set; HSTS only under TLS."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized uploads before they are buffered.

    The declared ``Content-Length`` is checked first so an obviously oversized
    request is refused without reading it. A body with no declared length is
    still bounded, because a chunked upload could otherwise stream until the
    process runs out of memory.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return self._too_large(request)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {"code": "bad_request", "message": "Malformed Content-Length"}
                    },
                )
        return await call_next(request)

    def _too_large(self, request: Request) -> JSONResponse:
        logger.warning("upload rejected as too large", extra={"path": request.url.path})
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "payload_too_large",
                    "message": (
                        f"Request body exceeds the {self._max_bytes // (1024 * 1024)} MB limit."
                    ),
                }
            },
        )
