from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from app.core.clock import Clock, utcnow
from app.core.config import SERVICE_NAME, AcademicSettings
from app.core.principal import Principal
from app.core.rate_limit import RateLimiter
from app.integrations.identity_client import IdentityPermissionsClient
from app.repositories.base import AcademicUnitOfWork
from schedence_auth import AccessTokenClaims, build_verifier
from schedence_db import DatabaseHealth


class AccessTokenVerifier(Protocol):
    async def verify_access_token(self, token: str) -> AccessTokenClaims: ...


class PermissionResolver(Protocol):
    async def principal_for(self, token: str, claims: AccessTokenClaims, *, fresh: bool = False) -> Principal: ...


@dataclass
class AcademicContainer:
    settings: AcademicSettings
    verifier: AccessTokenVerifier
    permissions: PermissionResolver
    uow_factory: Callable[[], AbstractAsyncContextManager[AcademicUnitOfWork]]
    db_health: Callable[[], Awaitable[DatabaseHealth]]
    rate_limiter: RateLimiter
    clock: Clock = utcnow
    today: Callable[[], date] | None = None
    shutdown_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def institution_today(self) -> date:
        if self.today is not None:
            return self.today()
        return datetime.now(ZoneInfo(self.settings.institution_timezone)).date()


def build_container(settings: AcademicSettings) -> AcademicContainer:
    from app.repositories.sql import SqlAcademicUnitOfWork
    from schedence_db import check_database, create_db_engine, create_session_factory, dispose_engine, load_settings, session_scope

    db_settings = load_settings()
    if db_settings.db_application_name is None:
        db_settings = db_settings.model_copy(update={"db_application_name": SERVICE_NAME})
    engine = create_db_engine(db_settings)
    session_factory = create_session_factory(engine)
    http = httpx.AsyncClient(follow_redirects=False)

    @asynccontextmanager
    async def uow_factory() -> AsyncIterator[AcademicUnitOfWork]:
        async with session_scope(session_factory, commit=False) as session:
            yield SqlAcademicUnitOfWork(session)

    async def db_health() -> DatabaseHealth:
        return await check_database(engine)

    async def close() -> None:
        await http.aclose()
        await dispose_engine(engine)

    return AcademicContainer(
        settings=settings,
        verifier=build_verifier(settings),
        permissions=IdentityPermissionsClient(settings.identity_service_url, http=http,
                                              cache_seconds=settings.identity_permissions_cache_seconds,
                                              timeout_seconds=settings.identity_timeout_seconds),
        uow_factory=uow_factory,
        db_health=db_health,
        rate_limiter=RateLimiter(settings.rate_limit_writes_per_minute),
        shutdown_hooks=[close],
    )
