"""Identity Service settings.

Cognito fields come from :class:`schedence_auth.CognitoSettings`; database
settings are loaded separately by ``schedence_db.load_settings()``. Both read
the same ``.env``: ``$SCHEDENCE_ENV_FILE`` if set, otherwise the nearest
``.env`` from the working directory up to the repository root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import Field, field_validator

from schedence_auth import CognitoSettings, load_settings_model
from schedence_db import find_env_file

ENV_FILE_VARIABLE = "SCHEDENCE_ENV_FILE"
SERVICE_NAME = "identity-service"
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


class IdentitySettings(CognitoSettings):
    aws_profile: str | None = Field(
        default=None,
        max_length=128,
        description="AWS CLI profile used by boto3 for Cognito admin calls. Unset in AWS deployments (use a role).",
    )
    frontend_url: str = "http://localhost:3000"
    cors_allowed_origins: str | None = Field(default=None, description="Comma-separated origins; defaults to FRONTEND_URL.")
    rate_limit_writes_per_minute: int = Field(default=60, ge=1, le=10_000)
    cognito_admin_timeout_seconds: float = Field(default=10, gt=0, le=60)

    @field_validator("frontend_url")
    @classmethod
    def _check_frontend_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not _ORIGIN.match(value):
            raise ValueError("must be an origin such as http://localhost:3000")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _check_origins(cls, value: str | None) -> str | None:
        if value is None:
            return None
        origins = [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        if not origins or not all(_ORIGIN.match(o) for o in origins):
            raise ValueError("must be a comma-separated list of origins (no wildcards)")
        return ",".join(origins)

    @property
    def cors_origins(self) -> list[str]:
        return (self.cors_allowed_origins or self.frontend_url).split(",")


def resolve_env_file() -> Path | None:
    explicit = os.environ.get(ENV_FILE_VARIABLE)
    return Path(explicit) if explicit else find_env_file()


def load_identity_settings(env_file: str | os.PathLike[str] | None = None) -> IdentitySettings:
    return load_settings_model(IdentitySettings, env_file if env_file is not None else resolve_env_file())
