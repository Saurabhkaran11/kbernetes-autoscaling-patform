"""Every test for the Items API, in one file.

Run:
    make test                 # or: .venv/bin/pytest -v
    .venv/bin/pytest -k ready # just the readiness tests

How the tests work: `TestClient` calls the app in-process (no network, no Uvicorn).
Entering `with TestClient(app)` runs the lifespan startup (seeding, readiness on);
leaving it runs shutdown. Each test builds a fresh app, so tests can't leak items
into each other.

Prometheus metrics are process-global, so the metric tests compare values
before and after a request instead of checking absolute numbers.
"""

import json
import logging
import threading

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.config import Settings
from app.logging_config import JsonFormatter
from app.main import create_app
from app.store import SEED_ITEMS, ItemStore


def make_settings(**overrides) -> Settings:
    base = {
        "app_name": "items-api",
        "app_version": "test",
        "log_level": "INFO",
        "pod_name": "test-pod",
        "seed_items": True,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def app():
    return create_app(make_settings())


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


VALID_ITEM = {"name": "Pen", "description": "Blue ink", "price": 2.5, "quantity": 10}


# --------------------------------------------------------------------------- health / readiness


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_returns_200_after_startup(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_ready_returns_503_before_startup(app):
    # No `with` block, so the lifespan never runs. This is the state of a pod that is still starting.
    c = TestClient(app)
    r = c.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not ready"}


def test_ready_flag_turns_off_on_shutdown(app):
    with TestClient(app) as c:
        assert c.get("/ready").status_code == 200
    # Leaving the block runs the lifespan shutdown, as SIGTERM does in Kubernetes.
    assert app.state.ready is False


def test_health_stays_ok_when_not_ready(app):
    # Liveness must not depend on readiness, or Kubernetes would restart a pod that is only draining.
    c = TestClient(app)
    assert c.get("/ready").status_code == 503
    assert c.get("/health").status_code == 200


# --------------------------------------------------------------------------- items: list


def test_list_returns_seed_items(client):
    r = client.get("/api/v1/items")
    assert r.status_code == 200
    body = r.json()
    assert [i["name"] for i in body] == [s["name"] for s in SEED_ITEMS]
    assert {"id", "name", "description", "price", "quantity", "created_at"} <= set(body[0])


def test_list_is_empty_when_seeding_disabled():
    with TestClient(create_app(make_settings(seed_items=False))) as c:
        assert c.get("/api/v1/items").json() == []


def test_list_pagination(client):
    r = client.get("/api/v1/items", params={"limit": 1, "offset": 1})
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [2]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "abc"}])
def test_list_rejects_bad_pagination(client, params):
    assert client.get("/api/v1/items", params=params).status_code == 422


# --------------------------------------------------------------------------- items: create


def test_create_item_returns_201_and_location(client):
    r = client.post("/api/v1/items", json=VALID_ITEM)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == len(SEED_ITEMS) + 1
    assert body["name"] == "Pen"
    assert r.headers["location"] == f"/api/v1/items/{body['id']}"


def test_created_item_can_be_fetched(client):
    created = client.post("/api/v1/items", json=VALID_ITEM).json()
    r = client.get(f"/api/v1/items/{created['id']}")
    assert r.status_code == 200
    assert r.json() == created


def test_create_applies_defaults_and_strips_whitespace(client):
    r = client.post("/api/v1/items", json={"name": "  Mug  ", "price": 7})
    assert r.status_code == 201
    assert r.json()["name"] == "Mug"
    assert r.json()["quantity"] == 0
    assert r.json()["description"] is None


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({**VALID_ITEM, "name": ""}, "name"),
        ({**VALID_ITEM, "name": "   "}, "name"),
        ({**VALID_ITEM, "name": "x" * 101}, "name"),
        ({**VALID_ITEM, "price": 0}, "price"),
        ({**VALID_ITEM, "price": -5}, "price"),
        ({**VALID_ITEM, "price": "free"}, "price"),
        ({**VALID_ITEM, "quantity": -1}, "quantity"),
        ({**VALID_ITEM, "description": "d" * 501}, "description"),
        ({"name": "Pen"}, "price"),
        ({**VALID_ITEM, "id": 999}, "id"),
    ],
)
def test_create_rejects_invalid_input(client, payload, bad_field):
    r = client.post("/api/v1/items", json=payload)
    assert r.status_code == 422
    assert any(err["loc"][-1] == bad_field for err in r.json()["detail"])


