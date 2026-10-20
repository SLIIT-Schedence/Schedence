"""Settings of the Member 3 demo API (environment variables ``M3_DEMO_*``, no secret values).

Never reads ``.env``: only real process environment variables are used, so the repository
``.env`` can never switch this feature on by accident. The default is DISABLED.

LOCAL_DEMO       reads the synthetic seed-pack CSV files; no database.
NEON_READ_ONLY   reads ``m3_demo`` through ``demo_production`` (dedicated read-only role,
                 explicit confirmations). The database URL is NOT a setting: it is read from
                 the env file named by ``M3_DEMO_NEON_ENV_FILE`` by ``demo_production`` only.
"""

from __future__ import annotations

import uuid
from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.duty_allocation.demo_production import PRODUCTION_BRANCH

DEMO_INSTITUTION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
DEMO_PERIOD_CODE = "PP1-2026-S2-FINAL"


class DemoSource(str, Enum):
    DISABLED = "DISABLED"
    LOCAL_DEMO = "LOCAL_DEMO"
    NEON_READ_ONLY = "NEON_READ_ONLY"


class Member3DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="M3_DEMO_", env_file=None, extra="ignore", case_sensitive=False)

    source: DemoSource = Field(default=DemoSource.DISABLED, description="DISABLED, LOCAL_DEMO or NEON_READ_ONLY.")
    institution_id: uuid.UUID = Field(default=DEMO_INSTITUTION_ID, description="Synthetic institution of m3_demo.")
    period_code: str = Field(default=DEMO_PERIOD_CODE, pattern=r"^[A-Z0-9-]{1,32}$")
    snapshot_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    max_solver_seconds: float = Field(default=60.0, gt=0, le=600)

    seed_dir: Path | None = Field(default=None, description="LOCAL_DEMO: folder with manifest.json and csv/.")

    neon_env_file: Path | None = Field(default=None, description="Absolute path of the env file with the URL variable.")
    neon_url_variable: str | None = None
    neon_host: str | None = None
    neon_database: str | None = None
    neon_role: str | None = Field(default=None, description="Dedicated read-only role (never the owner).")
    neon_branch: str = PRODUCTION_BRANCH
    neon_confirmed_in_console: bool = False
    neon_credential_rotated: bool = False
    neon_acknowledgement: str = ""

    def missing_setup(self) -> tuple[str, ...]:
        """Names (never values) of the variables still needed for the chosen source."""
        if self.source is DemoSource.LOCAL_DEMO:
            return () if self.seed_dir is not None else ("M3_DEMO_SEED_DIR",)
        if self.source is not DemoSource.NEON_READ_ONLY:
            return ()
        needed = {"M3_DEMO_NEON_ENV_FILE": self.neon_env_file, "M3_DEMO_NEON_URL_VARIABLE": self.neon_url_variable,
                  "M3_DEMO_NEON_HOST": self.neon_host, "M3_DEMO_NEON_DATABASE": self.neon_database,
                  "M3_DEMO_NEON_ROLE": self.neon_role, "M3_DEMO_NEON_ACKNOWLEDGEMENT": self.neon_acknowledgement or None}
        missing = [name for name, value in needed.items() if value is None]
        if not self.neon_confirmed_in_console:
            missing.append("M3_DEMO_NEON_CONFIRMED_IN_CONSOLE=true")
        if not self.neon_credential_rotated:
            missing.append("M3_DEMO_NEON_CREDENTIAL_ROTATED=true")
        return tuple(missing)
