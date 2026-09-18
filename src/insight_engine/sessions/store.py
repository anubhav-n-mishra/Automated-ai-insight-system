"""Session persistence.

:class:`FileSessionStore` is the default: durable across restarts, no external
service, adequate for a single node. :class:`SessionStore` is the protocol a
Redis or Postgres implementation satisfies for multi-node deployments.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from insight_engine.core.errors import StorageError
from insight_engine.core.logging import get_logger
from insight_engine.sessions.models import DashboardSession

logger = get_logger("sessions.store")

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@runtime_checkable
class SessionStore(Protocol):
    def save(self, session: DashboardSession) -> None: ...

    def load(self, session_id: str) -> DashboardSession | None: ...

    def delete(self, session_id: str) -> bool: ...

    def purge_expired(self) -> int: ...


class FileSessionStore:
    """One JSON document per session, under a directory that is never served.

    A small in-process cache keeps repeat dashboard polls off the disk. It is
    bounded so a long-lived process cannot accumulate every session it has ever
    seen — the previous implementation's cache grew without limit.
    """

    def __init__(self, directory: Path, *, cache_size: int = 256) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, DashboardSession] = {}
        self._cache_size = cache_size
        self._lock = threading.RLock()

    def _path_for(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise StorageError("Invalid session id", context={"session_id": session_id[:32]})
        return self._directory / f"{session_id}.json"

    def save(self, session: DashboardSession) -> None:
        path = self._path_for(session.session_id)
        staging = path.with_suffix(".json.partial")
        try:
            staging.write_text(json.dumps(session.to_record(), default=str), encoding="utf-8")
            staging.replace(path)
            # Tokens live in here in hashed form; still, no reason for the file
            # to be group- or world-readable.
            path.chmod(0o600)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise StorageError(
                "Could not persist the dashboard session", internal_detail=str(error)
            ) from error

        with self._lock:
            self._remember(session)

    def load(self, session_id: str) -> DashboardSession | None:
        with self._lock:
            cached = self._cache.get(session_id)
        if cached is not None:
            if cached.is_expired():
                self.delete(session_id)
                return None
            return cached

        try:
            path = self._path_for(session_id)
        except StorageError:
            return None
        if not path.is_file():
            return None

        try:
            session = DashboardSession.from_record(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
            logger.warning(
                "discarding unreadable session file",
                extra={"session_id": session_id, "error": str(error)},
            )
            path.unlink(missing_ok=True)
            return None

        if session.is_expired():
            self.delete(session_id)
            return None

        with self._lock:
            self._remember(session)
        return session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            self._cache.pop(session_id, None)
        try:
            path = self._path_for(session_id)
        except StorageError:
            return False
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        removed = 0
        for path in self._directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(record["expires_at"])
            except (OSError, json.JSONDecodeError, KeyError, ValueError):
                path.unlink(missing_ok=True)
                removed += 1
                continue
            if now >= expires_at:
                self.delete(record.get("session_id", path.stem))
                removed += 1
        if removed:
            logger.info("purged expired sessions", extra={"count": removed})
        return removed

    def _remember(self, session: DashboardSession) -> None:
        self._cache[session.session_id] = session
        while len(self._cache) > self._cache_size:
            self._cache.pop(next(iter(self._cache)))
