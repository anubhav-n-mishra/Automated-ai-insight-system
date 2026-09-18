"""Provider selection."""

from __future__ import annotations

from insight_engine.core.config import Settings
from insight_engine.core.logging import get_logger
from insight_engine.llm.base import NarrativeProvider, ResponseCache
from insight_engine.llm.gemini import DEFAULT_MODEL as GEMINI_DEFAULT
from insight_engine.llm.gemini import GeminiProvider
from insight_engine.llm.openai_compatible import DEFAULT_BASE_URL as OPENAI_BASE_URL
from insight_engine.llm.openai_compatible import DEFAULT_MODEL as OPENAI_DEFAULT
from insight_engine.llm.openai_compatible import OpenAICompatibleProvider

logger = get_logger("llm.registry")

_CACHE: ResponseCache | None = None


def _cache_for(settings: Settings) -> ResponseCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = ResponseCache(max_entries=settings.llm_cache_size)
    return _CACHE


def available_providers(settings: Settings) -> list[str]:
    """Providers that have enough configuration to be used."""
    providers: list[str] = []
    if settings.gemini_api_key:
        providers.append("gemini")
    if settings.openai_api_key:
        providers.append("openai")
    return providers


def build_provider(settings: Settings) -> NarrativeProvider | None:
    """Construct the configured provider, or ``None`` for template narration.

    Returning ``None`` rather than raising is deliberate: a missing API key
    should downgrade the prose, never fail the report. The numbers are the
    product.
    """
    if settings.llm_provider == "none":
        return None

    choice: str = settings.llm_provider
    if choice == "auto":
        candidates = available_providers(settings)
        if not candidates:
            logger.info("no narrative provider configured; using the template writer")
            return None
        choice = candidates[0]

    cache = _cache_for(settings)
    timeout = settings.llm_timeout_seconds
    retries = settings.llm_max_retries

    if choice == "gemini":
        if not settings.gemini_api_key:
            logger.warning("gemini selected but INSIGHT_ENGINE_GEMINI_API_KEY is unset")
            return None
        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.llm_model or GEMINI_DEFAULT,
            timeout_seconds=timeout,
            max_retries=retries,
            cache=cache,
        )

    if choice == "openai":
        if not settings.openai_api_key:
            logger.warning("openai selected but INSIGHT_ENGINE_OPENAI_API_KEY is unset")
            return None
        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.llm_model or OPENAI_DEFAULT,
            base_url=settings.openai_base_url or OPENAI_BASE_URL,
            timeout_seconds=timeout,
            max_retries=retries,
            cache=cache,
        )

    logger.warning("unknown narrative provider", extra={"provider": choice})
    return None


def reset_cache() -> None:
    """Drop the shared response cache (tests, and after a config reload)."""
    global _CACHE
    if _CACHE is not None:
        _CACHE.clear()
    _CACHE = None
