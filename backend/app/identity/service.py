from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.identity import security
from app.identity.models import AuthSession, Membership, Organization, Role, User
from app.identity.schemas import (
    CurrentUser,
    MemberAdd,
    MemberUpdate,
    MemberView,
    OrganizationCreate,
    OrganizationView,
    Profile,
    Register,
)


@dataclass
class AuthContext:
    user: User
    session: AuthSession


@contextmanager
def transaction(
    db: Session,
    conflict_message: str = "An account, slug, or membership already exists",
) -> Iterator[None]:
    # Authentication reads may already have opened a transaction. This service owns
    # its commit; dependency cleanup is never responsible for committing writes.
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "conflict", conflict_message) from exc
    except Exception:
        db.rollback()
        raise


def authenticate(db: Session, raw: str) -> AuthContext:
    if not security.TOKEN_PATTERN.fullmatch(raw):
        raise AppError(401, "unauthenticated", "Sign in to continue")
    record = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == security.token_hash(raw))
    )
    now = security.utcnow()
    if (
        record is None
        or record.revoked_at is not None
        or record.expires_at <= now
        or record.absolute_expires_at <= now
    ):
        raise AppError(401, "unauthenticated", "Sign in to continue")
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise AppError(401, "unauthenticated", "Sign in to continue")
    return AuthContext(user, record)


def issue_session(
    db: Session,
    user_id: UUID,
    settings: Settings,
    organization_id: UUID | None,
    absolute: datetime | None = None,
) -> tuple[AuthSession, str]:
    now = security.utcnow()
    absolute = absolute or now + timedelta(seconds=settings.session_absolute_seconds)
    raw = security.new_token()
    record = AuthSession(
        user_id=user_id,
        token_hash=security.token_hash(raw),
        selected_organization_id=organization_id,
        created_at=now,
        expires_at=min(now + timedelta(seconds=settings.session_ttl_seconds), absolute),
        absolute_expires_at=absolute,
    )
    db.add(record)
    db.flush()
    return record, raw


def revoke_cookie_session(db: Session, raw: str) -> None:
    if security.TOKEN_PATTERN.fullmatch(raw):
        db.execute(
            update(AuthSession)
            .where(
                AuthSession.token_hash == security.token_hash(raw),
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=security.utcnow())
        )


def register(
    db: Session, data: Register, settings: Settings, previous: str
) -> tuple[AuthContext, str]:
    # Hash outside the transaction's write locks. Never accept a role or tenant ID at signup.
    encoded = security.hasher.hash(data.password.get_secret_value())
    with transaction(db):
        user = User(email=data.email, full_name=data.full_name, password_hash=encoded)
        organization = Organization(name=data.organization.name, slug=data.organization.slug)
        db.add_all([user, organization])
        db.flush()
        db.add(Membership(organization_id=organization.id, user_id=user.id, role=Role.OWNER))
        db.flush()
        revoke_cookie_session(db, previous)
        record, raw = issue_session(db, user.id, settings, organization.id)
    return AuthContext(user, record), raw


def login(
    db: Session,
    email: str,
    password: str,
    settings: Settings,
    previous: str,
) -> tuple[AuthContext, str]:
    user = db.scalar(select(User).where(User.email == email))
    encoded = user.password_hash if user is not None else security.dummy_hash
    valid = security.verify_password(encoded, password)
    if not valid or user is None or not user.is_active:
        raise AppError(401, "invalid_credentials", "Email or password is incorrect")
    with transaction(db):
        if security.hasher.check_needs_rehash(encoded):
            user.password_hash = security.hasher.hash(password)
        selected = db.scalar(
            select(Membership.organization_id)
            .where(
                Membership.user_id == user.id,
                Membership.is_active.is_(True),
            )
            .order_by(Membership.created_at, Membership.id)
            .limit(1)
        )
        revoke_cookie_session(db, previous)
        record, raw = issue_session(db, user.id, settings, selected)
    return AuthContext(user, record), raw


def current_user(db: Session, auth: AuthContext) -> CurrentUser:
    rows = db.execute(
        select(Organization, Membership.role)
        .join(
            Membership,
            Membership.organization_id == Organization.id,
        )
        .where(Membership.user_id == auth.user.id, Membership.is_active.is_(True))
        .order_by(Organization.name, Organization.id)
    ).all()
    organizations = [
        OrganizationView(id=org.id, name=org.name, slug=org.slug, role=role) for org, role in rows
    ]
    selected = auth.session.selected_organization_id
    if selected not in {organization.id for organization in organizations}:
        selected = None
    return CurrentUser(
        user=Profile.model_validate(auth.user),
        organizations=organizations,
        selected_organization_id=selected,
        expires_at=auth.session.expires_at,
        absolute_expires_at=auth.session.absolute_expires_at,
    )


def locked_session(db: Session, auth: AuthContext) -> AuthSession:
    record = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == auth.session.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = security.utcnow()
    if (
        record is None
        or record.revoked_at
        or record.expires_at <= now
        or record.absolute_expires_at <= now
    ):
        raise AppError(401, "unauthenticated", "Sign in to continue")
    return record


def logout(db: Session, auth: AuthContext) -> None:
    with transaction(db):
        locked_session(db, auth).revoked_at = security.utcnow()


