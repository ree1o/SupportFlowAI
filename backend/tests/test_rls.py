import hashlib
import hmac
import json
import re
import time
from contextlib import ExitStack
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.customers.models import OutboxEvent
from app.main import create_app
from app.tenancy import set_tenant_context
from app.tickets.models import Contact, Ticket

pytestmark = pytest.mark.integration


def write(client, method, path, data=None):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method,
        f"/api{path}",
        json=data,
        headers={"Origin": "http://localhost:5173", "X-CSRF-Token": csrf},
    )


@pytest.fixture
def desk(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(
                TestClient(create_app(settings), base_url="http://localhost:5173")
            )
            for name in ("owner", "outsider")
        }
        profiles = {}
        for name, client in clients.items():
            result = write(
                client,
                "POST",
                "/auth/register",
                {
                    "email": f"{name}@rls.example",
                    "password": "a disposable RLS test passphrase",
                    "full_name": name.title(),
                    "organization": {"name": name.title(), "slug": f"{name}-rls"},
                },
            )
            assert result.status_code == 201, result.text
            profiles[name] = result.json()
        org = profiles["owner"]["selected_organization_id"]
        root = f"/organizations/{org}"
        contact = write(
            clients["owner"],
            "POST",
            f"{root}/contacts",
            {"name": "Customer", "email": "rls-customer@example.com"},
        )
        assert contact.status_code == 201, contact.text
        yield {
            "clients": clients,
            "database": database,
            "profiles": profiles,
            "org": org,
            "root": root,
            "contact": contact.json(),
        }


def test_runtime_role_is_unprivileged_and_policy_is_forced(postgres):
    _, database, _ = postgres
    with database.engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname='supportflow_runtime'"
            )
        ).one()
        assert role == (False, False, False, False)
        privileges = connection.execute(
            text(
                "SELECT has_table_privilege('supportflow_runtime', "
                "format('%I.messages', current_schema()), 'UPDATE'), "
                "has_table_privilege('supportflow_runtime', "
                "format('%I.tickets', current_schema()), 'DELETE'), "
                "has_table_privilege('supportflow_runtime', "
                "format('%I.webhook_receipts', current_schema()), 'DELETE'), "
                "has_table_privilege('supportflow_runtime', "
                "format('%I.alembic_version', current_schema()), 'SELECT'), "
                "has_table_privilege('supportflow_runtime', "
                "format('%I.alembic_version', current_schema()), 'UPDATE')"
                ", has_column_privilege('supportflow_runtime', "
                "format('%I.webhook_receipts', current_schema()), 'status', 'UPDATE'), "
                "has_column_privilege('supportflow_runtime', "
                "format('%I.webhook_receipts', current_schema()), 'external_event_id', 'UPDATE')"
            )
        ).one()
        assert privileges == (False, False, True, True, False, True, False)
        tables = (
            connection.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relnamespace=current_schema()::regnamespace "
                    "AND relname IN ('contacts','categories','tickets','messages','audit_events',"
                    "'customer_access','account_tokens','customer_invitations','notifications',"
                    "'sla_policies','sla_policy_versions','ticket_slas','integration_sources',"
                    "'webhook_receipts','incidents','incident_tickets','incident_events',"
                    "'radar_runs','radar_proposals','radar_proposal_tickets','radar_suppressions',"
                    "'ticket_embeddings') "
                    "AND relrowsecurity AND relforcerowsecurity"
                )
            )
            .scalars()
            .all()
        )
        assert set(tables) == {
            "tickets",
            "contacts",
            "messages",
            "categories",
            "audit_events",
            "customer_access",
            "account_tokens",
            "customer_invitations",
            "notifications",
            "sla_policies",
            "sla_policy_versions",
            "webhook_receipts",
            "incident_tickets",
            "incident_events",
            "radar_runs",
            "incidents",
            "radar_proposals",
            "radar_proposal_tickets",
            "radar_suppressions",
            "ticket_embeddings",
            "integration_sources",
            "ticket_slas",
        }


