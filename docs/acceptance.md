# Acceptance Criteria: Status and Evidence

Each criterion from the project brief, with what was done to verify it and where the evidence lives.

- ✅ = verified by running it.
- ⚠️ = verified with a stated limitation.

All work was done on 2026-10-15 and 2026-10-16 on an Apple Silicon Mac: kind v0.33 (Kubernetes 1.37.0), 3 nodes.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | The API runs successfully | ✅ | Run with Uvicorn locally (Phase 2), in Docker (Phase 3), on kind (Phases 4–8), and in the CI smoke test (`{"status":"ready"}`). All 7 endpoints were exercised with curl and k6. [deployment.md](deployment.md) |
| 2 | Automated tests pass | ✅ | `make test`: **62 passed, 100% line coverage**, also in a Linux container and on GitHub Actions. `make test-alerts`: 8 rules, **13 promtool cases** pass. |
| 3 | The application builds as a Docker image | ✅ | `items-api:v1`–`v4` built, 227 MB, non-root, installed from a hash-locked `requirements.lock`. Built in CI on every push. [docker.md](docker.md) |
| 4 | The application deploys to local Kubernetes | ✅ | `kubectl apply -k k8s/overlays/local` → `successfully rolled out`, 2 pods on 2 workers. The Helm chart was also installed in a separate namespace. [kubernetes.md](kubernetes.md) |
| 5 | Health and readiness probes work | ✅ | Startup probe: `Container api failed startup probe, will be restarted` for the broken v3 image, while the rollout stalled safely. Readiness plus `preStop`: pod kills under traffic went from 1–2 failed requests to **0**. [kubernetes.md](kubernetes.md) §7 |
| 6 | Resource requests and limits are configured | ✅ | App: 100m/96Mi requests, 500m/192Mi limits, sized from measured idle and load usage. The k6 Job has limits too. **Every** monitoring container, sidecars and init containers included, has a memory limit at about 2–3× its measured 24 h peak; a live check found 0 without. That check (Phase 9) first found 8 sidecars with no limits, which were then fixed. Grafana's limit was resized from measurement after an actual OOMKill. |
| 7 | HPA is configured, and scaling is demonstrated | ✅ | **2 → 6 → 2** in Phase 5 and again in the Phase 7 ramp (190,254 requests, 572 req/s peak), with the per-15-second replica timeline recorded. [load-test-results.md](load-test-results.md) |
| 8 | Prometheus collects application metrics | ✅ | `serviceMonitor/monitoring/items-api/0  2/2 up`. `http_requests_total`, the latency histogram and the in-flight gauge are queryable. [monitoring.md](monitoring.md) |
| 9 | Grafana displays real metrics | ✅ | All **21 panel queries** were run against Prometheus and returned data (0 empty). 3 screenshots taken under real traffic in `docs/images/`. |
| 10 | At least one alert is tested | ✅ | **Unit tests:** 13 promtool cases. Deliberately breaking each of the 5 alerts made its test fail. **Live:** `ItemsApiDown` (outage), `ItemsApiHighErrorRate` (Scenarios B and D) and `ItemsApiHighLatency` (Scenario C) each went pending → firing → resolved, with timestamps, including delivery to Alertmanager. |
| 11 | A rolling update and rollback are demonstrated | ✅ | v1→v2 under traffic: 605/605 requests OK. A broken release stalled with v2 still serving, and `rollout undo` restored it with 862/862 OK. The declarative update showed both versions serving together for about 3 s. A Helm upgrade and `helm rollback` were also done. |
| 12 | A failure simulation is documented | ✅ | Four drills (A: pod kill, B: 500s, C: latency, D: bad release), each with symptoms, detection, investigation, recovery, verification and prevention, plus measured timelines. Actual incidents are listed separately, and one has a full postmortem. [troubleshooting.md](troubleshooting.md), [incident-postmortem.md](incident-postmortem.md) |
| 13 | GitHub Actions runs the test suite | ✅ | Public run [#38990703832](https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform/actions/runs/38990703832): all 3 jobs green (25 s / 18 s / 43 s). The very first GitHub run (private repository, not publicly viewable) was also green on the first attempt (19 s / 11 s / 49 s, 0 annotations). [ci-cd.md](ci-cd.md) |
| 14 | Security checks are documented and tested | ✅ | `make security` and CI: pip-audit, Trivy secret, config and image scans, all passing. **Negative tests:** a planted token, a planted privileged pod and a corrupted dependency hash each fail the build. The NetworkPolicy was verified live (allowed vs denied callers, no egress). [security.md](security.md) |
| 15 | The README contains reproducible instructions | ✅ ⚠️ | Fresh-clone run of the README steps passed (below). Creating a brand-new cluster from scratch was **not** re-run in Phase 9 (disk space). It was run, and documented with its pitfalls, in Phase 4. |

## Criterion 15: how reproducibility was checked

A fresh `git clone` of commit `e62df03` into an empty folder, running the README's steps verbatim (2026-10-16):

| Step | Result |
|---|---|
| `./scripts/check-prereqs.sh` | `All required tools present.` |
| `make install` | OK (new `.venv`, pinned deps) |
| `make test` | **62 passed**, 100% coverage |
| `make lint` | clean (28 files) |
| `make test-alerts` | promtool `SUCCESS` ×2 |
| `make helm-check` | `equivalent: 6/6 checks` |
| `docker build -t items-api:v4 ...` (README step 3) | OK |
| `kubectl diff -k k8s/overlays/local` from the clone | exit 0: the running cluster **exactly matches** what the clone's manifests describe |
| `kubectl diff -k monitoring` from the clone | exit 0 |
| `make security` | exit 0 (all four gates) |
| Relative links in README and `docs/*.md` | 47 checked, 0 broken |

**Not re-run in Phase 9:** `kind create cluster` → metrics-server → Helm monitoring install from zero.

- That path was run during Phases 4–6, and its real pitfalls are documented in `deployment.md` and `troubleshooting.md` §3: disk-full Docker, kubectl version skew, the ServiceMonitor label.
- Re-running it needs about 20 GB of free disk, and this Mac had about 7 GB.
- To verify it yourself on a machine with space, follow the README quick start from step 3.

## Not verifiable in this environment

- **Production capacity:** k6 and the cluster share the same laptop CPUs. The results show *behaviour* (scaling, recovery, alerting), not how many req/s production would handle.
- **Real notifications:** Alertmanager routes to a `null` receiver. Delivery to Slack or email would need a real secret-managed receiver.
- **Cloud load balancer and ingress TLS:** kind has no cloud load balancer, and no ingress was installed.
