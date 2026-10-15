"""Application entrypoint.

Run locally:
    uvicorn app.main:app --port 8000 --no-access-log
    open http://localhost:8000/docs     # interactive OpenAPI docs

Graceful shutdown, step by step (what happens on Ctrl-C or Kubernetes' SIGTERM):
    1. Uvicorn receives SIGTERM and stops accepting new connections.
    2. Uvicorn waits for in-flight requests to finish, up to --timeout-graceful-shutdown.
    3. The lifespan "shutdown" half below runs: readiness flips to False and we log it.
    4. The process exits. Uvicorn re-raises the signal after a clean shutdown.
       Run from a shell, that shows as exit code 143 (128 + SIGTERM's 15). Inside
       the container Uvicorn is PID 1, the kernel ignores the re-raised signal, and
       the exit code is 0 (measured in Phase 3). Both are normal terminations; the
       "Application shutdown complete." log line is the proof of a clean drain.
In Kubernetes the pod is also removed from the Service endpoints when it starts
terminating. Phase 5 adds a short preStop sleep so that removal reaches every node
before step 1. Without it, a few requests can still hit a closing pod.
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.logging_config import setup_logging
from app.metrics import ITEMS_STORED, MetricsMiddleware
from app.routes import health, items, stats
from app.store import SEED_ITEMS, ItemStore

log = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Tests call it to get a fresh app with an empty store each time.

    Example:
        >>> from fastapi.testclient import TestClient
        >>> with TestClient(create_app()) as client:
        ...     client.get("/health").json()
        {'status': 'ok'}
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ---- startup ----
        if settings.seed_items:
            for data in SEED_ITEMS:
                app.state.store.create(dict(data))
        ITEMS_STORED.set(app.state.store.count())
        app.state.ready = True
        log.info("startup complete", extra={"pod": settings.pod_name, "version": settings.app_version})
        yield
        # ---- shutdown ----
        app.state.ready = False
        log.info("shutdown: readiness off, in-flight requests drained", extra={"pod": settings.pod_name})

    app = FastAPI(
        title="Items API",
        version=settings.app_version,
        description="Demo service for the Kubernetes auto-scaling platform. Data is in-memory and per-pod.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = ItemStore()
    app.state.ready = False  # becomes True only after startup finishes
    app.state.started_at = datetime.now(UTC)
    app.state.started_monotonic = time.monotonic()

    app.add_middleware(MetricsMiddleware, pod_name=settings.pod_name)
    app.include_router(health.router)
    app.include_router(items.router)
    app.include_router(stats.router)
    return app


# Module-level instance that Uvicorn imports: `uvicorn app.main:app`.
app = create_app()
