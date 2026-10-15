"""Item CRUD endpoints (/api/v1/items).

Status codes used:
    200 OK          — successful read
    201 Created     — new item made; a Location header points to it
    404 Not Found   — the item id does not exist
    422 Unprocessable Entity — the body or the path/query parameters failed validation

Example:
    $ curl -s -X POST localhost:8000/api/v1/items \
        -H 'content-type: application/json' -d '{"name":"pen","price":2.5}'
    {"name":"pen","description":null,"price":2.5,"quantity":0,"id":4,"created_at":"..."}
"""

import logging

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from app.metrics import ITEMS_STORED
from app.models import Item, ItemCreate

router = APIRouter(prefix="/api/v1/items", tags=["items"])
log = logging.getLogger("app.items")


@router.get("", response_model=list[Item])
async def list_items(
    request: Request,
    # Pagination keeps response size bounded no matter how many items exist.
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    return request.app.state.store.list(limit=limit, offset=offset)


@router.post("", response_model=Item, status_code=status.HTTP_201_CREATED)
async def create_item(payload: ItemCreate, request: Request, response: Response) -> dict:
    store = request.app.state.store
    item = store.create(payload.model_dump())
    ITEMS_STORED.set(store.count())
    response.headers["Location"] = f"/api/v1/items/{item['id']}"
    log.info("item created", extra={"item_id": item["id"], "request_id": request.state.request_id})
    return item


@router.get("/{item_id}", response_model=Item, responses={404: {"description": "Item not found"}})
async def get_item(request: Request, item_id: int = Path(gt=0)) -> dict:
    item = request.app.state.store.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    return item
