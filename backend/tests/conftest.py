import pytest

from app.config import Settings


@pytest.fixture
def settings():
    # No real connection is made by unit tests. No dependency on a developer's .env.
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:unit-secret@127.0.0.1:1/supportflow_test",
        environment="test",
        database_timeout_seconds=1,
    )
