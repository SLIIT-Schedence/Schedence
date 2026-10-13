"""Wiring of process-wide dependencies (engine, verifier, Cognito client, ...).

Tests build an :class:`IdentityContainer` from fakes and pass it to
``create_app(container=...)``; production builds one from settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from app.core.clock import Clock, utcnow
from app.core.config import SERVICE_NAME, IdentitySettings
from app.core.rate_limit import RateLimiter
from app.integrations.cognito import CognitoAdminClient
from app.repositories.base import IdentityUnitOfWork
from schedence_auth import AccessTokenClaims, build_verifier
from schedence_db import DatabaseHealth


class AccessTokenVerifier(Protocol):
    async def verify_access_token(self, token: str) -> AccessTokenClaims: ...


UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[IdentityUnitOfWork]]


@dataclass
class IdentityContainer:
    settings: IdentitySettings
    verifier: AccessTokenVerifier
    cognito: CognitoAdminClient
    uow_factory: UnitOfWorkFactory
    db_health: Callable[[], Awaitable[DatabaseHealth]]
    rate_limiter: RateLimiter
    clock: Clock = utcnow
    shutdown_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


def build_container(settings: IdentitySettings) -> IdentityContainer:
    """Production wiring. Creating the engine and clients opens no connections."""
    from app.integrations.cognito.boto3_client import Boto3CognitoAdminClient
    from app.repositories.sql import SqlIdentityUnitOfWork
    from schedence_db import check_database, create_db_engine, create_session_factory, dispose_engine, load_settings, session_scope

    db_settings = load_settings()
    if db_settings.db_application_name is None:
        db_settings = db_settings.model_copy(update={"db_application_name": SERVICE_NAME})
    engine = create_db_engine(db_settings)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def uow_factory() -> AsyncIterator[IdentityUnitOfWork]:
        async with session_scope(session_factory, commit=False) as session:
            yield SqlIdentityUnitOfWork(session)

    async def db_health() -> DatabaseHealth:
        return await check_database(engine)

    async def dispose() -> None:
        await dispose_engine(engine)

    return IdentityContainer(
        settings=settings,
        verifier=build_verifier(settings),
        cognito=Boto3CognitoAdminClient(region=settings.aws_region, user_pool_id=settings.cognito_user_pool_id,
                                        profile=settings.aws_profile, timeout_seconds=settings.cognito_admin_timeout_seconds),
        uow_factory=uow_factory,
        db_health=db_health,
        rate_limiter=RateLimiter(settings.rate_limit_writes_per_minute),
        shutdown_hooks=[dispose],
    )
