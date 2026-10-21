"""GET-only routes using shared Cognito verification then trusted Identity grants."""

import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from schedence_auth import JwksUnavailableError, TokenVerificationError

from .authorization import authorize
from .contracts import Page, SessionOut, TimetableOut, TimetableQuery, ValidationOut
from .errors import ApiError
from .runtime import build_runtime
from .service import TimetableReadService

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False, scheme_name="BearerToken", description="Existing Cognito access token.")
router = APIRouter(prefix="/timetables", tags=["teaching timetables"])


async def authenticate(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]):
    if credentials is None or not credentials.credentials:
        raise ApiError(401, "UNAUTHENTICATED", "A bearer access token is required.")
    token = credentials.credentials
    if len(token) > 16_384 or token.count(".") != 2:
        raise ApiError(401, "INVALID_TOKEN", "The access token is invalid.")
    if request.app.state.runtime is None:
        async with request.app.state.runtime_lock:
            if request.app.state.runtime is None:
                try:
                    # Configuration loading and heavy imports run off the event loop, so a slow or failing
                    # build never stalls health checks or other requests.
                    request.app.state.runtime = await asyncio.to_thread(build_runtime)
                except Exception:
                    logger.exception("Teaching runtime configuration failed")
                    raise ApiError(503, "AUTH_UNAVAILABLE", "Teaching API authentication is not configured.") from None
    runtime = request.app.state.runtime
    try:
        claims = await runtime.verifier.verify_access_token(token)
    except TokenVerificationError as error:
        if error.status_code == 403:
            raise ApiError(403, "INSUFFICIENT_SCOPE", "The access token lacks the required scope.") from None
        raise ApiError(401, "INVALID_TOKEN", "The access token is invalid or expired.") from None
    except JwksUnavailableError:
        raise ApiError(503, "AUTH_UNAVAILABLE", "Signing keys are temporarily unavailable.") from None
    principal = await runtime.permissions.principal_for(token, claims)
    request.state.teaching_bearer_token = token
    return runtime, principal


async def request_access(auth: Annotated[tuple, Depends(authenticate)], query: Annotated[TimetableQuery, Query()]):
    runtime, principal = auth
    access = await authorize(principal, query.institution_id, runtime.bindings)
    return TimetableReadService(runtime.store), access, query


Authorized = Annotated[tuple, Depends(request_access)]


@router.get("", response_model=Page[TimetableOut], response_model_exclude_none=True)
async def timetables(context: Authorized):
    service, access, query = context
    return await service.page(access, query)


@router.get("/{version_id}", response_model=TimetableOut, response_model_exclude_none=True)
async def timetable(version_id: UUID, context: Authorized):
    service, access, query = context
    return await service.detail(access, version_id, query)


@router.get("/{version_id}/sessions", response_model=Page[SessionOut])
async def sessions(version_id: UUID, context: Authorized):
    service, access, query = context
    return await service.sessions(access, version_id, query)


@router.get("/{version_id}/validation", response_model=ValidationOut, response_model_exclude_none=True)
async def validation(version_id: UUID, context: Authorized):
    service, access, query = context
    return await service.validation(access, version_id, query)


@router.get("/{version_id}/versions", response_model=Page[TimetableOut], response_model_exclude_none=True)
async def versions(version_id: UUID, context: Authorized):
    service, access, query = context
    return await service.versions(access, version_id, query)
