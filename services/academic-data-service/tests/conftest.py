from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest

from app.core.config import AcademicSettings
from app.core.container import AcademicContainer
from app.core.rate_limit import RateLimiter
from app.integrations.identity_client import IdentityPermissionsClient
from app.main import create_app
from schedence_auth.testing import TEST_CLIENT_ID, TEST_POOL_ID, TEST_REGION, TokenFactory
from schedence_db import DatabaseHealth
from tests.fakes import FakeAcademicStore, FakeIdentityService, uow_factory

INSTITUTION = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_INSTITUTION = uuid.UUID("22222222-2222-4222-8222-222222222222")
TODAY = date(2026, 10, 13)

ACADEMIC_PERMISSIONS = [
    "academic:positions:create", "academic:positions:read", "academic:positions:update", "academic:positions:disable",
    "academic:appointments:assign", "academic:appointments:read", "academic:appointments:update", "academic:appointments:end",
    "academic:staff:create", "academic:staff:read", "academic:workload:read",
]
# Mirrors the Identity Service catalogue (academic part only).
ROLE_PERMISSIONS = {
    "INSTITUTION_ADMIN": ACADEMIC_PERMISSIONS,
    "ACADEMIC_ADMIN": ["academic:positions:read", "academic:appointments:read", "academic:staff:read", "academic:workload:read"],
    "EXAMINATION_ADMIN": ["academic:staff:read", "academic:workload:read"],
    "LECTURER": [],
    "STUDENT": [],
}
# Platform roles are granted outside any institution and carry no academic permissions.
PLATFORM_ROLE_PERMISSIONS = {"SYSTEM_ADMIN": ["platform:institutions:create", "platform:institution_admins:create"]}
_PREFIXES = ("AWS_", "COGNITO_", "DATABASE_URL", "DIRECT_DATABASE_URL", "DB_", "APP_ENV", "SCHEDENCE_ENV_FILE", "IDENTITY_", "FRONTEND_URL")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(_PREFIXES):
            monkeypatch.delenv(name)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def store() -> FakeAcademicStore:
    return FakeAcademicStore()


@pytest.fixture
def identity() -> FakeIdentityService:
    return FakeIdentityService()


@pytest.fixture
async def container(tokens: TokenFactory, store: FakeAcademicStore, identity: FakeIdentityService) -> AsyncIterator[AcademicContainer]:
    settings = AcademicSettings(_env_file=None, app_env="testing", aws_region=TEST_REGION, cognito_user_pool_id=TEST_POOL_ID,
                                cognito_app_client_id=TEST_CLIENT_ID, identity_service_url="http://identity.test")
    http = httpx.AsyncClient(transport=httpx.MockTransport(identity.handler))

    async def healthy() -> DatabaseHealth:
        return DatabaseHealth(healthy=True, latency_ms=1.0)

    yield AcademicContainer(
        settings=settings, verifier=tokens.verifier(),
        permissions=IdentityPermissionsClient(settings.identity_service_url, http=http, cache_seconds=15),
        uow_factory=uow_factory(store), db_health=healthy, rate_limiter=RateLimiter(10_000), today=lambda: TODAY,
    )
    await http.aclose()


@pytest.fixture
async def client(container: AcademicContainer) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container)), base_url="http://academic.test") as http:
        yield http


@pytest.fixture
def caller(identity: FakeIdentityService, tokens: TokenFactory):
    """Headers for a caller whose Identity-Service roles are given as (ROLE, institution) pairs (platform roles: just the code)."""

    def make(*roles: str | tuple[str, uuid.UUID], status: str = "ACTIVE") -> dict[str, str]:
        institutions: dict[uuid.UUID, tuple[list[str], list[str]]] = {}
        platform: dict[str, list[str]] = {}
        for item in roles:
            if item in PLATFORM_ROLE_PERMISSIONS:
                platform[item] = PLATFORM_ROLE_PERMISSIONS[item]
                continue
            role, institution = (item, INSTITUTION) if isinstance(item, str) else item
            names, perms = institutions.setdefault(institution, ([], []))
            names.append(role)
            perms.extend(p for p in ROLE_PERMISSIONS[role] if p not in perms)
        sub, user_id = identity.register(institutions, status=status, platform=platform)
        headers = {"Authorization": f"Bearer {tokens.access_token(sub)}"}
        headers["X-Test-User-Id"] = str(user_id)  # for assertions only; the service ignores it
        return headers

    return make
