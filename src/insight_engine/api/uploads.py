"""Upload staging.

An uploaded file is written under a server-generated id, never under the
client-supplied filename. The old code did ``tmp_dir / upload_file.filename``,
so a request could name its upload ``../../src/insight_engine/api/app.py`` and
overwrite application source.

Uploads are short-lived: they exist to be profiled and then analysed, and are
swept on the same timer as sessions and artifacts.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from insight_engine.core.errors import InvalidUploadError, PayloadTooLargeError, StorageError
from insight_engine.core.logging import get_logger

logger = get_logger("api.uploads")

UPLOAD_ID_BYTES = 12
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

#: Extensions the analysis path can actually read.
ALLOWED_SUFFIXES = frozenset({".csv", ".tsv", ".txt"})

# NUL bytes and the ZIP/OLE magic numbers cover the common case of an .xlsx
# renamed to .csv, which otherwise fails deep inside the parser with a
# meaningless error.
_BINARY_MAGIC = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"%PDF")


@dataclass(frozen=True)
class StagedUpload:
    upload_id: str
    path: Path
    original_name: str
    size_bytes: int
    created_at: datetime


def sanitize_filename(name: str | None, *, fallback: str = "upload.csv") -> str:
    """Reduce a client filename to a display label.

    The result is only ever shown back to the user; it never touches the
    filesystem.
    """
    if not name:
        return fallback
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(". ")
    return cleaned[:120] or fallback


class UploadStore:
    """Stages uploaded datasets under generated ids."""

    def __init__(self, root: Path, *, max_bytes: int, retention_seconds: int = 6 * 3600) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes
        self._retention = retention_seconds

    @property
    def root(self) -> Path:
        return self._root

    def directory_for(self, upload_id: str) -> Path:
        """Directory an upload lives in, used as the connector's base path."""
        if not _SAFE_ID.match(upload_id):
            raise StorageError("Invalid upload id", context={"upload_id": upload_id[:32]})
        path = (self._root / upload_id).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise StorageError("Invalid upload id")
        return path

    def save(self, data: bytes, *, original_name: str | None) -> StagedUpload:
        """Persist ``data`` and return its handle."""
        if len(data) > self._max_bytes:
            raise PayloadTooLargeError(
                f"The uploaded file is larger than the {self._max_bytes // (1024 * 1024)} MB limit."
            )
        if not data.strip():
            raise InvalidUploadError("The uploaded file is empty.")
        if data[:4] in _BINARY_MAGIC or b"\x00" in data[:4096]:
            raise InvalidUploadError(
                "The uploaded file is not delimited text. Export the sheet as CSV and retry."
            )

        display_name = sanitize_filename(original_name)
        suffix = Path(display_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            display_name = f"{Path(display_name).stem or 'upload'}.csv"

        upload_id = secrets.token_urlsafe(UPLOAD_ID_BYTES).replace(".", "-")
        directory = self.directory_for(upload_id)
        directory.mkdir(parents=True, exist_ok=True)

        # Always the same on-disk name: the spec references "data.csv" and the
        # client filename never reaches the filesystem.
        target = directory / "data.csv"
        target.write_bytes(data)
        target.chmod(0o600)
        (directory / "origin.txt").write_text(display_name, encoding="utf-8")

        logger.info(
            "upload staged",
            extra={"upload_id": upload_id, "bytes": len(data), "original_name": display_name},
        )
        return StagedUpload(
            upload_id=upload_id,
            path=target,
            original_name=display_name,
            size_bytes=len(data),
            created_at=datetime.now(timezone.utc),
        )

    def resolve(self, upload_id: str) -> StagedUpload | None:
        directory = self.directory_for(upload_id)
        target = directory / "data.csv"
        if not target.is_file():
            return None
        stat = target.stat()
        if (time.time() - stat.st_mtime) > self._retention:
            self.delete(upload_id)
            return None
        origin = directory / "origin.txt"
        return StagedUpload(
            upload_id=upload_id,
            path=target,
            original_name=(
                origin.read_text(encoding="utf-8").strip() if origin.is_file() else "upload.csv"
            ),
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        )

    def delete(self, upload_id: str) -> bool:
        directory = self.directory_for(upload_id)
        if not directory.is_dir():
            return False
        for child in directory.iterdir():
            child.unlink(missing_ok=True)
        directory.rmdir()
        return True

    def purge_expired(self) -> int:
        removed = 0
        cutoff = time.time() - self._retention
        for directory in self._root.iterdir():
            if not directory.is_dir():
                continue
            try:
                if directory.stat().st_mtime < cutoff:
                    self.delete(directory.name)
                    removed += 1
            except (OSError, StorageError):
                continue
        if removed:
            logger.info("purged expired uploads", extra={"count": removed})
        return removed
