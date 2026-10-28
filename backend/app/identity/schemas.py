from datetime import datetime
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.identity.models import Role


def normalize_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False)
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address") from exc
    return (result.ascii_email or result.normalized).lower()


def strip_name(value: str) -> str:
    if not value.strip():
        raise ValueError("Name is required")
    return value.strip()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Credentials(Input):
    email: str = Field(max_length=254)
    password: SecretStr

    _normalize = field_validator("email")(normalize_email)

    @field_validator("password")
    @classmethod
    def password_length(cls, value: SecretStr) -> SecretStr:
        if not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password must contain between 12 and 128 characters")
        return value


class OrganizationCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=2, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")

    _strip_name = field_validator("name")(strip_name)


class Register(Credentials):
    full_name: str = Field(min_length=1, max_length=100)
    organization: OrganizationCreate

    _strip_name = field_validator("full_name")(strip_name)


class OrganizationUpdate(Input):
    name: str = Field(min_length=1, max_length=100)

    _strip_name = field_validator("name")(strip_name)


class MemberAdd(Input):
    email: str = Field(max_length=254)
    role: Role = Role.AGENT

    _normalize = field_validator("email")(normalize_email)


class MemberUpdate(Input):
    role: Role
    is_active: bool


class Profile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    full_name: str


class OrganizationView(BaseModel):
    id: UUID
    name: str
    slug: str
    role: Role


class MemberView(BaseModel):
    id: UUID
    user_id: UUID
    email: str
    full_name: str
    role: Role
    is_active: bool


class CurrentUser(BaseModel):
    user: Profile
    organizations: list[OrganizationView]
    selected_organization_id: UUID | None
    expires_at: datetime
    absolute_expires_at: datetime


class CsrfView(BaseModel):
    csrf_token: str
