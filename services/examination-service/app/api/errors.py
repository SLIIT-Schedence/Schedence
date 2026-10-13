"""Safe error responses: database problems never leak SQL, hosts or credentials."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from schedence_db import describe_db_error

logger = logging.getLogger(__name__)

DATABASE_UNAVAILABLE = "The examination database is unavailable. Please try again later."


async def database_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # describe_db_error gives a fixed, credential-free reason for the server log only.
    logger.error("Database error on %s %s: %s", request.method, request.url.path, describe_db_error(exc))
    return JSONResponse({"detail": DATABASE_UNAVAILABLE}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(SQLAlchemyError, database_error_handler)
    app.add_exception_handler(OSError, database_error_handler)
