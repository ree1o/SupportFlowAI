"""Store versioned local ticket embeddings for staff retrieval.

Revision ID: 0008_semantic_retrieval
Revises: 0007_incident_radar
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0008_semantic_retrieval"
down_revision: str | None = "0007_incident_radar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Installs in-place on the existing PostgreSQL 16 database and data volume.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.create_table(
        "ticket_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'stale')",
            name="ck_ticket_embeddings_valid_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ticket_embeddings_nonnegative_attempts"),
        sa.CheckConstraint(
            "source_version >= 1", name="ck_ticket_embeddings_positive_source_version"
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR embedding IS NOT NULL",
            name="ck_ticket_embeddings_ready_has_vector",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ticket_embeddings_ticket",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_ticket_embeddings_organization"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_embeddings"),
        sa.UniqueConstraint(
            "organization_id",
            "ticket_id",
            "model_version",
            "content_digest",
            name="uq_ticket_embeddings_content",
        ),
    )
    op.create_index(
        "uq_ticket_embeddings_current",
        "ticket_embeddings",
        ["organization_id", "ticket_id", "model_version"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_ticket_embeddings_dispatch",
        "ticket_embeddings",
        ["status", "available_at", "locked_until", "updated_at"],
    )
    op.create_index(
        "ix_ticket_embeddings_org_model_status",
        "ticket_embeddings",
        ["organization_id", "model_version", "is_current", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_embeddings_org_model_status", table_name="ticket_embeddings")
    op.drop_index("ix_ticket_embeddings_dispatch", table_name="ticket_embeddings")
    op.drop_index("uq_ticket_embeddings_current", table_name="ticket_embeddings")
    op.drop_table("ticket_embeddings")
