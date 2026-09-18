"""OpenAI-compatible Chat Completions provider.

Deliberately generic: the same class serves api.openai.com, Azure OpenAI,
vLLM, Ollama, LiteLLM and OpenRouter, because they all speak
``POST /v1/chat/completions``. Pointing the engine at a self-hosted model is a
base-URL change, which matters for deployments that cannot send business
metrics to a third party.
"""

from __future__ import annotations

import time
from typing import Any

from insight_engine.llm.base import (
    CompletionRequest,
    CompletionResult,
    HttpProvider,
    ProviderUnavailableError,
)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider(HttpProvider):
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not api_key:
            raise ProviderUnavailableError(
                "No API key configured for the OpenAI-compatible provider"
            )
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, request: CompletionRequest) -> CompletionResult:
        cached = self._cached(request)
        if cached is not None:
            return cached

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.json_only:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        data = self._post_json(
            f"{self._base_url}/chat/completions",
            payload,
            {"authorization": f"Bearer {self._api_key}", "content-type": "application/json"},
        )

        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderUnavailableError(
                "The provider returned an unexpected response shape",
                internal_detail=str(data)[:500],
            ) from error

        result = CompletionResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=data.get("usage", {}) or {},
        )
        self._store(request, result)
        return result
