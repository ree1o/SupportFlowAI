from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.customers.models import BackgroundJob, Notification, OutboxEvent
from app.errors import AppError
from app.identity.models import Membership, Role, User
from app.identity.security import utcnow
from app.identity.service import AuthContext, organization_context, transaction
from app.sla.models import SlaPolicy, SlaPolicyVersion, TicketSla
from app.sla.schemas import (
    AgentWorkload,
    OperationsReport,
    SlaPolicyView,
    SlaPolicyWrite,
    TicketSlaView,
)
from app.tickets.models import Status, Ticket

PRIORITIES = ("low", "normal", "high", "urgent")
OBLIGATIONS = ("response", "resolution")


def _targets(data: SlaPolicyWrite) -> dict[str, int]:
    return {
        f"{priority}_{kind}_seconds": getattr(data, f"{priority}_{kind}_seconds")
        for priority in PRIORITIES
        for kind in OBLIGATIONS
    }


def _manager(db: Session, auth: AuthContext, org: UUID, write: bool = False) -> Membership:
    member = organization_context(db, auth.user.id, org, shared=write)[1]
    if write and member.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise AppError(403, "forbidden", "Organization management permission required")
    return member


def get_policy(db: Session, auth: AuthContext, org: UUID) -> SlaPolicyView | None:
    _manager(db, auth, org)
    policy = db.scalar(select(SlaPolicy).where(SlaPolicy.organization_id == org))
    if policy is None:
        return None
    version = db.scalar(
        select(SlaPolicyVersion).where(
            SlaPolicyVersion.organization_id == org,
            SlaPolicyVersion.policy_id == policy.id,
            SlaPolicyVersion.version == policy.current_version,
        )
    )
    return SlaPolicyView(
        id=policy.id,
        organization_id=org,
        name=policy.name,
        active=policy.active,
        current_version=policy.current_version,
        targets=version.targets if version else {},
        updated_at=policy.updated_at,
    )