def renew(db: Session, auth: AuthContext, settings: Settings) -> tuple[AuthContext, str]:
    with transaction(db):
        record = locked_session(db, auth)
        record.revoked_at = security.utcnow()
        new, raw = issue_session(
            db,
            auth.user.id,
            settings,
            record.selected_organization_id,
            record.absolute_expires_at,
        )
    return AuthContext(auth.user, new), raw


def organization_context(
    db: Session,
    user_id: UUID,
    organization_id: UUID,
    *,
    write: bool = False,
    shared: bool = False,
) -> tuple[Organization, Membership]:
    query = (
        select(Organization)
        .join(
            Membership,
            Membership.organization_id == Organization.id,
        )
        .where(
            Organization.id == organization_id,
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
    )
    if write:
        query = query.with_for_update(of=Organization)
    elif shared:
        # Ticket transactions may proceed concurrently, but membership mutations
        # (which take FOR UPDATE) must wait until current authorization is used.
        query = query.with_for_update(read=True, of=Organization)
    organization = db.scalar(query)
    # Refresh after acquiring the organization lock: another manager may have just
    # disabled/demoted the actor while this transaction waited for the lock.
    membership = db.scalar(
        select(Membership)
        .where(
            Membership.organization_id == organization_id,
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if organization is None or membership is None:
        raise AppError(404, "not_found", "Organization not found")
    return organization, membership


def organization_view(organization: Organization, membership: Membership) -> OrganizationView:
    return OrganizationView(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        role=Role(membership.role),
    )


def create_organization(
    db: Session, auth: AuthContext, data: OrganizationCreate
) -> OrganizationView:
    with transaction(db):
        organization = Organization(name=data.name, slug=data.slug)
        db.add(organization)
        db.flush()
        member = Membership(organization_id=organization.id, user_id=auth.user.id, role=Role.OWNER)
        db.add(member)
        db.flush()
    return organization_view(organization, member)


def select_organization(db: Session, auth: AuthContext, organization_id: UUID) -> CurrentUser:
    with transaction(db):
        organization_context(db, auth.user.id, organization_id)
        locked_session(db, auth).selected_organization_id = organization_id
    return current_user(db, auth)


def update_organization(
    db: Session,
    auth: AuthContext,
    organization_id: UUID,
    name: str,
) -> OrganizationView:
    with transaction(db):
        organization, member = organization_context(db, auth.user.id, organization_id, write=True)
        if member.role not in {Role.OWNER, Role.ADMINISTRATOR}:
            raise AppError(403, "forbidden", "Organization management permission required")
        organization.name = name
    return organization_view(organization, member)


def list_members(db: Session, auth: AuthContext, organization_id: UUID) -> list[MemberView]:
    organization_context(db, auth.user.id, organization_id)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == organization_id,
        )
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return [member_view(member, user) for member, user in rows]


def member_view(member: Membership, user: User) -> MemberView:
    return MemberView(
        id=member.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=Role(member.role),
        is_active=member.is_active,
    )


def can_manage(actor: Membership, target_role: str, new_role: str) -> None:
    if actor.role == Role.OWNER:
        return
    if actor.role == Role.ADMINISTRATOR and target_role == new_role == Role.AGENT:
        return
    raise AppError(403, "forbidden", "Membership management permission required")


def add_member(
    db: Session,
    auth: AuthContext,
    organization_id: UUID,
    data: MemberAdd,
) -> MemberView:
    with transaction(db):
        _, actor = organization_context(db, auth.user.id, organization_id, write=True)
        can_manage(actor, data.role, data.role)
        user = db.scalar(select(User).where(User.email == data.email, User.is_active.is_(True)))
        if user is None:
            raise AppError(404, "not_found", "An existing active account is required")
        target = Membership(organization_id=organization_id, user_id=user.id, role=data.role)
        db.add(target)
        db.flush()
        result = member_view(target, user)
    return result


def change_member(
    db: Session,
    auth: AuthContext,
    organization_id: UUID,
    membership_id: UUID,
    data: MemberUpdate | None,
) -> MemberView:
    with transaction(db):
        _, actor = organization_context(db, auth.user.id, organization_id, write=True)
        target = db.scalar(
            select(Membership)
            .where(
                Membership.id == membership_id,
                Membership.organization_id == organization_id,
            )
            .execution_options(populate_existing=True)
        )
        if target is None:
            raise AppError(404, "not_found", "Membership not found")
        if data is None:
            data = MemberUpdate(role=Role(target.role), is_active=False)
        can_manage(actor, target.role, data.role)
        if (
            target.is_active
            and target.role == Role.OWNER
            and (not data.is_active or data.role != Role.OWNER)
        ):
            owners = db.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.organization_id == organization_id,
                    Membership.role == Role.OWNER,
                    Membership.is_active.is_(True),
                )
            )
            if owners == 1:
                raise AppError(409, "last_owner", "An organization must retain an active owner")
        target.role = data.role
        target.is_active = data.is_active
        user = db.get(User, target.user_id)
        assert user is not None  # Reinforced by the user foreign key.
        result = member_view(target, user)
    return result


def remove_member(
    db: Session,
    auth: AuthContext,
    organization_id: UUID,
    membership_id: UUID,
) -> None:
    # Removal is a soft disable: selected-session foreign keys and history remain valid.
    change_member(db, auth, organization_id, membership_id, None)
