"""Explicitly injectable runtime; configuration/connections are lazy on API use."""

from dataclasses import dataclass, field
import os
import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from schedence_auth import CognitoSettings, build_verifier, load_settings_model

from .authorization import MissingAcademicBindings
from .identity import IdentityPermissionsClient

logger = logging.getLogger(__name__)


class TeachingSettings(CognitoSettings):
    identity_service_url: str = "http://localhost:8001"
    identity_timeout_seconds: float = Field(default=5, gt=0, le=30)
    teaching_database_env_file: Path | None = None
    # OWNER_API: single-process development worker over the owners' HTTP exports and Teaching's stored snapshots,
    # saving validated drafts through the restricted runtime role (no owner database logins).
    teaching_generation_mode: Literal["DISABLED", "DEVELOPMENT", "DURABLE_ISOLATED", "OWNER_API"] = "DISABLED"
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
    teaching_generation_readiness: Literal["DISABLED", "ENABLED", "DEVELOPMENT"] = "ENABLED"
    teaching_generation_timezone: str = "Asia/Colombo"
    teaching_form_sending_enabled: bool = True
    academic_service_url: str = "http://localhost:8002"
    resource_service_url: str = "http://localhost:8006"
    teaching_owner_timeout_seconds: float = Field(default=10, gt=0, le=60)
    teaching_academic_inputs_path: str = "/api/v1/academic/teaching-inputs"
    teaching_resource_inputs_path: str = "/api/v1/resource/teaching-inputs"
    teaching_form_recipients_path: str = "/api/v1/academic/form-recipients"
    teaching_form_self_path: str = "/api/v1/academic/form-recipients/me"
    teaching_forms_public_origin: str = "http://localhost:3000"
    # Restricted role (migration 20261021_0005) every connection switches to; empty when the login already is it.
    teaching_database_role: str = "schedence_teaching_runtime"

    @field_validator("teaching_database_role")
    @classmethod
    def plain_role_name(cls, value):
        import re
        if value and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
            raise ValueError("TEACHING_DATABASE_ROLE must be a plain lower-case role name")
        return value

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
    forms: object | None = None
    form_sending: object | None = None


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


def use_database_role(engine, role):
    """Run every new connection as the restricted runtime role, so RLS and the security checks see that role.

    SET ROLE is session-level and committed at once: a later transaction rollback or pool reset cannot undo it.
    """
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def set_role(dbapi_connection, _record):
        autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_user")
            if cursor.fetchone()[0] != role:
                cursor.execute(f'SET ROLE "{role}"')
        finally:
            cursor.close()
            dbapi_connection.autocommit = autocommit


def build_runtime():
    from schedence_db import create_db_engine, create_session_factory, find_env_file, load_settings
    from .store import DatabaseTimetableStore
    shared_env = os.environ.get("SCHEDENCE_ENV_FILE") or find_env_file()
    settings = load_settings_model(TeachingSettings, shared_env)
    if settings.teaching_read_store == "LOCAL_PREVIEW":
        return local_preview_runtime(settings)
    # Teaching's own restricted credentials belong in the shared env, not a
    # workstation-only credentials file. No fallback to owner service logins.
    from dotenv import dotenv_values
    env_values = dotenv_values(shared_env, interpolate=False)
    prefix = "TEACHING_" if os.environ.get("TEACHING_DATABASE_URL") or env_values.get("TEACHING_DATABASE_URL") else ""
    db = load_settings(env_prefix=prefix, env_file=shared_env, db_echo=False, app_env=settings.app_env)
    # A long-lived API process keeps a small pre-pinged pool on the direct endpoint: opening a TLS connection to
    # Neon costs seconds, which a per-request NullPool paid on every read (db-core's direct default suits
    # short-lived scripts). Sized by TEACHING_DB_POOL_SIZE / TEACHING_DB_MAX_OVERFLOW.
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    engine = create_db_engine(db, direct=True, hide_parameters=True, poolclass=AsyncAdaptedQueuePool)
    if settings.teaching_database_role:
        use_database_role(engine, settings.teaching_database_role)
    http = httpx.AsyncClient(follow_redirects=False)
    runtime = TeachingRuntime(build_verifier(settings),
        IdentityPermissionsClient(settings.identity_service_url, http, timeout=settings.identity_timeout_seconds),
        DatabaseTimetableStore(create_session_factory(engine)), shutdown_hooks=(http.aclose, engine.dispose))
    if settings.teaching_generation_mode != "DISABLED":
        try:
            configure_generation(runtime, settings, engine, shared_env, http=http)
        except Exception as error:
            runtime.generations = None
            logger.warning("Generation configuration unavailable (%s); other Teaching APIs remain enabled", type(error).__name__)
    if settings.teaching_generation_readiness != "DISABLED":
        try:
            runtime.readiness = readiness_runtime(settings, factory=runtime.store.factory, http=http)
        except Exception as error:
            logger.warning("Readiness configuration unavailable (%s); other Teaching APIs remain enabled", type(error).__name__)
    from schedence_teaching.forms.store import SqlFormTemplateStore
    runtime.forms = SqlFormTemplateStore(runtime.store.factory)
    if settings.teaching_form_sending_enabled:
        try:
            configure_form_sending(runtime, settings, http=http)
        except Exception as error:
            logger.warning("Form sending configuration unavailable (%s); other Teaching APIs remain enabled", type(error).__name__)
    return runtime


