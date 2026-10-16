# Load Test Results

Every number here was **observed**, not estimated, on 2026-10-15. The setup:

- **Machine:** an Apple Silicon Mac. Docker Desktop has 8 CPUs and 7.8 GB.
- **Cluster:** kind v1.37.0 with 1 control-plane node and 2 workers.
- **App:** items-api v4. Each pod requests 100m CPU (limit 500m) and 96 Mi memory (limit 192 Mi).
- **HPA:** 2–6 replicas at 60% CPU.
- **Load generator:** k6 2.3.0 running as a Job inside the cluster (`scripts/run-load-test.sh`). Traffic goes through the Service, so all replicas share it.
- **Traffic mix** (`load-tests/common.js`):
  - 40% `GET /api/v1/items`
  - 30% `GET /api/v1/items/{id}`
  - 20% `GET /api/v1/stats`
  - 10% `POST /api/v1/items`
- **Caveat:** k6 and the app share the same laptop CPUs, so these numbers describe *this laptop*, not production capacity.

## 1. Smoke test (`load-tests/smoke.js`, 5 VUs, 30 s)

| Metric | Result | Threshold |
|---|---|---|
| Requests | 1,430 | |
| Throughput | 47.5 req/s | |
| Failed | **0.00%** | < 1% ✅ |
| Latency p50 / p90 / p95 / max | 3.6 / 6.9 / **7.9** / 17.2 ms | p95 < 300 ms ✅ |
| Checks | 1,430 / 1,430 passed | > 99% ✅ |
| Replicas | 2 → 2 | |

## 2. Ramp test (`load-tests/ramp.js`, 10 → 60 → 0 VUs over 10 min)

![Dashboard at the peak of the ramp test](images/grafana-ramp-peak.jpg)

*At the peak: traffic climbing to about 500 req/s, replicas stepping 2 → 6, p99 rising toward 27 ms, and 0% errors.*

### Client view (k6 summary, whole run)

| Metric | Result | Threshold |
|---|---|---|
| Requests | **190,254** | |
| Average throughput | 317.0 req/s | |
| Failed | **0.00%** | < 1% ✅ |
| Latency p50 / p90 / p95 / max | 1.9 / 5.6 / **10.4** / 664.6 ms | p95 < 500 ms ✅ |
| Checks | 190,254 / 190,254 passed | ✅ |

### Server view (Prometheus, worst 1-minute window)

| Metric | Value |
|---|---|
| Peak user throughput | **572 req/s** |
| Worst p95 | 13.5 ms |
| Worst p99 | 105 ms |
| Highest per-pod CPU | 0.24 cores (limit 0.5) |
| Highest CPU throttling (share of 100 ms periods throttled, any pod) | **40%** |

### Replicas before, during and after (sampled every 15 s)

| t | Stage | HPA CPU | Replicas |
|---|---|---|---|
| 0–77 s | ramp to 10 VUs | 10–51% | **2** |
| 92 s | holding 10 VUs | 68% | 3 |
| 230 s | ramping to 60 VUs | 80% | 4 |
| 245–276 s | | 97–110% | 5 → **6** |
| 292–599 s | peak (60 VUs), then ramp-down | 79–174% | 6 (at max) |
| 615 s | k6 finished | 35% | 6 |
| 645–706 s | idle, scale-down window (120 s) | 5% | 6 |
| 722 s | | 5% | 5 |
| 752 s | | 4% | 4 |
| 783 s | | 5% | 3 |
| 813 s → end | | 4–7% | **2** |

**Summary:**

- **Before:** 2 replicas.
- **During:** 6, the maximum, reached about 4.5 minutes in.
- **After:** back to 2 about 3.3 minutes after the load ended.
- The `ItemsApiAutoscalerAtMax` alert went **pending** but didn't fire. The HPA was at max for about 7.5 minutes, below the alert's 10-minute window.

## 3. How CPU, requests, the HPA target, replicas and performance relate

1. **The HPA measures CPU as a percentage of the request, not of the limit or the node.**
   - Request is 100m and the target is 60%, so the HPA aims for about 60m of average CPU per pod.
2. **Replicas follow the formula `desired = ceil(current × currentCPU% ÷ 60%)`.**
   - Example from this run: 3 pods at 80% gives ceil(3 × 80 ÷ 60) = 4, which matches the 230 s step.
   - The scale-up policy (at most +2 pods per 15 s) and `maxReplicas: 6` cap what the formula asks for.
3. **When the HPA hits its cap, the extra load turns into higher CPU per pod.**
   - From 292 s, CPU stayed at 79–174% of the request with 6 replicas.
   - The pods ran above their request, which is allowed up to the 500m limit.
   - Latency stayed low (p95 13.5 ms), because the per-pod limit (500m) still had room above the request.
4. **The limit decides when "busy" turns into "slow".**
   - CPU is throttled per 100 ms window, not on average.
   - Short Python bursts hit the limit and were throttled in up to 40% of periods, even though average CPU was only 0.24 cores.
   - That's where the p99 tail (105 ms) and the 664 ms maximum come from.
5. **The request is a contract with the scheduler.**
   - 6 pods × 100m = 600m reserved.
   - A request set too high (say 1000m) would make the HPA see low percentages and never scale, and would waste node capacity.
   - A request set too low would scale early and pack too many pods onto a node.
6. **CPU only tracks load when the work is CPU-bound.**
   - In Scenario C (see [troubleshooting.md](troubleshooting.md)), requests waited 600 ms on a "slow dependency".
   - CPU fell, so the HPA *removed* a replica.
   - For I/O-bound services, scale on request rate or in-flight requests instead (KEDA or prometheus-adapter).

## 4. Drill traffic (client view, `load-tests/steady.js`, 10 VUs)

| Drill | Duration | Requests | Throughput | Failed | p95 |
|---|---|---|---|---|---|
| B: 20% injected 500s for ~3.5 min | 12 min | 69,002 | 95.8/s | 5.59% | 6.8 ms |
| C: +600 ms latency for ~7 min | 15 min | 51,268 | 57.0/s | 0.00% | **607 ms** |
| D: regression release for ~4 min | 12 min | 68,235 | 94.7/s | 9.65% | 7.9 ms |

- **Reading the failure rates:** in B and D the overall rate is diluted by the healthy minutes before and after the fault.
  - B: 20% of all routes failed for about 3.5 of 12 minutes.
  - D: 100% of single-item reads (about 30% of traffic) failed for about 4 of 12 minutes.

## 5. Reproduce

```bash
./scripts/run-load-test.sh smoke
```

```bash
SETTLE=300 ./scripts/run-load-test.sh ramp
```

```bash
PEAK_VUS=100 SETTLE=300 ./scripts/run-load-test.sh ramp
```

- Each run writes a log and a JSON summary to `load-tests/results/`. That folder is gitignored, because results belong to the machine that produced them.
- Watch the dashboard during a run: http://localhost:3000/d/items-api/items-api-health.

## 6. Limits of these results

- **Shared CPUs:** k6 ran on the same laptop as the cluster, and during the ramp it landed on `autoscale-worker`, competing with app pods for CPU.
- **Closed-model load:** k6 VUs wait for each response before sending the next request. When the server slows down, the offered load drops too. That hides queueing, as seen in Scenario C. An open model (`constant-arrival-rate`) would show it.
- **Not representative traffic:** single-item reads only use seed ids 1–3, because the in-memory store is per pod.
- **No production capacity claims.** These numbers support statements about *behaviour* (scaling, recovery, alerting), not "the service handles N req/s".
