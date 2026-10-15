# Architecture and System Design

## 1. Project in a few lines

- **What it is:** a small FastAPI "items" service, packaged as a container, deployed to a local Kubernetes cluster (kind), watched by Prometheus/Grafana, and scaled automatically by a Horizontal Pod Autoscaler.
- **What it proves:** you can take code from `git push` to a self-healing, self-scaling, observable deployment — and you can debug it when it breaks.
- **What it is not:** a product. The API is deliberately simple so the platform around it is the focus.

## 2. The problem we are solving

A single app process on a single machine has four failure modes that every real team hits:

- **It crashes and stays down** until a human restarts it.
- **It slows down or falls over under traffic spikes**, because capacity is fixed.
- **Nobody knows it is unhealthy** until users complain, because there are no metrics or alerts.
- **Every release is risky**, because there is no automated test gate and no fast rollback.

This project answers each one with a platform mechanism:

| Problem | Mechanism | Where it lives |
|---|---|---|
| Crashes stay down | Deployment + liveness probe restart pods automatically | `k8s/base/deployment.yaml` |
| Fixed capacity | HPA adds/removes replicas based on CPU | `k8s/base/hpa.yaml` |
| No visibility | `/metrics` + Prometheus + Grafana + alerts | `app/metrics.py`, `monitoring/` |
| Risky releases | CI tests/scans every push; rolling update + `kubectl rollout undo` | `.github/workflows/`, `docs/deployment.md` |

## 3. Project specification

### 3.1 Functional requirements

| Endpoint | Purpose | Success | Failure |
|---|---|---|---|
| `GET /health` | Liveness — "the process is alive" | `200 {"status":"ok"}` | process hung → probe times out |
| `GET /ready` | Readiness — "send me traffic" | `200 {"status":"ready"}` | `503` during startup/shutdown |
| `GET /metrics` | Prometheus text exposition | `200 text/plain` | — |
| `GET /api/v1/items` | List items | `200 [...]` | — |
| `POST /api/v1/items` | Create item (validated) | `201 {...}` | `422` invalid body |
| `GET /api/v1/items/{item_id}` | Get one item | `200 {...}` | `404` not found, `422` bad id |
| `GET /api/v1/stats` | Item count, uptime, pod name | `200 {...}` | — |

### 3.2 Non-functional requirements

- **Availability:** survives the loss of any single pod with no failed requests visible to clients (≥2 replicas, readiness gating).
- **Scalability:** HPA scales 2 → 6 replicas when average CPU > 60% of the request (measured: 2→6 in 15 s; back to 2 about 3.5 min after load).
- **Observability:** request count, rate, error rate, p50/p95/p99 latency, pod CPU/memory, replica count — all on one dashboard.
- **Graceful shutdown:** on `SIGTERM`, `/ready` returns 503, in-flight requests finish, then the process exits.
- **Security:** non-root container, read-only root filesystem, dropped Linux capabilities, no secrets in the image, scanned dependencies and image.
- **Cost:** $0 — everything runs on a laptop.

### 3.3 Explicit constraints and limitations

- **In-memory data store.** Items live in a Python dict inside each pod. Data is lost on restart, and each replica has its *own* copy, so a `POST` to pod A is not visible from pod B. This is intentional for the MVP: it keeps the focus on the platform. It is documented as a known limitation and is the first "future improvement" (add Redis or Postgres).
- **Local cluster only.** kind runs Kubernetes nodes as Docker containers. There is no real cloud load balancer; we reach services through `kubectl port-forward` or an ingress controller mapped to localhost.
- **Laptop-sized load.** Load test numbers measure this laptop, not production capacity.

## 4. High-level architecture

```mermaid
flowchart LR
    dev[Developer<br/>macOS] -->|git push| gh[GitHub]
    gh --> ci[GitHub Actions<br/>lint · test · build · scan]

    dev -->|docker build<br/>kind load| kind

    subgraph kind["kind cluster (Docker on your Mac)"]
        direction LR
        subgraph ns_app["namespace: autoscale-app"]
            svc[Service<br/>ClusterIP :80] --> p1[Pod 1<br/>FastAPI :8000]
            svc --> p2[Pod 2<br/>FastAPI :8000]
            svc -.-> pN[Pod N<br/>added by HPA]
            cm[ConfigMap] -.env.-> p1 & p2
            hpa[HPA<br/>min 2 · max 6 · CPU 60%] -.scales.-> deploy[Deployment]
            deploy --> p1 & p2
        end
        subgraph ns_sys["kube-system"]
            ms[metrics-server]
        end
        subgraph ns_mon["namespace: monitoring"]
            prom[Prometheus] --> am[Alertmanager]
            graf[Grafana] --> prom
            ksm[kube-state-metrics]
        end
        ms -->|CPU/memory| hpa
        prom -->|scrape /metrics| p1 & p2
        prom --> ksm
    end

    user[k6 load test / curl] -->|port-forward :8080| svc
    dev -->|browser :3000| graf
```

