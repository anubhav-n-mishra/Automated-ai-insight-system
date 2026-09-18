# Configuration reference

Two things are configured separately, and keeping them apart is deliberate:

- **The specification** — what to analyse. A YAML document that lives in your
  repository next to the data it describes. Reviewable, diffable, reproducible.
- **The deployment** — how the service runs. Environment variables. Contains the
  secrets; never in the specification.

A [JSON Schema](../schemas/analysis-spec-v1.json) is published for the
specification, so most editors will validate and autocomplete it.

---

## Part 1: the specification

### Minimal

```yaml
dataset:
  primary_source: events
  sources:
    events:
      type: csv
      path: data/events.csv
      date_column: date
      metrics:
        - name: revenue

report:
  date_column: date
  comparison:
    current_start: 2025-06-08
    current_end: 2025-06-14
    previous_start: 2025-06-01
    previous_end: 2025-06-07
```

`insight-engine validate spec.yaml` checks it without reading a byte of data.

### `dataset`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `primary_source` | string | yes | Which source anchors the join |
| `sources` | map | yes | At least one |

#### CSV source

```yaml
sources:
  events:
    type: csv
    path: data/events.csv        # relative to the spec file, or --base-path
    delimiter: ","               # optional; sniffed when omitted
    encoding: utf-8
    null_values: ["", "NA", "N/A", "null", "NULL"]
    date_column: event_date
    dimensions: [country, channel]
    metrics:
      - name: revenue
        unit: currency
```

Delimiter sniffing handles comma, semicolon, tab and pipe. A UTF-8 BOM is
stripped from the header, which otherwise produces a first column literally
named `﻿ID` that fails every subsequent lookup.

#### SQL source

```yaml
sources:
  orders:
    type: sql
    connection_string: "postgresql://reporting:${DB_PASSWORD}@warehouse:5432/analytics"
    query: |
      SELECT order_date, country, channel, revenue, units
      FROM analytics.orders
      WHERE order_date >= '2025-01-01'
    date_column: order_date
    dimensions: [country, channel]
    metrics:
      - name: revenue
        unit: currency
      - name: units
```

`${ENV_VAR}` is resolved at read time, so the spec can live in version control.
An unset variable is an error rather than an empty string, because an empty
password otherwise produces a confusing anonymous connection attempt.

Queries must be a single read statement beginning `SELECT` or `WITH`. This is
defence in depth, not a security boundary — **use a read-only database role**.

