from __future__ import annotations

from pathlib import Path

import pytest

from schedence_auth import CognitoSettings, ConfigurationError, load_settings_model
from schedence_auth.testing import TEST_CLIENT_ID, TEST_DEVTOOLS_CLIENT_ID, TEST_POOL_ID

BASE = {
    "aws_region": "ap-south-1",
    "cognito_user_pool_id": TEST_POOL_ID,
    "cognito_app_client_id": TEST_CLIENT_ID,
}


def test_issuer_and_jwks_are_derived() -> None:
    settings = CognitoSettings(**BASE)
    assert settings.issuer == f"https://cognito-idp.ap-south-1.amazonaws.com/{TEST_POOL_ID}"
    assert settings.jwks_url == settings.issuer + "/.well-known/jwks.json"


def test_explicit_issuer_must_match_derived_value() -> None:
    CognitoSettings(**BASE, cognito_issuer=f"https://cognito-idp.ap-south-1.amazonaws.com/{TEST_POOL_ID}/")
    with pytest.raises(ValueError, match="COGNITO_ISSUER"):
        CognitoSettings(**BASE, cognito_issuer="https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_Other")


def test_region_must_match_pool() -> None:
    with pytest.raises(ValueError, match="different Region"):
        CognitoSettings(**{**BASE, "aws_region": "us-east-2"})


def test_devtools_client_is_accepted_only_outside_production() -> None:
    dev = CognitoSettings(**BASE, cognito_devtools_client_id=TEST_DEVTOOLS_CLIENT_ID, app_env="development")
    prod = CognitoSettings(**BASE, cognito_devtools_client_id=TEST_DEVTOOLS_CLIENT_ID, app_env="production")
    assert dev.allowed_client_ids == {TEST_CLIENT_ID, TEST_DEVTOOLS_CLIENT_ID}
    assert prod.allowed_client_ids == {TEST_CLIENT_ID}


def test_devtools_client_must_differ_from_the_web_client() -> None:
    with pytest.raises(ValueError, match="separate public"):
        CognitoSettings(**BASE, cognito_devtools_client_id=TEST_CLIENT_ID)


def test_domain_must_be_https_origin() -> None:
    with pytest.raises(ValueError):
        CognitoSettings(**BASE, cognito_domain="http://insecure.example.com")


def test_load_errors_name_variables_but_not_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("AWS_REGION=ap-south-1\nCOGNITO_USER_POOL_ID=not-a-pool-SECRETVALUE\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as info:
        load_settings_model(CognitoSettings, env)
    message = str(info.value)
    assert "COGNITO_USER_POOL_ID" in message
    assert "COGNITO_APP_CLIENT_ID is required" in message
    assert "SECRETVALUE" not in message
