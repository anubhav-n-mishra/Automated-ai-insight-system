# 0003. In-process job execution, not a broker

Status: Accepted
Date: 2026-09-18

## Context

Report generation reads files, may call a model provider, and renders a deck:
seconds to minutes of blocking work.

The prototype ran all of it inside `async def` handlers. That blocks the event
loop, so a single report froze every concurrent request on the process —
including the health check, which meant an orchestrator would restart a pod that
was merely busy.

Whatever replaced it had to decouple the request from the work. The question was
how much infrastructure to require.

## Decision

A bounded `ThreadPoolExecutor` inside the API process. Requests return `202`
with a job id; clients poll `GET /api/v1/jobs/{id}`.

The `JobManager` interface is the seam a Celery, RQ or ARQ backend implements
later. Nothing above it knows how work is executed.

## Consequences

Good:

- `pip install insight-engine && insight-engine serve` is the entire setup. No
  Redis, no broker, no worker deployment, no second image.
- Polars and DuckDB release the GIL for the heavy parts, so threads get real
  parallelism without paying to serialise DataFrames across a process boundary.
- The pool is bounded, so a traffic spike queues instead of exhausting memory.
  The queue depth is the back-pressure signal, and it is exported as a metric.

Costs, stated plainly:

- **Single node.** Job state, the rate limiter and the session cache are
  per-process. Two replicas means a client polls a job the other pod is running,
  and a dashboard link 404s on the pod that did not create it.
- **Jobs do not survive a restart.** A deploy during a long report loses it. The
  manager drains on shutdown, which is why the Kubernetes manifest sets a
  60-second grace period.
- **Memory is shared.** `WORKER_THREADS × per-report memory` must fit in the
  container limit.

[DEPLOYMENT.md](../DEPLOYMENT.md#scaling-out) documents the workarounds, and
[ROADMAP.md](../ROADMAP.md) lists Redis-backed stores as the unlock.

## Alternatives considered

**Celery with Redis.** Durable, distributed, well understood — and it turns a
one-command install into a four-service deployment. For the common case of one
team running weekly reports, that is a large tax on a problem they do not have.
The seam is in place for when they do.

**FastAPI `BackgroundTasks`.** Simpler, but runs after the response in the same
event loop, with no status, no cancellation and no bound. It would have moved
the blocking rather than removed it.

**Synchronous with a long timeout.** Honest about the latency, but a two-minute
request is a two-minute chance for a proxy, a load balancer or a laptop lid to
end it, with nothing to resume.
