"""Health, readiness and metrics endpoints. These are for the platform, not for users.

/health (liveness):  "Is the process alive?" Kubernetes RESTARTS the pod if it fails.
                     Keep it trivial; it must not check dependencies. If a database
                     outage failed liveness, Kubernetes would restart every pod and
                     make the outage worse.
/ready (readiness):  "Should I get traffic?" Kubernetes REMOVES the pod from the
                     Service (no restart) while it fails. It returns 503 before startup
                     finishes and once shutdown begins, so no new requests arrive
                     while the pod drains.
/metrics:            Scraped by Prometheus every ~15s.

Example:
    $ curl -i localhost:8000/ready
    HTTP/1.1 200 OK
    {"status":"ready"}
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.metrics import metrics_response
from app.models import StatusResponse

router = APIRouter(tags=["platform"])


@router.get("/health", response_model=StatusResponse)
async def health() -> dict:
    return {"status": "ok"}


@router.get(
    "/ready",
    response_model=StatusResponse,
    responses={503: {"model": StatusResponse, "description": "Starting up or shutting down"}},
)
async def ready(request: Request):
    if getattr(request.app.state, "ready", False):
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not ready"})


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return metrics_response()
