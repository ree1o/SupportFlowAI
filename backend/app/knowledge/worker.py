"""Leased, idempotent knowledge indexing on the isolated AI Celery queue."""

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.customers.models import BackgroundJob, OutboxEvent
from app.identity.security import utcnow
from app.knowledge.embedding import get_knowledge_embedder
from app.knowledge.models import KnowledgeArticle, KnowledgeArticleVersion, KnowledgeChunk
from app.semantic.embedding import embedding_text
from app.tenancy import set_tenant_context

logger = logging.getLogger("supportflow.knowledge.worker")


def dispatch_due(db: Session, limit: int = 20) -> list[tuple[UUID, UUID, int, str]]:
    now = utcnow()
    rows = db.execute(
        select(BackgroundJob, OutboxEvent)
        .join(OutboxEvent, OutboxEvent.id == BackgroundJob.outbox_event_id)
        .where(
            OutboxEvent.event_type == "knowledge.index",
            OutboxEvent.processed_at.is_(None),
            BackgroundJob.available_at <= now,
            or_(
                BackgroundJob.status == "pending",
                (BackgroundJob.status == "processing") & (BackgroundJob.locked_until < now),
            ),
        )
        .order_by(BackgroundJob.available_at, BackgroundJob.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    result = []
    for job, event in rows:
        payload = event.payload
        if event.organization_id is None or not isinstance(payload, dict):
            job.status = "failed"
            job.last_error = "InvalidKnowledgeOutboxEvent"
            continue
        token = uuid4().hex
        job.status = "processing"
        job.attempts += 1
        job.locked_until = now + timedelta(minutes=10)
        job.lease_token = token
        result.append(
            (
                event.organization_id,
                UUID(str(payload["article_id"])),
                int(payload["revision"]),
                token,
            )
        )
    db.commit()
    return result


def release_dispatch(
    db: Session, organization_id: UUID, article_id: UUID, revision: int, lease_token: str
) -> None:
    event = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.organization_id == organization_id,
            OutboxEvent.event_type == "knowledge.index",
            OutboxEvent.payload["article_id"].as_string() == str(article_id),
            OutboxEvent.payload["revision"].as_integer() == revision,
        )
    )
    if event is None:
        db.rollback()
        return
    job = db.scalar(
        select(BackgroundJob).where(BackgroundJob.outbox_event_id == event.id).with_for_update()
    )
    if job is not None and job.lease_token == lease_token and job.status == "processing":
        job.status = "pending"
        job.lease_token = None
        job.locked_until = None
        job.last_error = "BrokerUnavailable"
        db.commit()
    else:
        db.rollback()


