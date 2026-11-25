from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.identity.api import DB, Auth, WriteAuth
from app.knowledge import service
from app.knowledge.schemas import (
    ArticleCreate,
    ArticleEdit,
    ArticlePage,
    ArticleView,
    PublishArticle,
)
from app.rate_limit import limited

router = APIRouter(prefix="/api/organizations/{organization_id}/knowledge", tags=["knowledge base"])
Page = Annotated[int, Query(ge=1, le=10000)]
Size = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=200)]


@router.get("/articles", response_model=ArticlePage)
def articles(
    organization_id: UUID,
    auth: Auth,
    db: DB,
    q: Search = "",
    page: Page = 1,
    page_size: Size = 20,
) -> ArticlePage:
    return service.list_articles(db, auth, organization_id, q.strip(), page, page_size)


@router.post("/articles", response_model=ArticleView, status_code=201)
def create_article(
    organization_id: UUID, data: ArticleCreate, auth: WriteAuth, db: DB
) -> ArticleView:
    return service.create_article(db, auth, organization_id, data)


@router.get("/articles/{article_id}", response_model=ArticleView)
def article(organization_id: UUID, article_id: UUID, auth: Auth, db: DB) -> ArticleView:
    return service.get_article(db, auth, organization_id, article_id)


@router.patch("/articles/{article_id}", response_model=ArticleView)
def edit_article(
    organization_id: UUID, article_id: UUID, data: ArticleEdit, auth: WriteAuth, db: DB
) -> ArticleView:
    return service.edit_article(db, auth, organization_id, article_id, data)


@router.post(
    "/articles/{article_id}/publish",
    response_model=ArticleView,
    dependencies=[Depends(limited("knowledge-publish", 30, 60))],
)
def publish_article(
    organization_id: UUID,
    article_id: UUID,
    data: PublishArticle,
    auth: WriteAuth,
    db: DB,
) -> ArticleView:
    return service.publish_article(db, auth, organization_id, article_id, data.expected_revision)


@router.post("/articles/{article_id}/archive", response_model=ArticleView)
def archive_article(
    organization_id: UUID, article_id: UUID, auth: WriteAuth, db: DB
) -> ArticleView:
    return service.archive_article(db, auth, organization_id, article_id)


@router.post(
    "/articles/{article_id}/reindex",
    response_model=ArticleView,
    dependencies=[Depends(limited("knowledge-reindex", 10, 900))],
)
def reindex_article(
    organization_id: UUID, article_id: UUID, auth: WriteAuth, db: DB
) -> ArticleView:
    return service.retry_article_index(db, auth, organization_id, article_id)
