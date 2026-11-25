"""Explicit read-only tools for bounded, ticket-scoped assistance."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.identity.security import utcnow
from app.knowledge.embedding import get_knowledge_embedder
from app.knowledge.service import search_published
from app.sla.models import TicketSla
from app.tenancy import set_tenant_context
from app.tickets.models import Ticket

MAX_TOOL_RESULT_CHARS = 4500
MAX_AGENTIC_SOURCES = 4


class SearchKnowledgeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=400)]


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Find current published support instructions for this ticket.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 3, "maxLength": 400}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sla_status",
            "description": "Read the response and resolution SLA deadlines for this ticket.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


class ToolRejected(ValueError):
    """A proposed tool call did not match the narrow tool contract."""


def execute_read_only_tool(
    db: Session,
    org: UUID,
    ticket: Ticket,
    name: str,
    raw_arguments: dict[str, object],
    settings: Settings,
    collected_sources: list[dict],
) -> str:
    result: dict[str, object]
    if name == "search_knowledge_base":
        try:
            arguments = SearchKnowledgeArguments.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolRejected("Invalid knowledge search arguments") from exc
        vector = get_knowledge_embedder(settings.semantic_model_cache_dir).embed_query(
            arguments.query
        )
        set_tenant_context(db, org)
        matches = search_published(db, org, vector, MAX_AGENTIC_SOURCES, 0.30)
        db.commit()
        found = []
        known = {
            (item["article_id"], item["revision"], item["chunk_index"]): item
            for item in collected_sources
        }
        for match in matches:
            key = (match["article_id"], match["revision"], match["chunk_index"])
            source = known.get(key)
            if source is None:
                if len(collected_sources) >= MAX_AGENTIC_SOURCES:
                    continue
                source = {**match, "ref": f"S{len(collected_sources) + 1}"}
                collected_sources.append(source)
                known[key] = source
            found.append(
                {
                    "ref": source["ref"],
                    "title": source["title"],
                    "revision": source["revision"],
                    "excerpt": " ".join(str(source["text"]).split())[:480],
                    "relevance": source["relevance"],
                }
            )
        result = {
            "sources": found,
            "note": "Only these published revisions may support policy claims.",
        }
    elif name == "get_sla_status":
        try:
            NoArguments.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolRejected("Invalid SLA tool arguments") from exc
        set_tenant_context(db, org)
        sla = db.scalar(
            select(TicketSla).where(
                TicketSla.organization_id == org, TicketSla.ticket_id == ticket.id
            )
        )
        if sla is None:
            result = {"configured": False, "message": "No SLA snapshot exists for this ticket."}
        else:
            now = utcnow()
            result = {
                "configured": True,
                "response_due_at": sla.response_due_at.isoformat(),
                "response_state": (
                    "met"
                    if ticket.first_response_at and ticket.first_response_at <= sla.response_due_at
                    else "breached"
                    if sla.response_breached_at or now > sla.response_due_at
                    else "open"
                ),
                "resolution_due_at": sla.resolution_due_at.isoformat(),
                "resolution_state": (
                    "met"
                    if ticket.first_resolved_at
                    and ticket.first_resolved_at <= sla.resolution_due_at
                    else "breached"
                    if sla.resolution_breached_at or now > sla.resolution_due_at
                    else "open"
                ),
            }
    else:
        raise ToolRejected("Tool is not allowlisted")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return encoded[:MAX_TOOL_RESULT_CHARS]