### Component responsibilities

- **FastAPI app** — serves the API, exposes `/health`, `/ready`, `/metrics`, logs JSON to stdout.
- **Deployment** — keeps N identical pods running; replaces crashed pods; performs rolling updates.
- **Service** — one stable virtual IP + DNS name (`items-api.autoscale-app.svc`) load-balancing across *ready* pods only.
- **ConfigMap** — non-secret settings (log level, app name) injected as env vars, so the same image runs in any environment.
- **metrics-server** — collects pod CPU/memory from kubelets and serves the `metrics.k8s.io` API. **The HPA cannot work without it**, and kind does not ship it.
- **HPA** — every 15s compares average CPU (as % of the pod's CPU *request*) to the target and changes `replicas`.
- **Prometheus** — scrapes app metrics and cluster metrics, stores time series, evaluates alert rules.
- **kube-state-metrics** — exposes Kubernetes object state (desired/available replicas, HPA status) to Prometheus.
- **Grafana** — dashboards on top of Prometheus.
- **Alertmanager** — receives firing alerts from Prometheus, groups and routes them (locally: shown in its UI).

### Where every metric comes from

| Metric | Source | Type |
|---|---|---|
| `http_requests_total{method,path,status}` | App instrumentation (`prometheus_client`) | Counter |
| `http_request_duration_seconds` | App instrumentation | Histogram |
| `http_requests_in_progress` | App instrumentation | Gauge |
| Request rate, error rate | PromQL `rate()` over the counter | Derived |
| p95 latency | PromQL `histogram_quantile()` | Derived |
| `up{job="items-api"}` (availability) | Prometheus itself, per scrape | Built-in |
| `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes` | kubelet / cAdvisor | Infrastructure |
| `kube_deployment_status_replicas_available`, `kube_horizontalpodautoscaler_status_current_replicas` | kube-state-metrics | Infrastructure |
| HPA's own CPU reading | metrics-server (not Prometheus) | Infrastructure |

## 5. Sequence diagrams

### 5.1 A client request (happy path and metrics)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (curl / k6)
    participant PF as kubectl port-forward
    participant S as Service (ClusterIP)
    participant P as Pod (FastAPI)
    participant M as Metrics middleware
    participant DB as In-memory store
    participant PR as Prometheus

    C->>PF: POST /api/v1/items {"name":"pen","price":2.5}
    PF->>S: forward to :80
    S->>P: pick a READY pod (:8000)
    P->>M: request enters middleware (start timer)
    M->>P: call route handler
    P->>P: Pydantic validates body
    alt body invalid
        P-->>M: 422 + field errors
    else body valid
        P->>DB: insert item, assign id
        DB-->>P: item {id:1,...}
        P-->>M: 201 Created
    end
    M->>M: inc http_requests_total{status}<br/>observe duration histogram<br/>write JSON log line
    M-->>C: response
    Note over PR,P: Independently, every 15s
    PR->>P: GET /metrics
    P-->>PR: counters + histogram buckets
```

### 5.2 Autoscaling loop (HPA)

```mermaid
sequenceDiagram
    autonumber
    participant K6 as k6 load
    participant P as Pods (2)
    participant KL as kubelet / cAdvisor
    participant MS as metrics-server
    participant H as HPA controller
    participant D as Deployment
    participant S as Scheduler

    K6->>P: ramp traffic up
    P->>P: CPU rises (e.g. 180m of 100m request = 180%)
    loop every ~15s
        MS->>KL: scrape pod CPU
        H->>MS: GET metrics.k8s.io pod CPU
        H->>H: desired = ceil(2 × 180% / 60%) = 6
    end
    H->>D: set replicas = 6 (capped at max)
    D->>S: create 4 new pods
    S-->>P: pods start → readiness passes → join Service
    K6->>P: load spread over 6 pods, CPU per pod falls
    Note over H,D: Load stops → CPU low.<br/>HPA waits the 120s scale-down<br/>stabilization window, then removes 1 pod / 30s.
```

### 5.3 Rolling update and rollback

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Developer
    participant API as Kubernetes API
    participant D as Deployment
    participant RS1 as ReplicaSet v1
    participant RS2 as ReplicaSet v2
    participant S as Service

    Dev->>API: kubectl set image ... items-api:v2
    API->>D: new pod template
    D->>RS2: create 1 pod (maxSurge=1)
    RS2-->>S: v2 pod passes /ready → receives traffic
    D->>RS1: terminate 1 v1 pod (maxUnavailable=0)
    Note over RS1: SIGTERM → /ready 503 → drain → exit
    D->>D: repeat until all pods are v2
    alt v2 is broken (readiness never passes)
        D->>D: rollout stalls — v1 pods keep serving
        Dev->>API: kubectl rollout undo
        API->>D: restore v1 template
        D->>RS1: scale back up
        D->>RS2: scale to 0
    end
```

### 5.4 Pod failure and self-healing

```mermaid
sequenceDiagram
    autonumber
    participant Op as Operator
    participant P1 as Pod A
    participant S as Service / EndpointSlice
    participant RS as ReplicaSet controller
    participant P3 as Pod C (new)

    Op->>P1: kubectl delete pod (simulated crash)
    P1-->>S: removed from endpoints
    Note over S: traffic continues on Pod B
    RS->>RS: observed 1 / desired 2
    RS->>P3: create replacement
    P3->>P3: start → /ready 200
    P3-->>S: added to endpoints
```

## 6. Monitoring and alerting flow

```mermaid
flowchart LR
    app[App pods<br/>/metrics] -->|scrape 15s| prom[(Prometheus<br/>TSDB)]
    ksm[kube-state-metrics] -->|scrape| prom
    kubelet[kubelet / cAdvisor] -->|scrape| prom
    prom -->|PromQL| graf[Grafana dashboard]
    prom -->|evaluate rules| rules{Alert rules}
    rules -->|HighErrorRate &gt; 5% for 2m| am[Alertmanager]
    rules -->|HighLatency p95 &gt; 500ms for 5m| am
    rules -->|AppDown up==0 for 1m| am
    am -->|group · dedupe · route| recv[Receiver<br/>local: Alertmanager UI<br/>optional: Slack webhook]
```

## 7. Deployment workflow (code → cluster)

```mermaid
flowchart TD
    a[Edit code] --> b[pytest locally]
    b --> c[git push / open PR]
    c --> d{GitHub Actions}
    d --> d1[ruff lint]
    d --> d2[pytest + JUnit report]
    d --> d3[docker build]
    d3 --> d4[Trivy image scan]
    d --> d5[pip-audit dependency scan]
    d1 & d2 & d4 & d5 --> e{All green?}
    e -- no --> a
    e -- yes --> f[docker build -t items-api:vX locally]
    f --> g[kind load docker-image]
    g --> h[kubectl apply -k k8s/overlays/local<br/>or helm upgrade]
    h --> i[kubectl rollout status]
    i --> j{Healthy?}
    j -- no --> k[kubectl rollout undo]
    j -- yes --> l[Watch Grafana]
```

Deployment from CI to the cluster is **not** automated in the first version: a GitHub-hosted runner cannot reach a kind cluster on your laptop, and automating it securely needs a reachable cluster plus scoped credentials. That is a documented future improvement.

## 8. Key design decisions and trade-offs

| Decision | Why | Trade-off / simpler alternative |
|---|---|---|
| **FastAPI + Uvicorn** | Async, typed, automatic validation and OpenAPI docs | Flask is simpler but you hand-write validation |
| **Single Uvicorn worker per pod** | Kubernetes is the process manager; scale with pods, not workers, so HPA CPU math stays honest | Gunicorn with N workers gives more per-pod throughput but hides load from the HPA |
| **In-memory store** | Zero dependencies; focus stays on the platform | Inconsistent across replicas; Redis/Postgres fixes that |
| **kind** over minikube/Docker Desktop k8s | Multi-node, fast, same tool many CI systems use | minikube has more add-ons built in; Docker Desktop k8s is one click but single-node |
| **Kustomize base + overlays** as source of truth, Helm chart as packaging exercise | Kustomize is plain YAML you can read; Helm teaches templating/releases | Maintaining both is duplication — acceptable for learning, not for a real team |
| **kube-prometheus-stack via Helm** | One install gives Prometheus, Alertmanager, Grafana, kube-state-metrics, node-exporter | Heavy for a laptop (~1.5 GB RAM); a bare Prometheus + Grafana via Docker Compose is lighter |
| **HPA on CPU** | Built in, needs only metrics-server | CPU is a proxy for load; request-rate scaling needs prometheus-adapter or KEDA |
| **k6** for load | Scriptable in JS, thresholds turn a load test into a pass/fail check | `hey` or `ab` are one-liners but give no ramping or thresholds |
| **Trivy + pip-audit** | Free, fast, run in CI | Snyk/Dependabot add PR automation |

## 9. Capacity math (why requests matter for the HPA)

- The HPA target is a **percentage of the CPU request**, not of the node.
- Example: request `100m`, target `60%` → HPA aims for 60m per pod on average.
- If 2 pods average 150m: `desired = ceil(2 × 150 / 60) = 5` replicas.
- If the request were `500m`, the same 150m is only 30% → **no scaling**. Wrong requests silently break autoscaling.
- Limits cap usage (CPU is throttled, memory over-limit is OOM-killed). We set CPU limit higher than the request so pods can burst.
