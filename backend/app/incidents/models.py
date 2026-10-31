from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_incidents_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "owner_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incidents_owner",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incidents_creator",
        ),
        CheckConstraint(
            "status IN ('investigating', 'monitoring', 'resolved')", name="valid_status"
        ),
        CheckConstraint("severity IN ('sev1', 'sev2', 'sev3', 'sev4')", name="valid_severity"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("char_length(btrim(title)) > 0", name="nonempty_title"),
        CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolution_state"
        ),
        Index("ix_incidents_org_status_updated", "organization_id", "status", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(
        String(20), default="investigating", server_default="investigating"
    )
    severity: Mapped[str] = mapped_column(String(4), default="sev3", server_default="sev3")
    owner_membership_id: Mapped[UUID | None]
    created_by_membership_id: Mapped[UUID]
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentTicket(Base):
    __tablename__ = "incident_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_incident_tickets_incident",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_incident_tickets_ticket",
        ),
        ForeignKeyConstraint(
            ["organization_id", "linked_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incident_tickets_actor",
        ),
        Index("ix_incident_tickets_org_ticket", "organization_id", "ticket_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    incident_id: Mapped[UUID] = mapped_column(primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(primary_key=True)
    linked_by_membership_id: Mapped[UUID]
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_incident_events_incident",
        ),
        ForeignKeyConstraint(
            ["organization_id", "actor_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_incident_events_actor",
        ),
        UniqueConstraint(
            "organization_id",
            "incident_id",
            "incident_version",
            "sequence",
            name="uq_incident_event_order",
        ),
        CheckConstraint("incident_version >= 1 AND sequence >= 0", name="valid_order"),
        CheckConstraint(
            "action IN ('incident.created', 'incident.updated', 'incident.ticket_linked', "
            "'incident.ticket_unlinked', 'incident.note_added', 'incident.proposal_confirmed')",
            name="valid_action",
        ),
        CheckConstraint(
            "note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 5000", name="valid_note"
        ),
        Index("ix_incident_events_timeline", "organization_id", "incident_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    incident_id: Mapped[UUID]
    actor_membership_id: Mapped[UUID]
    actor_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    incident_version: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RadarRun(Base):
    __tablename__ = "radar_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_radar_runs_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "triggered_by_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_radar_runs_actor",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')", name="valid_status"
        ),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="valid_attempts"),
        CheckConstraint("proposal_count >= 0", name="valid_proposal_count"),
        Index("ix_radar_runs_dispatch", "status", "available_at", "locked_until"),
        Index("ix_radar_runs_org_created", "organization_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    triggered_by_membership_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    detector_version: Mapped[str] = mapped_column(
        String(30), default="lexical-v1", server_default="lexical-v1"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    proposal_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failure_type: Mapped[str | None] = mapped_column(String(120))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RadarProposal(Base):
    __tablename__ = "radar_proposals"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_radar_proposals_org_id"),
        UniqueConstraint(
            "organization_id", "group_key", "input_fingerprint", name="uq_radar_proposal_identity"
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["radar_runs.organization_id", "radar_runs.id"],
            name="fk_radar_proposals_run",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_membership_id"],
            ["memberships.organization_id", "memberships.id"],
            name="fk_radar_proposals_reviewer",
        ),
        ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_radar_proposals_incident",
        ),
        CheckConstraint("status IN ('pending', 'confirmed', 'rejected')", name="valid_status"),
        CheckConstraint("score >= 0 AND score <= 1", name="valid_score"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'", name="valid_evidence"),
        CheckConstraint(
            "(status = 'pending' AND reviewed_at IS NULL AND reviewer_membership_id IS NULL) OR "
            "(status <> 'pending' AND reviewed_at IS NOT NULL AND "
            "reviewer_membership_id IS NOT NULL)",
            name="review_state",
        ),
        Index(
            "ix_radar_proposals_org_status_created", "organization_id", "status", "created_at", "id"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    run_id: Mapped[UUID]
    group_key: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(500))
    score: Mapped[float] = mapped_column(Numeric(5, 4))
    detector_version: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    reviewer_membership_id: Mapped[UUID | None]
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incident_id: Mapped[UUID | None]
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RadarProposalTicket(Base):
    __tablename__ = "radar_proposal_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["radar_proposals.organization_id", "radar_proposals.id"],
            name="fk_radar_proposal_tickets_proposal",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"],
            ["tickets.organization_id", "tickets.id"],
            name="fk_radar_proposal_tickets_ticket",
        ),
        Index("ix_radar_proposal_tickets_ticket", "organization_id", "ticket_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    proposal_id: Mapped[UUID] = mapped_column(primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(primary_key=True)


class RadarSuppression(Base):
    __tablename__ = "radar_suppressions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["radar_proposals.organization_id", "radar_proposals.id"],
            name="fk_radar_suppressions_proposal",
        ),
        ForeignKeyConstraint(
            ["organization_id", "incident_id"],
            ["incidents.organization_id", "incidents.id"],
            name="fk_radar_suppressions_incident",
        ),
        CheckConstraint("status IN ('rejected', 'confirmed')", name="valid_status"),
        UniqueConstraint(
            "organization_id",
            "group_key",
            "input_fingerprint",
            name="uq_radar_suppression_identity",
        ),
        Index("ix_radar_suppressions_org_group", "organization_id", "group_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    group_key: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))
    proposal_id: Mapped[UUID]
    incident_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
