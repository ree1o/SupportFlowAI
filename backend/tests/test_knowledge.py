from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import UUID

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.customers.models import BackgroundJob, OutboxEvent
from app.knowledge import worker as knowledge_worker
from app.knowledge.chunking import chunk_text
from app.knowledge.models import KB_MODEL_VERSION, KnowledgeArticle, KnowledgeChunk
from app.main import create_app
from app.tenancy import set_tenant_context

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def write(client: TestClient, method: str, path: str, data=None):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )


@pytest.fixture
def knowledge_desk(postgres):
    settings, database, alembic = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
            for name in ("owner", "agent", "outsider")
        }
        profiles = {}
        for name, client in clients.items():
            response = write(
                client,
                "POST",
                "/auth/register",
                {
                    "email": f"knowledge-{name}@example.com",
                    "password": "a synthetic knowledge test passphrase",
                    "full_name": name.title(),
                    "organization": {
                        "name": f"{name.title()} KB",
                        "slug": f"knowledge-{name}",
                    },
                },
            )
            assert response.status_code == 201, response.text
            profiles[name] = response.json()
        org = profiles["owner"]["selected_organization_id"]
        root = f"/organizations/{org}"
        member = write(
            clients["owner"],
            "POST",
            f"{root}/memberships",
            {"email": "knowledge-agent@example.com", "role": "agent"},
        )
        assert member.status_code == 201, member.text
        yield {
            "clients": clients,
            "db": database,
            "alembic": alembic,
            "settings": settings,
            "root": root,
            "org": UUID(org),
        }


def article_payload(slug="password-reset", title="Reset a password", content="Use the reset page."):
    return {"slug": slug, "title": title, "content": content}


def test_article_draft_publication_and_immutable_revision_visibility(knowledge_desk):
    desk = knowledge_desk
    owner = desk["clients"]["owner"]
    agent = desk["clients"]["agent"]
    root = desk["root"]
    created = write(owner, "POST", f"{root}/knowledge/articles", article_payload())
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["lifecycle"] == "draft"
    assert draft["index_status"] == "not_published"
    assert agent.get(f"/api{root}/knowledge/articles").json()["total"] == 0
    assert agent.get(f"/api{root}/knowledge/articles/{draft['id']}").status_code == 404

    published = write(
        owner,
        "POST",
        f"{root}/knowledge/articles/{draft['id']}/publish",
        {"expected_revision": 1},
    )
    assert published.status_code == 200, published.text
    assert published.json()["index_status"] == "pending"
    visible = agent.get(f"/api{root}/knowledge/articles/{draft['id']}")
    assert visible.status_code == 200
    assert visible.json()["content"] == "Use the reset page."

    edit = write(
        owner,
        "PATCH",
        f"{root}/knowledge/articles/{draft['id']}",
        {
            "expected_revision": 1,
            "title": "Reset an account password",
            "content": "Use the account recovery page.",
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["current_revision"] == 2
    # Agents keep reading the previously published version until the new revision is published.
    assert agent.get(f"/api{root}/knowledge/articles/{draft['id']}").json()["content"] == (
        "Use the reset page."
    )
    stale = write(
        owner,
        "POST",
        f"{root}/knowledge/articles/{draft['id']}/publish",
        {"expected_revision": 1},
    )
    assert stale.status_code == 409
    revised = write(
        owner,
        "POST",
        f"{root}/knowledge/articles/{draft['id']}/publish",
        {"expected_revision": 2},
    )
    assert revised.status_code == 200
    assert agent.get(f"/api{root}/knowledge/articles/{draft['id']}").json()["content"] == (
        "Use the account recovery page."
    )

    archived = write(owner, "POST", f"{root}/knowledge/articles/{draft['id']}/archive")
    assert archived.status_code == 200
    assert agent.get(f"/api{root}/knowledge/articles/{draft['id']}").status_code == 404


def test_article_authorization_and_tenant_isolation(knowledge_desk):
    desk = knowledge_desk
    owner = desk["clients"]["owner"]
    agent = desk["clients"]["agent"]
    outsider = desk["clients"]["outsider"]
    root = desk["root"]
    forbidden = write(agent, "POST", f"{root}/knowledge/articles", article_payload())
    assert forbidden.status_code == 403
    created = write(owner, "POST", f"{root}/knowledge/articles", article_payload())
    article_id = created.json()["id"]
    stranger_root = (
        f"/organizations/{outsider.get('/api/auth/me').json()['selected_organization_id']}"
    )
    assert outsider.get(f"/api{stranger_root}/knowledge/articles/{article_id}").status_code == 404
    assert outsider.get(f"/api{root}/knowledge/articles/{article_id}").status_code == 404


def test_knowledge_rls_uses_runtime_role(knowledge_desk, runtime_database):
    from datetime import UTC, datetime

    from app.identity.security import utcnow

    desk = knowledge_desk
    outsider = desk["clients"]["outsider"]
    outsider_me = outsider.get("/api/auth/me").json()
    outsider_org = UUID(outsider_me["selected_organization_id"])
    outsider_root = f"/organizations/{outsider_org}"
    other_article_response = write(
        outsider,
        "POST",
        f"{outsider_root}/knowledge/articles",
        article_payload(slug="private-other-tenant"),
    )
    assert other_article_response.status_code == 201
    other_article_id = UUID(other_article_response.json()["id"])
    outsider_membership = outsider.get(f"/api{outsider_root}/memberships").json()[0]
    _, runtime_db = runtime_database

    with runtime_db.session() as db:
        set_tenant_context(db, desk["org"])
        role = db.scalar(text("select current_user"))
        visible = db.scalar(
            select(KnowledgeArticle.id).where(
                KnowledgeArticle.organization_id == outsider_org,
                KnowledgeArticle.id == other_article_id,
            )
        )
        assert role == "supportflow_runtime"
        assert visible is None
        with pytest.raises(DBAPIError):
            db.add(
                KnowledgeArticle(
                    organization_id=outsider_org,
                    slug="forged-cross-tenant",
                    lifecycle="draft",
                    current_revision=1,
                    published_revision=None,
                    created_by_membership_id=UUID(outsider_membership["id"]),
                    created_at=datetime.now(UTC),
                    updated_at=utcnow(),
                )
            )
            db.flush()
        db.rollback()
        policy = db.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c WHERE c.relname='knowledge_articles'"
            )
        ).one()
        assert policy == (True, True)


