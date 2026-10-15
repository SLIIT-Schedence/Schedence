"""Auth errors as HTTP responses. Messages are fixed: no token, claim or configuration value."""

from __future__ import annotations

from fastapi import HTTPException, status


class AuthenticationError(HTTPException):
    def __init__(self, detail: str = "Authentication is required.") -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


class PermissionDeniedError(HTTPException):
    def __init__(self, detail: str = "You do not have permission to perform this action.") -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, detail)


class ServiceUnavailableError(HTTPException):
    def __init__(self, detail: str = "Permissions cannot be verified right now. Please try again later.") -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, detail)
