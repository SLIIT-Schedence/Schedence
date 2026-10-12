"""Async session factories and per-unit-of-work session lifecycles.

A session is never shared: every request, job or script step gets a new
session from the factory and the session is closed when that unit of work ends.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from schedence_db.exceptions import describe_db_error

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


def create_session_factory(engine: AsyncEngine, **options: Any) -> SessionFactory:
    """Create an :class:`~sqlalchemy.ext.asyncio.async_sessionmaker` bound to ``engine``.

    ``expire_on_commit`` defaults to ``False``: with async sessions, expired
    attributes cannot be lazily reloaded, so objects must stay readable after
    ``commit()``. Any ``async_sessionmaker`` option can be overridden.
    """
    options.setdefault("expire_on_commit", False)
    return async_sessionmaker(engine, class_=AsyncSession, **options)


@asynccontextmanager
async def session_scope(factory: SessionFactory, *, commit: bool = True) -> AsyncIterator[AsyncSession]:
    """Run one unit of work in a fresh session.

    * On success the transaction is committed (unless ``commit=False``).
    * On any exception, including cancellation, it is rolled back and the
      exception propagates.
    * The session is always closed, returning its connection to the pool.

    Example::

        async with session_scope(session_factory) as session:
            session.add(obj)
    """
    session = factory()
    try:
        yield session
        if commit:
            await session.commit()
    except BaseException:
        await _rollback_quietly(session)
        raise
    finally:
        await session.close()


def session_dependency(factory: SessionFactory) -> Callable[[], AsyncIterator[AsyncSession]]:
    """Build a FastAPI dependency that yields one new session per request.

    The dependency does **not** commit: route or service code calls
    ``await session.commit()`` explicitly, so a failed commit becomes an error
    response instead of happening after the response was sent. Uncommitted
    work is discarded when the session closes, and errors trigger a rollback.

    Example::

        get_session = session_dependency(session_factory)

        @app.get("/users")
        async def list_users(session: AsyncSession = Depends(get_session)): ...
    """

    async def get_session() -> AsyncIterator[AsyncSession]:
        async with session_scope(factory, commit=False) as session:
            yield session

    return get_session


async def _rollback_quietly(session: AsyncSession) -> None:
    # A failed rollback (e.g. on a dropped connection) must not hide the
    # original exception; the connection is discarded when the session closes.
    try:
        await session.rollback()
    except Exception as exc:
        logger.warning("Rollback failed: %s", describe_db_error(exc))
