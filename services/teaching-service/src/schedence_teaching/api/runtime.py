"""Explicitly injectable runtime; configuration/connections are lazy on API use."""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from schedence_auth import CognitoSettings, build_verifier, load_settings_model

from .authorization import MissingAcademicBindings
from .identity import IdentityPermissionsClient


class TeachingSettings(CognitoSettings):
    identity_service_url: str = "http://localhost:8001"
    identity_timeout_seconds: float = Field(default=5, gt=0, le=30)
    teaching_database_env_file: Path | None = None
    teaching_generation_mode: Literal["DISABLED", "DEVELOPMENT", "DURABLE_ISOLATED"] = "DISABLED"
    teaching_generation_job_database_config: Path | None = None
    teaching_generation_institutions: tuple[UUID, ...] = ()
    teaching_generation_artifact_directory: Path | None = None
    teaching_generation_database: str | None = None
    teaching_generation_project_id: str | None = None
    teaching_generation_branch_id: str | None = None
    teaching_generation_max_timeout_seconds: float = Field(default=600, gt=0, le=3600)
    teaching_generation_preflight_timeout_seconds: float = Field(default=90, gt=0, le=180)
    teaching_generation_per_institution_limit: int = Field(default=2, ge=1, le=10)

    @field_validator("identity_service_url")
    @classmethod
    def trusted_origin(cls, value):
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Identity must be a configured service origin")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Identity requires HTTPS outside loopback development")
        return value.rstrip("/")


@dataclass
class TeachingRuntime:
    verifier: object
    permissions: object
    store: object
    bindings: object = field(default_factory=MissingAcademicBindings)
    shutdown_hooks: tuple = ()
    generations: object | None = None


def build_runtime():
    from schedence_db import create_db_engine, create_session_factory, find_env_file, load_settings
    from .store import DatabaseTimetableStore
    shared_env = os.environ.get("SCHEDENCE_ENV_FILE") or find_env_file()
    settings = load_settings_model(TeachingSettings, shared_env)
    db = load_settings(env_prefix="TEACHING_", env_file=settings.teaching_database_env_file or shared_env, db_echo=False)
    engine = create_db_engine(db, direct=True, hide_parameters=True)
    http = httpx.AsyncClient(follow_redirects=False)
    runtime = TeachingRuntime(build_verifier(settings),
        IdentityPermissionsClient(settings.identity_service_url, http, timeout=settings.identity_timeout_seconds),
        DatabaseTimetableStore(create_session_factory(engine)), shutdown_hooks=(http.aclose, engine.dispose))
    if settings.teaching_generation_mode in {"DEVELOPMENT", "DURABLE_ISOLATED"}:
        from schedence_teaching.generation.inputs import DatabaseGenerationInputs
        from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
        from schedence_teaching.generation.worker import ProcessGenerationExecutor
        from schedence_teaching.persistence.database_read import DatabaseTarget
        target_values = (settings.teaching_generation_database, settings.teaching_generation_project_id,
                         settings.teaching_generation_branch_id)
        if settings.teaching_generation_artifact_directory is None or not all(target_values):
            raise ValueError("development generation requires an explicit database target and private artifact directory")
        engines = {"teaching": engine}
        for schema in ("academic", "resource"):
            owner = load_settings(env_prefix=schema.upper()+"_", env_file=settings.teaching_database_env_file or shared_env, db_echo=False)
            engines[schema] = create_db_engine(owner, direct=True, hide_parameters=True)
        inputs = DatabaseGenerationInputs(engines, DatabaseTarget(*target_values))
        executor = ProcessGenerationExecutor(settings.teaching_generation_artifact_directory)
        if settings.teaching_generation_mode == "DEVELOPMENT":
            runtime.generations = DevelopmentGenerationJobs(inputs, executor, enabled=True,
                max_timeout_seconds=settings.teaching_generation_max_timeout_seconds,
                preflight_timeout_seconds=settings.teaching_generation_preflight_timeout_seconds,
                per_institution_limit=settings.teaching_generation_per_institution_limit)
        else:
            import json
            from schedence_teaching.generation.postgres import PostgresJobRepository
            from schedence_teaching.generation.durable import DurableGenerationJobs
            if settings.teaching_generation_job_database_config is None or not settings.teaching_generation_institutions:
                raise ValueError("durable isolated generation requires a private database configuration and institution allowlist")
            config = json.loads(settings.teaching_generation_job_database_config.read_text(encoding="utf-8"))
            repository = PostgresJobRepository(config["runtime_url"], institution_limit=settings.teaching_generation_per_institution_limit)
            runtime.generations = DurableGenerationJobs(repository, inputs, executor,
                institutions=settings.teaching_generation_institutions, enabled=True,
                max_timeout_seconds=settings.teaching_generation_max_timeout_seconds,
                preflight_timeout_seconds=settings.teaching_generation_preflight_timeout_seconds)
            # Input owners stay read-only. Generated test versions are retrieved
            # from the isolated result database, never mistaken for Neon drafts.
            runtime.store = DatabaseTimetableStore(repository.factory)
        runtime.shutdown_hooks += tuple(engines[schema].dispose for schema in ("academic", "resource"))
    return runtime
