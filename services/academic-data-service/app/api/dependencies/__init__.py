"""Authentication, authorisation and service wiring for the Academic Data Service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.container import AcademicContainer
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError, ServiceUnavailableError
from app.core.principal import Principal
from app.repositories.base import AcademicUnitOfWork
from app.services.appointments import AppointmentService
from app.services.positions import PositionService
from app.services.staff import StaffService
from app.services.teaching_inputs import TeachingInputService
from schedence_auth import JwksUnavailableError, TokenVerificationError

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerToken", description="Cognito **access token**.")
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_container(request: Request) -> AcademicContainer:
    return request.app.state.container


ContainerDep = Annotated[AcademicContainer, Depends(get_container)]


async def get_uow(container: ContainerDep) -> AsyncIterator[AcademicUnitOfWork]:
    async with container.uow_factory() as uow:
        yield uow


UnitOfWorkDep = Annotated[AcademicUnitOfWork, Depends(get_uow)]


async def get_current_user(
    request: Request,
    container: ContainerDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> Principal:
    """Verify the Cognito access token locally, then resolve permissions via the Identity Service."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("A bearer access token is required.")
    try:
        claims = await container.verifier.verify_access_token(credentials.credentials)
    except TokenVerificationError as exc:
        logger.info("Rejected access token: %s", exc.reason)
        if exc.status_code == 403:
            raise PermissionDeniedError(str(exc), code="INSUFFICIENT_SCOPE") from None
        raise AuthenticationError(str(exc), code="INVALID_TOKEN") from None
    except JwksUnavailableError:
        raise ServiceUnavailableError("Token signing keys are temporarily unavailable.", code="AUTH_UNAVAILABLE") from None
    return await container.permissions.principal_for(credentials.credentials, claims, fresh=request.method not in _SAFE_METHODS)


CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_permissions(*permissions: str):
    async def dependency(principal: CurrentUser) -> Principal:
        if not all(principal.has_permission(p) for p in permissions):
            raise PermissionDeniedError(details={"required_permissions": list(permissions)})
        return principal

    return dependency


async def enforce_write_rate_limit(principal: CurrentUser, container: ContainerDep) -> None:
    if not container.rate_limiter.hit(("write", principal.user_id)):
        raise RateLimitedError()


def _kwargs(container: AcademicContainer, request: Request) -> dict:
    return {"clock": container.clock, "today": container.institution_today, "request_id": getattr(request.state, "request_id", None)}


def get_position_service(uow: UnitOfWorkDep, container: ContainerDep, request: Request) -> PositionService:
    return PositionService(uow, **_kwargs(container, request))


def get_staff_service(uow: UnitOfWorkDep, container: ContainerDep, request: Request) -> StaffService:
    return StaffService(uow, **_kwargs(container, request))


def get_appointment_service(uow: UnitOfWorkDep, container: ContainerDep, request: Request) -> AppointmentService:
    return AppointmentService(uow, **_kwargs(container, request))


def get_teaching_input_service(uow: UnitOfWorkDep, container: ContainerDep, request: Request) -> TeachingInputService:
    return TeachingInputService(uow, **_kwargs(container, request))


TeachingInputServiceDep = Annotated[TeachingInputService, Depends(get_teaching_input_service)]
PositionServiceDep = Annotated[PositionService, Depends(get_position_service)]
StaffServiceDep = Annotated[StaffService, Depends(get_staff_service)]
AppointmentServiceDep = Annotated[AppointmentService, Depends(get_appointment_service)]


@dataclass(frozen=True)
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_pagination(page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=100)] = 20) -> Pagination:
    return Pagination(page, page_size)


PaginationDep = Annotated[Pagination, Depends(get_pagination)]
