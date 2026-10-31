from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RelatedTicket(BaseModel):
    id: UUID
    title: str
    status: str
    priority: str
    category_name: str | None
    preview: str
    similarity: float = Field(ge=0, le=1)


class RelatedTickets(BaseModel):
    model_version: str
    index_status: Literal["pending", "processing", "ready", "failed", "stale", "missing"]
    indexed_at: datetime | None
    items: list[RelatedTicket]


class IndexingBatch(BaseModel):
    queued: int
    remaining: bool
