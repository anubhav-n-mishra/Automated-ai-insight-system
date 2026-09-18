# Repository audit

A full scan of the codebase as it stood before 1.0.0, and what was done about
each finding. This is kept in the repository rather than in a pull request
description because several of the defects produced **silently incorrect
output** — anyone who generated a report with an earlier build deserves to be
able to look up exactly what was wrong with it.

Findings are ordered by severity. Each carries the symptom, the mechanism, and
the fix.

**Summary**

| Severity | Count | Fixed in 1.0.0 |
| --- | --- | --- |
| Critical (security) | 6 | 6 |
| High (silently wrong output) | 9 | 9 |
| High (reliability) | 5 | 5 |
| Medium (UX, accessibility, responsiveness) | 14 | 14 |
| Medium (engineering hygiene) | 11 | 11 |
| Feature gaps | 9 | 6, 3 on the roadmap |

---

## Critical: security

### S1. Dashboard authentication bypass

**Symptom.** Anyone who knew or guessed a session id could read the full
analysis for that report.

**Mechanism.** The endpoint declared its token as optional and only compared it
when one was present:

```python
async def get_dashboard_data(session_id: str, token: str = None):
    session = session_manager.get_session(session_id, token)

# and in the store:
if token and session.token != token:      # no token -> no check at all
    return None
```

Omitting `?token=` entirely skipped verification. The token was also compared
with `!=`, which leaks the length of a matching prefix through timing.

**Fixed.** The token is a required query parameter. Only a SHA-256 hash is
stored, comparison uses `secrets.compare_digest`, and a wrong token returns the
same 404 as an unknown session so the endpoint cannot be used to enumerate which
sessions exist. Regression tests:
`tests/integration/test_api.py::TestDashboardAccess`.

### S2. Every live session token was served as a static file

**Symptom.** A complete list of valid access tokens was downloadable over HTTP.

**Mechanism.** The application mounted its working directory:

```python
app.mount("/tmp", StaticFiles(directory=str(tmp_dir)), name="tmp")
```

Sessions were written as `tmp/sessions/<id>.json` containing the plaintext
token. `GET /tmp/sessions/<id>.json` returned it.

**Fixed.** Session records are no longer web-reachable, no longer contain a
plaintext token, and are written with mode `0600`. Artifacts are served through
a handler that validates its key. Regression test:
`TestArtifactIsolation::test_session_files_are_not_reachable_over_http`.

### S3. Path traversal through the upload filename

**Symptom.** A crafted upload could overwrite files on the server, including
application source.

**Mechanism.** The client-supplied filename was used as the destination path:

```python
file_path = dest_path / upload_file.filename        # "../../backend/app/main.py"
temp_config_path = tmp_dir / config_file.filename
```

**Fixed.** Uploads are written to `<uploads>/<server-generated-id>/data.csv`.
The client filename is reduced to a display label that never touches the
filesystem. `SourcePolicy.resolve_path` additionally confines every resolved
source path to the upload directory when the spec came over HTTP. Regression
tests: `tests/unit/test_security.py::TestUploadSafety`.

### S4. Unrestricted server-side database connections

**Symptom.** The service would connect to any host a request named, making it a
server-side request forgery primitive against everything the container could
reach — including cloud metadata endpoints.

**Mechanism.** The browser posted a connection string, the server passed it
straight to SQLAlchemy.

**Fixed.** Remote sources are disabled by default. Enabling them requires
`INSIGHT_ENGINE_ALLOW_REMOTE_SQL=true` **and** a host allowlist, which
production enforces the presence of at startup. Drivers are allowlisted
separately. Queries are rejected unless they are a single read statement, and
table names are validated against a strict identifier pattern before quoting.
Regression tests: `TestSourcePolicy`, `TestSqlSafety`.

### S5. Internal detail returned to clients

**Symptom.** Filesystem paths, connection strings and driver messages appeared
in HTTP responses.

**Mechanism.**

