from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.identity import security, service
from app.identity.models import AuthSession, Membership, Organization, User
from app.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"
PASSWORD = "a long test passphrase 2026"


def write(client, method, path, data=None):
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    return client.request(
        method, f"/api{path}", json=data, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )


def register(client, name="owner", **extra):
    payload = {
        "email": f"{name}@example.com",
        "password": PASSWORD,
        "full_name": name.title(),
        "organization": {"name": f"{name} company", "slug": f"{name}-company"},
        **extra,
    }
    return write(client, "POST", "/auth/register", payload)


@pytest.fixture
def clients(postgres):
    settings, database, _ = postgres
    with ExitStack() as stack:
        clients = [
            stack.enter_context(TestClient(create_app(settings), base_url=ORIGIN)) for _ in range(4)
        ]
        yield clients, database


@pytest.fixture
def team(clients):
    clients, database = clients
    owner, admin, agent, outsider = clients
    result = register(owner).json()
    organization = result["selected_organization_id"]
    for client, name in [(admin, "admin"), (agent, "agent"), (outsider, "outsider")]:
        assert register(client, name).status_code == 201
    for email, role in [("admin@example.com", "administrator"), ("agent@example.com", "agent")]:
        assert (
            write(
                owner,
                "POST",
                f"/organizations/{organization}/memberships",
                {"email": email, "role": role},
            ).status_code
            == 201
        )
    return clients, database, organization


def test_registration_normalizes_email_and_stores_only_hashes(clients):
    (owner, *_), database = clients
    response = register(owner, email="  OWNER@EXAMPLE.COM  ")
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "owner@example.com"
    assert body["organizations"][0]["role"] == "owner"
    assert "password" not in response.text and "token" not in response.text
    raw = owner.cookies.get(security.SESSION_COOKIE)
    with database.session() as db:
        user = db.scalar(select(User))
        session = db.scalar(select(AuthSession))
        assert user.password_hash.startswith("$argon2id$")
        assert security.verify_password(user.password_hash, PASSWORD)
        assert not security.verify_password(user.password_hash, "wrong password")
        assert session.token_hash == security.token_hash(raw) != raw
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    assert "secure;" not in cookie


def test_duplicate_registration_rolls_back_entire_onboarding(clients):
    (owner, other, *_), database = clients
    assert register(owner).status_code == 201
    response = register(other, "another", email="OWNER@example.com")
    assert response.status_code == 409
    assert "password" not in response.text
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(Organization)) == 1
    response = register(
        other, "different", organization={"name": "collision", "slug": "owner-company"}
    )
    assert response.status_code == 409
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "owner"},
        {"organization_id": str(uuid4())},
        {"password": "short"},
        {"email": "invalid"},
    ],
)
def test_registration_rejects_mass_assignment_and_bad_fields(clients, extra):
    (owner, *_), _ = clients
    assert register(owner, **extra).status_code == 422


