import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.identity.models import Membership, Organization, Role, User
from app.main import create_app
from app.semantic.models import MODEL_VERSION, TicketEmbedding
from app.tenancy import set_tenant_context
from app.tickets.models import Contact, Message, Ticket, Visibility

pytestmark = pytest.mark.integration


def test_migration_upgrade_downgrade_and_readiness(postgres):
    config, database, alembic = postgres
    with TestClient(create_app(config)) as client:
        assert client.get("/api/health/ready").status_code == 200
        command.downgrade(alembic, "base")
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "schema_not_ready"
        assert client.get("/api/health/live").status_code == 200
        command.upgrade(alembic, "head")
        assert client.get("/api/health/ready").status_code == 200
    with database.engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == (
            "0009_release_hardening",
        )
    assert set(inspect(database.engine).get_table_names()) == {
        "alembic_version",
        "users",
        "organizations",
        "memberships",
        "sessions",
        "contacts",
        "categories",
        "tickets",
        "messages",
        "audit_events",
        "customer_access",
        "customer_invitations",
        "account_tokens",
        "outbox_events",
        "background_jobs",
        "notifications",
        "email_deliveries",
        "sla_policies",
        "sla_policy_versions",
        "ticket_slas",
        "integration_sources",
        "webhook_receipts",
        "incidents",
        "incident_tickets",
        "incident_events",
        "radar_runs",
        "radar_proposals",
        "radar_proposal_tickets",
        "radar_suppressions",
        "ticket_embeddings",
    }
    command.check(alembic)


def test_m5_additive_upgrade_from_m4(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0004_m4")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "customer_access", "outbox_events"} <= tables
    assert not {"sla_policies", "sla_policy_versions", "ticket_slas"} & tables
    command.upgrade(alembic, "0005_sla")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "customer_access", "outbox_events"} <= tables
    assert {"sla_policies", "sla_policy_versions", "ticket_slas"} <= tables


def test_m6_upgrade_from_m5_is_additive(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0005_sla")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "ticket_slas", "outbox_events"} <= tables
    assert not {"integration_sources", "webhook_receipts"} & tables
    command.upgrade(alembic, "0006_integrations")
    tables = set(inspect(database.engine).get_table_names())
    assert {"integration_sources", "webhook_receipts", "tickets", "ticket_slas"} <= tables


def test_m7_upgrade_from_m6_is_additive(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0006_integrations")
    tables = set(inspect(database.engine).get_table_names())
    assert {"integration_sources", "webhook_receipts", "tickets", "ticket_slas"} <= tables
    assert (
        not {
            "incidents",
            "incident_tickets",
            "incident_events",
            "radar_runs",
            "radar_proposals",
            "radar_proposal_tickets",
            "radar_suppressions",
        }
        & tables
    )
    command.upgrade(alembic, "head")
    tables = set(inspect(database.engine).get_table_names())
    assert {
        "incidents",
        "incident_tickets",
        "incident_events",
        "radar_runs",
        "radar_proposals",
        "radar_proposal_tickets",
        "radar_suppressions",
    } <= tables


def test_m8_upgrade_from_m7_is_additive(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0007_incident_radar")
    tables = set(inspect(database.engine).get_table_names())
    assert "tickets" in tables and "radar_runs" in tables
    assert "ticket_embeddings" not in tables
    command.upgrade(alembic, "head")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "radar_runs", "ticket_embeddings"} <= tables


def test_failed_transaction_rolls_back_all_writes(postgres):
    _, database, _ = postgres
    # PostgreSQL DDL is transactional; this scratch table never survives the rollback.
    with pytest.raises(IntegrityError), database.session() as session, session.begin():
        session.execute(text("CREATE TABLE m1_rollback_probe (id integer PRIMARY KEY)"))
        session.execute(text("INSERT INTO m1_rollback_probe VALUES (1)"))
        session.execute(text("INSERT INTO m1_rollback_probe VALUES (1)"))
    with database.session() as session:
        assert session.scalar(text("SELECT to_regclass('m1_rollback_probe')")) is None
        assert session.scalar(text("SELECT 1")) == 1


def test_sessions_are_independent_and_cleanup_rolls_back(postgres):
    _, database, _ = postgres
    with database.session() as first, database.session() as second:
        assert first.scalar(text("SELECT pg_backend_pid()")) != second.scalar(
            text("SELECT pg_backend_pid()")
        )
        first.execute(text("CREATE TABLE m1_uncommitted_probe (id integer)"))
        # No commit: dependency cleanup must roll back, even on a successful return.
    with database.session() as session:
        assert session.scalar(text("SELECT to_regclass('m1_uncommitted_probe')")) is None


def test_m9_upgrade_from_m8_preserves_tenant_data_and_enforces_rls(postgres, runtime_database):
    _, database, alembic = postgres
    with database.session() as session, session.begin():
        organization = Organization(name="M9 upgrade fixture", slug="m9-upgrade-fixture")
        user = User(
            email="m9-upgrade@example.com",
            full_name="M9 Upgrade Fixture",
            password_hash="test-only-hash",
        )
        session.add_all((organization, user))
        session.flush()
        membership = Membership(
            organization_id=organization.id,
            user_id=user.id,
            role=Role.OWNER,
        )
        contact = Contact(
            organization_id=organization.id,
            name="Synthetic migration contact",
        )
        session.add_all((membership, contact))
        session.flush()
        ticket = Ticket(
            organization_id=organization.id,
            requester_contact_id=contact.id,
            category_id=None,
            assigned_membership_id=None,
            created_by_membership_id=membership.id,
            created_by_customer_access_id=None,
            created_by_integration_source_id=None,
            title="Synthetic migration ticket",
            status="open",
            priority="normal",
            version=1,
        )
        session.add(ticket)
        session.flush()
        message = Message(
            organization_id=organization.id,
            ticket_id=ticket.id,
            author_membership_id=membership.id,
            author_customer_access_id=None,
            author_integration_source_id=None,
            author_name="M9 Upgrade Fixture",
            visibility=Visibility.PUBLIC,
            body="Synthetic migration message",
            ticket_version=1,
        )
        embedding = TicketEmbedding(
            organization_id=organization.id,
            ticket_id=ticket.id,
            model_version=MODEL_VERSION,
            content_digest="a" * 64,
            source_version=1,
            is_current=True,
            status="pending",
        )
        session.add_all((message, embedding))
        session.flush()
        organization_id, ticket_id, embedding_id = organization.id, ticket.id, embedding.id

    command.downgrade(alembic, "0008_semantic_retrieval")
    with database.session() as session:
        assert session.get(Ticket, ticket_id) is not None
        assert session.get(TicketEmbedding, embedding_id) is not None

    command.upgrade(alembic, "head")
    with database.engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='tickets'::regclass"
            )
        )
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='ticket_embeddings'::regclass"
            )
        )

    _, runtime_db = runtime_database
    with runtime_db.session_factory() as session:
        set_tenant_context(session, organization_id)
        assert session.scalar(select(Ticket.id).where(Ticket.id == ticket_id)) == ticket_id
        assert (
            session.scalar(select(TicketEmbedding.id).where(TicketEmbedding.id == embedding_id))
            == embedding_id
        )
