"""Artifact store protocol.

Reports and audio are addressed by an opaque key rather than a file path, so a
deployment can move to object storage by swapping the implementation. Nothing
above this layer builds a filesystem path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StoredArtifact:
    """A stored file and how to reach it."""

    key: str
    media_type: str
    size_bytes: int
    created_at: datetime
    download_name: str
    local_path: Path | None = None


@runtime_checkable
class ArtifactStore(Protocol):
    """Stores and retrieves generated files."""

    def put(self, data: bytes, *, key: str, media_type: str, download_name: str) -> StoredArtifact:
        """Store ``data`` under ``key``."""
        ...

    def get(self, key: str) -> StoredArtifact | None:
        """Look up an artifact, or ``None`` when it is absent or expired."""
        ...

    def open(self, key: str) -> bytes | None:
        """Read an artifact's bytes."""
        ...

    def delete(self, key: str) -> bool:
        """Remove an artifact. Returns whether anything was removed."""
        ...

    def purge_expired(self) -> int:
        """Drop artifacts past their retention window. Returns the count removed."""
        ...
