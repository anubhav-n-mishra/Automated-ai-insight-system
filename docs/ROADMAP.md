# Roadmap

What is planned, what is being considered, and what will not be built. Plans
change; the "out of scope" section changes rarely, and that is the useful part.

Dates are omitted deliberately. This is volunteer-maintained and a date would be
fiction.

---

## Next

### Statistical significance on segment movements

Ranking currently orders by magnitude, materiality and KPI priority. It does not
say whether a movement is distinguishable from noise. On small segments that
makes the ranking a shortlist rather than a verdict, and the tool should say so
per segment rather than in a caveat.

Likely shape: a confidence interval on each segment's movement, with insights
below a threshold marked rather than hidden — hiding them would be a different
kind of dishonesty.

### Hierarchical driver search

Dimensions are crossed into one grid. A dedicated root-cause tool searches
dimension *subsets*: is this a US problem, a mobile problem, or specifically a
US-mobile problem? Chaos Genius did this with an A\*-like search over
high-cardinality dimensions, and it is the single largest analytical gap here.

Constraint: it must stay explainable. A search that finds the answer but cannot
show why it stopped there is worse than the grid.

### Redis session and job backends

The stores are already protocols. Implementing them on Redis is what unlocks
running more than one replica, and it is the smallest genuinely useful
contribution anyone could make. See
[ADR 0003](adr/0003-in-process-job-execution.md).

### Warehouse connectors

BigQuery, Snowflake, Redshift, ClickHouse. The connector protocol exists; each
is mostly a URL, a driver extra and a test.

---

## Considered

### Reading metric definitions from a semantic layer

If an organisation already defines metrics in dbt or Cube, redefining them here
is duplication that will drift. Reading `dbt` `semantic_models` or Cube's meta
endpoint and generating the metric block would be strictly better than asking
people to copy them.

Open question: how much of the spec is left once metrics come from elsewhere,
and whether that is still a coherent document.

### More output formats

PDF, HTML and Markdown have all been asked for. The renderer takes an
`AnalysisResult` and nothing else, so a second one is additive. PowerPoint is
first because it is what gets forwarded.

### Anomaly detection over a metric's history

"This week is unusual compared with the last twenty" is a different question
from "this week versus last week", and needs history the engine does not
currently keep. If it happens, it happens as an optional store, not as a
requirement.

### Helm chart

The manifests in [DEPLOYMENT.md](DEPLOYMENT.md) work. A chart is packaging, and
worth doing once the multi-replica story is real.

### Slack and email delivery

Currently the answer is "pipe the CLI's output to whatever you already use".
That is a reasonable answer, and a thin notifier might be a better one.

---

## Out of scope

Proposals in these directions will be declined. Each is somebody else's problem,
and they solve it better.

**Ad-hoc exploration.** Chart builders, drag-and-drop pivots, saved views.
Metabase and Superset. This tool answers a question you already decided to ask.

**A semantic layer.** Metric definitions here describe one report. A
warehouse-wide governed metric store is Cube's and dbt's job.

**Scheduling.** Cron, Airflow, GitHub Actions and Kubernetes CronJobs all exist
and all work. A scheduler inside the engine would be a worse version of each.

**Natural-language querying.** The model writes prose about figures the engine
computed. It will not query, will not compute, and will not decide what is
important. A wrong number in a weekly report survives far longer than a wrong
answer in a chat window, because nobody re-checks a deck.

**Causal inference.** This tool decomposes movement arithmetically and says so.
It does not claim causality. DoWhy and PyRCA do that properly.

**Data quality monitoring.** Great Expectations, Soda and dbt tests.

**Multi-tenancy.** API keys authenticate; they do not partition. Making them
partition means a real authorisation model, per-tenant storage and a migration
story, and that is a different product. Run one deployment per trust boundary.

---

## Deliberate non-goals

Things that will not change, because they are the point.

**The engine works with no model provider.** The deterministic writer is not a
degraded mode to be removed once the API keys are set up. Plenty of deployments
cannot send business metrics to a third party, and they get the full tool.

**No figure is ever displayed that the engine did not compute.** No estimates,
no filled-in defaults, no placeholder percentages. If a number cannot be
computed honestly it is reported as unavailable.

**The specification stays a plain document.** No binary format, no database
table, no UI-only state. It is reviewable in a pull request, and that is worth
more than convenience.

**Every ranking stays explainable.** If a scoring change cannot be described in
a paragraph on the methodology slide, it does not ship.

---

## Influencing this

Open an issue. Describe the situation rather than the feature — the best design
is often not the one either of us thought of first. If something on this list
matters to you, say so; priority here follows demonstrated need much more
closely than it follows this document.
