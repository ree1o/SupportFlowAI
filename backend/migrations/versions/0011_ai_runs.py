"""Add durable, tenant-scoped asynchronous AI runs.

Revision ID: 0011_ai_runs
Revises: 0010_knowledge_base
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_ai_runs"
down_revision: str | None = "0010_knowledge_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("ticket_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("model_digest", sa.String(128), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "action IN ('reply', 'summary', 'agentic')", name="ck_ai_runs_valid_action"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'stale', 'cancelled')",
            name="ck_ai_runs_valid_status",
        ),
        sa.CheckConstraint(
            "ticket_version >= 1 AND attempts BETWEEN 0 AND 3", name="ck_ai_runs_valid_attempts"
        ),
        sa.CheckConstraint(
            "char_length(idempotency_key) BETWEEN 8 AND 100", name="ck_ai_runs_valid_key"
        ),
        sa.CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'", name="ck_ai_runs_valid_result"
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[a-z_]{1,48}$'", name="ck_ai_runs_valid_error"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ai_runs_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ai_runs_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requested_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_ai_runs_requester",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_runs"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ai_runs_org_id"),
        sa.UniqueConstraint(
            "organization_id",
            "requested_by_membership_id",
            "idempotency_key",
            name="uq_ai_runs_request_key",
        ),
    )
    op.create_index(
        "ix_ai_runs_org_status_created",
        "ai_runs",
        ["organization_id", "status", "created_at", "id"],
    )
    op.create_index("ix_ai_runs_recovery", "ai_runs", ["status", "updated_at"])

    bind = op.get_bind()
    bind.exec_driver_sql("ALTER TABLE ai_runs ENABLE ROW LEVEL SECURITY")
    bind.exec_driver_sql("ALTER TABLE ai_runs FORCE ROW LEVEL SECURITY")
    bind.exec_driver_sql(
        "CREATE POLICY supportflow_tenant_isolation ON ai_runs "
        "USING (organization_id = "
        "public.supportflow_current_uuid('supportflow.organization_id')) "
        "WITH CHECK (organization_id = "
        "public.supportflow_current_uuid('supportflow.organization_id'))"
    )
    bind.exec_driver_sql("GRANT SELECT, INSERT, UPDATE ON TABLE ai_runs TO supportflow_runtime")
    bind.exec_driver_sql("REVOKE DELETE ON TABLE ai_runs FROM supportflow_runtime")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP POLICY IF EXISTS supportflow_tenant_isolation ON ai_runs")
    bind.exec_driver_sql("ALTER TABLE ai_runs NO FORCE ROW LEVEL SECURITY")
    bind.exec_driver_sql("ALTER TABLE ai_runs DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_ai_runs_recovery", table_name="ai_runs")
    op.drop_index("ix_ai_runs_org_status_created", table_name="ai_runs")
    op.drop_table("ai_runs")
