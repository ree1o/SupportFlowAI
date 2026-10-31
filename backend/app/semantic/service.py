from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.customers.models import BackgroundJob, OutboxEvent
from app.errors import AppError
from app.identity.models import Membership, Organization, Role
from app.identity.security import utcnow
from app.identity.service import AuthContext, organization_context, transaction
from app.semantic.embedding import content_digest, embedding_text, get_local_embedder
from app.semantic.models import MODEL_VERSION, TicketEmbedding
from app.semantic.schemas import IndexingBatch, RelatedTicket, RelatedTickets
from app.tenancy import set_tenant_context
from app.tickets.models import Category, Message, Ticket, Visibility
from app.tickets.service import manager


def source_text(db: Session, org: UUID, ticket_id: UUID) -> tuple[str, int, str] | None:
    row = db.execute(
        select(Ticket.title, Message.body, Ticket.version)
        .join(
            Message,
            and_(
                Message.organization_id == Ticket.organization_id,
                Message.ticket_id == Ticket.id,
                Message.ticket_version == 1,
                Message.visibility == Visibility.PUBLIC,
            ),
        )
        .where(Ticket.organization_id == org, Ticket.id == ticket_id)
    ).first()
    if row is None:
        return None
    text = embedding_text(row.title, row.body)
    return text, row.version, content_digest(row.title, row.body)


def enqueue_ticket(db: Session, org: UUID, ticket_id: UUID) -> bool:
    ticket = db.scalar(
        select(Ticket)
        .where(Ticket.organization_id == org, Ticket.id == ticket_id)
        .with_for_update()
    )
    if ticket is None:
        return False
    source = source_text(db, org, ticket_id)
    if source is None:
        return False
    _, version, digest = source
    current = db.scalar(
        select(TicketEmbedding)
        .where(
            TicketEmbedding.organization_id == org,
            TicketEmbedding.ticket_id == ticket_id,
            TicketEmbedding.model_version == MODEL_VERSION,
            TicketEmbedding.content_digest == digest,
        )
        .with_for_update()
    )
    if current is not None:
        if not current.is_current:
            current.is_current = True
        return False
    db.execute(
        update(TicketEmbedding)
        .where(
            TicketEmbedding.organization_id == org,
            TicketEmbedding.ticket_id == ticket_id,
            TicketEmbedding.model_version == MODEL_VERSION,
            TicketEmbedding.is_current.is_(True),
        )
        .values(is_current=False, updated_at=utcnow())
    )
    db.add(
        TicketEmbedding(
            organization_id=org,
            ticket_id=ticket_id,
            model_version=MODEL_VERSION,
            content_digest=digest,
            source_version=version,
            status="pending",
            is_current=True,
            available_at=utcnow(),
        )
    )
    db.flush()
    embedding = db.scalar(
        select(TicketEmbedding).where(
            TicketEmbedding.organization_id == org,
            TicketEmbedding.ticket_id == ticket_id,
            TicketEmbedding.model_version == MODEL_VERSION,
            TicketEmbedding.content_digest == digest,
        )
    )
    if embedding is None:
        raise RuntimeError("Embedding queue row was not persisted")
    event = OutboxEvent(
        organization_id=org,
        event_key=f"semantic-index:{embedding.id}",
        event_type="semantic.index",
        payload={"embedding_id": str(embedding.id)},
    )
    db.add(event)
    db.flush()
    db.add(BackgroundJob(outbox_event_id=event.id))
    return True


def _staff_context(db: Session, auth: AuthContext, org: UUID) -> Membership:
    member = organization_context(db, auth.user.id, org)[1]
    return member


