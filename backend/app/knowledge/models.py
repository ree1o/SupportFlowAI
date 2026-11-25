from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

KB_VECTOR_DIMENSIONS = 1024
KB_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B-Q"
KB_MODEL_REPOSITORY = "Qdrant/Qwen3-Embedding-0.6B-onnx"
KB_MODEL_REVISION = "af95f2c416ffe9379369ad64f9113e865db6112c"
KB_MODEL_VERSION = f"{KB_MODEL_ID}@fastembed-0.8.1:{KB_MODEL_REVISION}"


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_knowledge_articles_org_id"),
        UniqueConstraint("organization_id", "slug", name="uq_knowledge_articles_org_slug"),
        ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_knowledge_articles_creator",
        ),
        CheckConstraint("lifecycle IN ('draft', 'published', 'archived')", name="valid_lifecycle"),
        CheckConstraint("current_revision >= 1", name="positive_revision"),
        CheckConstraint(
            "(lifecycle = 'draft' AND published_revision IS NULL) OR "
            "(lifecycle = 'published' AND published_revision IS NOT NULL) OR "
            "lifecycle = 'archived'",
            name="published_revision_state",
        ),
        Index(
            "ix_knowledge_articles_org_lifecycle_updated",
            "organization_id",
            "lifecycle",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    slug: Mapped[str] = mapped_column(String(120))
    lifecycle: Mapped[str] = mapped_column(String(12), default="draft", server_default="draft")
    current_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    published_revision: Mapped[int | None] = mapped_column(Integer)
    created_by_membership_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeArticleVersion(Base):
    __tablename__ = "knowledge_article_versions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "article_id",
            "revision",
            name="uq_knowledge_article_versions_revision",
        ),
        ForeignKeyConstraint(
            ["organization_id", "article_id"],
            ["knowledge_articles.organization_id", "knowledge_articles.id"],
            name="fk_knowledge_article_versions_article",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_knowledge_article_versions_creator",
        ),
        CheckConstraint("revision >= 1", name="positive_revision"),
        CheckConstraint("char_length(btrim(title)) BETWEEN 1 AND 200", name="valid_title"),
        CheckConstraint("char_length(content) BETWEEN 1 AND 50000", name="valid_content"),
        Index("ix_knowledge_article_versions_article", "organization_id", "article_id", "revision"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    article_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_digest: Mapped[str] = mapped_column(String(64))
    source_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by_membership_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "article_id", "revision"],
            [
                "knowledge_article_versions.organization_id",
                "knowledge_article_versions.article_id",
                "knowledge_article_versions.revision",
            ],
            name="fk_knowledge_chunks_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("chunk_index >= 0", name="nonnegative_chunk"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'stale')", name="valid_status"
        ),
        CheckConstraint("attempts BETWEEN 0 AND 10", name="valid_attempts"),
        CheckConstraint("status <> 'ready' OR embedding IS NOT NULL", name="ready_has_vector"),
        UniqueConstraint(
            "organization_id",
            "article_id",
            "revision",
            "chunk_index",
            "model_version",
            name="uq_knowledge_chunks_version_model",
        ),
        Index("ix_knowledge_chunks_dispatch", "status", "available_at", "locked_until"),
        Index(
            "ix_knowledge_chunks_org_version", "organization_id", "article_id", "revision", "status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    article_id: Mapped[UUID]
    revision: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(240))
    content_digest: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(KB_VECTOR_DIMENSIONS))
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
