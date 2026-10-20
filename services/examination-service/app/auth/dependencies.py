"""Authentication and authorisation for the examination API.

Layering (same as the Identity and Academic Data services):

1. :func:`get_current_user`: verify the Cognito access token locally (auth-core),
   then resolve permissions through the Identity Service.
2. :func:`require_any_permission`: deny by default; the caller needs at least one
   of the permissions **in the request's institution**, which is resolved from the
   caller's own permissions (:func:`resolve_institution`), never from configuration.
   A caller may name the institution in the ``X-Institution-Id`` header; it is only
   used when the caller holds the permission there. A caller who holds it in
   several institutions must send the header (400 otherwise). The dependency
   returns an :class:`ExamAccess` (the caller and the resolved institution).
3. Dataset routes (modules, students, registrations, venues, exam periods,
   seating rules) and the routes that run the solver on it (generate, save a
   draft, regenerate, approve, publish) also require the institution to own the imported dataset
   (``examinations.exam_dataset_owner``, see :mod:`app.db.dataset_models`).

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
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.context import AuthContext
from app.auth.errors import AuthenticationError, PermissionDeniedError, ServiceUnavailableError
from app.auth.principal import Principal
from app.dataset_owner import dataset_owner
from schedence_auth import JwksUnavailableError, TokenVerificationError
from schedence_db import session_scope

logger = logging.getLogger(__name__)

READ = "examinations:read"
MANAGE = "examinations:manage"
GENERATE = "examinations:generate"
APPROVE = "examinations:approve"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
INSTITUTION_HEADER = "X-Institution-Id"

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


@dataclass(frozen=True)
class ExamAccess:
    """The caller and the institution whose examination data the request may use."""

    principal: Principal
    institution_id: UUID

    @property
    def user_id(self) -> UUID:
        return self.principal.user_id


class InstitutionRequiredError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, detail)


def requested_institution(
    x_institution_id: Annotated[str | None, Header(
        alias=INSTITUTION_HEADER,
        description="Institution to act in. Needed only when you hold the permission in more than one institution.",
    )] = None,
) -> UUID | None:
    if x_institution_id is None:
        return None
    try:
        return UUID(x_institution_id.strip())
    except ValueError:
        raise InstitutionRequiredError(f"{INSTITUTION_HEADER} must be an institution ID (a UUID).") from None


def resolve_institution(principal: Principal, permissions: Iterable[str], requested: UUID | None) -> UUID:
    """The institution in which the caller holds one of ``permissions``. Deny by default.

    An explicit ``requested`` institution is used only when the caller holds a
    permission there. Without one, the caller must hold it in exactly one
    institution; several give 400 (never a silent choice), none gives 403.
    """
    permissions = tuple(permissions)
    allowed = [i for i in principal.permissions_by_institution
               if any(principal.has_permission(p, i) for p in permissions)]
    if requested is not None:
        if requested in allowed:
            return requested
        raise PermissionDeniedError()
    if len(allowed) == 1:
        return allowed[0]
    if not allowed:
        raise PermissionDeniedError()
    raise InstitutionRequiredError(
        f"You hold this permission in more than one institution. Send the {INSTITUTION_HEADER} header to choose one.")


def _remember(request: Request, institution_id: UUID) -> None:
    """One request acts in one institution, even when several checks run."""
    known = getattr(request.state, "exam_institution", None)
    if known is not None and known != institution_id:
        raise InstitutionRequiredError(f"Send the {INSTITUTION_HEADER} header to choose one institution.")
    request.state.exam_institution = institution_id


def current_institution(request: Request) -> UUID:
    """The institution resolved by this request's permission check (403 if none ran)."""
    institution_id = getattr(request.state, "exam_institution", None)
    if institution_id is None:
        raise PermissionDeniedError()
    return institution_id


async def get_dataset_owner(request: Request) -> UUID | None:
    """The institution that owns the imported dataset; a short read-only session, closed at once."""
    async with session_scope(request.app.state.session_factory, commit=False) as session:
        return await dataset_owner(session)


DATASET_NOT_OWNED = "This institution has no examination dataset in this service."


def require_any_permission(*permissions: str, dataset: bool = False):
    """Caller must hold at least one of ``permissions`` in the resolved institution.

    ``dataset``: the institution must also own the imported examination dataset.
    """

    async def dependency(
        request: Request, principal: CurrentUser,
        requested: Annotated[UUID | None, Depends(requested_institution)],
    ) -> ExamAccess:
        access = ExamAccess(principal, resolve_institution(principal, permissions, requested))
        _remember(request, access.institution_id)
        return access

    if not dataset:
        return dependency

    # Defaults, not Annotated: ``dependency`` is local, so a string annotation could not be resolved.
    async def dataset_dependency(
        access: ExamAccess = Depends(dependency),
        owner: UUID | None = Depends(get_dataset_owner),
    ) -> ExamAccess:
        if owner is None or owner != access.institution_id:
            raise PermissionDeniedError(DATASET_NOT_OWNED)
        return access

    return dataset_dependency


# Saved timetables and scheduling settings (both stored per institution): reading.
require_exam_data_access = require_any_permission(READ)

# Scheduling settings changes (including dry runs) do not also require read permission.
require_exam_settings_admin = require_any_permission(MANAGE)

# Dataset GET routes (modules, students, registrations, venues, exam periods, seating rules).
require_dataset_read = require_any_permission(READ, dataset=True)

# Dataset changes: seating rules, exam periods and module exam details.
require_dataset_admin = require_any_permission(MANAGE, dataset=True)

# Timetable generation (runs the solver on the dataset) and saving the result as a draft.
require_exam_generation = require_any_permission(GENERATE, dataset=True)

# Seat allocations of a saved timetable list student IDs: only who builds or approves timetables.
require_exam_seat_access = require_any_permission(GENERATE, APPROVE)

# Approving and publishing saved timetables (both check H1-H11 again on the current dataset).
require_exam_approval = require_any_permission(APPROVE, dataset=True)
