"""Session records.

The access token is **never stored**. A SHA-256 hash of it is, and comparison
is constant-time. The previous implementation wrote the plaintext token into a
JSON file *and* served that directory as static assets, so anyone could list
``/tmp/sessions/`` and read every live token in the deployment.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

TOKEN_BYTES = 32
SESSION_ID_BYTES = 16


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SessionSecret:
    """A freshly minted session id and its plaintext token.

    Returned exactly once, at creation. The token exists in the share link and
    nowhere else.
    """

    session_id: str
    token: str

    @classmethod
    def mint(cls) -> SessionSecret:
        return cls(
            session_id=secrets.token_urlsafe(SESSION_ID_BYTES),
            token=secrets.token_urlsafe(TOKEN_BYTES),
        )


@dataclass
class DashboardSession:
    """A stored, expiring snapshot of one analysis."""

    session_id: str
    token_hash: str
    created_at: datetime
    expires_at: datetime
    title: str
    payload: dict[str, Any]
    briefing: dict[str, Any] | None = None
    report_key: str | None = None
    audio_key: str | None = None
    view_count: int = 0

    @classmethod
    def create(
        cls,
        secret: SessionSecret,
        *,
        title: str,
        payload: dict[str, Any],
        ttl_hours: int = 72,
        briefing: dict[str, Any] | None = None,
        report_key: str | None = None,
        audio_key: str | None = None,
    ) -> DashboardSession:
        now = datetime.now(timezone.utc)
        return cls(
            session_id=secret.session_id,
            token_hash=hash_token(secret.token),
            created_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
            title=title,
            payload=payload,
            briefing=briefing,
            report_key=report_key,
            audio_key=audio_key,
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def verify(self, token: str | None) -> bool:
        """Constant-time token check.

        A plain ``!=`` leaks the length of the shared prefix through timing. The
        cost of getting this right is one function call.
        """
        if not token:
            return False
        return secrets.compare_digest(self.token_hash, hash_token(token))

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "token_hash": self.token_hash,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "title": self.title,
            "payload": self.payload,
            "briefing": self.briefing,
            "report_key": self.report_key,
            "audio_key": self.audio_key,
            "view_count": self.view_count,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> DashboardSession:
        return cls(
            session_id=record["session_id"],
            token_hash=record["token_hash"],
            created_at=datetime.fromisoformat(record["created_at"]),
            expires_at=datetime.fromisoformat(record["expires_at"]),
            title=record.get("title", "Insight report"),
            payload=record.get("payload", {}),
            briefing=record.get("briefing"),
            report_key=record.get("report_key"),
            audio_key=record.get("audio_key"),
            view_count=int(record.get("view_count", 0)),
        )
