"""Reusable, credential-safe database connectivity check.

``db-core`` exposes no HTTP endpoint; each service wires :func:`check_database`
into its own ``/health/db`` route.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from schedence_db.exceptions import describe_db_error

logger = logging.getLogger(__name__)

HEALTH_QUERY = "SELECT 1"


@dataclass(frozen=True, slots=True)
class DatabaseHealth:
    """Result of :func:`check_database`. ``error`` is a safe, generic description."""

    healthy: bool
    latency_ms: float | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        return "healthy" if self.healthy else "unhealthy"

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form for a health endpoint response."""
        return {"status": self.status, "latency_ms": self.latency_ms, "error": self.error}


async def check_database(
    target: AsyncSession | AsyncConnection | AsyncEngine,
    *,
    timeout: float = 10.0,
) -> DatabaseHealth:
    """Run ``SELECT 1`` and report whether the database answered correctly.

    Args:
        target: A session (e.g. from a request dependency), a connection, or an
            engine (a connection is checked out and returned to the pool).
        timeout: Seconds to wait before reporting the database as unhealthy.
            The first check may need a new TLS connection (several network
            round trips) and a suspended Neon compute to wake up.

    Database, network and timeout errors are reported in the result, never
    raised. The query reads nothing and writes nothing.
    """
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            value = await _select_one(target)
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        error = describe_db_error(exc)
        logger.warning("Database health check failed: %s", error)
        return DatabaseHealth(healthy=False, error=error)

    if value != 1:
        return DatabaseHealth(healthy=False, error=f"unexpected result from {HEALTH_QUERY}")
    return DatabaseHealth(healthy=True, latency_ms=round((time.perf_counter() - started) * 1000, 1))


async def _select_one(target: AsyncSession | AsyncConnection | AsyncEngine) -> Any:
    query = text(HEALTH_QUERY)
    if isinstance(target, AsyncEngine):
        async with target.connect() as connection:
            return (await connection.execute(query)).scalar_one()
    return (await target.execute(query)).scalar_one()
