from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.customers.models import CustomerAccess
from app.identity.models import User
from app.identity.security import SESSION_COOKIE, utcnow
from app.identity.service import issue_session
from app.incidents import service
from app.incidents.models import (
    IncidentTicket,
    RadarProposal,
    RadarRun,
    RadarSuppression,
)
from app.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"
PASSWORD = "synthetic incident test passphrase"


def write(client, method, path, data=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )


@pytest.fixture
def incident_desk(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
            for name in ("owner", "admin", "agent", "outsider", "customer")
        }
        accounts = {}
        for name in ("owner", "admin", "agent", "outsider"):
            response = write(
                clients[name],
                "POST",
                "/auth/register",
                {
                    "email": f"incident-{name}@example.com",
                    "password": PASSWORD,
                    "full_name": name.title(),
                    "organization": {
                        "name": name.title(),
                        "slug": f"incident-{name}",
                    },
                },
            )
            assert response.status_code == 201, response.text
            accounts[name] = response.json()
        org = accounts["owner"]["selected_organization_id"]
        root = f"/organizations/{org}"
        memberships = {"owner": clients["owner"].get(f"/api{root}/memberships").json()[0]["id"]}
        for name, role in (("admin", "administrator"), ("agent", "agent")):
            response = write(
                clients["owner"],
                "POST",
                f"{root}/memberships",
                {"email": f"incident-{name}@example.com", "role": role},
            )
            assert response.status_code == 201, response.text
            memberships[name] = response.json()["id"]
        contacts = []
        for index in range(1, 5):
            response = write(
                clients["owner"],
                "POST",
                f"{root}/contacts",
                {"name": f"Incident Customer {index}", "email": f"customer{index}@example.com"},
            )
            assert response.status_code == 201
            contacts.append(response.json())
        category = write(clients["owner"], "POST", f"{root}/categories", {"name": "Payments"})
        assert category.status_code == 201
        yield {
            "settings": settings,
            "db": database,
            "clients": clients,
            "accounts": accounts,
            "org": org,
            "root": root,
            "memberships": memberships,
            "contacts": contacts,
            "category": category.json(),
        }


def create_ticket(
    desk,
    index=0,
    *,
    title="Payment gateway timeout",
    body="Card payment times out at checkout.",
    category=True,
):
    return write(
        desk["clients"]["owner"],
        "POST",
        f"{desk['root']}/tickets",
        {
            "requester_contact_id": desk["contacts"][index]["id"],
            "title": title,
            "initial_message": body,
            "category_id": desk["category"]["id"] if category else None,
        },
    )


def create_pair(desk):
    first = create_ticket(desk, 0)
    second = create_ticket(
        desk,
        1,
        title="Checkout payment does not complete",
        body="Card authorization timeout while submitting order.",
    )
    assert first.status_code == second.status_code == 201
    return first.json(), second.json()


