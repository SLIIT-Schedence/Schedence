"""Authentication and authorisation for the examination API.

Layering (same as the Identity and Academic Data services):

1. :func:`get_current_user`: verify the Cognito access token locally (auth-core),
   then resolve permissions through the Identity Service. Nothing is read from
   request headers other than the bearer token.
2. :func:`require_any_permission`: deny by default; the caller needs at least one
   of the permissions **in this service's institution**.

Reading examination data requires ``examinations:read``; changing seating and
scheduling settings, creating or updating exam periods and updating module
exam details requires ``examinations:manage``; generating a timetable
and saving it as a draft requires ``examinations:generate``. Saved timetables are
listed and viewed with ``examinations:read``; their seat allocations (student
IDs) need ``examinations:generate`` or ``examinations:approve``. Approving and
publishing a saved timetable require ``examinations:approve``. Student-specific access is
intentionally absent until a secure user -> student ID mapping exists.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.context import AuthContext
from app.auth.errors import AuthenticationError, PermissionDeniedError, ServiceUnavailableError
from app.auth.principal import Principal
from schedence_auth import JwksUnavailableError, TokenVerificationError

logger = logging.getLogger(__name__)

READ = "examinations:read"
MANAGE = "examinations:manage"
GENERATE = "examinations:generate"
APPROVE = "examinations:approve"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerToken",
                           description="Cognito **access token** (not the ID token).")


def get_auth(request: Request) -> AuthContext:
    return request.app.state.auth


async def get_current_user(
    request: Request,
    auth: Annotated[AuthContext, Depends(get_auth)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("A bearer access token is required.")
    try:
        claims = await auth.verifier.verify_access_token(credentials.credentials)
    except TokenVerificationError as exc:
        logger.info("Rejected access token: %s", exc.reason)
        if exc.status_code == 403:
            raise PermissionDeniedError("The access token lacks a required scope.") from None
        raise AuthenticationError("The access token is invalid or expired.") from None
    except JwksUnavailableError:
        raise ServiceUnavailableError("Token signing keys are temporarily unavailable.") from None
    # Reads may reuse permissions for a few seconds; writes always re-check.
    return await auth.permissions.principal_for(
        credentials.credentials, claims, fresh=request.method not in _SAFE_METHODS
    )


CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_any_permission(*permissions: str):
    """Caller must hold at least one of ``permissions`` in this service's institution."""

    async def dependency(principal: CurrentUser, auth: Annotated[AuthContext, Depends(get_auth)]) -> Principal:
        if not any(principal.has_permission(p, auth.settings.institution_id) for p in permissions):
            raise PermissionDeniedError()
        return principal

    return dependency


# Every examination GET route, including seating settings.
require_exam_data_access = require_any_permission(READ)

# Seating changes (including dry runs) do not also require read permission.
require_exam_settings_admin = require_any_permission(MANAGE)

# Timetable generation (runs the solver) and saving the result as a draft.
require_exam_generation = require_any_permission(GENERATE)

# Seat allocations of a saved timetable list student IDs: only who builds or approves timetables.
require_exam_seat_access = require_any_permission(GENERATE, APPROVE)

# Approving and publishing saved timetables.
require_exam_approval = require_any_permission(APPROVE)
