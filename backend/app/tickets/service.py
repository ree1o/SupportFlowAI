from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.identity.models import Membership, Role, User
from app.identity.security import utcnow
from app.identity.service import AuthContext, organization_context, transaction
from app.tickets.models import (
    AuditEvent,
    Category,
    Contact,
    Message,
    Priority,
    Status,
    Ticket,
    Visibility,
)
from app.tickets.schemas import (
    ActivityPage,
    AuditView,
    CategoryCreate,
    CategoryUpdate,
    CategoryView,
    ContactCreate,
    ContactPage,
    ContactUpdate,
    ContactView,
    MessageCreate,
    MessagePage,
    PublicConversation,
    PublicMessage,
    StaffMessage,
    TicketCreate,
    TicketPage,
    TicketRecord,
    TicketSort,
    TicketUpdate,
    TicketView,
)

TRANSITIONS: dict[Status, list[Status]] = {
    Status.OPEN: [Status.PENDING, Status.RESOLVED],
    Status.PENDING: [Status.OPEN, Status.RESOLVED],
    Status.RESOLVED: [Status.OPEN, Status.CLOSED],
    Status.CLOSED: [Status.OPEN],
}


def context(db: Session, auth: AuthContext, org: UUID, write: bool = False) -> Membership:
    return organization_context(db, auth.user.id, org, shared=write)[1]


def manager(member: Membership) -> bool:
    return member.role in {Role.OWNER, Role.ADMINISTRATOR}


def require_manager(member: Membership) -> None:
    if not manager(member):
        raise AppError(403, "forbidden", "Organization management permission required")


def contact(db: Session, org: UUID, id: UUID, *, write: bool = False) -> Contact:
    query = select(Contact).where(Contact.organization_id == org, Contact.id == id)
    if write:
        query = query.with_for_update()
    record = db.scalar(query.execution_options(populate_existing=True))
    if record is None:
        raise AppError(404, "not_found", "Contact not found")
    return record


def create_contact(db: Session, auth: AuthContext, org: UUID, data: ContactCreate) -> ContactView:
    with transaction(db, "Contact data conflicts with an existing record"):
        context(db, auth, org, True)
        now = utcnow()
        record = Contact(organization_id=org, **data.model_dump(), created_at=now, updated_at=now)
        db.add(record)
        db.flush()
        result = ContactView.model_validate(record)
    return result


