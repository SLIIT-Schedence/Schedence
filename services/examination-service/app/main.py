"""FastAPI application of the Examination Service.

Run from ``services/examination-service`` (Windows needs the selector loop
for psycopg async; see the service README)::

    uvicorn app.main:create_app --factory --port 8003 --loop asyncio:SelectorEventLoop

Security: ``/health`` and ``/health/db`` are public; every ``/api/v1`` route
requires a Cognito access token. GET routes require ``examinations:read``
(saved timetable seats: ``examinations:generate`` or ``examinations:approve``);
seating and scheduling-settings PUT/DELETE routes, exam period POST/PUT
routes and the module exam-details PUT require ``examinations:manage``, and
``POST /api/v1/timetables/generate`` and ``/drafts`` require
``examinations:generate``, in the request's institution. That institution
comes from the caller's permissions (or the ``X-Institution-Id`` header, which
is checked against them); dataset routes also require it to own the imported
dataset (see ``app.auth.dependencies``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import duty_allocation, exam_periods, health, modules, scheduling_settings, settings, students, timetables, venues
from app.api.errors import register_error_handlers
from app.auth.context import AuthContext, build_auth_context
from app.auth.dependencies import INSTITUTION_HEADER, get_current_user, require_dataset_read
from app.auth.settings import load_auth_settings
from app.database import close_database, open_database
from app.duty_allocation.demo_service import DemoServiceError, Member3DemoService
from app.duty_allocation.demo_settings import DemoSource, Member3DemoSettings

API_PREFIX = "/api/v1"


def create_app(auth: AuthContext | None = None, m3_demo: Member3DemoSettings | None = None) -> FastAPI:
    """Build the app. ``auth`` is injected by tests; otherwise it is loaded from the environment.

    ``m3_demo`` (default: ``M3_DEMO_*`` environment variables) controls the Member 3 duty-allocation
    preview API, which is only mounted when its data source is not DISABLED."""
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
        description="Examination input data, settings, timetable generation and saved draft timetables for examination administrators.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.auth = auth
    app.state.generation_lock = asyncio.Lock()  # one timetable generation at a time
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[auth.settings.frontend_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", INSTITUTION_HEADER],
    )
    register_error_handlers(app)
    app.add_exception_handler(DemoServiceError, duty_allocation.demo_service_error_handler)
    app.include_router(health.router)  # public

    # Authenticate every data request; each route also has a permission check.
    api = APIRouter(prefix=API_PREFIX, dependencies=[Depends(get_current_user)])
    for module in (students, venues):
        api.include_router(module.router, dependencies=[Depends(require_dataset_read)])
    # These mix read and write operations, so they check each operation.
    api.include_router(modules.router)
    api.include_router(exam_periods.router)
    api.include_router(settings.router)
    api.include_router(scheduling_settings.router)
    api.include_router(timetables.router)  # checks examinations:generate / :read per route
    # Member 3 duty-allocation preview (synthetic m3_demo data): mounted only when M3_DEMO_SOURCE is set;
    # each route checks examinations:read (GET) or examinations:generate (POST).
    demo = m3_demo if m3_demo is not None else Member3DemoSettings()
    if demo.source is not DemoSource.DISABLED:
        app.state.m3_demo_service = Member3DemoService(
            demo, app_env=auth.settings.app_env, real_institution_id=auth.settings.institution_id)
        api.include_router(duty_allocation.router)
    app.include_router(api)
    return app
