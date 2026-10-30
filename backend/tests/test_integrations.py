import hashlib
import hmac
import json
import time
from contextlib import ExitStack
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.customers.models import BackgroundJob, Notification, OutboxEvent
from app.integrations.models import IntegrationSource, WebhookReceipt
from app.main import create_app
from app.tickets.models import AuditEvent, Contact, Message, Ticket

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
def integration_desk(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        owner = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        agent = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        outsider = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        owner_response = write(
            owner,
            "POST",
            "/auth/register",
            {
                "email": "integration-owner@example.com",
                "password": "secure integration test passphrase",
                "full_name": "Integration Owner",
                "organization": {"name": "Integration Org", "slug": "integration-org"},
            },
        )
        assert owner_response.status_code == 201, owner_response.text
        agent_response = write(
            agent,
            "POST",
            "/auth/register",
            {
                "email": "integration-agent@example.com",
                "password": "secure integration test passphrase",
                "full_name": "Integration Agent",
                "organization": {"name": "Agent Org", "slug": "integration-agent-org"},
            },
        )
        assert agent_response.status_code == 201
        outsider_response = write(
            outsider,
            "POST",
            "/auth/register",
            {
                "email": "integration-outsider@example.com",
                "password": "secure integration test passphrase",
                "full_name": "Integration Outsider",
                "organization": {"name": "Outsider Org", "slug": "integration-outsider-org"},
            },
        )
        assert outsider_response.status_code == 201
        org = owner_response.json()["selected_organization_id"]
        root = f"/organizations/{org}"
        agent_member = write(
            owner,
            "POST",
            f"{root}/memberships",
            {"email": "integration-agent@example.com", "role": "agent"},
        )
        assert agent_member.status_code == 201, agent_member.text
        contact = write(
            owner,
            "POST",
            f"{root}/contacts",
            {"name": "Mapped Customer", "email": "mapped@example.com"},
        )
        assert contact.status_code == 201, contact.text
        source_response = write(
            owner,
            "POST",
            f"{root}/integrations",
            {"name": "Billing Platform", "contact_id": contact.json()["id"]},
        )
        assert source_response.status_code == 201, source_response.text
        yield {
            "settings": settings,
            "db": database,
            "owner": owner,
            "agent": agent,
            "outsider": outsider,
            "org": org,
            "root": root,
            "contact": contact.json(),
            "source": source_response.json()["source"],
            "credential": source_response.json()["credential"],
        }


def signed_request(desk, event_id, body, *, timestamp=None, signature_key=None):
    timestamp = str(timestamp if timestamp is not None else int(time.time()))
    signed = timestamp.encode() + b"." + event_id.encode() + b"." + body
    signature = hmac.new(
        (signature_key or desk["credential"]).encode(), signed, hashlib.sha256
    ).hexdigest()
    return desk["owner"].post(
        f"/api/integrations/{desk['source']['id']}/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-SupportFlow-Event-Id": event_id,
            "X-SupportFlow-Timestamp": timestamp,
            "X-SupportFlow-Signature": f"v1={signature}",
        },
    )


def event_body(value):
    return json.dumps(value, separators=(",", ":")).encode()


def test_source_credentials_are_one_time_and_manager_only(integration_desk):
    desk = integration_desk
    agent = desk["agent"]
    contact = write(
        agent,
        "POST",
        f"/organizations/{desk['org']}/contacts",
        {"name": "Agent Contact"},
    )
    assert contact.status_code == 201
    assert (
        write(
            agent,
            "POST",
            f"{desk['root']}/integrations",
            {"name": "Forbidden", "contact_id": contact.json()["id"]},
        ).status_code
        == 403
    )
    listed = agent.get(f"/api{desk['root']}/integrations")
    assert listed.status_code == 200
    assert desk["credential"] not in listed.text
    source = desk["source"]
    rotated = write(
        desk["owner"],
        "POST",
        f"{desk['root']}/integrations/{source['id']}/rotate-credential",
    )
    assert rotated.status_code == 200
    assert rotated.json()["source"]["credential_version"] == 2
    original_body = event_body({"type": "ticket.created", "title": "Billing", "message": "Failed"})
    assert signed_request(desk, "rotated", original_body).status_code == 401
    new_credential = rotated.json()["credential"]
    assert (
        write(
            desk["owner"],
            "PATCH",
            f"{desk['root']}/integrations/{source['id']}",
            {"enabled": False},
        ).status_code
        == 200
    )
    assert (
        signed_request(
            desk, "disabled-event", original_body, signature_key=new_credential
        ).status_code
        == 404
    )
    assert (
        write(
            desk["owner"], "PATCH", f"{desk['root']}/integrations/{source['id']}", {"enabled": True}
        ).status_code
        == 200
    )
    accepted = signed_request(desk, "rotated", original_body, signature_key=new_credential)
    assert accepted.status_code == 201
    assert (
        write(desk["owner"], "DELETE", f"{desk['root']}/integrations/{source['id']}").status_code
        == 204
    )
    assert (
        signed_request(
            desk, "after-revocation", original_body, signature_key=new_credential
        ).status_code
        == 404
    )


