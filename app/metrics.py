"""Prometheus metrics and the HTTP middleware that records them.

Metric types used:
    Counter   — only goes up. Prometheus derives rates with rate().
    Histogram — counts observations into buckets, so Prometheus can compute
                percentiles (p95) across every pod with histogram_quantile().
    Gauge     — goes up and down (requests in flight right now).

The `path` label is the route TEMPLATE ("/api/v1/items/{item_id}"), never the raw
URL ("/api/v1/items/42"). Raw URLs would create a new time series per item id,
the "cardinality explosion" that slows or crashes Prometheus.

Example PromQL (used in Phase 6):
    # requests per second by status, over the last minute
    sum by (status) (rate(http_requests_total[1m]))
    # 95th percentile latency
    histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
"""

import logging
import re
import time
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

access_log = logging.getLogger("app.access")
error_log = logging.getLogger("app.error")

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled.",
    ["method", "path", "status"],
)
HTTP_ERRORS = Counter(
    "http_errors_total",
    "HTTP responses with status >= 500 (server errors only; 4xx are client mistakes).",
    ["method", "path", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency in seconds.",
    ["method", "path"],
    # Buckets chosen around a 500 ms latency SLO so the alert threshold lands on a bucket edge.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being processed.",
)
ITEMS_STORED = Gauge(
    "items_stored",
    "Items currently held in this pod's in-memory store.",
)

# Accept a caller's X-Request-ID only if it looks sane; otherwise generate one.
# This stops someone injecting newlines or huge strings into our logs.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _route_template(request: Request) -> str:
    # FastAPI stores the matched route in the ASGI scope after routing.
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Wraps every request: times it, counts it, logs it, and turns crashes into safe 500s.

    Example: GET /api/v1/items/999 (not found) produces
        http_requests_total{method="GET",path="/api/v1/items/{item_id}",status="404"} += 1
        one JSON access-log line with status 404 and duration_ms
        response header X-Request-ID: <id>
    """

    def __init__(self, app, pod_name: str):
        super().__init__(app)
        self.pod_name = pod_name

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        HTTP_IN_PROGRESS.inc()
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled bug in a handler. Log the full traceback for us, but send the
            # client only a generic message — stack traces can reveal internals.
            error_log.exception(
                "unhandled exception",
                extra={"request_id": request_id, "path": request.url.path, "pod": self.pod_name},
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )
        finally:
            HTTP_IN_PROGRESS.dec()

        duration = time.perf_counter() - start
        path = _route_template(request)
        method = request.method
        status = str(response.status_code)

        HTTP_REQUESTS.labels(method, path, status).inc()
        HTTP_LATENCY.labels(method, path).observe(duration)
        if response.status_code >= 500:
            HTTP_ERRORS.labels(method, path, status).inc()

        response.headers["X-Request-ID"] = request_id
        access_log.info(
            "request",
            extra={
                "method": method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "request_id": request_id,
                "pod": self.pod_name,
                "client": request.client.host if request.client else None,
            },
        )
        return response


def metrics_response() -> Response:
    """Render every registered metric in the Prometheus text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
