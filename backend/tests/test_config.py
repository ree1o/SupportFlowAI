import pytest
from pydantic import ValidationError

from app.config import Settings


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("SUPPORTFLOW_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "sqlite:///test.db", "postgresql://u:p@localhost/db", "postgresql+psycopg://"],
)
def test_rejects_invalid_database_urls(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=url)


def test_environment_and_secrets(monkeypatch):
    monkeypatch.setenv("SUPPORTFLOW_DATABASE_URL", "postgresql+psycopg://u:secret@localhost/db")
    monkeypatch.setenv("SUPPORTFLOW_ENVIRONMENT", "test")
    config = Settings(_env_file=None)
    assert config.environment == "test"
    assert "secret" not in repr(config)
    assert "secret" not in config.model_dump_json()


def test_invalid_configuration_does_not_echo_secret():
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url="postgresql://u:private-password@host/db")
    assert "private-password" not in str(error.value)


def test_pool_limits(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=settings.database_url, database_pool_size=0)