Remote sources also require the deployment to enable them; see
[`INSIGHT_ENGINE_ALLOW_REMOTE_SQL`](#relational-source-policy).

#### Database table source

```yaml
sources:
  customers:
    type: database
    driver: postgresql            # postgresql | mysql | sqlite | mssql
    host: warehouse.internal
    port: 5432
    database: analytics
    schema_name: public
    table: customers
    username: reporting
    password: ${DB_PASSWORD}
    date_column: signup_date
    dimensions: [plan, region]
    metrics:
      - name: mrr
        unit: currency
```

Only the columns the spec names are selected, and the row cap is applied in the
database rather than in Python.

#### Multiple sources

```yaml
dataset:
  primary_source: impressions
  sources:
    impressions:
      type: csv
      path: data/impressions.csv
      date_column: date
      dimensions: [campaign, geo]
      metrics: [{name: impressions}]
    conversions:
      type: csv
      path: data/conversions.csv
      date_column: date
      join_keys: [date, campaign, geo]
      dimensions: [campaign, geo]
      metrics: [{name: conversions}, {name: revenue, unit: currency}]
```

Secondary sources are LEFT JOINed onto the primary through DuckDB, with a Polars
fallback. Declare `join_keys` explicitly; without it the engine joins on
whatever dimensions and date column the sources happen to share, which is
usually right and occasionally surprising.

### `metrics`

```yaml
metrics:
  - name: revenue          # the name used everywhere else
    column: rev_usd        # source column, if it differs
    aggregation: sum       # sum | avg | min | max | median | count | count_distinct
    label: Revenue         # human-facing name in reports
    unit: currency         # count | currency | percent | ratio | duration_seconds
    format_precision: 2
    higher_is_better: true
```

**`aggregation` matters.** Summing an average, a ratio or a distinct count
produces a number with no meaning. Choosing it is the single most common
modelling mistake, so it is explicit rather than inferred:

| Data | Aggregation |
| --- | --- |
| Revenue, spend, clicks, units | `sum` |
| Household income, session duration, rating | `avg` |
| Distinct customers, distinct sessions | `count_distinct` |
| Number of rows | `count` |
| Latency, price (skewed distributions) | `median` |

**`higher_is_better` matters too.** Cost per click rising is a problem; revenue
rising is a win. Reports colour movements by intent, not by sign, and ranking
uses it to describe a movement as positive or negative.

### `derived_metrics`

Expressions over **aggregated** metrics, computed after the group-by:

```yaml
derived_metrics:
  - name: ctr
    expression: clicks / impressions
    label: Click-through rate
    unit: percent

  - name: margin
    expression: (revenue - cost) / revenue
    unit: percent

  - name: revenue_per_customer
    expression: safe_div(revenue, customers)
    unit: currency
```

Available: `+ - * / % **`, parentheses, and `abs`, `min(a,b)`, `max(a,b)`,
`coalesce(a,b)`, `safe_div(a,b)`, `log`, `sqrt`, `round`.

Division by zero yields `null`, not `0`. Zero would assert "the rate was zero"
when the truth is "the rate is undefined", and that lie then propagates into
ranking and into the deck.

Expressions are parsed against an allowlisted grammar and never evaluated as
code, so a spec uploaded over HTTP cannot execute anything.

**A derived metric is never computed per row.** `ctr = clicks / impressions` is
only true when both sides are summed first.

### `report`

```yaml
report:
  title: Paid media, week over week
  date_column: date

  comparison:
    current_start: 2025-11-24
    current_end: 2025-11-30       # inclusive
    previous_start: 2025-11-17
    previous_end: 2025-11-23

  dimensions: [campaign, geo]
  kpi_priority: [revenue, conversions, ctr, cpc]

  top_insights: 20
  min_impact_score: 0.0
  min_segment_share: 0.001
  include_drivers: true
  max_drivers_per_metric: 5
```

| Field | Default | Notes |
| --- | --- | --- |
| `title` | `Performance Analysis` | Deck cover and dashboard heading |
| `date_column` | required | Must exist after joining |
| `comparison` | required | Both windows inclusive; overlap is rejected |
| `dimensions` | `[]` | Crossed into one grid. Empty analyses totals only |
| `kpi_priority` | all metrics | Most important first; drives ranking weight |
| `top_insights` | 20 | How many ranked movements to keep |
| `min_segment_share` | 0.001 | Segments below this share of a metric are excluded before ranking |
| `include_drivers` | true | Decompose each movement across segments |
| `max_drivers_per_metric` | 5 | Drivers listed per metric |

**Overlapping periods are rejected.** A period compared against part of itself
is not a comparison.

**Unequal periods are allowed but warned about.** Month-over-month is
legitimately ragged; the warning appears on the deck, in the dashboard and in
the API response, so nobody reads a 28-versus-31-day delta as like-for-like.

**`min_segment_share` is the guard against false headlines.** Without it, a
segment with three impressions growing to nine is +200% and takes the top slot.
Raise it for noisy long-tail data; lower it when small segments genuinely
matter.

### Choosing dimensions

Dimensions are crossed, so cardinality multiplies: 50 campaigns × 10 countries ×
5 devices is 2,500 segments, most containing almost nothing. Two or three
low-cardinality dimensions produce a far more readable report than five.

`insight-engine profile` reports each column's distinct count for exactly this.

---

## Part 2: deployment

Environment variables, all prefixed `INSIGHT_ENGINE_`. A `.env` file in the
working directory is read automatically. [`.env.example`](../.env.example) is
the annotated list.

### Runtime

| Variable | Default | |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development`, `staging`, `production` |
| `DEBUG` | `false` | |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `json` or `text` |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | |

### Public identity

| Variable | Default | |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | — | **Required in production.** The origin users reach. QR codes and share links are built from it |
| `CORS_ALLOW_ORIGINS` | `[]` | Comma-separated. Never `*` in production |

Without `PUBLIC_BASE_URL`, links point at the server's own host — a QR code that
resolves to `localhost` is useless the moment it leaves the machine.

### Authentication and limits

| Variable | Default | |
| --- | --- | --- |
| `API_KEYS` | — | **Required in production.** Comma-separated; several at once so you can rotate without downtime |
| `RATE_LIMIT_REQUESTS` | `60` | Per window, per principal. `0` disables |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | |
| `MAX_UPLOAD_BYTES` | `67108864` | 64 MB |
| `MAX_ROWS` | `5000000` | Per source |
| `WORKER_THREADS` | `4` | Concurrent reports |

Generate a key:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Rotation: add the new key, deploy, remove the old one.

### Storage

| Variable | Default | |
| --- | --- | --- |
| `DATA_DIR` | `./var` | Decks, audio, sessions, uploads. Mount a volume |
| `SESSION_TTL_HOURS` | `72` | How long a share link works |
| `ARTIFACT_TTL_HOURS` | `168` | How long a deck stays downloadable |
| `CLEANUP_INTERVAL_SECONDS` | `900` | Sweep frequency |

### Narrative

| Variable | Default | |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` | `auto`, `gemini`, `openai`, `none` |
| `LLM_MODEL` | per provider | |
| `GEMINI_API_KEY` | — | |
| `OPENAI_API_KEY` | — | |
| `OPENAI_BASE_URL` | OpenAI | Any OpenAI-compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | `45` | |
| `LLM_MAX_RETRIES` | `2` | |
| `LLM_CACHE_SIZE` | `256` | Cached responses |

With no provider the summary is written deterministically from the computed
figures. That is the right default for anywhere business metrics cannot leave
the deployment, and it is why the tool has no hard dependency on a model.

Self-hosted models work by pointing at them:

```sh
INSIGHT_ENGINE_LLM_PROVIDER=openai
INSIGHT_ENGINE_OPENAI_BASE_URL=http://vllm.internal:8000/v1
INSIGHT_ENGINE_OPENAI_API_KEY=unused-but-required
INSIGHT_ENGINE_LLM_MODEL=meta-llama/Llama-3.3-70B-Instruct
```

### Relational source policy

| Variable | Default | |
| --- | --- | --- |
| `ALLOW_REMOTE_SQL` | `false` | Whether callers may supply connection details |
| `ALLOWED_SQL_HOSTS` | `[]` | Hostname allowlist. Empty allows any host |
| `ALLOWED_SQL_DRIVERS` | all four | |
| `SQL_STATEMENT_TIMEOUT_SECONDS` | `60` | |

Off by default because an engine that connects wherever a request tells it to is
a server-side request forgery primitive against everything it can reach. In
production, enabling it without a host allowlist is refused at startup.

### Telemetry

| Variable | Default | |
| --- | --- | --- |
| `METRICS_ENABLED` | `true` | `/metrics` in Prometheus format |
| `TRACING_ENABLED` | `false` | Needs the `otel` extra |
| `OTLP_ENDPOINT` | — | |
| `SERVICE_NAME` | `insight-engine` | |

### Production guardrails

With `ENVIRONMENT=production` the service **refuses to start** unless:

- `API_KEYS` is set
- `PUBLIC_BASE_URL` is set
- `DEBUG` is off
- `CORS_ALLOW_ORIGINS` is not `*`
- `ALLOW_REMOTE_SQL`, if enabled, has a non-empty `ALLOWED_SQL_HOSTS`

A misconfiguration should be a failed deploy, not a quiet exposure. The error
names every problem at once, so you fix them in one pass.

---

## Worked examples

### Marketing, week over week

[`examples/configs/marketing-csv.yaml`](../examples/configs/marketing-csv.yaml)
— three campaigns, two regions, derived CTR and CPC, with cost marked
`higher_is_better: false`.

### Customer cohort, half-year over half-year

[`examples/configs/customer-campaign-csv.yaml`](../examples/configs/customer-campaign-csv.yaml)
— a semicolon-delimited historical extract, mixed aggregations (`avg` for
income, `count_distinct` for customers), and unequal periods that raise a
warning rather than an error.

Run either:

```sh
insight-engine run examples/configs/marketing-csv.yaml
```
