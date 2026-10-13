"""boto3 implementation of :class:`CognitoAdminClient`.

Credentials come from boto3's normal chain. Locally that is the AWS CLI
profile named by ``AWS_PROFILE`` (ideally IAM Identity Center short-lived
credentials); in AWS it is the task/instance role. Credentials are never read
from or written to Schedence configuration.

boto3 is synchronous, so each call runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.integrations.cognito.client import (
    CognitoDeliveryError,
    CognitoError,
    CognitoInvalidRequestError,
    CognitoMisconfiguredError,
    CognitoPermissionError,
    CognitoThrottledError,
    CognitoUnavailableError,
    CognitoUser,
    CognitoUserExistsError,
    CognitoUserNotFoundError,
)

logger = logging.getLogger(__name__)

_ERROR_MAP: dict[str, type[CognitoError]] = {
    "UsernameExistsException": CognitoUserExistsError,
    "AliasExistsException": CognitoUserExistsError,
    "UserNotFoundException": CognitoUserNotFoundError,
    "InvalidParameterException": CognitoInvalidRequestError,
    "InvalidPasswordException": CognitoInvalidRequestError,
    "UnsupportedUserStateException": CognitoInvalidRequestError,
    "PreconditionNotMetException": CognitoInvalidRequestError,
    "CodeDeliveryFailureException": CognitoDeliveryError,
    "InvalidEmailRoleAccessPolicyException": CognitoDeliveryError,
    "TooManyRequestsException": CognitoThrottledError,
    "LimitExceededException": CognitoThrottledError,
    "ThrottlingException": CognitoThrottledError,
    "InternalErrorException": CognitoUnavailableError,
    "NotAuthorizedException": CognitoPermissionError,
    "AccessDeniedException": CognitoPermissionError,
    "UnrecognizedClientException": CognitoPermissionError,
    "ExpiredTokenException": CognitoPermissionError,
    "ResourceNotFoundException": CognitoMisconfiguredError,  # wrong pool ID or Region
}


def translate_error(exc: Exception) -> CognitoError:
    if isinstance(exc, CognitoError):
        return exc
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        return _ERROR_MAP.get(code, CognitoError)(code)
    if isinstance(exc, NoCredentialsError):
        return CognitoPermissionError("NoCredentials")
    if isinstance(exc, BotoCoreError):
        # Includes endpoint/connection errors, timeouts and expired SSO sessions.
        name = type(exc).__name__
        if "Token" in name or "Credential" in name or "SSO" in name:
            return CognitoPermissionError(name)
        return CognitoUnavailableError(name)
    return CognitoUnavailableError(type(exc).__name__)


def _to_user(username: str, attributes: list[dict[str, str]], enabled: bool, status: str) -> CognitoUser:
    attrs = {a["Name"]: a.get("Value") for a in attributes}
    return CognitoUser(
        username=username,
        sub=attrs.get("sub") or username,
        email=(attrs.get("email") or None),
        email_verified=str(attrs.get("email_verified", "false")).lower() == "true",
        name=attrs.get("name"),
        enabled=enabled,
        status=status,
    )


class Boto3CognitoAdminClient:
    def __init__(self, *, region: str, user_pool_id: str, profile: str | None = None, timeout_seconds: float = 10,
                 client: Any | None = None) -> None:
        self.region = region
        self.user_pool_id = user_pool_id
        self._profile = profile
        self._timeout = timeout_seconds
        self._client = client
        self._lock = threading.Lock()

    def _boto_client(self) -> Any:
        with self._lock:
            if self._client is None:
                import boto3

                session = boto3.Session(profile_name=self._profile, region_name=self.region)
                self._client = session.client(
                    "cognito-idp",
                    config=Config(
                        region_name=self.region,
                        connect_timeout=self._timeout,
                        read_timeout=self._timeout,
                        retries={"mode": "standard", "max_attempts": 3},
                    ),
                )
            return self._client

    async def _call(self, operation: str, **params: Any) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            return getattr(self._boto_client(), operation)(UserPoolId=self.user_pool_id, **params)

        try:
            return await asyncio.to_thread(invoke)
        except Exception as exc:
            error = translate_error(exc)
            logger.warning("Cognito %s failed: %s", operation, error.category)
            raise error from None

    async def create_user(self, *, email: str, full_name: str) -> CognitoUser:
        response = await self._call(
            "admin_create_user",
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "name", "Value": full_name},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
        user = response["User"]
        return _to_user(user["Username"], user.get("Attributes", []), user.get("Enabled", True), user.get("UserStatus", ""))

    async def get_user(self, username: str) -> CognitoUser | None:
        try:
            response = await self._call("admin_get_user", Username=username)
        except CognitoUserNotFoundError:
            return None
        return _to_user(response["Username"], response.get("UserAttributes", []), response.get("Enabled", True),
                        response.get("UserStatus", ""))

    async def resend_invitation(self, *, email: str) -> None:
        await self._call("admin_create_user", Username=email, MessageAction="RESEND", DesiredDeliveryMediums=["EMAIL"])

    async def disable_user(self, username: str) -> None:
        await self._call("admin_disable_user", Username=username)

    async def enable_user(self, username: str) -> None:
        await self._call("admin_enable_user", Username=username)

    async def global_sign_out(self, username: str) -> None:
        await self._call("admin_user_global_sign_out", Username=username)

    async def update_name(self, username: str, full_name: str) -> None:
        await self._call("admin_update_user_attributes", Username=username, UserAttributes=[{"Name": "name", "Value": full_name}])

    async def describe_user_pool(self) -> dict:
        return (await self._call("describe_user_pool"))["UserPool"]
