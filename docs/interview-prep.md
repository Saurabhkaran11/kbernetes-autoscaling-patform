# Interview Preparation

Questions you're likely to get about this project, with answers grounded in what was actually built and measured.

**Answer shape:** answer each question in four short parts:

1. **Decision:** what you chose.
2. **Why:** the problem it solves.
3. **Trade-off:** what it costs, or when it's the wrong choice.
4. **Evidence:** a number or observation from this project.

The trade-off and the evidence are what separate you from someone who memorised a definition. Every number below can be traced to a file in `docs/`.

---

## Part 1 — 25 technical questions

### Docker (1–4)

**1. Walk me through your Dockerfile. Why multi-stage?**

- **Decision:** a `builder` stage installs dependencies into a virtualenv, and a `runtime` stage copies only that virtualenv and the app code.
- **Why:** build tools, pip and caches never reach the image that runs, so there's less to download and less to attack.
- **Trade-off:**
  - A slightly more complex Dockerfile.
  - With no shell tools in the image, debugging needs `kubectl debug` or an ephemeral container.
- **Evidence:**
  - Image 227 MB on a 205 MB base: we add about 22 MB.
  - pip is removed from both the virtualenv and the base image.

**2. Why does `CMD` use the exec form (JSON array)?**

- **Decision:** `CMD ["uvicorn", ...]`, not `CMD uvicorn ...`.
- **Why:**
  - Exec form makes Uvicorn PID 1, so it receives SIGTERM directly.
  - Shell form runs under `/bin/sh`, which doesn't forward signals.
- **Trade-off:** no shell features like variable expansion in `CMD`. Use an entrypoint script with `exec` if you need them.
- **Evidence:** measured on the same image.
  - Exec form stopped in **0.57 s** with exit 0.
  - Shell form took **10.2 s** and was SIGKILLed (exit 137), with no clean shutdown logged.

**3. How do you make builds reproducible and resistant to tampering?**

- **Decision:**
  - Pin the base image (`python:3.12.15-slim-trixie`; a digest is better still).
  - Install from `requirements.lock` with `--require-hashes --no-deps`.
- **Why:**
  - `requirements.txt` only pins direct dependencies, so indirect ones (for example `opentelemetry-api`) can drift between builds.
  - Hashes make a tampered upload fail the build.
- **Trade-off:**
  - Every dependency change needs `make lock`.
  - The lock is per-platform (here linux / Python 3.12).
- **Evidence:** corrupting every `uvicorn` hash made the build fail with `THESE PACKAGES DO NOT MATCH THE HASHES`.

**4. What's in your `.dockerignore`, and why an allow-list?**

- **Decision:** ignore `*`, then re-include only `app/` and `requirements.lock`.
- **Why:** a deny-list leaks every new file someone forgets to add, such as a `.env`, a key or a dataset. An allow-list excludes them by default.
- **Trade-off:** adding a new file the image needs means remembering to allow it, and the build fails loudly if you forget, which is the safe direction.
- **Evidence:** the image's `/app` contains only the 11 `app/*.py` files. `.venv` (69 MB) and `.git` never reach the build context.

### Kubernetes core (5–8)

**5. Deployment, ReplicaSet, Pod: why three objects?**

- **Pod:** the running unit.
- **ReplicaSet:** keeps N identical pods for **one** pod template, so a deleted pod comes back.
- **Deployment:** manages ReplicaSets across template changes. A new image means a new ReplicaSet, with a gradual shift between them and old ReplicaSets kept for rollback.
- **Evidence:**
  - After `rollout undo` in Scenario D, the Deployment scaled the previous ReplicaSet back up instead of creating a new one.
  - `rollout history` listed the revisions.

**6. How does a Service find pods, and how did you prove load balancing?**

- **How:** label selectors produce an EndpointSlice of **ready** pod IPs, and kube-proxy on each node spreads connections across them.
- **The trap:** `kubectl port-forward svc/...` pins **one** pod. It's useful for debugging and useless for demonstrating balancing.
- **Evidence:**
  - 6 requests through port-forward all hit one pod.
  - 20 requests through the Service DNS name from inside the cluster split 11/9.

