"""Authentication settings (shares the repository-root .env with the other services).

Cognito fields (AWS_REGION, COGNITO_*) come from auth-core's CognitoSettings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import Field, field_validator

from schedence_auth import CognitoSettings, load_settings_model
from schedence_db import find_env_file

ENV_FILE_VARIABLE = "SCHEDENCE_ENV_FILE"
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


class ExaminationAuthSettings(CognitoSettings):
    identity_service_url: str = Field(
        default="http://localhost:8001",
        description="Base URL of the Identity Service, which resolves callers' permissions. "
        "Use HTTPS (or mTLS) outside local development.",
    )
    identity_permissions_cache_seconds: int = Field(
        default=15, ge=0, le=300, description="How long read requests may reuse a caller's permissions."
    )
    identity_timeout_seconds: float = Field(default=5, gt=0, le=30)
    # No institution is configured: each request's institution comes from the caller's
    # permissions (see app.auth.dependencies). Any number of institutions can exist.
    frontend_url: str = "http://localhost:3000"

    @field_validator("identity_service_url", "frontend_url")
    @classmethod
    def _check_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not _ORIGIN.match(value):
            raise ValueError("must be an origin such as http://localhost:8001")
        return value


def load_auth_settings(env_file: str | os.PathLike[str] | None = None) -> ExaminationAuthSettings:
    """Raises ``schedence_auth.ConfigurationError`` naming missing variables (never their values)."""
    if env_file is None:
        explicit = os.environ.get(ENV_FILE_VARIABLE)
        env_file = Path(explicit) if explicit else find_env_file()
    return load_settings_model(ExaminationAuthSettings, env_file)
