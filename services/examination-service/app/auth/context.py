"""Everything the API needs to authenticate callers, created once per process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.auth.identity_client import IdentityPermissionsClient
from app.auth.principal import Principal
from app.auth.settings import ExaminationAuthSettings
from schedence_auth import AccessTokenClaims, build_verifier


class AccessTokenVerifier(Protocol):
    async def verify_access_token(self, token: str) -> AccessTokenClaims: ...


class PermissionResolver(Protocol):
    async def principal_for(self, token: str, claims: AccessTokenClaims, *, fresh: bool = False) -> Principal: ...


@dataclass
class AuthContext:
    settings: ExaminationAuthSettings
    verifier: AccessTokenVerifier
    permissions: PermissionResolver
    shutdown_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def close(self) -> None:
        for hook in self.shutdown_hooks:
            await hook()


def build_auth_context(settings: ExaminationAuthSettings) -> AuthContext:
    http = httpx.AsyncClient(follow_redirects=False)
    return AuthContext(
        settings=settings,
        verifier=build_verifier(settings),
        permissions=IdentityPermissionsClient(
            settings.identity_service_url,
            http=http,
            cache_seconds=settings.identity_permissions_cache_seconds,
            timeout_seconds=settings.identity_timeout_seconds,
        ),
        shutdown_hooks=[http.aclose],
    )
