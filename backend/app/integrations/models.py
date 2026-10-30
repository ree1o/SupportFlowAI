from datetime import datetime
from uuid import UUID, uuid4

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


class IntegrationSource(Base):
    __tablename__ = "integration_sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_integration_sources_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_integration_sources_contact",
        ),
        CheckConstraint("char_length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("credential_version >= 1", name="positive_credential_version"),
        Index("ix_integration_sources_org", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    contact_id: Mapped[UUID]
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    credential_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookReceipt(Base):
    __tablename__ = "webhook_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["integration_sources.organization_id", "integration_sources.id"],
            name="fk_webhook_receipts_source",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_webhook_receipts_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id", "message_id"],
            ["messages.organization_id", "messages.ticket_id", "messages.id"],
            name="fk_webhook_receipts_message",
        ),
        UniqueConstraint("source_id", "external_event_id", name="uq_webhook_receipts_event"),
        CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="valid_payload_digest"),
        CheckConstraint("status IN ('succeeded', 'rejected')", name="valid_status"),
        CheckConstraint(
            "event_type IN ('ticket.created', 'ticket.public_message')", name="valid_event_type"
        ),
        Index("ix_webhook_receipts_org_received", "organization_id", "received_at", "id"),
        Index("ix_webhook_receipts_org_source", "organization_id", "source_id", "received_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    source_id: Mapped[UUID]
    external_event_id: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(12))
    ticket_id: Mapped[UUID | None]
    message_id: Mapped[UUID | None]
    error_category: Mapped[str | None] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSONB)
