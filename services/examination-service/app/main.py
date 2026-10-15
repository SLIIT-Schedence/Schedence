"""FastAPI application of the Examination Service.

Run from ``services/examination-service`` (Windows needs the selector loop
for psycopg async; see the service README)::

    uvicorn app.main:create_app --factory --port 8003 --loop asyncio:SelectorEventLoop

Security: ``/health`` and ``/health/db`` are public; every ``/api/v1`` route
requires a Cognito access token whose holder has ``examinations:generate`` or
``examinations:approve`` in this service's institution (see ``app.auth``).
Changing settings (``PUT``/``DELETE /api/v1/settings/...``) needs ``examinations:approve``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import exam_periods, health, modules, settings, students, venues
from app.api.errors import register_error_handlers
from app.auth.context import AuthContext, build_auth_context
from app.auth.dependencies import require_exam_data_access
from app.auth.settings import load_auth_settings
from app.database import close_database, open_database

API_PREFIX = "/api/v1"


def create_app(auth: AuthContext | None = None) -> FastAPI:
    """Build the app. ``auth`` is injected by tests; otherwise it is loaded from the environment."""
    if auth is None:
        auth = build_auth_context(load_auth_settings())
    production = auth.settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await open_database(app)
        try:
            yield
        finally:
            await close_database(app)
            await auth.close()

    app = FastAPI(
        title="Schedence Examination Service",
        version="0.1.0",
        description="Examination input data for examination administrators. Timetable generation is not available yet.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.auth = auth
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[auth.settings.frontend_url],
        allow_credentials=False,
        allow_methods=["GET", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    register_error_handlers(app)
    app.include_router(health.router)  # public

    # One check for every data route, so no route can be added unprotected.
    api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_exam_data_access)])
    for module in (modules, students, venues, exam_periods, settings):
        api.include_router(module.router)
    app.include_router(api)
    return app
