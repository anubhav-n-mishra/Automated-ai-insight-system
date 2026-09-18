"""Provider protocol, retry policy and a response cache."""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from insight_engine.core.errors import NarrativeError
from insight_engine.core.logging import get_logger
from insight_engine.core.telemetry import METRICS

logger = get_logger("llm")

# Status codes worth trying again: rate limits and transient server faults.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class ProviderUnavailableError(NarrativeError):
    """The provider is not configured (no key, no endpoint, missing extra)."""

    code = "provider_unavailable"
    status = 503


@dataclass(frozen=True)
class CompletionRequest:
    """A single structured-output request."""

    system: str
    user: str
    max_output_tokens: int = 1200
    temperature: float = 0.2
    json_only: bool = True


@dataclass
class CompletionResult:
    """Raw provider output plus what it cost."""

    text: str
    provider: str
    model: str
    latency_ms: int = 0
    cached: bool = False
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class NarrativeProvider(Protocol):
    """Turns a prompt into text."""

    name: str
    model: str

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one completion. Raises :class:`NarrativeError` on failure."""
        ...


class ResponseCache:
    """Bounded, thread-safe LRU over prompt hashes.

    Regenerating the same report — a retried job, a refreshed dashboard, a demo
    run in a loop — should not spend money or latency twice.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._entries: OrderedDict[str, CompletionResult] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    @staticmethod
    def key(provider: str, model: str, request: CompletionRequest) -> str:
        material = json.dumps(
            {
                "provider": provider,
                "model": model,
                "system": request.system,
                "user": request.user,
                "temperature": request.temperature,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> CompletionResult | None:
        if self._max <= 0:
            return None
        with self._lock:
            result = self._entries.get(key)
            if result is None:
                return None
            self._entries.move_to_end(key)
            METRICS.counter("insight_engine_llm_cache_total", labels={"outcome": "hit"})
            return CompletionResult(**{**result.__dict__, "cached": True})

    def put(self, key: str, value: CompletionResult) -> None:
        if self._max <= 0:
            return
        with self._lock:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
        METRICS.counter("insight_engine_llm_cache_total", labels={"outcome": "miss"})

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class HttpProvider:
    """Shared HTTP behaviour: timeouts, bounded retries, metrics.

    Retries use exponential backoff with full jitter. Unjittered retries from a
    fleet of workers synchronise into a thundering herd against a provider that
    is already rate-limiting.
    """

    name: str = "http"
    model: str = ""

    def __init__(
        self,
        *,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
        cache: ResponseCache | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._cache = cache
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
                follow_redirects=False,
                headers={"user-agent": "insight-engine"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                response = self._http().post(url, json=payload, headers=headers)
            except httpx.TimeoutException as error:
                last_error = error
                logger.warning(
                    "llm request timed out", extra={"provider": self.name, "attempt": attempt}
                )
            except httpx.HTTPError as error:
                last_error = error
                logger.warning(
                    "llm transport error",
                    extra={"provider": self.name, "attempt": attempt, "error": str(error)},
                )
            else:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                METRICS.observe(
                    "insight_engine_llm_request_seconds",
                    elapsed_ms / 1000.0,
                    {"provider": self.name},
                )
                if response.status_code < 400:
                    data: dict[str, Any] = response.json()
                    return data
                if response.status_code in RETRYABLE_STATUS and attempt < self._max_retries:
                    last_error = NarrativeError(f"provider returned {response.status_code}")
                    self._sleep_before_retry(attempt, response)
                    continue

                METRICS.counter(
                    "insight_engine_llm_errors_total",
                    labels={"provider": self.name, "status": str(response.status_code)},
                )
                raise NarrativeError(
                    f"The {self.name} narrative provider rejected the request "
                    f"(HTTP {response.status_code}).",
                    internal_detail=response.text[:500],
                    context={"status": response.status_code},
                )

            if attempt < self._max_retries:
                self._sleep_before_retry(attempt, None)

        METRICS.counter(
            "insight_engine_llm_errors_total", labels={"provider": self.name, "status": "transport"}
        )
        raise NarrativeError(
            f"The {self.name} narrative provider is unreachable.",
            internal_detail=str(last_error),
        ) from last_error

    def _sleep_before_retry(self, attempt: int, response: httpx.Response | None) -> None:
        if response is not None:
            header = response.headers.get("retry-after")
            if header:
                try:
                    time.sleep(min(float(header), 30.0))
                    return
                except ValueError:
                    pass
        backoff = min(2.0**attempt, 8.0)
        time.sleep(random.uniform(0, backoff))  # noqa: S311 - jitter, not cryptography

    def _cached(self, request: CompletionRequest) -> CompletionResult | None:
        if self._cache is None:
            return None
        return self._cache.get(ResponseCache.key(self.name, self.model, request))

    def _store(self, request: CompletionRequest, result: CompletionResult) -> None:
        if self._cache is not None:
            self._cache.put(ResponseCache.key(self.name, self.model, request), result)
