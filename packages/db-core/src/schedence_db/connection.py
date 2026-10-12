"""Async SQLAlchemy engine creation and disposal.

Each service process creates its *own* engine from its *own* settings, usually
once at startup, and disposes of it on shutdown. Nothing here is a module-level
global, so two services (or two tests) never share an engine or a pool.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from schedence_db.config import DatabaseSettings, load_settings
from schedence_db.exceptions import DatabaseConfigurationError, describe_db_error, redact

logger = logging.getLogger(__name__)


def create_db_engine(
    settings: DatabaseSettings | None = None,
    *,
    direct: bool = False,
    **engine_options: Any,
) -> AsyncEngine:
    """Create an :class:`~sqlalchemy.ext.asyncio.AsyncEngine` for one service process.

    Creating the engine does not open a connection; the first query does.

    Args:
        settings: Database settings. Loaded with :func:`load_settings` when omitted.
        direct: Use ``DIRECT_DATABASE_URL`` (falling back to ``DATABASE_URL``)
            instead of the pooled URL. Direct engines default to ``NullPool``
            because migrations and admin tasks are short-lived and direct
            connections count against the compute's ``max_connections``.
        **engine_options: Extra :func:`~sqlalchemy.ext.asyncio.create_async_engine`
            keyword arguments; they override the values derived from ``settings``.
            ``connect_args`` is merged rather than replaced.

    Pooling policy:
        * Pooled engines keep at most ``DB_POOL_SIZE + DB_MAX_OVERFLOW``
          connections per process, use LIFO checkout so surplus connections go
          idle and get recycled, and pre-ping connections because Neon closes
          them when the compute scales to zero.
        * ``NullPool`` (no client-side pool) is used for direct engines, when
          ``APP_ENV=testing`` or when ``DB_USE_NULL_POOL=true``.
    """
    if settings is None:
        settings = load_settings()
    url = settings.get_url(direct=direct)

    connect_args: dict[str, Any] = {"connect_timeout": settings.db_connect_timeout}
    if settings.db_application_name:
        connect_args["application_name"] = settings.db_application_name
    if settings.db_disable_prepared_statements:
        connect_args["prepare_threshold"] = None
    connect_args.update(engine_options.pop("connect_args", {}))

    options: dict[str, Any] = {"echo": settings.db_echo, "connect_args": connect_args}
    poolclass = engine_options.pop("poolclass", NullPool if direct or settings.use_null_pool else None)
    if poolclass is NullPool:
        options["poolclass"] = NullPool
    else:
        if poolclass is not None:
            options["poolclass"] = poolclass
        options.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_recycle=settings.db_pool_recycle,
            pool_pre_ping=settings.db_pool_pre_ping,
            pool_use_lifo=True,
        )
    options.update(engine_options)

    try:
        return create_async_engine(url, **options)
    except (ArgumentError, TypeError) as exc:
        detail = redact(str(exc), url.password, url.render_as_string(hide_password=False))
        raise DatabaseConfigurationError(f"Could not create the database engine: {detail}") from None


async def dispose_engine(engine: AsyncEngine | None) -> None:
    """Close every pooled connection of ``engine``. Safe to call during shutdown.

    Accepts ``None`` so shutdown code works even if startup failed before the
    engine was created. Errors are logged (without credentials), not raised.
    """
    if engine is None:
        return
    try:
        await engine.dispose()
    except Exception as exc:  # shutdown must not crash the application
        logger.warning("Error while disposing of the database engine: %s", describe_db_error(exc))
