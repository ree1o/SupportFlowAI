"""SLA policy snapshots, deadline state, and reporting indexes.

Revision ID: 0005_sla
Revises: 0004_m4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_sla"
down_revision: str | None = "0004_m4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_sla_policies_positive_version"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_sla_policies_organization_id_organizations",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_sla_policies_org"),
        sa.UniqueConstraint("organization_id", "id", name="uq_sla_policies_org_id"),
    )
    op.create_table(
        "sla_policy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_sla_policy_versions_positive_version"),
        sa.CheckConstraint(
            "jsonb_typeof(targets) = 'object'", name="ck_sla_policy_versions_valid_targets"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_sla_policy_versions_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "changed_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_sla_versions_actor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["sla_policies.organization_id", "sla_policies.id"],
            name="fk_sla_versions_policy",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "policy_id", "version", name="uq_sla_policy_version"
        ),
    )
    op.create_table(
        "ticket_slas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_priority", sa.String(10), nullable=False),
        sa.Column("response_target_seconds", sa.Integer(), nullable=False),
        sa.Column("resolution_target_seconds", sa.Integer(), nullable=False),
        sa.Column("response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_breached_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_breached_at", sa.DateTime(timezone=True)),
        sa.Column("response_escalated_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_escalated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "response_target_seconds > 0 AND resolution_target_seconds > 0",
            name="ck_ticket_slas_positive_targets",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ticket_slas_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["sla_policies.organization_id", "sla_policies.id"],
            name="fk_ticket_slas_policy",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_id", "policy_version"],
            [
                "sla_policy_versions.organization_id",
                "sla_policy_versions.policy_id",
                "sla_policy_versions.version",
            ],
            name="fk_ticket_slas_version",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ticket_slas_ticket",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "ticket_id", name="uq_ticket_slas_ticket"),
    )
    op.create_index(
        "ix_ticket_slas_response_due",
        "ticket_slas",
        ["organization_id", "response_breached_at", "response_due_at"],
    )
    op.create_index(
        "ix_ticket_slas_resolution_due",
        "ticket_slas",
        ["organization_id", "resolution_breached_at", "resolution_due_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_slas_resolution_due", table_name="ticket_slas")
    op.drop_index("ix_ticket_slas_response_due", table_name="ticket_slas")
    op.drop_table("ticket_slas")
    op.drop_table("sla_policy_versions")
    op.drop_table("sla_policies")