def save_policy(db: Session, auth: AuthContext, org: UUID, data: SlaPolicyWrite) -> SlaPolicyView:
    with transaction(db, "An SLA policy already exists or was changed concurrently"):
        actor = _manager(db, auth, org, write=True)
        policy = db.scalar(
            select(SlaPolicy)
            .where(SlaPolicy.organization_id == org)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        targets = _targets(data)
        now = utcnow()
        if policy is None:
            if data.expected_version not in (None, 0):
                raise AppError(
                    409, "version_conflict", "SLA policy changed; reload before retrying"
                )
            policy = SlaPolicy(
                organization_id=org,
                name=data.name,
                active=data.active,
                current_version=1,
                created_at=now,
                updated_at=now,
            )
            db.add(policy)
            db.flush()
            db.add(
                SlaPolicyVersion(
                    organization_id=org,
                    policy_id=policy.id,
                    version=1,
                    targets=targets,
                    changed_by_membership_id=actor.id,
                    created_at=now,
                )
            )
        else:
            if data.expected_version != policy.current_version:
                raise AppError(
                    409,
                    "version_conflict",
                    "SLA policy changed; reload before retrying",
                    {"current_version": policy.current_version},
                )
            version = db.scalar(
                select(SlaPolicyVersion).where(
                    SlaPolicyVersion.organization_id == org,
                    SlaPolicyVersion.policy_id == policy.id,
                    SlaPolicyVersion.version == policy.current_version,
                )
            )
            changed = (
                policy.name != data.name
                or policy.active != data.active
                or version is None
                or version.targets != targets
            )
            if changed:
                policy.name = data.name
                policy.active = data.active
                policy.updated_at = now
                policy.current_version += 1
                db.add(
                    SlaPolicyVersion(
                        organization_id=org,
                        policy_id=policy.id,
                        version=policy.current_version,
                        targets=targets,
                        changed_by_membership_id=actor.id,
                        created_at=now,
                    )
                )
        db.flush()
        result = get_policy(db, auth, org)
        assert result is not None
    return result


def snapshot_ticket(db: Session, ticket: Ticket, now: datetime) -> TicketSla | None:
    policy = db.scalar(
        select(SlaPolicy).where(
            SlaPolicy.organization_id == ticket.organization_id, SlaPolicy.active.is_(True)
        )
    )
    if policy is None:
        return None
    version = db.scalar(
        select(SlaPolicyVersion).where(
            SlaPolicyVersion.organization_id == ticket.organization_id,
            SlaPolicyVersion.policy_id == policy.id,
            SlaPolicyVersion.version == policy.current_version,
        )
    )
    if version is None:
        return None
    response = int(version.targets[f"{ticket.priority}_response_seconds"])
    resolution = int(version.targets[f"{ticket.priority}_resolution_seconds"])
    record = TicketSla(
        organization_id=ticket.organization_id,
        ticket_id=ticket.id,
        policy_id=policy.id,
        policy_version=policy.current_version,
        snapshot_priority=ticket.priority,
        response_target_seconds=response,
        resolution_target_seconds=resolution,
        response_due_at=now + timedelta(seconds=response),
        resolution_due_at=now + timedelta(seconds=resolution),
        created_at=now,
    )
    db.add(record)
    return record


def _status(
    due: datetime, completed: datetime | None, breached: datetime | None, now: datetime
) -> str:
    if completed is not None:
        return "met" if completed <= due else "breached"
    if breached is not None or now > due:
        return "breached"
    if due - now <= timedelta(hours=1):
        return "approaching"
    return "on_track"


def ticket_sla(
    db: Session, org: UUID, ticket: Ticket, now: datetime | None = None
) -> TicketSlaView | None:
    record = db.scalar(
        select(TicketSla).where(TicketSla.organization_id == org, TicketSla.ticket_id == ticket.id)
    )
    return present_sla(record, ticket, now)


def present_sla(
    record: TicketSla | None, ticket: Ticket, now: datetime | None = None
) -> TicketSlaView | None:
    if record is None:
        return None
    now = now or utcnow()
    return TicketSlaView(
        policy_version=record.policy_version,
        snapshot_priority=record.snapshot_priority,
        response_due_at=record.response_due_at,
        resolution_due_at=record.resolution_due_at,
        response_target_seconds=record.response_target_seconds,
        resolution_target_seconds=record.resolution_target_seconds,
        response_status=_status(
            record.response_due_at, ticket.first_response_at, record.response_breached_at, now
        ),
        resolution_status=_status(
            record.resolution_due_at, ticket.first_resolved_at, record.resolution_breached_at, now
        ),
    )


def _enqueue_breach(db: Session, sla: TicketSla, ticket: Ticket, kind: str, at: datetime) -> None:
    event_key = f"sla-{kind}:{ticket.id}"
    members = list(
        db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.organization_id == ticket.organization_id,
                Membership.is_active.is_(True),
                User.is_active.is_(True),
                (
                    Membership.role.in_([Role.OWNER, Role.ADMINISTRATOR])
                    | (
                        Membership.id == ticket.assigned_membership_id
                        if ticket.assigned_membership_id
                        else False
                    )
                ),
            )
        )
    )
    for _, user in members:
        db.add(
            Notification(
                user_id=user.id,
                organization_id=ticket.organization_id,
                ticket_id=ticket.id,
                audience="staff",
                event_key=event_key,
                title=f"SLA {kind} deadline breached for ticket {str(ticket.id)[:8]}",
                created_at=at,
            )
        )
        if user.email_verified_at is not None:
            db.add(
                OutboxEvent(
                    organization_id=ticket.organization_id,
                    event_key=f"sla-email:{kind}:{ticket.id}:{user.id}",
                    event_type="sla_breach_email",
                    payload={
                        "to": user.email,
                        "subject": f"SLA {kind} deadline breached",
                        "body": f"Ticket {ticket.id} has breached its {kind} SLA deadline.",
                    },
                    created_at=at,
                    available_at=at,
                )
            )
            db.flush()
            event = db.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_key == f"sla-email:{kind}:{ticket.id}:{user.id}"
                )
            )
            if event:
                db.add(BackgroundJob(outbox_event_id=event.id, available_at=at))


def record_completion(db: Session, ticket: Ticket, kind: str, completed_at: datetime) -> None:
    sla = db.scalar(
        select(TicketSla)
        .where(
            TicketSla.organization_id == ticket.organization_id, TicketSla.ticket_id == ticket.id
        )
        .with_for_update()
    )
    if sla is None:
        return
    due = sla.response_due_at if kind == "response" else sla.resolution_due_at
    field = "response_breached_at" if kind == "response" else "resolution_breached_at"
    if completed_at > due and getattr(sla, field) is None:
        setattr(sla, field, due)
        setattr(sla, f"{kind}_escalated_at", completed_at)
        _enqueue_breach(db, sla, ticket, kind, completed_at)


def scan_deadlines(db: Session, now: datetime | None = None, batch_size: int = 100) -> int:
    """Mark due obligations once; safe for concurrent/repeated scans."""
    now = now or utcnow()
    changed = 0
    with db.begin_nested():
        rows = db.execute(
            select(TicketSla, Ticket)
            .join(
                Ticket,
                and_(
                    Ticket.organization_id == TicketSla.organization_id,
                    Ticket.id == TicketSla.ticket_id,
                ),
            )
            .where(
                (
                    (Ticket.first_response_at.is_(None))
                    & (TicketSla.response_due_at < now)
                    & TicketSla.response_breached_at.is_(None)
                )
                | (
                    (Ticket.first_resolved_at.is_(None))
                    & (TicketSla.resolution_due_at < now)
                    & TicketSla.resolution_breached_at.is_(None)
                )
            )
            .order_by(TicketSla.response_due_at, TicketSla.id)
            # Match ticket mutation lock order (Ticket, then TicketSla). This
            # serializes completion against a scan without a lock-order deadlock.
            .with_for_update(of=Ticket, skip_locked=True)
            .limit(batch_size)
        ).all()
        for sla, ticket in rows:
            for kind in OBLIGATIONS:
                completed = (
                    ticket.first_response_at if kind == "response" else ticket.first_resolved_at
                )
                due = getattr(sla, f"{kind}_due_at")
                field = f"{kind}_breached_at"
                if completed is None and now > due and getattr(sla, field) is None:
                    setattr(sla, field, due)
                    setattr(sla, f"{kind}_escalated_at", now)
                    _enqueue_breach(db, sla, ticket, kind, due)
                    changed += 1
        db.flush()
    return changed


