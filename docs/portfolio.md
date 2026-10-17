# Portfolio Guide

How to present this project on a resume, on LinkedIn, in a live demo and in interviews.

Every number here comes from a measured run in this repository, so you can defend each one by pointing at the evidence.

## 1. One-line summary

> Built a self-healing, autoscaling Kubernetes platform for a FastAPI service, with Prometheus/Grafana alerting, k6 load testing, failure drills and a security-gated GitHub Actions pipeline. Verified 0 dropped requests during pod failures and 2→6 replica autoscaling at 572 req/s.

## 2. Resume bullets

Pick 3–4 that fit the role. Each follows *action → technology → measured result*.

### Platform / DevOps / Cloud Infrastructure Engineer

- Deployed a containerised FastAPI service to a 3-node Kubernetes cluster (kind) with Kustomize and a Helm chart. The Helm chart is verified field-by-field against the Kustomize manifests by an automated check.
- Configured a Horizontal Pod Autoscaler with resource requests sized from measured usage. It scaled 2→6 replicas under a 190k-request k6 load test (572 req/s peak) with 0% errors, and back to 2 within 3.3 minutes.
- Eliminated dropped requests during pod termination: 1–2 failures per pod kill fell to 0 of 392, using readiness probes and a preStop drain, verified with a repeatable kill-under-load drill.
- Built a 3-job GitHub Actions pipeline (lint, 62 unit tests at 100% coverage, alert-rule tests, image build, Trivy and pip-audit gates), with SHA-pinned actions and a read-only token. Green on its first run.
- Hardened the supply chain with hash-pinned dependencies (a tampered package fails the build), non-root read-only containers under Pod Security "restricted", and default-deny NetworkPolicies verified live.

### Site Reliability Engineer

- Instrumented the service with Prometheus RED metrics and built a 14-panel Grafana dashboard as code. All 21 queries were checked to return data, so no panel shows a metric that doesn't exist.
- Wrote 5 alerts with 13 promtool unit tests, each proven able to fail by breaking its rule on purpose. Then fired and resolved 3 of them live (error rate, latency, service down) in failure drills, with delivery to Alertmanager.
- Ran four failure drills (pod loss, HTTP 500s, dependency latency, bad release) with measured detection and recovery timelines. Example: a bad release was detected in 201 s, rolled back at 228 s, and clean for users at 284 s.
- Found that CPU-based autoscaling scaled *down* during an I/O-latency incident, and documented scaling on request rate or concurrency instead.
- Wrote a blameless postmortem for an actual Grafana OOMKill. The follow-up found a 90%-of-limit near-miss and added a memory-near-limit alert with tests.

### Software / Backend Engineer

- Built a FastAPI service with Pydantic validation, structured JSON logs with request IDs, safe 500 responses, graceful SIGTERM shutdown and Prometheus metrics labelled by route template.
- Wrote 62 pytest tests at 100% coverage in one consolidated suite, including concurrency and fault-injection cases. The coverage gate is enforced in CI.
- Added opt-in fault injection (error rate, latency) as a router dependency, so injected failures are recorded under the real route. Used it to test alerting and runbooks end to end.

## 3. LinkedIn / project-page blurb

> **Cloud-Native Deployment & Auto-Scaling Platform** (personal project)
>
> I wanted to understand what keeps a service up, beyond deploying it. So I built a FastAPI service on Kubernetes and broke it on purpose: I killed pods under load, injected 500s and latency, and shipped a release that passed every health check but was broken. Then I measured what the platform caught and how fast.
>
> - Autoscaling: 2→6 replicas at 572 req/s with 0% errors (k6, 190k requests)
> - Zero dropped requests during pod failures, after measuring 1–2 without a preStop drain
> - 5 Prometheus alerts with unit tests; a bad release was rolled back within 228 s of deploying it
> - CI with hash-pinned dependencies, Trivy and pip-audit gates, and default-deny NetworkPolicies
>
> Stack: Python, FastAPI, Docker, Kubernetes (kind), Kustomize, Helm, Prometheus, Grafana, Alertmanager, k6, GitHub Actions, Trivy.

## 4. Five-minute live demo

Run this beforehand, so the demo starts warm:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

Open http://localhost:3000/d/items-api/items-api-health and set the time range to "Last 15 minutes".

