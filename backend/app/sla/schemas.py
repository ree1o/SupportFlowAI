from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SlaPolicyWrite(BaseModel):
    name: str = Field(default="Default SLA", min_length=1, max_length=100)
    active: bool = True
    expected_version: int | None = Field(default=None, ge=1)
    low_response_seconds: int = Field(ge=60, le=31_536_000)
    low_resolution_seconds: int = Field(ge=60, le=31_536_000)
    normal_response_seconds: int = Field(ge=60, le=31_536_000)
    normal_resolution_seconds: int = Field(ge=60, le=31_536_000)
    high_response_seconds: int = Field(ge=60, le=31_536_000)
    high_resolution_seconds: int = Field(ge=60, le=31_536_000)
    urgent_response_seconds: int = Field(ge=60, le=31_536_000)
    urgent_resolution_seconds: int = Field(ge=60, le=31_536_000)


class SlaPolicyView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    name: str
    active: bool
    current_version: int
    targets: dict[str, int]
    updated_at: datetime


class TicketSlaView(BaseModel):
    policy_version: int
    snapshot_priority: str
    response_due_at: datetime
    resolution_due_at: datetime
    response_target_seconds: int
    resolution_target_seconds: int
    response_status: str
    resolution_status: str


class AgentWorkload(BaseModel):
    membership_id: UUID
    agent_name: str
    open_tickets: int


class OperationsReport(BaseModel):
    period_start: datetime
    period_end: datetime
    as_of: datetime
    tickets_created: int
    status_counts: dict[str, int]
    priority_counts: dict[str, int]
    unresolved_tickets: int
    average_first_response_seconds: float | None
    average_first_resolution_seconds: float | None
    response_sla_compliance_rate: float | None
    resolution_sla_compliance_rate: float | None
    currently_overdue_tickets: int
    workload: list[AgentWorkload]
