import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.identity.schemas import Input, normalize_email
from app.sla.schemas import TicketSlaView
from app.tickets.models import Priority, Status, Visibility

Version = Annotated[int, Field(strict=True, ge=1)]


def clean_text(value: str, multiline: bool = False) -> str:
    if any(
        unicodedata.category(char) == "Cc" and not (multiline and char in "\n\r\t")
        for char in value
    ):
        raise ValueError("Control characters are not allowed")
    value = value.strip()
    if not value:
        raise ValueError("Text cannot be blank")
    return value if multiline else " ".join(value.split())


class ContactCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("email")
    @classmethod
    def email_text(cls, value: str | None) -> str | None:
        return normalize_email(value) if value is not None else None


class ContactUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    is_active: bool | None = None

    @model_validator(mode="after")
    def valid_changes(self) -> "ContactUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one contact field is required")
        if "name" in self.model_fields_set:
            if self.name is None:
                raise ValueError("Name cannot be null")
            self.name = clean_text(self.name)
        if "email" in self.model_fields_set and self.email is not None:
            self.email = normalize_email(self.email)
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("Active state cannot be null")
        return self


class ContactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    name: str
    email: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ContactPage(BaseModel):
    items: list[ContactView]
    total: int
    page: int
    page_size: int


class CategoryCreate(Input):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        return clean_text(value)


class CategoryUpdate(Input):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None

    @model_validator(mode="after")
    def valid_changes(self) -> "CategoryUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one category field is required")
        if "name" in self.model_fields_set:
            if self.name is None:
                raise ValueError("Name cannot be null")
            self.name = clean_text(self.name)
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("Active state cannot be null")
        return self


class CategoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TicketCreate(Input):
    requester_contact_id: UUID
    title: str = Field(min_length=1, max_length=200)
    initial_message: str = Field(min_length=1, max_length=10000)
    priority: Priority = Priority.NORMAL
    category_id: UUID | None = None
    assigned_membership_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("initial_message")
    @classmethod
    def message_text(cls, value: str) -> str:
        return clean_text(value, multiline=True)


class TicketUpdate(Input):
    expected_version: Version
    status: Status | None = None
    priority: Priority | None = None
    category_id: UUID | None = None
    assigned_membership_id: UUID | None = None

    @model_validator(mode="after")
    def changed_fields(self) -> "TicketUpdate":
        if not (self.model_fields_set - {"expected_version"}):
            raise ValueError("At least one ticket field is required")
        if ("status" in self.model_fields_set and self.status is None) or (
            "priority" in self.model_fields_set and self.priority is None
        ):
            raise ValueError("Status and priority cannot be null")
        return self


class MessageCreate(Input):
    expected_version: Version
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def message_text(cls, value: str) -> str:
        return clean_text(value, multiline=True)


class TicketRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    requester_contact_id: UUID
    created_by_membership_id: UUID | None
    created_by_integration_source_id: UUID | None = None
    title: str
    status: Status
    priority: Priority
    category_id: UUID | None
    assigned_membership_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    first_response_at: datetime | None
    first_resolved_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None


class TicketView(TicketRecord):
    sla: TicketSlaView | None = None
    requester_name: str
    requester_email: str | None
    category_name: str | None
    category_active: bool | None
    assignee_name: str | None
    assignee_active: bool | None
    allowed_transitions: list[Status]
    can_update: bool
    can_reply: bool
    can_note: bool
    can_assign_others: bool


TicketSort = Literal["created_desc", "created_asc", "updated_desc", "priority_desc"]


class TicketPage(BaseModel):
    items: list[TicketView]
    total: int
    page: int
    page_size: int


class PublicMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    author_name: str
    body: str
    created_at: datetime


class StaffMessage(PublicMessage):
    author_membership_id: UUID | None
    author_customer_access_id: UUID | None = None
    author_integration_source_id: UUID | None = None
    visibility: Visibility
    ticket_version: int


class MessagePage(BaseModel):
    items: list[StaffMessage]
    next_after_version: int | None


class PublicConversation(BaseModel):
    items: list[PublicMessage]
    next_after_version: int | None


class AuditView(BaseModel):
    id: UUID
    actor_membership_id: UUID | None
    actor_customer_access_id: UUID | None = None
    actor_integration_source_id: UUID | None = None
    actor_name: str
    action: str
    target_type: Literal["ticket", "message"]
    target_id: UUID
    changes: dict[str, Any]
    ticket_version: int
    sequence: int
    created_at: datetime


class ActivityPage(BaseModel):
    items: list[AuditView]
    next_after_version: int | None
