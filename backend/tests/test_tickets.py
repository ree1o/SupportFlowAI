from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.errors import AppError
from app.identity.models import Membership, Organization
from app.identity.security import SESSION_COOKIE, token_hash, utcnow
from app.identity.service import authenticate
from app.main import create_app
from app.sla.models import TicketSla
from app.sla.service import _status, scan_deadlines
from app.tickets import service
from app.tickets.models import AuditEvent, Category, Contact, Message, Ticket
from app.tickets.schemas import CategoryCreate, TicketCreate, TicketUpdate

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def write(client, method, path, data=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method, f"/api{path}", json=data, headers={"Origin": ORIGIN, "X-CSRF-Token": token}
    )


@pytest.fixture
def desk(postgres):
    settings, database, alembic = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
            for name in ["owner", "admin", "agent", "outsider"]
        }
        accounts = {}
        for name, client in clients.items():
            response = write(
                client,
                "POST",
                "/auth/register",
                {
                    "email": f"{name}@example.com",
                    "password": "a synthetic test passphrase",
                    "full_name": name.title(),
                    "organization": {"name": name.title(), "slug": f"{name}-org"},
                },
            )
            assert response.status_code == 201, response.text
            accounts[name] = response.json()
        org = accounts["owner"]["selected_organization_id"]
        root = f"/organizations/{org}"
        members = {"owner": clients["owner"].get(f"/api{root}/memberships").json()[0]["id"]}
        for name, role in [("admin", "administrator"), ("agent", "agent")]:
            response = write(
                clients["owner"],
                "POST",
                f"{root}/memberships",
                {"email": f"{name}@example.com", "role": role},
            )
            assert response.status_code == 201
            members[name] = response.json()["id"]
        requester = write(
            clients["owner"],
            "POST",
            f"{root}/contacts",
            {"name": "Customer One", "email": "CUSTOMER@example.com"},
        )
        assert requester.status_code == 201, requester.text
        category = write(clients["owner"], "POST", f"{root}/categories", {"name": "Billing"})
        assert category.status_code == 201, category.text
        yield {
            "clients": clients,
            "db": database,
            "alembic": alembic,
            "org": org,
            "root": root,
            "members": members,
            "accounts": accounts,
            "contact": requester.json(),
            "category": category.json(),
        }


def create_ticket(desk, client="owner", **fields):
    return write(
        desk["clients"][client],
        "POST",
        f"{desk['root']}/tickets",
        {
            "requester_contact_id": desk["contact"]["id"],
            "title": "Checkout payment failed",
            "initial_message": "Payment times out during checkout.",
            **fields,
        },
    )


def edit(desk, ticket, client="owner", **fields):
    return write(
        desk["clients"][client],
        "PATCH",
        f"{desk['root']}/tickets/{ticket['id']}",
        {
            "expected_version": ticket["version"],
            **fields,
        },
    )


def sla_targets(**overrides):
    values = {
        f"{priority}_{kind}_seconds": seconds
        for priority in ["low", "normal", "high", "urgent"]
        for kind, seconds in [("response", 3600), ("resolution", 86400)]
    }
    return {**values, **overrides}