def test_signed_ticket_creation_sla_and_duplicate_receipt(integration_desk):
    desk = integration_desk
    targets = {
        f"{priority}_{kind}_seconds": 3600
        for priority in ["low", "normal", "high", "urgent"]
        for kind in ["response", "resolution"]
    }
    assert (
        write(
            desk["owner"],
            "PUT",
            f"{desk['root']}/sla/policy",
            {"name": "Default", "active": True, **targets},
        ).status_code
        == 200
    )
    body = event_body(
        {
            "type": "ticket.created",
            "title": "Payment rejected",
            "message": "Card payment failed",
            "priority": "urgent",
        }
    )
    first = signed_request(desk, "event-create-1", body)
    duplicate = signed_request(desk, "event-create-1", body)
    assert first.status_code == duplicate.status_code == 201
    assert first.json()["ticket_id"] == duplicate.json()["ticket_id"]
    assert duplicate.headers["x-supportflow-duplicate"] == "true"
    with desk["db"].session() as db:
        ticket = db.get(Ticket, first.json()["ticket_id"])
        assert ticket is not None
        assert str(ticket.created_by_integration_source_id) == desk["source"]["id"]
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert db.scalar(select(func.count()).select_from(WebhookReceipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.audience == "staff")
            )
            == 2
        )
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 2
        message = db.scalar(select(Message).where(Message.ticket_id == ticket.id))
        assert message and str(message.author_integration_source_id) == desk["source"]["id"]
        assert message.author_membership_id is None and message.visibility == "public"
        assert ticket.first_response_at is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        from app.sla.models import TicketSla

        sla = db.scalar(select(TicketSla).where(TicketSla.ticket_id == ticket.id))
        assert sla and sla.snapshot_priority == "urgent"
    changed = event_body(
        {"type": "ticket.created", "title": "Other request", "message": "Changed content"}
    )
    conflict = signed_request(desk, "event-create-1", changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "event_id_reused"


def test_signature_validation_message_visibility_and_receipts(integration_desk):
    desk = integration_desk
    bad_body = event_body({"type": "ticket.created", "title": "x", "message": "y"})
    assert (
        signed_request(desk, "bad-signature", bad_body, signature_key="wrong-key").status_code
        == 401
    )
    assert signed_request(desk, "expired", bad_body, timestamp=1).status_code == 401
    assert signed_request(desk, "malformed", b"{not-json").status_code == 400
    assert signed_request(desk, "malformed-time", bad_body, timestamp="tomorrow").status_code == 400
    assert signed_request(desk, "oversized", b" " * 70000).status_code == 413
    missing_signature = desk["owner"].post(
        f"/api/integrations/{desk['source']['id']}/events",
        content=bad_body,
        headers={
            "X-SupportFlow-Event-Id": "missing-signature",
            "X-SupportFlow-Timestamp": str(int(time.time())),
        },
    )
    assert missing_signature.status_code == 422
    unknown_source = desk["owner"].post(
        f"/api/integrations/{uuid4()}/events",
        content=bad_body,
        headers={
            "X-SupportFlow-Event-Id": "unknown-source",
            "X-SupportFlow-Timestamp": str(int(time.time())),
            "X-SupportFlow-Signature": "v1=" + ("0" * 64),
        },
    )
    assert unknown_source.status_code == 404
    unexpected = event_body(
        {
            "type": "ticket.created",
            "title": "Fake tenant",
            "message": "x",
            "organization_id": desk["org"],
        }
    )
    assert signed_request(desk, "unexpected-field", unexpected).status_code == 422
    created = signed_request(desk, "for-message", bad_body)
    assert created.status_code == 201
    ticket_id = created.json()["ticket_id"]
    reply_body = event_body(
        {"type": "ticket.public_message", "ticket_id": ticket_id, "message": "Customer update"}
    )
    reply = signed_request(desk, "event-message-1", reply_body)
    assert reply.status_code == 201
    resolved = write(
        desk["owner"],
        "PATCH",
        f"{desk['root']}/tickets/{ticket_id}",
        {"expected_version": 2, "status": "resolved"},
    )
    assert resolved.status_code == 200
    reopen_body = event_body(
        {"type": "ticket.public_message", "ticket_id": ticket_id, "message": "It failed again."}
    )
    reopened = signed_request(desk, "event-message-reopen", reopen_body)
    assert reopened.status_code == 201
    with desk["db"].session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.audience == "staff")
            )
            == 6
        )
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 6
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 6
        ticket_row = db.get(Ticket, ticket_id)
        assert ticket_row and ticket_row.status == "open"
        assert ticket_row.first_resolved_at is not None and ticket_row.resolved_at is None
    notes = write(
        desk["owner"],
        "POST",
        f"{desk['root']}/tickets/{ticket_id}/notes",
        {"expected_version": 4, "body": "Secret internal note"},
    )
    assert notes.status_code == 201
    messages = desk["owner"].get(f"/api{desk['root']}/tickets/{ticket_id}/public-conversation")
    assert messages.status_code == 200
    assert "Secret internal note" not in messages.text
    resolved_again = write(
        desk["owner"],
        "PATCH",
        f"{desk['root']}/tickets/{ticket_id}",
        {"expected_version": 5, "status": "resolved"},
    )
    assert resolved_again.status_code == 200
    closed = write(
        desk["owner"],
        "PATCH",
        f"{desk['root']}/tickets/{ticket_id}",
        {"expected_version": 6, "status": "closed"},
    )
    assert closed.status_code == 200
    closed_reply = signed_request(
        desk,
        "closed-ticket-reply",
        event_body(
            {"type": "ticket.public_message", "ticket_id": ticket_id, "message": "Late reply"}
        ),
    )
    assert closed_reply.status_code == 422
    receipt_page = desk["owner"].get(f"/api{desk['root']}/integrations/receipts")
    assert receipt_page.status_code == 200
    assert receipt_page.json()["total"] == 4
    assert "Card" not in receipt_page.text
    assert desk["outsider"].get(f"/api{desk['root']}/integrations/receipts").status_code == 404


