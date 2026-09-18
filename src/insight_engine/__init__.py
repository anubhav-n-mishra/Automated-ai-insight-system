"""Insight Engine: config-driven, explainable analytics reporting.

The package is organised in layers that only depend downwards:

``domain``
    Declarative specification of *what* to analyse (pure data, no I/O).
``connectors``
    Load a :class:`~insight_engine.domain.spec.SourceSpec` into a DataFrame.
``engine``
    Metric derivation, period comparison, driver attribution, ranking and
    rendering (deck, dashboard payload, audio briefing).
``llm`` / ``storage`` / ``sessions`` / ``jobs``
    Swappable infrastructure behind narrow protocols.
``api``
    Transport. Contains no analytics logic.

Nothing below ``api`` imports FastAPI, so the whole pipeline is usable as a
library and from the ``insight-engine`` CLI without an HTTP server.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
