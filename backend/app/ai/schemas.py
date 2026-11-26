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
    request_limit: int = Field(default=30, ge=1)
    requests_remaining: int | None = Field(default=None, ge=0)
    limit_resets_in_seconds: int | None = Field(default=None, ge=0)


class AIRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["reply", "summary", "agentic"]
    expected_ticket_version: Annotated[int, Field(strict=True, ge=1)]


class ConditionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    status: Literal["met", "not_met", "unknown"]


class GeneratedContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Annotated[str, StringConstraints(max_length=6000)] = ""
    insufficient_evidence: bool = False
    cited_source_refs: list[Annotated[str, StringConstraints(min_length=1, max_length=8)]] = Field(
        default_factory=list, max_length=6
    )
    condition_checks: list[ConditionCheck] = Field(default_factory=list, max_length=8)
    clarifying_questions: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=6
    )


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: Annotated[str, StringConstraints(min_length=1, max_length=6000)]


class AgenticAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_assessment: Annotated[str, StringConstraints(max_length=1200)] = ""
    sla_assessment: Annotated[str, StringConstraints(max_length=800)] = ""
    related_ticket_assessment: Annotated[str, StringConstraints(max_length=800)] = ""
    potential_causes: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=5
    )
    missing_information: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=6
    )
    recommended_actions: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=6
    )


class GeneratedAgenticContent(GeneratedContent):
    analysis: AgenticAnalysis = Field(default_factory=AgenticAnalysis)


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
    tools_used: list[
        Literal[
            "search_knowledge_base",
            "get_sla_status",
            "get_ticket_context",
            "find_related_tickets",
        ]
    ] = Field(default_factory=list)
    agentic_analysis: AgenticAnalysis | None = None
    warnings: list[str]
    error_code: str | None
    latency_ms: int | None
    created_at: datetime
    finished_at: datetime | None
