# Deployment Guide (macOS, $0, no cloud account)

Anyone with a Mac can follow these steps top to bottom.

Each step is tagged with the phase that delivers it.
Steps tagged with a later phase than the current one will not work yet.

> **Status:** All phases through 9 complete. Every step below was run on this project (see [acceptance.md](acceptance.md) for evidence).

---

## Step 0 — Install prerequisites  `[Phase 1]`

```bash
brew install kind helm k6 trivy act
```

- Also needed: Docker Desktop (or OrbStack) **running**, plus `git`, `kubectl` and Python 3.12.
- Give Docker at least **4 CPUs and 6 GB RAM** (Docker Desktop → Settings → Resources). The monitoring stack needs it.

Check everything:

```bash
./scripts/check-prereqs.sh
```

- **Expected:** `All required tools present.` and exit code 0.
- **If you see** `docker daemon not running`: open Docker Desktop and wait for the whale icon to stop animating.

## Step 1 — Get the code  `[Phase 1]`

```bash
git clone https://github.com/<your-user>/kubernetes-autoscaling-platform.git
```

```bash
cd kubernetes-autoscaling-platform
```

## Step 2 — Run the API locally  `[Phase 2]`

```bash
make install
```

```bash
make test
```

```bash
make run
```

In a second terminal:

```bash
curl -s localhost:8000/health
```

```bash
curl -s -X POST localhost:8000/api/v1/items -H 'content-type: application/json' -d '{"name":"pen","price":2.5}'
```

```bash
curl -s localhost:8000/metrics | grep http_requests_total
```

- **Expected:** `47 passed` and 100% coverage from `make test`.
- **Expected:** `/health` returns `{"status":"ok"}`, and the POST returns `201` with `"id":4`.
- **Expected:** the metrics output shows `path="/api/v1/items"` with `status="201"`.
- Interactive API docs: http://localhost:8000/docs
- Stop the server with Ctrl-C. The last log lines should include `Application shutdown complete.`

## Step 3 — Run it in Docker  `[Phase 3]`

```bash
docker build -t items-api:v1 .
```

```bash
docker run --rm -p 127.0.0.1:8080:8000 --read-only --tmpfs /tmp --cap-drop ALL items-api:v1
```

In a second terminal:

```bash
curl -s localhost:8080/ready
```

- **Expected:** `{"status":"ready"}`.
- Ctrl-C stops it in under a second, and the last log line is `Finished server process [1]`.

Optional: run the API with Prometheus.

```bash
docker compose up -d --build
```

- Open http://localhost:9090/targets. `items-api` should be **UP**.
- Stop it with `docker compose down`.
- **If you see** `port is already allocated`: something else is using 8000 or 9090. Find it with `lsof -nP -iTCP:8000 -sTCP:LISTEN`.

Details and measured results are in [docker.md](docker.md).

## Step 4 — Create the local Kubernetes cluster  `[Phase 4]`

```bash
kind create cluster --config k8s/kind-config.yaml
```

```bash
kubectl wait --for=condition=Ready nodes --all --timeout=180s
```

```bash
kubectl get nodes
```

- **Expected:** 3 nodes (`autoscale-control-plane`, `autoscale-worker`, `autoscale-worker2`), all `Ready`. Creation takes about 2 minutes the first time.
- `kubectl version` must show a client within one minor version of the server (v1.37). Docker Desktop's bundled kubectl is older, so install the current one with `brew install kubernetes-cli`.
- **If nodes stay `NotReady` or kubectl times out:** check free disk with `df -h /System/Volumes/Data`. A nearly full Mac makes Docker's VM fail with `input/output error`. Free space, restart Docker Desktop, then `kind delete cluster --name autoscale` and create it again.

## Step 5 — Install metrics-server (required for autoscaling)  `[Phase 5]`

```bash
kubectl apply -k k8s/addons/metrics-server
```

```bash
kubectl -n kube-system rollout status deploy/metrics-server
```

```bash
kubectl top nodes
```

- **Expected:** CPU and memory numbers. For the first ~30 s you may see `Metrics API not available`, which is normal.
- Pinned to v0.9.0, with `--kubelet-insecure-tls` because kind's kubelets use self-signed certificates. Use that flag on local clusters only.

## Step 6 — Deploy the app  `[Phase 4–5]`

Build the tag the overlay asks for (`newTag` in `k8s/overlays/local/kustomization.yaml`, currently `v3`):

