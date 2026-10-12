"""Database configuration loaded from environment variables and ``.env`` files.

Precedence (highest first):

1. keyword overrides passed to :func:`load_settings`
2. process environment variables
3. the ``.env`` file (see :func:`load_settings` for how it is located)
4. field defaults
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from schedence_db.exceptions import DatabaseConfigurationError

Environment = Literal["development", "testing", "production"]

#: Environment variable that points at an explicit ``.env`` file.
ENV_FILE_VARIABLE = "SCHEDENCE_ENV_FILE"

DRIVER_NAME = "postgresql+psycopg"
_ACCEPTED_SCHEMES = frozenset({"postgresql", "postgres", DRIVER_NAME})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_INSECURE_SSL_MODES = frozenset({"disable", "allow", "prefer"})


def normalize_database_url(raw: str) -> str:
    """Validate a PostgreSQL URL and adapt it to SQLAlchemy + psycopg 3.

    * ``postgresql://`` / ``postgres://`` become ``postgresql+psycopg://``.
    * Remote hosts without ``sslmode`` get ``sslmode=require``; remote hosts
      with ``sslmode`` set to ``disable``, ``allow`` or ``prefer`` are rejected.
    * Other query parameters (e.g. Neon's ``channel_binding=require``) are kept.

    Error messages never include the URL or any part of its credentials.
    """
    try:
        url = make_url(raw.strip())
    except Exception:  # ArgumentError, or ValueError from a malformed port
        raise ValueError(
            "is not a valid database URL (expected postgresql://USER:PASSWORD@HOST/DATABASE); "
            "special characters in the password must be URL-encoded"
        ) from None

    if url.drivername not in _ACCEPTED_SCHEMES:
        raise ValueError(
            f"must use the postgresql:// or {DRIVER_NAME}:// scheme (found {url.drivername}://)"
        )
    url = url.set(drivername=DRIVER_NAME)

    if not url.host:
        raise ValueError("is missing the host name")
    if not url.database:
        raise ValueError("is missing the database name")

    if url.host not in _LOCAL_HOSTS:
        ssl_mode = url.query.get("sslmode")
        if ssl_mode is None:
            url = url.update_query_dict({"sslmode": "require"})
        elif ssl_mode in _INSECURE_SSL_MODES:
            raise ValueError(
                f"uses sslmode={ssl_mode}, which does not guarantee encryption; "
                "use sslmode=require (or verify-full) for remote databases"
            )

    return url.render_as_string(hide_password=False)


class DatabaseSettings(BaseSettings):
    """Connection settings for one service's database.

    Secrets are stored as :class:`~pydantic.SecretStr`, so ``repr()``, ``str()``
    and ``model_dump()`` never reveal them. Use :meth:`get_url` to obtain a
    SQLAlchemy :class:`~sqlalchemy.engine.URL` (whose ``repr`` masks the password).
    """

    model_config = SettingsConfigDict(
        env_file=None,  # resolved by load_settings(); see its docstring
        env_file_encoding="utf-8-sig",  # tolerate the BOM PowerShell 5.1 writes
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
    )

    app_env: Environment = Field(
        default="development",
        description="Deployment environment: development, testing or production.",
    )
    database_url: SecretStr = Field(
        description="Pooled connection URL used by the application at runtime.",
    )
    direct_database_url: SecretStr | None = Field(
        default=None,
        description="Direct (non-pooled) URL for migrations and admin tasks. Falls back to DATABASE_URL.",
    )

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout: float = Field(default=30, gt=0, le=300)
    db_pool_recycle: int = Field(default=1800, ge=-1, description="Seconds; -1 disables recycling.")
    db_pool_pre_ping: bool = True
    db_use_null_pool: bool | None = Field(
        default=None,
        description="Disable client-side pooling. Defaults to true when APP_ENV=testing.",
    )
    db_connect_timeout: int = Field(default=10, ge=1, le=120, description="Seconds.")
    db_echo: bool = False
    db_application_name: str | None = Field(
        default=None,
        max_length=63,
        description="Reported to PostgreSQL as application_name, e.g. 'identity-service'.",
    )
    db_disable_prepared_statements: bool = Field(
        default=False,
        description="Set true if a connection pooler reports 'prepared statement ... does not exist'.",
    )

    @field_validator("database_url", "direct_database_url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any, info: ValidationInfo) -> Any:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None or (isinstance(value, str) and not value.strip()):
            if info.field_name == "direct_database_url":
                return None
            raise ValueError("must not be empty")
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _check_environment_rules(self) -> DatabaseSettings:
        if self.app_env == "production" and self.db_echo:
            raise ValueError("DB_ECHO must be false in production (SQL logging can expose sensitive data)")
        return self

    @property
    def use_null_pool(self) -> bool:
        """Whether engines should skip client-side pooling."""
        if self.db_use_null_pool is not None:
            return self.db_use_null_pool
        return self.app_env == "testing"

    @property
    def has_direct_url(self) -> bool:
        return self.direct_database_url is not None

    def get_url(self, *, direct: bool = False) -> URL:
        """Return the pooled URL, or the direct URL when ``direct=True`` and one is configured."""
        secret = self.direct_database_url if direct and self.direct_database_url else self.database_url
        return make_url(secret.get_secret_value())


def find_env_file(start: str | os.PathLike[str] | None = None, filename: str = ".env") -> Path | None:
    """Find ``filename`` in ``start`` (default: the current directory) or its parents.

    The search stops at the first directory containing ``.git`` (the repository
    root), so a stray ``.env`` outside the repository is never picked up.
    """
    current = Path(start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def load_settings(
    env_file: str | os.PathLike[str] | Literal[False] | None = None,
    *,
    env_prefix: str = "",
    **overrides: Any,
) -> DatabaseSettings:
    """Load and validate :class:`DatabaseSettings`.

    Args:
        env_file: Path to a ``.env`` file, or ``False`` to read only the process
            environment. When omitted, ``$SCHEDENCE_ENV_FILE`` is used if set;
            otherwise the nearest ``.env`` found by :func:`find_env_file`.
        env_prefix: Optional variable prefix, e.g. ``"IDENTITY_"`` reads
            ``IDENTITY_DATABASE_URL``, ``IDENTITY_DB_POOL_SIZE``, ...
        **overrides: Field values that take precedence over every other source.

    Raises:
        DatabaseConfigurationError: if a setting is missing or invalid. The
            message names the setting but never includes its value.
    """
    resolved = _resolve_env_file(env_file)
    try:
        return DatabaseSettings(_env_file=resolved, _env_prefix=env_prefix, **overrides)
    except ValidationError as exc:
        raise _configuration_error(exc, env_prefix, resolved) from None
    except UnicodeDecodeError:
        raise DatabaseConfigurationError(
            f"{resolved} is not UTF-8 encoded; re-save it as UTF-8 "
            "(PowerShell 5.1 '>' redirection writes UTF-16)"
        ) from None


def _resolve_env_file(env_file: str | os.PathLike[str] | Literal[False] | None) -> Path | None:
    if env_file is False:
        return None
    if env_file is None:
        from_variable = os.environ.get(ENV_FILE_VARIABLE)
        if not from_variable:
            return find_env_file()
        env_file = from_variable
    path = Path(env_file)
    if not path.is_file():
        raise DatabaseConfigurationError(f"Environment file not found: {path}")
    return path


def _configuration_error(
    exc: ValidationError, env_prefix: str, env_file: Path | None
) -> DatabaseConfigurationError:
    problems: list[str] = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        location = ".".join(str(part) for part in error["loc"])
        name = f"{env_prefix}{location}".upper() if location else "settings"
        if error["type"] == "missing":
            problems.append(f"{name} is required but was not set")
        else:
            message = error["msg"].removeprefix("Value error, ")
            problems.append(f"{name} {message}" if location else message)

    source = f"environment variables and {env_file}" if env_file else "environment variables (no .env file found)"
    message = f"Invalid database configuration (read from {source}):\n  - " + "\n  - ".join(problems)

    hint = _env_file_syntax_hint(env_file, f"{env_prefix}DATABASE_URL")
    if hint:
        message += f"\n  Hint: {hint}"
    return DatabaseConfigurationError(message)


def _env_file_syntax_hint(env_file: Path | None, variable: str) -> str | None:
    """Detect ``DATABASE_URL: value`` (colon instead of ``=``), a common .env mistake."""
    if env_file is None:
        return None
    try:
        text = env_file.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(variable)}\s*:", re.IGNORECASE | re.MULTILINE)
    if pattern.search(text):
        return f"{env_file} writes '{variable}:' - .env files need '{variable}=<value>' (equals sign, no spaces)."
    return None
