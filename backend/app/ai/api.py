from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response

from app.ai import service
from app.ai.provider import OllamaProvider
from app.ai.schemas import AIRunCreate, AIRunView, AIStatusView
from app.errors import AppError
from app.identity.api import DB, Auth, WriteAuth
from app.identity.service import organization_context

router = APIRouter(prefix="/api/organizations/{organization_id}/ai", tags=["AI"])


@router.get("/status", response_model=AIStatusView)
def model_status(organization_id: UUID, auth: Auth, db: DB, request: Request) -> AIStatusView:
    # Membership is checked server-side before revealing whether local inference is enabled.
    organization_context(db, auth.user.id, organization_id)
    settings = request.app.state.settings
    status = OllamaProvider(settings).status()
    quotas = [
        request.app.state.rate_limiter.quota(
            "ai-run-global", "global", settings.ai_global_hourly_limit
        ),
        request.app.state.rate_limiter.quota(
            "ai-run-organization", str(organization_id), settings.ai_organization_hourly_limit
        ),
        request.app.state.rate_limiter.quota(
            "ai-run-user-organization",
            f"{auth.user.id}:{organization_id}",
            settings.ai_user_organization_hourly_limit,
        ),
    ]
    available_quotas = [item for item in quotas if item is not None]
    remaining = (
        min(item[0] for item in available_quotas) if len(available_quotas) == len(quotas) else None
    )
    resets_in = (
        max(item[1] for item in available_quotas if item[0] == remaining)
        if remaining is not None
        else None
    )
    return AIStatusView(
        enabled=settings.ai_enabled,
        state=status.state,
        model=status.model,
        digest=status.digest,
        request_limit=min(
            settings.ai_global_hourly_limit,
            settings.ai_organization_hourly_limit,
            settings.ai_user_organization_hourly_limit,
        ),
        requests_remaining=remaining,
        limit_resets_in_seconds=resets_in,
    )


@router.post(
    "/tickets/{ticket_id}/runs",
    response_model=AIRunView,
    status_code=202,
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
        request.app.state.rate_limiter,
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
