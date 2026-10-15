"""Request/response schemas. Pydantic validates every incoming body against these.

If validation fails, FastAPI answers 422 Unprocessable Entity with the exact field
errors, before our handler code runs at all.

Example:
    POST /api/v1/items {"name": "", "price": -1}
    -> 422 {"detail": [{"loc": ["body","name"], "msg": "String should have at least 1 character", ...},
                       {"loc": ["body","price"], "msg": "Input should be greater than 0", ...}]}
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemCreate(BaseModel):
    # extra="forbid": reject unknown fields ({"id": 5, ...}) instead of silently ignoring them.
    # str_strip_whitespace: "  pen  " is stored as "pen", and "   " then fails min_length.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100, examples=["Keyboard"])
    description: str | None = Field(default=None, max_length=500)
    price: float = Field(gt=0, le=1_000_000, examples=[89.0])
    quantity: int = Field(default=0, ge=0, le=1_000_000)


class Item(ItemCreate):
    id: int
    created_at: datetime


class StatusResponse(BaseModel):
    status: str


class Faults(BaseModel):
    error_rate: float
    latency_ms: int


class Stats(BaseModel):
    app: str
    version: str
    pod: str
    item_count: int
    uptime_seconds: float
    started_at: datetime
    faults: Faults
