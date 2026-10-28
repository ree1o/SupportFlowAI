import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
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
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = parsed.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("SUPPORTFLOW_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    config = Settings(
        _env_file=None,
        database_url=scoped_url.render_as_string(hide_password=False),
        environment="test",
        cookie_secure=False,
    )
    database = Database(config)
    alembic = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        command.upgrade(alembic, "head")
        yield config, database, alembic
    finally:
        database.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
