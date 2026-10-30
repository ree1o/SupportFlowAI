from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.identity.api import DB, Auth, WriteAuth
from app.sla import service
from app.sla.schemas import OperationsReport, SlaPolicyView, SlaPolicyWrite

router = APIRouter(prefix="/api/organizations/{organization_id}", tags=["sla and operations"])


@router.get("/sla/policy", response_model=SlaPolicyView | None)
def get_policy(organization_id: UUID, auth: Auth, db: DB) -> SlaPolicyView | None:
    return service.get_policy(db, auth, organization_id)


@router.put("/sla/policy", response_model=SlaPolicyView)
def save_policy(
    organization_id: UUID, data: SlaPolicyWrite, auth: WriteAuth, db: DB
) -> SlaPolicyView:
    return service.save_policy(db, auth, organization_id, data)


@router.get("/reports/operations", response_model=OperationsReport)
def operations_report(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    period_start: Annotated[datetime, Query()],
    period_end: Annotated[datetime, Query()],
) -> OperationsReport:
    start = (
        period_start.astimezone(UTC) if period_start.tzinfo else period_start.replace(tzinfo=UTC)
    )
    end = period_end.astimezone(UTC) if period_end.tzinfo else period_end.replace(tzinfo=UTC)
    if end <= start or end - start > timedelta(days=366):
        from app.errors import AppError

        raise AppError(422, "invalid_period", "Report period must be positive and at most one year")
    return service.report(db, auth, organization_id, start, end)