```bash
docker build -t items-api:v3 --build-arg APP_VERSION=0.3.0 .
```

```bash
kind load docker-image items-api:v3 --name autoscale
```

```bash
kubectl apply -k k8s/overlays/local
```

```bash
kubectl -n autoscale-app rollout status deploy/items-api
```

```bash
kubectl -n autoscale-app get pods -o wide
```

- **Expected:** `successfully rolled out`, and 2 pods `Running`, one per worker node.
- Check the autoscaler with `kubectl -n autoscale-app get hpa`. `TARGETS` should show a number like `cpu: 5%/60%`, not `<unknown>`.

Reach it from your Mac:

```bash
kubectl -n autoscale-app port-forward svc/items-api 8080:80
```

In a second terminal:

```bash
curl -s localhost:8080/api/v1/stats
```

- **Expected:** JSON with `"pod":"items-api-..."`.
- port-forward always reaches the same pod. To see load balancing, run `./scripts/pod-kill-drill.sh`, which also shows self-healing.
- **If you see** `ErrImagePull`: the image wasn't loaded into kind. Re-run `kind load docker-image`.

## Step 7 — Install Prometheus + Grafana  `[Phase 6]`

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
```

```bash
kubectl create namespace monitoring
```

```bash
kubectl -n monitoring create secret generic grafana-admin --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -base64 18)"
```

```bash
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack --version 92.9.0 -n monitoring -f monitoring/values-local.yaml --wait --timeout 10m
```

```bash
kubectl apply -k monitoring
```

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

- **Needs:** about 2.5 GB of free disk for the images, and about 1 GB of RAM in Docker.
- **Expected:** 5 pods `Running` in `monitoring`.
- Get the password with `kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d`.
- Open http://localhost:3000/d/items-api/items-api-health.
- Prometheus targets (port-forward `svc/monitoring-kube-prometheus-prometheus 9090:9090`) should list `serviceMonitor/monitoring/items-api/0` as `2/2 up`.
- Details, alert tests and evidence are in [monitoring.md](monitoring.md).

## Step 8 — Load test and watch it scale  `[Phase 7]`

Smoke test first (30 s). If it fails, a bigger test is pointless:

```bash
./scripts/run-load-test.sh smoke
```

- **Expected:** `failed=0.00%`, p95 in single-digit milliseconds, and `thresholds: PASSED`.

The full ramp (about 10 min of load, plus 5 min watching scale-down):

```bash
SETTLE=300 ./scripts/run-load-test.sh ramp
```

- **Expected:**
  - replicas rise from 2 to 6 within about 5 minutes,
  - `thresholds: PASSED`,
  - replicas back to 2 about 3 minutes after the load ends.
- **Observed run:** 190,254 requests, 0% errors, p95 10.4 ms. See [load-test-results.md](load-test-results.md).
- k6 runs as a Job **inside** the cluster, so traffic spreads across all replicas. Running k6 from the Mac through port-forward would reach only one pod.

Failure drills (each takes about 10 minutes and needs the monitoring stack from Step 7):

```bash
./scripts/pod-kill-drill.sh
```

- Scenarios B–D, with the exact commands, are in [troubleshooting.md](troubleshooting.md).

## Step 9 — Clean up (frees all resources)

```bash
kind delete cluster --name autoscale
```

- This deletes the whole cluster, including monitoring and load-test results inside it. Local files in `load-tests/results/` stay.
- To also free the Docker images this project built:

```bash
docker image rm items-api:v1 items-api:v2 items-api:v3 items-api:v4 items-api:v5-regression items-api:v3-broken
```

---

## Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker not running | Start Docker Desktop |
| Pod `ImagePullBackOff` | Image not loaded into kind | `kind load docker-image ...` and use `imagePullPolicy: IfNotPresent` |
| HPA shows `<unknown>/60%` | metrics-server missing, pods under ~1 min old, or no CPU request | Step 5; wait a minute; check `resources.requests.cpu` |
| `kubectl apply -k` rolled the app back to an old version | `kubectl set image` was used and the overlay still has the old `newTag` | Change `newTag` in the overlay and apply |
| Pod `CrashLoopBackOff` | App error on start | `kubectl logs <pod> --previous` |
| Pods `Pending` | Not enough CPU/RAM in Docker | Raise Docker resources or lower requests |
