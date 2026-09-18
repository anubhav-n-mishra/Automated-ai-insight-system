"""Data source connectors.

A connector turns a :class:`~insight_engine.domain.spec.SourceSpec` into a
Polars DataFrame. It owns policy enforcement (which drivers and hosts are
reachable, row caps) so the engine never has to think about where data came
from.
"""

from insight_engine.connectors.base import Connector, LoadedSource, SourcePolicy
from insight_engine.connectors.registry import get_connector, load_source, register_connector

__all__ = [
    "Connector",
    "LoadedSource",
    "SourcePolicy",
    "get_connector",
    "load_source",
    "register_connector",
]
