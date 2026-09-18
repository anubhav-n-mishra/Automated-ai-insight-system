# Architecture

How Insight Engine is built, and why. If you are about to change something
structural, this document and [the decision records](adr/) are the context.

## Table of contents

- [The shape of the thing](#the-shape-of-the-thing)
- [Layers](#layers)
- [Request lifecycle](#request-lifecycle)
- [The analytical core](#the-analytical-core)
- [Ranking](#ranking)
- [Driver attribution](#driver-attribution)
- [Concurrency](#concurrency)
- [Security boundaries](#security-boundaries)
- [Observability](#observability)
- [Extension points](#extension-points)
- [Known limits](#known-limits)

---

## The shape of the thing

The whole system is one function with a lot of scaffolding around it:

```python
def run_analysis(spec: AnalysisSpec, *, policy: SourcePolicy) -> AnalysisResult
```

Everything else — the CLI, the HTTP API, the job queue, the deck renderer, the
dashboard — either builds a spec, calls that function, or renders its result.
Keeping that function free of I/O, frameworks and global state is what makes the
pipeline testable, embeddable in a notebook, and identical whether it runs in a
terminal or behind a load balancer.

## Layers

Dependencies point downwards only. Nothing below `api/` imports FastAPI.

```
┌──────────────────────────────────────────────────────────────────┐
│  api/            FastAPI routers, middleware, auth, rate limits  │
│                  Contains no analytics logic.                    │
├──────────────────────────────────────────────────────────────────┤
│  service.py      The one place that knows the full choreography  │
│                  The CLI and the API both call it.               │
├──────────────────────────────────────────────────────────────────┤
│  engine/         formula · aggregate · insights · pipeline       │
│                  narrative · report · voice · profiling          │
├──────────────────────────────────────────────────────────────────┤
│  connectors/     SourceSpec -> DataFrame, plus source policy     │
│  llm/            Narrative providers behind one protocol         │
│  storage/        Artifacts by opaque key                         │
│  sessions/       Shareable sessions, tokens hashed               │
│  jobs/           Bounded worker pool with pollable status        │
├──────────────────────────────────────────────────────────────────┤
│  domain/         Pure data: the spec, and the result objects     │
│  core/           Settings, logging, errors, telemetry            │
└──────────────────────────────────────────────────────────────────┘
```

### Why `service.py` exists

The previous generation inlined the whole flow into one 200-line request
handler. Two things followed from that: the CLI and the API drifted apart
because each reimplemented the sequence, and nothing could be tested without
starting a server. A single service object that both entry points call removes
both problems, at the cost of one indirection.

### Why `domain/` has no behaviour

`AnalysisSpec` and `AnalysisResult` are data with validation. They import
nothing but Pydantic. That means a test, a notebook, a worker and a request
handler can all hold the same objects, and that the serialisation contract is
visible in one file rather than reconstructed at each boundary.

The previous build had three different shapes for the same numbers — the deck
read `current_totals`, the dashboard read `change`, the engine wrote `delta_pct`
— and as a result the deck chart and the dashboard table both silently rendered
nothing. One model, one set of field names, everywhere.

## Request lifecycle

```
POST /api/v1/datasets          multipart upload
  │
  ├─ BodySizeLimitMiddleware   reject by Content-Length before buffering
  ├─ SecurityHeadersMiddleware CSP, nosniff, frame-options
  ├─ RequestContextMiddleware  correlation id, timing, metrics
  ├─ authenticate()            X-API-Key, constant-time
  ├─ rate_limited()            sliding window per principal
  │
  ├─ UploadStore.save()        server-generated id; client filename discarded
  └─ profile_dataframe()       local inference; nothing leaves the deployment
                               201 + column roles + a usable date range

POST /api/v1/reports           the form payload
  │
  ├─ spec_from_report_request() build and validate the spec — fails here, not
  │                             halfway through a report
  └─ JobManager.submit()        202 + job id + Location header
                                returns immediately

      worker thread
        ├─ ingest        connectors, policy-enforced
        ├─ aggregate     per-metric reduction, derived metrics after
        ├─ compare       full outer join, keys coalesced
        ├─ rank          impact scoring
        ├─ attribute     driver decomposition
        ├─ narrate       optional; failure is non-fatal
        └─ publish       session, QR, deck, briefing

GET /api/v1/jobs/{id}          honest stage and fraction, no-store
GET /api/v1/dashboards/{id}    token required, constant-time, 404 either way
GET /api/v1/artifacts/{key}    through a handler, never a static mount
```

## The analytical core

### Aggregation

Two rules govern `engine/aggregate.py`, and both were broken before:

**Each metric reduces with its own aggregation.** Summing an average, a ratio or
a distinct count produces a number with no meaning. `MetricSpec.aggregation`
names it; the engine never assumes.

**Derived metrics are computed after aggregation.** This is the single most
important correctness property in the system.

```
rows:        clicks  impressions   per-row ctr
             10      100           0.10
             30      200           0.15

wrong:  sum of per-row ratios         = 0.25
right:  sum(clicks) / sum(impressions) = 40 / 300 = 0.133
```

Nothing in the pipeline ever evaluates a derived metric against a raw row.

### Period comparison

`join_periods` is a full outer join with `coalesce=True` on the dimension key.
Without the coalesce, a segment present in only one period comes back with null
key columns and renders as a blank row, which is exactly what used to happen to
every new and lost segment.

Only **additive** metrics are zero-filled. Filling a ratio with `0` asserts "the
CTR was zero" when the truth is "this segment did not exist". Presence is
recorded before the fill, which is how new and lost segments are labelled.

### Undefined values

`percent_change` returns `None` when the baseline is zero. Not `100`, not `0`.
It propagates as `None` through ranking, renders as `n/a` in the deck, the
dashboard and the API, and is spoken as "changed from a zero baseline".

## Ranking

```
impact = contribution_share × priority_weight × materiality
```

**`contribution_share`** — the segment's share of that metric's total absolute
movement. A segment that moved 40 out of 100 total movement scores 0.4. This is
what makes scores comparable across metrics in different units; multiplying raw
deltas means whatever is denominated in impressions always outranks whatever is
denominated in currency.

**`priority_weight`** — position in `kpi_priority`, decaying as `1/(1+rank)`,
normalised so the first KPI scores 1.0. The user knows which metric matters.

**`materiality`** — `sqrt` of the segment's share of the current-period total.
The guard against the classic false headline: three impressions becoming nine is
+200% and means nothing. `sqrt` damps rather than eliminates, so a
small-but-real segment can still surface.

Segments below `report.min_segment_share` are dropped before ranking. Flat
segments never become insights.

## Driver attribution

Each additive metric's movement is decomposed across segments. Two denominators
matter, and conflating them is how a report ends up claiming "460% explained":

- `total_delta` — the **net** movement. Gains and losses cancel inside it.
- `gross_movement` — the sum of every segment's absolute movement.

`explained_pct` is a share of **gross**, so it always lands between 0 and 100.
When gross materially exceeds net, `offsetting` is set and every surface says so
— a net figure that hides churn underneath it is the kind of reassuring average
that gets people into trouble.

Ratio metrics are not decomposed at all. Segment deltas of a ratio do not sum to
the ratio's movement, so presenting them as drivers would be a lie dressed as
arithmetic.

## Concurrency

Report generation reads files, calls a model and renders a deck: seconds to
minutes of blocking work. The previous build did all of it inside `async def`
handlers, so one report froze every other request on the process, health checks
included.

Work now runs on a bounded `ThreadPoolExecutor` while the request thread returns
a job id. The pool is bounded deliberately: unbounded concurrency turns a
traffic spike into memory exhaustion, and the queue is the back-pressure signal.

Polars and DuckDB release the GIL for the heavy parts, so threads — rather than
processes — get real parallelism without paying to serialise DataFrames across a
process boundary. See [ADR 0003](adr/0003-in-process-job-execution.md) for why
this is single-node by design and what the seam for Celery looks like.

## Security boundaries

| Boundary | Enforced by |
| --- | --- |
| Uploaded file → filesystem | `UploadStore`: server-generated id, client filename discarded, magic-byte check, size cap |
| Spec path → filesystem | `SourcePolicy.resolve_path`, confined to the upload directory over HTTP |
| Spec → outbound network | `SourcePolicy.check_driver` / `check_host`; off by default |
| Derived metric → execution | `engine/formula.py`: AST allowlist, never `eval` |
| Session id → session data | Token required, SHA-256 stored, `compare_digest`, 404 either way |
| Artifact key → filesystem | `LocalArtifactStore`: strict key pattern, resolved-path containment |
| Exception → client | `api/errors.py`: stable code and safe message; detail to the log |

The one boundary that does **not** exist is between callers. API keys
authenticate; they do not partition. Run one deployment per trust boundary.

## Observability

**Logs** are one JSON object per line with a correlation id that survives the
hop into a worker thread via `contextvars`. Known credential key names are
redacted by the formatter.

**Metrics** are a dependency-free in-process registry rendered in the Prometheus
text format, so `/metrics` works out of the box:

| Metric | Type | Useful for |
| --- | --- | --- |
| `insight_engine_http_requests_total` | counter | Traffic and error rate by route |
| `insight_engine_http_request_seconds` | histogram | Latency |
| `insight_engine_jobs_total{outcome}` | counter | **Alert on `outcome="failed"`** |
| `insight_engine_job_seconds` | histogram | How long reports take |
| `insight_engine_jobs_queued` / `_running` | gauge | Saturation — scale on this |
| `insight_engine_stage_seconds{stage}` | histogram | Which stage is slow |
| `insight_engine_llm_request_seconds` | histogram | Provider latency |
| `insight_engine_llm_errors_total` | counter | Provider health |
| `insight_engine_llm_cache_total{outcome}` | counter | Cache effectiveness |

Route labels are templated, so per-id URLs do not explode cardinality.

**Tracing** is opt-in OpenTelemetry and degrades to no-ops when the extra is not
installed.

## Extension points

Each of these is a protocol with a registry, not a subclass hierarchy.

**A new source type**

```python
from insight_engine.connectors import register_connector

class ParquetConnector:
    source_type = "parquet"

    def load(self, name, spec, policy) -> LoadedSource:
        ...

register_connector(ParquetConnector())
```

**A new narrative provider** — implement `complete(CompletionRequest) ->
CompletionResult`. `HttpProvider` already gives you timeouts, jittered retries,
caching and metrics.

**A different artifact store** — implement the `ArtifactStore` protocol
(`put`, `get`, `open`, `delete`, `purge_expired`) and pass it to `create_app`.

**A different session store** — implement `SessionStore`. Redis is the obvious
one for multi-node.

**A different deck theme** — construct a `Theme` and pass it to `ReportService`.

## Known limits

Stated plainly, because knowing them is part of using the tool well.

- **Single node.** Jobs, rate limiting and session caching are per-process.
  Multi-node needs a shared job backend, a shared limiter at the ingress and a
  shared session store.
- **Memory-bound.** The whole dataset is materialised. Polars is efficient, but
  a 50M-row CSV will want more RAM than the default container limit. `max_rows`
  is the guard.
- **One time axis.** Comparison is two contiguous windows. No cohorting, no
  rolling windows, no year-over-year alongside week-over-week in one report.
- **Single-level segmentation.** Dimensions are crossed into one grid. There is
  no hierarchical drill-down search across dimension subsets, which is what a
  dedicated root-cause tool does. See [ROADMAP.md](ROADMAP.md).
- **No statistical significance.** A movement is ranked by size and materiality,
  not by whether it is distinguishable from noise. On small segments, treat the
  ranking as a shortlist rather than a verdict.