def test_sla_policy_roles_snapshot_and_priority_history(desk):
    owner, agent, outsider = [desk["clients"][key] for key in ["owner", "agent", "outsider"]]
    path = f"{desk['root']}/sla/policy"
    body = {"name": "Support targets", "active": True, **sla_targets(high_response_seconds=120)}
    created = write(owner, "PUT", path, body)
    assert created.status_code == 200, created.text
    assert created.json()["current_version"] == 1
    assert created.json()["targets"]["high_response_seconds"] == 120
    assert write(agent, "PUT", path, body).status_code == 403
    assert outsider.get(f"/api{path}").status_code == 404

    ticket_response = create_ticket(desk, priority="high")
    assert ticket_response.status_code == 201, ticket_response.text
    ticket = ticket_response.json()
    assert ticket["sla"]["policy_version"] == 1
    assert ticket["sla"]["snapshot_priority"] == "high"
    assert ticket["sla"]["response_target_seconds"] == 120

    changed = write(
        owner,
        "PUT",
        path,
        {
            "name": "Support targets",
            "active": True,
            "expected_version": 1,
            **sla_targets(high_response_seconds=240),
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["current_version"] == 2
    assert changed.json()["targets"]["high_response_seconds"] == 240
    assert (
        owner.get(f"/api{desk['root']}/tickets/{ticket['id']}").json()["sla"][
            "response_target_seconds"
        ]
        == 120
    )
    assert write(owner, "PUT", path, body).status_code == 409
    deactivated = write(
        owner,
        "PUT",
        path,
        {
            "name": changed.json()["name"],
            "active": False,
            "expected_version": 2,
            **changed.json()["targets"],
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["current_version"] == 3
    assert (
        owner.get(f"/api{desk['root']}/tickets/{ticket['id']}").json()["sla"]["policy_version"] == 1
    )
    future_ticket = create_ticket(desk)
    assert future_ticket.status_code == 201, future_ticket.text
    assert future_ticket.json()["sla"] is None


def test_sla_deadline_equality_and_overdue_state():
    from datetime import UTC, datetime, timedelta

    due = datetime(2026, 1, 1, tzinfo=UTC)
    assert _status(due, due, None, due) == "met"
    assert _status(due, None, None, due) == "approaching"
    assert _status(due, None, None, due + timedelta(microseconds=1)) == "breached"
    assert _status(due, due + timedelta(microseconds=1), due, due) == "breached"


def test_sla_scan_is_idempotent_and_does_not_invent_legacy_snapshots(desk):
    from datetime import timedelta

    from app.identity.security import utcnow

    owner = desk["clients"]["owner"]
    legacy_ticket = create_ticket(desk).json()
    path = f"{desk['root']}/sla/policy"
    body = {"name": "Fast", "active": True, **sla_targets()}
    assert write(owner, "PUT", path, body).status_code == 200
    ticket = create_ticket(desk).json()
    with desk["db"].session() as db:
        record = db.scalar(select(TicketSla).where(TicketSla.ticket_id == UUID(ticket["id"])))
        assert record is not None
        record.response_due_at = utcnow() - timedelta(seconds=1)
        db.commit()
        assert scan_deadlines(db, utcnow()) == 1
        db.commit()
        assert scan_deadlines(db, utcnow()) == 0
        db.commit()
        assert (
            db.scalar(
                select(TicketSla.response_breached_at).where(
                    TicketSla.ticket_id == UUID(ticket["id"])
                )
            )
            is not None
        )
        from app.customers.models import Notification

        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.event_key == f"sla-response:{ticket['id']}")
            )
            == 2  # active Owner and Administrator; unassigned Agents are not escalated
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(TicketSla)
                .where(TicketSla.ticket_id == UUID(legacy_ticket["id"]))
            )
            == 0
        )


def test_concurrent_sla_scans_emit_one_breach(desk):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Barrier

    from app.identity.security import utcnow

    owner = desk["clients"]["owner"]
    path = f"{desk['root']}/sla/policy"
    assert (
        write(owner, "PUT", path, {"name": "Fast", "active": True, **sla_targets()}).status_code
        == 200
    )
    ticket = create_ticket(desk).json()
    with desk["db"].session() as db:
        record = db.scalar(select(TicketSla).where(TicketSla.ticket_id == UUID(ticket["id"])))
        assert record
        record.response_due_at = utcnow() - timedelta(seconds=1)
        db.commit()
    barrier = Barrier(2)

    def scan():
        with desk["db"].session() as db:
            barrier.wait()
            result = scan_deadlines(db, utcnow())
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scan(), range(2)))
    assert sum(results) == 1


def test_existing_ticket_without_active_policy_has_no_sla_snapshot(desk):
    ticket = create_ticket(desk)
    assert ticket.status_code == 201, ticket.text
    assert ticket.json()["sla"] is None


def test_operational_report_uses_eligible_sla_denominators(desk):
    from datetime import timedelta

    from app.identity.security import utcnow

    owner = desk["clients"]["owner"]
    policy_path = f"{desk['root']}/sla/policy"
    assert (
        write(
            owner, "PUT", policy_path, {"name": "Fast", "active": True, **sla_targets()}
        ).status_code
        == 200
    )
    answered = create_ticket(desk).json()
    reply = write(
        owner,
        "POST",
        f"{desk['root']}/tickets/{answered['id']}/replies",
        {
            "expected_version": answered["version"],
            "body": "A human agent is investigating.",
        },
    )
    assert reply.status_code == 201, reply.text
    unanswered = create_ticket(desk, title="Unanswered request").json()
    now = utcnow()
    with desk["db"].session() as db:
        answered_sla = db.scalar(
            select(TicketSla).where(TicketSla.ticket_id == UUID(answered["id"]))
        )
        unanswered_sla = db.scalar(
            select(TicketSla).where(TicketSla.ticket_id == UUID(unanswered["id"]))
        )
        assert answered_sla and unanswered_sla
        answered_sla.response_due_at = now + timedelta(days=1)
        answered_sla.resolution_due_at = now + timedelta(days=1)
        unanswered_sla.response_due_at = now - timedelta(seconds=1)
        unanswered_sla.resolution_due_at = now - timedelta(seconds=1)
        db.commit()
    report = owner.get(
        f"/api{desk['root']}/reports/operations",
        params={
            "period_start": (now - timedelta(days=1)).isoformat(),
            "period_end": (now + timedelta(days=1)).isoformat(),
        },
    )
    assert report.status_code == 200, report.text
    data = report.json()
    assert data["tickets_created"] == 2
    assert data["unresolved_tickets"] == 2
    assert data["response_sla_compliance_rate"] == 0.5
    assert data["resolution_sla_compliance_rate"] == 0.0