**7. Why Kustomize *and* Helm?**

- **Decision:** Kustomize is the source of truth for the local cluster. The Helm chart is the packaging exercise.
- **Why:** Kustomize is plain YAML with overlays (easy to review). Helm adds templating, releases, `helm history` and `helm rollback`.
- **Trade-off:**
  - Two sources of truth can drift, so `make helm-check` compares them field by field: pod spec, HPA, NetworkPolicies, ports, config.
  - On a real team, pick one.
- **Evidence:**
  - `equivalent: 6/6 checks`.
  - Allowing `default` in the chart's NetworkPolicy, or setting `preStop` to 0, makes the check fail.

**8. What does Pod Security "restricted" enforce, and what did it catch?**

- **What it enforces:** at the namespace level, the API server rejects pods that run as root, allow privilege escalation, keep Linux capabilities or skip the seccomp profile.
- **Trade-off:**
  - Every workload must declare its security context, including your debug pods.
  - Many third-party images need adjustments.
- **Evidence:**
  - `kubectl run root-test --image=nginx` was rejected with the exact four violations.
  - The k6 Job and drill pods had to be written to comply.

### Probes, HPA and resources (9–13)

**9. Liveness vs readiness vs startup probes: what does each failure do?**

| Probe | Failure means |
|---|---|
| Readiness | Removed from the Service, **not** restarted |
| Liveness | Killed and restarted |
| Startup | Delays the other two until boot finishes |

- **Design rule:** liveness must not check dependencies. If it checked a database, a database outage would make Kubernetes restart *every* pod.
- **Evidence:**
  - The broken v3 image (listening on the wrong port) failed its startup probe 25 times in 44 s, was restarted, and the rollout stalled while v2 kept serving.
  - In Scenario D, both probes passed for a functionally broken release. Probes prove the process is alive, not that it's correct.

**10. Why did killing a pod drop requests, and how did you fix it?**

- **Cause:**
  - On deletion, endpoint removal and SIGTERM happen **in parallel**.
  - Uvicorn stopped listening before every node's kube-proxy had stopped routing to the pod.
- **Fix:**
  - `preStop: sleep 5` (native sleep action) keeps serving while endpoints update.
  - A readiness probe gates new pods.
- **Trade-off:** pods take about 6 s to terminate instead of under 1 s, which means slower rollouts and scale-down.
- **Evidence:** 1–2 failed requests per kill before the fix, then **0 of 392** across repeated runs.

**11. Explain the HPA formula and why the CPU *request* matters.**

- **Formula:** `desired = ceil(current × currentCPU% ÷ target%)`, where CPU% is usage divided by the **request**, not the limit.
- **Why the request matters:**
  - Request 100m with a 60% target means about 60m per pod.
  - Set the request to 1000m, and the same load reads 6%: the HPA never scales.
  - Omit it, and the HPA shows `<unknown>`.
- **Evidence:**
  - 3 pods at 80% → ceil(3 × 80 ÷ 60) = 4, matching the observed step at 230 s.
  - A full run went 2 → 6 → 2.

**12. Your HPA scaled *down* during an incident. Explain.**

- **What happened:**
  - Scenario C injected 600 ms of waiting, simulating a slow dependency.
  - Waiting costs no CPU: total CPU fell from 0.164 to about 0.04 cores, so the HPA removed a replica.
- **Lesson:** CPU tracks load only for CPU-bound work.
- **Better options:**
  - Scale on concurrency or request rate (KEDA or prometheus-adapter on `http_requests_in_progress`).
  - Alert on latency regardless of how scaling is configured.
- **Trade-off:** custom-metrics scaling adds components and more ways to fail.

**13. Requests vs limits: how did you size them, and what happens at each limit?**

- **What happens at the limit:**
  - **CPU** over the limit is throttled, which shows up as latency.
  - **Memory** over the limit is OOM-killed (exit 137).
- **Sizing:**
  - Requests come from measured use: idle 3m CPU / 37 MiB, about 0.24 cores per pod under load.
  - Limits leave headroom.
