"""In-memory item store.

LIMITATION (by design for the MVP):
    * Data lives in this process's memory. A pod restart, crash or rolling update
      erases it.
    * Each replica has its OWN store. With 2 pods behind a Service, a POST that
      lands on pod A is invisible to a GET that lands on pod B.
    A shared database (Redis/Postgres) fixes both; see "Future improvements" in the README.

Why a lock: Uvicorn serves many requests concurrently. Without a lock, two POSTs
could read the same `_next_id` and create two items with the same id.

Example:
    >>> s = ItemStore()
    >>> s.create({"name": "pen", "price": 2.5, "quantity": 10, "description": None})["id"]
    1
    >>> s.get(1)["name"]
    'pen'
    >>> s.get(99) is None
    True
"""

import threading
from datetime import UTC, datetime


class ItemStore:
    def __init__(self) -> None:
        self._items: dict[int, dict] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def create(self, data: dict) -> dict:
        with self._lock:
            item = {"id": self._next_id, "created_at": datetime.now(UTC), **data}
            self._items[item["id"]] = item
            self._next_id += 1
            return item

    def get(self, item_id: int) -> dict | None:
        with self._lock:
            return self._items.get(item_id)

    def list(self, limit: int, offset: int) -> list[dict]:
        with self._lock:
            ordered = sorted(self._items.values(), key=lambda i: i["id"])
            return ordered[offset : offset + limit]

    def count(self) -> int:
        with self._lock:
            return len(self._items)


SEED_ITEMS = (
    {"name": "Keyboard", "description": "Mechanical, 87 keys", "price": 89.0, "quantity": 12},
    {"name": "Monitor", "description": "27-inch 1440p", "price": 299.99, "quantity": 4},
    {"name": "USB-C cable", "description": None, "price": 9.5, "quantity": 150},
)