def contacts(
    db: Session,
    auth: AuthContext,
    org: UUID,
    q: str,
    include_inactive: bool,
    page: int,
    size: int,
) -> ContactPage:
    context(db, auth, org)
    query = select(Contact).where(Contact.organization_id == org)
    if not include_inactive:
        query = query.where(Contact.is_active.is_(True))
    if q.strip():
        query = query.where(
            or_(
                Contact.name.icontains(q.strip(), autoescape=True),
                Contact.email.icontains(q.strip(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Contact.name, Contact.id).offset((page - 1) * size).limit(size)
    )
    return ContactPage(
        items=[ContactView.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


def get_contact(db: Session, auth: AuthContext, org: UUID, id: UUID) -> ContactView:
    context(db, auth, org)
    return ContactView.model_validate(contact(db, org, id))


def update_contact(
    db: Session, auth: AuthContext, org: UUID, id: UUID, data: ContactUpdate
) -> ContactView:
    with transaction(db, "Contact data conflicts with an existing record"):
        actor = context(db, auth, org, True)
        if "is_active" in data.model_fields_set:
            require_manager(actor)
        record = contact(db, org, id, write=True)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        db.flush()
        result = ContactView.model_validate(record)
    return result


def categories(
    db: Session, auth: AuthContext, org: UUID, include_inactive: bool
) -> list[CategoryView]:
    context(db, auth, org)
    query = select(Category).where(Category.organization_id == org)
    if not include_inactive:
        query = query.where(Category.is_active.is_(True))
    return [
        CategoryView.model_validate(row)
        for row in db.scalars(query.order_by(Category.normalized_name, Category.id))
    ]


def create_category(
    db: Session, auth: AuthContext, org: UUID, data: CategoryCreate
) -> CategoryView:
    with transaction(db, "A category with this normalized name already exists"):
        require_manager(context(db, auth, org, True))
        now = utcnow()
        record = Category(organization_id=org, name=data.name, created_at=now, updated_at=now)
        db.add(record)
        db.flush()
        result = CategoryView.model_validate(record)
    return result


def update_category(
    db: Session, auth: AuthContext, org: UUID, id: UUID, data: CategoryUpdate
) -> CategoryView:
    with transaction(db, "A category with this normalized name already exists"):
        require_manager(context(db, auth, org, True))
        record = db.scalar(
            select(Category)
            .where(Category.organization_id == org, Category.id == id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise AppError(404, "not_found", "Category not found")
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(record, key, value)
        record.updated_at = utcnow()
        db.flush()
        result = CategoryView.model_validate(record)
    return result


def workable(ticket: Ticket, actor: Membership) -> bool:
    return manager(actor) or ticket.assigned_membership_id in {None, actor.id}


def transitions(ticket: Ticket, actor: Membership) -> list[Status]:
    if not workable(ticket, actor) or (ticket.status == Status.CLOSED and not manager(actor)):
        return []
    return TRANSITIONS[Status(ticket.status)]


def ticket_query(org: UUID):
    # Joined projections avoid a per-ticket contact/category/assignee query loop.
    return (
        select(Ticket, Contact, Category, Membership, User)
        .join(
            Contact,
            and_(
                Contact.organization_id == Ticket.organization_id,
                Contact.id == Ticket.requester_contact_id,
            ),
        )
        .outerjoin(
            Category,
            and_(
                Category.organization_id == Ticket.organization_id,
                Category.id == Ticket.category_id,
            ),
        )
        .outerjoin(
            Membership,
            and_(
                Membership.organization_id == Ticket.organization_id,
                Membership.id == Ticket.assigned_membership_id,
            ),
        )
        .outerjoin(User, User.id == Membership.user_id)
        .where(Ticket.organization_id == org)
    )


def view(row: Any, actor: Membership) -> TicketView:
    ticket, requester, category, assignee, user = row
    can_work = workable(ticket, actor)
    return TicketView(
        **TicketRecord.model_validate(ticket).model_dump(),
        requester_name=requester.name,
        requester_email=requester.email,
        category_name=category.name if category else None,
        category_active=category.is_active if category else None,
        assignee_name=user.full_name if user else None,
        assignee_active=(assignee.is_active and user.is_active) if assignee and user else None,
        allowed_transitions=transitions(ticket, actor),
        can_update=can_work and ticket.status != Status.CLOSED,
        can_reply=can_work and ticket.status in {Status.OPEN, Status.PENDING},
        can_note=can_work and ticket.status != Status.CLOSED,
        can_assign_others=manager(actor),
    )


def get_ticket(db: Session, auth: AuthContext, org: UUID, id: UUID) -> TicketView:
    actor = context(db, auth, org)
    row = db.execute(ticket_query(org).where(Ticket.id == id)).first()
    if row is None:
        raise AppError(404, "not_found", "Ticket not found")
    return view(row, actor)


def tickets(
    db: Session,
    auth: AuthContext,
    org: UUID,
    q: str,
    status: Status | None,
    priority: Priority | None,
    category_id: UUID | None,
    assigned_to: UUID | None,
    unassigned: bool,
    requester_id: UUID | None,
    sort: TicketSort,
    page: int,
    size: int,
) -> TicketPage:
    actor = context(db, auth, org)
    query = ticket_query(org)
    if q.strip():
        query = query.where(
            or_(
                Ticket.title.icontains(q.strip(), autoescape=True),
                Contact.name.icontains(q.strip(), autoescape=True),
                Contact.email.icontains(q.strip(), autoescape=True),
            )
        )
    for column, value in [
        (Ticket.status, status),
        (Ticket.priority, priority),
        (Ticket.category_id, category_id),
        (Ticket.assigned_membership_id, assigned_to),
        (Ticket.requester_contact_id, requester_id),
    ]:
        if value is not None:
            query = query.where(column == value)
    if unassigned:
        query = query.where(Ticket.assigned_membership_id.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if sort == "priority_desc":
        rank = case(
            (Ticket.priority == Priority.URGENT, 4),
            (Ticket.priority == Priority.HIGH, 3),
            (Ticket.priority == Priority.NORMAL, 2),
            else_=1,
        )
        order = [rank.desc(), Ticket.created_at.desc(), Ticket.id.desc()]
    elif sort == "created_asc":
        order = [Ticket.created_at.asc(), Ticket.id.asc()]
    elif sort == "updated_desc":
        order = [Ticket.updated_at.desc(), Ticket.id.desc()]
    else:
        order = [Ticket.created_at.desc(), Ticket.id.desc()]
    rows = db.execute(query.order_by(*order).offset((page - 1) * size).limit(size))
    return TicketPage(
        items=[view(row, actor) for row in rows], total=total, page=page, page_size=size
    )


def active_contact(db: Session, org: UUID, id: UUID) -> None:
    record = db.scalar(
        select(Contact)
        .where(Contact.organization_id == org, Contact.id == id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise AppError(404, "not_found", "Requester contact not found")
    if not record.is_active:
        raise AppError(422, "invalid_reference", "Requester contact is archived")


def active_category(db: Session, org: UUID, id: UUID | None) -> None:
    if id is None:
        return
    record = db.scalar(
        select(Category)
        .where(Category.organization_id == org, Category.id == id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise AppError(404, "not_found", "Category not found")
    if not record.is_active:
        raise AppError(422, "invalid_reference", "Category is inactive")


def active_assignee(db: Session, org: UUID, id: UUID | None, actor: Membership) -> None:
    if not manager(actor) and id not in {None, actor.id}:
        raise AppError(
            403,
            "forbidden",
            "Agents may only claim unassigned work or release their own assignment",
        )
    if id is None:
        return
    record = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == org,
            Membership.id == id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if record is None:
        raise AppError(404, "not_found", "Active assignment target not found")


def audit(
    db: Session,
    ticket: Ticket,
    actor: Membership,
    actor_name: str,
    action: str,
    changes: dict[str, Any],
    now: datetime,
    sequence: int = 0,
    message_id: UUID | None = None,
) -> None:
    db.add(
        AuditEvent(
            organization_id=ticket.organization_id,
            ticket_id=ticket.id,
            actor_membership_id=actor.id,
            actor_name=actor_name,
            action=action,
            changes=changes,
            ticket_version=ticket.version,
            sequence=sequence,
            message_id=message_id,
            created_at=now,
        )
    )


def safe_id(value: Any) -> Any:
    return str(value) if isinstance(value, UUID) else value


def create_ticket(db: Session, auth: AuthContext, org: UUID, data: TicketCreate) -> TicketView:
    with transaction(db, "Ticket references conflict with existing data"):
        actor = context(db, auth, org, True)
        active_contact(db, org, data.requester_contact_id)
        active_category(db, org, data.category_id)
        active_assignee(db, org, data.assigned_membership_id, actor)
        now = utcnow()
        record = Ticket(
            organization_id=org,
            requester_contact_id=data.requester_contact_id,
            created_by_membership_id=actor.id,
            title=data.title,
            priority=data.priority,
            status=Status.OPEN,
            version=1,
            category_id=data.category_id,
            assigned_membership_id=data.assigned_membership_id,
            created_at=now,
            updated_at=now,
        )
        db.add(record)
        db.flush()
        message = Message(
            organization_id=org,
            ticket_id=record.id,
            author_membership_id=actor.id,
            author_name=auth.user.full_name,
            visibility=Visibility.PUBLIC,
            body=data.initial_message,
            ticket_version=1,
            created_at=now,
        )
        db.add(message)
        db.flush()
        audit(
            db,
            record,
            actor,
            auth.user.full_name,
            "ticket.created",
            {
                "requester_contact_id": str(record.requester_contact_id),
                "priority": record.priority,
                "status": record.status,
                "category_id": safe_id(record.category_id),
                "assigned_membership_id": safe_id(record.assigned_membership_id),
                "initial_message_id": str(message.id),
            },
            now,
        )
        db.flush()
        result = view(db.execute(ticket_query(org).where(Ticket.id == record.id)).one(), actor)
    return result


def mutable_ticket(db: Session, org: UUID, id: UUID, actor: Membership, expected: int) -> Ticket:
    record = db.scalar(
        select(Ticket)
        .where(Ticket.organization_id == org, Ticket.id == id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise AppError(404, "not_found", "Ticket not found")
    if not workable(record, actor):
        raise AppError(403, "forbidden", "This ticket is assigned to another agent")
    if record.version != expected:
        raise AppError(
            409,
            "version_conflict",
            "Ticket changed; reload before retrying",
            {"current_version": record.version},
        )
    return record


def update_ticket(
    db: Session, auth: AuthContext, org: UUID, id: UUID, data: TicketUpdate
) -> TicketView:
    with transaction(db, "Ticket references conflict with existing data"):
        actor = context(db, auth, org, True)
        record = mutable_ticket(db, org, id, actor, data.expected_version)
        fields = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        changes = {key: value for key, value in fields.items() if getattr(record, key) != value}
        if record.status == Status.CLOSED and (
            not manager(actor) or changes != {"status": Status.OPEN}
        ):
            raise AppError(
                422, "invalid_transition", "A manager must reopen a closed ticket before editing"
            )
        if "status" in changes and changes["status"] not in transitions(record, actor):
            raise AppError(422, "invalid_transition", "This status transition is not permitted")
        if "category_id" in changes:
            active_category(db, org, data.category_id)
        if "assigned_membership_id" in changes:
            active_assignee(db, org, data.assigned_membership_id, actor)
        if changes:
            now = max(utcnow(), record.updated_at)
            record.version += 1
            record.updated_at = now
            if "status" in changes:
                if changes["status"] == Status.RESOLVED:
                    record.resolved_at = now
                    record.first_resolved_at = record.first_resolved_at or now
                elif changes["status"] == Status.OPEN:
                    record.resolved_at = None
                    record.closed_at = None
                elif changes["status"] == Status.CLOSED:
                    record.closed_at = now
            actions = {
                "status": "status",
                "priority": "priority",
                "category_id": "category",
                "assigned_membership_id": "assignment",
            }
            for sequence, (key, value) in enumerate(changes.items()):
                old = getattr(record, key)
                setattr(record, key, value)
                audit(
                    db,
                    record,
                    actor,
                    auth.user.full_name,
                    f"ticket.{actions[key]}_changed",
                    {
                        "from": safe_id(old),
                        "to": safe_id(value),
                    },
                    now,
                    sequence,
                )
            db.flush()
        result = view(db.execute(ticket_query(org).where(Ticket.id == id)).one(), actor)
    return result


def post_message(
    db: Session,
    auth: AuthContext,
    org: UUID,
    id: UUID,
    data: MessageCreate,
    visibility: Visibility,
    settings: Settings,
) -> TicketView:
    with transaction(db, "Conversation data conflicts with existing history"):
        actor = context(db, auth, org, True)
        record = mutable_ticket(db, org, id, actor, data.expected_version)
        if record.status == Status.CLOSED or (
            visibility == Visibility.PUBLIC and record.status == Status.RESOLVED
        ):
            raise AppError(
                422, "invalid_transition", "Reopen the ticket before posting this message"
            )
        now = max(utcnow(), record.updated_at)
        record.version += 1
        record.updated_at = now
        if visibility == Visibility.PUBLIC:
            record.first_response_at = record.first_response_at or now
        message = Message(
            organization_id=org,
            ticket_id=id,
            author_membership_id=actor.id,
            author_name=auth.user.full_name,
            visibility=visibility,
            body=data.body,
            ticket_version=record.version,
            created_at=now,
        )
        db.add(message)
        db.flush()
        if visibility == Visibility.PUBLIC:
            from app.customers.service import staff_reply_notifications

            staff_reply_notifications(db, record.id, message.id, settings)
            db.flush()
        audit(
            db,
            record,
            actor,
            auth.user.full_name,
            "message.public_posted" if visibility == Visibility.PUBLIC else "message.note_added",
            {"visibility": visibility, "message_id": str(message.id)},
            now,
            message_id=message.id,
        )
        db.flush()
        result = view(db.execute(ticket_query(org).where(Ticket.id == id)).one(), actor)
    return result


def conversation(
    db: Session,
    auth: AuthContext,
    org: UUID,
    id: UUID,
    after: int,
    limit: int,
    public: bool = False,
) -> MessagePage | PublicConversation:
    get_ticket(db, auth, org, id)
    query = select(Message).where(
        Message.organization_id == org, Message.ticket_id == id, Message.ticket_version > after
    )
    if public:
        query = query.where(Message.visibility == Visibility.PUBLIC)
    rows = list(db.scalars(query.order_by(Message.ticket_version).limit(limit + 1)))
    cursor = rows[limit - 1].ticket_version if len(rows) > limit else None
    if public:
        return PublicConversation(
            items=[PublicMessage.model_validate(row) for row in rows[:limit]],
            next_after_version=cursor,
        )
    return MessagePage(
        items=[StaffMessage.model_validate(row) for row in rows[:limit]], next_after_version=cursor
    )


def activity(
    db: Session, auth: AuthContext, org: UUID, id: UUID, after: int, limit: int
) -> ActivityPage:
    get_ticket(db, auth, org, id)
    versions = list(
        db.scalars(
            select(AuditEvent.ticket_version)
            .where(
                AuditEvent.organization_id == org,
                AuditEvent.ticket_id == id,
                AuditEvent.ticket_version > after,
            )
            .distinct()
            .order_by(AuditEvent.ticket_version)
            .limit(limit + 1)
        )
    )
    next_version = versions[limit - 1] if len(versions) > limit else None
    rows = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == org,
            AuditEvent.ticket_id == id,
            AuditEvent.ticket_version.in_(versions[:limit]),
        )
        .order_by(AuditEvent.ticket_version, AuditEvent.sequence)
    )
    return ActivityPage(
        items=[
            AuditView(
                id=row.id,
                actor_membership_id=row.actor_membership_id,
                actor_name=row.actor_name,
                action=row.action,
                target_type="message" if row.message_id else "ticket",
                target_id=row.message_id or row.ticket_id,
                changes=row.changes,
                ticket_version=row.ticket_version,
                sequence=row.sequence,
                created_at=row.created_at,
            )
            for row in rows
        ],
        next_after_version=next_version,
    )
