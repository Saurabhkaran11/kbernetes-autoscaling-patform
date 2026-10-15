"""GET /api/v1/stats — a quick view of this replica.

`pod` shows which replica answered. Call it a few times through the Kubernetes
Service and you will see the name change as requests are load-balanced.

Example:
    $ curl -s localhost:8000/api/v1/stats
    {"app":"items-api","version":"0.1.0","pod":"items-api-7c9f","item_count":3,
     "uptime_seconds":42.17,"started_at":"2026-10-15T10:00:00Z"}
"""

import time

from fastapi import APIRouter, Request

from app.models import Stats

router = APIRouter(prefix="/api/v1", tags=["stats"])


@router.get("/stats", response_model=Stats)
async def stats(request: Request) -> dict:
    state = request.app.state
    return {
        "app": state.settings.app_name,
        "version": state.settings.app_version,
        "pod": state.settings.pod_name,
        "item_count": state.store.count(),
        "uptime_seconds": round(time.monotonic() - state.started_monotonic, 2),
        "started_at": state.started_at,
    }