def test_publish_commits_chunks_and_outbox_atomically(knowledge_desk):
    desk = knowledge_desk
    owner = desk["clients"]["owner"]
    created = write(owner, "POST", f"{desk['root']}/knowledge/articles", article_payload())
    article_id = created.json()["id"]
    published = write(
        owner,
        "POST",
        f"{desk['root']}/knowledge/articles/{article_id}/publish",
        {"expected_revision": 1},
    )
    assert published.status_code == 200
    with desk["db"].session() as db:
        article = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.id == UUID(article_id)))
        chunks = list(
            db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.article_id == UUID(article_id)))
        )
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.event_key.like(f"knowledge-index:{article_id}:%"))
        )
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.outbox_event_id == event.id))
        assert article and article.published_revision == 1
        assert len(chunks) == 1
        assert chunks[0].status == "pending" and chunks[0].model_version == KB_MODEL_VERSION
        assert event and event.event_type == "knowledge.index" and job


def test_upgrade_from_m9_preserves_existing_organization_data(knowledge_desk):
    desk = knowledge_desk
    with desk["db"].session() as db:
        before = db.scalar(
            select(KnowledgeArticle.organization_id).where(
                KnowledgeArticle.organization_id == desk["org"]
            )
        )
    assert before is None
    command.downgrade(desk["alembic"], "0009_release_hardening")
    command.upgrade(desk["alembic"], "head")
    with desk["db"].session() as db:
        organization_id = db.scalar(
            text("SELECT id FROM organizations WHERE id = :organization_id"),
            {"organization_id": desk["org"]},
        )
        assert organization_id == desk["org"]
        assert db.scalar(text("SELECT count(*) FROM knowledge_articles")) == 0


class FixedKnowledgeEmbedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]


def publish_one(desk, slug: str) -> str:
    owner = desk["clients"]["owner"]
    response = write(
        owner,
        "POST",
        f"{desk['root']}/knowledge/articles",
        article_payload(slug=slug),
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    response = write(
        owner,
        "POST",
        f"{desk['root']}/knowledge/articles/{identifier}/publish",
        {"expected_revision": 1},
    )
    assert response.status_code == 200, response.text
    return identifier


def test_dispatch_and_index_are_idempotent_and_published_only(knowledge_desk, monkeypatch):
    desk = knowledge_desk
    article_id = publish_one(desk, "index-this")
    monkeypatch.setattr(
        knowledge_worker, "get_knowledge_embedder", lambda _cache: FixedKnowledgeEmbedder()
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = list(pool.map(lambda _: dispatch_once(desk), range(2)))
    dispatched = [item for item in calls if item]
    assert len(dispatched) == 1
    organization_id, target_id, revision, token = dispatched[0]
    assert target_id == UUID(article_id) and organization_id == desk["org"]
    with desk["db"].session() as db:
        result = knowledge_worker.process_index(
            db, organization_id, target_id, revision, token, desk["settings"]
        )
    assert result == "ready"
    # Redelivered old lease is a harmless no-op.
    with desk["db"].session() as db:
        duplicate = knowledge_worker.process_index(
            db, organization_id, target_id, revision, token, desk["settings"]
        )
    assert duplicate == "lease-stale"
    with desk["db"].session() as db:
        chunks = list(
            db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.article_id == target_id))
        )
        assert len(chunks) == 1
        assert chunks[0].status == "ready" and len(chunks[0].embedding) == 1024
        set_tenant_context(db, organization_id)
        from app.knowledge.service import search_published

        hits = search_published(db, organization_id, chunks[0].embedding, 5, 0.1)
        assert [str(hit["article_id"]) for hit in hits] == [article_id]


def dispatch_once(desk):
    with desk["db"].session() as db:
        work = knowledge_worker.dispatch_due(db)
    return work[0] if work else None


def test_chunking_is_bounded_deterministic_and_overlapping():
    content = "Paragraph one.\n\n" + ("shared boundary detail " * 240) + "\n\nLast paragraph."
    first = chunk_text(content)
    assert first == chunk_text(content)
    assert len(first) >= 3
    assert all(1 <= len(chunk) <= 2000 for chunk in first)
    assert first[-1].endswith("Last paragraph.")
    assert any(first[i][-80:] in first[i + 1] for i in range(len(first) - 1))
