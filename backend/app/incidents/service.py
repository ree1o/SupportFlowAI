from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.identity.models import Membership, Role, User
from app.identity.security import utcnow
from app.identity.service import AuthContext, organization_context, transaction
from app.incidents.correlation import DETECTOR_VERSION, TicketText, candidate_groups
from app.incidents.models import (
    Incident,
    IncidentEvent,
    IncidentTicket,
    RadarProposal,
    RadarProposalTicket,
    RadarRun,
    RadarSuppression,
)
from app.incidents.schemas import (
    IncidentCreate,
    IncidentDetail,
    IncidentEventView,
    IncidentNote,
    IncidentPage,
    IncidentTicketView,
    IncidentUpdate,
    IncidentView,
    ProposalConfirm,
    ProposalPage,
    ProposalReject,
    ProposalReviewResult,
    ProposalView,
    RadarRunPage,
    RadarRunView,
    TicketCandidate,
)
from app.tickets.models import Category, Contact, Message, Status, Ticket, Visibility

MAX_RUN_ATTEMPTS = 5
RUN_LEASE = timedelta(minutes=10)


def _membership(db: Session, auth: AuthContext, org: UUID, *, write: bool = False) -> Membership:
    return organization_context(db, auth.user.id, org, shared=write)[1]


def _manager(member: Membership) -> None:
    if member.role not in {Role.OWNER, Role.ADMINISTRATOR}:
        raise AppError(403, "forbidden", "Incident management permission required")


def _member_target(db: Session, org: UUID, member_id: UUID | None) -> None:
    if member_id is None:
        return
    member = db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.organization_id == org,
            Membership.id == member_id,
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if member is None:
        raise AppError(404, "not_found", "Active organization member not found")


def _incident(db: Session, org: UUID, incident_id: UUID, lock: bool = False) -> Incident:
    query = select(Incident).where(Incident.organization_id == org, Incident.id == incident_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise AppError(404, "not_found", "Incident not found")
    return row


def _actor_name(db: Session, member: Membership) -> str:
    return db.scalar(select(User.full_name).where(User.id == member.user_id)) or "Staff member"


def _event(
    db: Session,
    incident: Incident,
    member: Membership,
    action: str,
    name: str,
    changes: dict[str, Any] | None = None,
    note: str | None = None,
    sequence: int = 0,
) -> None:
    db.add(
        IncidentEvent(
            organization_id=incident.organization_id,
            incident_id=incident.id,
            actor_membership_id=member.id,
            actor_name=name[:100],
            action=action,
            note=note,
            changes=changes or {},
            incident_version=incident.version,
            sequence=sequence,
            created_at=utcnow(),
        )
    )


def _incident_view(db: Session, incident: Incident) -> IncidentView:
    owner_name = (
        db.scalar(
            select(User.full_name)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == incident.organization_id,
                Membership.id == incident.owner_membership_id,
            )
        )
        if incident.owner_membership_id
        else None
    )
    tickets_count = (
        db.scalar(
            select(func.count())
            .select_from(IncidentTicket)
            .where(
                IncidentTicket.organization_id == incident.organization_id,
                IncidentTicket.incident_id == incident.id,
            )
        )
        or 0
    )
    return IncidentView(
        id=incident.id,
        organization_id=incident.organization_id,
        title=incident.title,
        description=incident.description,
        status=incident.status,
        severity=incident.severity,
        owner_membership_id=incident.owner_membership_id,
        owner_name=owner_name,
        created_by_membership_id=incident.created_by_membership_id,
        version=incident.version,
        ticket_count=tickets_count,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        resolved_at=incident.resolved_at,
    )


def _ticket_candidates(db: Session, org: UUID, ids: list[UUID]) -> list[TicketCandidate]:
    if not ids:
        return []
    rows = db.execute(
        select(Ticket, Contact.name, Category.name)
        .join(
            Contact,
            (Contact.organization_id == Ticket.organization_id)
            & (Contact.id == Ticket.requester_contact_id),
        )
        .outerjoin(
            Category,
            (Category.organization_id == Ticket.organization_id)
            & (Category.id == Ticket.category_id),
        )
        .where(Ticket.organization_id == org, Ticket.id.in_(ids))
        .order_by(Ticket.created_at, Ticket.id)
    )
    return [
        TicketCandidate(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            priority=ticket.priority,
            category_id=ticket.category_id,
            category_name=category_name,
            requester_name=requester_name,
            created_at=ticket.created_at,
        )
        for ticket, requester_name, category_name in rows
    ]


