import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.errors import AppError
from app.rate_limit import _INCREMENT, RateLimiter


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
