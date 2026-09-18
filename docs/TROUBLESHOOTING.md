# Troubleshooting

The failures people actually hit, what causes them, and how to fix them.

Start here, whatever the problem:

```sh
insight-engine doctor                      # effective config; prints no secrets
insight-engine validate your-spec.yaml     # what is wrong with the spec
insight-engine profile your-data.csv       # what the engine thinks your columns are
```

---

## The report is empty

### "Neither period contains any rows"

```
error: Neither period contains any rows. The configured date range does not
       overlap the data, which spans 2012-07-30 to 2014-06-29.
```

The message names the range your data actually covers. Set the comparison inside
it. `insight-engine profile` suggests a window anchored to the last day with
data, which is almost always what you want on a historical extract.

### "No segment moved enough to rank"

The analysis ran; nothing cleared the materiality threshold. In order of
likelihood:

1. **The periods are too short.** A single day against a single day rarely moves
   anything by a meaningful share.
2. **`min_segment_share` is too high** for your distribution. The default 0.001
   drops segments below a tenth of a percent of a metric. Lower it if your data
   is genuinely long-tailed and small segments matter.
3. **The dimensions do not vary.** If every row has the same `channel`, there is
   only one segment and nothing to compare between segments.
4. **Nothing changed.** Check the totals; if they are flat, the report is
   correct and boring.

### Every metric reads 0

Your metric columns probably parsed as text. Look at the profile output: if a
numeric column shows `dtype: String`, the file has thousands separators,
currency symbols or a decimal comma. Clean it at the source, or read it through
a SQL source with an explicit cast.

---

## A figure looks wrong

This is the most important class of problem, and
[there is a template for it](../.github/ISSUE_TEMPLATE/wrong_number.yml). Before
filing, three things explain most reports:

### A ratio does not match a hand calculation

The engine computes ratios **after** aggregation, which is correct and often
differs from a spreadsheet that averaged a per-row column:

```
rows:   clicks  impressions
        10      100          per-row ctr 0.10
        30      200          per-row ctr 0.15

average of per-row ratios : 0.125     ← what a spreadsheet often shows
sum(clicks)/sum(imps)     : 0.133     ← what the engine reports, and what CTR means
```

### A percentage reads `n/a`

The previous period was zero. Growth from nothing has no percentage. The
absolute values are still shown, and the segment is labelled `new`.

### A total does not match your warehouse

In order of likelihood: the date column is not the one you think (check the
profile output for a second date-like column), the window boundaries are
inclusive at both ends here and may not be in your query, a join dropped rows
(secondary sources are LEFT JOINed onto the primary), or a row cap truncated the
read — which appears as a warning on the report.

---

## Specification errors

### "current and previous periods overlap"

A period compared against part of itself is not a comparison. Both windows are
inclusive, so `current_start` must be strictly after `previous_end`.

### "kpi_priority references undefined metrics"

A name in `kpi_priority` must match a `metrics[].name` or a
`derived_metrics[].name`, not a source column name. If the metric renames a
column with `column:`, use the `name`.

### "a column cannot be both a dimension and a metric"

Grouping by the column you are summing produces one row per distinct value and a
meaningless comparison. Pick one role.

### "Expression element Attribute is not allowed"

