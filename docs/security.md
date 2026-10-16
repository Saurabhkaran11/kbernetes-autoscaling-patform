# Security

> **This project is not production-secure, and doesn't claim to be.**
>
> It applies practical, verifiable controls for a local learning platform. Each control below lists the **evidence** that it works.
>
> Section 4 lists what is missing for a real production system.

## 1. Controls in place, with evidence

### Container image

| Control | Where | Evidence |
|---|---|---|
| Non-root user, numeric uid 10001 | `Dockerfile` (`USER 10001:10001`) | `id` in container → `uid=10001(app)`. The CI smoke test asserts `id -u` = 10001. |
| App code not writable by the app | code owned by root | `touch /app/app/x` → Permission denied |
| No package installer in the image | pip removed from venv **and** base | `command -v pip` → not present |
| Minimal build context | allow-list `.dockerignore` | only `app/` and `requirements.lock` are sent to the build |
| Exec-form `CMD` (clean SIGTERM) | `Dockerfile` | stops in 0.57 s with exit 0, vs 10.2 s / SIGKILL with shell form ([docker.md](docker.md)) |
| Pinned base image | `python:3.12.15-slim-trixie` | Dockerfile `ARG PYTHON_IMAGE` |
| **Hash-pinned dependencies** (direct and indirect) | `requirements.lock`, `pip install --require-hashes --no-deps` | Corrupting every `uvicorn` hash fails the build with `THESE PACKAGES DO NOT MATCH THE HASHES` (exit 1) |

### Kubernetes

| Control | Where | Evidence |
|---|---|---|
| Pod Security Admission **restricted** on app and loadtest namespaces | `k8s/base/namespace.yaml`, `load-tests/k8s/namespace.yaml` | A root `nginx` pod is rejected: `violates PodSecurity "restricted:latest"` |
| `runAsNonRoot`, uid 10001, seccomp `RuntimeDefault` | `k8s/base/deployment.yaml` | the API server accepts the pod under the restricted level |
| No privilege escalation, all capabilities dropped, read-only root fs | container `securityContext` | writes to `/app` and `/etc` → Read-only file system |
| No service-account token mounted | `automountServiceAccountToken: false` | the app never calls the Kubernetes API |
| CPU and memory requests and limits on every container | deployment, monitoring values, k6 Job | `kubectl describe pod`. Limits were sized from measurement after an actual OOMKill ([postmortem](incident-postmortem.md)). |
| No external exposure | Service type `ClusterIP`. All port-forwards and the kind port mapping bind `127.0.0.1`. | nothing is reachable from your network, only from this Mac |
| **NetworkPolicy: default deny, plus a narrow allow** | `k8s/base/networkpolicy.yaml` | Tested live on kind (kindnetd v20260820): from `loadtest` → 200; from `default` → timeout; Prometheus still scrapes (`up=1`); API egress to `1.1.1.1:443` times out; probes and port-forward still work |
| Config separate from secrets | ConfigMap holds only `APP_NAME`, `APP_LOG_LEVEL`, `APP_SEED_ITEMS` | no credentials anywhere in `k8s/` |
| Grafana admin password not in git | Secret created at install with `openssl rand` (`monitoring/values-local.yaml`) | grepping the repo for the live password finds nothing (checked in Phase 6) |

### Application

| Control | Where | Evidence |
|---|---|---|
| Input validation, unknown fields rejected | `app/models.py` (Pydantic, `extra="forbid"`) | tests: 10 invalid-input cases → 422 |
| Safe error responses (no stack traces to clients) | `app/metrics.py` middleware | test: an unhandled error returns `{"detail":"Internal server error","request_id":…}` with no internals |
| Request IDs sanitised | `^[A-Za-z0-9._-]{1,64}$` | test: `"bad id; drop table"` is replaced |
| Health endpoints reveal nothing sensitive | `/health`, `/ready` return only `{"status": …}` | tests |
| Fault injection off by default, validated, loud | `app/faults.py`, `app/config.py` | tests: defaults 0; invalid values stop startup; warning logged when on |
| Static security lint | ruff `S` (bandit) rules | `make lint` clean |

### Pipeline and supply chain (`.github/workflows/ci.yml`)

| Control | Evidence |
|---|---|
| Actions pinned to full commit SHAs | `actions/checkout@3d3c42e…`, `setup-python@5fda3b9…`, `upload-artifact@cf430e0…` |
| Tool images pinned by digest | `aquasec/trivy:0.76.0@sha256:2416d1f…`, `prom/prometheus:v3.15.0@sha256:efd719c…` |
| Read-only `GITHUB_TOKEN` (`permissions: contents: read`) | workflow top-level |
| `persist-credentials: false` on checkout | the token isn't left in `.git/config` for later steps |
| No secrets required | the workflow references none |