def test_create_rejects_non_json_body(client):
    r = client.post("/api/v1/items", content="not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- items: get one


def test_get_missing_item_returns_404(client):
    r = client.get("/api/v1/items/9999")
    assert r.status_code == 404
    assert r.json() == {"detail": "Item 9999 not found"}


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc", "1.5"])
def test_get_rejects_invalid_id(client, bad_id):
    assert client.get(f"/api/v1/items/{bad_id}").status_code == 422


def test_unknown_route_returns_404(client):
    assert client.get("/api/v1/nope").status_code == 404


def test_wrong_method_returns_405(client):
    assert client.delete("/api/v1/items/1").status_code == 405


# --------------------------------------------------------------------------- stats


def test_stats_reports_pod_and_count(client):
    client.post("/api/v1/items", json=VALID_ITEM)
    body = client.get("/api/v1/stats").json()
    assert body["app"] == "items-api"
    assert body["version"] == "test"
    assert body["pod"] == "test-pod"
    assert body["item_count"] == len(SEED_ITEMS) + 1
    assert body["uptime_seconds"] >= 0


# --------------------------------------------------------------------------- metrics


def test_metrics_endpoint_exposes_prometheus_format(client):
    client.get("/health")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    for name in ("http_requests_total", "http_request_duration_seconds_bucket", "http_requests_in_progress"):
        assert name in r.text


def test_request_counter_uses_route_template_not_raw_id(client):
    labels = {"method": "GET", "path": "/api/v1/items/{item_id}", "status": "404"}
    before = metric("http_requests_total", **labels)
    client.get("/api/v1/items/424242")
    assert metric("http_requests_total", **labels) == before + 1
    # The raw id must never become a label value (that would be a cardinality explosion).
    assert "424242" not in client.get("/metrics").text


def test_latency_histogram_records_observation(client):
    before = metric("http_request_duration_seconds_count", method="GET", path="/health")
    client.get("/health")
    assert metric("http_request_duration_seconds_count", method="GET", path="/health") == before + 1


def test_client_errors_are_not_counted_as_server_errors(client):
    labels = {"method": "GET", "path": "/api/v1/items/{item_id}", "status": "404"}
    before = metric("http_errors_total", **labels)
    client.get("/api/v1/items/9999")
    assert metric("http_errors_total", **labels) == before


def test_items_stored_gauge_tracks_store(client):
    client.post("/api/v1/items", json=VALID_ITEM)
    assert metric("items_stored") == len(SEED_ITEMS) + 1


# --------------------------------------------------------------------------- errors / request id


@pytest.fixture
def crashing_client(app):
    # A route that raises, standing in for an unexpected bug.
    def boom():
        raise RuntimeError("secret internal detail")

    app.add_api_route("/boom", boom)
    with TestClient(app) as c:
        yield c


def test_unhandled_exception_returns_safe_500(crashing_client):
    r = crashing_client.get("/boom")
    assert r.status_code == 500
    assert r.json()["detail"] == "Internal server error"
    assert "secret internal detail" not in r.text  # no internals leaked to the client
    assert r.json()["request_id"] == r.headers["x-request-id"]


def test_unhandled_exception_increments_error_counter(crashing_client):
    labels = {"method": "GET", "path": "/boom", "status": "500"}
    before = metric("http_errors_total", **labels)
    crashing_client.get("/boom")
    assert metric("http_errors_total", **labels) == before + 1


def test_request_id_is_generated(client):
    rid = client.get("/health").headers["x-request-id"]
    assert len(rid) == 32


def test_valid_request_id_is_echoed(client):
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"


def test_unsafe_request_id_is_replaced(client):
    rid = client.get("/health", headers={"X-Request-ID": "bad id; drop table"}).headers["x-request-id"]
    assert rid != "bad id; drop table"
    assert len(rid) == 32


# --------------------------------------------------------------------------- logging


def test_json_formatter_emits_structured_fields():
    record = logging.LogRecord("app.access", logging.INFO, __file__, 1, "request", None, None)
    record.status = 201
    record.path = "/api/v1/items"
    out = json.loads(JsonFormatter().format(record))
    assert out["level"] == "INFO"
    assert out["msg"] == "request"
    assert out["status"] == 201
    assert out["path"] == "/api/v1/items"
    assert out["ts"].endswith("Z")


def test_access_log_written_per_request(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.access"):
        client.get("/health")
    rec = next(r for r in caplog.records if r.name == "app.access")
    assert rec.status == 200
    assert rec.path == "/health"
    assert rec.duration_ms >= 0


# --------------------------------------------------------------------------- store


def test_store_assigns_unique_ids_under_concurrency():
    store = ItemStore()

    def worker():
        for _ in range(200):
            store.create({"name": "x", "price": 1.0, "quantity": 0, "description": None})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [i["id"] for i in store.list(limit=10_000, offset=0)]
    assert len(ids) == 1600
    assert len(set(ids)) == 1600