def test_incident_workflow_permissions_history_and_ticket_independence(incident_desk):
    desk = incident_desk
    owner, admin, agent, outsider = [
        desk["clients"][name] for name in ("owner", "admin", "agent", "outsider")
    ]
    first, second = create_pair(desk)
    before_statuses = [first["status"], second["status"]]
    created = write(
        owner,
        "POST",
        f"{desk['root']}/incidents",
        {
            "title": "Checkout payment degradation",
            "description": "Investigate reports of payment timeouts.",
            "severity": "sev2",
            "owner_membership_id": desk["memberships"]["admin"],
            "ticket_ids": [first["id"], second["id"]],
        },
    )
    assert created.status_code == 201, created.text
    incident = created.json()
    assert incident["status"] == "investigating"
    assert incident["ticket_count"] == 2
    assert incident["events"][0]["action"] == "incident.created"
    assert (
        write(
            agent,
            "POST",
            f"{desk['root']}/incidents",
            {"title": "No permission", "ticket_ids": [first["id"]]},
        ).status_code
        == 403
    )
    assert outsider.get(f"/api/organizations/{desk['org']}/incidents").status_code == 404

    note = write(
        agent,
        "POST",
        f"{desk['root']}/incidents/{incident['id']}/notes",
        {"expected_version": incident["version"], "body": "Checking payment gateway logs."},
    )
    assert note.status_code == 200, note.text
    incident = note.json()
    assert note.json()["events"][-1]["note"] == "Checking payment gateway logs."
    assert (
        write(
            agent,
            "PATCH",
            f"{desk['root']}/incidents/{incident['id']}",
            {"expected_version": incident["version"], "status": "monitoring"},
        ).status_code
        == 403
    )
    update = write(
        admin,
        "PATCH",
        f"{desk['root']}/incidents/{incident['id']}",
        {"expected_version": incident["version"], "status": "monitoring", "severity": "sev1"},
    )
    assert update.status_code == 200, update.text
    incident = update.json()
    assert incident["status"] == "monitoring" and incident["severity"] == "sev1"
    stale = write(
        owner,
        "PATCH",
        f"{desk['root']}/incidents/{incident['id']}",
        {"expected_version": 1, "status": "resolved"},
    )
    assert stale.status_code == 409
    resolved = write(
        owner,
        "PATCH",
        f"{desk['root']}/incidents/{incident['id']}",
        {"expected_version": incident["version"], "status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text
    reopened = write(
        owner,
        "PATCH",
        f"{desk['root']}/incidents/{incident['id']}",
        {"expected_version": resolved.json()["version"], "status": "investigating"},
    )
    assert reopened.status_code == 200
    assert [
        owner.get(f"/api{desk['root']}/tickets/{ticket_id}").json()["status"]
        for ticket_id in (first["id"], second["id"])
    ] == before_statuses


def test_safe_ticket_linking_and_database_tenant_constraints(incident_desk):
    desk = incident_desk
    owner = desk["clients"]["owner"]
    first, second = create_pair(desk)
    created = write(
        owner,
        "POST",
        f"{desk['root']}/incidents",
        {"title": "Payment issue", "ticket_ids": [first["id"], second["id"]]},
    )
    incident = created.json()
    third = create_ticket(
        desk, 2, title="Payment retry also times out", body="Card payment timeout during retry."
    ).json()
    linked = write(
        owner,
        "POST",
        f"{desk['root']}/incidents/{incident['id']}/tickets",
        {"expected_version": incident["version"], "ticket_id": third["id"]},
    )
    assert linked.status_code == 200, linked.text
    duplicate = write(
        owner,
        "POST",
        f"{desk['root']}/incidents/{incident['id']}/tickets",
        {"expected_version": linked.json()["version"], "ticket_id": third["id"]},
    )
    assert duplicate.status_code == 409
    outsider = desk["clients"]["outsider"]
    other_ticket = write(
        outsider,
        "POST",
        f"/organizations/{desk['accounts']['outsider']['selected_organization_id']}/contacts",
        {"name": "Other contact"},
    )
    assert other_ticket.status_code == 201
    other_org = desk["accounts"]["outsider"]["selected_organization_id"]
    other_created = write(
        outsider,
        "POST",
        f"/organizations/{other_org}/tickets",
        {
            "requester_contact_id": other_ticket.json()["id"],
            "title": "Other ticket",
            "initial_message": "Other tenant",
        },
    )
    assert other_created.status_code == 201
    cross = write(
        owner,
        "POST",
        f"{desk['root']}/incidents/{incident['id']}/tickets",
        {"expected_version": linked.json()["version"], "ticket_id": other_created.json()["id"]},
    )
    assert cross.status_code == 404
    current = linked.json()
    for ticket_id in [first["id"], second["id"]]:
        removed = owner.delete(
            f"/api{desk['root']}/incidents/{incident['id']}/tickets/{ticket_id}?expected_version={current['version']}",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": owner.get("/api/auth/csrf").json()["csrf_token"],
            },
        )
        assert removed.status_code == 200, removed.text
        current = removed.json()
    last_ticket = owner.delete(
        f"/api{desk['root']}/incidents/{incident['id']}/tickets/{third['id']}?expected_version={current['version']}",
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": owner.get("/api/auth/csrf").json()["csrf_token"],
        },
    )
    assert last_ticket.status_code == 422

    with pytest.raises(IntegrityError), desk["db"].session() as db, db.begin():
        db.add(
            IncidentTicket(
                organization_id=UUID(desk["org"]),
                incident_id=UUID(incident["id"]),
                ticket_id=UUID(other_created.json()["id"]),
                linked_by_membership_id=UUID(desk["memberships"]["owner"]),
            )
        )