def test_rls_denies_missing_or_foreign_context_and_does_not_leak_pool_context(
    desk, runtime_database
):
    settings, runtime_db = runtime_database
    ticket_response = desk["clients"]["owner"].post(
        f"/api{desk['root']}/tickets",
        json={
            "requester_contact_id": desk["contact"]["id"],
            "title": "RLS scoped ticket",
            "initial_message": "A tenant scoped issue.",
        },
        headers={
            "Origin": "http://localhost:5173",
            "X-CSRF-Token": desk["clients"]["owner"].get("/api/auth/csrf").json()["csrf_token"],
        },
    )
    assert ticket_response.status_code == 201, ticket_response.text
    ticket_id = UUID(ticket_response.json()["id"])
    organization_id = UUID(desk["org"])

    with runtime_db.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        set_tenant_context(db, UUID(int=0))
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        db.commit()

    with runtime_db.session_factory() as db:
        set_tenant_context(db, organization_id)
        assert db.scalar(select(Ticket.id).where(Ticket.id == ticket_id)) == ticket_id
        db.commit()

    # The same pooled connection starts the next transaction without the prior tenant GUC.
    with runtime_db.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        db.commit()

    with runtime_db.session_factory() as db, pytest.raises(DBAPIError):
        db.add(
            Contact(
                organization_id=organization_id,
                name="Must be denied without tenant context",
                email=None,
            )
        )
        db.flush()
        db.rollback()

    with TestClient(create_app(settings), base_url="http://localhost:5173") as runtime_client:
        raw_session = desk["clients"]["owner"].cookies.get("supportflow_session")
        runtime_client.cookies.set("supportflow_session", raw_session, path="/api")
        result = runtime_client.get(f"/api{desk['root']}/tickets/{ticket_id}")
        assert result.status_code == 200, result.text


def test_rls_isolation_between_two_organizations(desk, runtime_database):
    _, runtime_db = runtime_database
    outsider = desk["clients"]["outsider"]
    outsider_profile = outsider.get("/api/auth/me").json()
    outsider_org = UUID(outsider_profile["selected_organization_id"])
    outsider_root = f"/organizations/{outsider_org}"
    contact = outsider.post(
        f"/api{outsider_root}/contacts",
        json={"name": "Other tenant", "email": None},
        headers={
            "Origin": "http://localhost:5173",
            "X-CSRF-Token": outsider.get("/api/auth/csrf").json()["csrf_token"],
        },
    )
    assert contact.status_code == 201, contact.text
    response = outsider.post(
        f"/api{outsider_root}/tickets",
        json={
            "requester_contact_id": contact.json()["id"],
            "title": "Other tenant ticket",
            "initial_message": "Private to the second organization.",
        },
        headers={
            "Origin": "http://localhost:5173",
            "X-CSRF-Token": outsider.get("/api/auth/csrf").json()["csrf_token"],
        },
    )
    assert response.status_code == 201, response.text
    other_ticket = UUID(response.json()["id"])
    with runtime_db.session_factory() as db:
        set_tenant_context(db, UUID(desk["org"]))
        assert db.scalar(select(Ticket.id).where(Ticket.id == other_ticket)) is None


