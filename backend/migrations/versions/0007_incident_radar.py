"""Manual incidents and deterministic incident radar.

Revision ID: 0007_incident_radar
Revises: 0006_integrations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_incident_radar"
down_revision: str | None = "0006_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(20), server_default="investigating", nullable=False),
        sa.Column("severity", sa.String(4), server_default="sev3", nullable=False),
        sa.Column("owner_membership_id", sa.Uuid()),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('investigating', 'monitoring', 'resolved')",
            name="ck_incidents_valid_status",
        ),
        sa.CheckConstraint(
            "severity IN ('sev1', 'sev2', 'sev3', 'sev4')", name="ck_incidents_valid_severity"
        ),
        sa.CheckConstraint("version >= 1", name="ck_incidents_positive_version"),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name="ck_incidents_nonempty_title"),
        sa.CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_incidents_resolution_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_incidents_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incidents_owner",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incidents_creator",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incidents"),
        sa.UniqueConstraint("organization_id", "id", name="uq_incidents_org_id"),
    )
    op.create_index(
        "ix_incidents_org_status_updated",
        "incidents",
        ["organization_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "radar_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("triggered_by_membership_id", sa.Uuid()),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("detector_version", sa.String(30), server_default="lexical-v1", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("proposal_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_type", sa.String(120)),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_radar_runs_valid_status",
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_radar_runs_valid_attempts"),
        sa.CheckConstraint("proposal_count >= 0", name="ck_radar_runs_valid_proposal_count"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_radar_runs_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "triggered_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_radar_runs_actor",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_radar_runs"),
        sa.UniqueConstraint("organization_id", "id", name="uq_radar_runs_org_id"),
    )
    op.create_index(
        "ix_radar_runs_dispatch", "radar_runs", ["status", "available_at", "locked_until"]
    )
    op.create_index(
        "ix_radar_runs_org_created", "radar_runs", ["organization_id", "created_at", "id"]
    )

    op.create_table(
        "radar_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("detector_version", sa.String(30), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
        sa.Column("reviewer_membership_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("incident_id", sa.Uuid()),
        sa.Column("rejection_reason", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name="ck_radar_proposals_valid_status"
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_radar_proposals_valid_score"),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'object'", name="ck_radar_proposals_valid_evidence"
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_at IS NULL AND reviewer_membership_id IS NULL) OR "
            "(status <> 'pending' AND reviewed_at IS NOT NULL AND "
            "reviewer_membership_id IS NOT NULL)",
            name="ck_radar_proposals_review_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_radar_proposals_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["radar_runs.organization_id", "radar_runs.id"],
            name="fk_radar_proposals_run",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_radar_proposals_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_radar_proposals_incident",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_radar_proposals"),
        sa.UniqueConstraint("organization_id", "id", name="uq_radar_proposals_org_id"),
        sa.UniqueConstraint(
            "organization_id", "group_key", "input_fingerprint", name="uq_radar_proposal_identity"
        ),
    )
    op.create_index(
        "ix_radar_proposals_org_status_created",
        "radar_proposals",
        ["organization_id", "status", "created_at", "id"],
    )

    op.create_table(
        "incident_tickets",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("linked_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_incident_tickets_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_incident_tickets_incident",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_incident_tickets_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "linked_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incident_tickets_actor",
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "incident_id", "ticket_id", name="pk_incident_tickets"
        ),
    )
    op.create_index(
        "ix_incident_tickets_org_ticket", "incident_tickets", ["organization_id", "ticket_id"]
    )

    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(100), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "changes", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("incident_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "incident_version >= 1 AND sequence >= 0", name="ck_incident_events_valid_order"
        ),
        sa.CheckConstraint(
            "action IN ('incident.created', 'incident.updated', 'incident.ticket_linked', "
            "'incident.ticket_unlinked', 'incident.note_added', 'incident.proposal_confirmed')",
            name="ck_incident_events_valid_action",
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 5000",
            name="ck_incident_events_valid_note",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_incident_events_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_incident_events_incident",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "actor_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incident_events_actor",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incident_events"),
        sa.UniqueConstraint(
            "organization_id",
            "incident_id",
            "incident_version",
            "sequence",
            name="uq_incident_event_order",
        ),
    )
    op.create_index(
        "ix_incident_events_timeline",
        "incident_events",
        ["organization_id", "incident_id", "created_at", "id"],
    )
    op.execute("""
        CREATE FUNCTION reject_incident_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'incident history is append-only' USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute(
        "CREATE TRIGGER incident_events_append_only BEFORE UPDATE OR DELETE ON incident_events "
        "FOR EACH ROW EXECUTE FUNCTION reject_incident_event_mutation()"
    )

    op.create_table(
        "radar_proposal_tickets",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_radar_proposal_tickets_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["radar_proposals.organization_id", "radar_proposals.id"],
            name="fk_radar_proposal_tickets_proposal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_radar_proposal_tickets_ticket",
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "proposal_id", "ticket_id", name="pk_radar_proposal_tickets"
        ),
    )
    op.create_index(
        "ix_radar_proposal_tickets_ticket",
        "radar_proposal_tickets",
        ["organization_id", "ticket_id"],
    )

    op.create_table(
        "radar_suppressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('rejected', 'confirmed')", name="ck_radar_suppressions_valid_status"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_radar_suppressions_organization"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["radar_proposals.organization_id", "radar_proposals.id"],
            name="fk_radar_suppressions_proposal",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_radar_suppressions_incident",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_radar_suppressions"),
        sa.UniqueConstraint(
            "organization_id",
            "group_key",
            "input_fingerprint",
            name="uq_radar_suppression_identity",
        ),
    )
    op.create_index(
        "ix_radar_suppressions_org_group", "radar_suppressions", ["organization_id", "group_key"]
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS incident_events_append_only ON incident_events")
    op.execute("DROP FUNCTION reject_incident_event_mutation()")
    op.drop_index("ix_radar_suppressions_org_group", table_name="radar_suppressions")
    op.drop_table("radar_suppressions")
    op.drop_index("ix_radar_proposal_tickets_ticket", table_name="radar_proposal_tickets")
    op.drop_table("radar_proposal_tickets")
    op.drop_index("ix_incident_events_timeline", table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_index("ix_incident_tickets_org_ticket", table_name="incident_tickets")
    op.drop_table("incident_tickets")
    op.drop_index("ix_radar_proposals_org_status_created", table_name="radar_proposals")
    op.drop_table("radar_proposals")
    op.drop_index("ix_radar_runs_org_created", table_name="radar_runs")
    op.drop_index("ix_radar_runs_dispatch", table_name="radar_runs")
    op.drop_table("radar_runs")
    op.drop_index("ix_incidents_org_status_updated", table_name="incidents")
    op.drop_table("incidents")
