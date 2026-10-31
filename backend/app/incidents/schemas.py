from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IncidentStatus = Literal["investigating", "monitoring", "resolved"]
Severity = Literal["sev1", "sev2", "sev3", "sev4"]
ProposalStatus = Literal["pending", "confirmed", "rejected"]
RunStatus = Literal["pending", "running", "completed", "failed"]


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    severity: Severity = "sev3"
    owner_membership_id: UUID | None = None
    ticket_ids: list[UUID] = Field(min_length=1, max_length=20)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value

    @field_validator("ticket_ids")
    @classmethod
    def unique_tickets(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("ticket_ids must be unique")
        return value


class IncidentUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    status: IncidentStatus | None = None
    severity: Severity | None = None
    owner_membership_id: UUID | None = None

    @model_validator(mode="after")
    def reject_null_nonnullable_fields(self) -> "IncidentUpdate":
        for field in ("status", "severity"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class IncidentNote(BaseModel):
    expected_version: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def trim_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("body cannot be blank")
        return value


class IncidentLink(BaseModel):
    expected_version: int = Field(ge=1)
    ticket_id: UUID


class IncidentView(BaseModel):
    id: UUID
    organization_id: UUID
    title: str
    description: str
    status: IncidentStatus
    severity: Severity
    owner_membership_id: UUID | None
    owner_name: str | None
    created_by_membership_id: UUID
    version: int
    ticket_count: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class IncidentTicketView(BaseModel):
    id: UUID
    title: str
    status: str
    priority: str
    category_id: UUID | None
    category_name: str | None
    requester_name: str
    created_at: datetime
    linked_at: datetime


class IncidentEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_membership_id: UUID
    actor_name: str
    action: str
    note: str | None
    changes: dict
    incident_version: int
    sequence: int
    created_at: datetime


class IncidentDetail(IncidentView):
    tickets: list[IncidentTicketView]
    events: list[IncidentEventView]


class IncidentPage(BaseModel):
    items: list[IncidentView]
    total: int
    page: int
    page_size: int


class TicketCandidate(BaseModel):
    id: UUID
    title: str
    status: str
    priority: str
    category_id: UUID | None
    category_name: str | None
    requester_name: str
    created_at: datetime


class ProposalView(BaseModel):
    id: UUID
    organization_id: UUID
    run_id: UUID
    title: str
    summary: str
    score: float
    detector_version: str
    evidence: dict
    status: ProposalStatus
    ticket_ids: list[UUID]
    tickets: list[TicketCandidate]
    reviewer_membership_id: UUID | None
    reviewed_at: datetime | None
    incident_id: UUID | None
    rejection_reason: str | None
    created_at: datetime


class ProposalPage(BaseModel):
    items: list[ProposalView]
    total: int
    page: int
    page_size: int


class ProposalConfirm(BaseModel):
    ticket_ids: list[UUID] = Field(min_length=2, max_length=20)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    severity: Severity = "sev3"

    @field_validator("title")
    @classmethod
    def trim_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title cannot be blank")
        return value

    @field_validator("ticket_ids")
    @classmethod
    def unique_tickets(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("ticket_ids must be unique")
        return value


class ProposalReject(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ProposalReviewResult(BaseModel):
    proposal: ProposalView
    incident: IncidentView | None


class RadarRunView(BaseModel):
    id: UUID
    organization_id: UUID
    status: RunStatus
    detector_version: str
    attempts: int
    proposal_count: int
    failure_type: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RadarRunPage(BaseModel):
    items: list[RadarRunView]
    total: int
    page: int
    page_size: int
