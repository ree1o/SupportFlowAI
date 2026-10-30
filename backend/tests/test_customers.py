import re
from contextlib import ExitStack
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.customers.models import (
    AccountToken,
    BackgroundJob,
    CustomerAccess,
    OutboxEvent,
)
from app.identity.models import User
from app.identity.security import utcnow
from app.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"
PASSWORD = "customer portal secure passphrase"


def write(client, method, path, data=None):
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )


def action_token(event: OutboxEvent) -> str:
    match = re.search(r"https?://\S+", event.payload["body"])
    assert match
    return parse_qs(urlsplit(match.group()).query)["token"][0]


@pytest.fixture
def setup(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        owner = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        outsider = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        customer = stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN))
        owner_auth = write(
            owner,
            "POST",
            "/auth/register",
            {
                "email": "portal-owner@example.com",
                "password": PASSWORD,
                "full_name": "Portal Owner",
                "organization": {"name": "Portal Org", "slug": "portal-org"},
            },
        )
        assert owner_auth.status_code == 201, owner_auth.text
        outsider_auth = write(
            outsider,
            "POST",
            "/auth/register",
            {
                "email": "other-staff@example.com",
                "password": PASSWORD,
                "full_name": "Other Staff",
                "organization": {"name": "Other Org", "slug": "other-org"},
            },
        )
        assert outsider_auth.status_code == 201
        org = owner_auth.json()["selected_organization_id"]
        contact = write(
            owner,
            "POST",
            f"/organizations/{org}/contacts",
            {"name": "Portal Customer", "email": "portal-customer@example.com"},
        )
        assert contact.status_code == 201
        yield {
            "settings": settings,
            "database": database,
            "owner": owner,
            "outsider": outsider,
            "customer": customer,
            "org": org,
            "contact": contact.json(),
        }


def create_verified_customer(setup):
    response = write(
        setup["owner"],
        "POST",
        "/customer/register",
        {
            "email": "portal-customer@example.com",
            "password": PASSWORD,
            "full_name": "Portal Customer",
            "organization_slug": "portal-org",
        },
    )
    assert response.status_code == 202
    with setup["database"].session() as db:
        user = db.scalar(select(User).where(User.email == "portal-customer@example.com"))
        assert user is not None and user.email_verified_at is None
        assert db.scalar(select(CustomerAccess.id).where(CustomerAccess.user_id == user.id)) is None
        token = action_token(
            db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("verify:%")))
        )
    assert write(setup["owner"], "POST", "/customer/verify", {"token": token}).status_code == 204
    login = write(
        setup["customer"],
        "POST",
        "/auth/login",
        {"email": "portal-customer@example.com", "password": PASSWORD},
    )
    assert login.status_code == 200
    return setup["customer"]


def test_customer_verification_ticket_conversation_privacy_and_tenant_isolation(setup):
    customer = create_verified_customer(setup)
    profile = customer.get("/api/customer/me")
    assert profile.status_code == 200
    assert [item["organization_id"] for item in profile.json()["organizations"]] == [setup["org"]]

    created = write(
        customer,
        "POST",
        f"/customer/organizations/{setup['org']}/tickets",
        {"title": "Customer checkout issue", "message": "Payment timed out."},
    )
    assert created.status_code == 201, created.text
    ticket = created.json()
    detail_path = f"/customer/organizations/{setup['org']}/tickets/{ticket['id']}"
    assert customer.get(f"/api{detail_path}").json()["messages"][0]["body"] == "Payment timed out."

    note = write(
        setup["owner"],
        "POST",
        f"/organizations/{setup['org']}/tickets/{ticket['id']}/notes",
        {"expected_version": ticket["version"], "body": "STAFF-PRIVATE diagnostic"},
    )
    assert note.status_code == 201, note.text
    staff_reply = write(
        setup["owner"],
        "POST",
        f"/organizations/{setup['org']}/tickets/{ticket['id']}/replies",
        {"expected_version": note.json()["version"], "body": "We are investigating."},
    )
    assert staff_reply.status_code == 201, staff_reply.text
    customer_detail = customer.get(f"/api{detail_path}")
    assert customer_detail.status_code == 200
    assert [item["body"] for item in customer_detail.json()["messages"]] == [
        "Payment timed out.",
        "We are investigating.",
    ]
    assert "STAFF-PRIVATE" not in customer_detail.text
    assert "activity" not in customer_detail.json()

    reply = write(
        customer,
        "POST",
        f"{detail_path}/replies",
        {"expected_version": staff_reply.json()["version"], "body": "Here is an update."},
    )
    assert reply.status_code == 201, reply.text
    stale = write(
        customer,
        "POST",
        f"{detail_path}/replies",
        {"expected_version": staff_reply.json()["version"], "body": "Stale reply."},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == reply.json()["version"]
    staff_notifications = setup["owner"].get(f"/api/organizations/{setup['org']}/notifications")
    assert staff_notifications.status_code == 200
    assert any("Customer replied" in item["title"] for item in staff_notifications.json())

    assert setup["outsider"].get(f"/api{detail_path}").status_code == 404
    assert (
        setup["outsider"]
        .get(f"/api/organizations/{setup['org']}/tickets/{ticket['id']}/activity")
        .status_code
        == 404
    )
    wrong_org = setup["outsider"].get("/api/customer/me")
    assert wrong_org.status_code == 404
    assert (
        customer.get(
            f"/api/customer/organizations/{setup['org']}/tickets/{ticket['id']}/activity"
        ).status_code
        == 404
    )
    access = setup["owner"].get(f"/api/organizations/{setup['org']}/customer-access")
    assert access.status_code == 200 and len(access.json()) == 1
    revoked = write(
        setup["owner"],
        "DELETE",
        f"/organizations/{setup['org']}/customer-access/{access.json()[0]['id']}",
    )
    assert revoked.status_code == 204
    assert customer.get(f"/api{detail_path}").status_code == 404
    reinvite = write(
        setup["owner"],
        "POST",
        f"/organizations/{setup['org']}/customer-access/invitations",
        {"contact_id": setup["contact"]["id"]},
    )
    assert reinvite.status_code == 202
    with setup["database"].session() as db:
        event = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.event_key.like("invite:%"))
            .order_by(OutboxEvent.created_at.desc())
        )
        token = action_token(event)
    accepted = write(customer, "POST", "/customer/invitations/accept", {"token": token})
    assert accepted.status_code == 200, accepted.text
    assert customer.get("/api/customer/me").status_code == 200


