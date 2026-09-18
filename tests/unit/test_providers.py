"""LLM providers speak HTTP, so they are tested against a mock transport."""

from __future__ import annotations

import httpx
import pytest

from insight_engine.core.config import Settings
from insight_engine.core.errors import NarrativeError
from insight_engine.llm.base import CompletionRequest, ProviderUnavailableError
from insight_engine.llm.gemini import GeminiProvider
from insight_engine.llm.openai_compatible import OpenAICompatibleProvider
from insight_engine.llm.registry import available_providers, build_provider, reset_cache

REQUEST = CompletionRequest(system="system", user="user")


def client_returning(*responses: httpx.Response) -> httpx.Client:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        response.request = request
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestOpenAICompatible:
    def test_extracts_the_message_content(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="k",
            client=client_returning(
                httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
            ),
        )
        assert provider.complete(REQUEST).text == "hello"

    def test_a_key_is_required(self) -> None:
        with pytest.raises(ProviderUnavailableError):
            OpenAICompatibleProvider(api_key="")

    def test_a_client_error_is_surfaced_without_the_body(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="k",
            max_retries=0,
            client=client_returning(httpx.Response(401, text="secret internal detail")),
        )
        with pytest.raises(NarrativeError) as error:
            provider.complete(REQUEST)
        assert "secret internal detail" not in error.value.message

    def test_a_retryable_status_is_retried_then_fails(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="k",
            max_retries=1,
            client=client_returning(
                httpx.Response(503, text="busy"), httpx.Response(503, text="busy")
            ),
        )
        with pytest.raises(NarrativeError):
            provider.complete(REQUEST)

    def test_an_unexpected_shape_is_reported_clearly(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="k", client=client_returning(httpx.Response(200, json={"unexpected": 1}))
        )
        with pytest.raises(ProviderUnavailableError, match="unexpected response shape"):
            provider.complete(REQUEST)

    def test_base_url_is_configurable_for_self_hosted_gateways(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        provider = OpenAICompatibleProvider(
            api_key="k",
            base_url="http://vllm.internal:8000/v1",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        provider.complete(REQUEST)
        assert captured["url"].startswith("http://vllm.internal:8000/v1/chat/completions")


class TestGemini:
    def test_joins_the_candidate_text_parts(self) -> None:
        provider = GeminiProvider(
            api_key="k",
            client=client_returning(
                httpx.Response(
                    200,
                    json={"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]},
                )
            ),
        )
        assert provider.complete(REQUEST).text == "ab"

    def test_a_blocked_response_is_reported(self) -> None:
        provider = GeminiProvider(
            api_key="k",
            client=client_returning(
                httpx.Response(
                    200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
                )
            ),
        )
        with pytest.raises(ProviderUnavailableError, match="SAFETY"):
            provider.complete(REQUEST)

    def test_the_api_key_travels_in_a_header_not_the_query_string(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["header"] = request.headers.get("x-goog-api-key")
            return httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
            )

        provider = GeminiProvider(
            api_key="super-secret", client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        provider.complete(REQUEST)
        # A key in a query string ends up in every proxy access log on the path.
        assert "super-secret" not in str(captured["url"])
        assert captured["header"] == "super-secret"


class TestRegistry:
    def test_no_configuration_means_no_provider(self) -> None:
        reset_cache()
        assert build_provider(Settings(llm_provider="auto")) is None

    def test_explicit_none_disables_narration(self) -> None:
        reset_cache()
        assert build_provider(Settings(llm_provider="none", gemini_api_key="k")) is None

    def test_auto_prefers_a_configured_provider(self) -> None:
        reset_cache()
        settings = Settings(llm_provider="auto", gemini_api_key="k")
        assert available_providers(settings) == ["gemini"]
        provider = build_provider(settings)
        assert provider is not None
        assert provider.name == "gemini"

    def test_model_override_is_honoured(self) -> None:
        reset_cache()
        provider = build_provider(
            Settings(llm_provider="openai", openai_api_key="k", llm_model="my-model")
        )
        assert provider is not None
        assert provider.model == "my-model"
