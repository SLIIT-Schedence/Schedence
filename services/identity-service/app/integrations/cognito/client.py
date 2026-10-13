"""Cognito administration port: what the Identity Service needs from Cognito.

The service layer depends on :class:`CognitoAdminClient` (a protocol). The
boto3 adapter implements it for real; tests use an in-memory fake. Errors are
translated into :class:`CognitoError` subclasses with a fixed ``category`` so
nothing from a raw AWS error message (which can contain e-mail addresses) is
stored or returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Cognito user statuses (subset that matters to Schedence)
STATUS_CONFIRMED = "CONFIRMED"
STATUS_FORCE_CHANGE_PASSWORD = "FORCE_CHANGE_PASSWORD"


@dataclass(frozen=True, slots=True)
class CognitoUser:
    username: str
    sub: str
    email: str | None
    email_verified: bool
    name: str | None
    enabled: bool
    status: str


class CognitoError(Exception):
    category = "COGNITO_ERROR"
    retryable = False

    def __init__(self, aws_code: str | None = None) -> None:
        self.aws_code = aws_code
        super().__init__(f"{self.category} ({aws_code or 'n/a'})")


class CognitoUserExistsError(CognitoError):
    category = "COGNITO_USER_EXISTS"


class CognitoUserNotFoundError(CognitoError):
    category = "COGNITO_USER_NOT_FOUND"


class CognitoInvalidRequestError(CognitoError):
    category = "COGNITO_INVALID_REQUEST"


class CognitoDeliveryError(CognitoError):
    category = "COGNITO_INVITATION_DELIVERY_FAILED"
    retryable = True


class CognitoThrottledError(CognitoError):
    category = "COGNITO_THROTTLED"
    retryable = True


class CognitoUnavailableError(CognitoError):
    category = "COGNITO_UNAVAILABLE"
    retryable = True


class CognitoPermissionError(CognitoError):
    """The service's IAM identity is missing, expired, or lacks a permission (an operator problem)."""

    category = "COGNITO_ACCESS_DENIED"


class CognitoMisconfiguredError(CognitoError):
    """The configured user pool does not exist in the configured Region."""

    category = "COGNITO_MISCONFIGURED"


class CognitoAdminClient(Protocol):
    async def create_user(self, *, email: str, full_name: str) -> CognitoUser:
        """AdminCreateUser with a verified e-mail; Cognito generates and e-mails a temporary password."""
        ...

    async def get_user(self, username: str) -> CognitoUser | None:
        """AdminGetUser by username, sub, or e-mail (the pool's sign-in attribute). None if absent."""
        ...

    async def resend_invitation(self, *, email: str) -> None: ...
    async def disable_user(self, username: str) -> None: ...
    async def enable_user(self, username: str) -> None: ...
    async def global_sign_out(self, username: str) -> None: ...
    async def update_name(self, username: str, full_name: str) -> None: ...
    async def describe_user_pool(self) -> dict: ...
