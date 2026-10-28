import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Database
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Integration tests require a dedicated database name ending in _test")
    monkeypatch.setenv("SUPPORTFLOW_DATABASE_URL", url)
    config = Settings(_env_file=None, database_url=url, environment="test")
    database = Database(config)
    alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # Refuse to downgrade a database containing any non-baseline user table.
    assert set(inspect(database.engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(alembic, "head")
    try:
        yield config, database, alembic
    finally:
        command.upgrade(alembic, "head")
        database.dispose()


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
        assert MigrationContext.configure(connection).get_current_heads() == ("0001_baseline",)
    assert set(inspect(database.engine).get_table_names()) == {"alembic_version"}
    command.check(alembic)


def test_failed_transaction_rolls_back_all_writes(postgres):
    _, database, _ = postgres
    # PostgreSQL DDL is transactional; this scratch table never survives the rollback.
    with pytest.raises(IntegrityError), database.session() as session, session.begin():
        session.execute(text("CREATE TABLE m1_rollback_probe (id integer PRIMARY KEY)"))
        session.execute(text("INSERT INTO m1_rollback_probe VALUES (1)"))
        session.execute(text("INSERT INTO m1_rollback_probe VALUES (1)"))
    with database.session() as session:
        assert session.scalar(text("SELECT to_regclass('public.m1_rollback_probe')")) is None
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
        assert session.scalar(text("SELECT to_regclass('public.m1_uncommitted_probe')")) is None
