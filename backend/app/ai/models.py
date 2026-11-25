"""Tenant-scoped metadata for asynchronous local AI assistance."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_ai_runs_org_id"),
        UniqueConstraint(
            "organization_id",
            "requested_by_membership_id",
            "idempotency_key",
            name="uq_ai_runs_request_key",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ai_runs_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "requested_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_ai_runs_requester",
        ),
        CheckConstraint("action IN ('reply', 'summary', 'agentic')", name="valid_action"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'stale', 'cancelled')",
            name="valid_status",
        ),
        CheckConstraint("ticket_version >= 1 AND attempts BETWEEN 0 AND 3", name="valid_attempts"),
        CheckConstraint("char_length(idempotency_key) BETWEEN 8 AND 100", name="valid_key"),
        CheckConstraint("result IS NULL OR jsonb_typeof(result) = 'object'", name="valid_result"),
        CheckConstraint("error_code IS NULL OR error_code ~ '^[a-z_]{1,48}$'", name="valid_error"),
        Index("ix_ai_runs_org_status_created", "organization_id", "status", "created_at", "id"),
        Index("ix_ai_runs_recovery", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    ticket_id: Mapped[UUID]
    requested_by_membership_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(12))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    ticket_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    model_id: Mapped[str] = mapped_column(String(100))
    model_digest: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    error_code: Mapped[str | None] = mapped_column(String(48))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
