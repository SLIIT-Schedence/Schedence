"""Academic Data Service settings (shares the repository-root .env with other services)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import Field, field_validator

from schedence_auth import CognitoSettings, load_settings_model
from schedence_db import find_env_file

ENV_FILE_VARIABLE = "SCHEDENCE_ENV_FILE"
SERVICE_NAME = "academic-data-service"
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


class AcademicSettings(CognitoSettings):
    identity_service_url: str = Field(
        default="http://localhost:8001",
        description="Base URL of the Identity Service, which resolves callers' permissions. Use HTTPS (or mTLS) outside local development.",
    )
    identity_permissions_cache_seconds: int = Field(default=15, ge=0, le=300,
                                                    description="How long read requests may reuse a caller's permissions. Writes always re-check.")
    identity_timeout_seconds: float = Field(default=5, gt=0, le=30)
    frontend_url: str = "http://localhost:3000"
    cors_allowed_origins: str | None = None
    rate_limit_writes_per_minute: int = Field(default=60, ge=1, le=10_000)
    institution_timezone: str = Field(default="Asia/Colombo", description="Defines 'today' for effective dates.")

    @field_validator("institution_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("is not a known IANA time zone (e.g. Asia/Colombo)") from None
        return value

    @field_validator("identity_service_url", "frontend_url")
    @classmethod
    def _check_origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not _ORIGIN.match(value):
            raise ValueError("must be an origin such as http://localhost:8001")
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


def load_academic_settings(env_file: str | os.PathLike[str] | None = None) -> AcademicSettings:
    return load_settings_model(AcademicSettings, env_file if env_file is not None else resolve_env_file())