def test_contacts_crud_search_archive_and_scoping(desk):
    owner, agent, outsider = [desk["clients"][key] for key in ["owner", "agent", "outsider"]]
    path = f"{desk['root']}/contacts"
    id = desk["contact"]["id"]
    assert desk["contact"]["email"] == "customer@example.com"
    assert owner.get(f"/api{path}", params={"q": "CUSTOMER", "page_size": 1}).json()["total"] == 1
    assert owner.get(f"/api{path}/{id}").json()["name"] == "Customer One"
    assert write(agent, "PATCH", f"{path}/{id}", {"name": "Updated customer"}).status_code == 200
    assert owner.get(f"/api{path}/{id}").json()["email"] == "customer@example.com"
    assert write(agent, "DELETE", f"{path}/{id}").status_code == 403
    assert write(owner, "DELETE", f"{path}/{id}").status_code == 204
    assert owner.get(f"/api{path}").json()["total"] == 0
    assert owner.get(f"/api{path}", params={"include_inactive": True}).json()["total"] == 1
    assert create_ticket(desk).status_code == 422
    assert (
        write(owner, "PATCH", f"{path}/{id}", {"is_active": True, "email": None}).status_code == 200
    )
    assert owner.get(f"/api{path}/{id}").json()["email"] is None
    for method, suffix, data in [
        ("GET", "", None),
        ("GET", f"/{id}", None),
        ("POST", "", {"name": "intrusion"}),
        ("PATCH", f"/{id}", {"name": "intrusion"}),
        ("DELETE", f"/{id}", None),
    ]:
        response = (
            outsider.get(f"/api{path}{suffix}")
            if method == "GET"
            else write(outsider, method, f"{path}{suffix}", data)
        )
        assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{"name": " "}, {"name": "bad\x00name"}, {"name": "Good", "email": "bad"}, {"name": "x" * 101}],
)
def test_contact_validation(desk, payload):
    assert (
        write(desk["clients"]["agent"], "POST", f"{desk['root']}/contacts", payload).status_code
        == 422
    )


def test_categories_normalization_uniqueness_permissions_and_history(desk):
    owner, agent = desk["clients"]["owner"], desk["clients"]["agent"]
    path = f"{desk['root']}/categories"
    assert write(owner, "POST", path, {"name": "  BILLING  "}).status_code == 409
    assert write(owner, "POST", path, {"name": "Account  Access"}).status_code == 201
    assert write(owner, "POST", path, {"name": "account access"}).status_code == 409
    assert write(agent, "POST", path, {"name": "Forbidden"}).status_code == 403
    ticket = create_ticket(desk, category_id=desk["category"]["id"]).json()
    id = desk["category"]["id"]
    assert (
        write(owner, "PATCH", f"{path}/{id}", {"name": "Payments", "is_active": False}).status_code
        == 200
    )
    detail = owner.get(f"/api{desk['root']}/tickets/{ticket['id']}").json()
    assert detail["category_name"] == "Payments" and detail["category_active"] is False
    assert create_ticket(desk, category_id=id).status_code == 422
    assert edit(desk, ticket, priority="high").status_code == 200
    assert all(item["id"] != id for item in owner.get(f"/api{path}").json())
    assert any(
        item["id"] == id
        for item in owner.get(f"/api{path}", params={"include_inactive": True}).json()
    )


def test_ticket_creation_and_atomic_initial_history(desk):
    response = create_ticket(desk, category_id=desk["category"]["id"])
    assert response.status_code == 201, response.text
    ticket = response.json()
    assert ticket["version"] == 1 and ticket["status"] == "open" and ticket["priority"] == "normal"
    assert ticket["first_response_at"] is None and ticket["first_resolved_at"] is None
    assert ticket["created_by_membership_id"] == desk["members"]["owner"]
    client = desk["clients"]["owner"]
    messages = client.get(f"/api{desk['root']}/tickets/{ticket['id']}/messages").json()["items"]
    events = client.get(f"/api{desk['root']}/tickets/{ticket['id']}/activity").json()["items"]
    assert len(messages) == len(events) == 1
    assert messages[0]["visibility"] == "public" and messages[0]["author_name"] == "Owner"
    assert events[0]["action"] == "ticket.created"
    assert "Payment times out" not in str(events)


@pytest.mark.parametrize(
    "fields",
    [
        {"title": " "},
        {"title": "x" * 201},
        {"initial_message": " "},
        {"initial_message": "x" * 10001},
        {"priority": "critical"},
        {"status": "closed"},
        {"created_by_membership_id": str(uuid4())},
    ],
)
def test_ticket_creation_validation(desk, fields):
    assert create_ticket(desk, **fields).status_code == 422


