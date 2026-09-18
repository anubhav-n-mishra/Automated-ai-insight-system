"""Typed error hierarchy.

Every error carries a stable ``code`` and an HTTP ``status`` so the API layer
can translate domain failures into responses without a chain of ``except``
clauses, and clients can branch on ``code`` instead of parsing prose.

``message`` is safe to show a caller. Anything sensitive (file system paths,
connection strings, driver stack traces) belongs in ``internal_detail``, which
is logged but never serialised into a response.
"""

from __future__ import annotations

from typing import Any


class InsightEngineError(Exception):
    """Base class for every error raised deliberately by this package."""

    code: str = "internal_error"
    status: int = 500

    def __init__(
        self,
        message: str,
        *,
        internal_detail: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.internal_detail = internal_detail
        self.context: dict[str, Any] = context or {}

    def to_payload(self) -> dict[str, Any]:
        """Client-safe representation. Deliberately omits ``internal_detail``."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.context:
            payload["context"] = self.context
        return payload


class ConfigurationError(InsightEngineError):
    """The analysis specification is malformed or self-inconsistent."""

    code = "invalid_configuration"
    status = 400


class SourceError(InsightEngineError):
    """A data source could not be read."""

    code = "source_unavailable"
    status = 400


class SourceNotAllowedError(SourceError):
    """A source was rejected by policy (disabled driver, host not allowlisted)."""

    code = "source_not_allowed"
    status = 403


class InvalidUploadError(InsightEngineError):
    """The uploaded file is not something the engine can read."""

    code = "invalid_upload"
    status = 400


class DataError(InsightEngineError):
    """The data loaded fine but cannot answer the question that was asked."""

    code = "unusable_data"
    status = 422


class FormulaError(ConfigurationError):
    """A derived-metric expression is invalid or references unknown columns."""

    code = "invalid_formula"
    status = 400


class NarrativeError(InsightEngineError):
    """The narrative provider failed in a way the caller should know about."""

    code = "narrative_failed"
    status = 502


class RenderError(InsightEngineError):
    """Report rendering failed."""

    code = "render_failed"
    status = 500


class StorageError(InsightEngineError):
    """An artifact could not be written or read."""

    code = "storage_failed"
    status = 500


class NotFoundError(InsightEngineError):
    """A session, job or artifact does not exist (or has expired)."""

    code = "not_found"
    status = 404


class AuthError(InsightEngineError):
    """Credentials are missing or wrong."""

    code = "unauthorized"
    status = 401


class PayloadTooLargeError(InsightEngineError):
    """An upload exceeded the configured limit."""

    code = "payload_too_large"
    status = 413


class RateLimitedError(InsightEngineError):
    """The caller exceeded its request budget."""

    code = "rate_limited"
    status = 429

    def __init__(self, message: str, *, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
