"""Read-only venue export for the Teaching service. Synthetic ids only."""

from __future__ import annotations

import uuid

import httpx
from fastapi.testclient import TestClient

from schedence_auth.testing import TokenFactory
from schedence_resource.main import IdentityPermissions, ResourceContainer, create_app

INSTITUTION = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER = uuid.UUID("22222222-2222-4222-8222-222222222222")
SOURCE = uuid.UUID("33333333-3333-4333-8333-333333333333")
PATH = "/api/v1/resource/teaching-inputs"


def build(grants: dict[uuid.UUID, list[str]], *, status: str = "ACTIVE"):
    tokens, sub = TokenFactory(), "SYN-SUB-1"

    def identity(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_id": str(uuid.uuid4()), "cognito_sub": sub, "account_status": status,
            "institutions": [{"institution_id": str(i), "roles": [], "permissions": p} for i, p in grants.items()]})

    exported: list[uuid.UUID] = []

    async def export(institution_id: uuid.UUID) -> dict[str, list[dict]]:
        exported.append(institution_id)
        return {"source_datasets": [{"id": SOURCE, "institution_id": institution_id, "sha256": "0" * 64}],
                "venues": [{"id": uuid.uuid4(), "institution_id": institution_id, "venue_code": "SYN-V1", "capacity": 60,
                            "source_id": SOURCE}]}

    http = httpx.AsyncClient(transport=httpx.MockTransport(identity))
    container = ResourceContainer(tokens.verifier(), IdentityPermissions("http://identity.test", http), export)
    return TestClient(create_app(container)), {"Authorization": f"Bearer {tokens.access_token(sub)}"}, exported


def test_export_is_complete_and_scoped_to_the_institution() -> None:
    client, headers, exported = build({INSTITUTION: ["venues:read"]})
    response = client.get(PATH, headers=headers, params={"institution_id": str(INSTITUTION)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_id"] == str(INSTITUTION) and body["complete"] is True
    assert set(body["tables"]) == {"source_datasets", "venues"}
    assert body["tables"]["venues"][0]["source_id"] == str(SOURCE)
    assert exported == [INSTITUTION] and response.headers["cache-control"] == "no-store"


def test_export_requires_a_token_and_venues_read_in_that_institution() -> None:
    client, headers, exported = build({INSTITUTION: ["academic:staff:read"]})
    params = {"institution_id": str(INSTITUTION)}
    assert client.get(PATH, params=params).status_code == 401
    assert client.get(PATH, headers={"Authorization": "Bearer not-a-token"}, params=params).status_code == 401
    assert client.get(PATH, headers=headers, params=params).status_code == 403
    assert client.get(PATH, headers=headers, params={"institution_id": str(OTHER)}).status_code == 404
    assert exported == []


def test_inactive_accounts_are_refused() -> None:
    client, headers, exported = build({INSTITUTION: ["venues:read"]}, status="DISABLED")
    assert client.get(PATH, headers=headers, params={"institution_id": str(INSTITUTION)}).status_code == 403
    assert exported == []