def test_filter_sort_pagination_and_literal_search(desk):
    first = create_ticket(desk, title="100% invoice", priority="low").json()
    second = create_ticket(
        desk,
        title="Second",
        priority="urgent",
        category_id=desk["category"]["id"],
        assigned_membership_id=desk["members"]["agent"],
    ).json()
    third = create_ticket(desk, title="Third", priority="high").json()
    assert edit(desk, third, status="pending").status_code == 200
    owner = desk["clients"]["owner"]
    path = f"/api{desk['root']}/tickets"
    assert owner.get(path, params={"q": "%"}).json()["total"] == 1
    assert owner.get(path, params={"q": "customer@example.com"}).json()["total"] == 3
    assert owner.get(path, params={"status": "pending"}).json()["items"][0]["id"] == third["id"]
    assert (
        owner.get(
            path,
            params={
                "priority": "urgent",
                "category_id": desk["category"]["id"],
                "assigned_to": desk["members"]["agent"],
                "requester_id": desk["contact"]["id"],
            },
        ).json()["items"][0]["id"]
        == second["id"]
    )
    assert owner.get(path, params={"unassigned": True}).json()["total"] == 2
    pages = [
        owner.get(path, params={"page_size": 1, "page": page, "sort": "priority_desc"}).json()
        for page in [1, 2, 3]
    ]
    assert [p["items"][0]["id"] for p in pages] == [second["id"], third["id"], first["id"]]
    assert owner.get(path, params={"sort": "created_asc"}).json()["items"][0]["id"] == first["id"]
    assert owner.get(path, params={"sort": "updated_desc"}).json()["items"][0]["id"] == third["id"]
    for invalid in [
        {"page": 0},
        {"page_size": 101},
        {"sort": "sql-injection"},
        {"status": "invalid"},
    ]:
        assert owner.get(path, params=invalid).status_code == 422


def test_status_workflow_lifecycle_and_noop(desk):
    ticket = create_ticket(desk).json()
    assert edit(desk, ticket, status="closed").status_code == 422
    assert edit(desk, ticket, priority="normal").json()["version"] == 1
    ticket = edit(desk, ticket, status="pending").json()
    ticket = edit(desk, ticket, status="resolved").json()
    first = ticket["first_resolved_at"]
    assert ticket["resolved_at"] == first
    assert (
        write(
            desk["clients"]["owner"],
            "POST",
            f"{desk['root']}/tickets/{ticket['id']}/replies",
            {"expected_version": ticket["version"], "body": "must reopen"},
        ).status_code
        == 422
    )
    ticket = edit(desk, ticket, status="closed").json()
    assert ticket["closed_at"] and ticket["resolved_at"]
    assert edit(desk, ticket, priority="urgent").status_code == 422
    ticket = edit(desk, ticket, status="open").json()
    assert ticket["resolved_at"] is None and ticket["closed_at"] is None
    assert ticket["first_resolved_at"] == first


def test_agent_assignment_policy_and_manager_override(desk):
    ticket = create_ticket(desk, client="agent").json()
    ticket = edit(
        desk, ticket, client="agent", assigned_membership_id=desk["members"]["agent"]
    ).json()
    assert (
        edit(
            desk, ticket, client="agent", assigned_membership_id=desk["members"]["admin"]
        ).status_code
        == 403
    )
    ticket = edit(desk, ticket, client="agent", assigned_membership_id=None).json()
    ticket = edit(
        desk, ticket, client="admin", assigned_membership_id=desk["members"]["owner"]
    ).json()
    assert (
        desk["clients"]["agent"].get(f"/api{desk['root']}/tickets/{ticket['id']}").status_code
        == 200
    )
    assert edit(desk, ticket, client="agent", priority="urgent").status_code == 403
    assert (
        write(
            desk["clients"]["agent"],
            "POST",
            f"{desk['root']}/tickets/{ticket['id']}/notes",
            {"expected_version": ticket["version"], "body": "unauthorized"},
        ).status_code
        == 403
    )
    assert (
        create_ticket(
            desk, client="agent", assigned_membership_id=desk["members"]["owner"]
        ).status_code
        == 403
    )
    ticket = edit(desk, ticket, client="admin", priority="high").json()
    assert ticket["priority"] == "high"


def test_inactive_assignment_target_and_disabled_actor(desk):
    owner = desk["clients"]["owner"]
    member_path = f"{desk['root']}/memberships/{desk['members']['agent']}"
    ticket = create_ticket(desk, assigned_membership_id=desk["members"]["agent"]).json()
    assert write(owner, "DELETE", member_path).status_code == 204
    assert create_ticket(desk, assigned_membership_id=desk["members"]["agent"]).status_code == 404
    assert desk["clients"]["agent"].get(f"/api{desk['root']}/tickets").status_code == 404
    assert (
        owner.get(f"/api{desk['root']}/tickets/{ticket['id']}").json()["assignee_active"] is False
    )
    assert edit(desk, ticket, assigned_membership_id=None).status_code == 200


