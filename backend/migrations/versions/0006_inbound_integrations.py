"""Organization-scoped signed inbound webhooks and durable receipts.

Revision ID: 0006_integrations
Revises: 0005_sla
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_integrations"
down_revision: str | None = "0005_sla"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("credential_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name="ck_integration_sources_nonempty_name"
        ),
        sa.CheckConstraint(
            "credential_version >= 1", name="ck_integration_sources_positive_credential_version"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_integration_sources_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_integration_sources_contact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_integration_sources"),
        sa.UniqueConstraint("organization_id", "id", name="uq_integration_sources_org_id"),
    )
    op.create_index(
        "ix_integration_sources_org", "integration_sources", ["organization_id", "created_at"]
    )

    op.add_column("tickets", sa.Column("created_by_integration_source_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_tickets_creator_integration",
        "tickets",
        "integration_sources",
        ["organization_id", "created_by_integration_source_id"],
        ["organization_id", "id"],
    )
    op.drop_constraint("ck_tickets_single_creator", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_single_creator",
        "tickets",
        "num_nonnulls(created_by_membership_id, created_by_customer_access_id, "
        "created_by_integration_source_id) = 1",
    )

    op.add_column("messages", sa.Column("author_integration_source_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_messages_integration_author",
        "messages",
        "integration_sources",
        ["organization_id", "author_integration_source_id"],
        ["organization_id", "id"],
    )
    op.drop_constraint("ck_messages_single_author", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_single_author",
        "messages",
        "num_nonnulls(author_membership_id, author_customer_access_id, "
        "author_integration_source_id) = 1",
    )

    op.add_column("audit_events", sa.Column("actor_integration_source_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_audit_integration_actor",
        "audit_events",
        "integration_sources",
        ["organization_id", "actor_integration_source_id"],
        ["organization_id", "id"],
    )
    op.drop_constraint("ck_audit_events_single_actor", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_single_actor",
        "audit_events",
        "num_nonnulls(actor_membership_id, actor_customer_access_id, "
        "actor_integration_source_id) = 1",
    )

    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_event_id", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("ticket_id", sa.Uuid()),
        sa.Column("message_id", sa.Uuid()),
        sa.Column("error_category", sa.String(40)),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'", name="ck_webhook_receipts_valid_payload_digest"
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'rejected')", name="ck_webhook_receipts_valid_status"
        ),
        sa.CheckConstraint(
            "event_type IN ('ticket.created', 'ticket.public_message')",
            name="ck_webhook_receipts_valid_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_webhook_receipts_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["integration_sources.organization_id", "integration_sources.id"],
            name="fk_webhook_receipts_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_webhook_receipts_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id", "message_id"],
            ["messages.organization_id", "messages.ticket_id", "messages.id"],
            name="fk_webhook_receipts_message",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_receipts"),
        sa.UniqueConstraint("source_id", "external_event_id", name="uq_webhook_receipts_event"),
    )
    op.create_index(
        "ix_webhook_receipts_org_received",
        "webhook_receipts",
        ["organization_id", "received_at", "id"],
    )
    op.create_index(
        "ix_webhook_receipts_org_source",
        "webhook_receipts",
        ["organization_id", "source_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_receipts_org_source", table_name="webhook_receipts")
    op.drop_index("ix_webhook_receipts_org_received", table_name="webhook_receipts")
    op.drop_table("webhook_receipts")
    op.drop_constraint("ck_audit_events_single_actor", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_single_actor",
        "audit_events",
        "(actor_membership_id IS NOT NULL) <> (actor_customer_access_id IS NOT NULL)",
    )
    op.drop_constraint("fk_audit_integration_actor", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "actor_integration_source_id")
    op.drop_constraint("ck_messages_single_author", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_single_author",
        "messages",
        "(author_membership_id IS NOT NULL) <> (author_customer_access_id IS NOT NULL)",
    )
    op.drop_constraint("fk_messages_integration_author", "messages", type_="foreignkey")
    op.drop_column("messages", "author_integration_source_id")
    op.drop_constraint("ck_tickets_single_creator", "tickets", type_="check")
    op.create_check_constraint(
        "ck_tickets_single_creator",
        "tickets",
        "(created_by_membership_id IS NOT NULL) <> (created_by_customer_access_id IS NOT NULL)",
    )
    op.drop_constraint("fk_tickets_creator_integration", "tickets", type_="foreignkey")
    op.drop_column("tickets", "created_by_integration_source_id")
    op.drop_index("ix_integration_sources_org", table_name="integration_sources")
    op.drop_table("integration_sources")
