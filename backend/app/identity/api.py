from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.database import get_session
from app.identity import security, service
from app.identity.schemas import (
    Credentials,
    CsrfView,
    CurrentUser,
    MemberAdd,
    MemberUpdate,
    MemberView,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationView,
    Register,
)

router = APIRouter(
    prefix="/api",
    tags=["identity"],
    responses={
        401: {"description": "Invalid credentials or session; safe error envelope"},
        403: {"description": "Insufficient role or invalid CSRF token/Origin"},
        404: {"description": "Resource absent or inaccessible to this account"},
        409: {"description": "Unique constraint or final-owner conflict"},
    },
)
DB = Annotated[Session, Depends(get_session)]
session_cookie = APIKeyCookie(name=security.SESSION_COOKIE, auto_error=False)


def csrf(
    request: Request,
    csrf_header: Annotated[
        str | None,
        Header(
            alias="X-CSRF-Token",
            description="Required for mutations; obtain from GET /api/auth/csrf",
        ),
    ] = None,
) -> None:
    security.validate_csrf(request, request.app.state.settings)


def authenticated(
    db: DB,
    raw: Annotated[str | None, Security(session_cookie)],
) -> service.AuthContext:
    return service.authenticate(db, raw or "")


Auth = Annotated[service.AuthContext, Depends(authenticated)]


def authenticated_write(auth: Auth, _: Annotated[None, Depends(csrf)]) -> service.AuthContext:
    return auth


WriteAuth = Annotated[service.AuthContext, Depends(authenticated_write)]


def session_response(
    request: Request,
    response: Response,
    db: Session,
    result: tuple[service.AuthContext, str],
) -> CurrentUser:
    auth, raw = result
    remaining = max(0, int((auth.session.expires_at - security.utcnow()).total_seconds()))
    security.set_session_cookie(response, raw, remaining, request.app.state.settings)
    return service.current_user(db, auth)


@router.get("/auth/csrf", response_model=CsrfView)
def get_csrf(request: Request, response: Response) -> CsrfView:
    return CsrfView(csrf_token=security.issue_csrf(request, response, request.app.state.settings))


@router.post(
    "/auth/register", response_model=CurrentUser, status_code=201, dependencies=[Depends(csrf)]
)
def register(data: Register, request: Request, response: Response, db: DB) -> CurrentUser:
    result = service.register(
        db,
        data,
        request.app.state.settings,
        request.cookies.get(security.SESSION_COOKIE, ""),
    )
    return session_response(request, response, db, result)


@router.post("/auth/login", response_model=CurrentUser, dependencies=[Depends(csrf)])
def login(data: Credentials, request: Request, response: Response, db: DB) -> CurrentUser:
    result = service.login(
        db,
        data.email,
        data.password.get_secret_value(),
        request.app.state.settings,
        request.cookies.get(security.SESSION_COOKIE, ""),
    )
    return session_response(request, response, db, result)


@router.get("/auth/me", response_model=CurrentUser)
def me(auth: Auth, db: DB) -> CurrentUser:
    return service.current_user(db, auth)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, auth: WriteAuth, db: DB) -> None:
    service.logout(db, auth)
    security.clear_cookies(response, request.app.state.settings)


@router.post("/auth/renew", response_model=CurrentUser)
def renew(request: Request, response: Response, auth: WriteAuth, db: DB) -> CurrentUser:
    return session_response(
        request, response, db, service.renew(db, auth, request.app.state.settings)
    )


@router.get("/organizations", response_model=list[OrganizationView], tags=["organizations"])
def organizations(auth: Auth, db: DB) -> list[OrganizationView]:
    return service.current_user(db, auth).organizations


@router.post(
    "/organizations", response_model=OrganizationView, status_code=201, tags=["organizations"]
)
def create_organization(data: OrganizationCreate, auth: WriteAuth, db: DB) -> OrganizationView:
    return service.create_organization(db, auth, data)


@router.get(
    "/organizations/{organization_id}", response_model=OrganizationView, tags=["organizations"]
)
def organization(organization_id: UUID, auth: Auth, db: DB) -> OrganizationView:
    return service.organization_view(
        *service.organization_context(db, auth.user.id, organization_id)
    )


@router.patch(
    "/organizations/{organization_id}", response_model=OrganizationView, tags=["organizations"]
)
def update_organization(
    organization_id: UUID,
    data: OrganizationUpdate,
    auth: WriteAuth,
    db: DB,
) -> OrganizationView:
    return service.update_organization(db, auth, organization_id, data.name)


@router.post(
    "/organizations/{organization_id}/select", response_model=CurrentUser, tags=["organizations"]
)
def select_organization(organization_id: UUID, auth: WriteAuth, db: DB) -> CurrentUser:
    return service.select_organization(db, auth, organization_id)


@router.get(
    "/organizations/{organization_id}/memberships",
    response_model=list[MemberView],
    tags=["organizations"],
)
def members(organization_id: UUID, auth: Auth, db: DB) -> list[MemberView]:
    return service.list_members(db, auth, organization_id)


@router.post(
    "/organizations/{organization_id}/memberships",
    response_model=MemberView,
    status_code=201,
    tags=["organizations"],
)
def add_member(organization_id: UUID, data: MemberAdd, auth: WriteAuth, db: DB) -> MemberView:
    return service.add_member(db, auth, organization_id, data)


@router.patch(
    "/organizations/{organization_id}/memberships/{membership_id}",
    response_model=MemberView,
    tags=["organizations"],
)
def change_member(
    organization_id: UUID,
    membership_id: UUID,
    data: MemberUpdate,
    auth: WriteAuth,
    db: DB,
) -> MemberView:
    return service.change_member(db, auth, organization_id, membership_id, data)


@router.delete(
    "/organizations/{organization_id}/memberships/{membership_id}",
    status_code=204,
    tags=["organizations"],
)
def remove_member(organization_id: UUID, membership_id: UUID, auth: WriteAuth, db: DB) -> None:
    service.remove_member(db, auth, organization_id, membership_id)
