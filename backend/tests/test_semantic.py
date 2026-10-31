from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.customers.models import BackgroundJob, OutboxEvent
from app.identity.security import utcnow
from app.main import create_app
from app.semantic.models import MODEL_VERSION, TicketEmbedding
from app.tickets.models import Ticket

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def write(client, method, path, data=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )


@pytest.fixture
def search_desk(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
            for name in ("owner", "agent", "other")
        }
        profiles = {}
        for name, client in clients.items():
            response = write(
                client,
                "POST",
                "/auth/register",
                {
                    "email": f"semantic-{name}@example.com",
                    "password": "synthetic semantic search passphrase",
                    "full_name": name.title(),
                    "organization": {"name": name.title(), "slug": f"semantic-{name}"},
                },
            )
            assert response.status_code == 201, response.text
            profiles[name] = response.json()
        org = profiles["owner"]["selected_organization_id"]
        root = f"/organizations/{org}"
        owner_member = clients["owner"].get(f"/api{root}/memberships").json()[0]
        membership = write(
            clients["owner"],
            "POST",
            f"{root}/memberships",
            {"email": "semantic-agent@example.com", "role": "agent"},
        )
        assert membership.status_code == 201
        contact = write(clients["owner"], "POST", f"{root}/contacts", {"name": "Private Customer"})
        assert contact.status_code == 201
        # Keep worker tests deterministic: registration verification mail is unrelated to indexing.
        with database.session() as db:
            email_ids = select(OutboxEvent.id).where(OutboxEvent.event_type == "email")
            db.execute(delete(BackgroundJob).where(BackgroundJob.outbox_event_id.in_(email_ids)))
            db.execute(delete(OutboxEvent).where(OutboxEvent.event_type == "email"))
            db.commit()
        yield {
            "clients": clients,
            "profiles": profiles,
            "db": database,
            "settings": settings,
            "org": UUID(org),
            "root": root,
            "owner_membership": UUID(owner_member["id"]),
            "contact": contact.json(),
        }