```python
async def global_exception_handler(request, exc):
    return JSONResponse(status_code=500,
                        content={"error": "Internal server error", "detail": str(exc)})
```

`traceback.print_exc()` was also called in the request path.

**Fixed.** A typed error hierarchy separates the client-safe `message` from
`internal_detail`, which is logged and never serialised. Unhandled exceptions
return a stable code, a generic message and the request id. Regression test:
`TestErrorEnvelope`; `tests/unit/test_infrastructure.py::test_a_failure_never_leaks_internal_detail`.

### S6. No authentication, rate limiting or upload bounds

**Symptom.** An open endpoint that reads files, runs queries and spends money at
a model provider.

**Fixed.** Optional API-key authentication that production refuses to start
without, a sliding-window rate limiter, `Content-Length` rejection before
buffering, chunked reads that abort past the cap, strict security headers, and a
Content-Security-Policy that needs no `unsafe-inline` because the UI ships no
inline script or style.

---

## High: silently incorrect output

This class is the most dangerous in a reporting tool, because nothing looks
broken.

### C1. Ratio metrics were averaged across rows

**Symptom.** Every CTR, CPC, conversion rate and any other derived ratio was
wrong, in both the deck and the dashboard.

**Mechanism.** Derived metrics were computed per row and then summed with
everything else:

```python
df = compute_all_derived_metrics(df, config.derived_metrics)   # per row
...
agg_exprs = [pl.col(m).sum().alias(m) for m in metrics]        # then summed
```

For two rows of `10/100` and `30/200`, that yields `0.25`. The period CTR is
`40/300 = 0.133`.

**Fixed.** Derived metrics are expressions over **aggregated** metrics,
evaluated after the group-by and again at grand-total level. Regression test:
`test_aggregate.py::TestDerivedMetrics::test_ratio_is_computed_after_aggregation`.

### C2. Every metric was summed regardless of meaning

**Symptom.** Averages, ratios, rates and identifiers were added together.
Summing customer ids produced a large, confident, meaningless number.

**Fixed.** `MetricSpec.aggregation` — `sum`, `avg`, `min`, `max`, `median`,
`count`, `count_distinct`. The profiler additionally classifies high-cardinality
numerics and year-like columns as identifiers and dimensions rather than
measures.

### C3. The deck's comparison chart never rendered

**Symptom.** Every deck ever generated printed "Insufficient data for chart
visualization" on the metric comparison slide.

**Mechanism.** The renderer read keys the engine never produced:

```python
current_metrics = metrics_data.get("current_totals", {})    # never set
previous_metrics = metrics_data.get("previous_totals", {})  # never set
```

**Fixed.** One result model shared by every renderer. Regression test:
`TestReportLifecycle::test_full_run_produces_a_deck_and_a_dashboard` asserts the
totals are present; the CI job asserts the deck contains at least seven slides.

### C4. The dashboard displayed invented numbers

**Symptom.** KPI cards showed percentage movements that had nothing to do with
the data, and changed on every page load.

**Mechanism.**

```javascript
const change = Math.random() * 20 - 10;   // "Demo change value"
```

**Fixed.** Every figure on the dashboard comes from the analysis payload. A
missing value renders as an em dash; an undefined percentage renders as `n/a`.

### C5. The insight table always showed 0.0%

**Symptom.** Every row of the dashboard's table read `↑ 0.0%`, and every impact
bar was identical.

**Mechanism.** The client read `insight.change`, `insight.dimension` and
`insight.impact`; the engine wrote `delta_pct`, `segment` and `impact_score`.
Every lookup returned `undefined`, and the `|| 0` defaults turned that into
zero.

**Fixed.** One set of field names across the engine, the API, the deck, the
dashboard and the briefing.

### C6. Every audio briefing announced 0.0 percent

**Symptom.** "MntWines increased by 0.0 percent. NumWebVisits increased by 0.0
percent."

**Mechanism.** The same field mismatch as C5, in `voice_briefing.py`.

**Fixed.** The script is built from the result model. A regression test asserts
the phrase `"0.0 percent"` does not appear in a generated briefing.