def _incident_detail(db: Session, incident: Incident) -> IncidentDetail:
    base = _incident_view(db, incident)
    ticket_rows = db.execute(
        select(Ticket, Contact.name, Category.name, IncidentTicket.linked_at)
        .join(
            IncidentTicket,
            (IncidentTicket.organization_id == Ticket.organization_id)
            & (IncidentTicket.ticket_id == Ticket.id),
        )
        .join(
            Contact,
            (Contact.organization_id == Ticket.organization_id)
            & (Contact.id == Ticket.requester_contact_id),
        )
        .outerjoin(
            Category,
            (Category.organization_id == Ticket.organization_id)
            & (Category.id == Ticket.category_id),
        )
        .where(
            IncidentTicket.organization_id == incident.organization_id,
            IncidentTicket.incident_id == incident.id,
        )
        .order_by(IncidentTicket.linked_at, Ticket.id)
    )
    tickets = [
        IncidentTicketView(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            priority=ticket.priority,
            category_id=ticket.category_id,
            category_name=category_name,
            requester_name=requester_name,
            created_at=ticket.created_at,
            linked_at=linked_at,
        )
        for ticket, requester_name, category_name, linked_at in ticket_rows
    ]
    events = db.execute(
        select(IncidentEvent)
        .where(
            IncidentEvent.organization_id == incident.organization_id,
            IncidentEvent.incident_id == incident.id,
        )
        .order_by(IncidentEvent.incident_version, IncidentEvent.sequence)
    ).scalars()
    return IncidentDetail(
        **base.model_dump(),
        tickets=tickets,
        events=[IncidentEventView.model_validate(event) for event in events],
    )