- **Surprise:**
  - At 0.24 cores average, still under the 0.5 limit, a pod was throttled in **40%** of its CPU periods.
  - Throttling happens per 100 ms window, so bursts are throttled before the average shows it. That's where the p99 tail (105 ms) came from.
- **Monitoring sidecars:** memory limits only, at 2–3× their measured peak. A leak can kill a node, while a CPU limit on a bursty sidecar mostly causes throttling.

### Prometheus and Grafana (14–17)

**14. Which metrics did you instrument, and why label by route template?**

- **Metrics:** RED (Rate, Errors, Duration): a request counter by method/route/status, a latency histogram, and an in-flight gauge.
- **Why route templates:**
  - The `path` label is `/api/v1/items/{item_id}`, never `/api/v1/items/42`.
  - Raw URLs create one time series per id, a cardinality explosion that slows or crashes Prometheus.
- **Evidence:** a test asserts the raw id never appears in `/metrics`.

**15. Why does the `ItemsApiDown` alert use `or vector(0)`?**

- **Why:**
  - When every pod is gone, there are **no** `up` series at all.
  - `sum(up) == 0` then returns nothing, and the alert never fires, exactly when it matters most.
  - `or vector(0)` substitutes 0.
- **Evidence:**
  - A unit test for "no targets at all" passes with it and fails without it (mutation tested).
  - Live, the alert went pending at 37 s and firing at 99 s after scaling to 0.

**16. How do you know your alerts work before a real incident?**

- **Unit tests:** promtool tests with synthetic series. 13 cases cover thresholds, the `for` duration, no traffic (no divide-by-zero) and probe paths excluded.
- **Mutation tests:** each of the 5 alerts' tests was proven able to fail by deliberately breaking that rule (threshold changed, `or vector(0)` removed, `>=` → `>`).
- **Live:** 3 alerts went pending → firing → resolved in drills, including delivery to Alertmanager.
- **Trade-off:** it's tests on *synthetic* data. Real drills are still needed for end-to-end confidence.

**17. Your dashboard showed a misleading number. What was it, and what's your rule for dashboards?**

- **Problem 1:** "In-flight requests" showed 1 on idle pods, because the gauge counted the Prometheus scrape itself. Fixed in the app (`/metrics` no longer counts itself), with a test.
- **Problem 2:** "Requests in range" was red, because Grafana's default threshold paints values above 80 red. Volume isn't good or bad by itself, so it got a neutral colour.
- **Rule:**
  - Every panel query is verified to return data (21/21).
  - Every panel's description names its metric source.
  - Colours only encode health.

### SRE and incident response (18–21)

**18. Walk me through the bad-release drill (Scenario D).**

| t | Event |
|---|---|
| 0 s | Deploy a release with a `KeyError` on single-item reads |
| 11 s | Kubernetes reports "successfully rolled out" (probes pass) |
| 85 s | Alert pending |
| 201 s | Alert firing |
| ~201–228 s | Investigation (below) |
| 228 s | `rollout undo` |
| 284 s | Users clean (1-minute error rate 0) |
| 505 s | Alert resolved |

**Investigation:**

- errors by route → only `/items/{item_id}`, at 100% → code, not config,
- the newest ReplicaSet image → `v5-regression`,
- the log for one request ID → `KeyError: 'tags'` at `items.py:51`.

**Lesson:** mitigate first (roll back), investigate second.

**19. Why does an alert resolve minutes after the fix?**

- **Why:** the error and latency alerts use 5-minute rate windows. After the fix, old errors stay in the window until they age out.
- **Practice:** confirm recovery with a **1-minute** query, and expect the alert to clear later.
- **Evidence:** Scenario B.
  - Users were clean about 1 minute after the rollback (1-minute rate 0%).
  - The 5-minute rate drained slowly from its 14.4% peak, and the alert resolved at 452 s, the moment it fell below the 5% threshold (4.9%).
- **Trade-off:** shorter windows resolve faster but alert more on noise.

**20. Tell me about a real incident.**