@pytest.mark.parametrize("model", [Contact, Category])
def test_new_references_refresh_preloaded_active_state(desk, model):
    key = "contact" if model is Contact else "category"
    raw = desk["clients"]["owner"].cookies.get(SESSION_COOKIE)
    with desk["db"].session() as db:
        auth = authenticate(db, raw)
        preloaded = db.get(model, UUID(desk[key]["id"]))
        assert preloaded.is_active
        with desk["db"].session() as other, other.begin():
            other.execute(update(model).where(model.id == preloaded.id).values(is_active=False))
        assert preloaded.is_active  # Deliberately retain stale ORM state before the service call.
        with pytest.raises(AppError) as error:
            service.create_ticket(
                db,
                auth,
                UUID(desk["org"]),
                TicketCreate(
                    requester_contact_id=UUID(desk["contact"]["id"]),
                    category_id=UUID(desk["category"]["id"]),
                    title="Must reject stale active reference",
                    initial_message="Customer request",
                ),
            )
        assert error.value.status == 422


def test_public_replies_internal_notes_and_visibility_projection(desk):
    ticket = create_ticket(desk).json()
    client = desk["clients"]["owner"]
    path = f"{desk['root']}/tickets/{ticket['id']}"
    ticket = write(
        client,
        "POST",
        f"{path}/notes",
        {"expected_version": 1, "body": "PRIVATE INTERNAL SENTINEL"},
    ).json()
    assert ticket["first_response_at"] is None
    ticket = write(
        client, "POST", f"{path}/replies", {"expected_version": 2, "body": "Public response"}
    ).json()
    assert ticket["first_response_at"]
    all_items = client.get(f"/api{path}/messages").json()["items"]
    assert [m["ticket_version"] for m in all_items] == [1, 2, 3]
    assert [m["visibility"] for m in all_items] == ["public", "internal", "public"]
    public = client.get(f"/api{path}/public-conversation").json()
    assert "PRIVATE INTERNAL SENTINEL" not in str(public)
    assert len(public["items"]) == 2
    assert set(public["items"][0]) == {"id", "author_name", "body", "created_at"}
    first_page = client.get(f"/api{path}/public-conversation", params={"limit": 1}).json()
    assert len(first_page["items"]) == 1
    assert (
        len(
            client.get(
                f"/api{path}/public-conversation",
                params={"after_version": first_page["next_after_version"]},
            ).json()["items"]
        )
        == 1
    )
    assert client.get(f"/api{path}/messages", params={"limit": 1}).json()["next_after_version"] == 1
    events = client.get(f"/api{path}/activity", params={"limit": 2}).json()
    assert events["next_after_version"] == 2
    assert [e["action"] for e in events["items"]] == ["ticket.created", "message.note_added"]
    assert "PRIVATE INTERNAL SENTINEL" not in str(events)


@pytest.mark.parametrize("body", ["", "  \n\t ", "x" * 10001, "null\x00byte"])
def test_message_validation(desk, body):
    ticket = create_ticket(desk).json()
    for endpoint in ["replies", "notes"]:
        assert (
            write(
                desk["clients"]["agent"],
                "POST",
                f"{desk['root']}/tickets/{ticket['id']}/{endpoint}",
                {"expected_version": 1, "body": body},
            ).status_code
            == 422
        )


def test_append_only_api_and_database_guards(desk):
    ticket = create_ticket(desk).json()
    owner = desk["clients"]["owner"]
    msg = owner.get(f"/api{desk['root']}/tickets/{ticket['id']}/messages").json()["items"][0]
    assert (
        write(
            owner, "DELETE", f"{desk['root']}/tickets/{ticket['id']}/messages/{msg['id']}"
        ).status_code
        == 404
    )
    for model in [Message, AuditEvent]:
        for statement in [delete(model), update(model).values(created_at=utcnow())]:
            with pytest.raises(DBAPIError), desk["db"].session() as db, db.begin():
                db.execute(statement)