def create_incident(
    db: Session, auth: AuthContext, org: UUID, data: IncidentCreate
) -> IncidentDetail:
    with transaction(db, "Incident conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        _member_target(db, org, data.owner_membership_id)
        linked = list(
            db.scalars(
                select(Ticket.id)
                .where(Ticket.organization_id == org, Ticket.id.in_(data.ticket_ids))
                .with_for_update(read=True)
            )
        )
        if len(linked) != len(data.ticket_ids):
            raise AppError(404, "not_found", "One or more tickets were not found")
        now = utcnow()
        incident = Incident(
            organization_id=org,
            title=data.title.strip(),
            description=data.description.strip(),
            status="investigating",
            severity=data.severity,
            owner_membership_id=data.owner_membership_id,
            created_by_membership_id=actor.id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(incident)
        db.flush()
        name = auth.user.full_name
        _event(db, incident, actor, "incident.created", name, {"source": "manual"}, sequence=0)
        for sequence, ticket_id in enumerate(sorted(linked, key=str), start=1):
            db.add(
                IncidentTicket(
                    organization_id=org,
                    incident_id=incident.id,
                    ticket_id=ticket_id,
                    linked_by_membership_id=actor.id,
                    linked_at=now,
                )
            )
            _event(
                db,
                incident,
                actor,
                "incident.ticket_linked",
                name,
                {"ticket_id": str(ticket_id)},
                sequence=sequence,
            )
        db.flush()
        result = _incident_detail(db, incident)
    return result


def list_incidents(
    db: Session, auth: AuthContext, org: UUID, status: str | None, page: int, size: int
) -> IncidentPage:
    _membership(db, auth, org)
    query = select(Incident).where(Incident.organization_id == org)
    if status:
        query = query.where(Incident.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Incident.updated_at.desc(), Incident.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    return IncidentPage(
        items=[_incident_view(db, incident) for incident in rows],
        total=total,
        page=page,
        page_size=size,
    )


def get_incident(db: Session, auth: AuthContext, org: UUID, incident_id: UUID) -> IncidentDetail:
    _membership(db, auth, org)
    return _incident_detail(db, _incident(db, org, incident_id))


def update_incident(
    db: Session,
    auth: AuthContext,
    org: UUID,
    incident_id: UUID,
    data: IncidentUpdate,
) -> IncidentDetail:
    with transaction(db, "Incident conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        incident = _incident(db, org, incident_id, lock=True)
        if incident.version != data.expected_version:
            raise AppError(
                409,
                "version_conflict",
                "Incident changed; reload before retrying",
                {"current_version": incident.version},
            )
        changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        effective = {
            key: value for key, value in changes.items() if getattr(incident, key) != value
        }
        if "status" in effective:
            allowed = {
                "investigating": {"monitoring", "resolved"},
                "monitoring": {"investigating", "resolved"},
                "resolved": {"investigating"},
            }
            if effective["status"] not in allowed[incident.status]:
                raise AppError(
                    422, "invalid_transition", "Incident status transition is not permitted"
                )
        if "owner_membership_id" in effective:
            _member_target(db, org, effective["owner_membership_id"])
        if effective:
            old = {key: getattr(incident, key) for key in effective}
            incident.version += 1
            incident.updated_at = utcnow()
            for key, value in effective.items():
                setattr(incident, key, value)
            if effective.get("status") == "resolved":
                incident.resolved_at = incident.updated_at
            elif effective.get("status") == "investigating":
                incident.resolved_at = None
            _event(
                db,
                incident,
                actor,
                "incident.updated",
                auth.user.full_name,
                {
                    key: {
                        "from": str(old[key]) if old[key] else None,
                        "to": str(effective[key]) if effective[key] else None,
                    }
                    for key in effective
                },
            )
            db.flush()
        result = _incident_detail(db, incident)
    return result


def add_incident_note(
    db: Session,
    auth: AuthContext,
    org: UUID,
    incident_id: UUID,
    data: IncidentNote,
) -> IncidentDetail:
    with transaction(db, "Incident history conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        incident = _incident(db, org, incident_id, lock=True)
        if incident.version != data.expected_version:
            raise AppError(
                409,
                "version_conflict",
                "Incident changed; reload before retrying",
                {"current_version": incident.version},
            )
        incident.version += 1
        incident.updated_at = utcnow()
        _event(
            db, incident, actor, "incident.note_added", auth.user.full_name, note=data.body.strip()
        )
        db.flush()
        result = _incident_detail(db, incident)
    return result


def link_ticket(
    db: Session,
    auth: AuthContext,
    org: UUID,
    incident_id: UUID,
    ticket_id: UUID,
    expected_version: int,
) -> IncidentDetail:
    with transaction(db, "Incident ticket link already exists"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        incident = _incident(db, org, incident_id, lock=True)
        if incident.version != expected_version:
            raise AppError(
                409,
                "version_conflict",
                "Incident changed; reload before retrying",
                {"current_version": incident.version},
            )
        ticket = db.scalar(
            select(Ticket.id)
            .where(Ticket.organization_id == org, Ticket.id == ticket_id)
            .with_for_update(read=True)
        )
        if ticket is None:
            raise AppError(404, "not_found", "Ticket not found")
        if incident.status == "resolved":
            raise AppError(422, "invalid_transition", "Reopen the incident before linking tickets")
        if (
            db.scalar(
                select(IncidentTicket.ticket_id).where(
                    IncidentTicket.organization_id == org,
                    IncidentTicket.incident_id == incident_id,
                    IncidentTicket.ticket_id == ticket_id,
                )
            )
            is not None
        ):
            raise AppError(
                409, "ticket_already_linked", "Ticket is already linked to this incident"
            )
        incident.version += 1
        incident.updated_at = utcnow()
        db.add(
            IncidentTicket(
                organization_id=org,
                incident_id=incident.id,
                ticket_id=ticket_id,
                linked_by_membership_id=actor.id,
                linked_at=incident.updated_at,
            )
        )
        _event(
            db,
            incident,
            actor,
            "incident.ticket_linked",
            auth.user.full_name,
            {"ticket_id": str(ticket_id)},
        )
        db.flush()
        result = _incident_detail(db, incident)
    return result


def unlink_ticket(
    db: Session,
    auth: AuthContext,
    org: UUID,
    incident_id: UUID,
    ticket_id: UUID,
    expected_version: int,
) -> IncidentDetail:
    with transaction(db, "Incident ticket link conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        incident = _incident(db, org, incident_id, lock=True)
        if incident.version != expected_version:
            raise AppError(
                409,
                "version_conflict",
                "Incident changed; reload before retrying",
                {"current_version": incident.version},
            )
        link = db.scalar(
            select(IncidentTicket)
            .where(
                IncidentTicket.organization_id == org,
                IncidentTicket.incident_id == incident_id,
                IncidentTicket.ticket_id == ticket_id,
            )
            .with_for_update()
        )
        if link is None:
            raise AppError(404, "not_found", "Incident ticket link not found")
        count_links = (
            db.scalar(
                select(func.count())
                .select_from(IncidentTicket)
                .where(
                    IncidentTicket.organization_id == org,
                    IncidentTicket.incident_id == incident_id,
                )
            )
            or 0
        )
        if count_links <= 1:
            raise AppError(
                422, "last_ticket_required", "An incident must retain at least one linked ticket"
            )
        db.delete(link)
        incident.version += 1
        incident.updated_at = utcnow()
        _event(
            db,
            incident,
            actor,
            "incident.ticket_unlinked",
            auth.user.full_name,
            {"ticket_id": str(ticket_id)},
        )
        db.flush()
        result = _incident_detail(db, incident)
    return result


def _candidate_rows(db: Session, org: UUID, settings: Settings) -> list[TicketText]:
    cutoff = utcnow() - timedelta(hours=settings.radar_lookback_hours)
    rows = db.execute(
        select(Ticket, Message.body, Category.id, Category.name, Contact.name)
        .join(
            Contact,
            (Contact.organization_id == Ticket.organization_id)
            & (Contact.id == Ticket.requester_contact_id),
        )
        .outerjoin(
            Category,
            (Category.organization_id == Ticket.organization_id)
            & (Category.id == Ticket.category_id),
        )
        .outerjoin(
            Message,
            (Message.organization_id == Ticket.organization_id)
            & (Message.ticket_id == Ticket.id)
            & (Message.ticket_version == 1)
            & (Message.visibility == Visibility.PUBLIC),
        )
        .where(
            Ticket.organization_id == org,
            Ticket.created_at >= cutoff,
            Ticket.status != Status.CLOSED,
        )
        .order_by(Ticket.created_at.desc(), Ticket.id)
        .limit(settings.radar_max_tickets)
    )
    return [
        TicketText(
            id=ticket.id,
            organization_id=org,
            requester_contact_id=ticket.requester_contact_id,
            title=ticket.title,
            body=body or "",
            status=ticket.status,
            priority=ticket.priority,
            category_id=category_id,
            category_name=category_name,
            requester_name=requester_name,
            created_at=ticket.created_at,
        )
        for ticket, body, category_id, category_name, requester_name in rows
    ]


def _run_view(run: RadarRun) -> RadarRunView:
    return RadarRunView(
        id=run.id,
        organization_id=run.organization_id,
        status=run.status,
        detector_version=run.detector_version,
        attempts=run.attempts,
        proposal_count=run.proposal_count,
        failure_type=run.failure_type,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def queue_run(db: Session, auth: AuthContext, org: UUID) -> RadarRunView:
    with transaction(db, "Radar run could not be queued"):
        actor = _membership(db, auth, org, write=True)
        run = RadarRun(
            organization_id=org,
            triggered_by_membership_id=actor.id,
            status="pending",
            detector_version=DETECTOR_VERSION,
            attempts=0,
            proposal_count=0,
            created_at=utcnow(),
            available_at=utcnow(),
        )
        db.add(run)
        db.flush()
        result = _run_view(run)
    return result


def list_runs(db: Session, auth: AuthContext, org: UUID, page: int, size: int) -> RadarRunPage:
    _membership(db, auth, org)
    query = select(RadarRun).where(RadarRun.organization_id == org)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(RadarRun.created_at.desc(), RadarRun.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return RadarRunPage(
        items=[_run_view(run) for run in rows], total=total, page=page, page_size=size
    )


def retry_run(db: Session, auth: AuthContext, org: UUID, run_id: UUID) -> RadarRunView:
    with transaction(db, "Radar run changed concurrently"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        run = db.scalar(
            select(RadarRun)
            .where(RadarRun.organization_id == org, RadarRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise AppError(404, "not_found", "Radar run not found")
        if run.status != "failed":
            raise AppError(409, "run_not_failed", "Only failed Radar runs can be retried")
        run.status = "pending"
        run.attempts = 0
        run.failure_type = None
        run.finished_at = None
        run.available_at = utcnow()
        result = _run_view(run)
    return result


def list_proposals(
    db: Session,
    auth: AuthContext,
    org: UUID,
    status: str | None,
    page: int,
    size: int,
) -> ProposalPage:
    _membership(db, auth, org)
    query = select(RadarProposal).where(RadarProposal.organization_id == org)
    if status:
        query = query.where(RadarProposal.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        db.scalars(
            query.order_by(RadarProposal.created_at.desc(), RadarProposal.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    return ProposalPage(
        items=[_proposal_view(db, proposal) for proposal in rows],
        total=total,
        page=page,
        page_size=size,
    )


def _proposal(db: Session, org: UUID, proposal_id: UUID, lock: bool = False) -> RadarProposal:
    query = select(RadarProposal).where(
        RadarProposal.organization_id == org, RadarProposal.id == proposal_id
    )
    if lock:
        query = query.with_for_update()
    proposal = db.scalar(query.execution_options(populate_existing=True))
    if proposal is None:
        raise AppError(404, "not_found", "Radar proposal not found")
    return proposal


def _proposal_view(db: Session, proposal: RadarProposal) -> ProposalView:
    ids = list(
        db.scalars(
            select(RadarProposalTicket.ticket_id)
            .where(
                RadarProposalTicket.organization_id == proposal.organization_id,
                RadarProposalTicket.proposal_id == proposal.id,
            )
            .order_by(RadarProposalTicket.ticket_id)
        )
    )
    return ProposalView(
        id=proposal.id,
        organization_id=proposal.organization_id,
        run_id=proposal.run_id,
        title=proposal.title,
        summary=proposal.summary,
        score=float(proposal.score),
        detector_version=proposal.detector_version,
        evidence=proposal.evidence,
        status=proposal.status,
        ticket_ids=ids,
        tickets=_ticket_candidates(db, proposal.organization_id, ids),
        reviewer_membership_id=proposal.reviewer_membership_id,
        reviewed_at=proposal.reviewed_at,
        incident_id=proposal.incident_id,
        rejection_reason=proposal.rejection_reason,
        created_at=proposal.created_at,
    )


def get_proposal(db: Session, auth: AuthContext, org: UUID, proposal_id: UUID) -> ProposalView:
    _membership(db, auth, org)
    return _proposal_view(db, _proposal(db, org, proposal_id))


def confirm_proposal(
    db: Session,
    auth: AuthContext,
    org: UUID,
    proposal_id: UUID,
    data: ProposalConfirm,
) -> ProposalReviewResult:
    with transaction(db, "Proposal or incident confirmation conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        proposal = _proposal(db, org, proposal_id, lock=True)
        if proposal.status != "pending":
            raise AppError(409, "proposal_reviewed", "Radar proposal has already been reviewed")
        proposed_ids = set(
            db.scalars(
                select(RadarProposalTicket.ticket_id).where(
                    RadarProposalTicket.organization_id == org,
                    RadarProposalTicket.proposal_id == proposal.id,
                )
            )
        )
        if not set(data.ticket_ids) <= proposed_ids:
            raise AppError(
                422, "invalid_proposal_tickets", "Confirmed tickets must come from this proposal"
            )
        now = utcnow()
        incident = Incident(
            organization_id=org,
            title=(data.title or proposal.title).strip(),
            description=(
                data.description if data.description is not None else proposal.summary
            ).strip(),
            status="investigating",
            severity=data.severity,
            owner_membership_id=None,
            created_by_membership_id=actor.id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        db.add(incident)
        db.flush()
        _event(
            db,
            incident,
            actor,
            "incident.proposal_confirmed",
            auth.user.full_name,
            {
                "proposal_id": str(proposal.id),
                "ticket_ids": [str(value) for value in data.ticket_ids],
            },
        )
        for sequence, ticket_id in enumerate(sorted(data.ticket_ids, key=str), start=1):
            db.add(
                IncidentTicket(
                    organization_id=org,
                    incident_id=incident.id,
                    ticket_id=ticket_id,
                    linked_by_membership_id=actor.id,
                    linked_at=now,
                )
            )
            _event(
                db,
                incident,
                actor,
                "incident.ticket_linked",
                auth.user.full_name,
                {"ticket_id": str(ticket_id), "proposal_id": str(proposal.id)},
                sequence=sequence,
            )
        proposal.status = "confirmed"
        proposal.reviewer_membership_id = actor.id
        proposal.reviewed_at = now
        proposal.incident_id = incident.id
        db.add(
            RadarSuppression(
                organization_id=org,
                group_key=proposal.group_key,
                input_fingerprint=proposal.input_fingerprint,
                status="confirmed",
                proposal_id=proposal.id,
                incident_id=incident.id,
                created_at=now,
            )
        )
        db.flush()
        result = ProposalReviewResult(
            proposal=_proposal_view(db, proposal),
            incident=_incident_view(db, incident),
        )
    return result


def reject_proposal(
    db: Session,
    auth: AuthContext,
    org: UUID,
    proposal_id: UUID,
    data: ProposalReject,
) -> ProposalReviewResult:
    with transaction(db, "Proposal rejection conflicts with existing data"):
        actor = _membership(db, auth, org, write=True)
        _manager(actor)
        proposal = _proposal(db, org, proposal_id, lock=True)
        if proposal.status != "pending":
            raise AppError(409, "proposal_reviewed", "Radar proposal has already been reviewed")
        proposal.status = "rejected"
        proposal.reviewer_membership_id = actor.id
        proposal.reviewed_at = utcnow()
        proposal.rejection_reason = data.reason.strip() or None if data.reason else None
        db.add(
            RadarSuppression(
                organization_id=org,
                group_key=proposal.group_key,
                input_fingerprint=proposal.input_fingerprint,
                status="rejected",
                proposal_id=proposal.id,
                incident_id=None,
                created_at=proposal.reviewed_at,
            )
        )
        db.flush()
        result = ProposalReviewResult(proposal=_proposal_view(db, proposal), incident=None)
    return result


def process_detection_run(db: Session, run_id: UUID, settings: Settings) -> int:
    """Claim, compute and commit proposal rows with a durable retry lease."""
    with transaction(db, "Radar run could not be claimed"):
        run = db.scalar(select(RadarRun).where(RadarRun.id == run_id).with_for_update())
        if run is None or run.status == "completed" or run.status == "failed":
            return 0
        now = utcnow()
        if run.status == "running" and run.locked_until and run.locked_until > now:
            return 0
        if run.attempts >= MAX_RUN_ATTEMPTS:
            run.status = "failed"
            run.failure_type = run.failure_type or "RetryLimitExceeded"
            run.finished_at = now
            return 0
        run.status = "running"
        run.attempts += 1
        attempt = run.attempts
        run.started_at = now
        run.locked_until = now + RUN_LEASE
        run.failure_type = None
        org = run.organization_id

    try:
        tickets = _candidate_rows(db, org, settings)
        groups = candidate_groups(tickets, settings)
        with transaction(db, "Radar proposal conflicts with an existing proposal"):
            run = db.scalar(select(RadarRun).where(RadarRun.id == run_id).with_for_update())
            if run is None or run.status != "running" or run.attempts != attempt:
                return 0
            ticket_incidents: dict[UUID, set[UUID]] = {}
            all_ids = {ticket_id for group in groups for ticket_id in group.ticket_ids}
            if all_ids:
                for ticket_id, incident_id in db.execute(
                    select(IncidentTicket.ticket_id, IncidentTicket.incident_id).where(
                        IncidentTicket.organization_id == org,
                        IncidentTicket.ticket_id.in_(all_ids),
                    )
                ):
                    ticket_incidents.setdefault(ticket_id, set()).add(incident_id)
            suppressions = {
                (row.group_key, row.input_fingerprint)
                for row in db.scalars(
                    select(RadarSuppression).where(
                        RadarSuppression.organization_id == org,
                        RadarSuppression.group_key.in_(
                            [group.group_key for group in groups] or ["-"]
                        ),
                    )
                )
            }
            created = 0
            for group in groups:
                covered = [ticket_incidents.get(ticket_id, set()) for ticket_id in group.ticket_ids]
                common_incidents = set.intersection(*covered) if covered else set()
                if common_incidents or (group.group_key, group.input_fingerprint) in suppressions:
                    continue
                statement = (
                    insert(RadarProposal)
                    .values(
                        organization_id=org,
                        run_id=run_id,
                        group_key=group.group_key,
                        input_fingerprint=group.input_fingerprint,
                        title=group.title,
                        summary=group.summary,
                        score=group.score,
                        detector_version=DETECTOR_VERSION,
                        evidence=group.evidence,
                        status="pending",
                        created_at=utcnow(),
                    )
                    .on_conflict_do_nothing(constraint="uq_radar_proposal_identity")
                    .returning(RadarProposal.id)
                )
                proposal_id = db.scalar(statement)
                if proposal_id is None:
                    continue
                db.add_all(
                    [
                        RadarProposalTicket(
                            organization_id=org,
                            proposal_id=proposal_id,
                            ticket_id=ticket_id,
                        )
                        for ticket_id in group.ticket_ids
                    ]
                )
                created += 1
            run.status = "completed"
            run.proposal_count = created
            run.finished_at = utcnow()
            run.locked_until = None
            run.failure_type = None
            db.flush()
        return created
    except Exception as exc:
        with transaction(db, "Radar run failure could not be recorded"):
            run = db.scalar(select(RadarRun).where(RadarRun.id == run_id).with_for_update())
            if run is not None and run.status == "running" and run.attempts == attempt:
                run.failure_type = type(exc).__name__[:120]
                run.locked_until = None
                if run.attempts >= MAX_RUN_ATTEMPTS:
                    run.status = "failed"
                    run.finished_at = utcnow()
                else:
                    run.status = "pending"
                    run.available_at = utcnow() + timedelta(seconds=min(900, 2**attempt * 5))
        raise


def load_pending_run_ids(db: Session, limit: int = 50) -> list[UUID]:
    now = utcnow()
    rows = list(
        db.scalars(
            select(RadarRun)
            .where(
                (RadarRun.status == "pending") & (RadarRun.available_at <= now)
                | ((RadarRun.status == "running") & (RadarRun.locked_until < now))
            )
            .order_by(RadarRun.available_at, RadarRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    ids = []
    for run in rows:
        if run.status == "running":
            run.status = "pending"
            run.locked_until = None
        ids.append(run.id)
    db.commit()
    return ids


def schedule_org_runs(db: Session, settings: Settings, limit: int = 25) -> list[UUID]:
    now = utcnow()
    cutoff = now - timedelta(hours=settings.radar_lookback_hours)
    org_ids = list(
        db.scalars(
            select(Ticket.organization_id)
            .where(Ticket.created_at >= cutoff, Ticket.status != Status.CLOSED)
            .distinct()
            .order_by(Ticket.organization_id)
            .limit(limit)
        )
    )
    created: list[UUID] = []
    with db.begin_nested():
        for org in org_ids:
            recent = db.scalar(
                select(RadarRun.id)
                .where(
                    RadarRun.organization_id == org,
                    RadarRun.created_at >= now - timedelta(minutes=15),
                    RadarRun.status.in_(["pending", "running", "completed"]),
                )
                .limit(1)
            )
            if recent:
                continue
            run = RadarRun(
                organization_id=org,
                status="pending",
                detector_version=DETECTOR_VERSION,
                attempts=0,
                proposal_count=0,
                created_at=now,
                available_at=now,
            )
            db.add(run)
            db.flush()
            created.append(run.id)
    db.commit()
    return created
