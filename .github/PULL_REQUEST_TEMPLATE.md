## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Why

<!-- The problem, not the patch. Link the issue if there is one: Closes #123 -->

## How it was verified

<!-- Commands you ran and what came back. "make check passes" is enough for a
     small change; a behavioural change wants the before and after. -->

```
$ make check
```

## Checklist

- [ ] `make check` passes (lint, strict types, tests)
- [ ] Tests cover the new behaviour, including the failure path
- [ ] Public behaviour changes are documented (README, `docs/`, docstrings)
- [ ] `CHANGELOG.md` has an entry under `## [Unreleased]`
- [ ] No secrets, tokens, customer data or internal hostnames in the diff

## Analytical changes

<!-- Delete this section if the change cannot alter a reported number. -->

- [ ] The change is covered by a test asserting the **specific expected value**,
      not merely that a number is produced
- [ ] Aggregation semantics are unchanged, or the change is described here and
      in `docs/ARCHITECTURE.md`
- [ ] Nothing new is displayed that the engine did not compute

## Security

<!-- Delete this section if the change cannot affect the security posture. -->

- [ ] No new network egress, filesystem write path or deserialisation
- [ ] New inputs are validated before use, and errors do not leak internals
- [ ] Anything that relaxes a default is opt-in and documented in `.env.example`