def test_login_failure_success_rotation_and_logout(clients):
    (owner, *_), database = clients
    register(owner)
    first = owner.cookies.get(security.SESSION_COOKIE)
    assert write(owner, "POST", "/auth/logout").status_code == 204
    assert owner.get("/api/auth/me").status_code == 401
    for email in ["owner@example.com", "missing@example.com"]:
        response = write(
            owner, "POST", "/auth/login", {"email": email, "password": "wrong but long password"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
    response = write(
        owner, "POST", "/auth/login", {"email": "OWNER@EXAMPLE.COM", "password": PASSWORD}
    )
    assert response.status_code == 200
    second = owner.cookies.get(security.SESSION_COOKIE)
    assert second != first
    old_csrf = owner.get("/api/auth/csrf").json()["csrf_token"]
    absolute = response.json()["absolute_expires_at"]
    assert write(owner, "POST", "/auth/renew").json()["absolute_expires_at"] == absolute
    third = owner.cookies.get(security.SESSION_COOKIE)
    assert third != second
    assert (
        owner.post(
            "/api/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf}
        ).status_code
        == 403
    )
    with database.session() as db:
        records = db.scalars(select(AuthSession).order_by(AuthSession.created_at)).all()
        assert records[0].revoked_at and records[1].revoked_at and records[2].revoked_at is None
    assert write(owner, "POST", "/auth/logout").status_code == 204
    owner.cookies.set(security.SESSION_COOKIE, third)
    assert owner.get("/api/auth/me").status_code == 401


def test_login_replaces_previous_session_and_rehashes_old_password(clients):
    (owner, *_), database = clients
    register(owner)
    old = owner.cookies.get(security.SESSION_COOKIE)
    from argon2 import PasswordHasher

    with database.session() as db, db.begin():
        user = db.scalar(select(User))
        user.password_hash = PasswordHasher(time_cost=1).hash(PASSWORD)
    assert (
        write(
            owner, "POST", "/auth/login", {"email": "owner@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    with database.session() as db:
        assert db.scalar(
            select(AuthSession).where(AuthSession.token_hash == security.token_hash(old))
        ).revoked_at
        assert not security.hasher.check_needs_rehash(db.scalar(select(User)).password_hash)


def test_renewal_stays_within_original_absolute_deadline(clients):
    (owner, *_), database = clients
    register(owner)
    with database.session() as db, db.begin():
        record = db.scalar(select(AuthSession))
        record.absolute_expires_at = security.utcnow() + timedelta(seconds=90)
        record.expires_at = security.utcnow() + timedelta(seconds=60)
        absolute = record.absolute_expires_at
    response = write(owner, "POST", "/auth/renew")
    assert response.status_code == 200
    with database.session() as db:
        active = db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
        assert active.expires_at == active.absolute_expires_at == absolute


def test_wrong_login_preserves_an_existing_valid_session(clients):
    (owner, *_), database = clients
    register(owner)
    raw = owner.cookies.get(security.SESSION_COOKIE)
    response = write(
        owner,
        "POST",
        "/auth/login",
        {
            "email": "owner@example.com",
            "password": "incorrect long passphrase",
        },
    )
    assert response.status_code == 401
    assert owner.cookies.get(security.SESSION_COOKIE) == raw
    assert owner.get("/api/auth/me").status_code == 200
    with database.session() as db:
        assert db.scalar(select(AuthSession)).revoked_at is None


@pytest.mark.parametrize(
    "kind", ["missing", "malformed", "unknown", "expired", "absolute", "revoked", "disabled_user"]
)
def test_invalid_sessions_are_rejected(clients, kind):
    (owner, *_), database = clients
    register(owner)
    if kind in {"missing", "malformed", "unknown"}:
        owner.cookies.clear()
        if kind != "missing":
            owner.cookies.set(
                security.SESSION_COOKIE, "bad" if kind == "malformed" else security.new_token()
            )
    else:
        with database.session() as db, db.begin():
            record = db.scalar(select(AuthSession))
            if kind in {"expired", "absolute"}:
                record.created_at = security.utcnow() - timedelta(days=10)
                record.expires_at = security.utcnow() - timedelta(seconds=1)
                if kind == "absolute":
                    record.absolute_expires_at = record.expires_at
            elif kind == "revoked":
                record.revoked_at = security.utcnow()
            else:
                db.scalar(select(User)).is_active = False
    response = owner.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert owner.get("/api/organizations").status_code == 401


@pytest.mark.parametrize(
    "origin", [None, "null", "https://evil.example", "http://localhost:5173.evil.example"]
)
def test_csrf_rejects_untrusted_and_absent_origin(clients, origin):
    (owner, *_), _ = clients
    token = owner.get("/api/auth/csrf").json()["csrf_token"]
    headers = {"X-CSRF-Token": token}
    if origin is not None:
        headers["Origin"] = origin
    assert (
        owner.post(
            "/api/auth/login",
            json={"email": "owner@example.com", "password": PASSWORD},
            headers=headers,
        ).status_code
        == 403
    )


def test_csrf_login_registration_and_every_authenticated_mutation(clients):
    (owner, other, *_), _ = clients
    assert owner.post("/api/auth/register", json={}, headers={"Origin": ORIGIN}).status_code == 403
    register(owner)
    other_token = other.get("/api/auth/csrf").json()["csrf_token"]
    org = owner.get("/api/auth/me").json()["selected_organization_id"]
    member = owner.get(f"/api/organizations/{org}/memberships").json()[0]["id"]
    for method, path in [
        ("POST", "/auth/logout"),
        ("POST", "/auth/renew"),
        ("POST", "/organizations"),
        ("PATCH", f"/organizations/{org}"),
        ("POST", f"/organizations/{org}/select"),
        ("POST", f"/organizations/{org}/memberships"),
        ("PATCH", f"/organizations/{org}/memberships/{member}"),
        ("DELETE", f"/organizations/{org}/memberships/{member}"),
    ]:
        response = owner.request(
            method, f"/api{path}", headers={"Origin": ORIGIN, "X-CSRF-Token": other_token}, json={}
        )
        assert response.status_code == 403, (path, response.text)
    assert owner.get("/api/auth/me").status_code == 200


def test_organization_creation_switching_and_isolation(team):
    (owner, admin, agent, outsider), _, org = team
    new = write(
        owner, "POST", "/organizations", {"name": "Second workspace", "slug": "second-workspace"}
    )
    assert new.status_code == 201
    second = new.json()["id"]
    assert (
        write(owner, "POST", f"/organizations/{second}/select").json()["selected_organization_id"]
        == second
    )
    assert len(owner.get("/api/organizations").json()) == 2
    assert len(admin.get("/api/organizations").json()) == 2
    for client in [admin, agent, outsider]:
        assert client.get(f"/api/organizations/{second}").status_code == 404
        assert client.get(f"/api/organizations/{second}/memberships").status_code == 404
        assert write(client, "POST", f"/organizations/{second}/select").status_code == 404
        assert (
            write(client, "PATCH", f"/organizations/{second}", {"name": "intrusion"}).status_code
            == 404
        )
    assert outsider.get(f"/api/organizations/{org}").status_code == 404


def test_permissions_and_role_escalation(team):
    (owner, admin, agent, outsider), _, org = team
    members = owner.get(f"/api/organizations/{org}/memberships").json()
    by_email = {m["email"]: m for m in members}
    for client, role in [(owner, "owner"), (admin, "administrator"), (agent, "agent")]:
        assert client.get(f"/api/organizations/{org}").json()["role"] == role
        assert client.get(f"/api/organizations/{org}/memberships").status_code == 200
        assert write(client, "PATCH", f"/organizations/{org}", {"name": "Renamed"}).status_code == (
            403 if role == "agent" else 200
        )
    for client in [admin, agent]:
        own = by_email["admin@example.com" if client is admin else "agent@example.com"]
        assert (
            write(
                client,
                "PATCH",
                f"/organizations/{org}/memberships/{own['id']}",
                {"role": "owner", "is_active": True},
            ).status_code
            == 403
        )
        assert (
            write(
                client,
                "POST",
                f"/organizations/{org}/memberships",
                {"email": "outsider@example.com", "role": "administrator"},
            ).status_code
            == 403
        )
    assert (
        write(
            agent,
            "POST",
            f"/organizations/{org}/memberships",
            {"email": "outsider@example.com", "role": "agent"},
        ).status_code
        == 403
    )
    assert (
        write(
            admin,
            "POST",
            f"/organizations/{org}/memberships",
            {"email": "outsider@example.com", "role": "agent"},
        ).status_code
        == 201
    )
    own = by_email["owner@example.com"]["id"]
    assert write(admin, "DELETE", f"/organizations/{org}/memberships/{own}").status_code == 403


def test_disabled_removed_membership_and_reactivation(team):
    (owner, admin, agent, _), _, org = team
    write(agent, "POST", f"/organizations/{org}/select")
    target = next(
        m
        for m in owner.get(f"/api/organizations/{org}/memberships").json()
        if m["email"] == "agent@example.com"
    )
    path = f"/organizations/{org}/memberships/{target['id']}"
    assert write(admin, "DELETE", path).status_code == 204
    assert agent.get(f"/api/organizations/{org}").status_code == 404
    current = agent.get("/api/auth/me").json()
    assert current["selected_organization_id"] is None
    assert org not in {o["id"] for o in current["organizations"]}
    assert write(admin, "PATCH", path, {"role": "agent", "is_active": True}).status_code == 200
    assert agent.get(f"/api/organizations/{org}").status_code == 200


@pytest.mark.parametrize("action", ["demote", "disable", "remove"])
def test_final_owner_protection(team, action):
    (owner, *_), _, org = team
    member = next(
        m for m in owner.get(f"/api/organizations/{org}/memberships").json() if m["role"] == "owner"
    )
    path = f"/organizations/{org}/memberships/{member['id']}"
    response = (
        write(owner, "DELETE", path)
        if action == "remove"
        else write(
            owner,
            "PATCH",
            path,
            {"role": "agent" if action == "demote" else "owner", "is_active": action != "disable"},
        )
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "last_owner"


def test_concurrent_owners_cannot_both_demote_themselves(team):
    (owner, admin, *_), database, org = team
    members = owner.get(f"/api/organizations/{org}/memberships").json()
    admin_member = next(m for m in members if m["email"] == "admin@example.com")
    write(
        owner,
        "PATCH",
        f"/organizations/{org}/memberships/{admin_member['id']}",
        {"role": "owner", "is_active": True},
    )
    barrier = Barrier(2)

    def demote(client, email):
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        membership = next(m for m in members if m["email"] == email)
        barrier.wait(timeout=10)
        return client.patch(
            f"/api/organizations/{org}/memberships/{membership['id']}",
            json={"role": "agent", "is_active": True},
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(demote, owner, "owner@example.com"),
            pool.submit(demote, admin, "admin@example.com"),
        ]
        assert sorted(f.result(timeout=20) for f in futures) == [200, 409]
    with database.session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.organization_id == UUID(org),
                    Membership.role == "owner",
                    Membership.is_active.is_(True),
                )
            )
            == 1
        )


def test_cross_tenant_nested_membership_ids(team):
    (owner, _, _, outsider), _, org = team
    outsider_org = outsider.get("/api/auth/me").json()["selected_organization_id"]
    outsider_id = outsider.get(f"/api/organizations/{outsider_org}/memberships").json()[0]["id"]
    assert (
        write(
            owner,
            "PATCH",
            f"/organizations/{org}/memberships/{outsider_id}",
            {"role": "agent", "is_active": False},
        ).status_code
        == 404
    )
    assert (
        write(owner, "DELETE", f"/organizations/{org}/memberships/{outsider_id}").status_code == 404
    )
    assert outsider.get(f"/api/organizations/{outsider_org}").json()["role"] == "owner"


def test_membership_duplicate_and_invalid_role(team):
    (owner, *_), _, org = team
    assert (
        write(
            owner,
            "POST",
            f"/organizations/{org}/memberships",
            {"email": "agent@example.com", "role": "agent"},
        ).status_code
        == 409
    )
    assert (
        write(
            owner,
            "POST",
            f"/organizations/{org}/memberships",
            {"email": "agent@example.com", "role": "superuser"},
        ).status_code
        == 422
    )


def test_database_constraints_reinforce_identity_and_tenant_relationships(team):
    (owner, _, _, outsider), database, org = team
    owner_id = UUID(owner.get("/api/auth/me").json()["user"]["id"])
    outside_org = UUID(outsider.get("/api/auth/me").json()["selected_organization_id"])
    invalid_records = [
        User(email="UPPER@example.com", full_name="Bad", password_hash="not-a-real-hash"),
        User(email="owner@example.com", full_name="Duplicate", password_hash="not-a-real-hash"),
        Membership(organization_id=UUID(org), user_id=owner_id, role="agent"),
        Membership(organization_id=uuid4(), user_id=owner_id, role="agent"),
        Membership(organization_id=outside_org, user_id=owner_id, role="superuser"),
        AuthSession(
            user_id=owner_id,
            selected_organization_id=outside_org,
            token_hash=security.token_hash(security.new_token()),
            created_at=security.utcnow(),
            expires_at=security.utcnow() + timedelta(hours=1),
            absolute_expires_at=security.utcnow() + timedelta(days=1),
        ),
        AuthSession(
            user_id=owner_id,
            selected_organization_id=UUID(org),
            token_hash=security.new_token(),
            created_at=security.utcnow(),
            expires_at=security.utcnow() + timedelta(hours=1),
            absolute_expires_at=security.utcnow() + timedelta(days=1),
        ),
    ]
    for record in invalid_records:
        with pytest.raises(IntegrityError), database.session() as db, db.begin():
            db.add(record)
            db.flush()


def test_m1_upgrade_preserves_existing_m1_data(postgres):
    _, database, alembic = postgres
    command.downgrade(alembic, "0001_baseline")
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE m1_preservation_probe (value text NOT NULL)"))
        connection.execute(text("INSERT INTO m1_preservation_probe VALUES ('keep me')"))
    command.upgrade(alembic, "head")
    with database.session() as db:
        assert db.scalar(text("SELECT value FROM m1_preservation_probe")) == "keep me"


def test_password_and_session_values_never_appear_in_logs(clients, capsys):
    (owner, *_), _ = clients
    register(owner)
    raw = owner.cookies.get(security.SESSION_COOKIE)
    assert (
        write(
            owner,
            "POST",
            "/auth/login",
            {"email": "owner@example.com", "password": "wrong passphrase value"},
        ).status_code
        == 401
    )
    output = capsys.readouterr().out
    assert PASSWORD not in output and raw not in output and "wrong passphrase value" not in output


def test_mutation_rechecks_role_after_waiting_for_organization_lock(team):
    (owner, admin, *_), database, org = team
    loaded = Event()
    raw = admin.cookies.get(security.SESSION_COOKIE)

    def stale_administrator():
        with database.session() as db:
            auth = service.authenticate(db, raw)
            member = db.scalar(
                select(Membership).where(
                    Membership.organization_id == UUID(org),
                    Membership.user_id == auth.user.id,
                )
            )
            assert member.role == "administrator"
            loaded.set()
            try:
                service.update_organization(db, auth, UUID(org), "unauthorized rename")
            except AppError as exc:
                return exc.status
            return 200

    with ThreadPoolExecutor(max_workers=1) as pool, database.session() as db:
        db.scalar(select(Organization).where(Organization.id == UUID(org)).with_for_update())
        future = pool.submit(stale_administrator)
        assert loaded.wait(timeout=10)
        user_id = UUID(admin.get("/api/auth/me").json()["user"]["id"])
        actor = db.scalar(
            select(Membership).where(
                Membership.organization_id == UUID(org),
                Membership.user_id == user_id,
            )
        )
        actor.role = "agent"
        db.commit()
        assert future.result(timeout=10) == 403


def test_outsider_query_does_not_lock_another_tenant(team):
    (_, _, _, outsider), database, org = team
    outsider_id = UUID(outsider.get("/api/auth/me").json()["user"]["id"])
    with database.session() as locked, database.session() as untrusted:
        locked.scalar(select(Organization).where(Organization.id == UUID(org)).with_for_update())
        untrusted.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(AppError) as error:
            service.organization_context(untrusted, outsider_id, UUID(org), write=True)
        assert error.value.status == 404
