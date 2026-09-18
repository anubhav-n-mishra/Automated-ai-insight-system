# 0006. Column profiling is local, not model-assisted

Status: Accepted
Date: 2026-09-18

## Context

When a user uploads a file, the tool has to guess which column is the time axis,
which are segments and which are measures.

The prototype sent the file's headers and five sample rows to Gemini and asked
it to classify them. It was, to be fair, reasonably accurate.

It was also slow (a network round trip on every upload), billable per upload,
non-functional without an API key — the first thing a new user hits — and it
transmitted a customer's business data to a third party purely to classify
column headers. That last point is the one that mattered: several categories of
user cannot do it at all, and for everyone else it is a disclosure with no
corresponding benefit.

The information needed to answer the question is already in the file: dtypes,
cardinality, null density, value shapes and column names.

## Decision

Profiling is local, from the data itself. A role classifier over dtype,
cardinality ratio, null fraction, sampled value shape and name patterns.

The date range is read from the column, so the suggested comparison is a window
**the data actually covers** — which also fixed a separate defect, where the UI
defaulted to "the last 14 days from today" and therefore selected two empty
windows for any historical extract.

## Consequences

Good:

- Milliseconds, not a round trip.
- No API key needed, so the first-run path works out of the box.
- Nothing leaves the deployment.
- Deterministic: the same file profiles the same way every time, which is
  testable.
- The reasoning is inspectable — every column reports why it was classified as
  it was, and `insight-engine profile` prints it.

Costs:

- Heuristics are wrong sometimes, and a model reads intent from an obscure
  column name better than a regex does. Mitigated by showing every suggestion in
  the UI as an editable selection rather than applying it silently, and by
  reporting a confidence and a reason per column.
- The heuristics need occasional tuning. Two already earned their place: a
  numeric column that is almost entirely unique is an identifier rather than a
  measure, and a four-digit integer in a plausible calendar range is a period
  label rather than a quantity — summing birth years is the single most common
  auto-detection failure.

## Alternatives considered

**Model-assisted with a local fallback.** Two code paths, two sets of
behaviour to test, and the privacy problem remains for anyone who leaves the
default on. The local path turned out to be good enough that the model path
earned nothing.

**No inference; make the user choose everything.** Honest, and tedious. The
profiler's suggestions are the difference between a two-minute first run and a
twenty-minute one.
