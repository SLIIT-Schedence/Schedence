"""Application errors and the consistent JSON error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

Messages are written for API clients. They never include tokens, credentials,
raw driver/AWS error text, or submitted input values.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code, code, message = 401, "UNAUTHENTICATED", "Authentication is required."

    def __init__(self, message: str | None = None, *, code: str | None = None, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, code=code, details=details, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


class PermissionDeniedError(AppError):
    status_code, code, message = 403, "FORBIDDEN", "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code, code, message = 404, "NOT_FOUND", "The requested resource was not found."


class ConflictError(AppError):
    status_code, code, message = 409, "CONFLICT", "The request conflicts with the current state."


class ValidationFailedError(AppError):
    status_code, code, message = 422, "VALIDATION_ERROR", "The request is invalid."


class RateLimitedError(AppError):
    status_code, code, message = 429, "RATE_LIMITED", "Too many requests. Try again shortly."


class IdentityProviderError(AppError):
    status_code, code, message = 502, "IDENTITY_PROVIDER_ERROR", "The identity provider rejected the request."


class ServiceUnavailableError(AppError):
    status_code, code, message = 503, "SERVICE_UNAVAILABLE", "A required dependency is temporarily unavailable."


def _envelope(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return {"error": body}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.warning("%s %s -> %s %s", request.method, request.url.path, exc.status_code, exc.code)
        return JSONResponse(_envelope(request, exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop "input"/"ctx" so submitted values (e-mails, names) are never echoed back.
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
        return JSONResponse(_envelope(request, "VALIDATION_ERROR", "The request is invalid.", {"errors": errors}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHENTICATED", 403: "FORBIDDEN"}.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(_envelope(request, code, message), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc)
        return JSONResponse(_envelope(request, "INTERNAL_ERROR", "An unexpected error occurred."), status_code=500)
