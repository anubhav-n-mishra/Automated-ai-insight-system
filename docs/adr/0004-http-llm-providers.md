# 0004. Narrative providers speak HTTP, not vendor SDKs

Status: Accepted
Date: 2026-09-18

## Context

The prototype depended on `google-generativeai` and `openai` in its base
install, imported both with try/except fallbacks, and selected a provider by
inspecting the API key's prefix:

```python
gemini_key = api_key if api_key and api_key.startswith("AIza") else ...
openai_key = api_key if api_key and api_key.startswith("sk-") else ...
```

Three problems. The base install carried two SDKs for a feature that is
optional. SDK releases changed public APIs underneath the code more than once.
And the prefix check silently failed for Azure keys, gateway keys, or anything
self-hosted.

The requests themselves are one POST with a JSON body.

## Decision

Providers call the REST APIs directly through `httpx`. No vendor SDK in any
install path. A shared `HttpProvider` base owns timeouts, bounded retries with
full jitter, `Retry-After` handling, response caching and metrics.

The OpenAI provider is written against the Chat Completions shape rather than
against OpenAI specifically, so Azure OpenAI, vLLM, Ollama, LiteLLM and
OpenRouter work by changing a base URL.

## Consequences

Good:

- Two fewer dependencies, and no SDK version churn to track.
- Explicit control over timeouts and retries. A hung provider cannot pin a
  worker, and jittered backoff stops a pool of workers synchronising into a
  thundering herd against a provider that is already rate-limiting.
- Self-hosting is a configuration change, which matters for deployments that
  cannot send business metrics to a third party.
- Providers are testable against `httpx.MockTransport` with no network and no
  credentials.
- The Gemini key travels in a header rather than a query string, so it does not
  land in every proxy access log on the path.

Costs:

- Provider-specific features (streaming, function calling, structured-output
  schemas beyond JSON mode) would have to be implemented rather than inherited.
  None are needed: one JSON object comes back.
- A breaking change to a provider's REST API is ours to fix. Both APIs here are
  stable and versioned.

## Alternatives considered

**Keep the SDKs as optional extras.** Better than the base install, but still
two code paths to maintain for what is one POST.

**LiteLLM as a universal client.** A real option, and it would cover more
providers. It is also a substantial dependency whose whole job is the thing
being avoided. Anyone who wants it can point `OPENAI_BASE_URL` at a LiteLLM
proxy and get every provider it supports, without this project depending on it.
