# Deployment Guide (macOS, $0, no cloud account)

Anyone with a Mac can follow these steps top to bottom.

Each step is tagged with the phase that delivers it.
Steps tagged with a later phase than the current one will not work yet.

> **Status:** Phases 1–5 complete. Steps 0–6 work today.

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

Build the tag the overlay asks for (`newTag` in `k8s/overlays/local/kustomization.yaml`, currently `v2`):

```bash
docker build -t items-api:v2 --build-arg APP_VERSION=0.2.0 .
```

```bash
kind load docker-image items-api:v2 --name autoscale
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
helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace -f monitoring/values-local.yaml
```

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80
```

- Open http://localhost:3000 and load the "Items API Health" dashboard.

## Step 8 — Load test and watch it scale  `[Phase 7]`

```bash
kubectl -n autoscale-app get hpa -w
```

```bash
k6 run load-tests/ramp.js
```

- **Expected:** replicas rise from 2 toward 6 during load, then fall back about 5 minutes after load ends.

## Step 9 — Clean up (frees all resources)

```bash
kind delete cluster --name autoscale
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