def test_radar_proposal_rejection_change_confirmation_and_repeated_scans(
    incident_desk, monkeypatch
):
    desk = incident_desk
    owner = desk["clients"]["owner"]
    first, second = create_pair(desk)
    from app.incidents import api as incident_api

    monkeypatch.setattr(incident_api, "_dispatch_run", lambda *_args: None)

    def detect() -> list[dict]:
        queued = write(owner, "POST", f"{desk['root']}/radar/runs")
        assert queued.status_code == 202, queued.text
        with desk["db"].session() as db:
            service.process_detection_run(
                db, UUID(desk["org"]), UUID(queued.json()["id"]), desk["settings"]
            )
        return owner.get(f"/api{desk['root']}/radar/proposals?status=pending").json()["items"]

    proposals = detect()
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["ticket_ids"] == sorted([first["id"], second["id"]])
    assert "payment" in proposal["evidence"]["shared_terms"]
    assert proposal["evidence"]["ticket_refs"] == proposal["ticket_ids"]
    assert proposal["score"] > 0.3
    assert "root cause" in proposal["evidence"]["interpretation"]
    assert detect() == [proposal]  # Same evidence fingerprint yields no duplicate.

    rejected = write(
        owner,
        "POST",
        f"{desk['root']}/radar/proposals/{proposal['id']}/reject",
        {"reason": "Different symptoms despite shared payment terms."},
    )
    assert rejected.status_code == 200
    assert detect() == []
    with desk["db"].session() as db:
        count_before = db.scalar(select(func.count()).select_from(RadarProposal))
        assert db.scalar(select(func.count()).select_from(RadarSuppression)) == 1

    # Category is part of the detector fingerprint: a material category edit permits review again.
    changed = write(
        owner,
        "PATCH",
        f"{desk['root']}/tickets/{second['id']}",
        {"expected_version": second["version"], "category_id": None},
    )
    assert changed.status_code == 200
    changed_proposals = detect()
    assert len(changed_proposals) == 1
    assert changed_proposals[0]["id"] != proposal["id"]
    assert len(changed_proposals[0]["ticket_ids"]) == 2
    assert (
        write(
            desk["clients"]["agent"],
            "POST",
            f"{desk['root']}/radar/proposals/{changed_proposals[0]['id']}/confirm",
            {"ticket_ids": changed_proposals[0]["ticket_ids"]},
        ).status_code
        == 403
    )
    confirmed = write(
        owner,
        "POST",
        f"{desk['root']}/radar/proposals/{changed_proposals[0]['id']}/confirm",
        {
            "ticket_ids": changed_proposals[0]["ticket_ids"],
            "title": "Payment gateway incident",
            "severity": "sev2",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    incident = confirmed.json()["incident"]
    assert incident["ticket_count"] == 2
    duplicate = write(
        owner,
        "POST",
        f"{desk['root']}/radar/proposals/{changed_proposals[0]['id']}/confirm",
        {"ticket_ids": changed_proposals[0]["ticket_ids"]},
    )
    assert duplicate.status_code == 409
    assert detect() == []  # Confirmed group membership suppresses future identical groups.
    with desk["db"].session() as db:
        assert db.scalar(select(func.count()).select_from(RadarProposal)) == count_before + 1
        assert db.scalar(select(func.count()).select_from(RadarSuppression)) == 2


def test_customer_account_cannot_access_staff_incident_data(incident_desk):
    desk = incident_desk
    customer = desk["clients"]["customer"]
    with desk["db"].session() as db:
        with db.begin():
            user = User(
                email="incident-customer@example.com",
                full_name="Portal Customer",
                password_hash="unused-for-auth-test",
                email_verified_at=utcnow(),
            )
            db.add(user)
            db.flush()
            db.add(
                CustomerAccess(
                    organization_id=UUID(desk["org"]),
                    contact_id=UUID(desk["contacts"][0]["id"]),
                    user_id=user.id,
                    is_active=True,
                )
            )
            _session, raw = issue_session(db, user.id, desk["settings"], None)
        customer.cookies.set(SESSION_COOKIE, raw, path="/api")
    response = customer.get(f"/api{desk['root']}/incidents")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_worker_failure_is_recorded_and_retryable(incident_desk, monkeypatch):
    desk = incident_desk
    owner = desk["clients"]["owner"]
    create_pair(desk)
    from app.incidents import api as incident_api

    monkeypatch.setattr(incident_api, "_dispatch_run", lambda *_args: None)
    run_response = write(owner, "POST", f"{desk['root']}/radar/runs")
    run_id = UUID(run_response.json()["id"])
    original = service._candidate_rows
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("synthetic detector interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_candidate_rows", fail_once)
    with desk["db"].session() as db, pytest.raises(RuntimeError):
        service.process_detection_run(db, UUID(desk["org"]), run_id, desk["settings"])
    with desk["db"].session() as db:
        run = db.get(RadarRun, run_id)
        assert run is not None and run.status == "pending"
        assert run.attempts == 1 and run.failure_type == "RuntimeError"
        run.available_at = utcnow()
        db.commit()
        assert service.process_detection_run(db, UUID(desk["org"]), run_id, desk["settings"]) == 1
        run = db.get(RadarRun, run_id)
        assert run is not None and run.status == "completed"


def test_concurrent_detection_delivery_processes_run_once(incident_desk, monkeypatch):
    desk = incident_desk
    owner = desk["clients"]["owner"]
    create_pair(desk)
    from app.incidents import api as incident_api

    monkeypatch.setattr(incident_api, "_dispatch_run", lambda *_args: None)
    run = write(owner, "POST", f"{desk['root']}/radar/runs").json()
    run_id = UUID(run["id"])
    original = service._candidate_rows
    first_loaded = Event()
    resume_first = Event()

    def paused(db, org, settings):
        rows = original(db, org, settings)
        first_loaded.set()
        assert resume_first.wait(timeout=10)
        return rows

    monkeypatch.setattr(service, "_candidate_rows", paused)

    def run_once():
        with desk["db"].session() as db:
            return service.process_detection_run(db, UUID(desk["org"]), run_id, desk["settings"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_once)
        assert first_loaded.wait(timeout=10)
        second = pool.submit(run_once)
        assert second.result(timeout=10) == 0
        resume_first.set()
        assert first.result(timeout=10) == 1
    with desk["db"].session() as db:
        assert db.scalar(select(func.count()).select_from(RadarProposal)) == 1
