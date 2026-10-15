"""Teaching-owned immutable history, never academic/identity/resource masters."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKeyConstraint,
                        Index, Integer, MetaData, String, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "teaching"
NAMING_CONVENTION = {"ix":"ix_%(table_name)s_%(column_0_N_name)s", "uq":"uq_%(table_name)s_%(column_0_N_name)s",
    "ck":"ck_%(table_name)s_%(constraint_name)s", "fk":"fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s", "pk":"pk_%(table_name)s"}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Entity:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    institution_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def snapshot_fk():
    return ForeignKeyConstraint(["institution_id","snapshot_id"],
        ["teaching.input_snapshots.institution_id","teaching.input_snapshots.id"], ondelete="RESTRICT")


class InputSnapshot(Entity, Base):
    __tablename__ = "input_snapshots"
    __table_args__ = (
        UniqueConstraint("institution_id","id",name="uq_snapshots_tenant_id"),
        UniqueConstraint("institution_id","id","expected_occurrences",name="uq_snapshots_expected"),
        CheckConstraint("schema_version = 1",name="schema_version"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'",name="sha256"),
        CheckConstraint("jsonb_typeof(payload) = 'object'",name="payload_object"),
        CheckConstraint("expected_occurrences >= 0",name="expected_count"),
        CheckConstraint("calendar_label IN ('PP1_TEST_CALENDAR','PP1_EXPERIMENTAL_CALENDAR','SUPPLIED_CALENDAR')",name="calendar_label"),
        Index("ix_snapshots_tenant_digest","institution_id","content_sha256"),
    )
    schema_version: Mapped[int] = mapped_column(Integer,nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    academic_term_reference: Mapped[str] = mapped_column(Text,nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(100),nullable=False)
    calendar_label: Mapped[str] = mapped_column(String(40),nullable=False)
    expected_occurrences: Mapped[int] = mapped_column(Integer,nullable=False)
    payload: Mapped[dict[str,Any]] = mapped_column(JSONB,nullable=False)


class SnapshotSource(Entity, Base):
    __tablename__ = "snapshot_sources"
    __table_args__ = (snapshot_fk(), UniqueConstraint("institution_id","snapshot_id","label"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'",name="sha256"),
        CheckConstraint("provenance_kind IN ('UNIVERSITY_SOURCED','APPROVED_PP1_ESTIMATE','SYNTHETIC_PP1','EXPERIMENTAL_CONFIGURATION','USER_CONFIRMED_CORRECTION','SERVICE_EXPORT')",name="provenance_kind"),
        CheckConstraint("jsonb_typeof(provenance) = 'object'",name="provenance_object"))
    snapshot_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    label: Mapped[str] = mapped_column(Text,nullable=False)
    sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    provenance_kind: Mapped[str] = mapped_column(String(40),nullable=False)
    owner: Mapped[str] = mapped_column(String(40),nullable=False)
    provenance: Mapped[dict[str,Any]] = mapped_column(JSONB,nullable=False)


class SnapshotScope(Entity, Base):
    __tablename__ = "snapshot_scopes"
    __table_args__ = (snapshot_fk(), UniqueConstraint("institution_id","snapshot_id","scope_key"),
        CheckConstraint("academic_year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 2",name="study_period"),
        CheckConstraint("stream IN ('WEEKDAY','WEEKEND')",name="stream"),
        CheckConstraint("student_count IS NULL OR student_count > 0",name="student_count"),
        CheckConstraint("scope_status IN ('SUPPORTED','BLOCKED_MISSING_ACADEMIC_MAPPING')",name="scope_status"),
        CheckConstraint("jsonb_typeof(group_references) = 'array'",name="group_array"),
        Index("ix_scopes_tenant_period","institution_id","semester","academic_year","stream","specialization"))
    snapshot_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    scope_key: Mapped[str] = mapped_column(Text,nullable=False)
    academic_year: Mapped[int] = mapped_column(Integer,nullable=False)
    semester: Mapped[int] = mapped_column(Integer,nullable=False)
    stream: Mapped[str] = mapped_column(String(10),nullable=False)
    specialization: Mapped[str] = mapped_column(Text,nullable=False)
    student_count: Mapped[int | None] = mapped_column(Integer)
    scope_status: Mapped[str] = mapped_column(String(40),nullable=False)
    group_references: Mapped[list[str]] = mapped_column(JSONB,nullable=False)


class SnapshotIssue(Entity, Base):
    __tablename__ = "snapshot_issues"
    __table_args__ = (snapshot_fk(),
        ForeignKeyConstraint(["institution_id","snapshot_id","scope_key"],
            ["teaching.snapshot_scopes.institution_id","teaching.snapshot_scopes.snapshot_id","teaching.snapshot_scopes.scope_key"],ondelete="RESTRICT"),
        CheckConstraint("severity IN ('INFO','WARNING','BLOCKER')",name="severity"),
        CheckConstraint("jsonb_typeof(details) = 'object'",name="details_object"))
    snapshot_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    scope_key: Mapped[str | None] = mapped_column(Text)
    issue_code: Mapped[str] = mapped_column(Text,nullable=False)
    severity: Mapped[str] = mapped_column(String(10),nullable=False)
    details: Mapped[dict[str,Any]] = mapped_column(JSONB,nullable=False)


class TimetableSeries(Entity, Base):
    __tablename__ = "timetable_series"
    __table_args__ = (UniqueConstraint("institution_id","id",name="uq_series_tenant_id"),
        UniqueConstraint("institution_id","series_reference"))
    series_reference: Mapped[str] = mapped_column(Text,nullable=False)


class GenerationRun(Entity, Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(["institution_id","series_id"],["teaching.timetable_series.institution_id","teaching.timetable_series.id"],ondelete="RESTRICT"),
        ForeignKeyConstraint(["institution_id","snapshot_id","expected_occurrences"],
            ["teaching.input_snapshots.institution_id","teaching.input_snapshots.id","teaching.input_snapshots.expected_occurrences"],ondelete="RESTRICT"),
        UniqueConstraint("institution_id","id",name="uq_runs_tenant_id"),
        UniqueConstraint("institution_id","series_id","id",name="uq_runs_series_id"),
        UniqueConstraint("institution_id","id","solver_success",name="uq_runs_success"),
        UniqueConstraint("institution_id","id","expected_occurrences",name="uq_runs_expected"),
        CheckConstraint("solver_status IN ('OPTIMAL','FEASIBLE','UNKNOWN','INFEASIBLE','MODEL_INVALID')",name="solver_status"),
        CheckConstraint("solver_success = (solver_status IN ('OPTIMAL','FEASIBLE'))",name="success_matches_status"),
        CheckConstraint("expected_occurrences >= 0 AND scheduled_occurrences >= 0 AND scheduled_occurrences <= expected_occurrences",name="counts"),
        CheckConstraint("(solver_success AND scheduled_occurrences = expected_occurrences) OR (NOT solver_success AND scheduled_occurrences = 0)",name="complete_or_empty"),
        CheckConstraint("runtime_seconds >= 0 AND runtime_seconds < 'Infinity'::float8",name="runtime"),
        CheckConstraint("jsonb_typeof(result_metadata) = 'object'",name="metadata_object"),
        Index("ix_runs_tenant_series_created","institution_id","series_id","created_at"))
    series_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    solver_status: Mapped[str] = mapped_column(String(20),nullable=False)
    solver_success: Mapped[bool] = mapped_column(Boolean,nullable=False)
    expected_occurrences: Mapped[int] = mapped_column(Integer,nullable=False)
    scheduled_occurrences: Mapped[int] = mapped_column(Integer,nullable=False)
    runtime_seconds: Mapped[float] = mapped_column(Float,nullable=False)
    result_metadata: Mapped[dict[str,Any]] = mapped_column(JSONB,nullable=False)


class ValidationReport(Entity, Base):
    __tablename__ = "validation_reports"
    __table_args__ = (
        ForeignKeyConstraint(["institution_id","run_id","expected_occurrences"],
            ["teaching.generation_runs.institution_id","teaching.generation_runs.id","teaching.generation_runs.expected_occurrences"],ondelete="RESTRICT"),
        UniqueConstraint("institution_id","run_id","id","passed",name="uq_reports_run_passed"),
        CheckConstraint("expected_occurrences >= 0 AND checked_occurrences BETWEEN 0 AND expected_occurrences AND hard_violations >= 0",name="counts"),
        CheckConstraint("NOT passed OR (hard_violations IS NOT NULL AND hard_violations = 0 AND checked_occurrences = expected_occurrences)",name="passed_complete"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'",name="evidence_object"))
    run_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    expected_occurrences: Mapped[int] = mapped_column(Integer,nullable=False)
    checked_occurrences: Mapped[int] = mapped_column(Integer,nullable=False)
    hard_violations: Mapped[int | None] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean,nullable=False)
    evidence: Mapped[dict[str,Any]] = mapped_column(JSONB,nullable=False)


class TimetableVersion(Entity, Base):
    __tablename__ = "timetable_versions"
    __table_args__ = (
        ForeignKeyConstraint(["institution_id","series_id","run_id"],
            ["teaching.generation_runs.institution_id","teaching.generation_runs.series_id","teaching.generation_runs.id"],ondelete="RESTRICT"),
        ForeignKeyConstraint(["institution_id","run_id","solver_success"],
            ["teaching.generation_runs.institution_id","teaching.generation_runs.id","teaching.generation_runs.solver_success"],ondelete="RESTRICT"),
        ForeignKeyConstraint(["institution_id","run_id","validation_report_id","validation_passed"],
            ["teaching.validation_reports.institution_id","teaching.validation_reports.run_id","teaching.validation_reports.id","teaching.validation_reports.passed"],ondelete="RESTRICT"),
        ForeignKeyConstraint(["institution_id","series_id","previous_version_id"],
            ["teaching.timetable_versions.institution_id","teaching.timetable_versions.series_id","teaching.timetable_versions.id"],ondelete="RESTRICT"),
        UniqueConstraint("institution_id","series_id","version_number"),
        UniqueConstraint("institution_id","series_id","id",name="uq_versions_series_id"),
        UniqueConstraint("institution_id","id",name="uq_versions_tenant_id"),
        UniqueConstraint("institution_id","run_id"),
        CheckConstraint("version_number > 0 AND ((version_number = 1 AND previous_version_id IS NULL) OR (version_number > 1 AND previous_version_id IS NOT NULL))",name="version_chain"),
        CheckConstraint("solver_success AND validation_passed",name="validated_success"),
        CheckConstraint("lifecycle_status = 'DRAFT_VALIDATED'",name="draft_only"),
        CheckConstraint("scope_completion = 'COMPLETE_SUPPORTED_SCOPE'",name="known_scope_complete"))
    series_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    validation_report_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    validation_passed: Mapped[bool] = mapped_column(Boolean,nullable=False)
    solver_success: Mapped[bool] = mapped_column(Boolean,nullable=False)
    version_number: Mapped[int] = mapped_column(Integer,nullable=False)
    previous_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    lifecycle_status: Mapped[str] = mapped_column(String(30),nullable=False)
    scope_completion: Mapped[str] = mapped_column(String(40),nullable=False)
    academic_complete: Mapped[bool] = mapped_column(Boolean,nullable=False)
    created_by_identity_user_id: Mapped[UUID | None] = mapped_column(Uuid)


class Delivery(Entity, Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        ForeignKeyConstraint(["institution_id","version_id"],["teaching.timetable_versions.institution_id","teaching.timetable_versions.id"],ondelete="RESTRICT"),
        UniqueConstraint("institution_id","version_id","id",name="uq_deliveries_version_id"),
        UniqueConstraint("institution_id","version_id","delivery_reference"),
        CheckConstraint("start_minute >= 0 AND end_minute <= 1440 AND end_minute > start_minute",name="time_range"),
        CheckConstraint("student_count > 0",name="student_count"),
        CheckConstraint("(delivery_mode = 'ONLINE' AND venue_reference IS NULL) OR (delivery_mode = 'PHYSICAL' AND venue_reference IS NOT NULL)",name="venue_mode"),
        Index("ix_deliveries_staff_date","institution_id","staff_reference","teaching_date"),
        Index("ix_deliveries_venue_date","institution_id","venue_reference","teaching_date"))
    version_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    delivery_reference: Mapped[str] = mapped_column(Text,nullable=False)
    teaching_date: Mapped[date] = mapped_column(Date,nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer,nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer,nullable=False)
    delivery_mode: Mapped[str] = mapped_column(String(10),nullable=False)
    staff_reference: Mapped[str] = mapped_column(Text,nullable=False)
    venue_reference: Mapped[str | None] = mapped_column(Text)
    student_count: Mapped[int] = mapped_column(Integer,nullable=False)


class DeliveryCoverage(Base):
    __tablename__ = "delivery_coverage"
    __table_args__ = (
        ForeignKeyConstraint(["institution_id","version_id","delivery_id"],
            ["teaching.deliveries.institution_id","teaching.deliveries.version_id","teaching.deliveries.id"],ondelete="RESTRICT"),
        CheckConstraint("duration_minutes > 0",name="duration"),
        CheckConstraint("session_type IN ('LECTURE','TUTORIAL','WORKSHOP','LAB')",name="session_type"),
        CheckConstraint("academic_year BETWEEN 1 AND 4 AND semester BETWEEN 1 AND 2",name="study_period"),
        CheckConstraint("stream IN ('WEEKDAY','WEEKEND')",name="stream"),
        Index("ix_coverage_group","institution_id","version_id","group_reference"))
    institution_id: Mapped[UUID] = mapped_column(Uuid,primary_key=True)
    version_id: Mapped[UUID] = mapped_column(Uuid,primary_key=True)
    occurrence_reference: Mapped[str] = mapped_column(Text,primary_key=True)
    delivery_id: Mapped[UUID] = mapped_column(Uuid,nullable=False)
    requirement_reference: Mapped[str] = mapped_column(Text,nullable=False)
    offering_reference: Mapped[str] = mapped_column(Text,nullable=False)
    group_reference: Mapped[str] = mapped_column(Text,nullable=False)
    session_type: Mapped[str] = mapped_column(String(12),nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer,nullable=False)
    academic_year: Mapped[int] = mapped_column(Integer,nullable=False)
    semester: Mapped[int] = mapped_column(Integer,nullable=False)
    stream: Mapped[str] = mapped_column(String(10),nullable=False)
    specialization: Mapped[str] = mapped_column(Text,nullable=False)