def test_customer_access_foreign_key_rejects_cross_tenant_contact(setup):
    other_org = setup["outsider"].get("/api/auth/me").json()["selected_organization_id"]
    other_contact = write(
        setup["outsider"],
        "POST",
        f"/organizations/{other_org}/contacts",
        {"name": "Other tenant contact", "email": "other-contact@example.com"},
    )
    assert other_contact.status_code == 201
    owner_user_id = setup["owner"].get("/api/auth/me").json()["user"]["id"]
    with pytest.raises(IntegrityError):
        with setup["database"].session() as db, db.begin():
            db.add(
                CustomerAccess(
                    organization_id=setup["org"],
                    contact_id=other_contact.json()["id"],
                    user_id=owner_user_id,
                )
            )


def test_closed_request_rejects_customer_replies(setup):
    customer = create_verified_customer(setup)
    ticket = write(
        customer,
        "POST",
        f"/customer/organizations/{setup['org']}/tickets",
        {"title": "Closed request", "message": "Please investigate."},
    ).json()
    path = f"/organizations/{setup['org']}/tickets/{ticket['id']}"
    resolved = write(
        setup["owner"],
        "PATCH",
        path,
        {"expected_version": ticket["version"], "status": "resolved"},
    )
    assert resolved.status_code == 200, resolved.text
    closed = write(
        setup["owner"],
        "PATCH",
        path,
        {"expected_version": resolved.json()["version"], "status": "closed"},
    )
    assert closed.status_code == 200, closed.text
    reply = write(
        customer,
        "POST",
        f"/customer/organizations/{setup['org']}/tickets/{ticket['id']}/replies",
        {"expected_version": closed.json()["version"], "body": "Reopen this please."},
    )
    assert reply.status_code == 422


def test_invitation_acceptance_requires_matching_account_and_is_single_use(setup):
    invitation = write(
        setup["owner"],
        "POST",
        f"/organizations/{setup['org']}/customer-access/invitations",
        {"contact_id": setup["contact"]["id"]},
    )
    assert invitation.status_code == 202
    with setup["database"].session() as db:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("invite:%")))
        token = action_token(event)
    accepted = write(
        setup["outsider"],
        "POST",
        "/customer/invitations/accept",
        {"token": token, "full_name": "Invited Customer", "password": PASSWORD},
    )
    assert accepted.status_code == 422  # An unrelated signed-in account cannot claim it.
    logout = write(setup["outsider"], "POST", "/auth/logout")
    assert logout.status_code == 204
    accepted = write(
        setup["outsider"],
        "POST",
        "/customer/invitations/accept",
        {"token": token, "full_name": "Invited Customer", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    profile = setup["outsider"].get("/api/customer/me")
    assert profile.status_code == 200
    repeated = write(setup["outsider"], "POST", "/customer/invitations/accept", {"token": token})
    assert repeated.status_code == 400


def test_recovery_is_generic_single_use_and_revokes_sessions(setup):
    customer = create_verified_customer(setup)
    for address in ["missing@example.com", "portal-customer@example.com"]:
        result = write(customer, "POST", "/customer/password-recovery", {"email": address})
        assert result.status_code == 202
        assert result.json() == {
            "message": "If an eligible account exists, a recovery email has been sent."
        }
    with setup["database"].session() as db:
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("recovery:%")))
        token = action_token(event)
    completed = write(
        customer,
        "POST",
        "/customer/password-recovery/complete",
        {"token": token, "password": "another customer secure phrase"},
    )
    assert completed.status_code == 204
    assert customer.get("/api/auth/me").status_code == 401
    reused = write(
        customer,
        "POST",
        "/customer/password-recovery/complete",
        {"token": token, "password": "a third customer secure phrase"},
    )
    assert reused.status_code == 400
    login = write(
        customer,
        "POST",
        "/auth/login",
        {"email": "portal-customer@example.com", "password": "another customer secure phrase"},
    )
    assert login.status_code == 200


