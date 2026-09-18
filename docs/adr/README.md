# Architecture decision records

Short documents recording decisions that were close calls, so a future
contributor can tell what was considered and why one option won — and so
reopening a question is possible without re-arguing it from scratch.

A decision only needs a record if reasonable people could disagree. Most code
does not.

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-declarative-specification.md) | A declarative specification, not a query builder | Accepted |
| [0002](0002-derived-metrics-after-aggregation.md) | Derived metrics are computed after aggregation | Accepted |
| [0003](0003-in-process-job-execution.md) | In-process job execution, not a broker | Accepted |
| [0004](0004-http-llm-providers.md) | Narrative providers speak HTTP, not vendor SDKs | Accepted |
| [0005](0005-apache-2-license.md) | Apache-2.0, not MIT | Accepted |
| [0006](0006-local-column-profiling.md) | Column profiling is local, not model-assisted | Accepted |

## Format

```markdown
# NNNN. Title

Status: Proposed | Accepted | Superseded by ADR-NNNN
Date: YYYY-MM-DD

## Context
What is true that forces a decision.

## Decision
What we are doing.

## Consequences
What this costs, and what it rules out.

## Alternatives considered
What else was on the table and why it lost.
```

Copy the next number, open a pull request. See
[GOVERNANCE.md](../../GOVERNANCE.md) for which decisions need explicit consensus.
