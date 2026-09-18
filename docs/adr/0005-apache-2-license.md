# 0005. Apache-2.0, not MIT

Status: Accepted
Date: 2026-09-18

## Context

The project had no licence file at all, which means it was not open source: with
no licence, default copyright applies and nobody may legally use it.

Choosing one is a permanent decision — relicensing later needs every
contributor's agreement.

Requirements:

1. Companies must be able to adopt it without a legal review that stalls.
2. Attribution must survive redistribution. The author wants credit, and wants
   it to persist into derivative works.
3. Patent exposure should be addressed explicitly, because enterprises ask.

## Decision

Apache License 2.0, with a `NOTICE` file carrying the attribution that section 4
requires be preserved.

## Consequences

Good:

- **Attribution is a licence condition, not a convention.** Section 4 requires
  retaining copyright, patent, trademark and attribution notices, and including
  the NOTICE contents in any redistribution. MIT requires only the licence text;
  in practice that attribution disappears into a bundled third-party list.
- **An explicit patent grant** from every contributor, which terminates for
  anyone who initiates patent litigation over the software. This is the clause
  enterprise legal teams look for, and its absence from MIT is a recurring
  adoption blocker.
- **Modifications must be stated**, so a fork that changes how a number is
  computed cannot present itself as unchanged. For a tool whose output people
  make decisions on, that matters more than it would for a library.
- Universally recognised. Apache-2.0 is on every corporate allowlist and is the
  Apache Software Foundation's and Kubernetes' licence.
- Compatible with GPLv3 in one direction, so it can be incorporated into
  copyleft projects.

Costs:

- Longer and less readable than MIT. Contributors notice.
- Slight friction for a project that wants to vendor a single file: the NOTICE
  has to come too.
- Permissive, so a company can build a commercial product on this and give
  nothing back. That is accepted: adoption is worth more here than extraction
  is worth preventing.

## Alternatives considered

**MIT.** Shortest and most familiar, but no patent grant, no NOTICE requirement,
and attribution in practice reduces to a line in a bundled licence file.
Requirements 2 and 3 both fail.

**BSD-3-Clause.** Adds a no-endorsement clause, still no patent grant.

**AGPL-3.0.** Would force hosted derivatives to publish their source. It would
also remove this from most corporate allowlists, and the goal is for companies
to run it inside their systems.

**Business Source License or similar.** Not open source. It would make the
project's own contribution documentation dishonest.
