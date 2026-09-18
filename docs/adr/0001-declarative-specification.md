# 0001. A declarative specification, not a query builder

Status: Accepted
Date: 2026-09-18

## Context

The tool has to learn what to analyse: which column is time, which are segments,
which are measures, what the comparison windows are, and which metrics matter
most.

Three ways to get that:

1. A UI where the user assembles it, stored server-side.
2. Inference, with no configuration at all.
3. A declarative document the user writes and keeps.

The prototype did the first, storing nothing — every run started over — and had
begun growing a second, incompatible path for uploads.

The observation that settled it: this is a **recurring** report. The same
comparison runs every week. What matters is not how quickly you can build it
once, but whether it produces the same thing next week, and whether a colleague
can see what changed when it does not.

## Decision

The analysis is a validated YAML document that lives in the user's repository.
It is the only input to the engine. A JSON Schema is published so editors
validate it.

The UI builds one and shows it; it is not a separate path.

## Consequences

Good:

- A report is reviewable in a pull request. "Why did last week's number change?"
  is answerable with `git log`.
- The same file produces the same report from a terminal, from CI and behind the
  API — the CLI and the API cannot drift, because there is nothing to drift.
- Validation happens before any data is read, so a typo fails in milliseconds
  rather than halfway through.
- Testing needs no server and no fixtures beyond a dict.

Costs:

- A first-time user must write a file. Mitigated by `insight-engine profile`,
  which infers roles and a usable date range and prints what to paste.
- No exploration. You cannot follow a hunch. That is what Metabase is for, and
  [COMPARISON.md](../COMPARISON.md) says so plainly.

## Alternatives considered

**Server-side saved reports.** Better first-run experience, worse everything
else: state to migrate, no diffing, no review, and a UI that becomes the only
interface.

**Full inference.** Metabase X-Rays do this well and it is genuinely useful for
a dataset you have never seen. It cannot know that CPC going up is bad, or that
revenue matters more than impressions. Inference informs the profiler; it does
not replace the declaration.
