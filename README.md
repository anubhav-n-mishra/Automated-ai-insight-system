<div align="center">

# Insight Engine

**Explainable period-over-period analytics. Numbers you can audit, in a deck
you can send.**

Point it at a dataset, describe what matters in a YAML file, and get back a
ranked account of what moved, which segments caused it, a PowerPoint deck, a
shareable dashboard and an audio briefing.

[![CI](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/actions/workflows/ci.yml/badge.svg)](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/actions/workflows/ci.yml)
[![CodeQL](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/actions/workflows/codeql.yml/badge.svg)](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/actions/workflows/codeql.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)
[![Typed](https://img.shields.io/badge/mypy-strict-brightgreen.svg)](pyproject.toml)

[Quick start](#quick-start) · [How it works](#how-it-works) ·
[Configuration](docs/CONFIGURATION.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Deployment](docs/DEPLOYMENT.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## The problem

Every week someone exports a dataset, pivots it against last week, stares at the
result, and writes three bullet points for a deck. It takes two hours, it is
easy to get subtly wrong, and the result is a number without a reason.

The hard part is not the arithmetic. It is answering **which of these hundred
movements actually matters, and what caused it** — and being able to show your
working when someone disagrees.

## What this does

```
$ insight-engine run examples/configs/marketing-csv.yaml

==============================================================================
Paid media, week over week
2025-11-24..2025-11-30 vs 2025-11-17..2025-11-23
==============================================================================

Clicks rose +40.2% to 71.6K over 2025-11-24..2025-11-30 vs 2025-11-17..2025-11-23.
  - Clicks: 51.1K to 71.6K (+40.2%)
  - Spend: $102.1K to $143.1K (+40.2%)
  - Impressions: 1.9M to 2.5M (+34.9%)
  - Spend: campaign: Performance_Max, geo: US accounts for 30% of the movement.

Recommended: Review campaign: Performance_Max, geo: US: it drives the largest
movement in Spend. Confirm the change is intentional before it compounds.

TOTALS
  Impressions: 1.9M to 2.5M (+34.9%)
  Clicks: 51.1K to 71.6K (+40.2%)
  Spend: $102.1K to $143.1K (+40.2%)
  Click-through rate: 2.7595% to 2.8662% (+3.9%)
  Cost per click: $2.00 to $2.00 (+0.0%)

DRIVERS
  Spend: net 41,010.00 across 6 segments; the top 5 account for 91% of all movement
  Clicks: net 20,505.00 across 6 segments; the top 5 account for 91% of all movement

84 rows, 6 segments, 2.27s

Deck      : var/reports/2cb708bc.pptx
Dashboard : https://insights.example.com/d/A1R58Y…?token=…
```

Eight slides, a live dashboard, and a spoken briefing — from a config file and a
CSV.

## Why this one

Most tools in this space either let you explore data yourself (Metabase,
Superset, Lightdash) or generate prose from a model and hope it is right. This
one does neither.

**It computes, then it explains.** The model never sees your data, never
computes anything, and never decides what is important. It receives figures the
engine already produced and writes sentences about them. Turn it off entirely
and everything still works — the summary is written deterministically from the
same numbers.

**It refuses to guess.** A percentage change against a zero baseline is `n/a`,
not `+100%`. A ratio is computed from aggregated numerators and denominators,
never averaged across rows. A segment too small to be meaningful is excluded
before ranking rather than allowed to take the headline with a 900% swing.

**Every report carries its own methodology.** How many rows, over which windows,
segmented how, ranked by what formula, with which caveats. A report a
stakeholder cannot audit is a report they are right not to trust.

**It is a config file in git, not a saved view in a SaaS.** Reports are
reviewable, diffable and reproducible, and the same code path runs in your
terminal, in CI and behind the HTTP API.

See [docs/COMPARISON.md](docs/COMPARISON.md) for an honest account of where
other tools fit better.

---

## Quick start

### Install

```sh
pip install insight-engine
```

```sh
# With relational sources and a Postgres driver
pip install "insight-engine[sql,postgres]"

# Everything
pip install "insight-engine[all]"
```

### Your first report

```sh
# What does the engine think your columns are?
insight-engine profile data/events.csv
```

```
data/events.csv: 84 rows, 6 columns (delimiter ',')

COLUMN                      ROLE        TYPE         DISTINCT  REASON
date                        date        Date               14  parsed as a temporal type
campaign                    dimension   String              3  3 distinct categorical values
geo                         dimension   String              2  2 distinct categorical values
impressions                 metric      Int64              78  numeric measure
clicks                      metric      Int64              71  numeric measure
spend                       metric      Int64              71  numeric measure

Suggested comparison: 2025-11-24..2025-11-30 vs 2025-11-17..2025-11-23
```

Write that into a specification:

```yaml
# weekly.yaml
dataset:
  primary_source: events
  sources:
    events:
      type: csv
      path: data/events.csv
      date_column: date
      dimensions: [campaign, geo]
      metrics:
        - name: impressions
        - name: clicks
        - name: spend
          unit: currency

derived_metrics:
  - name: ctr
    expression: clicks / impressions
    label: Click-through rate
    unit: percent
  - name: cpc
    expression: spend / clicks
    label: Cost per click
    unit: currency
    higher_is_better: false     # cost going up is bad, and reports colour it so

report:
  title: Paid media, week over week
  date_column: date
  comparison:
    current_start: 2025-11-24
    current_end: 2025-11-30
    previous_start: 2025-11-17
    previous_end: 2025-11-23
  dimensions: [campaign, geo]
  kpi_priority: [spend, clicks, ctr, cpc, impressions]
```

Then:

```sh
insight-engine validate weekly.yaml   # catches mistakes before reading any data
insight-engine run weekly.yaml        # deck, dashboard, briefing
```

### Or run the server

```sh
insight-engine serve
# UI       http://127.0.0.1:8000
# API docs http://127.0.0.1:8000/docs
```

### Or a container

```sh
docker run --rm -p 8000:8000 \
  -v insight-data:/var/lib/insight-engine \
  ghcr.io/anubhav-n-mishra/automated-ai-insight-system:1
```

---

## How it works

```
  specification (YAML, in git)
          │
          ▼
  ┌───────────────┐   CSV · SQL · database table
  │   connectors  │   path confinement, driver and host allowlists, row caps
  └───────┬───────┘
          ▼
  ┌───────────────┐   each metric reduced by its own aggregation
  │   aggregate   │   derived metrics computed AFTER aggregation
  └───────┬───────┘
          ▼
  ┌───────────────┐   full outer join across periods, keys coalesced
  │    compare    │   additive metrics zero-filled; ratios never are
  └───────┬───────┘
          ▼
  ┌───────────────┐   impact = contribution × priority × materiality
  │      rank     │   immaterial segments excluded before ranking
  └───────┬───────┘
          ▼
  ┌───────────────┐   which segments caused each movement,
  │   attribute   │   and how much of it they explain
  └───────┬───────┘
          ▼
  ┌───────────────┐   optional. figures only, never the data.
  │   narrative   │   falls back to a deterministic writer
  └───────┬───────┘
          ▼
   deck · dashboard · briefing   all from one result object
```

### The two rules that decide most of the design

**Derived metrics are computed after aggregation.** Summing per-row
`clicks / impressions` gives the sum of daily ratios, which is not the period
CTR and is not a quantity anyone wants. With 40 clicks over 300 impressions in a
segment, the answer is `0.133`, not `0.10 + 0.15 = 0.25`.

**Undefined is not zero, and it is not a hundred.** Growth from a zero baseline
has no percentage. Reporting `+100%` invents a fact and then ranks on it, which
is how a tiny segment ends up as the headline.

### How ranking works

```
impact = contribution_share × priority_weight × materiality
```

| Factor | What it is | Why |
| --- | --- | --- |
| `contribution_share` | The segment's share of the metric's total absolute movement | Makes scores comparable across metrics in different units. Multiplying raw deltas means whatever is denominated in impressions always wins |
| `priority_weight` | Position in `kpi_priority`, decaying as `1/(1+rank)` | You know which metric matters; the tool should not have to guess |
| `materiality` | `sqrt` of the segment's share of the current total | Damps the classic false headline: three impressions becoming nine is +200% and means nothing. `sqrt` damps rather than eliminates, so a small-but-real segment can still surface |

Documented in [`engine/insights.py`](src/insight_engine/engine/insights.py), on
the deck's methodology slide, and in the dashboard. If you cannot explain a
ranking, nobody should trust it.

---

## Features

### Analysis
- Period-over-period comparison with overlap and coverage validation
- Per-metric aggregation: `sum`, `avg`, `min`, `max`, `median`, `count`, `count_distinct`
- Derived metrics over a safe arithmetic grammar — parentheses, multiple terms,
  `abs`, `min`, `max`, `coalesce`, `safe_div`, `log`, `sqrt`, `round`
- Driver attribution: which segments caused a movement, how much they explain,
  and an explicit flag when gains and losses partly cancel
- New and lost segments detected and labelled
- Metric intent (`higher_is_better`) so cost rising reads as a problem
- Local dataset profiling: column roles and a usable date range, in
  milliseconds, with nothing sent anywhere

### Sources
- CSV and TSV with delimiter sniffing, BOM handling and date-format inference
- SQL queries and database tables (PostgreSQL, MySQL, SQLite, SQL Server)
- Multi-source joins through DuckDB, with a Polars fallback
- `${ENV_VAR}` interpolation so credentials stay out of the spec file

### Output
- Eight-slide PowerPoint deck: cover, executive summary, metric comparison,
  driver breakdown, ranked movements, findings, recommendation, methodology,
  and a QR code to the dashboard
- Shareable dashboard behind an expiring token
- Audio briefing — server-rendered MP3 when configured, otherwise spoken by the
  browser with no egress and no per-report cost
- JSON for anything downstream

### Platform
- CLI and versioned HTTP API running the identical code path
- Asynchronous jobs with honest, pollable progress
- API-key authentication, rate limiting, upload caps, strict security headers
- Structured JSON logs with a correlation id on every record
- Prometheus metrics, health and readiness endpoints, optional OpenTelemetry
- Published JSON Schema for the specification, verified in CI
- Non-root, read-only container with published build provenance

---

## Configuration

Everything is an environment variable prefixed `INSIGHT_ENGINE_`. See
[.env.example](.env.example) for the annotated list and
[docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the specification reference.

The ones that matter most:

| Variable | Default | Notes |
| --- | --- | --- |
| `INSIGHT_ENGINE_ENVIRONMENT` | `development` | `production` enforces the guardrails below |
| `INSIGHT_ENGINE_PUBLIC_BASE_URL` | — | **Required in production.** Share links and QR codes are built from it |
| `INSIGHT_ENGINE_API_KEYS` | — | **Required in production.** Comma-separated; several at once so you can rotate |
| `INSIGHT_ENGINE_DATA_DIR` | `./var` | Decks, audio, sessions, uploads. Mount a volume |
| `INSIGHT_ENGINE_LLM_PROVIDER` | `auto` | `auto`, `gemini`, `openai`, `none` |
| `INSIGHT_ENGINE_OPENAI_BASE_URL` | — | Point at Azure, vLLM, Ollama or LiteLLM |
| `INSIGHT_ENGINE_ALLOW_REMOTE_SQL` | `false` | Off by default; see below |

Running with `INSIGHT_ENGINE_ENVIRONMENT=production` **refuses to start** unless
authentication and a public base URL are set, debug is off, CORS is not a
wildcard, and any enabled remote SQL access carries a host allowlist. A
misconfiguration should be a failed deploy, not a quiet exposure.

---

## Security

The guarantees, the limits and how to report a problem are in
[SECURITY.md](SECURITY.md). In brief:

- Dashboard tokens are 256-bit, stored only as a hash, compared in constant time
- Uploads are written under server-generated ids; client filenames never touch
  the filesystem
- Derived-metric expressions are parsed against an allowlist, never evaluated
- Remote database access is off unless enabled **and** host-allowlisted
- Errors never carry paths, connection strings or stack traces to a client

And the limits, which matter just as much: API keys authenticate, they do not
partition — run one deployment per trust boundary. Dashboard links are bearer
credentials. Generated prose is model output and is not verified.

---

## Documentation

| | |
| --- | --- |
| [Architecture](docs/ARCHITECTURE.md) | How it is built, and why each layer exists |
| [Configuration](docs/CONFIGURATION.md) | Every specification field, with examples |
| [Deployment](docs/DEPLOYMENT.md) | Docker, Kubernetes, scaling, observability |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | The failures people actually hit |
| [Comparison](docs/COMPARISON.md) | Where other tools fit better |
| [Audit](docs/AUDIT.md) | Every defect fixed in 1.0.0, and how |
| [Decision records](docs/adr/) | Why the close calls went the way they did |
| [Roadmap](docs/ROADMAP.md) | What is planned, and what is out of scope |

---

## Contributing

Contributions are welcome — from typo fixes to connectors. Start with
[CONTRIBUTING.md](CONTRIBUTING.md).

The most valuable report this project receives is **a figure you believe is
wrong**. There is [a template for it](.github/ISSUE_TEMPLATE/wrong_number.yml).

```sh
git clone https://github.com/anubhav-n-mishra/Automated-ai-insight-system.git
cd Automated-ai-insight-system
make install
make check     # lint, strict types, tests
make demo      # a real report, no API key needed
```

Governance is documented in [GOVERNANCE.md](GOVERNANCE.md). This project is
looking for maintainers; see [MAINTAINERS.md](MAINTAINERS.md).

---

## Licence

Copyright 2025 Anubhav Mishra.

Licensed under the [Apache License 2.0](LICENSE).

You may use, modify and redistribute this software, including commercially.
Section 4 of the licence requires that you **retain the copyright,
[NOTICE](NOTICE) and attribution notices** in any redistribution or derivative
work, and state what you changed. The licence also grants an explicit patent
licence and terminates it for anyone who initiates patent litigation over the
software.
