"""Shared fixtures. Everything is in memory: fake repositories, fake Cognito, locally signed JWTs."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import IdentitySettings
from app.core.container import IdentityContainer
from app.core.principal import Principal
from app.core.rate_limit import RateLimiter
from app.main import create_app
from app.models import ACCOUNT_ACTIVE, User, UserRole
from app.services.principals import resolve_principal
from app.services.seeding import RbacSeeder
from schedence_auth.testing import TEST_CLIENT_ID, TEST_POOL_ID, TEST_REGION, TokenFactory
from schedence_db import DatabaseHealth
from tests.fakes import FakeCognito, FakeStore, FakeUnitOfWork, uow_factory

INSTITUTION = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_INSTITUTION = uuid.UUID("22222222-2222-4222-8222-222222222222")

_PREFIXES = ("AWS_", "COGNITO_", "DATABASE_URL", "DIRECT_DATABASE_URL", "DB_", "APP_ENV", "SCHEDENCE_ENV_FILE", "FRONTEND_URL", "CORS_")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never see the developer's real .env, database URL, or AWS profile."""
    for name in list(os.environ):
        if name.upper().startswith(_PREFIXES):
            monkeypatch.delenv(name)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()


@pytest.fixture
def settings() -> IdentitySettings:
    return IdentitySettings(_env_file=None, app_env="testing", aws_region=TEST_REGION, cognito_user_pool_id=TEST_POOL_ID,
                            cognito_app_client_id=TEST_CLIENT_ID)


@pytest.fixture
async def store() -> FakeStore:
    store = FakeStore()
    await RbacSeeder(FakeUnitOfWork(store)).seed()
    return store


@pytest.fixture
def cognito() -> FakeCognito:
    return FakeCognito()


@pytest.fixture
def container(settings: IdentitySettings, tokens: TokenFactory, cognito: FakeCognito, store: FakeStore) -> IdentityContainer:
    async def healthy() -> DatabaseHealth:
        return DatabaseHealth(healthy=True, latency_ms=1.0)

    return IdentityContainer(settings=settings, verifier=tokens.verifier(), cognito=cognito, uow_factory=uow_factory(store),
                             db_health=healthy, rate_limiter=RateLimiter(10_000))


@pytest.fixture
async def client(container: IdentityContainer) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://identity.test") as http:
        yield http


@dataclass
class Account:
    user: User
    token: str
    headers: dict[str, str]

    @property
    def id(self) -> str:
        return str(self.user.id)


@pytest.fixture
def make_account(store: FakeStore, cognito: FakeCognito, tokens: TokenFactory):
    """Create a local user (+ Cognito identity) with roles directly in the store, and a valid token for it."""

    def make(email: str, *roles: tuple[str, uuid.UUID] | str, status: str = ACCOUNT_ACTIVE, full_name: str | None = None) -> Account:
        identity = cognito.add_user(email)
        now = datetime.now(UTC)
        user = User(id=uuid.uuid4(), cognito_sub=identity.sub, email=email.lower(), full_name=full_name or email.split("@")[0].title(),
                    account_status=status, created_at=now, updated_at=now)
        store.users[user.id] = user
        for item in roles:
            code, institution = (item, INSTITUTION) if isinstance(item, str) else item
            assignment = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=store.role(code).id, institution_id=institution, assigned_at=now)
            store.user_roles[assignment.id] = assignment
        store.snapshot()
        token = tokens.access_token(identity.sub)
        return Account(user, token, {"Authorization": f"Bearer {token}"})

    return make


@pytest.fixture
def principal_of(store: FakeStore, tokens: TokenFactory):
    async def resolve(account: Account) -> Principal:
        claims = await tokens.verifier().verify_access_token(account.token)
        return await resolve_principal(FakeUnitOfWork(store), claims)

    return resolve
