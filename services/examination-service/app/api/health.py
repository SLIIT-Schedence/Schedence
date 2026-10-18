"""Liveness and database health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from schedence_db import check_database

from app.api.schemas import DatabaseHealthOut, HealthOut

router = APIRouter(tags=["health"])

# A new connection to Neon can take over 10 s (TLS from far away, or a compute
# waking up from scale-to-zero), so allow more than db-core's 10 s default.
DB_HEALTH_TIMEOUT_SECONDS = 30.0


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """The API process is running. Does not touch the database."""
    return HealthOut(status="ok", service="examination-service")


@router.get("/health/db", response_model=DatabaseHealthOut)
async def database_health(request: Request, response: Response) -> DatabaseHealthOut:
    """Runs ``SELECT 1``. Answers 503 with a safe reason when the database is unreachable."""
    result = await check_database(request.app.state.engine, timeout=DB_HEALTH_TIMEOUT_SECONDS)
    if not result.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return DatabaseHealthOut(**result.to_dict())
