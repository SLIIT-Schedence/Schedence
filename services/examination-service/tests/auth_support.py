"""Test doubles for authentication: in-memory Cognito keys and a fake Identity Service.

Nothing here talks to AWS or the real Identity Service.
"""

from __future__ import annotations

import uuid
from itertools import combinations
from uuid import UUID

import httpx
import jwt

from app.auth.context import AuthContext
from app.auth.identity_client import IdentityPermissionsClient
from app.auth.principal import Principal
from app.auth.settings import ExaminationAuthSettings
from schedence_auth.testing import TEST_CLIENT_ID, TEST_POOL_ID, TEST_REGION, TokenFactory

INSTITUTION = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_INSTITUTION = uuid.UUID("22222222-2222-4222-8222-222222222222")

# Mirrors services/identity-service/app/rbac/catalog.py (the parts relevant here).
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "SUPER_ADMIN": ["identity:users:read", "identity:roles:assign", "academic:staff:read", "academic:workload:read"],
    "ACADEMIC_ADMIN": ["teaching:generate", "teaching:approve", "academic:staff:read", "venues:read"],
    "EXAMINATION_ADMIN": ["examinations:read", "examinations:manage", "examinations:generate", "examinations:approve", "academic:staff:read",
                          "academic:workload:read", "venues:read"],
    "VENUE_MANAGER": ["venues:read", "venues:manage"],
    "LECTURER": [],
    "STUDENT": [],
}

EXAMINATION_PERMISSIONS = (
    "examinations:read", "examinations:manage", "examinations:generate", "examinations:approve",
)
EXAMINATION_PERMISSION_SETS = [
    list(subset) for size in range(len(EXAMINATION_PERMISSIONS) + 1)
    for subset in combinations(EXAMINATION_PERMISSIONS, size)
]


class FakeIdentityService:
    """Answers GET /api/v1/identity/me/permissions like the real Identity Service and records requests."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.requests: list[tuple[str, str]] = []
        self.down = False
        self.status_override: int | None = None
        self.answer_for_sub: str | None = None  # simulate a response about a different user

    def register(self, institutions: dict[UUID, tuple[list[str], list[str]]], *, status: str = "ACTIVE") -> str:
        sub = str(uuid.uuid4())
        self.accounts[sub] = {"user_id": uuid.uuid4(), "status": status, "institutions": institutions}
        return sub

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError("identity service down", request=request)
        if self.status_override is not None:
            return httpx.Response(self.status_override, json={"error": {"code": "X", "message": "x"}})
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        sub = jwt.decode(token, options={"verify_signature": False}).get("sub")  # the real service verifies
        account = self.accounts.get(sub)
        if account is None:
            return httpx.Response(403, json={"error": {"code": "ACCOUNT_NOT_PROVISIONED", "message": "x"}})
        if account["status"] != "ACTIVE":
            return httpx.Response(403, json={"error": {"code": "ACCOUNT_DISABLED", "message": "x"}})
        return httpx.Response(200, json={
            "user_id": str(account["user_id"]),
            "cognito_sub": self.answer_for_sub or sub,
            "account_status": "ACTIVE",
            "institutions": [{"institution_id": str(i), "roles": roles, "permissions": perms}
                             for i, (roles, perms) in account["institutions"].items()],
        })


def test_settings(**overrides) -> ExaminationAuthSettings:
    values = dict(app_env="testing", aws_region=TEST_REGION, cognito_user_pool_id=TEST_POOL_ID,
                  cognito_app_client_id=TEST_CLIENT_ID, identity_service_url="http://identity.test",
                  institution_id=INSTITUTION)
    return ExaminationAuthSettings(_env_file=None, **{**values, **overrides})


test_settings.__test__ = False  # a helper, not a pytest test


def make_auth_context(tokens: TokenFactory | None = None, identity: FakeIdentityService | None = None,
                      **settings_overrides) -> AuthContext:
    settings = test_settings(**settings_overrides)
    identity = identity or FakeIdentityService()
    http = httpx.AsyncClient(transport=httpx.MockTransport(identity.handler))
    return AuthContext(
        settings=settings,
        verifier=(tokens or TokenFactory()).verifier(),
        permissions=IdentityPermissionsClient(settings.identity_service_url, http=http, cache_seconds=15),
    )


EXAM_ADMIN = Principal(
    user_id=uuid.uuid4(), cognito_sub="exam-admin",
    permissions_by_institution={INSTITUTION: frozenset(ROLE_PERMISSIONS["EXAMINATION_ADMIN"])},
    roles_by_institution={INSTITUTION: frozenset({"EXAMINATION_ADMIN"})},
)
