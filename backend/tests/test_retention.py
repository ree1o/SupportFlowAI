from datetime import timedelta
from uuid import uuid4

from app.identity.models import AuthSession, User
from app.identity.security import utcnow
from app.retention import cleanup_transient_data


def test_retention_dry_run_and_cleanup_preserve_active_session(postgres, settings):
    _config, database, _alembic = postgres
    now = utcnow()
    user_id = uuid4()
    expired_id, active_id = uuid4(), uuid4()
    with database.session() as db:
        db.add(
            User(
                id=user_id,
                email=f"retention-{user_id}@example.test",
                full_name="Fixture",
                password_hash="not-a-real-hash",
            )
        )
        db.flush()
        db.add_all(
            [
                AuthSession(
                    id=expired_id,
                    user_id=user_id,
                    token_hash=uuid4().hex + uuid4().hex,
                    selected_organization_id=None,
                    created_at=now - timedelta(days=60),
                    expires_at=now - timedelta(days=40),
                    absolute_expires_at=now - timedelta(days=39),
                ),
                AuthSession(
                    id=active_id,
                    user_id=user_id,
                    token_hash=uuid4().hex + uuid4().hex,
                    selected_organization_id=None,
                    created_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                    absolute_expires_at=now + timedelta(days=2),
                ),
            ]
        )
        db.commit()
        preview = cleanup_transient_data(db, settings, dry_run=True)
        assert preview["sessions"] == 1
        assert db.get(AuthSession, expired_id) is not None
        removed = cleanup_transient_data(db, settings)
        assert removed["sessions"] == 1
        assert db.get(AuthSession, expired_id) is None
        assert db.get(AuthSession, active_id) is not None
