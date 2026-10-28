from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPPORTFLOW_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Root .env also contains Compose's POSTGRES_* variables.
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_timeout_seconds: int = Field(default=3, ge=1, le=30)
    # HTTP cookies are an explicit development-only option. Production fails closed.
    cookie_secure: bool = True
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    session_ttl_seconds: int = Field(default=28800, ge=60, le=86400)
    session_absolute_seconds: int = Field(default=604800, ge=60, le=2592000)

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        if self.session_absolute_seconds < self.session_ttl_seconds:
            raise ValueError("Absolute session lifetime must cover the session lifetime")
        if not self.allowed_origins:
            raise ValueError("At least one trusted origin is required")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or "*" in origin
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or (self.environment == "production" and parsed.scheme != "https")
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
            ):
                raise ValueError("Origins must be exact HTTPS origins or local development origins")
        return self

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+psycopg"
                and bool(url.host)
                and bool(url.database)
                and bool(url.username)
            )
            _ = url.port
        except (ArgumentError, ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("Use a postgresql+psycopg URL with host, database, and username")
        return value
