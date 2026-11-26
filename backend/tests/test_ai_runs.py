from contextlib import ExitStack
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.models import AIRun
from app.ai.provider import ModelStatus, ToolCall, ToolTurn
from app.ai.service import process_run
from app.identity.security import utcnow
from app.knowledge.models import KnowledgeChunk
from app.main import create_app
from app.tickets.models import Ticket

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def write(client, method, path, data=None, *, headers=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    request_headers = {"Origin": ORIGIN, "X-CSRF-Token": token, **(headers or {})}
    return client.request(method, f"/api{path}", json=data, headers=request_headers)


@pytest.fixture
def ai_desk(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
            for name in ("owner", "outsider")
        }
        profiles = {}
        for name, client in clients.items():
            response = write(
                client,
                "POST",
                "/auth/register",
                {
                    "email": f"ai-runs-{name}@example.com",
                    "password": "a sufficiently long test password",
                    "full_name": name.title(),
                    "organization": {
                        "name": name.title(),
                        "slug": f"ai-runs-{name}-{uuid4().hex[:8]}",
                    },
                },
            )
            assert response.status_code == 201, response.text
            profiles[name] = response.json()
        org_id = UUID(profiles["owner"]["selected_organization_id"])
        root = f"/organizations/{org_id}"
        contact = write(clients["owner"], "POST", f"{root}/contacts", {"name": "Example Contact"})
        assert contact.status_code == 201, contact.text
        ticket = write(
            clients["owner"],
            "POST",
            f"{root}/tickets",
            {
                "requester_contact_id": contact.json()["id"],
                "title": "Unable to reset account password",
                "initial_message": "The reset link returns an expired message.",
            },
        )
        assert ticket.status_code == 201, ticket.text
        article = write(
            clients["owner"],
            "POST",
            f"{root}/knowledge/articles",
            {
                "slug": "password-reset-help",
                "title": "Reset password help",
                "content": "Request a new password reset link from the account recovery page.",
            },
        )
        assert article.status_code == 201, article.text
        published = write(
            clients["owner"],
            "POST",
            f"{root}/knowledge/articles/{article.json()['id']}/publish",
            {"expected_revision": 1},
        )
        assert published.status_code == 200, published.text
        with database.session() as db:
            chunk = db.scalar(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.organization_id == org_id,
                    KnowledgeChunk.article_id == UUID(article.json()["id"]),
                )
            )
            assert chunk
            chunk.embedding = [1.0] + [0.0] * 1023
            chunk.status = "ready"
            db.commit()
        yield {
            "clients": clients,
            "db": database,
            "settings": settings,
            "org": org_id,
            "root": root,
            "ticket": ticket.json(),
            "article": article.json(),
        }


class FixedKnowledgeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        assert "staff investigation secret" not in text
        return [1.0] + [0.0] * 1023


class CapturingProvider:
    def __init__(self):
        self.prompt = ""

    def status(self):
        from app.ai.provider import ModelStatus

        return ModelStatus("available", "test-local-model", "synthetic-digest")

    def generate_structured(self, prompt, response_model, max_tokens):
        del max_tokens
        self.prompt = prompt
        return response_model.model_validate(
            {
                "answer": "Please request a fresh password reset link from account recovery.",
                "insufficient_evidence": False,
                "cited_source_refs": ["S1"],
            }
        )


def start_reply(desk, key="ai-run-key-0001", expected_version=1):
    response = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "reply", "expected_ticket_version": expected_version},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response


def start_agentic(desk, key="ai-agent-key-0001"):
    response = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "agentic", "expected_ticket_version": 1},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response


class ToolCallingProvider:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.messages = []

    def status(self):
        return ModelStatus("available", "test-local-model", "synthetic-digest")

    def generate_structured(self, prompt, response_model, max_tokens):
        raise AssertionError("agentic requests must use tool-capable orchestration")

    def generate_with_tools(self, messages, tools, max_tokens):
        assert len(tools) == 4
        self.messages = messages
        return next(self.calls)


