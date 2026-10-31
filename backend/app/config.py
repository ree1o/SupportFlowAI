import base64
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
    migration_database_url: SecretStr | None = None
    runtime_db_password: SecretStr | None = None
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
    redis_url: str = "redis://redis:6379/0"
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    email_from: str = "support@supportflow.local"
    public_app_url: str = "http://localhost:5173"
    radar_lookback_hours: int = Field(default=72, ge=1, le=720)
    radar_pair_window_minutes: int = Field(default=360, ge=5, le=1440)
    radar_similarity_threshold: float = Field(default=0.30, gt=0, le=1)
    radar_min_shared_terms: int = Field(default=2, ge=2, le=5)
    radar_max_tickets: int = Field(default=500, ge=10, le=2000)
    radar_max_candidate_pairs: int = Field(default=50000, ge=1000, le=250000)
    semantic_model_cache_dir: str = "/var/cache/supportflow/models"
    semantic_index_batch_size: int = Field(default=50, ge=1, le=200)
    semantic_index_max_attempts: int = Field(default=5, ge=1, le=10)
    semantic_min_similarity: float = Field(default=0.35, gt=0, le=1)
    retention_batch_size: int = Field(default=500, ge=1, le=5000)
    session_retention_days: int = Field(default=30, ge=1, le=3650)
    token_retention_days: int = Field(default=30, ge=1, le=3650)
    invitation_retention_days: int = Field(default=90, ge=1, le=3650)
    webhook_receipt_retention_days: int = Field(default=365, ge=1, le=3650)
    notification_retention_days: int = Field(default=180, ge=1, le=3650)
    completed_job_retention_days: int = Field(default=30, ge=1, le=3650)
    radar_run_retention_days: int = Field(default=90, ge=1, le=3650)
    stale_embedding_retention_days: int = Field(default=30, ge=1, le=3650)
    token_encryption_key: SecretStr = SecretStr("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        if self.session_absolute_seconds < self.session_ttl_seconds:
            raise ValueError("Absolute session lifetime must cover the session lifetime")
        try:
            encryption_key = base64.urlsafe_b64decode(
                self.token_encryption_key.get_secret_value().encode()
            )
        except (ValueError, TypeError):
            encryption_key = b""
        if len(encryption_key) != 32:
            raise ValueError("Token encryption key must be a URL-safe base64 32-byte key")
        if (
            self.environment == "production"
            and self.token_encryption_key.get_secret_value()
            == "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        ):
            raise ValueError("Production requires a dedicated token encryption key")
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
