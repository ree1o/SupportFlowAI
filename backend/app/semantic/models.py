from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

VECTOR_DIMENSIONS = 384
MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REPOSITORY = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
MODEL_REPOSITORY_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
MODEL_VERSION = f"{MODEL_ID}@fastembed-0.7.3:{MODEL_REPOSITORY_REVISION}"


class TicketEmbedding(Base):
    __tablename__ = "ticket_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ticket_embeddings_ticket",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "organization_id",
            "ticket_id",
            "model_version",
            "content_digest",
            name="uq_ticket_embeddings_content",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'stale')",
            name="valid_status",
        ),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
        CheckConstraint("source_version >= 1", name="positive_source_version"),
        CheckConstraint("status <> 'ready' OR embedding IS NOT NULL", name="ready_has_vector"),
        Index(
            "uq_ticket_embeddings_current",
            "organization_id",
            "ticket_id",
            "model_version",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index(
            "ix_ticket_embeddings_dispatch",
            "status",
            "available_at",
            "locked_until",
            "updated_at",
        ),
        Index(
            "ix_ticket_embeddings_org_model_status",
            "organization_id",
            "model_version",
            "is_current",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    ticket_id: Mapped[UUID]
    model_version: Mapped[str] = mapped_column(String(200))
    content_digest: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(VECTOR_DIMENSIONS))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
