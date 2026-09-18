"""Narrative providers.

Providers speak HTTP directly through :mod:`httpx` rather than vendor SDKs.
That keeps the base install small, gives explicit control over timeouts and
retries, and means any OpenAI-compatible gateway — Azure OpenAI, vLLM, Ollama,
LiteLLM, OpenRouter — works by changing a base URL instead of a code path.
"""

from insight_engine.llm.base import (
    CompletionRequest,
    CompletionResult,
    NarrativeProvider,
    ProviderUnavailableError,
)
from insight_engine.llm.registry import available_providers, build_provider

__all__ = [
    "CompletionRequest",
    "CompletionResult",
    "NarrativeProvider",
    "ProviderUnavailableError",
    "available_providers",
    "build_provider",
]
