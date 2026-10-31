"""Atomic Redis-backed limits for endpoints that are costly to abuse."""

import hashlib
import hmac
import math
from collections.abc import Callable
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from redis import Redis
from redis.exceptions import RedisError

from app.errors import AppError

_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}
"""


class RateLimiter:
    def __init__(self, redis_url: str, secret: str, *, disabled: bool = False) -> None:
        self._redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        self._secret = secret.encode()
        self._disabled = disabled

    def check(
        self,
        *,
        bucket: str,
        identity: str,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        if self._disabled:
            return None
        digest = hmac.new(self._secret, identity.encode(), hashlib.sha256).hexdigest()
        key = f"supportflow:limit:{bucket}:{digest}"
        try:
            count, ttl_ms = cast(
                tuple[int, int],
                cast(Any, self._redis).eval(_INCREMENT, 1, key, str(window_seconds * 1000)),
            )
        except RedisError as exc:
            raise AppError(
                503,
                "rate_limit_unavailable",
                "This operation is temporarily unavailable",
            ) from exc
        if int(count) > limit:
            return max(1, math.ceil(int(ttl_ms) / 1000))
        return None

    def close(self) -> None:
        self._redis.close()


def limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def limited(bucket: str, limit: int, window_seconds: int) -> Callable:
    def dependency(request: Request, service: Annotated[RateLimiter, Depends(limiter)]) -> None:
        client_ip = request.client.host if request.client else "unknown"
        route_scope = request.path_params.get("organization_id") or request.path_params.get(
            "source_id", ""
        )
        identity = f"{client_ip}:{route_scope}"
        retry_after = service.check(
            bucket=bucket,
            identity=identity,
            limit=limit,
            window_seconds=window_seconds,
        )
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many requests; try again later",
                {"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
