# 0002. Derived metrics are computed after aggregation

Status: Accepted
Date: 2026-09-18

## Context

A derived metric such as `ctr = clicks / impressions` can be computed at two
points: per row before aggregation, or from aggregated inputs afterwards.

The prototype did the former, then summed the result along with everything else.
For two rows of `10/100` and `30/200` that produces `0.25`. The period CTR is
`40/300 = 0.133`.

This was not a rounding difference. Every ratio in every report the tool had
ever produced was wrong, and nothing looked broken: the number was plausible,
formatted correctly, charted, and read aloud in the audio briefing.

It is Simpson's paradox in miniature, and it is the single most common error in
hand-built reporting.

## Decision

Derived metrics are **expressions over aggregated metrics**. They are evaluated
after the group-by, and again independently at grand-total level. Nothing in the
pipeline ever evaluates a derived metric against a raw row.

This is enforced structurally rather than by convention: the domain model
separates `MetricSpec` (a column plus an aggregation) from `DerivedMetricSpec`
(an expression over metric names), and `aggregate()` appends derived columns
only after the reduction.

## Consequences

Good:

- Ratios are correct, at every level, by construction.
- A grand total is not the sum of segment values for a ratio, which is also
  correct — and the driver attribution declines to decompose ratio metrics for
  the same reason.
- Row-level derived columns are impossible to express, so the bug cannot return
  by accident.

Costs:

- A user cannot filter or segment on a row-level derived value ("rows where
  per-row CTR exceeded 5%"). That is a real capability and it is gone. It can be
  done in a SQL source, where it belongs.
- Derived metrics cannot feed other derived metrics in one pass without ordering
  care; they are evaluated in declaration order against the columns available at
  that point.

## Alternatives considered

**Compute both and let the user choose.** Two fields with subtly different
meanings and no way to tell which one a report used. The wrong one would be
chosen constantly.

**Warn instead of forbidding.** A warning on a number that is already in a deck
being read in a meeting is not a control.
