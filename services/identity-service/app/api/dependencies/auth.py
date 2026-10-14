"""Reusable authentication and authorisation dependencies.

Layering:

1. :func:`get_access_token_claims` - Cognito verification only (signature, issuer,
   expiry, ``token_use``, ``client_id``). Knows nothing about roles.
2. :func:`get_current_user` - maps the verified ``sub`` to an *active* local
   account and loads its roles/permissions from the database (never from
   headers or token groups).
3. :func:`require_permissions` / :func:`require_roles` /
   :func:`require_institution_scope` - deny-by-default checks on top.

Services repeat institution-specific checks for resources whose institution
is only known from the database or the request body.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies.common import ContainerDep, UnitOfWorkDep
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError, ServiceUnavailableError, ValidationFailedError
from app.core.principal import Principal
from app.services.principals import resolve_principal
from schedence_auth import AccessTokenClaims, JwksUnavailableError, TokenVerificationError

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerToken",
                           description="Cognito **access token** (not the ID token).")


async def get_access_token_claims(
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> AccessTokenClaims:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("A bearer access token is required.")
    try:
        return await container.verifier.verify_access_token(credentials.credentials)
    except TokenVerificationError as exc:
        logger.info("Rejected access token: %s", exc.reason)
        if exc.status_code == 403:
            raise PermissionDeniedError(str(exc), code="INSUFFICIENT_SCOPE") from None
        raise AuthenticationError(str(exc), code="INVALID_TOKEN") from None
    except JwksUnavailableError:
        raise ServiceUnavailableError("Token signing keys are temporarily unavailable.", code="AUTH_UNAVAILABLE") from None


AccessTokenClaimsDep = Annotated[AccessTokenClaims, Depends(get_access_token_claims)]


async def get_current_user(claims: AccessTokenClaimsDep, uow: UnitOfWorkDep) -> Principal:
    return await resolve_principal(uow, claims)


CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_permissions(*permissions: str):
    """Caller must hold *all* permissions (in at least one institution)."""

    async def dependency(principal: CurrentUser) -> Principal:
        if not all(principal.has_permission(p) for p in permissions):
            raise PermissionDeniedError(details={"required_permissions": list(permissions)})
        return principal

    return dependency


def require_roles(*role_codes: str):
    """Caller must hold *any* of the roles (in at least one institution)."""

    async def dependency(principal: CurrentUser) -> Principal:
        if not any(principal.has_role(code) for code in role_codes):
            raise PermissionDeniedError(details={"required_roles": list(role_codes)})
        return principal

    return dependency


def require_institution_scope(parameter: str = "institution_id", permission: str | None = None):
    """Caller must belong to (or hold ``permission`` in) the institution named by a path/query parameter."""

    async def dependency(request: Request, principal: CurrentUser) -> Principal:
        raw = request.path_params.get(parameter) or request.query_params.get(parameter)
        if raw is None:
            raise ValidationFailedError(f"'{parameter}' is required.")
        try:
            institution_id = UUID(str(raw))
        except ValueError:
            raise ValidationFailedError(f"'{parameter}' must be a UUID.") from None
        allowed = principal.has_permission(permission, institution_id) if permission else institution_id in principal.institution_ids
        if not allowed:
            raise PermissionDeniedError("You do not have access to this institution.", code="INSTITUTION_SCOPE")
        return principal

    return dependency


async def enforce_write_rate_limit(principal: CurrentUser, container: ContainerDep) -> None:
    if not container.rate_limiter.hit(("write", principal.user_id)):
        raise RateLimitedError()
