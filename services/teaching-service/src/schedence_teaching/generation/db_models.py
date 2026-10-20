"""Separate ORM metadata: deployed history security checks still cover ten tables."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, Integer, String, Uuid, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JobBase(DeclarativeBase):
    pass


class GenerationJob(JobBase):
    __tablename__ = 'generation_jobs'
    __table_args__ = {'schema':'teaching'}
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by_identity_user_id: Mapped[UUID] = mapped_column(Uuid)
    source_snapshot_reference: Mapped[UUID] = mapped_column(Uuid)
    work_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    work_sha256: Mapped[str] = mapped_column(String)
    idempotency_sha256: Mapped[str] = mapped_column(String)
    request_sha256: Mapped[str] = mapped_column(String)
    request_payload: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_number: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[float] = mapped_column(Float)
    solver_status: Mapped[str | None] = mapped_column(String)
    solver_runtime_seconds: Mapped[float | None] = mapped_column(Float)
    expected_occurrences: Mapped[int] = mapped_column(Integer)
    scheduled_occurrences: Mapped[int] = mapped_column(Integer)
    validation_status: Mapped[str] = mapped_column(String)
    result_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    timetable_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    safe_error_category: Mapped[str | None] = mapped_column(String)
    safe_error_message: Mapped[str | None] = mapped_column(String)
    retry_with_new_key: Mapped[bool] = mapped_column(Boolean)


class GenerationJobAttempt(JobBase):
    __tablename__ = 'generation_job_attempts'
    __table_args__ = {'schema':'teaching'}
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    institution_id: Mapped[UUID] = mapped_column(Uuid)
    job_id: Mapped[UUID] = mapped_column(Uuid)
    attempt_number: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[UUID] = mapped_column(Uuid)
    worker_id: Mapped[UUID] = mapped_column(Uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String)
    error_category: Mapped[str | None] = mapped_column(String)
    report: Mapped[dict] = mapped_column(JSONB)
