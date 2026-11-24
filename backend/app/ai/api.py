from uuid import UUID

from fastapi import APIRouter, Request

from app.ai.provider import OllamaProvider
from app.ai.schemas import AIStatusView
from app.identity.api import DB, Auth
from app.identity.service import organization_context

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
