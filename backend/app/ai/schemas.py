from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AIStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    state: str
    model: str
    digest: str | None


class AIRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["reply", "summary", "agentic"]
    expected_ticket_version: Annotated[int, Field(strict=True, ge=1)]


class GeneratedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Annotated[str, StringConstraints(max_length=6000)] = ""
    insufficient_evidence: bool = False
    cited_source_refs: list[Annotated[str, StringConstraints(min_length=1, max_length=8)]] = Field(
        default_factory=list, max_length=6
    )


class AICitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: UUID
    revision: int
    chunk_index: int
    title: str
    slug: str
    excerpt: Annotated[str, StringConstraints(max_length=360)]
    relevance: float = Field(ge=0, le=1)


class AIRunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    organization_id: UUID
    ticket_id: UUID
    action: str
    status: str
    ticket_version: int
    model_id: str
    result: str | None
    insufficient_evidence: bool
    citations: list[AICitation]
    tools_used: list[Literal["search_knowledge_base", "get_sla_status"]] = Field(
        default_factory=list
    )
    warnings: list[str]
    error_code: str | None
    latency_ms: int | None
    created_at: datetime
    finished_at: datetime | None
