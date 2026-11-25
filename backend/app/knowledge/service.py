import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.customers.models import BackgroundJob, OutboxEvent
from app.errors import AppError
from app.identity.models import Membership
from app.identity.service import AuthContext, organization_context, transaction
from app.knowledge.chunking import chunk_text
from app.knowledge.models import (
    KB_MODEL_VERSION,
    KnowledgeArticle,
    KnowledgeArticleVersion,
    KnowledgeChunk,
)
from app.knowledge.schemas import ArticleCreate, ArticleEdit, ArticlePage, ArticleView
from app.tickets.service import manager


def _actor(db: Session, auth: AuthContext, org: UUID, *, write: bool = False) -> Membership:
    return organization_context(db, auth.user.id, org, write=write)[1]


def _manager(actor: Membership) -> None:
    if not manager(actor):
        raise AppError(403, "forbidden", "Organization management permission required")


def _digest(title: str, content: str) -> str:
    normalized = f"{title.strip()}\n{content.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _index_state(db: Session, article: KnowledgeArticle) -> str:
    if article.lifecycle == "draft":
        return "not_published"
    rows = db.execute(
        select(KnowledgeChunk.status, func.count())
        .where(
            KnowledgeChunk.organization_id == article.organization_id,
            KnowledgeChunk.article_id == article.id,
            KnowledgeChunk.revision == article.published_revision,
            KnowledgeChunk.model_version == KB_MODEL_VERSION,
        )
        .group_by(KnowledgeChunk.status)
    ).all()
    counts = dict(rows)
    if not counts:
        return "pending"
    if counts.get("failed"):
        return "failed"
    if counts.get("processing") or counts.get("pending"):
        return "pending"
    if counts.get("stale"):
        return "stale"
    return "indexed" if counts.get("ready", 0) else "pending"


