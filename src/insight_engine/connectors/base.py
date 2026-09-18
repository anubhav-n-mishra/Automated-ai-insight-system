"""Connector protocol and the policy every connector is handed."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import polars as pl

from insight_engine.core.errors import SourceError, SourceNotAllowedError

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class SourcePolicy:
    """What a connector is permitted to do.

    Built from :class:`~insight_engine.core.config.Settings` at the edge and
    passed down explicitly, so a library caller can tighten or relax it without
    environment variables and tests need no monkeypatching.
    """

    allow_remote_sql: bool = False
    allowed_drivers: frozenset[str] = frozenset({"postgresql", "mysql", "sqlite", "mssql"})
    allowed_hosts: frozenset[str] = frozenset()
    max_rows: int = 5_000_000
    statement_timeout_seconds: int = 60
    #: Directory relative source paths resolve against.
    base_path: Path | None = None

    #: Whether a resolved path must stay inside ``base_path``.
    #:
    #: The HTTP layer sets this, because there the spec is attacker-controlled
    #: and ``../../etc/passwd`` must not resolve. The CLI does not: an operator
    #: running a spec from their own shell already has the file permissions the
    #: process runs with, and forbidding ``../data/x.csv`` would only push them
    #: into writing brittle absolute paths.
    confine_to_base: bool = True

    def resolve_path(self, raw: str) -> Path:
        """Resolve a source path against ``base_path``, honouring confinement."""
        candidate = Path(raw).expanduser()
        if self.base_path is None:
            return candidate.resolve()

        root = self.base_path.resolve()
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if self.confine_to_base and not resolved.is_relative_to(root):
            raise SourceNotAllowedError(
                "Data file path escapes the permitted directory",
                internal_detail=f"{resolved} is outside {root}",
                context={"path": raw},
            )
        return resolved

    def check_driver(self, driver: str) -> None:
        if driver not in self.allowed_drivers:
            raise SourceNotAllowedError(
                f"Database driver {driver!r} is not enabled on this deployment",
                context={"driver": driver, "allowed": sorted(self.allowed_drivers)},
            )

    def check_host(self, host: str | None) -> None:
        """Enforce the host allowlist.

        An engine that connects wherever a request tells it to is a
        server-side request forgery primitive against every service the
        container can reach, so remote sources are off unless an operator opts
        in and names the hosts.
        """
        if not self.allow_remote_sql:
            raise SourceNotAllowedError(
                "Remote SQL sources are disabled. Set INSIGHT_ENGINE_ALLOW_REMOTE_SQL=true "
                "and list the hosts in INSIGHT_ENGINE_ALLOWED_SQL_HOSTS to enable them."
            )
        if not self.allowed_hosts:
            return
        if host is None or host.lower() not in {h.lower() for h in self.allowed_hosts}:
            raise SourceNotAllowedError(
                f"Database host {host!r} is not in the allowlist",
                context={"allowed": sorted(self.allowed_hosts)},
            )


@dataclass
class LoadedSource:
    """A source's data plus what it cost to read."""

    name: str
    frame: pl.DataFrame
    row_count: int
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class Connector(Protocol):
    """Loads one source type."""

    source_type: str

    def load(self, name: str, spec: object, policy: SourcePolicy) -> LoadedSource:
        """Read the source described by ``spec``."""
        ...


def resolve_env_placeholders(value: str | None, *, what: str = "value") -> str | None:
    """Expand ``${VAR}`` against the environment.

    Keeping credentials out of the spec file is the whole point, so an
    unresolved placeholder is an error rather than an empty string quietly
    producing an anonymous connection attempt.
    """
    if value is None:
        return None

    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = os.environ.get(name)
        if resolved is None:
            missing.append(name)
            return ""
        return resolved

    expanded = _ENV_PLACEHOLDER.sub(replace, value)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise SourceError(
            f"Unset environment variable(s) referenced by {what}: {names}",
            context={"missing": sorted(set(missing))},
        )
    return expanded


def enforce_row_cap(frame: pl.DataFrame, *, name: str, max_rows: int) -> tuple[pl.DataFrame, bool]:
    """Truncate to ``max_rows``, reporting whether anything was dropped."""
    if frame.height <= max_rows:
        return frame, False
    return frame.head(max_rows), True


def require_columns(frame: pl.DataFrame, required: set[str], *, source: str) -> None:
    """Fail with the actual column list rather than a bare KeyError."""
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SourceError(
            f"Source {source!r} is missing required column(s): {', '.join(missing)}",
            context={"missing": missing, "available": frame.columns[:50]},
        )