Derived-metric expressions are arithmetic over metric names. No attribute
access, no function calls beyond the documented set, no comprehensions. See
[CONFIGURATION.md](CONFIGURATION.md#derived_metrics).

### "only read-only queries are allowed in a source"

The query must be a single statement beginning `SELECT` or `WITH`. Semicolons
separating statements are rejected. This catches the common accident of pasting
a migration into a reporting spec; the real control is a read-only database role.

---

## Data source problems

### "Data file not found"

Paths resolve relative to the spec file, or to `--base-path`. From the CLI,
`../data/events.csv` is fine. Over HTTP, paths are confined to the upload
directory — reference the upload, not a server path.

### "Could not parse … as delimited text"

Delimiter sniffing failed. Set it explicitly:

```yaml
sources:
  events:
    delimiter: ";"
```

If the file is really a spreadsheet renamed to `.csv`, the upload endpoint says
so — export it as CSV.

### "Could not interpret 'date' as dates"

The engine tries `YYYY-MM-DD`, `DD/MM/YYYY`, `MM/DD/YYYY`, `YYYY/MM/DD`,
`DD-MM-YYYY` and `YYYYMMDD` before inferring. If your format is none of these,
cast it in a SQL source or normalise the file. `YYYY-MM-DD` always works.

Note that `03/04/2025` is genuinely ambiguous. The engine prefers `DD/MM/YYYY`
after ISO; if that is wrong for your data, say so explicitly.

### "Date column holds numbers, not dates"

A year column is a period label, not a time axis. Use it as a dimension and
point `date_column` at a real date.

### "Remote SQL sources are disabled"

Deliberate. Set `INSIGHT_ENGINE_ALLOW_REMOTE_SQL=true` and list the hosts in
`INSIGHT_ENGINE_ALLOWED_SQL_HOSTS`. In production, the allowlist is required.

### "Relational sources need the 'sql' extra"

```sh
pip install "insight-engine[sql,postgres]"
```

---

## API problems

### 401 on every request

`INSIGHT_ENGINE_API_KEYS` is set, so the `X-API-Key` header is required.
`/health` stays open.

### 404 on a dashboard link

Three causes, all reported as 404 so the endpoint cannot be used to enumerate
sessions: the session expired (72 hours by default), the token is wrong, or the
link was truncated. Query strings are easy to lose when a link is pasted into
chat — check the `?token=` survived.

### 413 on upload

Larger than `INSIGHT_ENGINE_MAX_UPLOAD_BYTES`. Raise it, and raise
`client_max_body_size` on any proxy in front to match.

### 429

Rate limited. The `Retry-After` header says how long. Raise
`INSIGHT_ENGINE_RATE_LIMIT_REQUESTS`, or set it to `0` to disable.

### A job never finishes

Poll `GET /api/v1/jobs/{id}`; the `stage` field says where it is. If it sits on
`narrating`, the model provider is slow — it will fall back to the template
writer after `LLM_TIMEOUT_SECONDS`. If it sits on `loading`, the source is slow
or very large.

### The server refuses to start in production

The message lists every problem at once:

```
error: Unsafe production configuration: INSIGHT_ENGINE_API_KEYS must be set in
production; INSIGHT_ENGINE_PUBLIC_BASE_URL must be set in production
```

This is intentional. See
[CONFIGURATION.md](CONFIGURATION.md#production-guardrails).

---

## Output problems

### The QR code points at localhost

`INSIGHT_ENGINE_PUBLIC_BASE_URL` is unset. Production refuses to start without
it for exactly this reason.

### The narrative is generic

No model provider is configured, so the deterministic writer produced it. That
is fully functional and always factually correct about the numbers; it is just
not prose. Configure a provider, or point at a self-hosted model:

```sh
INSIGHT_ENGINE_LLM_PROVIDER=openai
INSIGHT_ENGINE_OPENAI_BASE_URL=http://vllm.internal:8000/v1
```

`insight-engine doctor` reports which writer is active.

### The audio briefing does not play

With no `MURF_API_KEY`, the dashboard speaks the briefing with the Web Speech
API. Some browsers block speech until the user has interacted with the page —
the play button counts. Safari needs a voice installed. The transcript is always
available under "Read the briefing instead".

### The deck opens but a chart is missing

The comparison chart excludes ratio metrics, because plotting a value of `0.028`
against one of `2,497,000` makes both unreadable. Ratios appear in the totals
and in the insight table.

---

## Performance

### Reports are slow

Check `insight_engine_stage_seconds{stage}` to see which stage.

| Slow stage | Usual cause |
| --- | --- |
| `ingest` | Large file, or an unfiltered SQL source. Push the date range into the query |
| `aggregate` | High dimension cardinality. 50 × 10 × 5 is 2,500 segments |
| `narrate` | Provider latency. Bounded by `LLM_TIMEOUT_SECONDS` |
| `rank` | Very many segments; lower `top_insights` or raise `min_segment_share` |

### The process is killed

Out of memory. The dataset is materialised in full. Lower `MAX_ROWS`, lower
`WORKER_THREADS`, filter at the source, or raise the limit. Budget roughly 2 GB
per concurrent report at 10M rows.

### Reports queue up

`insight_engine_jobs_queued` climbing means the pool is saturated. Raise
`WORKER_THREADS` if memory allows, or scale up.

---

## Development

### `make check` fails on a clean clone

```sh
python --version        # 3.10 or newer
make clean && make install && make check
```

### Tests fail with a Starlette TestClient import error

Newer Starlette prefers `httpx2`:

```sh
pip install httpx2
```

### `mypy` fails on code you did not touch

The strict overrides for untyped third-party libraries are in `pyproject.toml`
with a comment explaining each. If a library changed its typing, update the
override rather than loosening the global setting.

---

## Still stuck

Open an issue with:

1. `insight-engine doctor` output
2. The specification, credentials removed
3. Representative rows — **synthetic, not real customer data**
4. What you expected and what you got
5. The `request_id`, if it came through the API

[Bug report](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=bug_report.yml) ·
[A figure looks wrong](https://github.com/anubhav-n-mishra/Automated-ai-insight-system/issues/new?template=wrong_number.yml) ·
[Security](../SECURITY.md)
