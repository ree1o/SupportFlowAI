"""Add versioned, tenant-scoped Knowledge Base articles and vectors.

Revision ID: 0010_knowledge_base
Revises: 0009_release_hardening
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0010_knowledge_base"
down_revision: str | None = "0009_release_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("lease_token", sa.String(64), nullable=True))
    op.create_table(
        "knowledge_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("lifecycle", sa.String(12), server_default="draft", nullable=False),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_revision", sa.Integer(), nullable=True),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "lifecycle IN ('draft', 'published', 'archived')",
            name="ck_knowledge_articles_valid_lifecycle",
        ),
        sa.CheckConstraint("current_revision >= 1", name="ck_knowledge_articles_positive_revision"),
        sa.CheckConstraint(
            "(lifecycle = 'draft' AND published_revision IS NULL) OR "
            "(lifecycle = 'published' AND published_revision IS NOT NULL) OR "
            "lifecycle = 'archived'",
            name="ck_knowledge_articles_published_revision_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_knowledge_articles_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_knowledge_articles_creator",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_articles"),
        sa.UniqueConstraint("organization_id", "id", name="uq_knowledge_articles_org_id"),
        sa.UniqueConstraint("organization_id", "slug", name="uq_knowledge_articles_org_slug"),
    )
    op.create_index(
        "ix_knowledge_articles_org_lifecycle_updated",
        "knowledge_articles",
        ["organization_id", "lifecycle", "updated_at"],
    )
    op.create_table(
        "knowledge_article_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column(
            "source_metadata",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 1", name="ck_knowledge_article_versions_positive_revision"),
        sa.CheckConstraint(
            "char_length(btrim(title)) BETWEEN 1 AND 200",
            name="ck_knowledge_article_versions_valid_title",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 50000",
            name="ck_knowledge_article_versions_valid_content",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_knowledge_article_versions_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "article_id"],
            ["knowledge_articles.organization_id", "knowledge_articles.id"],
            name="fk_knowledge_article_versions_article",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_knowledge_article_versions_creator",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_article_versions"),
        sa.UniqueConstraint(
            "organization_id",
            "article_id",
            "revision",
            name="uq_knowledge_article_versions_revision",
        ),
    )
    op.create_index(
        "ix_knowledge_article_versions_article",
        "knowledge_article_versions",
        ["organization_id", "article_id", "revision"],
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(240), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_knowledge_chunks_nonnegative_chunk"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'stale')",
            name="ck_knowledge_chunks_valid_status",
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 10", name="ck_knowledge_chunks_valid_attempts"),
        sa.CheckConstraint(
            "status <> 'ready' OR embedding IS NOT NULL",
            name="ck_knowledge_chunks_ready_has_vector",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_knowledge_chunks_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "article_id", "revision"],
            [
                "knowledge_article_versions.organization_id",
                "knowledge_article_versions.article_id",
                "knowledge_article_versions.revision",
            ],
            name="fk_knowledge_chunks_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunks"),
        sa.UniqueConstraint(
            "organization_id",
            "article_id",
            "revision",
            "chunk_index",
            "model_version",
            name="uq_knowledge_chunks_version_model",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_dispatch",
        "knowledge_chunks",
        ["status", "available_at", "locked_until"],
    )
    op.create_index(
        "ix_knowledge_chunks_org_version",
        "knowledge_chunks",
        ["organization_id", "article_id", "revision", "status"],
    )

    bind = op.get_bind()
    for table in ("knowledge_articles", "knowledge_article_versions", "knowledge_chunks"):
        bind.exec_driver_sql(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        bind.exec_driver_sql(
            f'CREATE POLICY supportflow_tenant_isolation ON "{table}" '
            "USING (organization_id = "
            "public.supportflow_current_uuid('supportflow.organization_id')) "
            "WITH CHECK (organization_id = "
            "public.supportflow_current_uuid('supportflow.organization_id'))"
        )
        bind.exec_driver_sql(
            f'GRANT SELECT, INSERT, UPDATE ON TABLE "{table}" TO supportflow_runtime'
        )
        bind.exec_driver_sql(f'REVOKE DELETE ON TABLE "{table}" FROM supportflow_runtime')


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("knowledge_chunks", "knowledge_article_versions", "knowledge_articles"):
        bind.exec_driver_sql(f'DROP POLICY IF EXISTS supportflow_tenant_isolation ON "{table}"')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        bind.exec_driver_sql(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_article_versions_article", table_name="knowledge_article_versions")
    op.drop_table("knowledge_article_versions")
    op.drop_index("ix_knowledge_articles_org_lifecycle_updated", table_name="knowledge_articles")
    op.drop_table("knowledge_articles")
    op.drop_column("background_jobs", "lease_token")