- **What happened:**
  - Grafana was OOMKilled twice at a 384 Mi limit I had estimated.
  - I first blamed the port-forward, because a pod restart looks like a network failure.
  - The pod's `lastState.terminated.reason: OOMKilled` gave the real cause.
- **Fix:** 768 Mi, sized from a measured 383 MiB peak.
- **Follow-up:**
  - Prometheus history then showed 694 MiB (90% of the new limit) under load.
  - Raised to 1 Gi, and added a `ContainerMemoryNearLimit` alert with tests.
- **Lessons:**
  - Measure under the heaviest real use.
  - Check the pod before the network.
  - Treat near-misses as incidents.
  - Written up blameless in [incident-postmortem.md](incident-postmortem.md).

**21. What's in a good postmortem, and what makes it blameless?**

- **Contents:** summary, impact, timeline from data, root cause plus contributing factors, how it was detected (and the detection gap), what went well and badly, and action items with owners and status.
- **Blameless:**
  - Ask how the system allowed the mistake (no measurement rule, no near-limit alert), not who made it.
  - People report honestly only when they won't be punished for it.

### CI/CD and security (22–25)

**22. Describe your CI pipeline and its design choices.**

- **Jobs (in parallel):**
  1. lint + 62 tests with a 95% coverage gate and a summary table,
  2. promtool rule tests,
  3. build + smoke test + pip-audit + Trivy secret, config and image scans.
- **Choices:**
  - Parallel, for faster feedback and clearer failures.
  - Summary written to `$GITHUB_STEP_SUMMARY`, so no `checks: write` permission is needed.
- **Evidence:** green on the first GitHub run (19 s / 11 s / 49 s), with 0 annotations.

**23. How did you secure the pipeline itself?**

- **Measures:**
  - actions pinned to full commit SHAs (a tag can be moved),
  - Trivy and Prometheus images pinned by digest,
  - `permissions: contents: read`,
  - `persist-credentials: false`,
  - no secrets used.
