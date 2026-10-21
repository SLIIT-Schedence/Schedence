"""Database wiring for the API, built on the shared ``schedence_db`` package.

* One engine per API process, created at startup and disposed of at shutdown
  (no module-level engine, so tests never open a connection by import).
* Pooled ``DATABASE_URL``, as recommended for services.
* Every request gets its own session whose transaction is ``READ ONLY``: the
  API cannot change the database even by mistake. psycopg opens it with
  ``BEGIN READ ONLY``, so this costs no extra round trip to Neon.
* The only exceptions: the seating-rule changes (``PUT``/``DELETE
  /api/v1/settings/exam-seating...``) and the scheduling-settings change
  (``PUT /api/v1/settings/scheduling``), the exam period changes
  (``POST``/``PUT /api/v1/exam-periods...``) and the module exam-details
  change (``PUT /api/v1/modules/{code}/exam-details``) get a writable session from
  :func:`get_write_session`, which never commits by itself. A dry run
  (``dry_run=true``) gets the read-only session instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from schedence_db import create_db_engine, create_session_factory, dispose_engine, load_settings, session_scope

APPLICATION_NAME = "examination-service-api"


async def open_database(app: FastAPI) -> None:
    settings = load_settings(db_application_name=APPLICATION_NAME)
    demo_service = getattr(app.state, "m3_demo_service", None)
    if demo_service is not None:
        from app.duty_allocation.neon_shared import verify_shared_database
        verify_shared_database(demo_service.settings, settings)
    app.state.engine = create_db_engine(settings)
    # Same pool; connections are returned to the pool with read-only switched off again.
    read_only = app.state.engine.execution_options(postgresql_readonly=True)
    app.state.session_factory = create_session_factory(read_only)
    app.state.write_session_factory = create_session_factory(app.state.engine)


async def close_database(app: FastAPI) -> None:
    await dispose_engine(getattr(app.state, "engine", None))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one read-only session per request, always closed."""
    async with session_scope(request.app.state.session_factory, commit=False) as session:
        yield session


async def get_write_session(request: Request, dry_run: bool = False) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for settings changes: writable unless ``dry_run``.

    Nothing is committed here; the caller commits explicitly. Uncommitted work
    is rolled back when the session closes, and on any error.
    """
    factory = request.app.state.session_factory if dry_run else request.app.state.write_session_factory
    async with session_scope(factory, commit=False) as session:
        yield session
