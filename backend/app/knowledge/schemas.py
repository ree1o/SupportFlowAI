import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.identity.schemas import Input


def clean(value: str, maximum: int) -> str:
    value = value.strip()
    if (
        not value
        or len(value) > maximum
        or any(ord(char) < 32 and char not in "\n\t\r" for char in value)
    ):
        raise ValueError("Text must be non-empty and within the allowed length")
    return value


class ArticleCreate(Input):
    slug: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50000)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("Slug must contain lowercase letters, digits, and hyphens")
        return value

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean(value, 200)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return clean(value, 50000)


class ArticleEdit(Input):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return clean(value, 200)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return clean(value, 50000)


class PublishArticle(Input):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]


class ArticleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    slug: str
    title: str
    content: str
    lifecycle: str
    current_revision: int
    published_revision: int | None
    index_status: str
    created_at: datetime
    updated_at: datetime


class ArticlePage(BaseModel):
    items: list[ArticleView]
    total: int
    page: int
    page_size: int


class RetrievalSource(BaseModel):
    article_id: UUID
    revision: int
    title: str
    slug: str
    chunk_index: int
    text: str
    relevance: float


class SearchResult(BaseModel):
    model_version: str
    items: list[RetrievalSource]