def report(
    db: Session,
    auth: AuthContext,
    org: UUID,
    start: datetime,
    end: datetime,
    now: datetime | None = None,
) -> OperationsReport:
    _manager(db, auth, org)
    now = now or utcnow()
    cohort = and_(
        Ticket.organization_id == org, Ticket.created_at >= start, Ticket.created_at < end
    )
    total = db.scalar(select(func.count()).select_from(Ticket).where(cohort)) or 0
    status_rows = list(
        db.execute(select(Ticket.status, func.count()).where(cohort).group_by(Ticket.status))
    )
    priority_rows = db.execute(
        select(Ticket.priority, func.count()).where(cohort).group_by(Ticket.priority)
    )
    averages = db.execute(
        select(
            func.avg(func.extract("epoch", Ticket.first_response_at - Ticket.created_at)),
            func.avg(func.extract("epoch", Ticket.first_resolved_at - Ticket.created_at)),
        ).where(cohort)
    ).one()
    sla_scope = and_(
        Ticket.organization_id == TicketSla.organization_id, Ticket.id == TicketSla.ticket_id
    )
    response_due_scope = or_(Ticket.first_response_at.is_not(None), TicketSla.response_due_at < now)
    resolution_due_scope = or_(
        Ticket.first_resolved_at.is_not(None), TicketSla.resolution_due_at < now
    )
    sla_metrics = db.execute(
        select(
            func.count().filter(response_due_scope),
            func.count().filter(
                response_due_scope & (Ticket.first_response_at <= TicketSla.response_due_at)
            ),
            func.count().filter(resolution_due_scope),
            func.count().filter(
                resolution_due_scope & (Ticket.first_resolved_at <= TicketSla.resolution_due_at)
            ),
        )
        .select_from(TicketSla)
        .join(Ticket, sla_scope)
        .where(cohort)
    ).one()
    response_rate = float(sla_metrics[1] / sla_metrics[0]) if sla_metrics[0] else None
    resolution_rate = float(sla_metrics[3] / sla_metrics[2]) if sla_metrics[2] else None
    overdue = (
        db.scalar(
            select(func.count())
            .select_from(TicketSla)
            .join(
                Ticket,
                and_(
                    Ticket.organization_id == TicketSla.organization_id,
                    Ticket.id == TicketSla.ticket_id,
                ),
            )
            .where(
                Ticket.organization_id == org,
                (
                    (Ticket.first_response_at.is_(None) & (TicketSla.response_due_at < now))
                    | (Ticket.first_resolved_at.is_(None) & (TicketSla.resolution_due_at < now))
                ),
            )
        )
        or 0
    )
    workload = db.execute(
        select(Membership.id, User.full_name, func.count(Ticket.id))
        .join(User, User.id == Membership.user_id)
        .outerjoin(
            Ticket,
            and_(
                Ticket.organization_id == Membership.organization_id,
                Ticket.assigned_membership_id == Membership.id,
                Ticket.status.in_([Status.OPEN, Status.PENDING]),
            ),
        )
        .where(
            Membership.organization_id == org,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
        .group_by(Membership.id, User.full_name)
        .order_by(User.full_name)
    ).all()
    return OperationsReport(
        period_start=start,
        period_end=end,
        as_of=now,
        tickets_created=total,
        status_counts={str(k): int(v) for k, v in status_rows},
        priority_counts={str(k): int(v) for k, v in priority_rows},
        unresolved_tickets=int(
            db.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(cohort, Ticket.status.in_([Status.OPEN, Status.PENDING]))
            )
            or 0
        ),
        average_first_response_seconds=float(averages[0]) if averages[0] is not None else None,
        average_first_resolution_seconds=float(averages[1]) if averages[1] is not None else None,
        response_sla_compliance_rate=response_rate,
        resolution_sla_compliance_rate=resolution_rate,
        currently_overdue_tickets=int(overdue),
        workload=[
            AgentWorkload(membership_id=row[0], agent_name=row[1], open_tickets=row[2])
            for row in workload
        ],
    )
