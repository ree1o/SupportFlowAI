from datetime import timedelta
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.customers.models import (
    AccountToken,
    BackgroundJob,
    CustomerAccess,
    CustomerInvitation,
    Notification,
    OutboxEvent,
)
from app.customers.schemas import (
    CustomerAccessView,
    CustomerGrantView,
    CustomerMessage,
    CustomerProfile,
    CustomerReply,
    CustomerTicketCreate,
    CustomerTicketDetail,
    CustomerTicketPage,
    CustomerTicketView,
    FailedJobView,
    InvitationAccept,
)
from app.errors import AppError
from app.identity import security
from app.identity.models import AuthSession, Membership, Organization, Role, User
from app.identity.schemas import normalize_email
from app.identity.service import AuthContext, issue_session, organization_context, transaction
from app.sla.service import snapshot_ticket
from app.tenancy import (
    set_account_token_context,
    set_invitation_token_context,
    set_tenant_context,
    set_user_context,
)
from app.tickets.models import AuditEvent, Contact, Message, Priority, Status, Ticket, Visibility

TOKEN_TTL = timedelta(hours=1)
INVITATION_TTL = timedelta(days=7)


def encrypt(settings: Settings, value: str) -> str:
    return (
        Fernet(settings.token_encryption_key.get_secret_value().encode())
        .encrypt(value.encode())
        .decode()
    )


def decrypt(settings: Settings, value: str) -> str:
    return (
        Fernet(settings.token_encryption_key.get_secret_value().encode())
        .decrypt(value.encode())
        .decode()
    )


def add_email(
    db: Session,
    key: str,
    event_type: str,
    recipient: str,
    subject: str,
    body: str,
    organization_id: UUID | None = None,
) -> None:
    event = OutboxEvent(
        organization_id=organization_id,
        event_key=key,
        event_type=event_type,
        payload={"to": recipient, "subject": subject, "body": body},
    )
    db.add(event)
    db.flush()
    db.add(BackgroundJob(outbox_event_id=event.id))


def add_token(
    db: Session,
    settings: Settings,
    user: User,
    purpose: str,
    expires,
    *,
    organization_id: UUID | None = None,
    contact_id: UUID | None = None,
) -> str:
    set_user_context(db, user.id)
    if organization_id is not None:
        set_tenant_context(db, organization_id)
    db.execute(
        update(AccountToken)
        .where(
            AccountToken.user_id == user.id,
            AccountToken.purpose == purpose,
            AccountToken.used_at.is_(None),
            AccountToken.revoked_at.is_(None),
        )
        .values(revoked_at=security.utcnow())
    )
    raw = security.new_token()
    db.add(
        AccountToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=security.token_hash(raw),
            organization_id=organization_id,
            contact_id=contact_id,
            expires_at=expires,
        )
    )
    return raw


def register_customer(db: Session, settings: Settings, data) -> None:
    """Generic response; only verified inbox access can create a customer grant."""
    email = normalize_email(data.email)
    # Hash even for an existing account to reduce account-enumeration timing signals.
    encoded = security.hasher.hash(data.password.get_secret_value())
    with transaction(db):
        existing = db.scalar(select(User).where(User.email == email))
        org = db.scalar(select(Organization).where(Organization.slug == data.organization_slug))
        contact = None
        if org is not None:
            set_tenant_context(db, org.id)
            contacts = list(
                db.scalars(
                    select(Contact).where(
                        Contact.organization_id == org.id,
                        Contact.email == email,
                        Contact.is_active.is_(True),
                    )
                )
            )
            if len(contacts) == 1:
                contact = contacts[0]
        if existing is None and org is not None and contact is not None:
            user = User(email=email, full_name=data.full_name, password_hash=encoded)
            db.add(user)
            db.flush()
            raw = add_token(
                db,
                settings,
                user,
                "verify_customer",
                security.utcnow() + TOKEN_TTL,
                organization_id=org.id,
                contact_id=contact.id,
            )
            url = f"{settings.public_app_url}/customer/verify?token={encrypt(settings, raw)}"
            add_email(
                db,
                f"verify:{user.id}:{security.token_hash(raw)}",
                "email",
                email,
                "Verify your SupportFlow customer account",
                f"Confirm your email to access your support requests: {url}",
                org.id,
            )