def test_run_creation_is_idempotent_and_grounded_reply_excludes_internal_notes(
    ai_desk, monkeypatch
):
    from app.ai import service as ai_service

    desk = ai_desk
    note = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/tickets/{desk['ticket']['id']}/notes",
        {"expected_version": 1, "body": "staff investigation secret"},
    )
    assert note.status_code == 201
    desk["ticket"]["version"] = 2
    first = start_reply(desk, "ai-run-key-0001", expected_version=2)
    duplicate = start_reply(desk, "ai-run-key-0001", expected_version=2)
    assert duplicate.json()["id"] == first.json()["id"]

    class FixedTicketEmbedder(FixedKnowledgeEmbedder):
        pass

    monkeypatch.setattr(ai_service, "get_knowledge_embedder", lambda _cache: FixedTicketEmbedder())
    provider = CapturingProvider()
    run_id = UUID(first.json()["id"])
    with desk["db"].session() as db:
        assert process_run(db, desk["org"], run_id, desk["settings"], provider) == "completed"
    assert "staff investigation secret" not in provider.prompt
    assert "The reset link returns an expired message." in provider.prompt

    completed = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["result"].startswith("Please request a fresh")
    assert completed.json()["citations"][0]["article_id"] == desk["article"]["id"]
    assert "staff investigation secret" not in completed.text
    assert "secret" not in completed.text.lower()


def test_conversation_summary_does_not_require_knowledge_citations(ai_desk, monkeypatch):
    from app.ai import service as ai_service
    from app.ai.schemas import ConversationSummary

    desk = ai_desk
    note = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/tickets/{desk['ticket']['id']}/notes",
        {"expected_version": 1, "body": "internal note must not enter the summary"},
    )
    assert note.status_code == 201
    monkeypatch.setattr(
        ai_service,
        "get_knowledge_embedder",
        lambda _cache: (_ for _ in ()).throw(AssertionError("summary must not embed")),
    )
    response = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "summary", "expected_ticket_version": 2},
        headers={"Idempotency-Key": "ai-summary-contract-01"},
    )
    assert response.status_code == 202, response.text

    class SummaryProvider(CapturingProvider):
        def generate_structured(self, prompt, response_model, max_tokens):
            del max_tokens
            self.prompt = prompt
            assert response_model is ConversationSummary
            assert "staff investigation secret" not in prompt
            assert "internal note must not enter the summary" not in prompt
            return ConversationSummary(summary="The customer cannot use the expired reset link.")

    provider = SummaryProvider()
    with desk["db"].session() as db:
        assert (
            process_run(db, desk["org"], UUID(response.json()["id"]), desk["settings"], provider)
            == "completed"
        )
    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{response.json()['id']}")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["result"] == "The customer cannot use the expired reset link."
    assert result.json()["insufficient_evidence"] is False
    assert result.json()["citations"] == []


def test_ai_run_requires_ticket_access_version_and_tenant(ai_desk):
    desk = ai_desk
    missing_key = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "reply", "expected_ticket_version": 1},
    )
    assert missing_key.status_code == 422
    stale = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "reply", "expected_ticket_version": 2},
        headers={"Idempotency-Key": "ai-run-key-stale"},
    )
    assert stale.status_code == 409
    run = start_reply(desk)
    other = desk["clients"]["outsider"].get(f"/api{desk['root']}/ai/runs/{run.json()['id']}")
    assert other.status_code == 404
    wrong_org = desk["clients"]["owner"].get(
        f"/api/organizations/{desk['clients']['outsider'].get('/api/auth/me').json()['selected_organization_id']}"
        f"/ai/runs/{run.json()['id']}"
    )
    assert wrong_org.status_code == 404