def process_index(
    db: Session,
    organization_id: UUID,
    article_id: UUID,
    revision: int,
    lease_token: str,
    settings: Settings,
) -> str:
    set_tenant_context(db, organization_id)
    event = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.organization_id == organization_id,
            OutboxEvent.event_type == "knowledge.index",
            OutboxEvent.payload["article_id"].as_string() == str(article_id),
            OutboxEvent.payload["revision"].as_integer() == revision,
        )
    )
    if event is None:
        db.rollback()
        return "event-missing"
    job = db.scalar(
        select(BackgroundJob).where(BackgroundJob.outbox_event_id == event.id).with_for_update()
    )
    if (
        job is None
        or job.status == "completed"
        or job.status != "processing"
        or job.lease_token != lease_token
    ):
        db.rollback()
        return "lease-stale"
    event_id = event.id
    article = db.scalar(
        select(KnowledgeArticle).where(
            KnowledgeArticle.organization_id == organization_id,
            KnowledgeArticle.id == article_id,
        )
    )
    version = db.scalar(
        select(KnowledgeArticleVersion).where(
            KnowledgeArticleVersion.organization_id == organization_id,
            KnowledgeArticleVersion.article_id == article_id,
            KnowledgeArticleVersion.revision == revision,
        )
    )
    chunks = list(
        db.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeChunk.article_id == article_id,
                KnowledgeChunk.revision == revision,
            )
            .order_by(KnowledgeChunk.chunk_index)
        )
    )
    if article is None or version is None or not chunks:
        job.status = "failed"
        job.last_error = "KnowledgeSourceMissing"
        job.locked_until = None
        job.lease_token = None
        event.processed_at = utcnow()
        db.commit()
        return "source-missing"
    if article.lifecycle != "published" or article.published_revision != revision:
        for chunk in chunks:
            chunk.status = "stale"
            chunk.embedding = None
        job.status = "completed"
        job.completed_at = utcnow()
        job.locked_until = None
        job.lease_token = None
        event.processed_at = utcnow()
        db.commit()
        return "stale"
    texts = [embedding_text("", chunk.content) for chunk in chunks]
    db.commit()  # No transaction or row lock remains open during local inference.

    try:
        vectors = get_knowledge_embedder(settings.semantic_model_cache_dir).embed_documents(texts)
        if len(vectors) != len(chunks):
            raise ValueError("Embedding provider returned an invalid vector count")
    except Exception as exc:
        _record_failure(db, organization_id, event_id, chunks, settings, type(exc).__name__)
        logger.warning(
            "knowledge_index_failed article_id=%s error_type=%s",
            str(article_id),
            type(exc).__name__,
        )
        return "retry-pending"

    set_tenant_context(db, organization_id)
    current = db.scalar(
        select(KnowledgeArticle).where(
            KnowledgeArticle.organization_id == organization_id,
            KnowledgeArticle.id == article_id,
        )
    )
    event = db.get(OutboxEvent, event_id)
    if event is None:
        db.rollback()
        return "event-missing"
    job = db.scalar(
        select(BackgroundJob).where(BackgroundJob.outbox_event_id == event_id).with_for_update()
    )
    if (
        current is None
        or current.lifecycle != "published"
        or current.published_revision != revision
        or job is None
        or job.lease_token != lease_token
    ):
        db.rollback()
        return "stale"
    now = utcnow()
    for chunk, vector in zip(chunks, vectors, strict=True):
        persisted = db.scalar(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.id == chunk.id,
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeChunk.article_id == article_id,
                KnowledgeChunk.revision == revision,
                KnowledgeChunk.content_digest == chunk.content_digest,
            )
            .with_for_update()
        )
        if persisted is not None:
            persisted.embedding = vector
            persisted.status = "ready"
            persisted.indexed_at = now
            persisted.locked_until = None
            persisted.last_error = None
    job.status = "completed"
    job.completed_at = now
    job.locked_until = None
    job.lease_token = None
    event.processed_at = now
    db.commit()
    return "ready"


def _record_failure(
    db: Session,
    organization_id: UUID,
    event_id: UUID,
    chunks: list[KnowledgeChunk],
    settings: Settings,
    error_type: str,
) -> None:
    set_tenant_context(db, organization_id)
    job = db.scalar(
        select(BackgroundJob).where(BackgroundJob.outbox_event_id == event_id).with_for_update()
    )
    if job is None or job.status == "completed":
        db.rollback()
        return
    now = utcnow()
    exhausted = job.attempts >= settings.knowledge_index_max_attempts
    job.status = "failed" if exhausted else "pending"
    job.last_error = error_type[:120]
    job.locked_until = None
    job.lease_token = None
    if not exhausted:
        job.available_at = now + timedelta(seconds=min(1800, 15 * 2 ** max(0, job.attempts - 1)))
    for chunk in chunks:
        current = db.scalar(
            select(KnowledgeChunk).where(KnowledgeChunk.id == chunk.id).with_for_update()
        )
        if current is not None and current.status != "ready":
            current.status = "failed" if exhausted else "pending"
            current.attempts = min(10, job.attempts)
            current.available_at = job.available_at
            current.locked_until = None
            current.last_error = error_type[:120]
    db.commit()
