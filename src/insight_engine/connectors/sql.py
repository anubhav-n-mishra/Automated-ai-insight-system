"""Relational connectors: raw queries and whole tables.

Both go through SQLAlchemy. ``connectorx`` is tried first for large reads
because it is substantially faster, but it is optional: its wheel matrix is
narrower than SQLAlchemy's, and a missing accelerator must not take the feature
down with it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus, urlparse

import polars as pl

from insight_engine.connectors.base import (
    LoadedSource,
    SourcePolicy,
    enforce_row_cap,
    require_columns,
    resolve_env_placeholders,
)
from insight_engine.core.errors import SourceError
from insight_engine.core.logging import get_logger
from insight_engine.domain.spec import DatabaseSourceSpec, SqlSourceSpec

logger = get_logger("connectors.sql")

_DEFAULT_PORTS: dict[str, int] = {"postgresql": 5432, "mysql": 3306, "mssql": 1433}
_SQLALCHEMY_DRIVERS: dict[str, str] = {
    "postgresql": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mssql": "mssql+pyodbc",
    "sqlite": "sqlite",
}


def _driver_of(url: str) -> str:
    scheme = urlparse(url).scheme.split("+", 1)[0].lower()
    return {"postgres": "postgresql"}.get(scheme, scheme)


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _read_sql(url: str, query: str, *, timeout_seconds: int) -> pl.DataFrame:
    """Execute ``query`` and return a DataFrame, preferring connectorx."""
    try:
        import connectorx as cx
    except ImportError:
        logger.debug("connectorx unavailable, using sqlalchemy")
    else:
        try:
            return pl.from_pandas(cx.read_sql(url, query))
        except Exception as error:
            logger.warning("connectorx read failed, falling back", extra={"error": str(error)})

    try:
        from sqlalchemy import create_engine, text
    except ImportError as error:
        raise SourceError(
            "Relational sources need the 'sql' extra: pip install 'insight-engine[sql]'",
            internal_detail=str(error),
        ) from error

    engine = create_engine(
        url, pool_pre_ping=True, connect_args=_connect_args(url, timeout_seconds)
    )
    try:
        with engine.connect() as connection:
            result = connection.execute(text(query))
            columns = list(result.keys())
            rows = result.fetchall()
    except Exception as error:
        raise SourceError(
            "Database query failed. Check the connection details and the query.",
            internal_detail=f"{type(error).__name__}: {error}",
        ) from error
    finally:
        engine.dispose()

    if not rows:
        return pl.DataFrame({column: [] for column in columns})
    return pl.DataFrame(
        {column: [row[index] for row in rows] for index, column in enumerate(columns)},
        strict=False,
    )


def _connect_args(url: str, timeout_seconds: int) -> dict[str, Any]:
    """Per-driver connect/statement timeouts, so a hung database cannot pin a worker."""
    driver = _driver_of(url)
    if driver == "postgresql":
        return {
            "connect_timeout": timeout_seconds,
            "options": f"-c statement_timeout={timeout_seconds * 1000}",
        }
    if driver == "mysql":
        return {"connect_timeout": timeout_seconds, "read_timeout": timeout_seconds}
    return {}


class SqlConnector:
    """Runs a caller-supplied read-only query."""

    source_type = "sql"

    def load(self, name: str, spec: object, policy: SourcePolicy) -> LoadedSource:
        if not isinstance(spec, SqlSourceSpec):
            raise SourceError(f"SqlConnector cannot load a {type(spec).__name__}")

        url = resolve_env_placeholders(spec.connection_string, what="connection_string")
        if not url:
            raise SourceError("connection_string resolved to an empty value")

        driver = _driver_of(url)
        policy.check_driver(driver)
        if driver != "sqlite":
            policy.check_host(_host_of(url))

        logger.info("loading sql source", extra={"source": name, "driver": driver})
        frame = _read_sql(url, spec.query, timeout_seconds=policy.statement_timeout_seconds)
        return _finalise(name, frame, spec, policy)


class DatabaseConnector:
    """Reads a named table, projecting only the columns the spec asks for."""

    source_type = "database"

    def load(self, name: str, spec: object, policy: SourcePolicy) -> LoadedSource:
        if not isinstance(spec, DatabaseSourceSpec):
            raise SourceError(f"DatabaseConnector cannot load a {type(spec).__name__}")

        policy.check_driver(spec.driver)
        if spec.driver != "sqlite":
            policy.check_host(spec.host)

        url = self._build_url(spec)
        query = self._build_query(spec, policy.max_rows)

        logger.info(
            "loading database source",
            extra={"source": name, "driver": spec.driver, "table": spec.table},
        )
        frame = _read_sql(url, query, timeout_seconds=policy.statement_timeout_seconds)
        return _finalise(name, frame, spec, policy)

    @staticmethod
    def _build_url(spec: DatabaseSourceSpec) -> str:
        if spec.driver == "sqlite":
            return f"sqlite:///{spec.database}"

        username = resolve_env_placeholders(spec.username, what="username")
        password = resolve_env_placeholders(spec.password, what="password")

        credentials = ""
        if username:
            credentials = quote_plus(username)
            if password:
                credentials += f":{quote_plus(password)}"
            credentials += "@"

        port = spec.port or _DEFAULT_PORTS.get(spec.driver)
        port_part = f":{port}" if port else ""
        dialect = _SQLALCHEMY_DRIVERS[spec.driver]
        return f"{dialect}://{credentials}{spec.host}{port_part}/{spec.database}"

    @staticmethod
    def _build_query(spec: DatabaseSourceSpec, max_rows: int) -> str:
        """Project the needed columns and cap rows in the database, not in Python.

        Identifiers are validated by the spec model and quoted here; nothing in
        this string comes from free-form user text.
        """
        wanted: list[str] = []
        for column in (
            spec.date_column,
            *spec.dimensions,
            *(m.source_column for m in spec.metrics),
            *spec.join_keys,
        ):
            if column not in wanted:
                wanted.append(column)

        projection = ", ".join(f'"{column}"' for column in wanted) if wanted else "*"
        table = f'"{spec.schema_name}"."{spec.table}"' if spec.schema_name else f'"{spec.table}"'
        limit = min(max_rows + 1, 10_000_000)

        # Identifiers come from DatabaseSourceSpec, which rejects anything
        # outside [A-Za-z_][A-Za-z0-9_$]*, and are quoted above; `limit` is an
        # int. No free-form user text reaches this string.
        if spec.driver == "mssql":
            return f"SELECT TOP {limit} {projection} FROM {table}"  # noqa: S608
        return f"SELECT {projection} FROM {table} LIMIT {limit}"  # noqa: S608


def _finalise(
    name: str,
    frame: pl.DataFrame,
    spec: SqlSourceSpec | DatabaseSourceSpec,
    policy: SourcePolicy,
) -> LoadedSource:
    if frame.height == 0:
        raise SourceError(f"Source {name!r} returned no rows", context={"source": name})

    required = {spec.date_column, *spec.dimensions, *(m.source_column for m in spec.metrics)}
    require_columns(frame, required, source=name)

    frame, truncated = enforce_row_cap(frame, name=name, max_rows=policy.max_rows)
    warnings = (
        [f"Source {name!r} was truncated to the first {policy.max_rows:,} rows."]
        if truncated
        else []
    )
    return LoadedSource(
        name=name, frame=frame, row_count=frame.height, truncated=truncated, warnings=warnings
    )
