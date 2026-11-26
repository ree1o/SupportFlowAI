"""Explicit read-only tools for bounded, ticket-scoped assistance."""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.identity.models import Membership, User
from app.identity.security import utcnow
from app.knowledge.embedding import get_knowledge_embedder
from app.knowledge.service import search_published
from app.semantic.models import LEGACY_MODEL_VERSION, MODEL_VERSION, TicketEmbedding
from app.sla.models import TicketSla
from app.tenancy import set_tenant_context
from app.tickets.models import Category, Status, Ticket
from app.tickets.service import manager

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
    {
        "type": "function",
        "function": {
            "name": "get_ticket_context",
            "description": "Read this ticket's status, priority, category, and assignment.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related_tickets",
            "description": "Find semantically related visible tickets in this organization.",
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
    actor_membership_id: UUID,
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
    elif name == "get_ticket_context":
        try:
            NoArguments.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolRejected("Invalid ticket-context arguments") from exc
        set_tenant_context(db, org)
        row = db.execute(
            select(Category.name, User.full_name)
            .select_from(Ticket)
            .outerjoin(
                Category,
                and_(
                    Category.organization_id == Ticket.organization_id,
                    Category.id == Ticket.category_id,
                ),
            )
            .outerjoin(
                Membership,
                and_(
                    Membership.organization_id == Ticket.organization_id,
                    Membership.id == Ticket.assigned_membership_id,
                    Membership.is_active.is_(True),
                ),
            )
            .outerjoin(User, and_(User.id == Membership.user_id, User.is_active.is_(True)))
            .where(Ticket.organization_id == org, Ticket.id == ticket.id)
        ).one_or_none()
        if row is None:
            raise ToolRejected("Ticket context is unavailable")
        result = {
            "status": ticket.status,
            "priority": ticket.priority,
            "category": row[0],
            "assigned_agent": row[1] or "Unassigned",
        }
    elif name == "find_related_tickets":
        try:
            NoArguments.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolRejected("Invalid related-ticket arguments") from exc
        set_tenant_context(db, org)
        source_embedding: TicketEmbedding | None = db.scalar(
            select(TicketEmbedding).where(
                TicketEmbedding.organization_id == org,
                TicketEmbedding.ticket_id == ticket.id,
                TicketEmbedding.model_version == MODEL_VERSION,
                TicketEmbedding.is_current.is_(True),
            )
        )
        model_version = MODEL_VERSION
        if (
            source_embedding is None
            or source_embedding.status != "ready"
            or source_embedding.embedding is None
        ):
            source_embedding = db.scalar(
                select(TicketEmbedding).where(
                    TicketEmbedding.organization_id == org,
                    TicketEmbedding.ticket_id == ticket.id,
                    TicketEmbedding.model_version == LEGACY_MODEL_VERSION,
                    TicketEmbedding.is_current.is_(True),
                )
            )
            model_version = LEGACY_MODEL_VERSION
        related = []
        if (
            source_embedding is not None
            and source_embedding.status == "ready"
            and source_embedding.embedding is not None
        ):
            distance = TicketEmbedding.embedding.cosine_distance(source_embedding.embedding)
            query = (
                select(
                    Ticket.id,
                    Ticket.status,
                    Ticket.priority,
                    Category.name,
                    distance.label("distance"),
                )
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
                .where(
                    TicketEmbedding.organization_id == org,
                    TicketEmbedding.model_version == model_version,
                    TicketEmbedding.status == "ready",
                    TicketEmbedding.is_current.is_(True),
                    TicketEmbedding.ticket_id != ticket.id,
                    Ticket.status != Status.CLOSED,
                )
            )
            actor = db.get(Membership, actor_membership_id)
            if actor is None or not actor.is_active:
                raise ToolRejected("Staff membership is no longer active")
            if not manager(actor):
                query = query.where(
                    or_(
                        Ticket.assigned_membership_id.is_(None),
                        Ticket.assigned_membership_id == actor_membership_id,
                    )
                )
            for ticket_id, status, priority, category, raw_distance in db.execute(
                query.order_by(distance, Ticket.id).limit(3)
            ):
                similarity = max(0.0, min(1.0, 1.0 - float(raw_distance)))
                if similarity >= settings.semantic_min_similarity:
                    # Do not pass other customers' titles or message bodies to the model.
                    related.append(
                        {
                            "ticket_id": str(ticket_id),
                            "status": str(status),
                            "priority": str(priority),
                            "category": category,
                            "similarity": round(similarity, 4),
                        }
                    )
        result = {"items": related, "count": len(related)}
    else:
        raise ToolRejected("Tool is not allowlisted")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return encoded[:MAX_TOOL_RESULT_CHARS]
