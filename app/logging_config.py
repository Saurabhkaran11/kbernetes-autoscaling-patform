"""Structured (JSON) logging to stdout.

Why JSON: in Kubernetes, logs are collected from container stdout. One JSON object
per line can be searched and filtered by field (status, path, request_id) in any
log tool, where free-text lines need fragile regexes.

Why stdout and not a file: containers are disposable. A log file inside the
container disappears with the pod; stdout is captured by the kubelet, so you can
read it with `kubectl logs`.

Example output line:
    {"ts": "2026-10-15T10:02:11.481Z", "level": "INFO", "logger": "app.access",
     "msg": "request", "method": "GET", "path": "/api/v1/items/{item_id}",
     "status": 404, "duration_ms": 1.2, "request_id": "9f1c...", "pod": "items-api-7c9f"}
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Extra fields callers may attach with logger.info("msg", extra={...}).
# Only these are copied into the JSON so stray LogRecord attributes don't leak in.
_EXTRA_FIELDS = (
    "method",
    "path",
    "status",
    "duration_ms",
    "request_id",
    "pod",
    "client",
    "item_id",
    "version",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            # The stack trace goes to the logs (for engineers), never to the HTTP response (for clients).
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Route the app's logs and Uvicorn's own logs through one JSON handler.

    Example:
        >>> setup_logging("DEBUG")
        >>> logging.getLogger("app").debug("hello")
        {"ts": "...", "level": "DEBUG", "logger": "app", "msg": "hello"}
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Uvicorn installs its own text handlers before importing our app; replace them
    # so its startup/shutdown messages are JSON as well.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # Our middleware writes one access line per request (with latency and request id),
    # so Uvicorn's access log would only duplicate it.
    logging.getLogger("uvicorn.access").disabled = True