def _view(db: Session, article: KnowledgeArticle, actor: Membership) -> ArticleView:
    is_manager = manager(actor)
    revision = article.current_revision if is_manager else article.published_revision
    if revision is None:
        raise AppError(404, "not_found", "Knowledge article not found")
    version = db.scalar(
        select(KnowledgeArticleVersion).where(
            KnowledgeArticleVersion.organization_id == article.organization_id,
            KnowledgeArticleVersion.article_id == article.id,
            KnowledgeArticleVersion.revision == revision,
        )
    )
    if version is None:
        raise AppError(404, "not_found", "Knowledge article not found")
    return ArticleView(
        id=article.id,
        organization_id=article.organization_id,
        slug=article.slug,
        title=version.title,
        content=version.content,
        lifecycle=article.lifecycle,
        current_revision=article.current_revision,
        published_revision=article.published_revision,
        index_status=_index_state(db, article),
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


def list_articles(
    db: Session, auth: AuthContext, org: UUID, query: str, page: int, page_size: int
) -> ArticlePage:
    actor = _actor(db, auth, org)
    stmt = select(KnowledgeArticle).where(KnowledgeArticle.organization_id == org)
    if not manager(actor):
        stmt = stmt.where(KnowledgeArticle.lifecycle == "published")
    if query:
        visible_revision = (
            KnowledgeArticle.current_revision
            if manager(actor)
            else KnowledgeArticle.published_revision
        )
        matching_ids = select(KnowledgeArticleVersion.article_id).where(
            KnowledgeArticleVersion.organization_id == org,
            KnowledgeArticleVersion.revision == visible_revision,
            KnowledgeArticleVersion.title.ilike(f"%{query}%"),
        )
        stmt = stmt.where(KnowledgeArticle.id.in_(matching_ids))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    articles = list(
        db.scalars(
            stmt.order_by(KnowledgeArticle.updated_at.desc(), KnowledgeArticle.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return ArticlePage(
        items=[_view(db, article, actor) for article in articles],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_article(db: Session, auth: AuthContext, org: UUID, article_id: UUID) -> ArticleView:
    actor = _actor(db, auth, org)
    stmt = select(KnowledgeArticle).where(
        KnowledgeArticle.organization_id == org, KnowledgeArticle.id == article_id
    )
    if not manager(actor):
        stmt = stmt.where(KnowledgeArticle.lifecycle == "published")
    article = db.scalar(stmt)
    if article is None:
        raise AppError(404, "not_found", "Knowledge article not found")
    return _view(db, article, actor)


def create_article(db: Session, auth: AuthContext, org: UUID, data: ArticleCreate) -> ArticleView:
    with transaction(db, "An article with this slug already exists"):
        actor = _actor(db, auth, org, write=True)
        _manager(actor)
        now = datetime.now(UTC)
        article = KnowledgeArticle(
            organization_id=org,
            slug=data.slug,
            lifecycle="draft",
            current_revision=1,
            published_revision=None,
            created_by_membership_id=actor.id,
            created_at=now,
            updated_at=now,
        )
        db.add(article)
        db.flush()
        db.add(
            KnowledgeArticleVersion(
                organization_id=org,
                article_id=article.id,
                revision=1,
                title=data.title,
                content=data.content,
                content_digest=_digest(data.title, data.content),
                source_metadata={"format": "markdown"},
                created_by_membership_id=actor.id,
                created_at=now,
            )
        )
        db.flush()
        result = _view(db, article, actor)
    return result


def edit_article(
    db: Session, auth: AuthContext, org: UUID, article_id: UUID, data: ArticleEdit
) -> ArticleView:
    with transaction(db, "Article revision conflicts with existing data"):
        actor = _actor(db, auth, org, write=True)
        _manager(actor)
        article = db.scalar(
            select(KnowledgeArticle)
            .where(KnowledgeArticle.organization_id == org, KnowledgeArticle.id == article_id)
            .with_for_update()
        )
        if article is None:
            raise AppError(404, "not_found", "Knowledge article not found")
        if article.lifecycle == "archived":
            raise AppError(409, "article_archived", "Archived articles cannot be edited")
        if article.current_revision != data.expected_revision:
            raise AppError(409, "stale_revision", "Article changed; reload before editing")
        previous = db.scalar(
            select(KnowledgeArticleVersion).where(
                KnowledgeArticleVersion.organization_id == org,
                KnowledgeArticleVersion.article_id == article.id,
                KnowledgeArticleVersion.revision == article.current_revision,
            )
        )
        if previous is None:
            raise AppError(409, "revision_missing", "Article revision is unavailable")
        article.current_revision += 1
        article.updated_at = datetime.now(UTC)
        db.add(
            KnowledgeArticleVersion(
                organization_id=org,
                article_id=article.id,
                revision=article.current_revision,
                title=data.title,
                content=data.content,
                content_digest=_digest(data.title, data.content),
                source_metadata={"format": "markdown"},
                created_by_membership_id=actor.id,
                created_at=article.updated_at,
            )
        )
        db.flush()
        result = _view(db, article, actor)
    return result


def _index_version(
    db: Session, article: KnowledgeArticle, version: KnowledgeArticleVersion
) -> None:
    chunks = chunk_text(f"{version.title}\n\n{version.content}")
    if not chunks:
        raise AppError(422, "empty_content", "Published article has no indexable text")
    for index, body in enumerate(chunks):
        db.add(
            KnowledgeChunk(
                organization_id=article.organization_id,
                article_id=article.id,
                revision=version.revision,
                chunk_index=index,
                model_version=KB_MODEL_VERSION,
                content_digest=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                content=body,
                status="pending",
            )
        )
    db.flush()
    event_key = f"knowledge-index:{article.id}:{version.revision}:{KB_MODEL_VERSION}"
    event = OutboxEvent(
        organization_id=article.organization_id,
        event_key=event_key,
        event_type="knowledge.index",
        payload={"article_id": str(article.id), "revision": version.revision},
    )
    db.add(event)
    db.flush()
    db.add(BackgroundJob(outbox_event_id=event.id))


def publish_article(
    db: Session, auth: AuthContext, org: UUID, article_id: UUID, expected_revision: int
) -> ArticleView:
    with transaction(db, "Article publication conflicts with existing data"):
        actor = _actor(db, auth, org, write=True)
        _manager(actor)
        article = db.scalar(
            select(KnowledgeArticle)
            .where(KnowledgeArticle.organization_id == org, KnowledgeArticle.id == article_id)
            .with_for_update()
        )
        if article is None:
            raise AppError(404, "not_found", "Knowledge article not found")
        if article.lifecycle == "archived":
            raise AppError(409, "article_archived", "Archived articles cannot be published")
        if article.current_revision != expected_revision:
            raise AppError(409, "stale_revision", "Article changed; reload before publishing")
        version = db.scalar(
            select(KnowledgeArticleVersion).where(
                KnowledgeArticleVersion.organization_id == org,
                KnowledgeArticleVersion.article_id == article.id,
                KnowledgeArticleVersion.revision == expected_revision,
            )
        )
        if version is None:
            raise AppError(409, "revision_missing", "Article revision is unavailable")
        if article.published_revision != expected_revision:
            version.published_at = datetime.now(UTC)
            article.published_revision = expected_revision
            article.lifecycle = "published"
            article.updated_at = version.published_at
            _index_version(db, article, version)
        db.flush()
        result = _view(db, article, actor)
    return result


def archive_article(db: Session, auth: AuthContext, org: UUID, article_id: UUID) -> ArticleView:
    with transaction(db):
        actor = _actor(db, auth, org, write=True)
        _manager(actor)
        article = db.scalar(
            select(KnowledgeArticle)
            .where(KnowledgeArticle.organization_id == org, KnowledgeArticle.id == article_id)
            .with_for_update()
        )
        if article is None:
            raise AppError(404, "not_found", "Knowledge article not found")
        if article.lifecycle != "archived":
            article.lifecycle = "archived"
            article.archived_at = datetime.now(UTC)
            article.updated_at = article.archived_at
        return _view(db, article, actor)


def retry_article_index(db: Session, auth: AuthContext, org: UUID, article_id: UUID) -> ArticleView:
    with transaction(db):
        actor = _actor(db, auth, org, write=True)
        _manager(actor)
        article = db.scalar(
            select(KnowledgeArticle)
            .where(KnowledgeArticle.organization_id == org, KnowledgeArticle.id == article_id)
            .with_for_update()
        )
        if (
            article is None
            or article.lifecycle != "published"
            or article.published_revision is None
        ):
            raise AppError(404, "not_found", "Published knowledge article not found")
        chunks = list(
            db.scalars(
                select(KnowledgeChunk)
                .where(
                    KnowledgeChunk.organization_id == org,
                    KnowledgeChunk.article_id == article.id,
                    KnowledgeChunk.revision == article.published_revision,
                    KnowledgeChunk.model_version == KB_MODEL_VERSION,
                    KnowledgeChunk.status == "failed",
                )
                .with_for_update(skip_locked=True)
            )
        )
        event_key = f"knowledge-index:{article.id}:{article.published_revision}:{KB_MODEL_VERSION}"
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key == event_key))
        if event is None:
            raise AppError(409, "index_event_missing", "Index request is unavailable")
        job = db.scalar(
            select(BackgroundJob).where(BackgroundJob.outbox_event_id == event.id).with_for_update()
        )
        if job is None:
            raise AppError(409, "index_job_missing", "Index request is unavailable")
        if job.status == "failed":
            job.status = "pending"
            job.attempts = 0
            job.available_at = datetime.now(UTC)
            job.locked_until = None
            job.lease_token = None
            job.last_error = None
            event.processed_at = None
            for chunk in chunks:
                chunk.status = "pending"
                chunk.attempts = 0
                chunk.available_at = job.available_at
                chunk.last_error = None
    return get_article(db, auth, org, article_id)


def search_published(
    db: Session, org: UUID, vector: list[float], limit: int, minimum_relevance: float
) -> list[dict]:
    """Exact tenant-filtered vector ranking; content is returned only from published versions."""
    distance = KnowledgeChunk.embedding.cosine_distance(vector)
    rows = db.execute(
        select(
            KnowledgeArticle, KnowledgeArticleVersion, KnowledgeChunk, distance.label("distance")
        )
        .join(
            KnowledgeChunk,
            and_(
                KnowledgeChunk.organization_id == KnowledgeArticle.organization_id,
                KnowledgeChunk.article_id == KnowledgeArticle.id,
                KnowledgeChunk.revision == KnowledgeArticle.published_revision,
            ),
        )
        .join(
            KnowledgeArticleVersion,
            and_(
                KnowledgeArticleVersion.organization_id == KnowledgeChunk.organization_id,
                KnowledgeArticleVersion.article_id == KnowledgeChunk.article_id,
                KnowledgeArticleVersion.revision == KnowledgeChunk.revision,
            ),
        )
        .where(
            KnowledgeArticle.organization_id == org,
            KnowledgeArticle.lifecycle == "published",
            KnowledgeChunk.model_version == KB_MODEL_VERSION,
            KnowledgeChunk.status == "ready",
            KnowledgeChunk.embedding.is_not(None),
        )
        .order_by(distance, KnowledgeChunk.article_id, KnowledgeChunk.chunk_index)
        .limit(limit)
    ).all()
    results = []
    for article, version, chunk, raw_distance in rows:
        relevance = max(0.0, min(1.0, 1.0 - float(raw_distance)))
        if relevance >= minimum_relevance:
            results.append(
                {
                    "article_id": article.id,
                    "revision": version.revision,
                    "title": version.title,
                    "slug": article.slug,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.content,
                    "relevance": round(relevance, 4),
                }
            )
    return results
