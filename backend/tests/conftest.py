import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import Database


@pytest.fixture
def settings():
    # No real connection is made by unit tests. No dependency on a developer's .env.
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:unit-secret@127.0.0.1:1/supportflow_test",
        environment="test",
        database_timeout_seconds=1,
    )


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    parsed = make_url(url)
    if not (parsed.database or "").endswith("_test"):
        pytest.fail("Integration tests require a dedicated database ending in _test")
    # Every test owns a random schema; migration downgrades never touch other schemas/data.
    schema = f"test_{uuid4().hex}"
    admin = create_engine(parsed, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = parsed.update_query_dict({"options": f"-csearch_path={schema},public"})
    monkeypatch.setenv("SUPPORTFLOW_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    monkeypatch.setenv(
        "SUPPORTFLOW_MIGRATION_DATABASE_URL", scoped_url.render_as_string(hide_password=False)
    )
    config = Settings(
        _env_file=None,
        database_url=scoped_url.render_as_string(hide_password=False),
        environment="test",
        cookie_secure=False,
    )
    runtime_password = os.environ.get("SUPPORTFLOW_RUNTIME_DB_PASSWORD")
    if not runtime_password:
        local_settings = Settings(
            _env_file=Path(__file__).resolve().parents[2] / ".env",
            database_url="postgresql+psycopg://test:unit-secret@127.0.0.1/supportflow_test",
            environment="test",
        )
        configured_password = local_settings.runtime_db_password
        runtime_password = (
            configured_password.get_secret_value()
            if configured_password is not None
            else "supportflow_runtime_dev_only"
        )
    monkeypatch.setenv("SUPPORTFLOW_RUNTIME_DB_PASSWORD", runtime_password)
    database = Database(config)
    alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        command.upgrade(alembic, "head")
        with admin.begin() as connection:
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO supportflow_runtime'))
            connection.execute(
                text(
                    f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA "{schema}" '
                    "TO supportflow_runtime"
                )
            )
            connection.execute(
                text(
                    f'GRANT DELETE ON TABLE "{schema}".sessions, "{schema}".account_tokens, '
                    f'"{schema}".customer_invitations, "{schema}".webhook_receipts, '
                    f'"{schema}".notifications, "{schema}".incident_tickets, '
                    f'"{schema}".radar_runs, "{schema}".ticket_embeddings, '
                    f'"{schema}".background_jobs, "{schema}".outbox_events, '
                    f'"{schema}".email_deliveries TO supportflow_runtime'
                )
            )
            connection.execute(
                text(
                    f'REVOKE UPDATE ON TABLE "{schema}".messages, '
                    f'"{schema}".audit_events, "{schema}".incident_events, '
                    f'"{schema}".webhook_receipts, "{schema}".email_deliveries '
                    "FROM supportflow_runtime"
                )
            )
            connection.execute(
                text(
                    f"GRANT UPDATE (status, processed_at, ticket_id, message_id, "
                    f'error_category, result) ON TABLE "{schema}".webhook_receipts '
                    "TO supportflow_runtime"
                )
            )
            connection.execute(
                text(
                    f'REVOKE DELETE ON TABLE "{schema}".messages, '
                    f'"{schema}".audit_events, "{schema}".incident_events '
                    "FROM supportflow_runtime"
                )
            )
            connection.execute(
                text(
                    f'REVOKE DELETE ON TABLE "{schema}".users, "{schema}".organizations, '
                    f'"{schema}".memberships, "{schema}".contacts, "{schema}".categories, '
                    f'"{schema}".tickets, "{schema}".customer_access, '
                    f'"{schema}".sla_policies, "{schema}".sla_policy_versions, '
                    f'"{schema}".ticket_slas, "{schema}".integration_sources, '
                    f'"{schema}".incidents, "{schema}".radar_proposals, '
                    f'"{schema}".radar_proposal_tickets, "{schema}".radar_suppressions '
                    "FROM supportflow_runtime"
                )
            )
            connection.execute(
                text(
                    f'REVOKE INSERT, UPDATE, DELETE ON TABLE "{schema}".alembic_version '
                    "FROM supportflow_runtime"
                )
            )
            connection.execute(
                text(f'GRANT SELECT ON TABLE "{schema}".alembic_version TO supportflow_runtime')
            )
            connection.execute(
                text(
                    f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" '
                    "TO supportflow_runtime"
                )
            )
        yield config, database, alembic
    finally:
        database.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def runtime_database(postgres):
    config, _, _ = postgres
    url = make_url(config.database_url.get_secret_value()).set(
        username="supportflow_runtime",
        password=os.environ["SUPPORTFLOW_RUNTIME_DB_PASSWORD"],
    )
    runtime_settings = config.model_copy(
        update={"database_url": SecretStr(url.render_as_string(hide_password=False))}
    )
    database = Database(runtime_settings)
    try:
        yield runtime_settings, database
    finally:
        database.dispose()
