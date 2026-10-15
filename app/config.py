"""Application settings, read once from environment variables.

Why environment variables: the same Docker image must run on a laptop, in CI and
in Kubernetes. Only configuration changes between them, and Kubernetes injects it
from a ConfigMap as env vars (12-factor app, factor III).

Example:
    $ APP_LOG_LEVEL=DEBUG POD_NAME=items-api-7c9f uvicorn app.main:app
    >>> get_settings().log_level
    'DEBUG'
"""

import os
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    log_level: str
    # Kubernetes sets HOSTNAME to the pod name. We report it in /api/v1/stats and in
    # logs so you can see which replica answered — useful when watching load balancing.
    pod_name: str
    # Seed a few demo items at startup so GET /api/v1/items isn't empty on a fresh pod.
    seed_items: bool


def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "items-api"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        log_level=os.getenv("APP_LOG_LEVEL", "INFO").upper(),
        pod_name=os.getenv("POD_NAME") or socket.gethostname(),
        seed_items=os.getenv("APP_SEED_ITEMS", "true").lower() in ("1", "true", "yes"),
    )