def test_only_one_active_generation_per_ticket(ai_desk):
    desk = ai_desk
    first = start_reply(desk, "ai-active-ticket-run-01")
    assert first.status_code == 202
    second = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/ai/tickets/{desk['ticket']['id']}/runs",
        {"action": "summary", "expected_ticket_version": 1},
        headers={"Idempotency-Key": "ai-active-ticket-run-02"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ai_run_in_progress"
    assert second.headers["Retry-After"] == "3"


def test_stale_ticket_version_cannot_publish_generation_result(ai_desk, monkeypatch):
    from app.ai import service as ai_service

    desk = ai_desk
    run = start_reply(desk, "ai-run-key-stale1")
    run_id = UUID(run.json()["id"])
    with desk["db"].session() as db:
        ticket = db.scalar(select(Ticket).where(Ticket.id == UUID(desk["ticket"]["id"])))
        assert ticket
        ticket.version += 1
        db.commit()
    monkeypatch.setattr(
        ai_service, "get_knowledge_embedder", lambda _cache: FixedKnowledgeEmbedder()
    )
    provider = CapturingProvider()
    with desk["db"].session() as db:
        assert process_run(db, desk["org"], run_id, desk["settings"], provider) == "stale"
    assert provider.prompt == ""
    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert result.json()["status"] == "stale"
    assert result.json()["result"] is None


def test_run_database_policy_is_forced_and_tenant_scoped(ai_desk, runtime_database):
    desk = ai_desk
    run = start_reply(desk, "ai-run-key-rls01")
    _, runtime_db = runtime_database
    other_org = UUID(
        desk["clients"]["outsider"].get("/api/auth/me").json()["selected_organization_id"]
    )
    with runtime_db.session() as db:
        from sqlalchemy import text

        from app.tenancy import set_tenant_context

        assert db.scalar(select(AIRun.id).where(AIRun.id == UUID(run.json()["id"]))) is None
        set_tenant_context(db, other_org)
        visible = db.scalar(select(AIRun.id).where(AIRun.id == UUID(run.json()["id"])))
        assert visible is None
        set_tenant_context(db, desk["org"])
        visible = db.scalar(select(AIRun.id).where(AIRun.id == UUID(run.json()["id"])))
        assert visible == UUID(run.json()["id"])
        flags = db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='ai_runs'")
        ).one()
        assert flags == (True, True)
        delete_allowed = db.scalar(
            text("SELECT has_table_privilege(current_user, 'ai_runs', 'DELETE')")
        )
        assert delete_allowed is False


def test_grounded_reply_finishes_under_runtime_role_with_forced_rls(
    ai_desk, runtime_database, monkeypatch
):
    from sqlalchemy import text

    from app.ai import service as ai_service

    desk = ai_desk
    run = start_reply(desk, "ai-runtime-role-key-01")
    run_id = UUID(run.json()["id"])
    monkeypatch.setattr(
        ai_service, "get_knowledge_embedder", lambda _cache: FixedKnowledgeEmbedder()
    )
    _, runtime_db = runtime_database
    provider = CapturingProvider()
    with runtime_db.session() as db:
        assert db.scalar(text("SELECT current_user")) == "supportflow_runtime"
        assert (
            db.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ai_runs' "
                    "AND relnamespace = current_schema()::regnamespace"
                )
            )
            is True
        )
        assert process_run(db, desk["org"], run_id, desk["settings"], provider) == "completed"

    completed = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["citations"][0]["article_id"] == desk["article"]["id"]


def test_failed_completion_transaction_is_rolled_back_before_failure_state_write(
    ai_desk, runtime_database, monkeypatch
):
    from sqlalchemy import text

    from app.ai import service as ai_service

    desk = ai_desk
    run_id = UUID(start_reply(desk, "ai-runtime-failure-key-01").json()["id"])
    monkeypatch.setattr(
        ai_service, "get_knowledge_embedder", lambda _cache: FixedKnowledgeEmbedder()
    )

    def break_completion(db, *_args, **_kwargs):
        db.execute(text("SELECT 1 / 0"))

    monkeypatch.setattr(ai_service, "_finish_run", break_completion)
    _, runtime_db = runtime_database
    with runtime_db.session() as db:
        assert db.scalar(text("SELECT current_user")) == "supportflow_runtime"
        assert (
            process_run(db, desk["org"], run_id, desk["settings"], CapturingProvider()) == "failed"
        )

    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    assert result.json()["error_code"] == "generation_failed"


def test_recovery_marks_max_attempt_interrupted_run_terminal_under_forced_rls(
    ai_desk, runtime_database
):
    from sqlalchemy import update

    from app.ai.service import MAX_RUN_ATTEMPTS, recover_runs

    desk = ai_desk
    run_id = UUID(start_reply(desk, "ai-runtime-recovery-key-01").json()["id"])
    old_time = utcnow() - timedelta(minutes=30)
    with desk["db"].session() as db:
        from app.tenancy import set_tenant_context

        set_tenant_context(db, desk["org"])
        db.execute(
            update(AIRun)
            .where(AIRun.organization_id == desk["org"], AIRun.id == run_id)
            .values(status="running", attempts=MAX_RUN_ATTEMPTS, updated_at=old_time)
        )
        db.commit()

    _, runtime_db = runtime_database
    with runtime_db.session() as db:
        assert recover_runs(db, desk["org"]) == []

    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    assert result.json()["error_code"] == "worker_interrupted"


