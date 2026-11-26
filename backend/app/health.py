import logging
from pathlib import Path
from typing import Literal

from alembic.migration import MigrationContext
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai.provider import OllamaProvider
from app.config import Settings
from app.database import get_session
from app.errors import error_response
from app.jobs import celery_app
from app.knowledge.embedding import MODEL_FILES as KB_MODEL_FILES
from app.knowledge.models import KB_MODEL_REPOSITORY, KB_MODEL_REVISION
from app.semantic.embedding import MODEL_FILES as TICKET_MODEL_FILES
from app.semantic.embedding import MODEL_REPOSITORY, MODEL_REPOSITORY_REVISION

router = APIRouter(prefix="/api/health", tags=["health"])
logger = logging.getLogger("supportflow.health")


class HealthStatus(BaseModel):
    status: str


class ComponentStatus(BaseModel):
    status: Literal["available", "unavailable", "disabled", "not_installed"]
    detail: str


class SystemDiagnostics(BaseModel):
    overall: Literal["operational", "degraded"]
    api: ComponentStatus
    database: ComponentStatus
    redis: ComponentStatus
    ticket_worker: ComponentStatus
    ai_worker: ComponentStatus
    ticket_embedding_model: ComponentStatus
    knowledge_embedding_model: ComponentStatus
    local_generation: ComponentStatus


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


def _model_installed(cache_dir: str, repository: str, revision: str, files: list[str]) -> bool:
    snapshot = Path(cache_dir) / f"models--{repository.replace('/', '--')}" / "snapshots" / revision
    return snapshot.is_dir() and all((snapshot / filename).is_file() for filename in files)


@router.get("/components", response_model=SystemDiagnostics)
def components(request: Request, session: Session = Depends(get_session)) -> SystemDiagnostics:
    """Public operational summary; optional local AI never changes core readiness."""
    settings: Settings = request.app.state.settings
    db_state = ComponentStatus(status="available", detail="Database and migrations are ready")
    try:
        session.execute(text("SELECT 1"))
        current = set(MigrationContext.configure(session.connection()).get_current_heads())
        if current != request.app.state.migration_heads:
            db_state = ComponentStatus(
                status="unavailable", detail="Database migration is required"
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "diagnostic_database_failed",
            extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
        )
        db_state = ComponentStatus(status="unavailable", detail="Database is unavailable")

    redis_state = ComponentStatus(status="available", detail="Queue and rate-limit store is ready")
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        client.close()
    except RedisError as exc:
        logger.warning(
            "diagnostic_redis_failed",
            extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
        )
        redis_state = ComponentStatus(status="unavailable", detail="Queue store is unavailable")

    ticket_worker = ComponentStatus(
        status="unavailable", detail="Ticket/indexing worker is offline"
    )
    ai_worker = ComponentStatus(status="unavailable", detail="Local generation worker is offline")
    try:
        queues = celery_app.control.inspect(timeout=0.75).active_queues() or {}
        for worker_queues in queues.values():
            names = {
                item.get("name")
                for item in worker_queues
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            if "default" in names:
                ticket_worker = ComponentStatus(
                    status="available", detail="Ticket, notification, and indexing worker is online"
                )
            if "ai" in names:
                ai_worker = ComponentStatus(
                    status="available", detail="Local generation worker is online"
                )
    except Exception as exc:
        logger.warning(
            "diagnostic_worker_probe_failed",
            extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
        )

    ticket_model = ComponentStatus(
        status="available"
        if _model_installed(
            settings.semantic_model_cache_dir,
            MODEL_REPOSITORY,
            MODEL_REPOSITORY_REVISION,
            TICKET_MODEL_FILES,
        )
        else "not_installed",
        detail="Ticket retrieval model files are installed locally"
        if _model_installed(
            settings.semantic_model_cache_dir,
            MODEL_REPOSITORY,
            MODEL_REPOSITORY_REVISION,
            TICKET_MODEL_FILES,
        )
        else "Ticket retrieval model files are not installed",
    )
    knowledge_model = ComponentStatus(
        status="available"
        if _model_installed(
            settings.semantic_model_cache_dir,
            KB_MODEL_REPOSITORY,
            KB_MODEL_REVISION,
            KB_MODEL_FILES,
        )
        else "not_installed",
        detail="Knowledge indexing model files are installed locally"
        if _model_installed(
            settings.semantic_model_cache_dir,
            KB_MODEL_REPOSITORY,
            KB_MODEL_REVISION,
            KB_MODEL_FILES,
        )
        else "Knowledge indexing model files are not installed",
    )
    if not settings.ai_enabled:
        generation = ComponentStatus(
            status="disabled", detail="Optional local generation is disabled"
        )
    else:
        model_status = OllamaProvider(settings).status()
        generation = ComponentStatus(
            status="available" if model_status.state == "available" else "unavailable",
            detail="Local model is available"
            if model_status.state == "available"
            else "Local model service or configured model is unavailable",
        )

    return SystemDiagnostics(
        overall=(
            "operational"
            if db_state.status == "available"
            and redis_state.status == "available"
            and ticket_worker.status == "available"
            else "degraded"
        ),
        api=ComponentStatus(status="available", detail="API is responding"),
        database=db_state,
        redis=redis_state,
        ticket_worker=ticket_worker,
        ai_worker=ai_worker,
        ticket_embedding_model=ticket_model,
        knowledge_embedding_model=knowledge_model,
        local_generation=generation,
    )