| Minute | Show | Say |
|---|---|---|
| 0:00 | README results section | "Every number here was measured. Here's the platform and what it proves." |
| 0:45 | `kubectl -n autoscale-app get deploy,hpa,networkpolicy,pods -o wide` | "Two replicas on two nodes, an autoscaler, and the network is deny-by-default." |
| 1:30 | `./scripts/run-load-test.sh smoke`, then the dashboard | "k6 runs inside the cluster, so traffic spreads across pods. Watch request rate and p95." |
| 2:30 | `./scripts/pod-kill-drill.sh` | "I kill a pod under live traffic. Before the preStop drain this dropped 1–2 requests. Now it's zero, and here's the count." |
| 3:30 | `docs/troubleshooting.md`, Scenario D table | "The scariest failure: a release that passes every probe. Kubernetes said 'successfully rolled out'. The error-rate alert caught it in 201 s, and the logs pointed to the exact line." |
| 4:15 | GitHub Actions run page | "Every push runs tests, alert-rule tests and security gates. Actions are pinned to SHAs and dependencies to hashes." |
| 4:45 | `docs/incident-postmortem.md` | "And a real incident I caused, with the postmortem and the alert I added afterwards." |

**If something breaks during the demo, say so and debug it live.** It's the best possible SRE demo. Start with `kubectl get pods`, then events, then logs.

## 5. Three stories for interviews

1. **"Health checks don't prove correctness."**
   - Scenario D: the rollout succeeded in 11 s, with every probe passing.
   - Only the error-rate alert caught it.
   - Fix: smoke tests after deploy, plus progressive delivery with automatic rollback.
2. **"Autoscaling on the wrong signal can make an incident worse."**
   - Scenario C: a slow dependency dropped CPU from 0.16 to 0.04 cores, and the HPA *removed* a replica.
   - Fix: scale on concurrency or request rate (KEDA / prometheus-adapter), and alert on latency.
3. **"My own limit caused an outage."**
   - Grafana was OOMKilled at a 384 Mi limit I had guessed. I first misdiagnosed it as a port-forward fault.
   - The fix was later found to sit at 90% under load.
   - Lessons: measure under real load, alert on near-misses, check the pod before the network.

For each story, answer the four questions interviewers probe: **what happened, how you knew, what you did, what you changed so it can't recur.**

## 6. Capturing evidence (screenshots and links)

| Evidence | How to capture it | Already in the repo |
|---|---|---|
| Dashboard under load | Run `SETTLE=300 ./scripts/run-load-test.sh ramp`. About 5 minutes in, open the dashboard with `&kiosk&theme=light` and take a full-page screenshot. | `docs/images/grafana-ramp-peak.jpg` |
| Failure drills | Run drills B–D (see `troubleshooting.md`), then screenshot a 40-minute window | `docs/images/grafana-drills-b-c-d.jpg` |
| Firing alert | During a drill, open http://localhost:9090/alerts (port-forward Prometheus) and http://localhost:9093 | timelines in `monitoring.md` and `troubleshooting.md` |
| CI run | GitHub → Actions → the run → Summary (shows the unit-test table) | link in `ci-cd.md` |
| HPA scaling in a terminal | `kubectl -n autoscale-app get hpa -w` in one pane while `run-load-test.sh ramp` runs in another; record with QuickTime (File → New Screen Recording) | timeline in `load-test-results.md` |

**Tips:**

- Use light theme and kiosk mode (`&kiosk&theme=light`) so screenshots are clean.
- Crop out browser chrome.
- Keep image files under about 200 KB each, so the README loads quickly.

## 7. For working professionals

- **If you already run services at work:**
  - Map each drill to a real incident type you've seen. Interviewers value "I reproduced the class of failure we had at work".
  - Keep employer specifics out of a public repository. Describe them only in the interview, and only at a level your employer allows.
- **Time-boxed version (about 2 hours a week):**
  - Weeks 1–2: Phases 1–3 (app, tests, Docker).
  - Weeks 3–4: Kubernetes plus HPA.
  - Week 5: monitoring.
  - Week 6: drills.
  - Week 7: CI and security.
  - Week 8: docs.
  - Pause the cluster between sessions (`docker stop autoscale-control-plane autoscale-worker autoscale-worker2`) to save laptop resources. `docker start` resumes it.
- **Using it for an internal transfer or promotion:**
  - The postmortem, the security accepted-risk table and the CI design section (`ci-cd.md` §5) show the judgement expected at senior levels.
  - Lead with those, not the tool list.
- **Talking about it with your manager:** frame it as "I practised incident response and capacity validation on a safe sandbox". Offer to run one drill format (e.g. Scenario D) as a team game day.
- **Further learning that fits around a job:**
  - Google's *Site Reliability Engineering* and *SRE Workbook*, free online at sre.google/books. Read the monitoring, alerting and postmortem chapters alongside this project.
  - The Kubernetes documentation tasks section (kubernetes.io/docs/tasks), short and hands-on.
  - The CNCF Kubernetes certifications (CKA, CKAD): paid exams with free official curricula on GitHub. This project covers much of the CKAD workload and troubleshooting material.
