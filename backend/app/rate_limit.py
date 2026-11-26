"""Atomic Redis-backed limits for endpoints that are costly to abuse."""

import hashlib
import hmac
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, Request
from redis import Redis
from redis.exceptions import RedisError

from app.errors import AppError

logger = logging.getLogger("supportflow.rate_limit")

_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}
"""

_RESERVE_MANY = """
for i = 1, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local limit = tonumber(ARGV[(i - 1) * 2 + 1])
  if current >= limit then
    return {0, i, redis.call('PTTL', KEYS[i]), 0}
  end
end
local remaining = 2147483647
local reset = 0
for i = 1, #KEYS do
  local count = redis.call('INCR', KEYS[i])
  local ttl
  if count == 1 then
    ttl = tonumber(ARGV[(i - 1) * 2 + 2])
    redis.call('PEXPIRE', KEYS[i], ttl)
  else
    ttl = redis.call('PTTL', KEYS[i])
  end
  local available = tonumber(ARGV[(i - 1) * 2 + 1]) - count
  if available < remaining then remaining = available end
  if ttl > reset then reset = ttl end
end
return {1, 0, remaining, reset}
"""

_RELEASE_MANY = """
for i = 1, #KEYS do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  if current > 1 then redis.call('DECR', KEYS[i])
  elseif current == 1 then redis.call('DEL', KEYS[i]) end
end
return 1
"""

_READ_QUOTA = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
return {count, redis.call('PTTL', KEYS[1])}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    limited_scope: str | None = None


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
        key = self._key(bucket, identity)
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

    def reserve_many(self, limits: list[tuple[str, str, str, int, int]]) -> RateLimitDecision:
        """Atomically reserve all AI quota scopes, charging none if any scope is full."""
        if self._disabled:
            return RateLimitDecision(True, min(item[3] for item in limits), 0)
        keys = [self._key(bucket, identity) for bucket, identity, _label, _, _ in limits]
        arguments = [
            value
            for _bucket, _identity, _label, limit, window in limits
            for value in (limit, window * 1000)
        ]
        try:
            result = cast(
                tuple[int, int, int, int],
                cast(Any, self._redis).eval(_RESERVE_MANY, len(keys), *keys, *arguments),
            )
        except RedisError as exc:
            raise AppError(
                503,
                "rate_limit_unavailable",
                "AI requests are temporarily unavailable",
            ) from exc
        allowed, blocked_index, amount, ttl_ms = map(int, result)
        if not allowed:
            spec = limits[max(0, blocked_index - 1)]
            return RateLimitDecision(False, 0, max(1, math.ceil(amount / 1000)), spec[2])
        return RateLimitDecision(True, max(0, amount), max(0, math.ceil(ttl_ms / 1000)))

    def release_many(self, limits: list[tuple[str, str, str, int, int]]) -> None:
        """Refund a reservation when no durable AI run was created."""
        if self._disabled or not limits:
            return
        keys = [self._key(bucket, identity) for bucket, identity, _label, _, _ in limits]
        try:
            cast(Any, self._redis).eval(_RELEASE_MANY, len(keys), *keys)
        except RedisError as exc:
            logger.warning("ai_quota_refund_failed", extra={"error_type": type(exc).__name__})

    def quota(self, bucket: str, identity: str, limit: int) -> tuple[int, int] | None:
        if self._disabled:
            return limit, 0
        try:
            count, ttl_ms = cast(
                tuple[int, int],
                cast(Any, self._redis).eval(_READ_QUOTA, 1, self._key(bucket, identity)),
            )
        except RedisError:
            return None
        count, ttl_ms = int(count), int(ttl_ms)
        return max(0, limit - count), max(0, math.ceil(ttl_ms / 1000))

    def _key(self, bucket: str, identity: str) -> str:
        digest = hmac.new(self._secret, identity.encode(), hashlib.sha256).hexdigest()
        return f"supportflow:limit:{bucket}:{digest}"

    def close(self) -> None:
        self._redis.close()


def limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def limited(
    bucket: str,
    limit: int,
    window_seconds: int,
    *,
    identity_scope: Literal["client", "organization", "global"] = "client",
) -> Callable:
    def dependency(request: Request, service: Annotated[RateLimiter, Depends(limiter)]) -> None:
        if identity_scope == "global":
            identity = "global"
        elif identity_scope == "organization":
            identity = str(request.path_params.get("organization_id") or "unknown")
        else:
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
