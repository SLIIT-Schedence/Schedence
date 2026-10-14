"""Cognito settings shared by every Schedence service.

Services subclass :class:`CognitoSettings` to add their own fields and load it
with :func:`load_settings_model`. The issuer and JWKS URL are *derived* from
``AWS_REGION`` and ``COGNITO_USER_POOL_ID`` so there is a single source of
truth; an explicit ``COGNITO_ISSUER`` is accepted only if it matches.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schedence_auth.errors import ConfigurationError
from schedence_auth.verifier import (
    CLIENT_ID_PATTERN,
    REGION_PATTERN,
    USER_POOL_ID_PATTERN,
    CognitoTokenVerifier,
    CognitoVerifierConfig,
    cognito_issuer,
    cognito_jwks_url,
)
from schedence_auth.jwks import JwksCache

Environment = Literal["development", "testing", "production"]
SettingsT = TypeVar("SettingsT", bound=BaseSettings)


class CognitoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8-sig",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
    )

    app_env: Environment = "development"
    aws_region: str
    cognito_user_pool_id: str
    cognito_app_client_id: str
    cognito_devtools_client_id: str | None = Field(
        default=None,
        description="Public app client used only by Swagger UI / Postman. Ignored when APP_ENV=production.",
    )
    cognito_domain: str | None = Field(default=None, description="https://<prefix>.auth.<region>.amazoncognito.com")
    cognito_issuer: str | None = Field(default=None, description="Optional; must equal the derived issuer.")
    cognito_required_scopes: str = Field(default="", description="Space-separated scopes every access token must carry.")
    jwks_cache_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    token_leeway_seconds: int = Field(default=30, ge=0, le=300)

    @field_validator("aws_region")
    @classmethod
    def _check_region(cls, value: str) -> str:
        value = value.strip()
        if not REGION_PATTERN.match(value):
            raise ValueError("is not a valid AWS Region code (e.g. ap-south-1)")
        return value

    @field_validator("cognito_user_pool_id")
    @classmethod
    def _check_pool(cls, value: str) -> str:
        value = value.strip()
        if not USER_POOL_ID_PATTERN.match(value):
            raise ValueError("is not a valid user pool ID (e.g. ap-south-1_EXAMPLE123)")
        return value

    @field_validator("cognito_app_client_id", "cognito_devtools_client_id")
    @classmethod
    def _check_client(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not CLIENT_ID_PATTERN.match(value):
            raise ValueError("is not a valid app client ID")
        return value

    @field_validator("cognito_domain")
    @classmethod
    def _check_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith("https://") or "/" in value.removeprefix("https://"):
            raise ValueError("must be an https:// origin without a path, e.g. https://<prefix>.auth.<region>.amazoncognito.com")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> CognitoSettings:
        pool_region = USER_POOL_ID_PATTERN.match(self.cognito_user_pool_id).group("region")  # type: ignore[union-attr]
        if pool_region != self.aws_region:
            raise ValueError("COGNITO_USER_POOL_ID belongs to a different Region than AWS_REGION")
        if self.cognito_issuer is not None and self.cognito_issuer.strip().rstrip("/") != self.issuer:
            raise ValueError("COGNITO_ISSUER does not match the issuer derived from AWS_REGION and COGNITO_USER_POOL_ID")
        if self.cognito_devtools_client_id is not None and self.cognito_devtools_client_id == self.cognito_app_client_id:
            raise ValueError("COGNITO_DEVTOOLS_CLIENT_ID must be a separate public (SPA) app client, not the same ID as "
                             "COGNITO_APP_CLIENT_ID")
        return self

    @property
    def issuer(self) -> str:
        return cognito_issuer(self.aws_region, self.cognito_user_pool_id)

    @property
    def jwks_url(self) -> str:
        return cognito_jwks_url(self.issuer)

    @property
    def devtools_client_enabled(self) -> bool:
        return self.cognito_devtools_client_id is not None and self.app_env != "production"

    @property
    def allowed_client_ids(self) -> frozenset[str]:
        ids = {self.cognito_app_client_id}
        if self.devtools_client_enabled:
            ids.add(self.cognito_devtools_client_id)  # type: ignore[arg-type]
        return frozenset(ids)

    @property
    def required_scopes(self) -> frozenset[str]:
        return frozenset(self.cognito_required_scopes.split())

    @property
    def swagger_oauth_enabled(self) -> bool:
        return self.devtools_client_enabled and self.cognito_domain is not None

    def verifier_config(self) -> CognitoVerifierConfig:
        return CognitoVerifierConfig(
            region=self.aws_region,
            user_pool_id=self.cognito_user_pool_id,
            client_ids=self.allowed_client_ids,
            required_scopes=self.required_scopes,
            leeway_seconds=self.token_leeway_seconds,
        )


def build_verifier(settings: CognitoSettings) -> CognitoTokenVerifier:
    config = settings.verifier_config()
    return CognitoTokenVerifier(config, jwks=JwksCache(config.jwks_url, ttl_seconds=settings.jwks_cache_ttl_seconds))


def load_settings_model(model: type[SettingsT], env_file: str | os.PathLike[str] | None = None, **overrides: object) -> SettingsT:
    """Instantiate a settings class from the environment and an optional ``.env`` file.

    Raises:
        ConfigurationError: listing the offending variable names (never their values).
    """
    path = Path(env_file) if env_file is not None else None
    if path is not None and not path.is_file():
        raise ConfigurationError(f"Environment file not found: {path}")
    try:
        return model(_env_file=path, **overrides)  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_url=False, include_input=False, include_context=False):
            name = ".".join(str(p) for p in error["loc"]).upper() or "settings"
            if error["type"] == "missing":
                problems.append(f"{name} is required but was not set")
            else:
                message = error["msg"].removeprefix("Value error, ")
                problems.append(f"{name} {message}" if error["loc"] else message)
        source = f"environment variables and {path}" if path else "environment variables"
        raise ConfigurationError(f"Invalid configuration (read from {source}):\n  - " + "\n  - ".join(problems)) from None
    except UnicodeDecodeError:
        raise ConfigurationError(f"{path} is not UTF-8 encoded; re-save it as UTF-8") from None