def test_combined_changes_safe_audit_and_stale_conflict(desk):
    ticket = create_ticket(desk).json()
    current = edit(
        desk,
        ticket,
        status="pending",
        priority="urgent",
        category_id=desk["category"]["id"],
        assigned_membership_id=desk["members"]["agent"],
    ).json()
    assert current["version"] == 2
    stale = edit(desk, ticket, priority="low")
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_version": 2}
    events = (
        desk["clients"]["owner"]
        .get(f"/api{desk['root']}/tickets/{ticket['id']}/activity")
        .json()["items"]
    )
    assert len(events) == 5
    assert [e["sequence"] for e in events[1:]] == [0, 1, 2, 3]
    assert {e["action"] for e in events[1:]} == {
        "ticket.status_changed",
        "ticket.priority_changed",
        "ticket.category_changed",
        "ticket.assignment_changed",
    }
    # One version can contain several events; cursor paging must not split it.
    page = (
        desk["clients"]["owner"]
        .get(
            f"/api{desk['root']}/tickets/{ticket['id']}/activity",
            params={"after_version": 1, "limit": 1},
        )
        .json()
    )
    assert len(page["items"]) == 4 and page["next_after_version"] is None


def test_complete_status_transition_matrix_and_agent_closed_policy(desk):
    allowed = {
        "open": {"open", "pending", "resolved"},
        "pending": {"open", "pending", "resolved"},
        "resolved": {"open", "resolved", "closed"},
        "closed": {"open"},
    }
    for source, targets in allowed.items():
        for target in allowed:
            ticket = create_ticket(desk).json()
            if source == "pending":
                ticket = edit(desk, ticket, status="pending").json()
            elif source in {"resolved", "closed"}:
                ticket = edit(desk, ticket, status="resolved").json()
                if source == "closed":
                    ticket = edit(desk, ticket, status="closed").json()
            response = edit(desk, ticket, status=target)
            assert response.status_code == (200 if target in targets else 422)
    ticket = create_ticket(desk, client="agent").json()
    ticket = edit(desk, ticket, client="agent", status="resolved").json()
    ticket = edit(desk, ticket, client="agent", status="closed").json()
    assert edit(desk, ticket, client="agent", status="open").status_code == 422
    assert edit(desk, ticket, status="open", priority="high").status_code == 422
    assert edit(desk, ticket, status="open").status_code == 200


def test_required_strict_versions_and_unknown_references(desk):
    ticket = create_ticket(desk).json()
    path = f"{desk['root']}/tickets/{ticket['id']}"
    client = desk["clients"]["owner"]
    for version in [None, True, 0, -1, "1"]:
        payload = {"priority": "high"}
        if version is not None:
            payload["expected_version"] = version
        assert write(client, "PATCH", path, payload).status_code == 422
    for field in ["requester_contact_id", "category_id", "assigned_membership_id"]:
        assert create_ticket(desk, **{field: str(uuid4())}).status_code == 404
    unknown = f"{desk['root']}/tickets/{uuid4()}"
    for suffix in ["", "/messages", "/activity", "/public-conversation"]:
        assert client.get(f"/api{unknown}{suffix}").status_code == 404
    for suffix in ["replies", "notes"]:
        assert (
            write(
                client,
                "POST",
                f"{unknown}/{suffix}",
                {
                    "expected_version": 1,
                    "body": "Unavailable ticket",
                },
            ).status_code
            == 404
        )


@pytest.mark.parametrize("scenario", ["actor_demoted", "actor_disabled", "target_disabled"])
def test_ticket_writes_recheck_memberships_after_waiting_for_org_lock(desk, scenario):
    ticket = create_ticket(desk).json()
    loaded = Event()
    client = "owner" if scenario == "target_disabled" else "admin"
    raw = desk["clients"][client].cookies.get(SESSION_COOKIE)

    def waiting_write():
        with desk["db"].session() as db:
            auth = authenticate(db, raw)
            # Populate the identity map before the concurrent authorization change.
            db.scalars(
                select(Membership).where(Membership.organization_id == UUID(desk["org"]))
            ).all()
            loaded.set()
            try:
                if scenario == "actor_demoted":
                    service.create_category(
                        db, auth, UUID(desk["org"]), CategoryCreate(name="Denied")
                    )
                else:
                    service.update_ticket(
                        db,
                        auth,
                        UUID(desk["org"]),
                        UUID(ticket["id"]),
                        TicketUpdate(
                            expected_version=1,
                            assigned_membership_id=UUID(desk["members"]["agent"]),
                        ),
                    )
                return 200
            except AppError as error:
                return error.status

    with ThreadPoolExecutor(max_workers=1) as pool, desk["db"].session() as db:
        db.scalar(
            select(Organization).where(Organization.id == UUID(desk["org"])).with_for_update()
        )
        future = pool.submit(waiting_write)
        assert loaded.wait(timeout=10)
        target = db.get(
            Membership, UUID(desk["members"]["agent" if scenario == "target_disabled" else "admin"])
        )
        if scenario == "actor_demoted":
            target.role = "agent"
        else:
            target.is_active = False
        db.commit()
        assert future.result(timeout=10) == (403 if scenario == "actor_demoted" else 404)
    with desk["db"].session() as db:
        assert db.get(Ticket, UUID(ticket["id"])).version == 1


