"""Artifact storage."""

from insight_engine.storage.base import ArtifactStore, StoredArtifact
from insight_engine.storage.local import LocalArtifactStore

__all__ = ["ArtifactStore", "LocalArtifactStore", "StoredArtifact"]
