"""Safe service error envelopes, consistent with the existing monorepo APIs."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def install_errors(app):
    def response(request, status, code, message, details=None):
        error = dict(code=code, message=message, request_id=getattr(request.state, "request_id", None))
        if details is not None:
            error["details"] = details
        return JSONResponse(dict(error=error), status_code=status,
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)

    @app.exception_handler(ApiError)
    async def application_error(request: Request, error: ApiError):
        return response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Never echo submitted tokens or field values.
        return response(request, 422, "VALIDATION_ERROR", "The request is invalid.",
            dict(errors=[dict(loc=list(item["loc"]), type=item["type"]) for item in error.errors()]))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return response(request, error.status_code,
            {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "HTTP_ERROR"),
            "The requested resource was not found." if error.status_code == 404 else "Request rejected.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception):
        # Driver exceptions can contain connection details: do not log or return them.
        return response(request, 503, "SERVICE_UNAVAILABLE", "A required service is unavailable.")
