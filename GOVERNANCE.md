# Governance

This document describes how decisions get made, who makes them, and how that
changes as the project grows. It exists so that a contributor can tell, before
investing effort, what happens to their work.

## Current state

Insight Engine is maintained by a single maintainer. That is stated plainly
rather than dressed up as a committee, because it sets accurate expectations:
review is usually fast, but it has one point of failure, and that is the main
thing this document is designed to grow out of.

See [MAINTAINERS.md](MAINTAINERS.md) for who that is and how to reach them.

## Roles

### Users

Anyone who runs the software. Users shape the project by filing issues,
reporting figures that look wrong, and describing what they actually needed.
A well-written correctness report is worth more than most pull requests.

### Contributors

Anyone whose pull request has been merged. No formal onboarding, no CLA. Every
contributor is listed in the release notes for the version their work ships in.

### Reviewers

Contributors trusted to review changes in an area they know well. A reviewer's
approval carries weight in that area; it does not grant merge rights.

Becoming a reviewer: sustained, high-quality participation in one part of the
codebase — roughly five substantial merged contributions, or an equivalent body
of review. Existing maintainers invite; the candidate accepts.

### Maintainers

Contributors with commit and release rights. Maintainers are responsible for
review, releases, security response and, above all, for the project's
correctness guarantees.

Becoming a maintainer: sustained contribution over at least three months,
demonstrated judgement on changes that affect reported numbers, and unanimous
agreement of existing maintainers. Nomination happens in a public issue; the
discussion of the decision may be private, the outcome is not.

Maintainers who have been inactive for six months move to emeritus. This is
administrative, not a judgement, and returning is a matter of asking.

## How decisions are made

**Lazy consensus.** Most changes need one maintainer approval and no objection.
If nobody objects within a reasonable time, the change proceeds.

**Explicit consensus** is required for:

- anything that changes how a number is computed
- breaking changes to the CLI, HTTP API or specification schema
- adding a dependency to the base install
- changing the licence, governance, or the code of conduct
- adding or removing a maintainer

For these, a maintainer opens an issue describing the change and the reasoning,
leaves it open for at least 7 days, and proceeds only if no maintainer objects.

**Objections must be substantive.** "I would have done it differently" is a
review comment. "This produces an incorrect figure when the previous period is
empty" is an objection, and blocks until it is resolved or withdrawn.

**Deadlock.** If maintainers cannot agree, the change does not happen. A project
that ships a contested change to its analytical core is worse off than one that
waits.

## Scope

What this project is:

- A config-driven engine that compares two periods, ranks what moved, attributes
  the movement to segments, and renders that into a deck, a dashboard and an
  audio briefing.
- Explainable by construction. Every ranking has a documented formula and every
  report carries its own methodology.
- Deployable by one person into a container, and by a platform team into a
  cluster.

What this project is not, and proposals in these directions will be declined:

- **A general BI tool.** Ad-hoc exploration, arbitrary chart building and
  dashboard design belong to Superset, Metabase and Lightdash, which do them
  well.
- **A semantic layer.** Metric definitions here describe one report. A
  warehouse-wide metric store is Cube's and dbt's problem.
- **A scheduler.** Run the CLI from cron, Airflow, or whatever you already have.
- **An LLM chat interface over data.** The model writes prose about figures the
  engine computed. It never queries, never computes, and never decides what is
  important.

Scope decisions are judgement calls, and the reasoning gets written down in
[docs/adr/](docs/adr/) so that reopening a question is possible without
re-arguing it from scratch.

## Changing this document

Amendments follow the explicit-consensus process above: a public issue, at least
7 days, no maintainer objection.

## Code of conduct enforcement

Maintainers enforce the [Code of Conduct](CODE_OF_CONDUCT.md). A report
concerning a maintainer is handled by the others; where that is not possible
because there is only one, the reporter should escalate to GitHub Support, and
that limitation is one more reason this project is looking for maintainers.

## Security response

Security reports follow [SECURITY.md](SECURITY.md). Any maintainer may
unilaterally ship a security fix without waiting for consensus. The
justification is documented afterwards, publicly, in the advisory.
