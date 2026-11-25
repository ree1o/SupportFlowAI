"""Opt-in real-model smoke test; standard CI requires no generative model download."""

import os
from contextlib import ExitStack
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.provider import OllamaProvider
from app.ai.service import process_run
from app.config import Settings
from app.knowledge.models import KnowledgeChunk
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_LOCAL_AI_SMOKE") != "1",
        reason="Set RUN_LOCAL_AI_SMOKE=1 to run the opt-in local model smoke test",
    ),
]
ORIGIN = "http://localhost:5173"


def write(client: TestClient, method: str, path: str, data: dict | None = None, headers=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token, **(headers or {})},
    )


class FixedTestEmbedder:
    def embed_query(self, text: str) -> list[float]:
        assert len(text) <= 400
        return [1.0] + [0.0] * 1023


def test_real_local_model_executes_a_bounded_agentic_ticket_run(postgres, monkeypatch):
    from app.ai import tools as ai_tools

    base_settings, database, _ = postgres
    settings: Settings = base_settings.model_copy(
        update={"ai_enabled": True, "ai_ollama_url": "http://127.0.0.1:11434"}
    )
    monkeypatch.setattr(ai_tools, "get_knowledge_embedder", lambda _cache: FixedTestEmbedder())
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        suffix = uuid4().hex[:10]
        registration = write(
            client,
            "POST",
            "/auth/register",
            {
                "email": f"local-ai-{suffix}@example.com",
                "password": "a sufficiently long test password",
                "full_name": "Local model smoke",
                "organization": {"name": "Local model smoke", "slug": f"local-ai-{suffix}"},
            },
        )
        assert registration.status_code == 201, registration.text
        org = UUID(registration.json()["selected_organization_id"])
        root = f"/organizations/{org}"
        contact = write(client, "POST", f"{root}/contacts", {"name": "Synthetic contact"})
        assert contact.status_code == 201, contact.text
        ticket = write(
            client,
            "POST",
            f"{root}/tickets",
            {
                "requester_contact_id": contact.json()["id"],
                "title": "Password reset link expires early",
                "initial_message": "The recovery link expires before I can finish the reset.",
            },
        )
        assert ticket.status_code == 201, ticket.text
        article = write(
            client,
            "POST",
            f"{root}/knowledge/articles",
            {
                "slug": "recovery-link-smoke",
                "title": "Use a fresh password recovery link",
                "content": (
                    "If a recovery link has expired, request a new link and complete "
                    "the reset promptly."
                ),
            },
        )
        assert article.status_code == 201, article.text
        published = write(
            client,
            "POST",
            f"{root}/knowledge/articles/{article.json()['id']}/publish",
            {"expected_revision": 1},
        )
        assert published.status_code == 200, published.text
        with database.session() as db:
            chunk = db.scalar(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.organization_id == org,
                    KnowledgeChunk.article_id == UUID(article.json()["id"]),
                )
            )
            assert chunk
            chunk.embedding = [1.0] + [0.0] * 1023
            chunk.status = "ready"
            db.commit()
        requested = write(
            client,
            "POST",
            f"{root}/ai/tickets/{ticket.json()['id']}/runs",
            {"action": "agentic", "expected_ticket_version": 1},
            {"Idempotency-Key": f"live-ai-{suffix}"},
        )
        assert requested.status_code == 202, requested.text
        run_id = UUID(requested.json()["id"])
        with database.session() as db:
            assert process_run(db, org, run_id, settings, OllamaProvider(settings)) == "completed"
        completed = client.get(f"/api{root}/ai/runs/{run_id}")
        assert completed.status_code == 200, completed.text
        result = completed.json()
        assert result["status"] == "completed", result.get("error_code")
        assert result["tools_used"], "the real model should call a bounded read-only tool"
        assert "staff investigation" not in completed.text.lower()
