from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.errors import AppError
from app.identity.api import DB, Auth, WriteAuth
from app.integrations import service
from app.integrations.schemas import (
    IssuedCredential,
    ReceiptPage,
    SourceCreate,
    SourceUpdate,
    SourceView,
)
from app.rate_limit import limited

router = APIRouter(
    prefix="/api/organizations/{organization_id}/integrations", tags=["integrations"]
)
inbound_router = APIRouter(prefix="/api/integrations", tags=["inbound webhooks"])
Page = Annotated[int, Query(ge=1, le=10000)]
Size = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=list[SourceView])
def list_sources(organization_id: UUID, auth: Auth, db: DB) -> list[SourceView]:
    return service.list_sources(db, auth, organization_id)


@router.post("", response_model=IssuedCredential, status_code=201)
def create_source(
    organization_id: UUID, data: SourceCreate, auth: WriteAuth, db: DB, request: Request
) -> IssuedCredential:
    source, credential = service.create_source(
        db, auth, organization_id, data, request.app.state.settings
    )
    return IssuedCredential(source=source, credential=credential)


@router.patch("/{source_id}", response_model=SourceView)
def update_source(
    organization_id: UUID, source_id: UUID, data: SourceUpdate, auth: WriteAuth, db: DB
) -> SourceView:
    return service.update_source(db, auth, organization_id, source_id, data)


@router.post("/{source_id}/rotate-credential", response_model=IssuedCredential)
def rotate(
    organization_id: UUID, source_id: UUID, auth: WriteAuth, db: DB, request: Request
) -> IssuedCredential:
    source, credential = service.rotate_credential(
        db, auth, organization_id, source_id, request.app.state.settings
    )
    return IssuedCredential(source=source, credential=credential)


@router.delete("/{source_id}", status_code=204)
def revoke(organization_id: UUID, source_id: UUID, auth: WriteAuth, db: DB) -> None:
    service.revoke_source(db, auth, organization_id, source_id)


@router.get("/receipts", response_model=ReceiptPage)
def list_receipts(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    source_id: UUID | None = None,
    status: Literal["succeeded", "rejected"] | None = None,
    page: Page = 1,
    page_size: Size = 20,
) -> ReceiptPage:
    return service.receipts(db, auth, organization_id, source_id, status, page, page_size)


@inbound_router.post("/{source_id}/events", dependencies=[Depends(limited("webhook", 120, 60))])
async def receive_event(
    source_id: UUID,
    request: Request,
    response: Response,
    event_id: Annotated[str, Header(alias="X-SupportFlow-Event-Id")],
    timestamp: Annotated[str, Header(alias="X-SupportFlow-Timestamp")],
    signature: Annotated[str, Header(alias="X-SupportFlow-Signature")],
) -> dict:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > service.MAX_BODY_BYTES:
                raise AppError(413, "payload_too_large", "Webhook payload exceeds the 64 KiB limit")
        except ValueError:
            raise AppError(400, "invalid_content_length", "Content-Length is invalid") from None
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > service.MAX_BODY_BYTES:
            raise AppError(413, "payload_too_large", "Webhook payload exceeds the 64 KiB limit")
        chunks.append(chunk)
    raw_body = b"".join(chunks)

    def process():
        with request.app.state.database.session() as session:
            return service.ingest(
                session,
                source_id,
                event_id,
                timestamp,
                signature,
                raw_body,
                request.app.state.settings,
            )

    result, duplicate = await run_in_threadpool(process)
    response.status_code = int(result.get("status_code", 200))
    response.headers["X-SupportFlow-Duplicate"] = "true" if duplicate else "false"
    return result
