"""Authentication and rate limiting."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

from insight_engine.core.config import Settings
from insight_engine.core.errors import AuthError, RateLimitedError

API_KEY_HEADER = "X-API-Key"


@dataclass(frozen=True)
class Principal:
    """Who is making a request.

    ``key_id`` is a short fingerprint of the API key, never the key itself: it
    is safe to log, attach to jobs and use as a rate-limit bucket.
    """

    key_id: str
    authenticated: bool

    @property
    def is_anonymous(self) -> bool:
        return not self.authenticated


ANONYMOUS = Principal(key_id="anonymous", authenticated=False)


def fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def authenticate(settings: Settings, presented: str | None) -> Principal:
    """Resolve an API key to a principal.

    Every configured key is compared in constant time and the loop always runs
    to completion, so response timing does not reveal how many keys exist or how
    close a guess was.
    """
    if not settings.api_keys:
        return ANONYMOUS

    if not presented:
        raise AuthError(f"Missing {API_KEY_HEADER} header")

    matched: str | None = None
    for configured in settings.api_keys:
        if secrets.compare_digest(configured.get_secret_value(), presented):
            matched = presented
    if matched is None:
        raise AuthError("Invalid API key")
    return Principal(key_id=fingerprint(matched), authenticated=True)


class SlidingWindowRateLimiter:
    """Per-principal sliding-window limiter.

    In-process, so it bounds a single node. A multi-node deployment puts a
    shared limiter at the ingress; this one exists so that a default
    single-container install is not trivially exhaustible. Report generation is
    expensive and can bill an LLM provider, which is exactly the shape of
    endpoint that needs a ceiling.
    """

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def check(self, key: str) -> None:
        """Record a hit, raising when the caller is over budget."""
        if not self.enabled:
            return

        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                retry_after = max(1, int(bucket[0] + self._window - now) + 1)
                raise RateLimitedError(
                    f"Rate limit exceeded: {self._limit} requests per {self._window} seconds",
                    retry_after=retry_after,
                )
            bucket.append(now)

            # Opportunistic sweep so idle buckets do not accumulate for callers
            # that never return.
            if len(self._hits) > 4096:
                for bucket_key in [k for k, v in self._hits.items() if not v or v[-1] < cutoff]:
                    del self._hits[bucket_key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
