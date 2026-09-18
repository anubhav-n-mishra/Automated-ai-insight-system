"""Exception handlers.

Every error leaves the process in the same envelope, and nothing internal
leaves with it. The previous handler returned ``str(exc)`` for unhandled
exceptions, which served filesystem paths, connection strings and stack frames
straight to the client.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from insight_engine.core.errors import InsightEngineError, RateLimitedError
from insight_engine.core.logging import current_request_id, get_logger

logger = get_logger("api.errors")


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    context: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if context:
        payload["error"]["context"] = context
    request_id = current_request_id()
    if request_id:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status, content=payload, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InsightEngineError)
    async def _domain_error(request: Request, exc: InsightEngineError) -> JSONResponse:
        if exc.internal_detail:
            logger.warning("domain error", extra={"code": exc.code, "detail": exc.internal_detail})
        headers = (
            {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitedError) else None
        )
        return error_response(
            exc.status, exc.code, exc.message, context=exc.context or None, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in item.get("loc", ()) if part != "body"),
                "message": item.get("msg", "invalid value"),
            }
            for item in exc.errors()[:20]
        ]
        return error_response(
            422, "validation_error", "The request payload is invalid.", context={"fields": fields}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            415: "unsupported_media_type",
            429: "rate_limited",
        }
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), detail)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The detail is logged with the correlation id and never serialised.
        logger.exception(
            "unhandled exception",
            extra={"path": request.url.path, "exception_type": type(exc).__name__},
        )
        return error_response(
            500,
            "internal_error",
            "The server could not complete the request. The failure has been logged with "
            "this request id.",
        )