def verify_customer(db: Session, settings: Settings, token: str) -> None:
    try:
        token = decrypt(settings, token)
    except Exception:
        raise AppError(400, "invalid_token", "Verification link is invalid or expired") from None
    set_account_token_context(db, security.token_hash(token))
    with transaction(db, "Verification link is invalid or expired"):
        record = db.scalar(
            select(AccountToken)
            .where(
                AccountToken.token_hash == security.token_hash(token),
                AccountToken.purpose == "verify_customer",
                AccountToken.used_at.is_(None),
                AccountToken.revoked_at.is_(None),
                AccountToken.expires_at > security.utcnow(),
            )
            .execution_options(populate_existing=True)
        )
        if record is None or record.organization_id is None or record.contact_id is None:
            raise AppError(400, "invalid_token", "Verification link is invalid or expired")
        set_user_context(db, record.user_id)
        set_tenant_context(db, record.organization_id)
        record = db.scalar(
            select(AccountToken)
            .where(
                AccountToken.id == record.id,
                AccountToken.token_hash == security.token_hash(token),
                AccountToken.purpose == "verify_customer",
                AccountToken.used_at.is_(None),
                AccountToken.revoked_at.is_(None),
                AccountToken.expires_at > security.utcnow(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise AppError(400, "invalid_token", "Verification link is invalid or expired")
        user = db.scalar(select(User).where(User.id == record.user_id).with_for_update())
        if user is None or not user.is_active:
            raise AppError(400, "invalid_token", "Verification link is invalid or expired")
        contact = db.scalar(
            select(Contact).where(
                Contact.organization_id == record.organization_id,
                Contact.id == record.contact_id,
                Contact.is_active.is_(True),
                Contact.email == user.email,
            )
        )
        if contact is None:
            raise AppError(400, "invalid_token", "Verification link is invalid or expired")
        now = security.utcnow()
        user.email_verified_at = now
        record.used_at = now
        db.add(
            CustomerAccess(
                organization_id=record.organization_id,
                contact_id=record.contact_id,
                user_id=user.id,
            )
        )


def request_recovery(db: Session, settings: Settings, email: str) -> None:
    email = normalize_email(email)
    with transaction(db):
        user = db.scalar(
            select(User).where(
                User.email == email, User.is_active.is_(True), User.email_verified_at.is_not(None)
            )
        )
        has_active_access = False
        if user is not None:
            set_user_context(db, user.id)
            access_orgs = db.scalars(
                select(CustomerAccess.organization_id).where(
                    CustomerAccess.user_id == user.id, CustomerAccess.is_active.is_(True)
                )
            )
            for organization_id in access_orgs:
                set_tenant_context(db, organization_id)
                if db.scalar(
                    select(CustomerAccess.id)
                    .join(
                        Contact,
                        (Contact.organization_id == CustomerAccess.organization_id)
                        & (Contact.id == CustomerAccess.contact_id),
                    )
                    .where(
                        CustomerAccess.user_id == user.id,
                        CustomerAccess.organization_id == organization_id,
                        CustomerAccess.is_active.is_(True),
                        Contact.is_active.is_(True),
                    )
                ):
                    has_active_access = True
                    break
        if user is not None and has_active_access:
            raw = add_token(db, settings, user, "password_recovery", security.utcnow() + TOKEN_TTL)
            url = f"{settings.public_app_url}/customer/recover?token={encrypt(settings, raw)}"
            add_email(
                db,
                f"recovery:{user.id}:{security.token_hash(raw)}",
                "email",
                user.email,
                "Reset your SupportFlow password",
                f"Use this single-use link to reset your password: {url}",
            )


def complete_recovery(db: Session, settings: Settings, token: str, password: str) -> None:
    try:
        token = decrypt(settings, token)
    except Exception:
        raise AppError(400, "invalid_token", "Recovery link is invalid or expired") from None
    set_account_token_context(db, security.token_hash(token))
    encoded = security.hasher.hash(password)
    with transaction(db, "Recovery link is invalid or expired"):
        record = db.scalar(
            select(AccountToken)
            .where(
                AccountToken.token_hash == security.token_hash(token),
                AccountToken.purpose == "password_recovery",
                AccountToken.used_at.is_(None),
                AccountToken.revoked_at.is_(None),
                AccountToken.expires_at > security.utcnow(),
            )
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise AppError(400, "invalid_token", "Recovery link is invalid or expired")
        set_user_context(db, record.user_id)
        record = db.scalar(
            select(AccountToken)
            .where(
                AccountToken.id == record.id,
                AccountToken.token_hash == security.token_hash(token),
                AccountToken.purpose == "password_recovery",
                AccountToken.used_at.is_(None),
                AccountToken.revoked_at.is_(None),
                AccountToken.expires_at > security.utcnow(),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise AppError(400, "invalid_token", "Recovery link is invalid or expired")
        user = db.scalar(select(User).where(User.id == record.user_id).with_for_update())
        if user is None or not user.is_active:
            raise AppError(400, "invalid_token", "Recovery link is invalid or expired")
        now = security.utcnow()
        user.password_hash = encoded
        record.used_at = now
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )


def invite_customer(
    db: Session, auth: AuthContext, organization_id: UUID, contact_id: UUID, settings: Settings
) -> None:
    with transaction(db, "Invitation conflicts with existing access"):
        _, actor = organization_context(db, auth.user.id, organization_id, write=True)
        if actor.role not in {Role.OWNER, Role.ADMINISTRATOR}:
            raise AppError(403, "forbidden", "Customer access management permission required")
        contact = db.scalar(
            select(Contact)
            .where(Contact.organization_id == organization_id, Contact.id == contact_id)
            .with_for_update()
        )
        if contact is None or not contact.is_active or not contact.email:
            raise AppError(404, "not_found", "Active contact with an email is required")
        existing = db.scalar(
            select(CustomerAccess.id).where(
                CustomerAccess.organization_id == organization_id,
                CustomerAccess.contact_id == contact_id,
                CustomerAccess.is_active.is_(True),
            )
        )
        if existing:
            raise AppError(
                409, "customer_access_exists", "This contact already has customer access"
            )
        raw = security.new_token()
        now = security.utcnow()
        db.execute(
            update(CustomerInvitation)
            .where(
                CustomerInvitation.organization_id == organization_id,
                CustomerInvitation.contact_id == contact_id,
                CustomerInvitation.accepted_at.is_(None),
                CustomerInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        invitation = CustomerInvitation(
            organization_id=organization_id,
            contact_id=contact_id,
            invited_email=contact.email,
            token_hash=security.token_hash(raw),
            created_by_user_id=auth.user.id,
            expires_at=now + INVITATION_TTL,
        )
        db.add(invitation)
        db.flush()
        encrypted = encrypt(settings, raw)
        url = f"{settings.public_app_url}/customer/invitation?token={encrypted}"
        add_email(
            db,
            f"invite:{invitation.id}",
            "email",
            contact.email,
            "You are invited to SupportFlow",
            f"Create or sign in to your customer account to access your support requests: {url}",
            organization_id,
        )


def accept_invitation(
    db: Session, data: InvitationAccept, auth: AuthContext | None, settings: Settings
) -> tuple[AuthContext | None, str | None]:
    try:
        raw = decrypt(settings, data.token)
    except Exception:
        raise AppError(400, "invalid_token", "Invitation is invalid or expired") from None
    token_hash = security.token_hash(raw)
    set_invitation_token_context(db, token_hash)
    now = security.utcnow()
    with transaction(db, "Invitation is invalid or expired"):
        # A token policy permits this exact read before tenant context exists.
        # Lock only after setting tenant context, because row locks also require
        # an UPDATE policy and the tenant policy must remain fail-closed.
        invite = db.scalar(
            select(CustomerInvitation).where(CustomerInvitation.token_hash == token_hash)
        )
        if invite is None:
            raise AppError(400, "invalid_token", "Invitation is invalid or expired")
        set_tenant_context(db, invite.organization_id)
        invite = db.scalar(
            select(CustomerInvitation)
            .where(
                CustomerInvitation.id == invite.id,
                CustomerInvitation.organization_id == invite.organization_id,
                CustomerInvitation.token_hash == token_hash,
                CustomerInvitation.accepted_at.is_(None),
                CustomerInvitation.revoked_at.is_(None),
                CustomerInvitation.expires_at > now,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invite is None:
            raise AppError(400, "invalid_token", "Invitation is invalid or expired")
        contact = db.scalar(
            select(Contact).where(
                Contact.organization_id == invite.organization_id,
                Contact.id == invite.contact_id,
                Contact.email == invite.invited_email,
                Contact.is_active.is_(True),
            )
        )
        if contact is None:
            raise AppError(400, "invalid_token", "Invitation is invalid or expired")
        user = db.scalar(select(User).where(User.email == invite.invited_email).with_for_update())
        session_result: tuple[AuthContext, str | None]
        if user is not None:
            if auth is None or auth.user.id != user.id:
                raise AppError(401, "account_sign_in_required", "Sign in to accept this invitation")
            user.email_verified_at = now
            session_result = (auth, None)
        else:
            if auth is not None or data.password is None or data.full_name is None:
                raise AppError(
                    422, "account_details_required", "Enter your name and a new password"
                )
            user = User(
                email=invite.invited_email,
                full_name=data.full_name,
                password_hash=security.hasher.hash(data.password.get_secret_value()),
                email_verified_at=now,
            )
            db.add(user)
            db.flush()
            session_record, session_token = issue_session(db, user.id, settings, None)
            session_result = (AuthContext(user, session_record), session_token)
        if not user.is_active:
            raise AppError(400, "invalid_token", "Invitation is invalid or expired")
        existing_access = db.scalar(
            select(CustomerAccess)
            .where(
                CustomerAccess.organization_id == invite.organization_id,
                CustomerAccess.contact_id == invite.contact_id,
            )
            .with_for_update()
        )
        if existing_access is None:
            db.add(
                CustomerAccess(
                    organization_id=invite.organization_id,
                    contact_id=invite.contact_id,
                    user_id=user.id,
                )
            )
        elif existing_access.user_id == user.id and not existing_access.is_active:
            existing_access.is_active = True
            existing_access.revoked_at = None
        else:
            raise AppError(409, "customer_access_conflict", "Customer access could not be restored")
        invite.accepted_at = now
        db.flush()
        return session_result


def revoke_customer(db: Session, auth: AuthContext, organization_id: UUID, access_id: UUID) -> None:
    with transaction(db):
        _, actor = organization_context(db, auth.user.id, organization_id, write=True)
        if actor.role not in {Role.OWNER, Role.ADMINISTRATOR}:
            raise AppError(403, "forbidden", "Customer access management permission required")
        record = db.scalar(
            select(CustomerAccess)
            .where(
                CustomerAccess.organization_id == organization_id, CustomerAccess.id == access_id
            )
            .with_for_update()
        )
        if record is None:
            raise AppError(404, "not_found", "Customer access not found")
        record.is_active = False
        record.revoked_at = security.utcnow()


def customer_grants(
    db: Session, auth: AuthContext, organization_id: UUID
) -> list[CustomerGrantView]:
    _, actor = organization_context(db, auth.user.id, organization_id)
    if actor.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise AppError(403, "forbidden", "Customer access management permission required")
    rows = db.execute(
        select(CustomerAccess, Contact, User)
        .join(
            Contact,
            (Contact.organization_id == CustomerAccess.organization_id)
            & (Contact.id == CustomerAccess.contact_id),
        )
        .join(User, User.id == CustomerAccess.user_id)
        .where(CustomerAccess.organization_id == organization_id)
        .order_by(Contact.name, CustomerAccess.id)
    )
    return [
        CustomerGrantView(
            id=access.id,
            contact_id=contact.id,
            contact_name=contact.name,
            email=user.email,
            user_id=user.id,
            user_name=user.full_name,
            is_active=access.is_active,
            created_at=access.created_at,
            revoked_at=access.revoked_at,
        )
        for access, contact, user in rows
    ]


def failed_jobs(db: Session, auth: AuthContext, organization_id: UUID) -> list[FailedJobView]:
    _, actor = organization_context(db, auth.user.id, organization_id)
    if actor.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise AppError(403, "forbidden", "Background job management permission required")
    rows = db.execute(
        select(BackgroundJob, OutboxEvent)
        .join(OutboxEvent, OutboxEvent.id == BackgroundJob.outbox_event_id)
        .where(OutboxEvent.organization_id == organization_id, BackgroundJob.status == "failed")
        .order_by(BackgroundJob.available_at, BackgroundJob.id)
        .limit(100)
    )
    return [
        FailedJobView(
            id=job.id,
            event_type=event.event_type,
            attempts=job.attempts,
            last_error=job.last_error,
            available_at=job.available_at,
        )
        for job, event in rows
    ]


def retry_failed_job(db: Session, auth: AuthContext, organization_id: UUID, job_id: UUID) -> None:
    with transaction(db):
        _, actor = organization_context(db, auth.user.id, organization_id, write=True)
        if actor.role not in {Role.OWNER, Role.ADMINISTRATOR}:
            raise AppError(403, "forbidden", "Background job management permission required")
        row = db.execute(
            select(BackgroundJob, OutboxEvent)
            .join(OutboxEvent, OutboxEvent.id == BackgroundJob.outbox_event_id)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.status == "failed",
                OutboxEvent.organization_id == organization_id,
            )
            .with_for_update()
        ).first()
        if row is None:
            raise AppError(404, "not_found", "Failed job not found")
        job, event = row
        job.status = "pending"
        job.attempts = 0
        job.available_at = security.utcnow()
        job.locked_until = None
        job.last_error = None
        event.available_at = job.available_at


def active_access(db: Session, auth: AuthContext, organization_id: UUID | None = None):
    set_user_context(db, auth.user.id)
    if organization_id is not None:
        set_tenant_context(db, organization_id)
    query = (
        select(CustomerAccess, Organization)
        .join(Organization, Organization.id == CustomerAccess.organization_id)
        .join(
            Contact,
            (Contact.id == CustomerAccess.contact_id)
            & (Contact.organization_id == CustomerAccess.organization_id),
        )
        .where(
            CustomerAccess.user_id == auth.user.id,
            CustomerAccess.is_active.is_(True),
            Contact.is_active.is_(True),
        )
    )
    if organization_id is not None:
        query = query.where(CustomerAccess.organization_id == organization_id)
        return list(db.execute(query.order_by(Organization.name, Organization.id)))

    organization_ids = list(
        db.scalars(
            select(CustomerAccess.organization_id)
            .where(CustomerAccess.user_id == auth.user.id, CustomerAccess.is_active.is_(True))
            .order_by(CustomerAccess.organization_id)
        )
    )
    rows: list[tuple[CustomerAccess, Organization]] = []
    for tenant_id in organization_ids:
        set_tenant_context(db, tenant_id)
        result = db.execute(
            query.where(CustomerAccess.organization_id == tenant_id).order_by(
                Organization.name, Organization.id
            )
        )
        rows.extend((access, organization) for access, organization in result)
    return rows


def customer_profile(db: Session, auth: AuthContext) -> CustomerProfile:
    rows = active_access(db, auth)
    if not rows:
        raise AppError(404, "not_found", "Customer profile not found")
    return CustomerProfile(
        id=auth.user.id,
        email=auth.user.email,
        full_name=auth.user.full_name,
        organizations=[
            CustomerAccessView(
                id=access.id,
                organization_id=org.id,
                contact_id=access.contact_id,
                organization_name=org.name,
                organization_slug=org.slug,
            )
            for access, org in rows
        ],
    )


def get_access(db: Session, auth: AuthContext, organization_id: UUID) -> CustomerAccess:
    rows = active_access(db, auth, organization_id)
    if not rows:
        raise AppError(404, "not_found", "Customer organization not found")
    return rows[0][0]


def ticket_view(ticket: Ticket) -> CustomerTicketView:
    return CustomerTicketView(
        id=ticket.id,
        organization_id=ticket.organization_id,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        version=ticket.version,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def list_tickets(
    db: Session, auth: AuthContext, org: UUID, status: Status | None, page: int, size: int
):
    access = get_access(db, auth, org)
    query = select(Ticket).where(
        Ticket.organization_id == org, Ticket.requester_contact_id == access.contact_id
    )
    if status is not None:
        query = query.where(Ticket.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return CustomerTicketPage(
        items=[ticket_view(row) for row in rows], total=total, page=page, page_size=size
    )


def create_ticket(db: Session, auth: AuthContext, org: UUID, data: CustomerTicketCreate):
    with transaction(db, "Unable to create support request"):
        access = get_access(db, auth, org)
        contact = db.scalar(
            select(Contact)
            .where(
                Contact.organization_id == org,
                Contact.id == access.contact_id,
                Contact.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
        if contact is None:
            raise AppError(404, "not_found", "Customer organization not found")
        now = security.utcnow()
        ticket = Ticket(
            organization_id=org,
            requester_contact_id=contact.id,
            created_by_customer_access_id=access.id,
            title=data.title,
            status=Status.OPEN,
            priority=Priority.NORMAL,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(ticket)
        db.flush()
        snapshot_ticket(db, ticket, now)
        message = Message(
            organization_id=org,
            ticket_id=ticket.id,
            author_customer_access_id=access.id,
            author_name=auth.user.full_name,
            visibility=Visibility.PUBLIC,
            body=data.message,
            ticket_version=1,
            created_at=now,
        )
        db.add(message)
        db.add(
            AuditEvent(
                organization_id=org,
                ticket_id=ticket.id,
                actor_customer_access_id=access.id,
                actor_name=auth.user.full_name,
                action="ticket.created",
                changes={"source": "customer_portal"},
                ticket_version=1,
                sequence=0,
                created_at=now,
            )
        )
        from app.semantic.service import enqueue_ticket

        enqueue_ticket(db, org, ticket.id)
        db.flush()
        return ticket_view(ticket)


def customer_ticket(db: Session, auth: AuthContext, org: UUID, ticket_id: UUID) -> Ticket:
    access = get_access(db, auth, org)
    ticket = db.scalar(
        select(Ticket).where(
            Ticket.organization_id == org,
            Ticket.requester_contact_id == access.contact_id,
            Ticket.id == ticket_id,
        )
    )
    if ticket is None:
        raise AppError(404, "not_found", "Support request not found")
    return ticket


def ticket_detail(
    db: Session, auth: AuthContext, org: UUID, ticket_id: UUID, after: int, limit: int
):
    ticket = customer_ticket(db, auth, org, ticket_id)
    rows = list(
        db.scalars(
            select(Message)
            .where(
                Message.organization_id == org,
                Message.ticket_id == ticket_id,
                Message.visibility == Visibility.PUBLIC,
                Message.ticket_version > after,
            )
            .order_by(Message.ticket_version)
            .limit(limit + 1)
        )
    )
    cursor = rows[limit - 1].ticket_version if len(rows) > limit else None
    return CustomerTicketDetail(
        **ticket_view(ticket).model_dump(),
        messages=[CustomerMessage.model_validate(row) for row in rows[:limit]],
        next_after_version=cursor,
    )


def reply(
    db: Session,
    auth: AuthContext,
    org: UUID,
    ticket_id: UUID,
    data: CustomerReply,
    settings: Settings,
):
    with transaction(db, "Conversation update conflicted with existing history"):
        access = get_access(db, auth, org)
        ticket = db.scalar(
            select(Ticket)
            .where(
                Ticket.organization_id == org,
                Ticket.requester_contact_id == access.contact_id,
                Ticket.id == ticket_id,
            )
            .with_for_update()
        )
        if ticket is None:
            raise AppError(404, "not_found", "Support request not found")
        if ticket.version != data.expected_version:
            raise AppError(
                409,
                "version_conflict",
                "Request changed; reload before replying",
                {"current_version": ticket.version},
            )
        if ticket.status == Status.CLOSED:
            raise AppError(422, "invalid_transition", "Closed requests cannot receive replies")
        now = max(security.utcnow(), ticket.updated_at)
        old_status = ticket.status
        ticket.version += 1
        ticket.updated_at = now
        if old_status == Status.RESOLVED:
            ticket.status = Status.OPEN
            ticket.resolved_at = None
            ticket.closed_at = None
        message = Message(
            organization_id=org,
            ticket_id=ticket.id,
            author_customer_access_id=access.id,
            author_name=auth.user.full_name,
            visibility=Visibility.PUBLIC,
            body=data.body,
            ticket_version=ticket.version,
            created_at=now,
        )
        db.add(message)
        if old_status == Status.RESOLVED:
            db.add(
                AuditEvent(
                    organization_id=org,
                    ticket_id=ticket.id,
                    actor_customer_access_id=access.id,
                    actor_name=auth.user.full_name,
                    action="ticket.status_changed",
                    changes={"from": old_status, "to": Status.OPEN},
                    ticket_version=ticket.version,
                    sequence=0,
                    created_at=now,
                )
            )
        db.flush()
        db.add(
            AuditEvent(
                organization_id=org,
                ticket_id=ticket.id,
                message_id=message.id,
                actor_customer_access_id=access.id,
                actor_name=auth.user.full_name,
                action="message.public_posted",
                changes={"visibility": Visibility.PUBLIC, "message_id": str(message.id)},
                ticket_version=ticket.version,
                sequence=1 if old_status == Status.RESOLVED else 0,
                created_at=now,
            )
        )
        event_key = f"customer-reply:{message.id}"
        for user_id, email in db.execute(
            select(User.id, User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org,
                Membership.is_active.is_(True),
                User.is_active.is_(True),
            )
        ):
            db.add(
                Notification(
                    user_id=user_id,
                    organization_id=org,
                    ticket_id=ticket.id,
                    audience="staff",
                    event_key=event_key,
                    title=f"Customer replied to {ticket.title}"[:160],
                    created_at=now,
                )
            )
            add_email(
                db,
                f"{event_key}:{user_id}",
                "email",
                email,
                f"Customer replied: {ticket.title}",
                "A customer replied to a support request. Sign in to view the conversation.",
                org,
            )
        db.flush()
        return ticket_view(ticket)


def staff_reply_notifications(
    db: Session, ticket_id: UUID, message_id: UUID, settings: Settings
) -> None:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        return
    access_row = db.execute(
        select(CustomerAccess, Contact)
        .join(
            Contact,
            (Contact.organization_id == CustomerAccess.organization_id)
            & (Contact.id == CustomerAccess.contact_id),
        )
        .where(
            CustomerAccess.organization_id == ticket.organization_id,
            CustomerAccess.contact_id == ticket.requester_contact_id,
            CustomerAccess.is_active.is_(True),
            Contact.is_active.is_(True),
        )
    ).first()
    if access_row is None:
        return
    access, _contact = access_row
    user = db.get(User, access.user_id)
    if user is None or not user.is_active or user.email_verified_at is None:
        return
    key = f"staff-reply:{message_id}"
    db.add(
        Notification(
            user_id=user.id,
            organization_id=ticket.organization_id,
            ticket_id=ticket.id,
            audience="customer",
            event_key=key,
            title=f"Support replied to {ticket.title}"[:160],
        )
    )
    add_email(
        db,
        key,
        "email",
        user.email,
        f"Support replied: {ticket.title}",
        "A support agent replied to your request. Sign in to view: "
        f"{settings.public_app_url}/customer",
        ticket.organization_id,
    )


def integration_reply_notifications(db: Session, ticket: Ticket, message: Message, now) -> None:
    """Notify the active support team about a public message received by integration."""
    event_key = f"integration-reply:{message.id}"
    recipients = db.execute(
        select(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == ticket.organization_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    for user_id, email in recipients:
        db.add(
            Notification(
                user_id=user_id,
                organization_id=ticket.organization_id,
                ticket_id=ticket.id,
                audience="staff",
                event_key=event_key,
                title=f"Customer replied to {ticket.title}"[:160],
                created_at=now,
            )
        )
        add_email(
            db,
            f"{event_key}:{user_id}",
            "email",
            email,
            f"Customer replied: {ticket.title}",
            "A customer replied to a support request. Sign in to view the conversation.",
            ticket.organization_id,
        )


def integration_ticket_notifications(db: Session, ticket: Ticket, now, settings: Settings) -> None:
    """Notify active staff of a newly committed ticket received from an integration."""
    event_key = f"integration-ticket:{ticket.id}"
    recipients = db.execute(
        select(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == ticket.organization_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    for user_id, email in recipients:
        db.add(
            Notification(
                user_id=user_id,
                organization_id=ticket.organization_id,
                ticket_id=ticket.id,
                audience="staff",
                event_key=event_key,
                title=f"New ticket received: {ticket.title}"[:160],
                created_at=now,
            )
        )
        add_email(
            db,
            f"{event_key}:{user_id}",
            "email",
            email,
            f"New support ticket: {ticket.title}",
            "A customer request was received. Sign in to view and respond.",
            ticket.organization_id,
        )


def notifications(db: Session, auth: AuthContext, unread_only: bool):
    org_ids = [row[1].id for row in active_access(db, auth)]
    if not org_ids:
        return []
    query = select(Notification).where(
        Notification.user_id == auth.user.id,
        Notification.organization_id.in_(org_ids),
        Notification.audience == "customer",
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(db.scalars(query.order_by(Notification.created_at.desc()).limit(100)))


def mark_notification_read(db: Session, auth: AuthContext, notification_id: UUID) -> None:
    with transaction(db):
        org_ids = [row[1].id for row in active_access(db, auth)]
        row = db.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == auth.user.id,
                Notification.organization_id.in_(org_ids),
                Notification.audience == "customer",
            )
            .with_for_update()
        )
        if row is None:
            raise AppError(404, "not_found", "Notification not found")
        row.read_at = row.read_at or security.utcnow()


def staff_notifications(db: Session, auth: AuthContext, organization_id: UUID, unread_only: bool):
    organization_context(db, auth.user.id, organization_id)
    query = select(Notification).where(
        Notification.user_id == auth.user.id,
        Notification.organization_id == organization_id,
        Notification.audience == "staff",
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(db.scalars(query.order_by(Notification.created_at.desc()).limit(100)))


def mark_staff_notification_read(
    db: Session, auth: AuthContext, organization_id: UUID, notification_id: UUID
) -> None:
    with transaction(db):
        organization_context(db, auth.user.id, organization_id, shared=True)
        row = db.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == auth.user.id,
                Notification.organization_id == organization_id,
                Notification.audience == "staff",
            )
            .with_for_update()
        )
        if row is None:
            raise AppError(404, "not_found", "Notification not found")
        row.read_at = row.read_at or security.utcnow()
