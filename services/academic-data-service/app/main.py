"""Academic Data Service application factory (administrative positions and appointments).

Run (from services/academic-data-service, Windows):
    uvicorn app.main:create_app --factory --port 8002 --loop asyncio:SelectorEventLoop
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.dependencies import ContainerDep
from app.api.v1 import appointments, positions, staff
from app.core.config import SERVICE_NAME, load_academic_settings
from app.core.container import AcademicContainer, build_container
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)
API_PREFIX = "/api/v1/academic"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
OAUTH_SCOPES = {"openid": "OpenID Connect", "email": "E-mail address", "profile": "Profile"}

health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME}


@health_router.get("/health/db", summary="Database connectivity (SELECT 1)")
async def database_health(container: ContainerDep, response: Response) -> dict:
    result = await container.db_health()
    if not result.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result.to_dict()


def create_app(container: AcademicContainer | None = None) -> FastAPI:
    if container is None:
        configure_logging()
        container = build_container(load_academic_settings())
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for hook in container.shutdown_hooks:
            await hook()

    production = settings.app_env == "production"
    app = FastAPI(
        title="Schedence Academic Data Service",
        version="0.1.0",
        description="Administrative position types, workload policies, staff appointments and the administrative-workload contract.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        swagger_ui_init_oauth=(
            {"clientId": settings.cognito_devtools_client_id, "usePkceWithAuthorizationCodeGrant": True, "scopes": " ".join(OAUTH_SCOPES)}
            if settings.swagger_oauth_enabled else None
        ),
    )
    app.state.container = container
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                       expose_headers=["X-Request-ID"], max_age=600)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    install_exception_handlers(app)
    app.include_router(health_router)
    api = APIRouter(prefix=API_PREFIX)
    for module in (positions, staff, appointments):
        api.include_router(module.router)
    app.include_router(api)
    if settings.swagger_oauth_enabled:
        _add_cognito_oauth(app, f"{settings.cognito_domain}/oauth2/authorize", f"{settings.cognito_domain}/oauth2/token")
    logger.info("%s configured (env=%s)", SERVICE_NAME, settings.app_env)
    return app


def _add_cognito_oauth(app: FastAPI, authorize_url: str, token_url: str) -> None:
    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["CognitoOAuth"] = {
            "type": "oauth2",
            "flows": {"authorizationCode": {"authorizationUrl": authorize_url, "tokenUrl": token_url, "scopes": OAUTH_SCOPES}},
        }
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and operation.get("security"):
                    operation["security"].append({"CognitoOAuth": list(OAUTH_SCOPES)})
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
