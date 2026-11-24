from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "http://localhost:5173"


def register(client: TestClient, name: str) -> str:
    token = client.get("/api/auth/csrf").json()["csrf_token"]
    response = client.post(
        "/api/auth/register",
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
        json={
            "email": f"{name}@example.com",
            "password": "a sufficiently long test password",
            "full_name": name,
            "organization": {"name": f"{name} Org", "slug": f"{name}-{uuid4().hex[:8]}"},
        },
    )
    assert response.status_code == 201
    return response.json()["selected_organization_id"]


def test_ai_status_is_optional_and_organization_scoped(postgres):
    settings, _, _ = postgres
    with TestClient(create_app(settings), base_url=ORIGIN) as owner:
        organization_id = register(owner, "ai-owner")
        response = owner.get(f"/api/organizations/{organization_id}/ai/status")
        assert response.status_code == 200
        assert response.json() == {
            "enabled": False,
            "state": "disabled",
            "model": "qwen3.5:4b-q4_K_M",
            "digest": None,
        }

    with TestClient(create_app(settings), base_url=ORIGIN) as outsider:
        register(outsider, "ai-outsider")
        response = outsider.get(f"/api/organizations/{organization_id}/ai/status")
        assert response.status_code == 404
