from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.identity.api import DB, Auth, WriteAuth
from app.semantic import service
from app.semantic.schemas import IndexingBatch, RelatedTickets

router = APIRouter(prefix="/api/organizations/{organization_id}", tags=["semantic retrieval"])
Limit = Annotated[int, Query(ge=1, le=10)]


@router.get("/tickets/{ticket_id}/related", response_model=RelatedTickets)
def related_tickets(
    organization_id: UUID, ticket_id: UUID, auth: Auth, db: DB, request: Request, limit: Limit = 5
) -> RelatedTickets:
    return service.related(db, auth, organization_id, ticket_id, limit, request.app.state.settings)


@router.post("/semantic-index/backfill", response_model=IndexingBatch, status_code=202)
def backfill(organization_id: UUID, auth: WriteAuth, db: DB, request: Request) -> IndexingBatch:
    return service.backfill(db, auth, organization_id, request.app.state.settings)
