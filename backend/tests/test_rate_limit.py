import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.errors import AppError
from app.rate_limit import _INCREMENT, _RELEASE_MANY, _RESERVE_MANY, RateLimiter


class RedisStub:
    def __init__(self, result):
        self.result = result
        self.args = None

    def eval(self, script, key_count, key, ttl):
        self.args = (script, key_count, key, ttl)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        return None


class MultiRedisStub:
    def __init__(self, result):
        self.result = result
        self.args = None

    def eval(self, *args):
        self.args = args
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        return None


def limiter_with_result(monkeypatch, result):
    limiter = RateLimiter("redis://localhost/0", "test-secret", disabled=False)
    stub = RedisStub(result)
    monkeypatch.setattr(limiter, "_redis", stub)
    return limiter, stub


def test_rate_limit_uses_atomic_counter_and_hashed_identity(monkeypatch):
    limiter, stub = limiter_with_result(monkeypatch, [2, 59_000])
    assert limiter.check(bucket="login", identity="203.0.113.4", limit=2, window_seconds=60) is None
    assert stub.args is not None
    script, key_count, key, ttl = stub.args
    assert script == _INCREMENT
    assert key_count == 1
    assert "203.0.113.4" not in key
    assert ttl == "60000"
    limiter.close()


def test_rate_limit_returns_ceil_retry_after(monkeypatch):
    limiter, _stub = limiter_with_result(monkeypatch, [3, 1001])
    assert limiter.check(bucket="login", identity="caller", limit=2, window_seconds=60) == 2
    limiter.close()


def test_reserve_many_charges_all_scopes_atomically_and_hashes_identities(monkeypatch):
    from app.rate_limit import RateLimitDecision

    limiter = RateLimiter("redis://localhost/0", "test-secret", disabled=False)
    stub = MultiRedisStub([1, 0, 8, 3_600_000])
    monkeypatch.setattr(limiter, "_redis", stub)
    limits = [
        ("global", "all", "service", 120, 3600),
        ("organization", "org-secret", "organization", 30, 3600),
        ("staff", "user-secret:org-secret", "your account", 30, 3600),
    ]
    decision = limiter.reserve_many(limits)
    assert decision == RateLimitDecision(True, 8, 3600)
    assert stub.args[0] == _RESERVE_MANY
    assert stub.args[1] == 3
    assert "org-secret" not in " ".join(stub.args[2:5])
    limiter.close()


def test_reserve_many_returns_limited_scope_and_releases_reservation(monkeypatch):
    limiter = RateLimiter("redis://localhost/0", "test-secret", disabled=False)
    stub = MultiRedisStub([0, 2, 45001, 0])
    monkeypatch.setattr(limiter, "_redis", stub)
    limits = [
        ("global", "all", "service", 120, 3600),
        ("organization", "org", "organization", 30, 3600),
    ]
    denied = limiter.reserve_many(limits)
    assert denied.allowed is False
    assert denied.limited_scope == "organization"
    assert denied.retry_after_seconds == 46
    limiter.release_many(limits)
    assert stub.args[0] == _RELEASE_MANY
    assert stub.args[1] == 2
    limiter.close()


def test_rate_limit_fails_closed_when_redis_unavailable(monkeypatch):
    limiter, _stub = limiter_with_result(monkeypatch, RedisConnectionError())
    with pytest.raises(AppError) as error:
        limiter.check(bucket="webhook", identity="source", limit=10, window_seconds=60)
    assert error.value.status == 503
    assert error.value.code == "rate_limit_unavailable"
    limiter.close()


def test_limited_request_carries_retry_after_header():
    from fastapi import Depends, FastAPI, Request
    from fastapi.testclient import TestClient

    from app.rate_limit import limited

    class FakeLimiter:
        def check(self, **_kwargs):
            return 17

    app = FastAPI()
    app.state.rate_limiter = FakeLimiter()

    @app.middleware("http")
    async def attach_request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    @app.get("/limited", dependencies=[Depends(limited("x", 1, 60))])
    def endpoint(request: Request):
        return {"ok": True}

    @app.exception_handler(AppError)
    async def handle_app_error(request, error):
        from app.errors import error_response

        response = error_response(request, error.status, error.code, error.message, error.details)
        response.headers.update(error.headers)
        return response

    response = TestClient(app).get("/limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"


def test_limited_supports_organization_and_global_identity_scopes():
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from app.rate_limit import limited

    class FakeLimiter:
        def __init__(self):
            self.calls = []

        def check(self, **kwargs):
            self.calls.append(kwargs)
            return None

    app = FastAPI()
    fake_limiter = FakeLimiter()
    app.state.rate_limiter = fake_limiter

    @app.get(
        "/organizations/{organization_id}/limited",
        dependencies=[Depends(limited("org", 30, 3600, identity_scope="organization"))],
    )
    def organization_limited():
        return {"ok": True}

    @app.get(
        "/global-limited",
        dependencies=[Depends(limited("global", 120, 3600, identity_scope="global"))],
    )
    def global_limited():
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/organizations/org-a/limited").status_code == 200
    assert client.get("/organizations/org-b/limited").status_code == 200
    assert client.get("/global-limited").status_code == 200
    assert [call["identity"] for call in fake_limiter.calls] == ["org-a", "org-b", "global"]
