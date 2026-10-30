"""Customer access and transactional background processing.

Revision ID: 0004_m4
Revises: 0003_tickets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_m4"
down_revision: str | None = "0003_tickets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.create_table(
        "customer_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_customer_access_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_customer_access_user_id_users"),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_customer_access_contact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_access"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_customer_access_org_user"),
        sa.UniqueConstraint("organization_id", "contact_id", name="uq_customer_access_org_contact"),
        sa.UniqueConstraint("organization_id", "id", name="uq_customer_access_org_id"),
    )
    op.create_index("ix_customer_access_user_active", "customer_access", ["user_id", "is_active"])

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("contact_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "purpose IN ('verify_customer', 'password_recovery')",
            name="ck_account_tokens_valid_purpose",
        ),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_account_tokens_digest"),
        sa.CheckConstraint(
            "(organization_id IS NULL) = (contact_id IS NULL)",
            name="ck_account_tokens_matching_scope",
        ),
        sa.CheckConstraint(
            "(purpose = 'verify_customer') = (organization_id IS NOT NULL)",
            name="ck_account_tokens_verification_scope",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_account_tokens_user_id_users"),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_account_tokens_contact",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_account_tokens_token_hash"),
    )
    op.create_index(
        "ix_account_tokens_user_purpose", "account_tokens", ["user_id", "purpose", "expires_at"]
    )

    op.create_table(
        "customer_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("invited_email", sa.String(254), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "invited_email = lower(btrim(invited_email))",
            name="ck_customer_invitations_normalized_email",
        ),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_customer_invitations_digest"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_customer_invitations_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_customer_invitations_contact",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_customer_invitations_created_by_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_invitations"),
        sa.UniqueConstraint("token_hash", name="uq_customer_invitations_token_hash"),
    )
    op.create_index("ix_customer_invitations_expiry", "customer_invitations", ["expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_outbox_events_organization_id_organizations",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.UniqueConstraint("event_key", name="uq_outbox_events_event_key"),
    )
    op.create_index("ix_outbox_events_pending", "outbox_events", ["available_at", "created_at"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("outbox_event_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(120)),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_background_jobs_valid_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_background_jobs_nonnegative_attempts"),
        sa.ForeignKeyConstraint(
            ["outbox_event_id"],
            ["outbox_events.id"],
            ondelete="RESTRICT",
            name="fk_background_jobs_outbox_event_id_outbox_events",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_background_jobs"),
        sa.UniqueConstraint("outbox_event_id", name="uq_background_jobs_event"),
    )
    op.create_index(
        "ix_background_jobs_due", "background_jobs", ["status", "available_at", "locked_until"]
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("audience", sa.String(8), server_default="customer", nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notifications_user_id_users"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_notifications_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_notifications_ticket",
        ),
        sa.CheckConstraint(
            "audience IN ('customer', 'staff')", name="ck_notifications_valid_audience"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.UniqueConstraint("event_key", "user_id", name="uq_notifications_event_user"),
    )
    op.create_index(
        "ix_notifications_user_unread", "notifications", ["user_id", "read_at", "created_at"]
    )

    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_deliveries"),
        sa.UniqueConstraint("event_key", name="uq_email_deliveries_event_key"),
    )

    op.add_column("tickets", sa.Column("created_by_customer_access_id", sa.Uuid()))
    op.alter_column("tickets", "created_by_membership_id", nullable=True)
    op.create_foreign_key(
        "fk_tickets_creator_customer",
        "tickets",
        "customer_access",
        ["organization_id", "created_by_customer_access_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "ck_tickets_single_creator",
        "tickets",
        "(created_by_membership_id IS NOT NULL) <> (created_by_customer_access_id IS NOT NULL)",
    )

    op.add_column("messages", sa.Column("author_customer_access_id", sa.Uuid()))
    op.alter_column("messages", "author_membership_id", nullable=True)
    op.create_foreign_key(
        "fk_messages_customer_author",
        "messages",
        "customer_access",
        ["organization_id", "author_customer_access_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "ck_messages_single_author",
        "messages",
        "(author_membership_id IS NOT NULL) <> (author_customer_access_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_messages_internal_staff_only",
        "messages",
        "visibility <> 'internal' OR author_membership_id IS NOT NULL",
    )

    op.add_column("audit_events", sa.Column("actor_customer_access_id", sa.Uuid()))
    op.alter_column("audit_events", "actor_membership_id", nullable=True)
    op.create_foreign_key(
        "fk_audit_customer_actor",
        "audit_events",
        "customer_access",
        ["organization_id", "actor_customer_access_id"],
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "ck_audit_events_single_actor",
        "audit_events",
        "(actor_membership_id IS NOT NULL) <> (actor_customer_access_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_events_single_actor", "audit_events", type_="check")
    op.drop_constraint("fk_audit_customer_actor", "audit_events", type_="foreignkey")
    op.drop_column("audit_events", "actor_customer_access_id")
    op.alter_column("audit_events", "actor_membership_id", nullable=False)
    op.drop_constraint("ck_messages_internal_staff_only", "messages", type_="check")
    op.drop_constraint("ck_messages_single_author", "messages", type_="check")
    op.drop_constraint("fk_messages_customer_author", "messages", type_="foreignkey")
    op.drop_column("messages", "author_customer_access_id")
    op.alter_column("messages", "author_membership_id", nullable=False)
    op.drop_constraint("ck_tickets_single_creator", "tickets", type_="check")
    op.drop_constraint("fk_tickets_creator_customer", "tickets", type_="foreignkey")
    op.drop_column("tickets", "created_by_customer_access_id")
    op.alter_column("tickets", "created_by_membership_id", nullable=False)
    for table in (
        "email_deliveries",
        "notifications",
        "background_jobs",
        "outbox_events",
        "customer_invitations",
        "account_tokens",
        "customer_access",
    ):
        op.drop_table(table)
    op.drop_column("users", "email_verified_at")
