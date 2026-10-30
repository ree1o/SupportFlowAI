from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_sla_policies_org_id"),
        UniqueConstraint("organization_id", name="uq_sla_policies_org"),
        CheckConstraint("current_version >= 1", name="positive_version"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(100), default="Default SLA")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    current_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SlaPolicyVersion(Base):
    __tablename__ = "sla_policy_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["sla_policies.organization_id", "sla_policies.id"],
            name="fk_sla_versions_policy",
        ),
        ForeignKeyConstraint(
            ["organization_id", "changed_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_sla_versions_actor",
        ),
        UniqueConstraint("organization_id", "policy_id", "version", name="uq_sla_policy_version"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("jsonb_typeof(targets) = 'object'", name="valid_targets"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    policy_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer)
    targets: Mapped[dict] = mapped_column(JSONB)
    changed_by_membership_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketSla(Base):
    __tablename__ = "ticket_slas"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_ticket_slas_ticket",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_id"],
            ["sla_policies.organization_id", "sla_policies.id"],
            name="fk_ticket_slas_policy",
        ),
        ForeignKeyConstraint(
            ["organization_id", "policy_id", "policy_version"],
            [
                "sla_policy_versions.organization_id",
                "sla_policy_versions.policy_id",
                "sla_policy_versions.version",
            ],
            name="fk_ticket_slas_version",
        ),
        UniqueConstraint("organization_id", "ticket_id", name="uq_ticket_slas_ticket"),
        CheckConstraint(
            "response_target_seconds > 0 AND resolution_target_seconds > 0", name="positive_targets"
        ),
        Index(
            "ix_ticket_slas_response_due",
            "organization_id",
            "response_breached_at",
            "response_due_at",
        ),
        Index(
            "ix_ticket_slas_resolution_due",
            "organization_id",
            "resolution_breached_at",
            "resolution_due_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    ticket_id: Mapped[UUID]
    policy_id: Mapped[UUID]
    policy_version: Mapped[int] = mapped_column(Integer)
    snapshot_priority: Mapped[str] = mapped_column(String(10))
    response_target_seconds: Mapped[int] = mapped_column(Integer)
    resolution_target_seconds: Mapped[int] = mapped_column(Integer)
    response_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolution_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