### C7. Segments present in only one period rendered blank

**Symptom.** New and lost segments appeared as empty rows.

**Mechanism.** `how="outer"` without coalescing left the join keys null on one
side.

**Fixed.** `how="full", coalesce=True`, plus presence markers captured before
zero-filling so new and lost segments are labelled rather than merely present.
Regression test:
`test_insights.py::test_segments_present_in_only_one_period_keep_their_labels`.

### C8. Growth from zero was reported as +100%

**Symptom.** A segment going from 0 to 3 outranked one going from 10,000 to
11,000.

**Mechanism.**

```python
if previous == 0:
    return 100.0 if current > 0 else -100.0
```

**Fixed.** `percent_change` returns `None`. It propagates as `None`, renders as
`n/a`, and is never ranked on. Regression test:
`TestPercentChange::test_zero_baseline_is_undefined_not_a_hundred`.

### C9. Derived metrics could only express one operation

**Symptom.** `(revenue - cost) / impressions` was rejected as unparseable.

**Mechanism.** A regular expression matching exactly `operand OP operand`.

**Fixed.** An AST-based evaluator over a restricted grammar: full arithmetic,
parentheses, and `abs`, `min`, `max`, `coalesce`, `safe_div`, `log`, `sqrt`,
`round`. It is an allowlist walk, not `eval`, so a spec uploaded over HTTP
cannot execute code. Regression tests: `tests/unit/test_formula.py`, which
includes a dozen rejected injection attempts.

---

## High: reliability

### R1. Report generation blocked the event loop

Ingestion, model calls and PPTX rendering ran synchronously inside `async def`
handlers. A single report froze every concurrent request, including `/health`,
for its full duration.

**Fixed.** Work runs on a bounded thread pool; the handler returns `202` with a
job id.

### R2. Unbounded memory growth

The session manager's cache had no eviction. Generated reports, sessions,
uploads and temporary files were never deleted — `cleanup_expired` existed but
nothing called it.

**Fixed.** Bounded caches with LRU eviction, retention windows on every stored
object, and a background sweeper that runs off the event loop.

### R3. No timeouts on outbound calls

A hung model or speech provider blocked a worker indefinitely.

**Fixed.** Explicit timeouts everywhere, bounded retries with full jitter, and
`Retry-After` honoured. Unjittered retries from a pool of workers synchronise
into a thundering herd against a provider that is already rate-limiting.

### R4. The singleton session manager was not safe to reuse

`__new__` reset the global instance whenever a different storage directory was
passed, so two concurrent callers could observe each other's manager.

**Fixed.** Ordinary objects, constructed once at startup and injected.

### R5. Share links pointed at localhost

`base_url = "http://localhost:8000"` was hard-coded in three places. The QR code
— a headline feature — was unusable anywhere but the machine that generated it.

**Fixed.** `INSIGHT_ENGINE_PUBLIC_BASE_URL`, which production refuses to start
without.

---

## Medium: user experience, accessibility, responsiveness

| # | Finding | Fix |
| --- | --- | --- |
| U1 | Paired date inputs used `display:flex` with no breakpoint; on a phone the four fields collapsed to roughly 60px each | Mobile-first grid that stacks below 560px, 44px minimum touch targets |
| U2 | `outline: none` on inputs with no replacement | A visible focus ring on every interactive element |
| U3 | Body text at `#a0a0a0` on translucent dark, placeholders at `#666` — both fail WCAG AA | Every foreground/background pair verified at AA in both themes |
| U4 | Progress was a five-step checklist on an 800ms timer, unrelated to what the backend was doing | Real stage and fraction polled from the job record |
| U5 | Errors surfaced through `alert()` | Inline field errors plus a dismissible toast region with `role="status"` |
| U6 | No ARIA on the tab list; emoji as the only semantics | Full ARIA tab pattern with roving tabindex, `aria-live` on status regions, labelled icons |
| U7 | No `prefers-reduced-motion` handling | Honoured globally |
| U8 | The whole CSV was read in the browser with `readAsText`, freezing the tab on a large file | The file is uploaded and profiled server-side; only a preview comes back |
| U9 | The date defaults were "the last 14 days from today", which selected two empty windows for any historical extract | The suggestion is derived from the data's actual range, and the pickers are clamped to it |
| U10 | A column could be selected as both a dimension and a metric | Mutually exclusive, with a toast explaining the move |
| U11 | Overlapping periods were accepted | Rejected in the browser and again at validation |
| U12 | No way to cancel a running report | Cancel button, `DELETE /api/v1/jobs/{id}` |
| U13 | No dark mode, no theme control | System-aware with an explicit toggle, persisted |
| U14 | User-controlled and model-generated text went through `innerHTML` | Nothing is rendered with `innerHTML`; a CSV column named `<img src=x onerror=…>` is inert |

