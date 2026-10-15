# syntax=docker/dockerfile:1
#
# Production-conscious image for the Items API.
#
# Build:   docker build -t items-api:v1 .
# Run:     docker run --rm -p 8000:8000 items-api:v1
# Inspect: docker image history items-api:v1     # one line per layer, with its size
#
# Two stages:
#   builder  — has pip; installs dependencies into a virtualenv at /opt/venv.
#   runtime  — copies only that virtualenv plus our code. No pip, no build caches,
#              no tests, so there is less to download and less to attack.
#
# The base image is pinned to an exact Python patch and Debian release ("trixie").
# `python:3.12-slim` alone would silently change underneath us on every rebuild.
# For full reproducibility you can also pin the digest:
#   FROM python:3.12.15-slim-trixie@sha256:<digest>
# You get the digest from `docker image inspect python:3.12.15-slim-trixie --format '{{index .RepoDigests 0}}'`.
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie

# ---------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy ONLY the requirements first. Docker caches each layer, so as long as
# requirements.txt is unchanged, editing app code skips this slow install step.
COPY requirements.txt .
RUN pip install -r requirements.txt \
 # pip is not needed at runtime. Removing it shrinks the image and removes pip's own CVEs from scans.
 && pip uninstall -y pip

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="items-api" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.description="Demo FastAPI service for the Kubernetes auto-scaling platform"

ENV PATH="/opt/venv/bin:$PATH" \
    # Don't write .pyc files. The filesystem is read-only in Kubernetes anyway.
    PYTHONDONTWRITEBYTECODE=1 \
    # Flush stdout immediately so `docker logs` and `kubectl logs` show lines in real time.
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}

# A dedicated unprivileged user with a fixed NUMERIC uid. Kubernetes'
# `runAsNonRoot: true` can only verify a numeric USER, not a name.
# The base image also ships its own pip at /usr/local/bin/pip. Remove it in the SAME
# layer, so the final filesystem (what runs and what Trivy scans) has no package installer.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
 && python -m pip uninstall -y pip \
 && rm -rf /root/.cache

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# The code stays owned by root, so the app user can read it but can't modify it.
COPY app/ ./app/

USER 10001:10001

# Documentation only: EXPOSE does not publish the port. `-p 8000:8000` or a k8s Service does that.
EXPOSE 8000

# Used by `docker run` and Docker Compose. Kubernetes ignores this and uses its own probes (Phase 5).
# Uses Python's stdlib so we don't install curl just for a health check.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# Exec form (a JSON array) makes Uvicorn PID 1, so it receives SIGTERM directly.
# Shell form (`CMD uvicorn ...`) would run under /bin/sh, which does NOT forward
# signals. Docker and Kubernetes would then wait out the grace period and SIGKILL it.
# --host 0.0.0.0: listen on the container's network interface, not just loopback.
# --timeout-graceful-shutdown 20: finish in-flight requests within 20s; below k8s' 30s default grace period.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "20"]
