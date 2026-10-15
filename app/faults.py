"""Fault injection for failure drills (Phase 7). OFF unless an env var turns it on.

Why build this into the app: Scenarios B (HTTP 500s) and C (slow responses) need the
service to misbehave the way a real bug or a slow dependency would, so the alerts,
dashboards and runbooks can be exercised end to end. Killing pods can't simulate that.

Why a FastAPI dependency and not middleware: a dependency runs AFTER routing, so the
metrics record the real route template ("/api/v1/items/{item_id}"). Middleware runs
before routing, and an injected error would be labelled path="unmatched". The dashboard
would then send an engineer to the wrong place.

Safety rules:
  * Both knobs default to 0, so a normal deployment has no faults.
  * Only the /api/v1 routers use this dependency. /health, /ready and /metrics stay
    truthful, so a drill tests our alerting, not Kubernetes restarting pods.
  * Every injected error is logged at WARNING with "fault injected", so nobody mistakes
    a drill for a real bug when reading logs.
  * In a real production system, compile this out or gate it behind an environment
    check. Here it stays visible for learning.

Turn it on in the cluster (Scenario B, 30% errors):
    kubectl -n autoscale-app set env deploy/items-api FAULT_ERROR_RATE=0.3
Turn it off:
    kubectl -n autoscale-app set env deploy/items-api FAULT_ERROR_RATE-
"""

import asyncio
import logging
import random

from fastapi import HTTPException, Request

log = logging.getLogger("app.faults")


async def inject_faults(request: Request) -> None:
    """Router dependency: maybe delay, maybe fail, according to the app's settings.

    Example (FAULT_ERROR_RATE=1.0):
        GET /api/v1/items -> 500 {"detail": "Internal server error"}
        GET /health       -> 200 (this route doesn't use the dependency)
    """
    settings = request.app.state.settings
    if settings.fault_latency_ms:
        # asyncio.sleep waits without burning CPU, like a slow downstream call would.
        # So latency rises while CPU (and therefore the HPA) stays flat — a real-world trap.
        await asyncio.sleep(settings.fault_latency_ms / 1000)

    # random() is fine here: this picks which requests fail in a drill, it isn't security.
    if settings.fault_error_rate and random.random() < settings.fault_error_rate:  # noqa: S311
        log.warning(
            "fault injected: returning 500",
            extra={"path": request.url.path, "request_id": getattr(request.state, "request_id", None)},
        )
        raise HTTPException(status_code=500, detail="Internal server error")
