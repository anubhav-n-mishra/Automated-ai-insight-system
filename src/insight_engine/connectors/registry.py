"""Connector lookup.

Third parties extend the engine by calling :func:`register_connector` with a
new ``source_type``; nothing in the pipeline hard-codes the built-in three.
"""

from __future__ import annotations

from insight_engine.connectors.base import Connector, LoadedSource, SourcePolicy
from insight_engine.connectors.csv import CsvConnector
from insight_engine.connectors.sql import DatabaseConnector, SqlConnector
from insight_engine.core.errors import SourceError

_REGISTRY: dict[str, Connector] = {}


def register_connector(connector: Connector, *, replace: bool = False) -> None:
    """Register a connector under its ``source_type``."""
    if connector.source_type in _REGISTRY and not replace:
        raise ValueError(f"connector for {connector.source_type!r} is already registered")
    _REGISTRY[connector.source_type] = connector


def get_connector(source_type: str) -> Connector:
    try:
        return _REGISTRY[source_type]
    except KeyError as error:
        raise SourceError(
            f"No connector registered for source type {source_type!r}",
            context={"available": sorted(_REGISTRY)},
        ) from error


def load_source(name: str, spec: object, policy: SourcePolicy) -> LoadedSource:
    """Dispatch to the connector matching ``spec.type``."""
    source_type = getattr(spec, "type", None)
    if not isinstance(source_type, str):
        raise SourceError(f"Source {name!r} has no source type")
    return get_connector(source_type).load(name, spec, policy)


for _connector in (CsvConnector(), SqlConnector(), DatabaseConnector()):
    register_connector(_connector)