def test_agentic_run_uses_only_bounded_read_only_tools_and_validated_sources(ai_desk, monkeypatch):
    from app.ai import tools as ai_tools

    desk = ai_desk
    monkeypatch.setattr(ai_tools, "get_knowledge_embedder", lambda _cache: FixedKnowledgeEmbedder())
    provider = ToolCallingProvider(
        [
            ToolTurn(
                "",
                (ToolCall("search_knowledge_base", {"query": "password recovery instructions"}),),
            ),
            ToolTurn("", (ToolCall("get_sla_status", {}),)),
            ToolTurn(
                "",
                (ToolCall("get_ticket_context", {}),),
            ),
            ToolTurn(
                "",
                (ToolCall("find_related_tickets", {}),),
            ),
            ToolTurn(
                '{"answer":"Ask the customer to request a fresh recovery link.",'
                '"insufficient_evidence":false,"cited_source_refs":["S1"],'
                '"analysis":{"ticket_assessment":"Password reset link expired.",'
                '"sla_assessment":"Within target.",'
                '"related_ticket_assessment":"No comparable ticket.",'
                '"potential_causes":["Expired token"],'
                '"missing_information":["When was the link requested?"],'
                '"recommended_actions":["Issue a new link"]}}',
                (),
            ),
        ]
    )
    run = start_agentic(desk)
    run_id = UUID(run.json()["id"])
    with desk["db"].session() as db:
        assert process_run(db, desk["org"], run_id, desk["settings"], provider) == "completed"
    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert result.json()["tools_used"] == [
        "search_knowledge_base",
        "get_sla_status",
        "get_ticket_context",
        "find_related_tickets",
    ]
    assert result.json()["agentic_analysis"]["potential_causes"] == ["Expired token"]
    assert result.json()["citations"][0]["article_id"] == desk["article"]["id"]
    assert [message["tool_name"] for message in provider.messages if message["role"] == "tool"] == [
        "search_knowledge_base",
        "get_sla_status",
        "get_ticket_context",
        "find_related_tickets",
    ]
    assert "staff investigation secret" not in str(provider.messages)


def test_agentic_run_rejects_unknown_and_repeated_tools(ai_desk):
    desk = ai_desk
    provider = ToolCallingProvider(
        [
            ToolTurn("", (ToolCall("delete_ticket", {"ticket_id": "anything"}),)),
            ToolTurn("", (ToolCall("delete_ticket", {"ticket_id": "anything"}),)),
            ToolTurn(
                '{"answer":"I could not read a verified source.",'
                '"insufficient_evidence":true,"cited_source_refs":[]}',
                (),
            ),
        ]
    )
    run = start_agentic(desk, "ai-agent-key-invalid")
    run_id = UUID(run.json()["id"])
    with desk["db"].session() as db:
        assert process_run(db, desk["org"], run_id, desk["settings"], provider) == "completed"
    result = desk["clients"]["owner"].get(f"/api{desk['root']}/ai/runs/{run_id}")
    assert result.status_code == 200
    assert result.json()["tools_used"] == []
    assert result.json()["result"] == "I could not read a verified source."
    assert any(
        message.get("role") == "tool" and "rejected" in message["content"]
        for message in provider.messages
    )


def test_ai_result_retention_redacts_expired_content_but_keeps_run_state(ai_desk):
    from app.retention import cleanup_transient_data

    desk = ai_desk
    response = start_reply(desk, "ai-retention-key-01")
    run_id = UUID(response.json()["id"])
    with desk["db"].session() as db:
        run = db.get(AIRun, run_id)
        assert run
        run.status = "completed"
        run.result = {
            "answer": "synthetic private draft",
            "citations": [],
            "tools_used": [],
            "warnings": [],
        }
        run.finished_at = utcnow() - timedelta(days=2)
        db.commit()
        settings = desk["settings"].model_copy(update={"ai_result_retention_days": 1})
        preview = cleanup_transient_data(db, settings, dry_run=True)
        assert preview["ai_results_redacted"] == 1
        assert db.get(AIRun, run_id).result["answer"] == "synthetic private draft"
        counts = cleanup_transient_data(db, settings)
        assert counts["ai_results_redacted"] == 1
        redacted = db.get(AIRun, run_id)
        assert redacted.status == "completed"
        assert redacted.result["answer"] == ""
        assert redacted.result["purged"] is True