- **Deployment design (not built, because a laptop cluster isn't reachable):**
  - a separate workflow, OIDC instead of a stored kubeconfig,
  - `packages: write` only on the publish job,
  - signed images,
  - an approval environment,
  - ideally GitOps, so CI never holds cluster credentials.

**24. Your image has 44 HIGH CVEs. Why does CI pass?**

- **Why:**
  - All 44 are in Debian base packages, **with no fixed version available**. The official base image scans identically.
  - The gate is `--ignore-unfixed`: it fails on CVEs that *have* a fix, because blocking on unfixable ones would block every build without improving anything.
- **Mitigations:**
  - It's documented as an accepted risk.
  - It's re-checked on each base-image bump.
  - A distroless or Wolfi base would remove most of these packages (trade-off: no shell).
- **The honest part:** state it as a decision with a review date, not as "the scan is clean".

**25. How did you restrict network access, and how do you know it works?**

- **Policies:**
  - Default-deny ingress and egress in the app namespace.
  - Allow ingress only from Prometheus pods and the `loadtest` namespace, on the `http` port.
  - No egress at all.
- **Verified live:**
  - `loadtest` → 200; `default` → timeout.
  - Prometheus `up=1`.
  - App egress to `1.1.1.1:443` timed out.
  - Probes and port-forward still work.
- **A correction worth telling:** I first assumed kind's CNI didn't enforce NetworkPolicy. A deny-all test proved it does, and the policy was built and verified instead.

---

## Part 2 — Kubernetes troubleshooting scenarios

For each one, say the commands you'd run **in order**, and why.

| # | Symptom | First commands | What you're looking for | Seen in this project |
|---|---|---|---|---|
| 1 | Pod `ImagePullBackOff` | `kubectl describe pod` (Events) | Wrong tag, private registry, image not loaded into kind | Image `does-not-exist` drill; forgotten `kind load` |
| 2 | Pod `CrashLoopBackOff` | `kubectl logs <pod> --previous`, then `describe` (Last State, exit code) | Startup exception (exit 1), OOMKilled (137), probe kills | Broken v3: startup probe kills |
| 3 | HPA shows `<unknown>` | `kubectl top pods`, `kubectl describe hpa` | metrics-server missing or not ready, pods too new, no CPU request | First minute after install; new pods |
| 4 | Service returns nothing / times out | `kubectl get endpointslices`, labels on pods vs selector, NetworkPolicies | No ready endpoints, a selector typo, a policy denying the caller | Debug pod in `default` blocked by policy |
| 5 | Prometheus target missing | Targets page, `kubectl get svc --show-labels` | A ServiceMonitor selects **Service** labels, not pod labels | Exactly this; fixed by labelling the Service |
| 6 | Rollout stuck | `kubectl rollout status`, `get rs`, `describe` new pods | New pods never Ready; with `maxUnavailable: 0`, old pods keep serving | Broken v3 |
| 7 | Nodes `NotReady`, API timeouts | `docker ps`, `docker info`, `df -h` | Infrastructure below Kubernetes: disk, memory, Docker VM | Mac disk 98% full → Docker `input/output error` |
| 8 | Errors but every pod is "healthy" | Error rate by route, logs with `--tail=-1`, ReplicaSet images, env | Functional regression or bad config; probes don't test logic | Scenarios B and D |

**Two gotchas worth naming:**

- `kubectl logs -l <selector>` shows only **10 lines per pod** by default. It once reported 4 errors when there were 2,315.
- `rollout history` change-cause notes are copied forward and can be stale.

---

## Part 3 — Five system design questions

Each answer starts from this project and states what changes at scale.

### SD1. Scale this service to 100× traffic, across regions

- **Clarify first:**
  - read/write ratio,
  - latency SLO,
  - consistency needs,
  - budget.
- **Compute:**
  - Stateless pods behind regional load balancers.
  - The HPA tuned with a higher `maxReplicas`, plus the cluster autoscaler for nodes. The HPA alone can't add machines; Phase 5 hit the 6-replica cap.
- **State:**
  - The in-memory store is the first thing to go.
  - Use a managed database with read replicas, plus a cache (Redis) for hot reads.
- **Traffic:** GeoDNS or a global load balancer to the nearest region, with health-checked failover.
- **Capacity:** a load test per region with an **open model** (arrival rate). The closed-model k6 run here hid queueing.
- **Trade-offs:**
  - Multi-region writes mean conflict handling, or a single write region with replication lag.
  - The cost of headroom vs the risk of saturation.

### SD2. Add persistence without losing zero-downtime deploys

- **Database:** PostgreSQL via a managed service or an operator; the app connects through a pool (PgBouncer).
- **Migrations:** expand → migrate → contract.
  - Additive schema changes first.
  - Then code that writes both shapes.
  - Then a backfill.
  - Then removal of the old column, in a later release.
- **Why:** during a rolling update, **old and new code run together**. Phase 5 showed both versions serving for about 3 s.
- **Readiness:** a pod is ready only when its pool is established. Liveness still must not check the database.
- **Trade-offs:** slower, multi-step releases, in exchange for no locked tables or broken old pods mid-rollout.

### SD3. Autoscaling for an I/O-bound or queue-driven workload

- **The problem:** CPU is the wrong signal (Scenario C).
- **Options:**
  - **KEDA** on queue depth or lag.
  - prometheus-adapter on `http_requests_in_progress` per pod (target concurrency).
  - Request rate per pod.
- **Safety:**
  - Scale-down stabilisation.
  - Minimum replicas for cold start.
  - A dependency-saturation guard: don't scale consumers past what the database can take.
- **Trade-offs:** more moving parts; a metrics outage could freeze scaling, so keep a sane minimum and alert when metrics go missing.

### SD4. An observability and alerting platform for 50 services

- **Standards:**
  - RED metrics with route-template labels.
  - Structured logs with request IDs.
  - Tracing via OpenTelemetry.
- **SLO-based alerting:**
  - Each service defines an SLO, such as 99.9% of requests successful in under 500 ms.
  - Alert on **multi-window burn rate**, not raw thresholds, so pages mean users are affected.
- **Scale:**
  - Prometheus per cluster, with remote write to long-term storage (Thanos or Mimir).
  - Persistent volumes; this project lost its history on a restart.
- **Quality gates:**
  - Alert rules unit-tested in CI (promtool), as here.
  - Dashboards as code.
  - Runbook links required on every alert.
- **Trade-offs:** cardinality budgets per team; cost of retention vs resolution.

### SD5. A secure delivery pipeline for many teams

- **CI:** as here, run per repository: SHA-pinned actions, a least-privilege token, tests and scans as gates.
- **Artifacts:**
  - Images signed (cosign keyless) with an SBOM.
  - Admission policy (Kyverno) rejects unsigned images or ones from unknown registries.
- **CD:**
  - **GitOps** (Argo CD or Flux) pulls from an environment repository, so CI never holds cluster credentials.
  - Promotion by pull request, with required reviewers for production.
- **Release safety:** progressive delivery (Argo Rollouts) with automatic rollback on error-rate or latency analysis. That turns Scenario D's 228 s manual rollback into an automatic abort within a minute or two.
- **Trade-offs:**
  - More platform to run.
  - Teams need training in GitOps workflows.
  - Policy exceptions need an owner and an expiry date (like `.trivyignore.yaml` here).

---

## Part 4 — Limitations to raise yourself

Mentioning these before the interviewer does shows judgement.

- **In-memory store:** state is per pod and lost on restart.
- **Laptop load numbers:** k6 shares CPUs with the cluster, so they show behaviour, not capacity.
- **Closed-model k6:** it hides queueing.
- **Monitoring not durable:** single-replica Prometheus and Alertmanager, no persistent storage, and the `null` receiver means no real notifications.
- **Missing production security:** no TLS, no authentication, no image signing; the details are in [security.md](security.md) §4.

---

## Part 5 — How to practise

- **Say answers out loud,** 60–90 seconds each, decision → why → trade-off → evidence. Record yourself once.
- **Re-run one drill from memory** (Scenario D is best) without reading the docs. Interviewers often ask you to talk through commands.
- **Whiteboard the request and pod-termination sequence diagrams** from [architecture.md](architecture.md) and [diagrams.html](diagrams.html).
- **For each design question,** spend 2 minutes on requirements before proposing anything.

### For working professionals

- **Map each drill to an incident class you've lived through at work** ("we had a release that passed health checks…"). Keep employer details generic.
- **Preparing around a full-time job:**
  - 20 minutes a day: one question out loud.
  - Weekend: one drill or one design question end to end.
- **Use your on-call experience as the strongest evidence.** This project shows you can *reproduce and measure* those failures deliberately, which is what senior SRE and platform interviews look for.

### Certifications and resources

**Free:**

- [Kubernetes documentation — Tasks](https://kubernetes.io/docs/tasks/): hands-on guides for probes, HPA and NetworkPolicy.
- [Google SRE books](https://sre.google/books/) (*Site Reliability Engineering*, *The SRE Workbook*). Read the monitoring, alerting-on-SLOs and postmortem chapters.
- [Prometheus documentation](https://prometheus.io/docs/), especially *Instrumentation* and *Alerting rules* best practices.
- [Linux Foundation — Introduction to Kubernetes (LFS158)](https://training.linuxfoundation.org/training/introduction-to-kubernetes/), a free course.
- [CNCF exam curricula](https://github.com/cncf/curriculum): the official topic lists for CKA, CKAD and KCNA, useful as checklists even without taking the exam.

**Paid:**

- [Certified Kubernetes Application Developer (CKAD)](https://training.linuxfoundation.org/certification/certified-kubernetes-application-developer-ckad/): closest to this project (probes, resources, rollouts, troubleshooting).
- [Certified Kubernetes Administrator (CKA)](https://training.linuxfoundation.org/certification/certified-kubernetes-administrator-cka/): cluster operations and troubleshooting.
- [Prometheus Certified Associate (PCA)](https://training.linuxfoundation.org/certification/prometheus-certified-associate/): metrics, PromQL and alerting.
- [Kubernetes and Cloud Native Associate (KCNA)](https://training.linuxfoundation.org/certification/kubernetes-cloud-native-associate/): an entry-level overview.