## 2. Security gates (`make security`, and the same checks in CI)

| # | Gate | Tool | Fails the build when | Current result |
|---|---|---|---|---|
| 1 | Known-vulnerable Python packages | `pip-audit` on `requirements.lock` (hashes required) | any known vulnerability | **0 found** (runtime and dev) |
| 2 | Secrets committed to the repo | `trivy fs --scanners secret` | any secret found | **clean** |
| 3 | Misconfiguration | `trivy config` on the Dockerfile and **rendered** manifests | MEDIUM or above | Dockerfile 27/27 pass. Rendered app, monitoring and load-tests: 0 failures (1 accepted exception, below). |
| 4 | Image vulnerabilities | `trivy image --ignore-unfixed` | HIGH/CRITICAL **with a fix available** | **0 fixable.** 44 HIGH without a fix (below). |

**Each gate was tested by making it fail on purpose:**

- **Gate 2:** a planted GitHub-style token in `app/` → exit 1, `GitHub Personal Access Token`.
- **Gate 3:** a planted `privileged: true` pod in the rendered output → exit 1, `KSV-0017 Privileged` (6 failures).
- **Hash pinning:** every `uvicorn` hash corrupted → build exit 1.
- **Alert rules:** three rule mutations, each caught by promtool tests ([monitoring.md](monitoring.md)).

**Why scan *rendered* manifests:**

- Scanning `k8s/base/` flagged "image tag `:latest`" and "default namespace". Both are false, because the overlay sets `items-api:v4` and Kustomize sets `autoscale-app`.
- `kubectl apply -k` sends the rendered output, so that's what must be secure.

## 3. Accepted risks and exceptions

| Item | Decision | Review |
|---|---|---|
| **44 HIGH CVEs in Debian base packages** (`util-linux` family: `libmount1`, `libblkid1`, `mount`, `login`, …) with **no fixed version available** | Reported, not blocking. Rebuilding can't fix them, so blocking would only stop every build. The gate still fails as soon as a fix exists. The official `python:3.12.15-slim-trixie` base shows the same 44. | Re-check at each base-image bump. Option: a distroless or Wolfi base (fewer packages, but no shell for debugging). |
| **KSV-01010** "ConfigMap with sensitive content" on `APP_LOG_LEVEL=INFO` | False positive from a key-name heuristic. Ignored in `.trivyignore.yaml` for **that check and that file only**. | **Expires 2027-04-15** |
| `--kubelet-insecure-tls` on metrics-server | Required on kind (self-signed kubelet certs). **Local clusters only.** | n/a in production |
| Fault-injection code ships in the image | Kept visible for learning. Off by default, and validated. | Compile out or gate by environment in production. |

**A correction worth recording:** an earlier draft of this file said kind's CNI doesn't enforce NetworkPolicy. A deny-all test showed it does (curl timed out, exit 28), so the policy was written and verified instead.

## 4. Known limitations, not production-ready

- **No TLS anywhere.** Traffic is plain HTTP inside the cluster and over port-forward. Production needs TLS at the ingress, and usually mTLS between services (service mesh).
- **No authentication or authorization on the API.** Anyone who can reach it can create items.
- **No rate limiting.** One client could exhaust a pod. Use an ingress or API-gateway rate limit.
- **Network policy covers only the app namespace.**
  - `monitoring` and `loadtest` have no policies of their own.
  - Policies are L3/L4 only: they control which pods may connect, but not which HTTP paths they call.
- **Images are not signed, and there is no SBOM.** Production would sign with cosign and verify at admission (Kyverno or a policy controller), and publish an SBOM.
- **Images aren't scanned at runtime.** CVEs published after the build aren't caught until the next build. Schedule nightly scans of running images.
- **The Trivy DB date matters.** A scan reflects the vulnerability database on that day, so the results above are a snapshot from 2026-10-15.
- **Grafana and Prometheus run with chart defaults** apart from the changes listed in `values-local.yaml`. No SSO, single admin user, no HA.
- **Secrets management** is just a Kubernetes Secret (base64, not encrypted at rest on kind). Production would use encryption at rest and an external secret store (Vault, a cloud KMS, or External Secrets).
- **Audit logging** of Kubernetes API calls isn't configured on kind.

## 5. Commands

Run all four gates:

```bash
make security
```

Lint, including the bandit-style `S` rules:

```bash
make lint
```

Re-pin dependencies after editing `requirements.txt`:

```bash
make lock
```
