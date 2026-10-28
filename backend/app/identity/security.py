import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response

from app.config import Settings
from app.errors import AppError

SESSION_COOKIE = "supportflow_session"
CSRF_COOKIE = "supportflow_csrf"
COOKIE_PATH = "/api"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
hasher = PasswordHasher()  # Argon2id, RFC 9106 low-memory defaults (64 MiB, t=3).
dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_for_session(token: str) -> str:
    return hmac.new(token.encode(), b"supportflow-csrf-v1", hashlib.sha256).hexdigest()


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def set_session_cookie(response: Response, raw: str, seconds: int, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=seconds,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(CSRF_COOKIE, path=COOKIE_PATH)


def clear_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(
            name, path=COOKIE_PATH, secure=settings.cookie_secure, httponly=True, samesite="lax"
        )


def issue_csrf(request: Request, response: Response, settings: Settings) -> str:
    raw = request.cookies.get(SESSION_COOKIE, "")
    if TOKEN_PATTERN.fullmatch(raw):
        return csrf_for_session(raw)
    token = request.cookies.get(CSRF_COOKIE, "")
    if not TOKEN_PATTERN.fullmatch(token):
        token = new_token()
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=900,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return token


def validate_csrf(request: Request, settings: Settings) -> None:
    # Origin is required even for API clients: do not infer trust from a supplied Host header.
    if request.headers.get("origin") not in settings.allowed_origins:
        raise AppError(403, "csrf_failed", "Request origin is not trusted")
    raw = request.cookies.get(SESSION_COOKIE, "")
    expected = (
        csrf_for_session(raw)
        if TOKEN_PATTERN.fullmatch(raw)
        else request.cookies.get(CSRF_COOKIE, "")
    )
    supplied = request.headers.get("x-csrf-token", "")
    if (
        not expected
        or len(supplied) > 128
        or not secrets.compare_digest(expected.encode(), supplied.encode())
    ):
        raise AppError(403, "csrf_failed", "CSRF validation failed")
