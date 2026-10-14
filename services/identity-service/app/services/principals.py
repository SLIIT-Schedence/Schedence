"""Turn a verified Cognito access token into a Schedence principal.

Cognito proves *who* the caller is; this step decides whether that identity
has an active Schedence account and *what* it may do. Permissions are read
from the database on every request, so disabling an account or revoking a role
takes effect immediately, even for tokens that have not expired yet.
"""

from __future__ import annotations

from app.core.errors import PermissionDeniedError
from app.core.principal import Principal
from app.repositories.base import IdentityUnitOfWork
from schedence_auth import AccessTokenClaims


async def resolve_principal(uow: IdentityUnitOfWork, claims: AccessTokenClaims) -> Principal:
    user = await uow.users.get_by_cognito_sub(claims.sub)
    if user is None:
        raise PermissionDeniedError("No Schedence account is linked to this identity.", code="ACCOUNT_NOT_PROVISIONED")
    if not user.is_active:
        raise PermissionDeniedError("This account is disabled.", code="ACCOUNT_DISABLED")
    grants = await uow.roles.get_grants(user.id)
    return Principal.from_grants(user_id=user.id, cognito_sub=user.cognito_sub, email=user.email, full_name=user.full_name,
                                 grants=grants)
