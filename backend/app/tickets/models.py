from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Status(StrEnum):
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Visibility(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_contacts_org_id"),
        CheckConstraint("char_length(btrim(name)) > 0", name="nonempty_name"),
        CheckConstraint("email IS NULL OR email = lower(btrim(email))", name="normalized_email"),
        Index("ix_contacts_org_created", "organization_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(254))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_categories_org_id"),
        UniqueConstraint("organization_id", "normalized_name", name="uq_categories_org_name"),
        CheckConstraint("char_length(btrim(name)) > 0", name="nonempty_name"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(80))
    normalized_name: Mapped[str] = mapped_column(
        String(80),
        Computed(
            "lower(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g'))",
            persisted=True,
        ),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_tickets_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "requester_contact_id"],
            ["contacts.organization_id", "contacts.id"],
            name="fk_tickets_requester",
        ),
        ForeignKeyConstraint(
            ["organization_id", "category_id"],
            ["categories.organization_id", "categories.id"],
            name="fk_tickets_category",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_tickets_assignee",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_tickets_creator",
        ),
        CheckConstraint("status IN ('open', 'pending', 'resolved', 'closed')", name="valid_status"),
        CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name="valid_priority"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("char_length(btrim(title)) > 0", name="nonempty_title"),
        CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name="resolution_state",
        ),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="closure_state"),
        CheckConstraint("updated_at >= created_at", name="update_time"),
        CheckConstraint("first_response_at >= created_at", name="response_time"),
        CheckConstraint("first_resolved_at >= created_at", name="first_resolution_time"),
        CheckConstraint("resolved_at >= created_at", name="resolution_time"),
        CheckConstraint("closed_at >= resolved_at", name="closure_time"),
        Index("ix_tickets_org_queue", "organization_id", "status", "created_at", "id"),
        Index(
            "ix_tickets_org_assignee",
            "organization_id",
            "assigned_membership_id",
            "updated_at",
            "id",
        ),
        Index("ix_tickets_org_category", "organization_id", "category_id", "created_at", "id"),
        Index("ix_tickets_org_requester", "organization_id", "requester_contact_id"),
        Index("ix_tickets_org_updated", "organization_id", "updated_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    requester_contact_id: Mapped[UUID]
    category_id: Mapped[UUID | None]
    assigned_membership_id: Mapped[UUID | None]
    created_by_membership_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=Status.OPEN)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.NORMAL)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("organization_id", "ticket_id", "id", name="uq_messages_org_ticket_id"),
        UniqueConstraint(
            "organization_id", "ticket_id", "ticket_version", name="uq_messages_org_ticket_version"
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_messages_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "author_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_messages_author",
        ),
        CheckConstraint("visibility IN ('public', 'internal')", name="valid_visibility"),
        CheckConstraint("char_length(btrim(body)) BETWEEN 1 AND 10000", name="valid_body"),
        CheckConstraint("ticket_version >= 1", name="positive_version"),
        Index(
            "ix_messages_public_timeline",
            "organization_id",
            "ticket_id",
            "visibility",
            "ticket_version",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    ticket_id: Mapped[UUID]
    author_membership_id: Mapped[UUID]
    author_name: Mapped[str] = mapped_column(String(100))
    visibility: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text)
    ticket_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_audit_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_audit_actor",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id", "message_id"],
            ["messages.organization_id", "messages.ticket_id", "messages.id"],
            name="fk_audit_message",
        ),
        UniqueConstraint(
            "organization_id",
            "ticket_id",
            "ticket_version",
            "sequence",
            name="uq_audit_ticket_order",
        ),
        CheckConstraint("ticket_version >= 1 AND sequence >= 0", name="valid_order"),
        CheckConstraint(
            "action IN ('ticket.created', 'ticket.status_changed', 'ticket.priority_changed', "
            "'ticket.category_changed', 'ticket.assignment_changed', "
            "'message.public_posted', 'message.note_added')",
            name="valid_action",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    ticket_id: Mapped[UUID]
    message_id: Mapped[UUID | None]
    actor_membership_id: Mapped[UUID]
    actor_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    ticket_version: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
