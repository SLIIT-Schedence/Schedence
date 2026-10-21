"""Resource Service API: the read-only venue export the Teaching service validates against.

Contract: teaching-service/docs/owner-api-integration.md (``GET /api/v1/resource/teaching-inputs``).
Callers present their own Cognito access token; permissions come from the Identity Service on every
request (``venues:read`` in the requested institution). Nothing is written.

Run (from services/resource-service, Windows):
    uvicorn schedence_resource.main:create_app --factory --app-dir src --port 8006 --loop asyncio:SelectorEventLoop
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field
from sqlalchemy import text

from schedence_auth import CognitoSettings, JwksUnavailableError, TokenVerificationError, build_verifier, load_settings_model

from .models import source_datasets, venues

logger = logging.getLogger(__name__)
SERVICE_NAME = "resource-service"
API_PREFIX = "/api/v1/resource"
PERMISSIONS_PATH = "/api/v1/identity/me/permissions"
PERM_VENUES_READ = "venues:read"
EXPORT_TABLES = (source_datasets, venues)
# One statement for both tables (each Neon round trip is costly). Identifiers come from the fixed metadata only.
EXPORT = text("SELECT json_build_object(" + ", ".join(
    f"'{t.name}', (SELECT coalesce(json_agg(r ORDER BY r.id), '[]'::json) FROM {t.schema}.{t.name} r"
    " WHERE r.institution_id = :tenant)" for t in EXPORT_TABLES) + ")")


class ResourceSettings(CognitoSettings):
    identity_service_url: str = "http://localhost:8001"
    identity_timeout_seconds: float = Field(default=5, gt=0, le=30)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    permissions_by_institution: dict[UUID, frozenset[str]]


class IdentityPermissions:
    """The Identity Service's /me/permissions contract; nothing is cached, so revoked roles take effect at once."""

    def __init__(self, base_url: str, http: httpx.AsyncClient, *, timeout: float = 5) -> None:
        self.base_url, self.http, self.timeout = base_url.rstrip("/"), http, timeout

    async def principal_for(self, token: str, claims) -> Principal:
        try:
            response = await self.http.get(self.base_url + PERMISSIONS_PATH, timeout=self.timeout, follow_redirects=False,
                                           headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        except httpx.HTTPError:
            raise ApiError(503, "IDENTITY_UNAVAILABLE", "Identity permissions cannot be verified.") from None
        if response.status_code in (401, 403):
            raise ApiError(response.status_code, "INVALID_TOKEN" if response.status_code == 401 else "ACCOUNT_FORBIDDEN",
                           "The Identity Service rejected the caller.")
        try:
            if response.status_code != 200:
                raise ValueError("unexpected status")
            data = response.json()
            if data["cognito_sub"] != claims.sub:
                raise ValueError("identity mismatch")
            grants = {UUID(row["institution_id"]): frozenset(row["permissions"]) for row in data["institutions"]}
            user_id = UUID(data["user_id"])
        except (ValueError, KeyError, TypeError):
            raise ApiError(503, "IDENTITY_UNAVAILABLE", "Identity returned inconsistent account information.") from None
        if data.get("account_status") != "ACTIVE":
            raise ApiError(403, "ACCOUNT_DISABLED", "The account is not active.")
        return Principal(user_id, grants)


@dataclass
class ResourceContainer:
    verifier: object
    permissions: IdentityPermissions
    export: Callable[[UUID], Awaitable[dict[str, list[dict]]]]
    shutdown_hooks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)


def database_export(session_factory) -> Callable[[UUID], Awaitable[dict[str, list[dict]]]]:
    async def export(institution_id: UUID) -> dict[str, list[dict]]:
        async with session_factory() as session:
            try:
                # Read-only, and both inventory tables use forced institution RLS keyed on this transaction-local setting.
                await session.execute(text("SELECT set_config('transaction_read_only', 'on', true),"
                                           " set_config('schedence.institution_id', :tenant, true)"), {"tenant": str(institution_id)})
                return await session.scalar(EXPORT, {"tenant": institution_id})
            finally:
                await session.rollback()

    return export


def build_container() -> ResourceContainer:
    from schedence_db import create_db_engine, create_session_factory, find_env_file, load_settings

    env_file = os.environ.get("SCHEDENCE_ENV_FILE") or find_env_file()
    settings = load_settings_model(ResourceSettings, env_file)
    db = load_settings(env_file=env_file)
    if db.db_application_name is None:
        db = db.model_copy(update={"db_application_name": SERVICE_NAME})
    engine = create_db_engine(db)
    http = httpx.AsyncClient(follow_redirects=False)
    return ResourceContainer(build_verifier(settings),
                             IdentityPermissions(settings.identity_service_url, http, timeout=settings.identity_timeout_seconds),
                             database_export(create_session_factory(engine)), [http.aclose, engine.dispose])


bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerToken", description="Cognito **access token**.")


async def current_user(request: Request,
                       credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]) -> Principal:
    container: ResourceContainer = request.app.state.container
    if credentials is None or not credentials.credentials:
        raise ApiError(401, "UNAUTHENTICATED", "A bearer access token is required.")
    try:
        claims = await container.verifier.verify_access_token(credentials.credentials)
    except TokenVerificationError as exc:
        raise ApiError(403 if exc.status_code == 403 else 401, "INVALID_TOKEN", str(exc)) from None
    except JwksUnavailableError:
        raise ApiError(503, "AUTH_UNAVAILABLE", "Token signing keys are temporarily unavailable.") from None
    return await container.permissions.principal_for(credentials.credentials, claims)


def create_app(container: ResourceContainer | None = None) -> FastAPI:
    if container is None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        container = build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for hook in container.shutdown_hooks:
            await hook()

    app = FastAPI(title="Schedence Resource Service", version="0.1.0", lifespan=lifespan,
                  description="Read-only venue inventory exports for scheduling services.")
    app.state.container = container

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status,
                            headers={"Cache-Control": "no-store"})

    @app.get("/health", tags=["health"], summary="Liveness")
    async def health() -> dict:
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get(API_PREFIX + "/teaching-inputs", tags=["teaching integration"],
             summary="Complete venue and source-dataset export for the Teaching service (read-only)")
    async def teaching_inputs(institution_id: UUID, principal: Annotated[Principal, Depends(current_user)]) -> JSONResponse:
        granted = principal.permissions_by_institution.get(institution_id)
        if granted is None:
            raise ApiError(404, "NOT_FOUND", "Institution not found.")  # existence outside the caller's institutions is not revealed
        if PERM_VENUES_READ not in granted:
            raise ApiError(403, "PERMISSION_DENIED", f"Requires {PERM_VENUES_READ}.")
        try:
            tables = await container.export(institution_id)
        except Exception as exc:
            logger.warning("Venue export failed (%s)", type(exc).__name__)
            raise ApiError(503, "DATABASE_UNAVAILABLE", "The database is temporarily unavailable.") from None
        from fastapi.encoders import jsonable_encoder
        return JSONResponse(jsonable_encoder({"institution_id": institution_id, "complete": True, "tables": tables}),
                            headers={"Cache-Control": "no-store"})

    logger.info("%s configured", SERVICE_NAME)
    return app
