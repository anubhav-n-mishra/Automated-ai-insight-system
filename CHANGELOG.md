# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**A change to how a figure is computed is treated as a breaking change**, even
when no function signature moves, because it changes what a stakeholder reads.

## [Unreleased]

Nothing yet.

## [1.0.0] - 2026-09-18

First public release under Apache-2.0. The project was restructured from a
prototype into an installable, tested, deployable package. Several defects that
produced silently incorrect output were fixed; each is listed below with what it
did, because anyone who generated a report with an earlier build should know.

### Added

#### Analysis
- **Driver attribution.** Every metric's movement is decomposed across the
  segments that caused it, with the share the listed drivers account for and an
  explicit flag when gains and losses partly cancel.
- **Per-metric aggregation.** Each metric declares how it reduces: `sum`, `avg`,
  `min`, `max`, `median`, `count` or `count_distinct`.
- **Metric intent.** `higher_is_better` lets cost rising read as a problem and
  revenue rising as a win. Reports colour by intent, not by sign.
- **Materiality filtering.** Segments below a configurable share of a metric are
  excluded before ranking, so a three-row segment cannot take the headline with
  a 900% swing.
- **Local dataset profiling.** Column roles and a usable comparison window are
  inferred from dtypes, cardinality and name patterns — in milliseconds, with no
  data leaving the deployment.
- **Documented ranking.** Impact is `contribution_share × priority_weight ×
  materiality`, explained in the code, on the deck's methodology slide and in
  the dashboard.

#### Platform
- **Installable package** with a `pyproject.toml`, a `src/` layout and an
  `insight-engine` CLI (`run`, `validate`, `profile`, `serve`, `schema`,
  `purge`, `doctor`).
- **Versioned HTTP API** at `/api/v1` with a complete OpenAPI document.
- **Asynchronous report generation.** Runs are queued to a bounded worker pool
  and polled, so one report no longer blocks every other request.
- **Published JSON Schema** for the specification, verified in CI.
- **Health, readiness and Prometheus metrics** endpoints.
- **Structured JSON logging** with a correlation id on every record, including
  from worker threads.
- **Optional OpenTelemetry tracing.**
- **Automatic cleanup** of expired sessions, uploads, artifacts and job records.
- **Container image**: multi-stage, non-root, read-only root filesystem, with a
  health check and published build provenance.
- **CI**: lint, strict types, tests on Python 3.10–3.13 across three operating
  systems, end-to-end CLI verification, dependency audit, secret scanning,
  CodeQL and an image boot test.

#### Narrative and voice
- **Any OpenAI-compatible endpoint** is supported by changing a base URL: Azure
  OpenAI, vLLM, Ollama, LiteLLM, OpenRouter.
- **Deterministic template writer** used when no provider is configured, so the
  tool is fully functional offline and without an API key.
- **Response caching, timeouts and jittered retries** on provider calls.

#### Documentation
- Architecture, configuration, deployment, troubleshooting and comparison
  guides; architecture decision records; a full audit of the defects fixed in
  this release.
- Apache-2.0 licence with a NOTICE file, plus contributing, governance,
  security, support and maintainer documentation.

### Fixed

#### Security
- **Dashboard authentication bypass.** The session endpoint declared its token
  parameter as optional and only compared it when present, so omitting the
  parameter entirely granted full access to any session id. The token is now
  required, compared in constant time against a stored hash, and a wrong token
  is indistinguishable from a missing session.
- **Session tokens were served as static files.** The working directory was
  mounted at `/tmp`, which exposed `tmp/sessions/*.json` — every live session's
  plaintext access token, readable by anyone who guessed the path. Session
  records are no longer web-reachable and no longer contain a plaintext token.
- **Path traversal in uploads.** Uploaded files were written to
  `tmp_dir / upload_file.filename`, so a request could name its upload
  `../../backend/app/main.py` and overwrite application source. Uploads are now
  written under server-generated identifiers.
- **Unrestricted outbound database access.** Connection strings supplied over
  HTTP were used verbatim, making the service a server-side request forgery
  primitive against everything the container could reach. Remote sources are now
  off by default and require both an opt-in and a host allowlist.