@pytest.mark.parametrize("field", ["status", "assigned_membership_id"])
def test_concurrent_independent_sessions_reject_lost_updates(desk, field):
    ticket = create_ticket(desk).json()
    barrier = Barrier(2)
    raw = desk["clients"]["owner"].cookies.get(SESSION_COOKIE)
    values = (
        ["pending", "resolved"]
        if field == "status"
        else [desk["members"]["agent"], desk["members"]["admin"]]
    )

    def mutate(value):
        with desk["db"].session() as db:
            auth = authenticate(db, raw)
            db.scalar(select(Ticket).where(Ticket.id == UUID(ticket["id"])))
            barrier.wait(timeout=10)
            try:
                service.update_ticket(
                    db,
                    auth,
                    UUID(desk["org"]),
                    UUID(ticket["id"]),
                    TicketUpdate(expected_version=1, **{field: value}),
                )
                return 200
            except AppError as error:
                return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, value) for value in values]
        assert sorted(f.result(timeout=15) for f in futures) == [200, 409]
    with desk["db"].session() as db:
        assert db.get(Ticket, UUID(ticket["id"])).version == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.ticket_id == UUID(ticket["id"]))
            )
            == 2
        )


@pytest.mark.parametrize("operation", ["create", "update", "reply", "note"])
def test_audit_failure_rolls_back_the_whole_transaction(desk, monkeypatch, operation):
    ticket = create_ticket(desk).json()
    with desk["db"].session() as db:
        before = tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in [Ticket, Message, AuditEvent]
        )

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(service, "audit", fail)
    if operation == "create":
        response = create_ticket(desk)
    elif operation == "update":
        response = edit(desk, ticket, priority="urgent")
    else:
        response = write(
            desk["clients"]["owner"],
            "POST",
            f"{desk['root']}/tickets/{ticket['id']}/"
            f"{'replies' if operation == 'reply' else 'notes'}",
            {"expected_version": 1, "body": "must roll back"},
        )
    assert response.status_code == 500
    with desk["db"].session() as db:
        assert (
            tuple(
                db.scalar(select(func.count()).select_from(model))
                for model in [Ticket, Message, AuditEvent]
            )
            == before
        )
        record = db.get(Ticket, UUID(ticket["id"]))
        assert (
            record.version == 1 and record.priority == "normal" and record.first_response_at is None
        )


def test_cross_tenant_api_reads_writes_and_references(desk):
    ticket = create_ticket(desk).json()
    owner, outsider = desk["clients"]["owner"], desk["clients"]["outsider"]
    path = f"{desk['root']}/tickets"
    for suffix in [
        "",
        f"/{ticket['id']}",
        f"/{ticket['id']}/messages",
        f"/{ticket['id']}/public-conversation",
        f"/{ticket['id']}/activity",
    ]:
        assert outsider.get(f"/api{path}{suffix}").status_code == 404
    assert create_ticket(desk, client="outsider").status_code == 404
    assert edit(desk, ticket, client="outsider", priority="urgent").status_code == 404
    assert (
        write(
            outsider,
            "POST",
            f"{path}/{ticket['id']}/notes",
            {"expected_version": 1, "body": "unauthorized"},
        ).status_code
        == 404
    )
    other_root = f"/organizations/{desk['accounts']['outsider']['selected_organization_id']}"
    other_contact = write(outsider, "POST", f"{other_root}/contacts", {"name": "Other"}).json()
    other_category = write(outsider, "POST", f"{other_root}/categories", {"name": "Other"}).json()
    other_member = outsider.get(f"/api{other_root}/memberships").json()[0]
    for fields in [
        {"requester_contact_id": other_contact["id"]},
        {"category_id": other_category["id"]},
        {"assigned_membership_id": other_member["id"]},
    ]:
        assert create_ticket(desk, **fields).status_code == 404
    assert edit(desk, ticket, category_id=other_category["id"]).status_code == 404
    assert edit(desk, ticket, assigned_membership_id=other_member["id"]).status_code == 404
    for suffix in [f"/categories/{other_category['id']}", f"/contacts/{other_contact['id']}"]:
        assert (
            write(owner, "PATCH", f"{desk['root']}{suffix}", {"name": "intrusion"}).status_code
            == 404
        )


