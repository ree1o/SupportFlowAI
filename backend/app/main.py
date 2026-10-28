import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.config import Settings
from app.database import Database
from app.errors import AppError, error_response
from app.health import router
from app.identity.api import router as identity_router
from app.identity.security import clear_cookies
from app.logging import configure_logging
from app.tickets.api import router as tickets_router

logger = logging.getLogger("supportflow.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.database = Database(settings)
        logger.info("application_started")
        try:
            yield
        finally:
            app.state.database.dispose()
            logger.info("application_stopped")

    app = FastAPI(
        title="SupportFlow AI",
        version="0.1.0-dev",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    migration_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    app.state.migration_heads = set(ScriptDirectory.from_config(migration_config).get_heads())

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request.state.request_id = str(uuid4())
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "request_failed",
                extra={"request_id": request.state.request_id, "error_type": type(exc).__name__},
            )
            response = error_response(
                request, 500, "internal_error", "An unexpected error occurred"
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        route = request.scope.get("route")
        logger.info(
            "request_completed",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            404: ("not_found", "Resource not found"),
            405: ("method_not_allowed", "Method not allowed"),
        }
        code, message = codes.get(exc.status_code, ("request_rejected", "Request rejected"))
        response = error_response(request, exc.status_code, code, message)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, 422, "validation_error", "Request validation failed")

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        response = error_response(request, exc.status, exc.code, exc.message, exc.details)
        if exc.status == 401 and exc.code == "unauthenticated":
            clear_cookies(response, settings)
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error(
            "database_request_failed",
            extra={
                "request_id": request.state.request_id,
                "error_type": type(exc).__name__,
            },
        )
        return error_response(request, 503, "database_unavailable", "Database is unavailable")

    app.include_router(router)
    app.include_router(identity_router)
    app.include_router(tickets_router)
    return app
