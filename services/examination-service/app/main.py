"""FastAPI application of the Examination Service.

Run from ``services/examination-service`` (Windows needs the selector loop
for psycopg async; see the service README)::

    uvicorn app.main:app --port 8003 --loop asyncio:SelectorEventLoop
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import exam_periods, health, modules, students, venues
from app.api.errors import register_error_handlers
from app.database import close_database, open_database

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await open_database(app)
    try:
        yield
    finally:
        await close_database(app)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Schedence Examination Service",
        version="0.1.0",
        description="Read-only examination input data. Timetable generation is not available yet.",
        lifespan=lifespan,
    )
    register_error_handlers(app)
    app.include_router(health.router)

    api = APIRouter(prefix=API_PREFIX)
    for module in (modules, students, venues, exam_periods):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
