"""Structured logging.

Production emits one JSON object per line so log shipping does not need a
regex; development can fall back to a readable text format. A correlation id
set by the API middleware rides along on every record for the lifetime of a
request, including from worker threads, via :class:`contextvars.ContextVar`.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("insight_engine_request_id", default=None)

# Attributes present on every LogRecord; anything else was passed via `extra`
# and is promoted to a top-level JSON field.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"asctime", "message", "taskName"}
)

_REDACT_KEYS = frozenset(
    {"api_key", "apikey", "password", "secret", "token", "authorization", "connection_string"}
)
_REDACTED = "***"


def current_request_id() -> str | None:
    """Correlation id for the in-flight request, if any."""
    return _request_id.get()


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def new_request_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def request_context(request_id: str) -> Iterator[str]:
    """Bind a correlation id for the duration of the block."""
    token = _request_id.set(request_id)
    try:
        yield request_id
    finally:
        _request_id.reset(token)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively blank out values whose key looks like a credential."""
    if _depth > 6:
        return value
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED if str(key).lower() in _REDACT_KEYS else redact(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render a record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = current_request_id()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = redact(value)
        return json.dumps(payload, default=str, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable single line, with the correlation id inline."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = current_request_id()
        return f"{base}  [req={request_id}]" if request_id else base


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own colourised handlers; route them through ours so
    # access logs and application logs share a format and a correlation id.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Module logger, namespaced under the package."""
    if not name.startswith("insight_engine"):
        name = f"insight_engine.{name}"
    return logging.getLogger(name)
