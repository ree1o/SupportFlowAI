"""Transaction-local database context used by PostgreSQL RLS policies.

Callers must first authorize the user or trusted system operation in application code.
The settings are local to the current database transaction and reset on commit/rollback.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def _set(db: Session, name: str, value: str) -> None:
    db.execute(
        text("SELECT set_config(:setting_name, :setting_value, true)"),
        {"setting_name": name, "setting_value": value},
    )


def set_tenant_context(db: Session, organization_id: UUID) -> None:
    _set(db, "supportflow.organization_id", str(organization_id))


def set_user_context(db: Session, user_id: UUID) -> None:
    _set(db, "supportflow.user_id", str(user_id))


def set_integration_source_context(db: Session, source_id: UUID) -> None:
    """Permit one source lookup before signature validation; writes still require tenant context."""
    _set(db, "supportflow.integration_source_id", str(source_id))


def set_account_token_context(db: Session, token_hash: str) -> None:
    """Permit lookup of exactly one submitted, hashed account token."""
    _set(db, "supportflow.account_token_hash", token_hash)


def set_invitation_token_context(db: Session, token_hash: str) -> None:
    """Permit lookup of exactly one submitted, hashed invitation token."""
    _set(db, "supportflow.invitation_token_hash", token_hash)
