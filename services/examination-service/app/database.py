"""Database wiring for the API, built on the shared ``schedence_db`` package.

* One engine per API process, created at startup and disposed of at shutdown
  (no module-level engine, so tests never open a connection by import).
* Pooled ``DATABASE_URL``, as recommended for services.
* Every request gets its own session whose transaction is ``READ ONLY``: the
  API cannot change the database even by mistake. psycopg opens it with
  ``BEGIN READ ONLY``, so this costs no extra round trip to Neon.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from schedence_db import create_db_engine, create_session_factory, dispose_engine, load_settings, session_scope

APPLICATION_NAME = "examination-service-api"


async def open_database(app: FastAPI) -> None:
    settings = load_settings(db_application_name=APPLICATION_NAME)
    app.state.engine = create_db_engine(settings)
    # Same pool; connections are returned to the pool with read-only switched off again.
    read_only = app.state.engine.execution_options(postgresql_readonly=True)
    app.state.session_factory = create_session_factory(read_only)


async def close_database(app: FastAPI) -> None:
    await dispose_engine(getattr(app.state, "engine", None))


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one read-only session per request, always closed."""
    async with session_scope(request.app.state.session_factory, commit=False) as session:
        yield session
