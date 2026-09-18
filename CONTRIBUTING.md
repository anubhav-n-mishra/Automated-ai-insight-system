# Contributing

Thanks for considering a contribution. This document covers everything from
"I found a typo" to "I want to change how ranking works".

If anything here is unclear or wrong, that is itself worth an issue.

---

## Table of contents

- [Ground rules](#ground-rules)
- [Ways to help](#ways-to-help)
- [Getting set up](#getting-set-up)
- [The development loop](#the-development-loop)
- [How the codebase is organised](#how-the-codebase-is-organised)
- [Code standards](#code-standards)
- [Testing](#testing)
- [Changing anything analytical](#changing-anything-analytical)
- [Commits and pull requests](#commits-and-pull-requests)
- [Review](#review)
- [Releasing](#releasing)
- [Licensing your contribution](#licensing-your-contribution)

---

## Ground rules

Everyone participating agrees to the [Code of Conduct](CODE_OF_CONDUCT.md).

Two project-specific principles are worth stating up front, because they decide
most design arguments here:

**A wrong number is worse than a missing one.** This tool tells people what
happened to their business. If a figure cannot be computed honestly, it must be
reported as unavailable, not estimated, defaulted or filled in. There is no
acceptable reason to display something the engine did not compute.

**A degraded feature beats a failed report.** When a model provider is down, the
narrative falls back to a deterministic writer. When speech synthesis is
unavailable, the browser reads the briefing. The numbers always get through.

---

## Ways to help

| You have | Start here |
| --- | --- |
| Found a figure that looks wrong | [Open a correctness report](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=wrong_number.yml) — the highest-value bug report this project gets |
| Found a bug | [Bug report](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=bug_report.yml) |
| Found a security issue | [SECURITY.md](SECURITY.md). Do not open a public issue |
| Have an idea | [Feature request](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=feature_request.yml), or a Discussion if it is still vague |
| Want to write code | Issues labelled `good first issue` and `help wanted` |
| Want to improve the docs | Just open a pull request. Documentation fixes need no prior discussion |

For a change larger than roughly 200 lines, please open an issue first. It is
much less disappointing to disagree about an approach before the work than
after it.

---

## Getting set up

Requirements: Python 3.10 or newer, `git`, and `make`. Docker is optional.

```sh
git clone https://github.com/anubhav-n-mishra/Automated-ai-insight-system.git
cd Automated-ai-insight-system

make install        # virtual environment, dev dependencies, pre-commit hooks
make check          # lint, strict types, tests — should pass on a clean clone
make demo           # generate a real report from the bundled example
```

`make demo` is the fastest way to confirm the whole pipeline works. It needs no
API key: with no provider configured the summary is written deterministically
from the computed figures.

To run the server and UI:

```sh
make run            # http://127.0.0.1:8000
```

`insight-engine doctor` prints the effective configuration, including which
optional dependencies are present. It prints no secrets, so it is safe to paste
into an issue.

---

## The development loop

```sh
make format         # apply formatting and safe lint fixes
make test-fast      # unit tests only, no coverage gate — fast enough to run constantly
make check          # everything CI will run
```

Run `make check` before pushing. It runs exactly what CI runs, so a green local
check means a green pipeline.

---

## How the codebase is organised

Layers depend downwards only. Nothing below `api/` imports FastAPI, which is
what keeps the whole pipeline usable as a library and from the CLI.

```
src/insight_engine/
  domain/       Pure data: the specification, and the result objects.
                No I/O, no framework imports, no behaviour beyond validation.
  connectors/   SourceSpec -> DataFrame. Owns source policy: which drivers and
                hosts are reachable, row caps, path confinement.
  engine/       The analysis itself: formula evaluation, aggregation, period
                comparison, ranking, driver attribution, and the renderers.
  llm/          Narrative providers behind one protocol. HTTP, not vendor SDKs.
  storage/      Artifacts by opaque key. Local filesystem today; the protocol is
                what an object-store implementation satisfies.
  sessions/     Shareable dashboard sessions. Tokens are hashed, never stored.
  jobs/         Bounded thread-pool execution with pollable status.
  service.py    The one place that knows the full choreography. The CLI and the
                API both call it, which is what stops them drifting apart.
  api/          Transport. Contains no analytics logic.
  web/          The shipped UI. No build step, no CDN, no framework.
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains why it is shaped this way,
and [docs/adr/](docs/adr/) records the decisions that were close calls.

---

## Code standards

Tooling enforces most of this; `make format` will fix what it can.

- **Formatting and linting**: `ruff`, 100 columns.
- **Types**: `mypy --strict` over `src/`. New code is fully annotated. Where a
  third-party library ships no usable types, the exception lives in
  `pyproject.toml` with a comment saying which library and why.
- **Errors**: raise something from `insight_engine.core.errors`. Every error
  carries a stable `code` and an HTTP `status`. Anything sensitive goes in
  `internal_detail`, which is logged and never serialised.
- **Logging**: `get_logger(__name__)`, structured fields via `extra=`, never an
  f-string of interpolated values. Never log a credential; the formatter
  redacts known key names, but do not rely on it.
- **Dependencies**: adding one to the base install needs a justification in the
  pull request. Optional capabilities belong in an extra.

### Comments

Comment the surprising, not the obvious. A comment should say **why**, or warn
about a trap. `# increment the counter` above `counter += 1` is noise; a note
explaining that a zero denominator must produce `null` rather than `0.0`,
because `0.0` asserts a fact that is not true, is worth its line forever.

Match the density of the file you are editing.

---

## Testing

```sh
pytest                          # everything, with the coverage gate
pytest tests/unit -q --no-cov   # fast inner loop
pytest -m integration           # end-to-end paths
pytest -k formula               # one area
```

Coverage must stay at or above 70%. That is a floor, not a target: the number
matters far less than whether the *failure* paths are tested.

What a good test looks like here:

- **Assert the specific value**, not that a number came back. `assert ctr ==
  pytest.approx(40 / 300)` catches a regression; `assert ctr > 0` does not.
- **Test the failure.** Most bugs this project has had were in the path where
  something was missing, zero, or present in only one period.
- **Name the behaviour, not the function.**
  `test_ratio_is_computed_after_aggregation` tells a future reader what broke.
- **When you fix a bug, add the test that would have caught it.** Several tests
  in this suite carry a comment describing the specific defect they pin down.
  That comment is the point.

Tests must not reach the network, mutate the environment, or depend on ordering.
Providers are tested against `httpx.MockTransport`; the filesystem is `tmp_path`.

---

## Changing anything analytical

Any change that can alter a reported number carries extra weight, because the
failure mode is silent: a report that is plausible and wrong.

If you touch `engine/aggregate.py`, `engine/insights.py`, `engine/formula.py`,
or the `domain/` models, please:

1. **Work an example by hand** and put the arithmetic in the test. State the
   inputs and the expected output in a comment so a reviewer can check it
   without re-deriving it.
2. **Say what it changes** for an existing user. "Reports will now rank X above
   Y" belongs in the pull request and in `CHANGELOG.md`.
3. **Respect the two invariants:**
   - Ratio and non-additive metrics are computed **after** aggregation, from
     aggregated inputs. Never averaged across rows.
   - A percentage change against a zero baseline is `None`, rendered as `n/a`.
     Never `100`, never `0`.
4. **Keep the score explainable.** Ranking is documented at the top of
   `engine/insights.py`. If you change the formula, change that docstring, the
   methodology slide, and the dashboard's methodology panel in the same commit.
   A ranking nobody can explain is a ranking nobody should trust.

---

## Commits and pull requests

### Commits

[Conventional Commits](https://www.conventionalcommits.org/), because the
changelog and release tooling read them:

```
feat(engine): attribute metric movement across segments
fix(api): require the session token on dashboard reads
docs(security): document the coordinated disclosure timeline
test(insights): cover segments present in only one period
perf(aggregate): push the period filter into the scan
refactor(llm): replace vendor SDKs with the REST APIs
chore(deps): bump polars to 1.17
```

Types: `feat`, `fix`, `docs`, `test`, `perf`, `refactor`, `build`, `ci`,
`chore`, `revert`. Breaking changes get a `!` after the scope and a
`BREAKING CHANGE:` footer.

Write the body for someone reading it in two years with no memory of the issue.

### Pull requests

- Branch from `main`. One logical change per pull request.
- Fill in the template. The analytical and security sections are there because
  those are the two places where a plausible-looking diff does real damage.
- Add a `## [Unreleased]` entry to `CHANGELOG.md`.
- Mark it a draft while it is still in progress.
- Keep it focused. An unrelated refactor buried in a bug fix makes the fix
  harder to review and much harder to revert.

---

## Review

A maintainer will respond within roughly a week. If a week passes with silence,
a comment on the thread is entirely welcome — it is far more likely that the
notification was lost than that the work was ignored.

Reviews look for, in order:

1. **Is it correct?** Especially: what happens when the input is empty, zero,
   null, or present in only one period?
2. **Is it safe?** New inputs, new egress, new write paths, new deserialisation.
3. **Is it tested?** Particularly the failure path.
4. **Will the next person understand it?** Naming, and comments on the parts
   that are not obvious.
5. **Does it match the house style?** Last, and the least interesting.

Review comments are about the code. If one ever reads otherwise, that is a bug
in the review, and saying so is appropriate.

---

## Releasing

Maintainers only. See [GOVERNANCE.md](GOVERNANCE.md) for who decides.

1. Move `## [Unreleased]` in `CHANGELOG.md` to the new version with a date.
2. Bump `__version__` in `src/insight_engine/__init__.py` and the `version` in
   `pyproject.toml`.
3. `make check && make schema` — the schema is a published contract and CI
   fails if it drifts.
4. Tag `vX.Y.Z` and push. The release workflow verifies that the tag matches
   the package version and that the changelog documents it, builds and smoke
   tests the wheel, then publishes to PyPI via trusted publishing and to GHCR
   with build provenance attestation.

This project follows [Semantic Versioning](https://semver.org/). The public API
is the CLI, the HTTP API, the specification schema and the importable Python
package. **A change to how a number is computed is a breaking change**, even
when no signature moves.

---

## Licensing your contribution

This project is licensed under [Apache License 2.0](LICENSE).

By submitting a contribution you agree that it is licensed under the same terms,
as described in section 5 of the license:

> Unless You explicitly state otherwise, any Contribution intentionally
> submitted for inclusion in the Work by You to the Licensor shall be under the
> terms and conditions of this License, without any additional terms or
> conditions.

No separate CLA is required. Please do not submit code you do not have the right
to license, and do not paste in code from an incompatibly licensed project.

If your employer owns your work, make sure you have permission before
contributing.