The single worst of these is U9. It meant the default path through the UI, for
any dataset that was not from this week, produced an empty report and no
explanation.

---

## Medium: engineering hygiene

| # | Finding | Fix |
| --- | --- | --- |
| E1 | Five packages the code imported were missing from `requirements.txt`; a clean install crashed on first run | `pyproject.toml` with the real dependency set and extras |
| E2 | `sys.path.insert` in seven modules | A proper installable package |
| E3 | No tests. The one `test_ingest.py` was a script that wrote a database into the repository | 201 tests, 74% coverage, a gate at 70% |
| E4 | No CI, linting, formatting or type checking | Ruff, `mypy --strict`, pytest on four Python versions and three operating systems, dependency audit, secret scanning, CodeQL |
| E5 | No container image or deployment documentation | Multi-stage, non-root, read-only rootfs, health check, compose file, deployment guide |
| E6 | No LICENSE file at all | Apache-2.0 with a NOTICE |
| E7 | 3.7 MB of screenshots and generated `.db` and `.pptx` files committed | Moved to `docs/media/`, artifacts removed; repository is 8.1 MB → 3.9 MB |
| E8 | Pydantic v1 validators against a v2 dependency; `class Config: env_prefix` on a plain `BaseModel`, which did nothing | Pydantic v2 throughout, settings via `pydantic-settings` |
| E9 | Two different defaults for the same model setting in the same file | One source of truth |
| E10 | Bare `except:` clauses, imports inside functions | Typed exception handling; imports at module scope except where an optional extra requires otherwise |
| E11 | Secrets written to disk as part of the uploaded config | Credentials are `${ENV_VAR}` references resolved at read time |

---

## Feature gaps

Identified during the scan. Six are addressed in 1.0.0; three are on the
[roadmap](ROADMAP.md).

| Gap | Status |
| --- | --- |
| No root-cause attribution — the tool said *what* moved but never *why* | **Done.** Driver decomposition with explained share and offsetting detection |
| No metric intent, so a cost increase was coloured as good news | **Done.** `higher_is_better` |
| Column detection required a model API call and shipped customer data to a third party | **Done.** Local profiling in milliseconds, nothing leaves the deployment |
| No programmatic output — the deck was the only artifact | **Done.** JSON from the CLI and the API |
| No report-level provenance; readers could not tell what had been analysed | **Done.** A methodology slide and dashboard panel on every report |
| Ranking was unexplained and not comparable across metrics | **Done.** A documented, normalised formula |
| No statistical significance on segment movements | Roadmap |
| No hierarchical drill-down across dimension subsets | Roadmap |
| No scheduling, alerting or change detection over time | Roadmap — deliberately out of scope for the engine itself |

---

## How these stay fixed

Every finding above has a test that fails if it returns. Several of those tests
carry a comment describing the original defect; that comment is the point, and
please leave it in place when you touch the test.

CI runs lint, strict type checking, the full suite on four Python versions,
`pip-audit`, secret scanning, CodeQL, and an end-to-end CLI run that asserts the
generated deck contains a real presentation with at least seven slides.
