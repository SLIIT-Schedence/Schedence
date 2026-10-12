from __future__ import annotations

import os

import pytest

from schedence_auth.testing import TokenFactory

_SETTINGS_PREFIXES = ("AWS_", "COGNITO_", "APP_ENV", "JWKS_", "TOKEN_", "SCHEDENCE_ENV_FILE")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the developer's real Cognito/AWS settings."""
    for name in list(os.environ):
        if name.upper().startswith(_SETTINGS_PREFIXES):
            monkeypatch.delenv(name)


@pytest.fixture
def tokens() -> TokenFactory:
    return TokenFactory()