def related(
    db: Session, auth: AuthContext, org: UUID, ticket_id: UUID, limit: int, settings: Settings
) -> RelatedTickets:
    actor = _staff_context(db, auth, org)
    source = db.scalar(select(Ticket).where(Ticket.organization_id == org, Ticket.id == ticket_id))
    if source is None:
        raise AppError(404, "not_found", "Ticket not found")
    index = db.scalar(
        select(TicketEmbedding).where(
            TicketEmbedding.organization_id == org,
            TicketEmbedding.ticket_id == ticket_id,
            TicketEmbedding.model_version == MODEL_VERSION,
            TicketEmbedding.is_current.is_(True),
        )
    )
    status = "missing" if index is None else index.status
    if index is None or index.status != "ready" or index.embedding is None:
        return RelatedTickets(
            model_version=MODEL_VERSION,
            index_status=status,
            indexed_at=index.indexed_at if index else None,
            items=[],
        )
    from app.tickets.models import Status

    distance = TicketEmbedding.embedding.cosine_distance(index.embedding)
    query = (
        select(Ticket, Category.name, Message.body, distance.label("distance"))
        .select_from(TicketEmbedding)
        .join(
            Ticket,
            and_(
                Ticket.organization_id == TicketEmbedding.organization_id,
                Ticket.id == TicketEmbedding.ticket_id,
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
            Message,
            and_(
                Message.organization_id == Ticket.organization_id,
                Message.ticket_id == Ticket.id,
                Message.ticket_version == 1,
                Message.visibility == Visibility.PUBLIC,
            ),
        )
        .where(
            TicketEmbedding.organization_id == org,
            TicketEmbedding.model_version == MODEL_VERSION,
            TicketEmbedding.status == "ready",
            TicketEmbedding.is_current.is_(True),
            TicketEmbedding.ticket_id != ticket_id,
            Ticket.status != Status.CLOSED,
        )
    )
    if not manager(actor):
        query = query.where(
            or_(Ticket.assigned_membership_id.is_(None), Ticket.assigned_membership_id == actor.id)
        )
    rows = db.execute(query.order_by(distance, Ticket.id).limit(limit)).all()
    results = []
    for ticket, category, body, raw_distance in rows:
        score = max(0.0, min(1.0, 1.0 - float(raw_distance)))
        if score < settings.semantic_min_similarity:
            continue
        preview = " ".join(embedding_text("", body or "").split())[:220]
        results.append(
            RelatedTicket(
                id=ticket.id,
                title=ticket.title,
                status=ticket.status,
                priority=ticket.priority,
                category_name=category,
                preview=preview,
                similarity=round(score, 4),
            )
        )
    return RelatedTickets(
        model_version=MODEL_VERSION,
        index_status=status,
        indexed_at=index.indexed_at,
        items=results,
    )


def backfill(db: Session, auth: AuthContext, org: UUID, settings: Settings) -> IndexingBatch:
    with transaction(db, "Unable to schedule semantic indexing"):
        member = organization_context(db, auth.user.id, org, shared=True)[1]
        if member.role not in {Role.OWNER, Role.ADMINISTRATOR}:
            raise AppError(403, "forbidden", "Organization management permission required")
        failed_rows = list(
            db.scalars(
                select(TicketEmbedding)
                .where(
                    TicketEmbedding.organization_id == org,
                    TicketEmbedding.model_version == MODEL_VERSION,
                    TicketEmbedding.is_current.is_(True),
                    TicketEmbedding.status == "failed",
                )
                .order_by(TicketEmbedding.created_at, TicketEmbedding.id)
                .with_for_update(skip_locked=True)
                .limit(settings.semantic_index_batch_size)
            )
        )
        for record in failed_rows:
            event = db.scalar(
                select(OutboxEvent).where(OutboxEvent.event_key == f"semantic-index:{record.id}")
            )
            if event is None:
                continue
            job = db.scalar(
                select(BackgroundJob)
                .where(BackgroundJob.outbox_event_id == event.id)
                .with_for_update()
            )
            if job is None:
                continue
            now = utcnow()
            record.status = "pending"
            record.attempts = 0
            record.available_at = now
            record.last_error = None
            job.status = "pending"
            job.attempts = 0
            job.available_at = now
            job.locked_until = None
            job.completed_at = None
            job.last_error = None
            event.processed_at = None
            event.available_at = now
        remaining_limit = settings.semantic_index_batch_size - len(failed_rows)
        rows = db.execute(
            select(Ticket.id, Ticket.title, Message.body)
            .join(
                Message,
                and_(
                    Message.organization_id == Ticket.organization_id,
                    Message.ticket_id == Ticket.id,
                    Message.ticket_version == 1,
                    Message.visibility == Visibility.PUBLIC,
                ),
            )
            .outerjoin(
                TicketEmbedding,
                and_(
                    TicketEmbedding.organization_id == Ticket.organization_id,
                    TicketEmbedding.ticket_id == Ticket.id,
                    TicketEmbedding.model_version == MODEL_VERSION,
                    TicketEmbedding.is_current.is_(True),
                ),
            )
            .where(Ticket.organization_id == org, TicketEmbedding.id.is_(None))
            .order_by(Ticket.created_at, Ticket.id)
            .limit(max(1, remaining_limit + 1))
        ).all()
        queued = len(failed_rows)
        for ticket_id, _, _ in rows[: max(0, remaining_limit)]:
            queued += int(enqueue_ticket(db, org, ticket_id))
        remaining = (len(rows) > max(0, remaining_limit)) or len(
            failed_rows
        ) >= settings.semantic_index_batch_size
    return IndexingBatch(queued=queued, remaining=remaining)


def schedule_missing(db: Session, settings: Settings, limit: int = 50) -> int:
    if limit < 1:
        return 0
    count = 0
    organization_ids = list(db.scalars(select(Organization.id).order_by(Organization.id)))
    for org in organization_ids:
        set_tenant_context(db, org)
        rows = db.scalars(
            select(Ticket.id)
            .join(
                Message,
                and_(
                    Message.organization_id == Ticket.organization_id,
                    Message.ticket_id == Ticket.id,
                    Message.ticket_version == 1,
                    Message.visibility == Visibility.PUBLIC,
                ),
            )
            .outerjoin(
                TicketEmbedding,
                and_(
                    TicketEmbedding.organization_id == Ticket.organization_id,
                    TicketEmbedding.ticket_id == Ticket.id,
                    TicketEmbedding.model_version == MODEL_VERSION,
                    TicketEmbedding.is_current.is_(True),
                ),
            )
            .where(Ticket.organization_id == org, TicketEmbedding.id.is_(None))
            .order_by(Ticket.created_at, Ticket.id)
            .limit(limit - count)
        )
        for ticket_id in rows:
            count += int(enqueue_ticket(db, org, ticket_id))
        if count >= limit:
            break
    db.commit()
    return count


def process_outbox_embedding(db: Session, embedding_id: UUID, settings: Settings) -> str:
    now = utcnow()
    record = db.scalar(
        select(TicketEmbedding)
        .where(
            TicketEmbedding.id == embedding_id,
        )
        .with_for_update()
    )
    if record is None or record.status == "ready" or not record.is_current:
        db.rollback()
        return "unchanged"
    org, ticket_id, digest = record.organization_id, record.ticket_id, record.content_digest
    record.status = "processing"
    record.attempts += 1
    record.locked_until = now + timedelta(minutes=5)
    record.last_error = None
    db.commit()

    set_tenant_context(db, org)
    source = source_text(db, org, ticket_id)
    if source is None or source[2] != digest:
        record = db.scalar(
            select(TicketEmbedding).where(TicketEmbedding.id == record.id).with_for_update()
        )
        if record is not None:
            record.status = "stale"
            record.is_current = False
            record.locked_until = None
            db.commit()
        return "stale"
    vector = get_local_embedder(settings.semantic_model_cache_dir).embed_document(source[0])

    current = source_text(db, org, ticket_id)
    record = db.scalar(
        select(TicketEmbedding).where(TicketEmbedding.id == record.id).with_for_update()
    )
    if record is None or not record.is_current or current is None or current[2] != digest:
        if record is not None:
            record.status = "stale"
            record.is_current = False
            record.locked_until = None
        db.commit()
        return "stale"
    record.embedding = vector
    record.status = "ready"
    record.indexed_at = utcnow()
    record.updated_at = utcnow()
    record.locked_until = None
    record.last_error = None
    db.commit()
    return "ready"


def fail_outbox_embedding(
    db: Session, embedding_id: UUID, attempts: int, error_type: str, settings: Settings
) -> None:
    record = db.scalar(
        select(TicketEmbedding).where(TicketEmbedding.id == embedding_id).with_for_update()
    )
    if record is None or record.status == "ready" or not record.is_current:
        db.rollback()
        return
    record.last_error = error_type[:120]
    record.locked_until = None
    if attempts >= settings.semantic_index_max_attempts:
        record.status = "failed"
    else:
        record.status = "pending"
        record.available_at = utcnow() + timedelta(seconds=min(1800, 15 * 2**attempts))
    db.commit()