- **Internal detail in error responses.** The unhandled-exception handler
  returned `str(exc)`, serving filesystem paths, connection strings and driver
  messages to clients. Responses now carry a stable code, a safe message and a
  request id; the detail goes to the log.
- **Arbitrary code execution via derived metrics.** Metric expressions are
  parsed against an allowlisted grammar; nothing is evaluated as code.
- Added upload size limits, request rate limiting, security headers with a
  Content-Security-Policy that needs no `unsafe-inline`, and optional API-key
  authentication that production refuses to start without.

#### Correctness
- **Ratio metrics were averaged across rows.** `ctr = clicks / impressions` was
  computed per row and then summed, producing a figure with no meaning. Derived
  metrics are now computed from aggregated inputs.
- **Every metric was summed.** Averages, ratios and distinct counts were added
  together. Each metric now declares its own aggregation.
- **The deck's comparison chart never rendered.** It read `current_totals` and
  `previous_totals`, keys the engine never produced, so every report generated
  printed "Insufficient data for chart visualization".
- **The dashboard displayed invented numbers.** KPI card movements were produced
  by `Math.random() * 20 - 10`. They now come from the analysis.
- **The dashboard's insight table always showed 0.0%.** It read `insight.change`
  and `insight.dimension`; the engine wrote `delta_pct` and `segment`. The same
  mismatch made every audio briefing announce that every metric had changed by
  0.0 percent. One set of field names is now shared by every surface.
- **Segments present in only one period rendered blank.** The period join did
  not coalesce its keys, so new and lost segments lost their dimension values.
- **Growth from zero was reported as +100%.** An undefined percentage is now
  `None`, rendered as `n/a`, and never ranked on.
- **Date defaults selected empty windows.** The suggested comparison defaulted
  to the last 14 days from today, which for any historical extract selected two
  periods containing no rows. It is now derived from the data's actual range.
- Derived-metric expressions were limited to a single binary operation;
  `(a + b) / c` was not expressible. They now support full arithmetic,
  parentheses and a small function set.
- Overlapping comparison periods were accepted, comparing a period against part
  of itself. They are now rejected at validation time.
- Missing dependencies (`qrcode`, `Pillow`, `requests`, `python-dotenv`,
  `SQLAlchemy`) were absent from the requirements file, so a clean install
  crashed on first run.

#### Reliability
- **Report generation blocked the event loop.** Ingestion, model calls and
  rendering ran synchronously inside `async def` handlers, so a single report
  froze every concurrent request including health checks.
- **Unbounded memory growth.** The session cache had no eviction, and generated
  reports, sessions and temporary files were never cleaned up.
- A hung model or speech provider could block indefinitely; all outbound calls
  now carry timeouts and bounded, jittered retries.
- QR codes and dashboard links were hard-coded to `http://localhost:8000`, which
  made the headline feature unusable anywhere but the machine that generated it.

### Changed

- **Licence**: Apache-2.0, with a NOTICE file carrying the attribution the
  licence requires on redistribution.
- **Narrative providers speak HTTP directly** instead of using vendor SDKs.
  Smaller install, explicit timeouts, and any OpenAI-compatible gateway works.
- **Report generation returns `202` with a job id** rather than blocking until
  the deck exists.
- **Web UI rebuilt**: mobile-first and responsive, WCAG 2.1 AA contrast and
  focus handling, full keyboard support, ARIA tab and live-region semantics,
  `prefers-reduced-motion` respected, light and dark themes, and rendering that
  cannot execute injected markup.
- **Progress is reported honestly.** The previous UI advanced a five-step
  checklist on an 800ms timer regardless of what the backend was doing.
- Every report now carries a methodology slide and panel stating what was
  analysed, how, and what the caveats are.

### Removed

- The `backend/` and `frontend/` directories, superseded by the package.
- Committed build artifacts, generated databases and 3.7 MB of screenshots from
  the repository root.
- Vendor LLM SDKs from the base install.

[Unreleased]: https://github.com/anubhav-n-mishra/Automated-ai-insight-system/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/anubhav-n-mishra/Automated-ai-insight-system/releases/tag/v1.0.0
