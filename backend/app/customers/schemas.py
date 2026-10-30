from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.identity.schemas import Credentials, Input, normalize_email, strip_name
from app.tickets.models import Priority, Status
from app.tickets.schemas import clean_text


class CustomerRegister(Credentials):
    full_name: str = Field(min_length=1, max_length=100)
    organization_slug: str = Field(min_length=2, max_length=60)

    _clean_name = field_validator("full_name")(strip_name)


class TokenInput(Input):
    token: str = Field(min_length=32, max_length=256)


class RecoveryInput(Input):
    email: str = Field(max_length=254)

    _normalize = field_validator("email")(normalize_email)


class InvitationCreate(Input):
    contact_id: UUID


class InvitationAccept(Input):
    token: str = Field(min_length=32, max_length=256)
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    password: SecretStr | None = None

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return strip_name(value) if value is not None else None

    @field_validator("password")
    @classmethod
    def password_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password must contain between 12 and 128 characters")
        return value


class CustomerTicketCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean_text(value)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        return clean_text(value, multiline=True)


class CustomerReply(Input):
    expected_version: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def clean_body(cls, value: str) -> str:
        return clean_text(value, multiline=True)


class CustomerOrganization(BaseModel):
    id: UUID
    name: str
    slug: str


class CustomerAccessView(BaseModel):
    id: UUID
    organization_id: UUID
    contact_id: UUID
    organization_name: str
    organization_slug: str


class CustomerProfile(BaseModel):
    id: UUID
    email: str
    full_name: str
    organizations: list[CustomerAccessView]


class CustomerTicketView(BaseModel):
    id: UUID
    organization_id: UUID
    title: str
    status: Status
    priority: Priority
    version: int
    created_at: datetime
    updated_at: datetime


class CustomerTicketPage(BaseModel):
    items: list[CustomerTicketView]
    total: int
    page: int
    page_size: int


class CustomerMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    author_name: str
    body: str
    created_at: datetime


class CustomerTicketDetail(CustomerTicketView):
    messages: list[CustomerMessage]
    next_after_version: int | None


class Accepted(BaseModel):
    message: str


class NotificationView(BaseModel):
    id: UUID
    organization_id: UUID
    ticket_id: UUID
    title: str
    created_at: datetime
    read_at: datetime | None


class CustomerGrantView(BaseModel):
    id: UUID
    contact_id: UUID
    contact_name: str
    email: str
    user_id: UUID
    user_name: str
    is_active: bool
    created_at: datetime
    revoked_at: datetime | None


class FailedJobView(BaseModel):
    id: UUID
    event_type: str
    attempts: int
    last_error: str | None
    available_at: datetime
