import logging

from alembic.migration import MigrationContext
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.errors import error_response

router = APIRouter(prefix="/api/health", tags=["health"])
logger = logging.getLogger("supportflow.health")


class HealthStatus(BaseModel):
    status: str


@router.get("/live", response_model=HealthStatus)
def live() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/ready", response_model=HealthStatus, responses={503: {"description": "Not ready"}})
def ready(request: Request, session: Session = Depends(get_session)):
    try:
        session.execute(text("SELECT 1"))
        current = set(MigrationContext.configure(session.connection()).get_current_heads())
        if current != request.app.state.migration_heads:
            return error_response(request, 503, "schema_not_ready", "Database migration required")
    except SQLAlchemyError as exc:
        logger.warning(
            "database_unavailable",
            extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
        )
        return error_response(request, 503, "database_unavailable", "Database is unavailable")
    return HealthStatus(status="ready")
