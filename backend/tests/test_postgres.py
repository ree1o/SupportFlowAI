import pytest
from alembic import command
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.main import create_app

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
        assert MigrationContext.configure(connection).get_current_heads() == ("0005_sla",)
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
    }
    command.check(alembic)


def test_m5_additive_upgrade_from_m4(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0004_m4")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "customer_access", "outbox_events"} <= tables
    assert not {"sla_policies", "sla_policy_versions", "ticket_slas"} & tables
    command.upgrade(alembic, "head")
    tables = set(inspect(database.engine).get_table_names())
    assert {"tickets", "customer_access", "outbox_events"} <= tables
    assert {"sla_policies", "sla_policy_versions", "ticket_slas"} <= tables


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
