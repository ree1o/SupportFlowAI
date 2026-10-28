import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.identity import security
from app.main import create_app


def test_secure_cookies_are_default_and_local_http_is_explicit(settings):
    assert settings.cookie_secure is True
    app = create_app(settings)
    with TestClient(app, base_url="https://localhost") as client:
        response = client.get("/api/auth/csrf")
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Domain=" not in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "extra",
    [
        {"cookie_secure": False, "allowed_origins": ["https://support.example.com"]},
        {"allowed_origins": ["http://localhost:5173"]},
        {"allowed_origins": ["https://example.com/path"]},
        {"allowed_origins": []},
    ],
)
def test_production_rejects_unsafe_security_configuration(settings, extra):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, database_url=settings.database_url, environment="production", **extra
        )


def test_session_cookie_https_flags_and_no_domain(settings):
    from fastapi import Response

    response = Response()
    security.set_session_cookie(response, security.new_token(), 3600, settings)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Domain=" not in cookie


def test_csrf_derivation_is_bound_to_session_and_hashes_are_one_way():
    first, second = security.new_token(), security.new_token()
    assert len(first) == 43 and first != second
    assert security.csrf_for_session(first) != security.csrf_for_session(second)
    assert security.token_hash(first) != first
    assert not security.verify_password("broken-hash", "a long random passphrase")
