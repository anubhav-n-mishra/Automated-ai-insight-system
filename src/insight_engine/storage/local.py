"""Filesystem-backed artifact store."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from insight_engine.core.errors import StorageError
from insight_engine.core.logging import get_logger
from insight_engine.storage.base import StoredArtifact

logger = get_logger("storage.local")

#: Keys are generated internally, but this is enforced anyway: a key is the last
#: thing between a URL path segment and ``open()``, and "../" in a key would be
#: a directory traversal with the same consequences as one in a filename.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_EXTENSIONS = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "audio/mpeg": ".mp3",
    "image/png": ".png",
    "application/json": ".json",
}


class LocalArtifactStore:
    """Writes artifacts under a root directory with a retention window."""

    def __init__(self, root: Path, *, retention_seconds: int = 7 * 24 * 3600) -> None:
        self._root = root
        self._retention = retention_seconds
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise StorageError("Invalid artifact key", context={"key": key[:64]})
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise StorageError("Artifact key escapes the storage root", context={"key": key[:64]})
        return path

    def put(self, data: bytes, *, key: str, media_type: str, download_name: str) -> StoredArtifact:
        path = self._path_for(key)
        # Write to a sibling temp file and rename, so a reader can never observe
        # a partially written report.
        staging = path.with_name(f".{path.name}.partial")
        try:
            staging.write_bytes(data)
            staging.replace(path)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise StorageError(
                "Could not write the generated artifact",
                internal_detail=str(error),
                context={"key": key},
            ) from error

        return StoredArtifact(
            key=key,
            media_type=media_type,
            size_bytes=len(data),
            created_at=datetime.now(timezone.utc),
            download_name=download_name,
            local_path=path,
        )

    def get(self, key: str) -> StoredArtifact | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        stat = path.stat()
        if self._is_expired(stat.st_mtime):
            self.delete(key)
            return None
        return StoredArtifact(
            key=key,
            media_type=_media_type_for(path),
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            download_name=path.name,
            local_path=path,
        )

    def open(self, key: str) -> bytes | None:
        artifact = self.get(key)
        if artifact is None or artifact.local_path is None:
            return None
        try:
            return artifact.local_path.read_bytes()
        except OSError as error:
            raise StorageError(
                "Could not read the stored artifact", internal_detail=str(error)
            ) from error

    def delete(self, key: str) -> bool:
        path = self._path_for(key)
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def purge_expired(self) -> int:
        removed = 0
        for path in self._root.iterdir():
            if not path.is_file():
                continue
            try:
                if self._is_expired(path.stat().st_mtime):
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        if removed:
            logger.info("purged expired artifacts", extra={"count": removed})
        return removed

    def _is_expired(self, mtime: float) -> bool:
        return (time.time() - mtime) > self._retention


def extension_for(media_type: str) -> str:
    return _EXTENSIONS.get(media_type, ".bin")


def _media_type_for(path: Path) -> str:
    for media_type, extension in _EXTENSIONS.items():
        if path.suffix == extension:
            return media_type
    return "application/octet-stream"
