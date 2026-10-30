from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.tickets.models import Priority
from app.tickets.schemas import clean_text


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    contact_id: UUID

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        return clean_text(value)


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str | None) -> str | None:
        return clean_text(value) if value is not None else None

    @model_validator(mode="after")
    def nonempty(self) -> "SourceUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one source field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("Enabled state cannot be null")
        return self


class SourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    contact_id: UUID
    name: str
    enabled: bool
    credential_version: int
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IssuedCredential(BaseModel):
    source: SourceView
    credential: str


class ReceiptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    external_event_id: str
    payload_digest: str
    event_type: str
    received_at: datetime
    processed_at: datetime
    status: Literal["succeeded", "rejected"]
    ticket_id: UUID | None
    message_id: UUID | None
    error_category: str | None


class ReceiptPage(BaseModel):
    items: list[ReceiptView]
    total: int
    page: int
    page_size: int


class TicketCreatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ticket.created"]
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)
    priority: Priority = Priority.NORMAL

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        return clean_text(value, multiline=True)


class PublicMessageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ticket.public_message"]
    ticket_id: UUID
    message: str = Field(min_length=1, max_length=10000)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        return clean_text(value, multiline=True)