def test_verification_rejects_expired_and_csrf_forged_requests(setup):
    no_origin = setup["owner"].post(
        "/api/customer/register",
        json={
            "email": "portal-customer@example.com",
            "password": PASSWORD,
            "full_name": "Portal Customer",
            "organization_slug": "portal-org",
        },
    )
    assert no_origin.status_code == 403
    response = write(
        setup["owner"],
        "POST",
        "/customer/register",
        {
            "email": "portal-customer@example.com",
            "password": PASSWORD,
            "full_name": "Portal Customer",
            "organization_slug": "portal-org",
        },
    )
    assert response.status_code == 202
    with setup["database"].session() as db:
        token_row = db.scalar(select(AccountToken).where(AccountToken.purpose == "verify_customer"))
        event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("verify:%")))
        token = action_token(event)
        assert token_row is not None
        token_row.expires_at = utcnow()
        db.commit()
    expired = write(setup["owner"], "POST", "/customer/verify", {"token": token})
    assert expired.status_code == 400
    with setup["database"].session() as db:
        assert db.scalar(select(CustomerAccess.id)) is None


def test_worker_delivery_is_idempotent_and_failures_remain_recoverable(setup, monkeypatch):
    from app import jobs

    with setup["database"].session() as db:
        event = OutboxEvent(
            event_key="worker-test-once",
            event_type="email",
            payload={"to": "x@example.com", "subject": "test", "body": "safe"},
            organization_id=setup["org"],
        )
        db.add(event)
        db.flush()
        job = BackgroundJob(outbox_event_id=event.id)
        db.add(job)
        db.commit()
        job_id = job.id

    calls = []
    monkeypatch.setattr(jobs, "send_email", lambda payload: calls.append(payload["to"]))
    assert jobs.process_one(setup["database"])
    assert not jobs.process_one(setup["database"])
    assert calls == ["x@example.com"]
    with setup["database"].session() as db:
        job = db.get(BackgroundJob, job_id)
        assert job.status == "completed" and job.attempts == 1

    def fail(_payload):
        raise OSError("recipient detail must not be persisted")

    monkeypatch.setattr(jobs, "send_email", fail)
    with setup["database"].session() as db:
        event = OutboxEvent(
            event_key="worker-test-failure",
            event_type="email",
            payload={"to": "y@example.com", "subject": "test", "body": "safe"},
            organization_id=setup["org"],
        )
        db.add(event)
        db.flush()
        failed_job = BackgroundJob(outbox_event_id=event.id)
        db.add(failed_job)
        db.commit()
        failed_id = failed_job.id
    assert jobs.process_one(setup["database"])
    with setup["database"].session() as db:
        failed_job = db.get(BackgroundJob, failed_id)
        assert failed_job.status == "pending" and failed_job.last_error == "OSError"
        assert "recipient detail" not in (failed_job.last_error or "")
        failed_job.available_at = utcnow()
        failed_job.attempts = 9
        db.commit()
    assert jobs.process_one(setup["database"])
    with setup["database"].session() as db:
        failed_job = db.get(BackgroundJob, failed_id)
        assert failed_job.status == "failed"


def test_expired_worker_claim_is_recovered_after_interruption(setup, monkeypatch):
    from app import jobs

    with setup["database"].session() as db:
        event = OutboxEvent(
            event_key="worker-test-interrupted",
            event_type="email",
            payload={"to": "recovered@example.test", "subject": "test", "body": "safe"},
            organization_id=setup["org"],
        )
        db.add(event)
        db.flush()
        interrupted_job = BackgroundJob(
            outbox_event_id=event.id,
            status="processing",
            attempts=1,
            locked_until=utcnow() - timedelta(seconds=1),
        )
        db.add(interrupted_job)
        db.commit()
        job_id = interrupted_job.id

    sent = []
    monkeypatch.setattr(jobs, "send_email", lambda payload: sent.append(payload["to"]))
    assert jobs.process_one(setup["database"])
    assert sent == ["recovered@example.test"]
    with setup["database"].session() as db:
        recovered = db.get(BackgroundJob, job_id)
        assert recovered is not None
        assert recovered.status == "completed" and recovered.attempts == 2