def test_database_composite_constraints_block_cross_tenant_links(desk):
    ticket = create_ticket(desk).json()
    org, id = UUID(desk["org"]), UUID(ticket["id"])
    other_org = UUID(desk["accounts"]["outsider"]["selected_organization_id"])
    other_user = UUID(desk["accounts"]["outsider"]["user"]["id"])
    with desk["db"].session() as db, db.begin():
        other_contact = Contact(organization_id=other_org, name="Other")
        other_category = Category(organization_id=other_org, name="Other")
        db.add_all([other_contact, other_category])
        db.flush()
        contact_id, category_id = other_contact.id, other_category.id
        other_member = db.scalar(
            select(Membership.id).where(
                Membership.organization_id == other_org, Membership.user_id == other_user
            )
        )
    for values in [
        {"requester_contact_id": contact_id},
        {"category_id": category_id},
        {"assigned_membership_id": other_member},
        {"created_by_membership_id": other_member},
    ]:
        with pytest.raises(IntegrityError), desk["db"].session() as db, db.begin():
            db.execute(update(Ticket).where(Ticket.id == id).values(**values))
    for record in [
        Message(
            organization_id=other_org,
            ticket_id=id,
            author_membership_id=other_member,
            author_name="Outsider",
            visibility="public",
            body="bad",
            ticket_version=20,
        ),
        Message(
            organization_id=org,
            ticket_id=id,
            author_membership_id=other_member,
            author_name="Outsider",
            visibility="public",
            body="bad",
            ticket_version=20,
        ),
        AuditEvent(
            organization_id=other_org,
            ticket_id=id,
            actor_membership_id=other_member,
            actor_name="Outsider",
            action="ticket.created",
            changes={},
            ticket_version=20,
            sequence=0,
        ),
        AuditEvent(
            organization_id=org,
            ticket_id=id,
            actor_membership_id=other_member,
            actor_name="Outsider",
            action="ticket.created",
            changes={},
            ticket_version=20,
            sequence=0,
        ),
    ]:
        with pytest.raises(IntegrityError), desk["db"].session() as db, db.begin():
            db.add(record)
            db.flush()
    with pytest.raises(IntegrityError), desk["db"].session() as db, db.begin():
        db.add(Category(organization_id=org, name="  BILLING  "))
        db.flush()
    # An audit event cannot identify a different ticket's message, even in the same tenant.
    second = create_ticket(desk).json()
    with desk["db"].session() as db:
        message_id = db.scalar(select(Message.id).where(Message.ticket_id == id))
    with pytest.raises(IntegrityError) as error, desk["db"].session() as db, db.begin():
        db.add(
            AuditEvent(
                organization_id=org,
                ticket_id=UUID(second["id"]),
                message_id=message_id,
                actor_membership_id=UUID(desk["members"]["owner"]),
                actor_name="Owner",
                action="message.public_posted",
                changes={},
                ticket_version=20,
                sequence=0,
            )
        )
        db.flush()
    assert error.value.orig.diag.constraint_name == "fk_audit_message"


def test_mutations_require_csrf_and_session(desk):
    ticket = create_ticket(desk).json()
    owner = desk["clients"]["owner"]
    for method, path, payload in [
        ("POST", "/contacts", {"name": "X"}),
        ("PATCH", f"/contacts/{desk['contact']['id']}", {"name": "X"}),
        ("DELETE", f"/contacts/{desk['contact']['id']}", None),
        ("POST", "/categories", {"name": "X"}),
        ("PATCH", f"/categories/{desk['category']['id']}", {"name": "X"}),
        ("POST", "/tickets", {}),
        ("PATCH", f"/tickets/{ticket['id']}", {"expected_version": 1, "priority": "urgent"}),
        ("POST", f"/tickets/{ticket['id']}/replies", {"expected_version": 1, "body": "x"}),
        ("POST", f"/tickets/{ticket['id']}/notes", {"expected_version": 1, "body": "x"}),
    ]:
        assert (
            owner.request(
                method, f"/api{desk['root']}{path}", json=payload, headers={"Origin": ORIGIN}
            ).status_code
            == 403
        )
    owner.cookies.clear()
    for suffix in [
        "/tickets",
        "/contacts",
        "/categories",
        f"/tickets/{ticket['id']}/public-conversation",
    ]:
        assert owner.get(f"/api{desk['root']}{suffix}").status_code == 401


def test_m2_upgrade_preserves_users_sessions_and_memberships(desk):
    raw = desk["clients"]["owner"].cookies.get(SESSION_COOKIE)
    command.downgrade(desk["alembic"], "0002_identity")
    with desk["db"].session() as db:
        user = (
            db.execute(
                text("SELECT id, email FROM users WHERE email = :email"),
                {"email": "owner@example.com"},
            )
            .mappings()
            .one()
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM sessions WHERE user_id = :user_id AND token_hash = :hash"
                ),
                {"user_id": user["id"], "hash": token_hash(raw)},
            )
            == 1
        )
        assert db.scalar(text("SELECT count(*) FROM memberships")) == 6
        assert user["email"] == "owner@example.com"
    command.upgrade(desk["alembic"], "head")
    assert "tickets" in inspect(desk["db"].engine).get_table_names()
    assert desk["clients"]["owner"].get("/api/auth/me").status_code == 200
    command.check(desk["alembic"])
