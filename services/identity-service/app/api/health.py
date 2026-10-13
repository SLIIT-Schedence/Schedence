from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.dependencies import ContainerDep
from app.core.config import SERVICE_NAME

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME}


@router.get("/health/db", summary="Database connectivity (SELECT 1)")
async def database_health(container: ContainerDep, response: Response) -> dict:
    result = await container.db_health()
    if not result.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result.to_dict()
