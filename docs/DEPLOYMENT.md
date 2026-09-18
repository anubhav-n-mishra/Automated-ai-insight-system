# Deployment

Running Insight Engine somewhere that matters.

- [Before you start](#before-you-start)
- [Docker](#docker)
- [Kubernetes](#kubernetes)
- [Behind a reverse proxy](#behind-a-reverse-proxy)
- [Scheduled reports](#scheduled-reports)
- [Sizing](#sizing)
- [Scaling out](#scaling-out)
- [Observability](#observability)
- [Backup and retention](#backup-and-retention)
- [Upgrades](#upgrades)
- [Production checklist](#production-checklist)

---

## Before you start

Two facts about the trust model, because they change how you deploy it:

**API keys authenticate, they do not partition.** Anyone with a valid key can
generate a report from any source the deployment can reach. Run one deployment
per trust boundary, not one shared deployment with a key per team.

**Dashboard links are bearer credentials.** Anyone holding the link can read
that report until it expires. Terminate TLS, and treat a share link like a
password.

---

## Docker

### Quick

```sh
docker run -d --name insight-engine \
  -p 8000:8000 \
  -v insight-data:/var/lib/insight-engine \
  -e INSIGHT_ENGINE_ENVIRONMENT=production \
  -e INSIGHT_ENGINE_PUBLIC_BASE_URL=https://insights.example.com \
  -e INSIGHT_ENGINE_API_KEYS="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  ghcr.io/anubhav-n-mishra/automated-ai-insight-system:1
```

Pin to a minor (`:1.0`) or a digest in production. `:1` tracks the major.

### Compose

```sh
cp .env.example .env    # edit it
docker compose -f deploy/docker-compose.yml up -d
```

The shipped compose file runs read-only, non-root, with all capabilities
dropped, `no-new-privileges`, a tmpfs for `/tmp` and a memory ceiling. The data
volume is the only writable path.

### Building

```sh
make docker
```

With optional extras:

```sh
docker build -f deploy/Dockerfile --build-arg EXTRAS="[sql,postgres]" -t insight-engine:pg .
```

Extras are opt-in so the default image stays small and pulls in no database
drivers you will not use.

---

## Kubernetes

No Helm chart yet ([contributions welcome](../CONTRIBUTING.md)). These manifests
are complete and known to work.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: insight-engine
type: Opaque
stringData:
  INSIGHT_ENGINE_API_KEYS: "replace-me,rotating-key"
  INSIGHT_ENGINE_OPENAI_API_KEY: ""
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: insight-engine
data:
  INSIGHT_ENGINE_ENVIRONMENT: "production"
  INSIGHT_ENGINE_PUBLIC_BASE_URL: "https://insights.example.com"
  INSIGHT_ENGINE_LOG_FORMAT: "json"
  INSIGHT_ENGINE_DATA_DIR: "/var/lib/insight-engine"
  INSIGHT_ENGINE_WORKER_THREADS: "4"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: insight-engine-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: insight-engine
spec:
  # Jobs and sessions are per-process, so a second replica would serve 404 for
  # a session the first one created. See "Scaling out" below.
  replicas: 1
  strategy:
    type: Recreate          # ReadWriteOnce volume; no two pods at once
  selector:
    matchLabels: {app: insight-engine}
  template:
    metadata:
      labels: {app: insight-engine}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: "/metrics"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
        seccompProfile: {type: RuntimeDefault}
      containers:
        - name: insight-engine
          image: ghcr.io/anubhav-n-mishra/automated-ai-insight-system:1.0.0
          ports: [{containerPort: 8000, name: http}]
          envFrom:
            - configMapRef: {name: insight-engine}
            - secretRef: {name: insight-engine}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: [ALL]}
          volumeMounts:
            - {name: data, mountPath: /var/lib/insight-engine}
            - {name: tmp, mountPath: /tmp}
          resources:
            requests: {cpu: "500m", memory: "512Mi"}
            limits:   {cpu: "2",    memory: "2Gi"}

          # Liveness only asks "is the process alive". Restarting a pod that is
          # merely busy would kill in-flight reports.
          livenessProbe:
            httpGet: {path: /health, port: http}
            initialDelaySeconds: 10
            periodSeconds: 30
            timeoutSeconds: 5

          # Readiness asks "can this instance actually serve a report".
          readinessProbe:
            httpGet: {path: /health/ready, port: http}
            initialDelaySeconds: 5
            periodSeconds: 10

          startupProbe:
            httpGet: {path: /health, port: http}
            failureThreshold: 30
            periodSeconds: 2
      volumes:
        - name: data
          persistentVolumeClaim: {claimName: insight-engine-data}
        - name: tmp
          emptyDir: {medium: Memory, sizeLimit: 128Mi}
      terminationGracePeriodSeconds: 60   # let running reports finish
---
apiVersion: v1
kind: Service
metadata:
  name: insight-engine
spec:
  selector: {app: insight-engine}
  ports: [{port: 80, targetPort: http, name: http}]
```

`terminationGracePeriodSeconds: 60` matters: the job manager drains its pool on
shutdown, and a shorter grace period kills reports mid-render.

### Network policy

Default-deny egress, then allow only what the deployment actually needs:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: insight-engine
spec:
  podSelector:
    matchLabels: {app: insight-engine}
  policyTypes: [Ingress, Egress]
  ingress:
    - from: [{podSelector: {matchLabels: {app: ingress-nginx}}}]
      ports: [{port: 8000}]
  egress:
    - to: [{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}}]
      ports: [{port: 53, protocol: UDP}]
    # Only if you enabled remote SQL sources.
    - to: [{ipBlock: {cidr: 10.0.4.0/24}}]
      ports: [{port: 5432}]
    # Only if you configured a model provider.
    - to: [{ipBlock: {cidr: 0.0.0.0/0, except: [169.254.169.254/32, 10.0.0.0/8]}}]
      ports: [{port: 443}]
```

Excluding `169.254.169.254` blocks the cloud metadata endpoint, which is the
first thing an SSRF attempt reaches for.

---

## Behind a reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name insights.example.com;

    ssl_certificate     /etc/letsencrypt/live/insights.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/insights.example.com/privkey.pem;

    # Dashboard links carry tokens in the query string. Do not log them.
    access_log /var/log/nginx/insight.log combined;
    set $sanitised_uri $uri;

    client_max_body_size 64m;         # match INSIGHT_ENGINE_MAX_UPLOAD_BYTES

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Report generation is queued, so requests are short. Uploads of large
        # files are the long ones.
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

Set `INSIGHT_ENGINE_PUBLIC_BASE_URL=https://insights.example.com` so links match
the proxy's origin, not the container's.

The shipped rate limiter keys anonymous callers by peer address, which behind a
proxy is the proxy. Either require API keys, or put a real limiter at the
ingress:

```nginx
limit_req_zone $binary_remote_addr zone=insight:10m rate=30r/m;
location /api/ { limit_req zone=insight burst=10 nodelay; ... }
```

---

## Scheduled reports

The engine has no scheduler, deliberately. Use the one you already run.

**cron**

```cron
0 9 * * 1 cd /srv/reports && /opt/insight/bin/insight-engine run weekly.yaml --json >> /var/log/insight.jsonl 2>&1
```

**GitHub Actions**

```yaml
name: Weekly report
on:
  schedule: [{cron: "0 9 * * 1"}]
  workflow_dispatch:

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "insight-engine[sql,postgres]"
      - run: insight-engine run reports/weekly.yaml
        env:
          DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
          INSIGHT_ENGINE_OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: weekly-report
          path: var/reports/*.pptx
```

**Airflow**

```python
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

KubernetesPodOperator(
    task_id="weekly_report",
    image="ghcr.io/anubhav-n-mishra/automated-ai-insight-system:1.0.0",
    cmds=["insight-engine"],
    arguments=["run", "/specs/weekly.yaml", "--json"],
    env_vars={"INSIGHT_ENGINE_LOG_FORMAT": "json"},
)
```

Rolling windows are easy to generate, because the spec is just a file:

```sh
python - <<'PY' > weekly.yaml
from datetime import date
from insight_engine.domain.spec import ComparisonSpec
import yaml, pathlib

spec = yaml.safe_load(pathlib.Path("weekly.template.yaml").read_text())
window = ComparisonSpec.trailing(anchor=date.today(), days=7)
spec["report"]["comparison"] = {
    "current_start": window.current_start, "current_end": window.current_end,
    "previous_start": window.previous_start, "previous_end": window.previous_end,
}
print(yaml.safe_dump(spec, sort_keys=False))
PY
```

---

## Sizing

Memory is the constraint. The dataset is materialised in full.

| Rows | Columns | Peak RSS | Report |
| --- | --- | --- | --- |
| 10K | 10 | ~150 MB | under 1s |
| 1M | 10 | ~400 MB | 2–5s |
| 10M | 10 | ~2.5 GB | 20–60s |
| 10M | 50 | ~8 GB | 60s+ |

Measured on one core with no model provider configured. A provider call adds
1–5 seconds; rendering adds roughly a second.

Rules of thumb:

- Budget roughly **2 GB per concurrent report** at 10M rows.
- `WORKER_THREADS × per-report memory` must fit inside the container limit. The
  default of 4 assumes datasets in the low millions.
- Use `MAX_ROWS` as the hard guard. Truncation is reported as a warning on the
  report rather than failing it.
- Push filtering into the source. A SQL source with a `WHERE` clause on the date
  range beats loading three years and slicing two weeks out of it.

---

## Scaling out

Three pieces of state are per-process today: the job registry, the rate limiter,
and the session cache. Running two replicas without addressing them means a
client polls a job the other pod is running, and a dashboard link 404s on the
pod that did not create it.

**Until a shared backend exists** ([ADR 0003](adr/0003-in-process-job-execution.md)),
the options that work:

1. **Scale vertically.** Raise `WORKER_THREADS` and the memory limit. One pod
   with eight workers handles a lot of weekly reports.
2. **Sticky sessions** at the ingress, keyed on the session id path segment.
   Workable, fragile on rollout.
3. **Split the workloads.** Run report generation from a scheduler with the CLI
   and keep the HTTP service for reads only.
4. **Implement `SessionStore` on Redis** and swap it in — the protocol exists
   for this, and it is the smallest useful contribution anyone could make here.

---

## Observability

### Metrics

Scrape `/metrics`. The alerts worth having:

```yaml
groups:
  - name: insight-engine
    rules:
      - alert: InsightEngineReportsFailing
        expr: |
          rate(insight_engine_jobs_total{outcome="failed"}[15m])
          / rate(insight_engine_jobs_total{outcome="submitted"}[15m]) > 0.1
        for: 10m
        annotations:
          summary: "More than 10% of reports are failing"

      - alert: InsightEngineQueueBacklog
        expr: insight_engine_jobs_queued > 20
        for: 5m
        annotations:
          summary: "Reports are queueing; raise WORKER_THREADS or scale up"

      - alert: InsightEngineNarrativeProviderDown
        expr: rate(insight_engine_llm_errors_total[15m]) > 0.5
        for: 15m
        annotations:
          summary: "Narrative provider failing; reports fall back to the template writer"

      - alert: InsightEngineNotReady
        expr: up{job="insight-engine"} == 0
        for: 5m
```

The narrative alert is informational, not paging: reports still succeed, they
just read differently.

### Logs

One JSON object per line, with `request_id` on every record including from
worker threads.

```sh
kubectl logs -l app=insight-engine | jq -c 'select(.level=="ERROR")'
kubectl logs -l app=insight-engine | jq -c 'select(.request_id=="ab12…")'
```

Known credential key names are redacted by the formatter. Do not rely on that as
a substitute for not logging secrets.

### Tracing

```sh
pip install "insight-engine[otel]"
export INSIGHT_ENGINE_TRACING_ENABLED=true
export INSIGHT_ENGINE_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
```

---

## Backup and retention

The data directory holds generated decks, audio, session records and staged
uploads. Everything in it is regenerable from the specification and the source
data, so this is a convenience backup, not a disaster-recovery one.

Retention is enforced automatically: sessions expire after
`SESSION_TTL_HOURS`, artifacts after `ARTIFACT_TTL_HOURS`, uploads after six
hours, and a sweeper runs every `CLEANUP_INTERVAL_SECONDS`.

Force a sweep:

```sh
insight-engine purge
```

What actually needs backing up is the **specifications**, and those belong in
git.

---

## Upgrades

This project follows semantic versioning, and **a change to how a number is
computed counts as breaking** even when no signature moves.

```sh
# Read what changed, especially anything under "Fixed / Correctness"
curl -s https://raw.githubusercontent.com/anubhav-n-mishra/Automated-ai-insight-system/main/CHANGELOG.md | head -80

docker pull ghcr.io/anubhav-n-mishra/automated-ai-insight-system:1.1.0
insight-engine validate your-spec.yaml     # schema changes surface here
```

Rolling back is a tag change; the data directory format is forward and backward
compatible within a major version.

---

## Production checklist

Configuration the service enforces itself at startup:

- [ ] `ENVIRONMENT=production`
- [ ] `API_KEYS` set, and rotated on a schedule you have written down
- [ ] `PUBLIC_BASE_URL` matches the origin users actually reach
- [ ] `CORS_ALLOW_ORIGINS` is a list, not `*`
- [ ] `ALLOW_REMOTE_SQL` off, or on with a host allowlist

Everything else, which it cannot:

- [ ] TLS terminated in front; share links carry tokens
- [ ] `DATA_DIR` on a persistent volume, and it is the only writable path
- [ ] Container runs non-root with a read-only root filesystem
- [ ] Memory limit set, and `WORKER_THREADS × per-report memory` fits inside it
- [ ] Default-deny egress; cloud metadata endpoint blocked
- [ ] Database users have read-only access to the specific tables you report on
- [ ] Rate limiting at the ingress if more than one replica
- [ ] `/metrics` scraped, with an alert on failed jobs
- [ ] Log aggregation retains `request_id`
- [ ] Proxy access logs do not record dashboard query strings
- [ ] Image pinned to a digest or a minor version, not `latest`
