from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, Security
from fastapi.security import APIKeyCookie
from pydantic import Field, SecretStr, field_validator

from app.customers import service
from app.customers.schemas import (
    Accepted,
    CustomerAccessView,
    CustomerGrantView,
    CustomerProfile,
    CustomerRegister,
    CustomerReply,
    CustomerTicketCreate,
    CustomerTicketDetail,
    CustomerTicketPage,
    CustomerTicketView,
    FailedJobView,
    InvitationAccept,
    InvitationCreate,
    NotificationView,
    RecoveryInput,
    TokenInput,
)
from app.identity import security
from app.identity.api import DB, Auth, WriteAuth, csrf
from app.identity.schemas import Input
from app.identity.service import AuthContext, authenticate
from app.tickets.models import Status

router = APIRouter(prefix="/api/customer", tags=["customer portal"])
identity_router = APIRouter(
    prefix="/api/organizations/{organization_id}/customer-access", tags=["customer access"]
)
staff_router = APIRouter(
    prefix="/api/organizations/{organization_id}", tags=["organization operations"]
)
Size = Annotated[int, Query(ge=1, le=100)]
session_cookie = APIKeyCookie(name=security.SESSION_COOKIE, auto_error=False)


class CustomerRegisterToken(Input):
    token: str = Field(min_length=32, max_length=256)
    password: SecretStr

    @field_validator("password")
    @classmethod
    def password_length(cls, value: SecretStr) -> SecretStr:
        if not 12 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password must contain between 12 and 128 characters")
        return value


def optional_auth(
    db: DB, raw: Annotated[str | None, Security(session_cookie)]
) -> AuthContext | None:
    if not raw:
        return None
    try:
        return authenticate(db, raw)
    except Exception:
        return None


OptionalAuth = Annotated[AuthContext | None, Depends(optional_auth)]


@router.post("/register", status_code=202, response_model=Accepted, dependencies=[Depends(csrf)])
def register(data: CustomerRegister, request: Request, db: DB) -> Accepted:
    service.register_customer(db, request.app.state.settings, data)
    return Accepted(message="If your details are eligible, an email with next steps has been sent.")


@router.post("/verify", status_code=204, dependencies=[Depends(csrf)])
def verify(data: TokenInput, request: Request, db: DB) -> None:
    service.verify_customer(db, request.app.state.settings, data.token)


@router.post(
    "/password-recovery", status_code=202, response_model=Accepted, dependencies=[Depends(csrf)]
)
def recovery(data: RecoveryInput, request: Request, db: DB) -> Accepted:
    service.request_recovery(db, request.app.state.settings, data.email)
    return Accepted(message="If an eligible account exists, a recovery email has been sent.")


@router.post("/password-recovery/complete", status_code=204, dependencies=[Depends(csrf)])
def recovery_complete(data: CustomerRegisterToken, request: Request, db: DB) -> None:
    service.complete_recovery(
        db,
        request.app.state.settings,
        data.token,
        data.password.get_secret_value(),
    )


@router.post("/invitations/accept", response_model=Accepted, dependencies=[Depends(csrf)])
def accept_invitation(
    data: InvitationAccept,
    request: Request,
    response: Response,
    db: DB,
    auth: OptionalAuth,
) -> Accepted:
    result, raw = service.accept_invitation(
        data=data, db=db, auth=auth, settings=request.app.state.settings
    )
    if result is not None and raw is not None:
        seconds = max(0, int((result.session.expires_at - security.utcnow()).total_seconds()))
        security.set_session_cookie(response, raw, seconds, request.app.state.settings)
    return Accepted(message="Invitation accepted. Sign in to view your support requests.")


@router.get("/me", response_model=CustomerProfile)
def me(auth: Auth, db: DB) -> CustomerProfile:
    return service.customer_profile(db, auth)


@router.get("/organizations", response_model=list[CustomerAccessView])
def organizations(auth: Auth, db: DB) -> list[CustomerAccessView]:
    return service.customer_profile(db, auth).organizations


@router.get("/organizations/{organization_id}/tickets", response_model=CustomerTicketPage)
def tickets(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    status: Status | None = None,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Size = 20,
):
    return service.list_tickets(db, auth, organization_id, status, page, page_size)


@router.post(
    "/organizations/{organization_id}/tickets",
    response_model=CustomerTicketView,
    status_code=201,
)
def create_ticket(organization_id: UUID, data: CustomerTicketCreate, auth: WriteAuth, db: DB):
    return service.create_ticket(db, auth, organization_id, data)


@router.get(
    "/organizations/{organization_id}/tickets/{ticket_id}", response_model=CustomerTicketDetail
)
def ticket(
    organization_id: UUID,
    ticket_id: UUID,
    auth: Auth,
    db: DB,
    after_version: Annotated[int, Query(ge=0)] = 0,
    limit: Size = 50,
):
    return service.ticket_detail(db, auth, organization_id, ticket_id, after_version, limit)


@router.post(
    "/organizations/{organization_id}/tickets/{ticket_id}/replies",
    response_model=CustomerTicketView,
    status_code=201,
)
def reply(
    organization_id: UUID,
    ticket_id: UUID,
    data: CustomerReply,
    auth: WriteAuth,
    db: DB,
    request: Request,
):
    return service.reply(db, auth, organization_id, ticket_id, data, request.app.state.settings)


@router.get("/notifications", response_model=list[NotificationView])
def notifications(auth: Auth, db: DB, unread_only: bool = False):
    return service.notifications(db, auth, unread_only)


@router.post("/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: UUID, auth: WriteAuth, db: DB):
    service.mark_notification_read(db, auth, notification_id)


@staff_router.get("/customer-access", response_model=list[CustomerGrantView])
def customer_access(organization_id: UUID, auth: Auth, db: DB):
    return service.customer_grants(db, auth, organization_id)


@staff_router.get("/notifications", response_model=list[NotificationView])
def staff_notifications(organization_id: UUID, auth: Auth, db: DB, unread_only: bool = False):
    return service.staff_notifications(db, auth, organization_id, unread_only)


@staff_router.post("/notifications/{notification_id}/read", status_code=204)
def staff_mark_read(organization_id: UUID, notification_id: UUID, auth: WriteAuth, db: DB):
    service.mark_staff_notification_read(db, auth, organization_id, notification_id)


@staff_router.get("/background-jobs/failed", response_model=list[FailedJobView])
def failed_jobs(organization_id: UUID, auth: Auth, db: DB):
    return service.failed_jobs(db, auth, organization_id)


@staff_router.post("/background-jobs/{job_id}/retry", status_code=204)
def retry_job(organization_id: UUID, job_id: UUID, auth: WriteAuth, db: DB):
    service.retry_failed_job(db, auth, organization_id, job_id)


@identity_router.post("/invitations", status_code=202, response_model=Accepted)
def invite(
    organization_id: UUID,
    data: InvitationCreate,
    auth: WriteAuth,
    db: DB,
    request: Request,
) -> Accepted:
    service.invite_customer(db, auth, organization_id, data.contact_id, request.app.state.settings)
    return Accepted(message="Invitation queued for delivery.")


@identity_router.delete("/{access_id}", status_code=204)
def revoke(organization_id: UUID, access_id: UUID, auth: WriteAuth, db: DB) -> None:
    service.revoke_customer(db, auth, organization_id, access_id)
