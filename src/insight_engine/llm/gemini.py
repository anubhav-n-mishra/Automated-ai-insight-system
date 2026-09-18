"""Google Gemini provider over the Generative Language REST API."""

from __future__ import annotations

import time
from typing import Any

from insight_engine.llm.base import (
    CompletionRequest,
    CompletionResult,
    HttpProvider,
    ProviderUnavailableError,
)

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(HttpProvider):
    name = "gemini"

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
            raise ProviderUnavailableError("No API key configured for the Gemini provider")
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")

    def complete(self, request: CompletionRequest) -> CompletionResult:
        cached = self._cached(request)
        if cached is not None:
            return cached

        generation_config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.json_only:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": generation_config,
        }

        started = time.perf_counter()
        # The key rides in a header rather than the query string so it does not
        # end up in proxy access logs.
        data = self._post_json(
            f"{self._base_url}/models/{self.model}:generateContent",
            payload,
            {"x-goog-api-key": self._api_key, "content-type": "application/json"},
        )

        text = _extract_text(data)
        if text is None:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderUnavailableError(
                "Gemini returned no usable content" + (f" (blocked: {reason})" if reason else ""),
                internal_detail=str(data)[:500],
            )

        result = CompletionResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=data.get("usageMetadata", {}) or {},
        )
        self._store(request, result)
        return result


def _extract_text(data: dict[str, Any]) -> str | None:
    """Concatenate the text parts of the first candidate, if any."""
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    chunks = [part["text"] for part in parts if isinstance(part, dict) and "text" in part]
    joined = "".join(chunks).strip()
    return joined or None
