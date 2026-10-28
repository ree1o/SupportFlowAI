"""Add ticket management and history

Revision ID: 0003_tickets
Revises: 0002_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_tickets"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive schema: identity records and existing tables are preserved.
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "normalized_name",
            sa.String(length=80),
            sa.Computed(
                "lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g'))", persisted=True
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0", name=op.f("ck_categories_nonempty_name")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_categories_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("organization_id", "id", name="uq_categories_org_id"),
        sa.UniqueConstraint("organization_id", "normalized_name", name="uq_categories_org_name"),
    )
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name=op.f("ck_contacts_nonempty_name")),
        sa.CheckConstraint(
            "email IS NULL OR email = lower(btrim(email))",
            name=op.f("ck_contacts_normalized_email"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_contacts_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint("organization_id", "id", name="uq_contacts_org_id"),
    )
    op.create_index(
        "ix_contacts_org_created", "contacts", ["organization_id", "created_at", "id"], unique=False
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requester_contact_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_membership_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_membership_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name=op.f("ck_tickets_closure_state")
        ),
        sa.CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name=op.f("ck_tickets_resolution_state"),
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name=op.f("ck_tickets_valid_priority"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'pending', 'resolved', 'closed')",
            name=op.f("ck_tickets_valid_status"),
        ),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name=op.f("ck_tickets_nonempty_title")),
        sa.CheckConstraint("closed_at >= resolved_at", name=op.f("ck_tickets_closure_time")),
        sa.CheckConstraint(
            "first_resolved_at >= created_at", name=op.f("ck_tickets_first_resolution_time")
        ),
        sa.CheckConstraint(
            "first_response_at >= created_at", name=op.f("ck_tickets_response_time")
        ),
        sa.CheckConstraint("resolved_at >= created_at", name=op.f("ck_tickets_resolution_time")),
        sa.CheckConstraint("updated_at >= created_at", name=op.f("ck_tickets_update_time")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tickets_positive_version")),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_tickets_assignee",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "category_id"],
            ["categories.organization_id", "categories.id"],
            name="fk_tickets_category",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_tickets_creator",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requester_contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_tickets_requester",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_tickets_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("organization_id", "id", name="uq_tickets_org_id"),
    )
    op.create_index(
        "ix_tickets_org_assignee",
        "tickets",
        ["organization_id", "assigned_membership_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_org_category",
        "tickets",
        ["organization_id", "category_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_org_queue",
        "tickets",
        ["organization_id", "status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_org_requester",
        "tickets",
        ["organization_id", "requester_contact_id"],
        unique=False,
    )
    op.create_index(
        "ix_tickets_org_updated", "tickets", ["organization_id", "updated_at", "id"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("author_membership_id", sa.Uuid(), nullable=False),
        sa.Column("author_name", sa.String(length=100), nullable=False),
        sa.Column("visibility", sa.String(length=10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ticket_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'internal')", name=op.f("ck_messages_valid_visibility")
        ),
        sa.CheckConstraint(
            "char_length(btrim(body)) BETWEEN 1 AND 10000", name=op.f("ck_messages_valid_body")
        ),
        sa.CheckConstraint("ticket_version >= 1", name=op.f("ck_messages_positive_version")),
        sa.ForeignKeyConstraint(
            ["organization_id", "author_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_messages_author",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_messages_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_messages_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("organization_id", "ticket_id", "id", name="uq_messages_org_ticket_id"),
        sa.UniqueConstraint(
            "organization_id", "ticket_id", "ticket_version", name="uq_messages_org_ticket_version"
        ),
    )
    op.create_index(
        "ix_messages_public_timeline",
        "messages",
        ["organization_id", "ticket_id", "visibility", "ticket_version"],
        unique=False,
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("actor_membership_id", sa.Uuid(), nullable=False),
        sa.Column("actor_name", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ticket_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('ticket.created', 'ticket.status_changed', 'ticket.priority_changed', "
            "'ticket.category_changed', 'ticket.assignment_changed', "
            "'message.public_posted', 'message.note_added')",
            name=op.f("ck_audit_events_valid_action"),
        ),
        sa.CheckConstraint(
            "ticket_version >= 1 AND sequence >= 0", name=op.f("ck_audit_events_valid_order")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "actor_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_audit_actor",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id", "message_id"],
            ["messages.organization_id", "messages.ticket_id", "messages.id"],
            name="fk_audit_message",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_audit_ticket",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_audit_events_organization_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.UniqueConstraint(
            "organization_id",
            "ticket_id",
            "ticket_version",
            "sequence",
            name="uq_audit_ticket_order",
        ),
    )
    op.execute("""
        CREATE FUNCTION reject_support_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'History records are append-only';
        END;
        $$
    """)
    for table in ("messages", "audit_events"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_support_history_mutation()"
        )


def downgrade() -> None:
    # Destructive rollback is exercised only in disposable integration schemas.
    op.drop_table("audit_events")
    op.drop_index("ix_messages_public_timeline", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_tickets_org_updated", table_name="tickets")
    op.drop_index("ix_tickets_org_requester", table_name="tickets")
    op.drop_index("ix_tickets_org_queue", table_name="tickets")
    op.drop_index("ix_tickets_org_category", table_name="tickets")
    op.drop_index("ix_tickets_org_assignee", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index("ix_contacts_org_created", table_name="contacts")
    op.drop_table("contacts")
    op.drop_table("categories")
    op.execute("DROP FUNCTION reject_support_history_mutation()")
