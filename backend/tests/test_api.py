import json
from unittest.mock import MagicMock
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_session
from app.main import create_app


def test_liveness_never_connects_to_database(settings, monkeypatch):
    app = create_app(settings)
    with TestClient(app) as client:
        connect = MagicMock(side_effect=AssertionError("liveness must not connect"))
        monkeypatch.setattr(app.state.database.engine, "connect", connect)
        response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    UUID(response.headers["x-request-id"])
    connect.assert_not_called()


def test_readiness_unavailable_is_safe(settings):
    app = create_app(settings)
    session = MagicMock()
    session.execute.side_effect = OperationalError("private-sql", {}, Exception("private-password"))
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "private" not in response.text


def test_readiness_requires_migration_head(settings, monkeypatch):
    app = create_app(settings)
    session = MagicMock()
    app.dependency_overrides[get_session] = lambda: session
    migration = MagicMock()
    monkeypatch.setattr("app.health.MigrationContext.configure", lambda connection: migration)
    with TestClient(app) as client:
        migration.get_current_heads.return_value = ()
        response = client.get("/api/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "schema_not_ready"
        migration.get_current_heads.return_value = tuple(app.state.migration_heads)
        assert client.get("/api/health/ready").json() == {"status": "ready"}


def test_unknown_route_and_method_have_consistent_errors(settings):
    with TestClient(create_app(settings)) as client:
        for response, status, code in [
            (client.get("/unknown"), 404, "not_found"),
            (client.post("/api/health/live"), 405, "method_not_allowed"),
        ]:
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
            assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


def test_unexpected_error_is_sanitized(settings):
    app = create_app(settings)

    @app.get("/test-failure")
    def fail():
        raise RuntimeError("sensitive-token")

    with TestClient(app) as client:
        response = client.get("/test-failure")
        assert client.get("/api/health/live").status_code == 200
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive-token" not in response.text


def test_structured_logs_exclude_query_and_unmatched_path(settings, capsys):
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/secret-path?token=secret-query", headers={"X-Request-ID": "untrusted"}
        )
    output = capsys.readouterr().out
    assert "secret-path" not in output
    assert "secret-query" not in output
    records = [json.loads(line) for line in output.splitlines()]
    completed = next(record for record in records if record["event"] == "request_completed")
    assert completed["request_id"] == response.headers["x-request-id"]
    assert completed["request_id"] != "untrusted"
    assert completed["status_code"] == 404
    assert completed["route"] == "unmatched"
    assert completed["duration_ms"] >= 0


def test_lifespan_disposes_engine(settings, monkeypatch):
    app = create_app(settings)
    with TestClient(app):
        dispose = MagicMock()
        monkeypatch.setattr(app.state.database, "dispose", dispose)
    dispose.assert_called_once()
