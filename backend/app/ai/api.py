from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.ai import service
from app.ai.provider import OllamaProvider
from app.ai.schemas import AIRunCreate, AIRunView, AIStatusView
from app.errors import AppError
from app.identity.api import DB, Auth, WriteAuth
from app.identity.service import organization_context
from app.rate_limit import limited

router = APIRouter(prefix="/api/organizations/{organization_id}/ai", tags=["AI"])


@router.get("/status", response_model=AIStatusView)
def model_status(organization_id: UUID, auth: Auth, db: DB, request: Request) -> AIStatusView:
    # Membership is checked server-side before revealing whether local inference is enabled.
    organization_context(db, auth.user.id, organization_id)
    settings = request.app.state.settings
    status = OllamaProvider(settings).status()
    return AIStatusView(
        enabled=settings.ai_enabled,
        state=status.state,
        model=status.model,
        digest=status.digest,
    )


@router.post(
    "/tickets/{ticket_id}/runs",
    response_model=AIRunView,
    status_code=202,
    dependencies=[
        Depends(limited("ai-run-global", 120, 3600, identity_scope="global")),
        Depends(limited("ai-run-organization", 30, 3600, identity_scope="organization")),
        Depends(limited("ai-run", 6, 3600)),
    ],
)
def create_run(
    organization_id: UUID,
    ticket_id: UUID,
    data: AIRunCreate,
    request: Request,
    response: Response,
    auth: WriteAuth,
    db: DB,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=8, max_length=100),
    ] = None,
) -> AIRunView:
    if idempotency_key is None:
        raise AppError(422, "invalid_idempotency_key", "Idempotency-Key header is required")
    result = service.request_run(
        db,
        auth,
        organization_id,
        ticket_id,
        data,
        idempotency_key,
        request.app.state.settings,
    )
    response.headers["Location"] = f"/api/organizations/{organization_id}/ai/runs/{result.id}"
    response.headers["Retry-After"] = "2"
    return result


@router.get("/runs/{run_id}", response_model=AIRunView)
def get_run(organization_id: UUID, run_id: UUID, auth: Auth, db: DB) -> AIRunView:
    return service.get_run(db, auth, organization_id, run_id)


@router.post("/runs/{run_id}/cancel", response_model=AIRunView)
def cancel_run(organization_id: UUID, run_id: UUID, auth: WriteAuth, db: DB) -> AIRunView:
    return service.cancel_run(db, auth, organization_id, run_id)
