# Milestone Plan

Each phase ends with: summary, files changed, verification commands, expected results, open issues — then a pause for you to verify.

| Phase | Milestone | Done when (verifiable) | Acceptance criteria |
|---|---|---|---|
| 1 ✅ | Requirements, architecture, setup | `./scripts/check-prereqs.sh` exits 0; repo initialised | — |
| 2 ✅ | FastAPI app + tests | `pytest` green; all 7 endpoints respond; JSON logs; graceful shutdown | 1, 2 |
| 3 ✅ | Docker | Image builds; runs as non-root; `/health` OK in container; Compose up | 3 |
| 4 ✅ | Kubernetes basics | kind cluster; Namespace/Deployment/Service/ConfigMap applied; port-forward works; pod-delete self-heals | 4 |
| 5 ✅ | Probes, limits, HPA | `kubectl top` works; HPA shows real `%`; rolling update + rollback demonstrated | 5, 6, 11 |
| 6 ✅ | Prometheus + Grafana + alerts | Targets `UP`; dashboard shows live data; one alert fires and resolves | 8, 9, 10 |
| 7 ✅ | Load tests + failure drills | k6 results captured; HPA scale-up/down observed; 4 scenarios documented; postmortem | 7, 12 |
| 8 ✅ | CI/CD + security | Actions green on GitHub; Trivy + pip-audit run; `act` works locally | 13, 14 |
| 9 ✅ | Docs + portfolio | README reproducible from a clean clone; screenshots in `docs/images/` | 15 |
| 10 ✅ | Interview prep | 25 Q&A + 5 system-design questions + troubleshooting drills | — |

## Phase details

### Phase 2 — Application

- `app/main.py` — app factory, lifespan (startup/shutdown), readiness flag.
- `app/routes/` — `health.py`, `items.py`, `stats.py`.
- `app/config.py`, `app/models.py`, `app/store.py` — settings, schemas, in-memory store.
- `app/metrics.py` — Prometheus counters/histogram + middleware.
- `app/logging_config.py` — JSON logs to stdout.
- `tests/test_app.py` — **one** consolidated test file covering every endpoint, validation, 404s, metrics and readiness during shutdown.

### Phase 3 — Docker

- Multi-stage `Dockerfile` on `python:3.12-slim`, non-root UID 10001, `exec`-form `CMD` so Uvicorn receives `SIGTERM`.
- `.dockerignore`, `docker-compose.yml` (app + optional Prometheus for local metrics).

### Phase 4–5 — Kubernetes

- `k8s/kind-config.yaml` — 1 control-plane + 2 workers.
- `k8s/base/` — namespace, configmap, deployment, service, hpa, kustomization.
- `k8s/overlays/local/` — image tag, replica tweaks.
- `helm/items-api/` — same resources as a chart.

### Phase 6 — Monitoring

- `monitoring/values-local.yaml` — trimmed kube-prometheus-stack for a laptop.
- `monitoring/prometheus/servicemonitor.yaml` — tells Prometheus to scrape the app.
- `monitoring/alerts/items-api-rules.yaml` — ItemsApiDown, HighErrorRate, HighLatency, AutoscalerAtMax, plus recording rules.
- `monitoring/alerts/items-api-rules.test.yaml` — promtool unit tests (`make test-alerts`).
- `monitoring/grafana/dashboards/items-api.json` — dashboard as code.

### Phase 7 — Load and failure

- `load-tests/smoke.js`, `ramp.js` (thresholds) and `steady.js` (drill traffic), run in-cluster by `scripts/run-load-test.sh`.
- App gets two **test-only** fault switches (env-controlled): error injection and artificial latency, used for Scenarios B and C.
- `docs/troubleshooting.md`, `docs/incident-postmortem.md`, `docs/load-test-results.md` (observed numbers only).
- `scripts/build-regression-image.sh` builds the broken release for Scenario D.

### Phase 8 — CI/CD and security

- `.github/workflows/ci.yml` — 3 jobs: lint+tests (JUnit, coverage gate), promtool rule tests, build + smoke + pip-audit + Trivy (secret, config, image).
- `requirements.lock` (hash-pinned), `.trivyignore.yaml` (scoped, expiring), `make security` / `make render` / `make lock`.
- `k8s/base/networkpolicy.yaml` — default deny; ingress only from Prometheus and loadtest.
- `docs/security.md`, `docs/ci-cd.md` — controls with evidence, known limits, deployment design.
- First GitHub run (private repo): all 3 jobs green, run 38860472342. Public repo: [https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform](https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform), run 38990703832 green.

### Phase 9 — Documentation and portfolio (done)

- `README.md` rewritten as the entry point: measured results, three Mermaid diagrams, macOS quick start.
- `helm/items-api/` chart plus `make helm-check`: field-by-field equivalence with Kustomize, verified by install, upgrade and rollback.
- `docs/acceptance.md`: the 15 criteria with evidence, including a fresh-clone run of the README steps.
- `docs/portfolio.md`: resume bullets, demo script, interview stories, how to capture evidence.
- An audit gave every monitoring container a memory limit (8 were missing).
- `docs/diagrams.html` refreshed to the final design.
