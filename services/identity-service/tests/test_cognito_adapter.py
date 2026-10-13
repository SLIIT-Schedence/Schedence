"""boto3 adapter against botocore's Stubber: exact request shapes and error translation, no network."""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from app.integrations.cognito import (
    CognitoMisconfiguredError,
    CognitoPermissionError,
    CognitoThrottledError,
    CognitoUnavailableError,
    CognitoUserExistsError,
)
from app.integrations.cognito.boto3_client import Boto3CognitoAdminClient, translate_error

POOL = "ap-south-1_TestPool1"


@pytest.fixture
def stubbed():
    client = boto3.client("cognito-idp", region_name="ap-south-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    with Stubber(client) as stubber:
        yield Boto3CognitoAdminClient(region="ap-south-1", user_pool_id=POOL, client=client), stubber
        stubber.assert_no_pending_responses()


async def test_create_user_sends_verified_email_and_invitation(stubbed) -> None:
    adapter, stubber = stubbed
    stubber.add_response(
        "admin_create_user",
        {"User": {"Username": "sub-123", "Enabled": True, "UserStatus": "FORCE_CHANGE_PASSWORD",
                  "Attributes": [{"Name": "sub", "Value": "sub-123"}, {"Name": "email", "Value": "lec@sliit.example"},
                                 {"Name": "email_verified", "Value": "true"}, {"Name": "name", "Value": "Lec"}]}},
        {
            "UserPoolId": POOL,
            "Username": "lec@sliit.example",
            "UserAttributes": [{"Name": "email", "Value": "lec@sliit.example"}, {"Name": "email_verified", "Value": "true"},
                               {"Name": "name", "Value": "Lec"}],
            "DesiredDeliveryMediums": ["EMAIL"],
        },
    )
    user = await adapter.create_user(email="lec@sliit.example", full_name="Lec")
    assert (user.sub, user.status, user.email_verified) == ("sub-123", "FORCE_CHANGE_PASSWORD", True)


async def test_get_missing_user_returns_none(stubbed) -> None:
    adapter, stubber = stubbed
    stubber.add_client_error("admin_get_user", service_error_code="UserNotFoundException", http_status_code=400)
    assert await adapter.get_user("nobody@sliit.example") is None


async def test_existing_user_maps_to_user_exists(stubbed) -> None:
    adapter, stubber = stubbed
    stubber.add_client_error("admin_create_user", service_error_code="UsernameExistsException",
                             service_message="User account already exists for lec@sliit.example")
    with pytest.raises(CognitoUserExistsError) as info:
        await adapter.create_user(email="lec@sliit.example", full_name="Lec")
    assert "lec@sliit.example" not in str(info.value)  # raw AWS messages never propagate


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("TooManyRequestsException", CognitoThrottledError),
        ("AccessDeniedException", CognitoPermissionError),
        ("NotAuthorizedException", CognitoPermissionError),
        ("ResourceNotFoundException", CognitoMisconfiguredError),
        ("InternalErrorException", CognitoUnavailableError),
    ],
)
async def test_error_translation(stubbed, code, expected) -> None:
    adapter, stubber = stubbed
    stubber.add_client_error("admin_disable_user", service_error_code=code)
    with pytest.raises(expected):
        await adapter.disable_user("sub-123")


def test_network_errors_are_unavailable() -> None:
    assert isinstance(translate_error(EndpointConnectionError(endpoint_url="https://x")), CognitoUnavailableError)


async def test_resend_uses_resend_message_action(stubbed) -> None:
    adapter, stubber = stubbed
    stubber.add_response("admin_create_user", {"User": {"Username": "sub-1"}},
                         {"UserPoolId": POOL, "Username": "lec@sliit.example", "MessageAction": "RESEND",
                          "DesiredDeliveryMediums": ["EMAIL"]})
    await adapter.resend_invitation(email="lec@sliit.example")
