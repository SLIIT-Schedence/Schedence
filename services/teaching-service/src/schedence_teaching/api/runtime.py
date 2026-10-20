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
    teaching_generation_prepared_manifest: Path | None = None
    teaching_generation_institutions: tuple[UUID, ...] = ()
    teaching_generation_artifact_directory: Path | None = None
    teaching_generation_database: str | None = None
    teaching_generation_project_id: str | None = None
    teaching_generation_branch_id: str | None = None
    teaching_generation_max_timeout_seconds: float = Field(default=600, gt=0, le=3600)
    teaching_generation_preflight_timeout_seconds: float = Field(default=90, gt=0, le=180)
    teaching_generation_per_institution_limit: int = Field(default=2, ge=1, le=10)
    # LOCAL_PREVIEW serves saved, gate-validated local runs read-only (development only, never production).
    teaching_read_store: Literal["DATABASE", "LOCAL_PREVIEW"] = "DATABASE"
    teaching_local_preview_manifest: Path | None = None
    # Read-only generation input readiness (owner-record validation of a prepared snapshot; never stores or solves).
    teaching_generation_readiness: Literal["DISABLED", "DEVELOPMENT"] = "DISABLED"
    teaching_generation_timezone: str = "Asia/Colombo"

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
    readiness: object | None = None


def local_preview_runtime(settings):
    """Development-only read runtime over saved local runs: no database connection and no generation."""
    from .local_preview import LocalPreviewTimetableStore
    if settings.app_env == "production" or settings.teaching_generation_mode != "DISABLED":
        raise ValueError("the local preview store is for development reads only")
    if settings.teaching_local_preview_manifest is None:
        raise ValueError("the local preview store requires an explicit manifest")
    store = LocalPreviewTimetableStore(settings.teaching_local_preview_manifest)
    store.start()  # Re-validate the saved runs in the background from the first API use.
    http = httpx.AsyncClient(follow_redirects=False)
    return TeachingRuntime(build_verifier(settings),
        IdentityPermissionsClient(settings.identity_service_url, http, timeout=settings.identity_timeout_seconds),
        store, shutdown_hooks=(http.aclose,))


def build_runtime():
    from schedence_db import create_db_engine, create_session_factory, find_env_file, load_settings
    from .store import DatabaseTimetableStore
    shared_env = os.environ.get("SCHEDENCE_ENV_FILE") or find_env_file()
    settings = load_settings_model(TeachingSettings, shared_env)
    validate_prepared_mode(settings)
    if settings.teaching_read_store == "LOCAL_PREVIEW":
        return local_preview_runtime(settings)
    db = load_settings(env_prefix="TEACHING_", env_file=settings.teaching_database_env_file or shared_env, db_echo=False)
    # A long-lived API process keeps a small pre-pinged pool on the direct endpoint: opening a TLS connection to
    # Neon costs seconds, which a per-request NullPool paid on every read (db-core's direct default suits
    # short-lived scripts). Sized by TEACHING_DB_POOL_SIZE / TEACHING_DB_MAX_OVERFLOW.
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    engine = create_db_engine(db, direct=True, hide_parameters=True, poolclass=AsyncAdaptedQueuePool)
    http = httpx.AsyncClient(follow_redirects=False)
    runtime = TeachingRuntime(build_verifier(settings),
        IdentityPermissionsClient(settings.identity_service_url, http, timeout=settings.identity_timeout_seconds),
        DatabaseTimetableStore(create_session_factory(engine)), shutdown_hooks=(http.aclose, engine.dispose))
    prepared_inputs = None
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
            if settings.teaching_generation_prepared_manifest is not None:
                from schedence_teaching.generation.prepared_inputs import IsolatedPreparedInputs
                prepared_inputs = IsolatedPreparedInputs.from_path(repository,
                    {schema: engines[schema] for schema in ("academic", "resource")}, DatabaseTarget(*target_values),
                    settings.teaching_generation_prepared_manifest)
                if set(settings.teaching_generation_institutions) != {prepared_inputs.manifest.request.institution_id}:
                    raise ValueError("prepared generation must allow only its manifest institution")
                inputs = prepared_inputs
            runtime.generations = DurableGenerationJobs(repository, inputs, executor,
                institutions=settings.teaching_generation_institutions, enabled=True,
                max_timeout_seconds=settings.teaching_generation_max_timeout_seconds,
                preflight_timeout_seconds=settings.teaching_generation_preflight_timeout_seconds)
            # Input owners stay read-only. Generated test versions are retrieved
            # from the isolated result database, never mistaken for Neon drafts.
            runtime.store = DatabaseTimetableStore(repository.factory)
        runtime.shutdown_hooks += tuple(engines[schema].dispose for schema in ("academic", "resource"))
    if settings.teaching_generation_readiness == "DEVELOPMENT":
        runtime.readiness = readiness_runtime(settings, shared_env)
        runtime.readiness.stored_inputs = prepared_inputs
        runtime.shutdown_hooks += runtime.readiness.shutdown_hooks
    return runtime


def validate_prepared_mode(settings):
    if settings.teaching_generation_prepared_manifest is not None and (
            settings.app_env != "development" or settings.teaching_generation_mode != "DURABLE_ISOLATED"
            or settings.teaching_read_store != "DATABASE" or settings.teaching_database_env_file is None
            or settings.teaching_generation_readiness != "DEVELOPMENT"):
        raise ValueError("prepared input snapshots require development-only isolated persistence and read-only owner readiness")


def readiness_runtime(settings, shared_env):
    """Development-only, read-only input readiness: restricted Academic/Resource readers, no writes, no solver."""
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    from schedence_db import create_db_engine, load_settings
    from schedence_teaching.generation.readiness import GenerationReadiness, PreparationSources, PreparedDocuments
    from schedence_teaching.persistence.database_read import DatabaseTarget
    from schedence_teaching.pilot.__main__ import LOCAL_DATA, SERVICE_ROOT
    if settings.app_env == "production":
        raise ValueError("generation readiness is a development capability")
    target_values = (settings.teaching_generation_database, settings.teaching_generation_project_id, settings.teaching_generation_branch_id)
    if settings.teaching_database_env_file is None or not all(target_values):
        raise ValueError("generation readiness requires the restricted owner credentials file and an explicit database target")
    engines = {schema: create_db_engine(load_settings(env_prefix=schema.upper() + "_", env_file=settings.teaching_database_env_file,
        db_echo=False), direct=True, hide_parameters=True, poolclass=AsyncAdaptedQueuePool) for schema in ("academic", "resource")}
    sources = PreparationSources(LOCAL_DATA / "pilot-previews/step12a33-resumed-reviewed",
        LOCAL_DATA / "pilot-previews/step12a35-experimental-calendar/reviewed-results",
        SERVICE_ROOT / "examples/pp1-experimental-calendar.json", settings.teaching_generation_timezone)
    readiness = GenerationReadiness(engines, DatabaseTarget(*target_values), PreparedDocuments(sources))
    readiness.shutdown_hooks = tuple(engine.dispose for engine in engines.values())
    return readiness
