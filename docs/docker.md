# Docker: Concepts, Commands and Evidence

Everything in the "Evidence" sections was run on 2026-10-15 on an Apple Silicon Mac (Docker 29.5.2, 8 CPUs and 7.8 GB allocated to Docker).

## 1. Concepts, tied to this project

### Image vs container

- **Image:** a read-only, layered filesystem plus metadata (user, command, ports). `items-api:v1` is an image.
- **Container:** a running process started from an image, with a thin writable layer on top. You can start many containers from one image. That is exactly what Kubernetes replicas are.
- **Analogy:** an image is a class and a container is an instance.

```bash
docker image ls items-api
```

```bash
docker ps
```

### Image layers

- Each Dockerfile instruction that changes files (`RUN`, `COPY`) creates a layer.
- Layers are cached and shared. If a layer's inputs are unchanged, Docker reuses it.
- That is why the Dockerfile copies `requirements.txt` and installs dependencies **before** copying `app/`. Editing code then reuses the slow dependency layer.
- Deleting a file in a later layer hides it, but doesn't shrink earlier layers. Clean up in the **same** `RUN` that created the files.

```bash
docker image history items-api:v1
```

### Multi-stage builds

- **`builder` stage:** has pip and installs dependencies into `/opt/venv`.
- **`runtime` stage:** copies only `/opt/venv` and `app/`.
- Build tools never reach the final image.

### Container ports

- The app listens on port 8000 **inside** the container's own network namespace.
- `EXPOSE 8000` is documentation only.
- `-p 127.0.0.1:8080:8000` publishes it: Mac port 8080 maps to container port 8000, reachable only from this Mac.
- `--host 0.0.0.0` matters. An app bound to `127.0.0.1` inside a container can't be reached from outside it.

### Container networking

- **`docker run`:** containers join the default bridge network.
- **Compose:** creates a private network per project, with built-in DNS. Prometheus reaches the API at `http://api:8000` by **service name**.
- Kubernetes works the same way: a Service gets a DNS name such as `items-api.autoscale-app.svc`.

### Environment variables

- Configuration is injected at run time (`-e APP_LOG_LEVEL=DEBUG`), so one image serves every environment.
- Kubernetes injects them from a ConfigMap (non-secret) or a Secret.
- Never bake secrets into an image with `ENV` or `COPY`. Anyone who can pull the image can read every layer.

### Persistent storage

- A container's writable layer disappears when the container is removed.
- **Bind mount:** a host path mounted in, e.g. `./docker/prometheus.yml` (read-only config).
- **Named volume:** Docker-managed storage that outlives containers, e.g. `prometheus-data`. It survives `docker compose down` and is deleted by `down -v`.
- The API deliberately has **no** volume. Its data is in memory, and losing it on restart is a documented limitation.

## 2. Security measures in the image

| Measure | How | Verified by |
|---|---|---|
| Non-root user | `USER 10001:10001` (numeric, so Kubernetes `runAsNonRoot` can verify it) | `id` → `uid=10001(app)` |
| Code not writable by the app | `app/` owned by root | `touch /app/app/x` → Permission denied |
| No package installer | pip removed from the venv **and** the base image | `command -v pip` → not present |
| Minimal build context | allow-list `.dockerignore` (`app/`, `requirements.txt` only) | `/app` contains only the 11 `app/*.py` files |
| No secrets in image | nothing secret is copied; config comes from env vars | `docker image inspect` → Env has no credentials |
| Pinned base | `python:3.12.15-slim-trixie` | Dockerfile `ARG PYTHON_IMAGE` |
| Runtime hardening (run flags) | `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`, memory/CPU limits | writes to `/app` and `/etc` → Read-only file system |

## 3. Commands

Build:

```bash
docker build -t items-api:v1 --build-arg APP_VERSION=0.1.0 .
```

Run with the same hardening Kubernetes will use:

```bash
docker run --rm -p 127.0.0.1:8080:8000 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --memory 256m --cpus 0.5 -e POD_NAME=docker-local items-api:v1
```

Check it (second terminal):

```bash
curl -s localhost:8080/api/v1/stats
```

```bash
docker ps --format '{{.Names}} {{.Status}}'
```

- **Expected:** stats JSON with `"pod":"docker-local"`, and status `Up ... (healthy)` after about 5 seconds.

Run the API plus Prometheus:

```bash
docker compose up -d --build
```

```bash
docker compose ps
```

- Open http://localhost:9090/targets. The `items-api` target should be **UP**.

Stop:

```bash
docker compose down
```

## 4. Evidence (observed results)

### Image

- **Size:** `items-api:v1` is 227 MB. The `python:3.12-slim` base alone is 205 MB.
- **What we add:** about 22 MB in total (an 18.8 MB virtualenv, 65 kB of code, 41 kB for the user and setup layer).
- **Cache:** a rebuild with nothing changed reported every step `CACHED`.

### Running container

| Check | Result |
|---|---|
| HEALTHCHECK | `healthy` after about 5 s |
| `GET /ready` | `200 {"status":"ready"}` |
| `POST /api/v1/items` | `201`, id 4 (after 3 seed items) |
| `GET /api/v1/stats` | `"pod":"docker-local"` (env var applied) |
| `id` inside container | `uid=10001(app) gid=10001(app)` |
| PID 1 | `/opt/venv/bin/python /opt/venv/bin/uvicorn ...` |
| `docker stop` (SIGTERM) | stopped in **0.57 s**, exit code **0**, log ends `Application shutdown complete.` |

### Why exec-form `CMD` matters (measured)

The same image, started with a shell wrapper (`sh -c 'uvicorn ...'`):

| | Exec form (our Dockerfile) | Shell form |
|---|---|---|
| PID 1 | uvicorn | `sh` |
| `docker stop -t 10` | 0.57 s | **10.2 s** (full timeout) |
| Exit code | 0 | **137** (SIGKILL) |
| Clean shutdown logged | yes | **no** |

`sh` does not forward SIGTERM to its child. Docker waits out the timeout and then SIGKILLs everything, so in-flight requests are cut off.

### Compose + Prometheus

- **Target:** `items-api http://api:8000/metrics up` with no error.
- **Test traffic:** 10 × `GET /api/v1/items` and 3 × `GET /api/v1/items/999`.
- **PromQL** `sum by (path,status) (http_requests_total{job="items-api"})` returned:
  - `/api/v1/items` `200` → 10
  - `/api/v1/items/{item_id}` `404` → 3
- `/health` showed more calls than we made, because the Docker HEALTHCHECK also calls it every 10 s.

### Trivy image scan (HIGH and CRITICAL only, Trivy 0.76.0)

- **Python packages in `/opt/venv`:** 0 findings.
- **Debian OS packages:** 44 HIGH, 0 CRITICAL.
  - All 44 are inherited from the official `python:3.12.15-slim-trixie` base, which scans identically (44).
  - None has a fixed version available yet, so rebuilding won't clear them.
  - Most affected packages: `util-linux` family (`libmount1`, `libblkid1`, `mount`, `login`, ...).
- **Options, decided in Phase 8:**
  - Gate CI on `--ignore-unfixed` so builds fail only on CVEs that have a fix.
  - Or move to a distroless/Wolfi base with fewer OS packages. Trade-off: no shell for debugging.

## 5. Simpler alternatives

- **Single-stage build:** simpler Dockerfile, but pip ends up in the final image.
- **`python:3.12` (full image):** about 1 GB and more CVEs. Only worth it if you compile C extensions.
- **Buildpacks (`pack build`):** no Dockerfile at all, but less control and less to learn.