def test_bound_contact_cannot_read_other_tenants_ticket(integration_desk):
    desk = integration_desk
    local_contact = write(
        desk["owner"],
        "POST",
        f"{desk['root']}/contacts",
        {"name": "A different local contact"},
    )
    local_ticket = write(
        desk["owner"],
        "POST",
        f"{desk['root']}/tickets",
        {
            "requester_contact_id": local_contact.json()["id"],
            "title": "Different local request",
            "initial_message": "This contact is not mapped to the source.",
        },
    )
    assert local_ticket.status_code == 201
    local_reply = signed_request(
        desk,
        "cross-contact-message",
        event_body(
            {
                "type": "ticket.public_message",
                "ticket_id": local_ticket.json()["id"],
                "message": "Wrong mapped contact",
            }
        ),
    )
    assert local_reply.status_code == 404
    other_root = (
        f"/organizations/{desk['outsider'].get('/api/auth/me').json()['selected_organization_id']}"
    )
    contact = write(
        desk["outsider"], "POST", f"{other_root}/contacts", {"name": "Outside customer"}
    )
    ticket = write(
        desk["outsider"],
        "POST",
        f"{other_root}/tickets",
        {
            "requester_contact_id": contact.json()["id"],
            "title": "Outside ticket",
            "initial_message": "Private request",
        },
    )
    assert ticket.status_code == 201
    body = event_body(
        {
            "type": "ticket.public_message",
            "ticket_id": ticket.json()["id"],
            "message": "Unauthorized access attempt",
        }
    )
    denied = signed_request(desk, "cross-org-message", body)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"
    rejected = desk["owner"].get(f"/api{desk['root']}/integrations/receipts?status=rejected")
    assert rejected.json()["items"][0]["status"] == "rejected"


def test_concurrent_duplicate_delivery_is_atomic(integration_desk):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    desk = integration_desk
    event_id = "concurrent-event"
    body = event_body({"type": "ticket.created", "title": "Concurrent", "message": "One ticket"})
    gate = Barrier(2)

    def send():
        gate.wait()
        return signed_request(desk, event_id, body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send(), range(2)))
    assert [result.status_code for result in results] == [201, 201]
    assert len({result.json()["ticket_id"] for result in results}) == 1
    with desk["db"].session() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1
        assert db.scalar(select(func.count()).select_from(WebhookReceipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.audience == "staff")
            )
            == 2
        )


def test_contact_relationship_is_enforced_by_database(integration_desk):
    from app.identity.models import Organization

    desk = integration_desk
    with desk["db"].session() as db:
        other = Organization(name="Second", slug="second-org")
        db.add(other)
        db.flush()
        other_contact = Contact(organization_id=other.id, name="Other", email=None)
        db.add(other_contact)
        db.flush()
        invalid = IntegrationSource(
            organization_id=desk["org"],
            contact_id=other_contact.id,
            name="Invalid cross tenant",
            enabled=True,
            secret_ciphertext="encrypted",
        )
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(invalid)
                db.flush()
