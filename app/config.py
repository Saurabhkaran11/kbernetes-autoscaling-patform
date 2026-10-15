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
    # ---- Fault injection (failure drills only; both default to OFF) ----
    # Share of /api/* requests answered with a 500, from 0.0 to 1.0. Used for Scenario B.
    fault_error_rate: float = 0.0
    # Extra delay added to every /api/* request, in milliseconds. Used for Scenario C.
    fault_latency_ms: int = 0

    @property
    def faults_enabled(self) -> bool:
        return self.fault_error_rate > 0 or self.fault_latency_ms > 0


def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "items-api"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        log_level=os.getenv("APP_LOG_LEVEL", "INFO").upper(),
        pod_name=os.getenv("POD_NAME") or socket.gethostname(),
        seed_items=os.getenv("APP_SEED_ITEMS", "true").lower() in ("1", "true", "yes"),
        fault_error_rate=_bounded_float("FAULT_ERROR_RATE", 0.0, 1.0),
        fault_latency_ms=int(_bounded_float("FAULT_LATENCY_MS", 0, 30_000)),
    )


def _bounded_float(name: str, low: float, high: float) -> float:
    """Read a numeric env var and refuse nonsense, so a typo fails at startup, not mid-drill.

    Example:
        FAULT_ERROR_RATE=0.2  -> 0.2
        FAULT_ERROR_RATE=20   -> ValueError (20 is not a share between 0 and 1)
        unset                 -> 0.0 (fault off)
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return 0.0
    value = float(raw)
    if not low <= value <= high:
        raise ValueError(f"{name}={raw!r} must be between {low} and {high}")
    return value