def create_ticket(desk, title: str, body: str) -> dict:
    response = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/tickets",
        {
            "requester_contact_id": desk["contact"]["id"],
            "title": title,
            "initial_message": body,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class FixedEmbedder:
    def __init__(self, block: Event | None = None, entered: Event | None = None) -> None:
        self.block = block
        self.entered = entered
        self.calls = 0

    def embed_document(self, text: str) -> list[float]:
        self.calls += 1
        if self.entered:
            self.entered.set()
        if self.block:
            self.block.wait(timeout=3)
        vector = [0.0] * 384
        if "source anchor" in text:
            vector[0] = 1.0
        elif "related candidate" in text:
            vector[0] = 0.8
            vector[1] = 0.6
        else:
            vector[2] = 1.0
        return vector


def embedding_rows(desk) -> list[TicketEmbedding]:
    with desk["db"].session() as db:
        return list(db.scalars(select(TicketEmbedding).order_by(TicketEmbedding.created_at)))


def test_new_ticket_is_transactionally_queued_and_worker_indexes_with_local_fake(
    search_desk, monkeypatch
):
    from app import jobs
    from app.semantic import service

    desk = search_desk
    source = create_ticket(desk, "Source request", "Public source anchor details")
    candidate = create_ticket(desk, "Different wording", "Public related candidate details")
    assert len(embedding_rows(desk)) == 2
    fake = FixedEmbedder()
    monkeypatch.setattr(service, "get_local_embedder", lambda _cache: fake)
    assert jobs.process_one(desk["db"])
    assert jobs.process_one(desk["db"])
    rows = embedding_rows(desk)
    assert all(row.status == "ready" and row.indexed_at for row in rows)
    response = desk["clients"]["owner"].get(
        f"/api{desk['root']}/tickets/{source['id']}/related?limit=1"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["index_status"] == "ready"
    assert data["items"][0]["id"] == candidate["id"]
    assert data["items"][0]["similarity"] == pytest.approx(0.8, abs=0.001)


def test_related_retrieval_is_staff_only_tenant_scoped_and_excludes_internal_notes(
    search_desk, monkeypatch
):
    from app import jobs
    from app.semantic import service

    desk = search_desk
    source = create_ticket(desk, "Source request", "Public source anchor details")
    candidate = create_ticket(desk, "Candidate request", "Public related candidate details")
    private_note = write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/tickets/{candidate['id']}/notes",
        {"expected_version": 1, "body": "SECRET staff investigation phrase"},
    )
    assert private_note.status_code == 201
    monkeypatch.setattr(service, "get_local_embedder", lambda _cache: FixedEmbedder())
    assert jobs.process_one(desk["db"])
    assert jobs.process_one(desk["db"])

    related = desk["clients"]["owner"].get(f"/api{desk['root']}/tickets/{source['id']}/related")
    assert related.status_code == 200
    assert candidate["id"] in {row["id"] for row in related.json()["items"]}
    assert "SECRET" not in related.text
    cross_tenant = desk["clients"]["owner"].get(
        f"/api/organizations/{desk['profiles']['other']['selected_organization_id']}"
        f"/tickets/{source['id']}/related"
    )
    assert cross_tenant.status_code in {403, 404}
    outsider = desk["clients"]["other"].get(f"/api{desk['root']}/tickets/{source['id']}/related")
    assert outsider.status_code in {403, 404}
    assert (
        desk["clients"]["owner"]
        .get(f"/api{desk['root']}/tickets/{source['id']}/related?limit=11")
        .status_code
        == 422
    )
    assert (
        write(
            desk["clients"]["agent"], "POST", f"{desk['root']}/semantic-index/backfill", {}
        ).status_code
        == 403
    )


def test_worker_failure_is_safe_and_manager_backfill_retries_failed_index(search_desk, monkeypatch):
    from app import jobs
    from app.semantic import service

    desk = search_desk
    ticket = create_ticket(desk, "Source request", "Public source anchor details")

    class Unavailable:
        def embed_document(self, _text):
            raise RuntimeError("private model path is not persisted")

    monkeypatch.setattr(service, "get_local_embedder", lambda _cache: Unavailable())
    assert jobs.process_one(desk["db"])
    row = embedding_rows(desk)[0]
    assert row.status == "pending"
    assert row.last_error == "RuntimeError"
    assert "private model path" not in (row.last_error or "")
    unavailable = desk["clients"]["owner"].get(f"/api{desk['root']}/tickets/{ticket['id']}/related")
    assert unavailable.status_code == 200
    assert unavailable.json()["items"] == []


def test_old_index_job_cannot_publish_a_vector_for_changed_source(search_desk, monkeypatch):
    from app.semantic import service

    desk = search_desk
    ticket = create_ticket(desk, "Source request", "Public source anchor details")
    row = embedding_rows(desk)[0]
    with desk["db"].session() as db:
        record = db.scalar(select(Ticket).where(Ticket.id == ticket["id"]))
        assert record
        record.title = "Changed source content"
        db.commit()
    monkeypatch.setattr(service, "get_local_embedder", lambda _cache: FixedEmbedder())
    with desk["db"].session() as db:
        state = service.process_outbox_embedding(db, row.id, desk["settings"])
    assert state == "stale"
    assert embedding_rows(desk)[0].embedding is None


def test_concurrent_worker_delivery_claims_one_durable_embedding_once(search_desk, monkeypatch):
    from app import jobs
    from app.semantic import service

    desk = search_desk
    create_ticket(desk, "Source request", "Public source anchor details")
    gate = Event()
    entered = Event()
    fake = FixedEmbedder(block=gate, entered=entered)
    monkeypatch.setattr(service, "get_local_embedder", lambda _cache: fake)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(jobs.process_one, desk["db"])
        assert entered.wait(timeout=3)
        second = executor.submit(jobs.process_one, desk["db"])
        assert second.result(timeout=3) is False
        gate.set()
        assert first.result(timeout=3) is True
    assert fake.calls == 1
    row = embedding_rows(desk)[0]
    assert row.status == "ready" and row.embedding is not None


def test_database_enforces_embedding_tenant_reference(search_desk):
    desk = search_desk
    ticket = create_ticket(desk, "Source request", "Public source anchor details")
    foreign_org = UUID(desk["profiles"]["other"]["selected_organization_id"])
    with desk["db"].session() as db, pytest.raises(IntegrityError):
        db.add(
            TicketEmbedding(
                organization_id=foreign_org,
                ticket_id=UUID(ticket["id"]),
                model_version=MODEL_VERSION,
                content_digest="f" * 64,
                source_version=1,
                status="pending",
                available_at=utcnow(),
            )
        )
        db.commit()


def test_database_allows_one_current_model_embedding_per_ticket(search_desk):
    desk = search_desk
    create_ticket(desk, "Source request", "Public source anchor details")
    current = embedding_rows(desk)[0]
    with desk["db"].session() as db, pytest.raises(IntegrityError):
        db.add(
            TicketEmbedding(
                organization_id=current.organization_id,
                ticket_id=current.ticket_id,
                model_version=MODEL_VERSION,
                content_digest="a" * 64,
                source_version=current.source_version,
                status="pending",
                is_current=True,
                available_at=utcnow(),
            )
        )
        db.commit()
