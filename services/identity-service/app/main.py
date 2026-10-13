"""Identity Service application factory.

Run (from services/identity-service, Windows):
    uvicorn app.main:create_app --factory --port 8001 --loop asyncio:SelectorEventLoop
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api import health
from app.api.v1.router import router as v1_router
from app.core.config import SERVICE_NAME, load_identity_settings
from app.core.container import IdentityContainer, build_container
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
OAUTH_SCOPES = {"openid": "OpenID Connect", "email": "E-mail address", "profile": "Profile"}


def create_app(container: IdentityContainer | None = None) -> FastAPI:
    if container is None:
        configure_logging()
        container = build_container(load_identity_settings())
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for hook in container.shutdown_hooks:
            await hook()

    production = settings.app_env == "production"
    app = FastAPI(
        title="Schedence Identity Service",
        version="0.1.0",
        description="Authentication (AWS Cognito), user accounts, system roles and permissions for Schedence.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
        swagger_ui_init_oauth=(
            {"clientId": settings.cognito_devtools_client_id, "usePkceWithAuthorizationCodeGrant": True,
             "scopes": " ".join(OAUTH_SCOPES)}
            if settings.swagger_oauth_enabled else None
        ),
    )
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

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
    app.include_router(health.router)
    app.include_router(v1_router)
    if settings.swagger_oauth_enabled:
        _add_cognito_oauth_to_openapi(app, f"{settings.cognito_domain}/oauth2/authorize", f"{settings.cognito_domain}/oauth2/token")
    logger.info("%s configured (env=%s)", SERVICE_NAME, settings.app_env)
    return app


def _add_cognito_oauth_to_openapi(app: FastAPI, authorize_url: str, token_url: str) -> None:
    """Let Swagger UI's Authorize button run Authorization Code + PKCE against Cognito (dev tools client)."""

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes["CognitoOAuth"] = {
            "type": "oauth2",
            "description": "Sign in through Cognito managed login (Schedence-DevTools app client, PKCE).",
            "flows": {"authorizationCode": {"authorizationUrl": authorize_url, "tokenUrl": token_url, "scopes": OAUTH_SCOPES}},
        }
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and operation.get("security"):
                    operation["security"].append({"CognitoOAuth": list(OAUTH_SCOPES)})
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
