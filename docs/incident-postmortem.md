# Postmortem: Grafana OOMKilled after the monitoring stack was installed

> **ACTUAL incident, not a drill.** It happened unplanned on 2026-10-15 while building Phase 6.
>
> The four *simulated* failure drills are in [troubleshooting.md](troubleshooting.md).
>
> This postmortem is written in the usual blameless format: it looks at how the system and the process allowed the failure, not at who made a mistake.

| | |
|---|---|
| **Date** | 2026-10-15 (all times UTC) |
| **Duration** | about 3 minutes of Grafana outages (23:21–23:24Z); exposure to a repeat until 00:3xZ |
| **Severity** | Low: internal tooling only (local cluster) |
| **Services affected** | Grafana (dashboards). **Not** affected: the Items API, Prometheus, Alertmanager. |
| **Status** | Resolved. All action items done. |

## Summary

- The Grafana container in `kube-prometheus-stack` was given a 384 MiB memory limit.
- Loading the new 14-panel dashboard pushed its working set to the limit, and the kernel OOM-killed it twice within two minutes.
- Each restart broke the `kubectl port-forward` used to reach Grafana, so the dashboard went blank.
- The first diagnosis blamed the port-forward. The real cause was found in the pod's status: `lastState.terminated.reason: OOMKilled`.
- The limit was raised to 768 MiB. Prometheus history later showed Grafana reaching **90%** of that new limit under load, so it was raised again to 1 GiB, and a memory-near-limit alert was added.

## Impact

- **Dashboards unavailable twice**, about a minute each. The browser showed `ERR_CONNECTION_REFUSED`, then "Failed to load dashboard".
- **No metric data lost.** Prometheus kept scraping, and Grafana only reads from it.
- **The Items API was unaffected**, with 100% availability for the whole period.
- **Logins lost.** Grafana sessions live inside the pod, so each restart logged the user out.

## Timeline (UTC)

| Time | Event | Source |
|---|---|---|
| 23:09:17 | `helm install` (revision 1). Grafana limit **384 Mi**, chosen by estimate | `helm history` |
| 23:11:02 | Grafana pod `…-mqvrz` starts | Prometheus |
| ~23:20 | 14-panel "Items API Health" dashboard loaded; several queries over 30–45 min ranges | browser |
| 23:21:17 | Grafana working set reaches **376 MiB** (sampled peak) | Prometheus |
| **23:21:47** | **First OOMKill**, container restart 0 → 1 | `kube_pod_container_status_restarts_total` |
| ~23:22 | Dashboard blank: `ERR_CONNECTION_REFUSED`. **Misdiagnosed** as a flaky port-forward; port-forward loops restarted | browser console |
| **23:23:32** | **Second OOMKill**, restart 1 → 2 | Prometheus |
| ~23:23 | `kubectl get pods` shows `RESTARTS 2`; `lastState.terminated.reason = OOMKilled`, exit 137. **Root cause identified.** | kubectl |
| 23:23:44 | `helm upgrade` (revision 2): limit **768 Mi**, request 256 Mi | `helm history` |
| 23:24:17 | New Grafana pod `…-7n94q` up; 0 restarts afterwards | Prometheus |
| 23:45:47 | **Near-miss:** new pod peaks at **694 MiB = 90% of 768 Mi** while rendering a 15-min dashboard during the ramp load test | Prometheus |
| 00:3x | `helm upgrade` (revision 3): limit **1 Gi**, request 384 Mi; `ContainerMemoryNearLimit` alert added and unit-tested | git `2b20994` |

## Root cause

- **The memory limit was set by estimate, not by measurement.**
  - Grafana 13 needs more than 384 MiB to render a multi-panel dashboard with several range queries at once.
  - Once its working set reached the limit, the kernel's OOM killer terminated it (exit 137), and Kubernetes restarted it, as designed.
- **Contributing factors:**
  1. **No early warning.**
     - Nothing alerted on memory approaching the limit.
     - The chart's restart alert (`KubePodCrashLooping`) needs repeated crashes over several minutes, so two restarts didn't trigger it.
  2. **The failure showed up somewhere else.**
     - `kubectl port-forward` is bound to one pod, so a pod restart looks like a network problem, not a memory problem.
     - Time went into fixing port-forward loops before anyone looked at the pod.
  3. **"2× the peak" was based on a peak taken at the wrong time.**
     - The 768 MiB fix was sized from the 383 MiB idle-browsing peak.
     - Heavier use (a 15-minute dashboard during a load test) used 694 MiB.

## Detection

- **How it was found:** by a human. The dashboard went blank, and the pod status showed `OOMKilled`.
- **Gap:** no alert would have caught it, and neither would the near-miss at 23:45.
- **Fixed by:** `ContainerMemoryNearLimit` (working set > 90% of the limit for 5 minutes), now in `monitoring/alerts/items-api-rules.yaml`.

## Resolution and recovery

1. Read the pod's last termination state:

   ```bash
   kubectl -n monitoring get pod -l app.kubernetes.io/name=grafana -o jsonpath='{.items[0].status.containerStatuses[?(@.name=="grafana")].lastState}'
   ```

2. Measured the real peak from Prometheus:

   ```
   max_over_time(container_memory_working_set_bytes{namespace="monitoring",container="grafana"}[30m])
   ```

3. Raised the limit in `monitoring/values-local.yaml` and applied it with `helm upgrade`.
4. Verified 0 restarts afterwards, then logged in again, because the sessions had been lost with the pod.

## What went well

- **The app was isolated from the failure.** Monitoring lives in its own namespace with its own limits, so the Items API was unaffected.
- **Prometheus kept the evidence.** Its history made it possible to measure the real peak, and later to spot the 90% near-miss, instead of guessing again.
- **The fix lives in git.** It's a values change, not a manual `kubectl edit`, so it survives a reinstall.

## What went badly

- **The first diagnosis was wrong,** about 2 minutes spent on port-forwarding.
- **The first fix had too little headroom.** It took a second measurement under real load to size the limit properly.
- **The same mistake had been made twice.** Phase 5 had sized the *app's* limits from measured usage, but the monitoring stack's limits were not held to the same rule.

## Action items

| # | Action | Status |
|---|---|---|
| 1 | Size Grafana from measured peak under load: limit 1 Gi, request 384 Mi | ✅ Done (`2b20994`) |
| 2 | Alert when any container in `autoscale-app` or `monitoring` stays above 90% of its memory limit for 5 min | ✅ Done, with 2 promtool tests (`2b20994`) |
| 3 | Troubleshooting entry: "port-forward dies → check pod restarts first" | ✅ Done (`docs/troubleshooting.md` §3) |
| 4 | Rule for every new component: set limits only after measuring under representative load; review headroom after load tests | ✅ Documented here and in `values-local.yaml` comments |
| 5 | (Future) Persist Grafana sessions or use a stateless auth proxy so restarts don't log users out | Open: not needed for a local cluster |

## Lessons

- **A limit you haven't measured is a guess.** Measure under the heaviest realistic use, not idle use.
- **When a connection drops, check whether the pod behind it restarted** (`kubectl get pods`, `lastState`) before debugging the network.
- **Watch for near-misses.** The 90% peak caused no outage, but finding it prevented the next one.