def configure_generation(runtime, settings, engine, shared_env, *, http=None):
    """Legacy opt-in worker setup; isolated from unrelated read/form APIs."""
    from schedence_db import create_db_engine, load_settings
    from .store import DatabaseTimetableStore
    validate_prepared_mode(settings)
    if settings.teaching_generation_mode == "OWNER_API":
        configure_owner_api_generation(runtime, settings, http)
        return
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
            runtime.shutdown_hooks += (engines[schema].dispose,)
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


def configure_owner_api_generation(runtime, settings, http):
    """Development worker that reads inputs exactly as readiness does and saves drafts as the restricted role."""
    from schedence_teaching.generation.http_readiness import DatabasePreparedDocuments, OwnerApiGenerationInputs
    from schedence_teaching.generation.jobs import DevelopmentGenerationJobs
    from schedence_teaching.generation.persistence import TeachingDatabasePersistence
    from schedence_teaching.generation.worker import ProcessGenerationExecutor
    from schedence_teaching.pilot.__main__ import LOCAL_DATA
    if settings.app_env == "production":
        raise ValueError("the single-process OWNER_API worker is a development capability")
    if http is None:
        http = httpx.AsyncClient(follow_redirects=False)
        runtime.shutdown_hooks += (http.aclose,)
    factory = runtime.store.factory
    inputs = OwnerApiGenerationInputs(owner_client(settings, http), DatabasePreparedDocuments(factory),
        academic_path=settings.teaching_academic_inputs_path, resource_path=settings.teaching_resource_inputs_path)
    executor = ProcessGenerationExecutor(settings.teaching_generation_artifact_directory or LOCAL_DATA / "generation-artifacts")
    runtime.generations = DevelopmentGenerationJobs(inputs, executor, enabled=True,
        persistence=TeachingDatabasePersistence(factory),
        max_timeout_seconds=settings.teaching_generation_max_timeout_seconds,
        preflight_timeout_seconds=settings.teaching_generation_preflight_timeout_seconds,
        per_institution_limit=settings.teaching_generation_per_institution_limit)


def owner_client(settings, http, *, include_resource=True):
    from .owner_http import OwnerApiClient
    return OwnerApiClient(http, settings.academic_service_url, settings.resource_service_url if include_resource else None,
        timeout=settings.teaching_owner_timeout_seconds)


def configure_form_sending(runtime, settings, *, http=None):
    """Permission-checked Forms over Teaching storage and Academic HTTP identity data."""
    from schedence_teaching.forms.http_directory import ApiLecturerDirectory
    from schedence_teaching.forms.sending import FormSending
    from schedence_teaching.forms.sending_store import SqlFormSendingStore
    if http is None:
        http = httpx.AsyncClient(follow_redirects=False)
        runtime.shutdown_hooks += (http.aclose,)
    directory = ApiLecturerDirectory(owner_client(settings, http, include_resource=False), settings.teaching_form_recipients_path,
        settings.teaching_form_self_path)
    runtime.form_sending = FormSending(SqlFormSendingStore(runtime.forms), directory, settings.teaching_forms_public_origin)


def validate_prepared_mode(settings):
    if settings.teaching_generation_prepared_manifest is not None and (
            settings.app_env != "development" or settings.teaching_generation_mode != "DURABLE_ISOLATED"
            or settings.teaching_read_store != "DATABASE" or settings.teaching_database_env_file is None
            or settings.teaching_generation_readiness == "DISABLED"):
        raise ValueError("prepared input snapshots require development-only isolated persistence and read-only owner readiness")


def readiness_runtime(settings, shared_env=None, *, factory=None, http=None):
    """All-environment HTTP readiness. No owner engines, Neon pins or private files."""
    from schedence_teaching.generation.http_readiness import ApiGenerationReadiness, DatabasePreparedDocuments
    if factory is None:
        raise ValueError("Readiness requires Teaching's shared database session factory")
    owns_http = http is None
    if owns_http:
        http = httpx.AsyncClient(follow_redirects=False)
    readiness = ApiGenerationReadiness(owner_client(settings, http), DatabasePreparedDocuments(factory),
        academic_path=settings.teaching_academic_inputs_path, resource_path=settings.teaching_resource_inputs_path)
    readiness.shutdown_hooks = (http.aclose,) if owns_http else ()
    return readiness
