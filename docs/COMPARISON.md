# Where this fits

An honest account of the landscape, written after reading the source of the
open-source tools in it. Most of the time one of them is a better answer than
this one, and saying so is more useful than a feature grid.

## The short version

| If you want to… | Use |
| --- | --- |
| Explore data, build charts, share dashboards | [Metabase](https://github.com/metabase/metabase), [Apache Superset](https://github.com/apache/superset) |
| Define metrics once for your whole stack | [Cube](https://github.com/cube-js/cube), [dbt](https://github.com/dbt-labs/dbt-core) MetricFlow |
| BI as code, on top of dbt | [Lightdash](https://github.com/lightdash/lightdash), [Evidence](https://github.com/evidence-dev/evidence) |
| Sub-second exploration of event data | [Rill](https://github.com/rilldata/rill) |
| Detect anomalies in a KPI over time | [Prophet](https://github.com/facebook/prophet), [Merlion](https://github.com/salesforce/Merlion), [ADTK](https://github.com/arundo/adtk) |
| Root-cause analysis for infrastructure incidents | [PyRCA](https://github.com/salesforce/PyRCA), [DoWhy](https://github.com/py-why/dowhy) |
| Profile a dataset you have never seen | [ydata-profiling](https://github.com/ydataai/ydata-profiling) |
| Ask questions of your data in English | [Vanna](https://github.com/vanna-ai/vanna), [PandasAI](https://github.com/sinaptik-ai/pandas-ai) |
| **Answer "what changed since last week and why", the same way every week, as a document you can send** | This |

## What was learned from each

### Metabase X-Rays

Metabase's automatic dashboards are driven by declarative YAML "dashboard
templates". Each names `dimensions`, `metrics`, `filters`, `groups` and `cards`,
and every card carries a **`score`** used to rank which visualisations are worth
showing. `GenericTable.yaml` alone defines fifteen dimension types and sixty-odd
scored cards.

**Taken from it:** the idea that "what matters" should be a declared, scored
decision rather than a heuristic buried in code. This project's impact score is
a direct descendant, with the difference that it is normalised so scores are
comparable across metrics in different units, and documented in three places
where a reader will meet it.

**Where it is stronger:** breadth. X-Rays work on any table with no
configuration at all. This tool asks you to name what matters first — which is
the trade for being able to run the same report every week and diff it.

### Chaos Genius

An open-source business-observability platform (now archived) whose "DeepDrills"
feature searched multi-dimensional drilldowns to find the key drivers of a KPI
change, using statistical filtering and A\*-like path search across
high-cardinality dimensions. Flask, Celery workers, Postgres.

**Taken from it:** driver attribution as a first-class output rather than a
footnote, and the framing that the interesting question is *which segment caused
this*, not *what is the total*.

**Where it was stronger:** the search itself. Chaos Genius explored dimension
*subsets*; this project crosses the configured dimensions into a single grid and
ranks within it. Hierarchical drill-down is on the [roadmap](ROADMAP.md).

Its archival is also a reminder: a platform with an anomaly engine, a scheduler,
an alerting system and a dashboard builder is a large surface to maintain. This
project deliberately does one thing.

### Cube, Lightdash, dbt MetricFlow

The semantic-layer argument: metric definitions belong in version control, once,
feeding every consumer — and increasingly feeding AI agents as well as
dashboards.

**Taken from it:** the specification is a document in your repository, not a
saved view in someone's SaaS. It is reviewable in a pull request, diffable, and
the same file produces the same report in a terminal, in CI and behind the API.
A JSON Schema is published so editors can validate it.

**Where they are stronger:** scope. A real semantic layer governs every metric
in a warehouse and enforces consistency across every tool that reads it. A spec
here describes one report. If you already run Cube or dbt, they own the metric
definitions and this tool should read from them — see the roadmap.

### Evidence and Rill

BI-as-code: SQL and Markdown compiled into a site; sub-second exploration over
event data.

**Taken from it:** reproducibility as a design goal, and the expectation that
output is a document rather than an application session.

**Where they are stronger:** interactivity and presentation. Evidence produces a
far better-looking site than this produces a deck. If your audience will read a
web page, use Evidence.

### PyRCA, DoWhy, RCAEval

Rigorous causal machinery for infrastructure telemetry: causal graphs,
interventions, benchmarked root-cause algorithms.

**Taken from it:** the discipline of separating *contribution* from *cause*.
This project attributes movement arithmetically and says so; it never claims
causality, because summing deltas across segments is decomposition, not
inference.

**Where they are stronger:** everything about causality. If you need to know
whether a change *caused* an outcome, you need one of these, not this.

### ydata-profiling

Deep automated dataset profiling.

**Taken from it:** profile locally, instantly, before asking the user anything.
The previous generation of this tool sent uploaded CSV contents to a hosted
model to classify column headers — slow, billable per upload, non-functional
without an API key, and an unnecessary disclosure of customer data. Local
profiling from dtypes, cardinality, null density and name patterns does the same
job in milliseconds with nothing leaving the deployment.

**Where it is stronger:** depth. ydata-profiling produces correlation matrices,
distribution analysis and interaction plots. This profiler answers exactly one
question: which column is time, which are segments, which are measures.

### Vanna and PandasAI

Natural-language querying: a model writes SQL or pandas against your data.

**Deliberately not taken.** Here the model never sees the data, never writes a
query and never computes anything. It receives figures the engine already
produced and writes sentences about them. The reasons are practical rather than
ideological:

- A wrong number in a weekly report survives longer than a wrong answer in a
  chat, because nobody re-checks a deck.
- Many deployments cannot send business metrics to a third-party model at all.
- Everything must still work with no API key. It does: the deterministic writer
  produces the summary from the same numbers.

## Honest weaknesses

Compared with the tools above, this one:

- has **no ad-hoc exploration**. You write a spec; you do not click around.
- is **single-node**. Jobs, rate limiting and session state are per-process.
- **materialises the dataset in memory**. Polars is efficient, but this is not a
  warehouse-native tool that pushes computation down.
- has **no scheduler, alerting or anomaly detection**. Run it from cron, Airflow
  or GitHub Actions.
- **crosses dimensions into one grid** rather than searching dimension subsets.
- offers **no statistical significance testing**. Rankings are a shortlist, not
  a verdict, especially on small segments.

## When this is the right tool

- A recurring comparison — week over week, month over month — that somebody
  currently assembles by hand.
- The deliverable is a document a stakeholder receives, not a dashboard they
  visit.
- You need to show your working, because someone will disagree with a number.
- You want the report definition in git, reviewed like code.
- You cannot, or would rather not, send business data to a model provider.

## When it is not

- You want to explore and follow a hunch. Use Metabase.
- You need one metric definition governing every tool. Use Cube or dbt.
- You need to know whether something *caused* an outcome. Use DoWhy.
- You want a beautiful interactive site. Use Evidence.
- You have one number and want to know when it looks abnormal. Use Prophet or
  Merlion.

---

*Assessments reflect these projects as of September 2026 and are based on their
public source and documentation. Corrections are welcome — if something here is
out of date or unfair to a project, please open an issue.*