def test_runtime_role_can_accept_invitation_and_process_webhook(desk, runtime_database):
    settings, _ = runtime_database
    owner = desk["clients"]["owner"]
    verification_contact = write(
        owner,
        "POST",
        f"{desk['root']}/contacts",
        {"name": "RLS Verified Customer", "email": "rls-verified@example.com"},
    )
    assert verification_contact.status_code == 201
    profile = owner.get("/api/auth/me").json()
    with TestClient(create_app(settings), base_url="http://localhost:5173") as runtime_client:
        registration = write(
            runtime_client,
            "POST",
            "/customer/register",
            {
                "email": "rls-verified@example.com",
                "password": "a secure disposable customer passphrase",
                "full_name": "RLS Verified Customer",
                "organization_slug": profile["organizations"][0]["slug"],
            },
        )
        assert registration.status_code == 202, registration.text
        with desk["database"].session_factory() as db:
            verify_event = db.scalar(
                select(OutboxEvent).where(OutboxEvent.event_key.like("verify:%"))
            )
            assert verify_event is not None
            verify_match = re.search(
                r"customer/verify\?token=([^\s]+)", verify_event.payload["body"]
            )
            assert verify_match is not None
        verified = write(
            runtime_client, "POST", "/customer/verify", {"token": verify_match.group(1)}
        )
        assert verified.status_code == 204, verified.text
        recovery = write(
            runtime_client,
            "POST",
            "/customer/password-recovery",
            {"email": "rls-verified@example.com"},
        )
        assert recovery.status_code == 202, recovery.text
        with desk["database"].session_factory() as db:
            recovery_event = db.scalar(
                select(OutboxEvent).where(OutboxEvent.event_key.like("recovery:%"))
            )
            assert recovery_event is not None
            recovery_match = re.search(
                r"customer/recover\?token=([^\s]+)", recovery_event.payload["body"]
            )
            assert recovery_match is not None
        recovered = write(
            runtime_client,
            "POST",
            "/customer/password-recovery/complete",
            {
                "token": recovery_match.group(1),
                "password": "a replacement secure customer passphrase",
            },
        )
        assert recovered.status_code == 204, recovered.text

        invitation = write(
            owner,
            "POST",
            f"{desk['root']}/customer-access/invitations",
            {"contact_id": desk["contact"]["id"]},
        )
        assert invitation.status_code == 202, invitation.text
        with desk["database"].session_factory() as db:
            event = db.scalar(select(OutboxEvent).where(OutboxEvent.event_key.like("invite:%")))
            assert event is not None
            match = re.search(r"customer/invitation\?token=([^\s]+)", event.payload["body"])
            assert match is not None
            token = match.group(1)

        accepted = write(
            runtime_client,
            "POST",
            "/customer/invitations/accept",
            {
                "token": token,
                "full_name": "RLS Customer",
                "password": "a secure disposable customer passphrase",
            },
        )
        assert accepted.status_code == 200, accepted.text

        created_source = write(
            owner,
            "POST",
            f"{desk['root']}/integrations",
            {"name": "RLS verification source", "contact_id": desk["contact"]["id"]},
        )
        assert created_source.status_code == 201, created_source.text
        issued = created_source.json()
        event_id = f"rls-{uuid4()}"
        timestamp = str(int(time.time()))
        body = json.dumps(
            {
                "type": "ticket.created",
                "title": "RLS webhook ticket",
                "message": "A runtime-role event.",
            },
            separators=(",", ":"),
        ).encode()
        signed = timestamp.encode() + b"." + event_id.encode() + b"." + body
        signature = hmac.new(issued["credential"].encode(), signed, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-SupportFlow-Event-Id": event_id,
            "X-SupportFlow-Timestamp": timestamp,
            "X-SupportFlow-Signature": f"v1={signature}",
        }
        path = f"/api/integrations/{issued['source']['id']}/events"
        first = runtime_client.post(path, content=body, headers=headers)
        assert first.status_code == 201, first.text
        duplicate = runtime_client.post(path, content=body, headers=headers)
        assert duplicate.status_code == 201, duplicate.text
        assert duplicate.headers["X-SupportFlow-Duplicate"] == "true"


def test_migration_owner_role_remains_distinct_from_runtime(postgres):
    _, database, _ = postgres
    with database.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, r.rolname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner "
                "WHERE n.nspname=current_schema() AND c.relname='tickets'"
            )
        ).one()
        assert rows[1] != "supportflow_runtime"
