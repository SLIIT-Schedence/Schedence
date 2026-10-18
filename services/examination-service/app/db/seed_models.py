"""Development-only seed tables (``examinations.seed_*``).

These tables hold a **synthetic** dataset so the Examination Service can be
built before the Academic Data Service and Resource Service exist. They are a
local snapshot of data those services will own later:

=================================  =========================================
Seed table                         Future owner / replacement
=================================  =========================================
seed_modules                       Academic Data Service: module offerings API
seed_module_specializations        Academic Data Service: programme structure API
seed_students                      Academic Data Service: student/batch API
seed_registrations                 Academic Data Service: registrations API
seed_venues                        Resource Service: venues API
seed_venue_blocks                  Resource Service: availability API
seed_exam_periods (+ exclusions)   Examination Service (stays here), but fed
                                   by the institutional calendar
=================================  =========================================

Scheduling code must read this data through the source interfaces in
``app.sources`` (to be added with the scheduler), never by importing these
models directly, so the tables can be swapped for API clients later.

String "enums" use CHECK constraints instead of PostgreSQL ENUM types, which
are harder to change in migrations.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EXAM_TYPES = ("written", "computer_based", "hybrid")
# Still allowed by the database for the original SYN rows; removed by a later
# cleanup migration. New data must use EXAM_TYPES.
LEGACY_EXAM_TYPES = ("practical",)
REGISTRATION_TYPES = ("normal", "repeat")
# Campus venue types (data/venue_catalog.csv). Migration 0003 still allows the
# old fictional types (auditorium, exam_hall) so the old rows stay valid until
# they are replaced; migration 0004 removes them.
VENUE_TYPES = ("lecture_hall", "smart_classroom", "computer_lab", "byod_lab")
# Real availability (filled later from the Teaching Timetable / Resource services)
# and clearly labelled prototype simulations.
REAL_BLOCK_SOURCES = ("teaching_timetable", "maintenance")
SIMULATED_BLOCK_SOURCES = ("simulated_teaching", "simulated_maintenance")
BLOCK_SOURCES = REAL_BLOCK_SOURCES + SIMULATED_BLOCK_SOURCES


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class SeedModule(Base):
    __tablename__ = "seed_modules"
    __table_args__ = (
        CheckConstraint("academic_year BETWEEN 1 AND 4", name="academic_year_range"),
        CheckConstraint("semester IN (1, 2)", name="semester_range"),
        CheckConstraint("credits BETWEEN 1 AND 10", name="credits_range"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="difficulty_range"),
        CheckConstraint(_in("exam_type", EXAM_TYPES + LEGACY_EXAM_TYPES), name="exam_type_valid"),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        CheckConstraint("reading_time_minutes >= 0", name="reading_time_non_negative"),
    )

    module_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    module_name: Mapped[str] = mapped_column(String(120))
    academic_year: Mapped[int] = mapped_column(SmallInteger)
    semester: Mapped[int] = mapped_column(SmallInteger)
    # Legacy single specialization of the original SYN rows. New rows leave it
    # NULL and use seed_module_specializations. Dropped by a later cleanup migration.
    specialization: Mapped[str | None] = mapped_column(String(60))
    credits: Mapped[int] = mapped_column(SmallInteger)
    difficulty: Mapped[int] = mapped_column(SmallInteger)
    exam_type: Mapped[str] = mapped_column(String(20))
    duration_minutes: Mapped[int] = mapped_column(SmallInteger)
    reading_time_minutes: Mapped[int] = mapped_column(SmallInteger)
    # Source flag (Extra_Reading_Time = Yes/No); minutes above are an assumption.
    has_extra_reading_time: Mapped[bool] = mapped_column(Boolean, server_default=false())


class SeedModuleSpecialization(Base):
    """Every specialization (programme) a module belongs to."""

    __tablename__ = "seed_module_specializations"
    __table_args__ = (Index(None, "specialization"),)

    module_code: Mapped[str] = mapped_column(
        ForeignKey("seed_modules.module_code", ondelete="RESTRICT"), primary_key=True
    )
    specialization: Mapped[str] = mapped_column(String(30), primary_key=True)


class SeedStudent(Base):
    __tablename__ = "seed_students"
    __table_args__ = (
        CheckConstraint("academic_year BETWEEN 1 AND 4", name="academic_year_range"),
    )

    student_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    academic_year: Mapped[int] = mapped_column(SmallInteger)
    specialization: Mapped[str] = mapped_column(String(60))


class SeedRegistration(Base):
    __tablename__ = "seed_registrations"
    __table_args__ = (
        CheckConstraint(_in("registration_type", REGISTRATION_TYPES), name="registration_type_valid"),
        # The primary key (student_id, module_code) already indexes student_id;
        # this index serves "who sits module X?" lookups.
        Index(None, "module_code"),
    )

    student_id: Mapped[str] = mapped_column(
        ForeignKey("seed_students.student_id", ondelete="RESTRICT"), primary_key=True
    )
    module_code: Mapped[str] = mapped_column(
        ForeignKey("seed_modules.module_code", ondelete="RESTRICT"), primary_key=True
    )
    registration_type: Mapped[str] = mapped_column(String(10))


class SeedVenue(Base):
    __tablename__ = "seed_venues"
    __table_args__ = (
        CheckConstraint(_in("venue_type", VENUE_TYPES), name="venue_type_valid"),
        CheckConstraint("normal_capacity > 0", name="normal_capacity_positive"),
        CheckConstraint(
            "exam_capacity > 0 AND exam_capacity <= normal_capacity", name="exam_capacity_range"
        ),
        # The fixed 50% check (0004) was removed by 0005: the usable share now
        # comes from examinations.exam_seating_rules (see app.seating).
        UniqueConstraint("venue_name"),
    )

    # Campus venue code exactly as supplied (e.g. "F1301+F1302", "Robotics Lab").
    venue_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    venue_name: Mapped[str] = mapped_column(String(80))
    venue_type: Mapped[str] = mapped_column(String(20))
    building: Mapped[str] = mapped_column(String(8))
    floor: Mapped[int] = mapped_column(SmallInteger)
    # Full (normal) seating capacity, never reduced.
    normal_capacity: Mapped[int] = mapped_column(SmallInteger)
    # Legacy seed value (always floor(full x 0.5)), kept for compatibility. The
    # API and the scheduler use the effective capacity from the seating rules.
    exam_capacity: Mapped[int] = mapped_column(SmallInteger)


class SeedVenueBlock(Base):
    """A period when a venue cannot be used for examinations."""

    __tablename__ = "seed_venue_blocks"
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="time_range_valid"),
        CheckConstraint(_in("source", BLOCK_SOURCES), name="source_valid"),
        # Natural key: the seed script matches rows on it, so reruns never duplicate.
        # It also indexes (source, venue_id, block_date) lookups.
        UniqueConstraint("source", "venue_id", "block_date", "start_time"),
        # A synced booking (e.g. a Teaching Timetable session) appears at most once.
        UniqueConstraint("source", "source_ref"),
    )

    block_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    venue_id: Mapped[str] = mapped_column(ForeignKey("seed_venues.venue_id", ondelete="RESTRICT"))
    block_date: Mapped[dt.date] = mapped_column(Date)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)
    source: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(String(120))
    # ID of the booking in the system that owns it; NULL for seed/simulated rows.
    source_ref: Mapped[str | None] = mapped_column(String(64))


class SeedExamPeriod(Base):
    __tablename__ = "seed_exam_periods"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="date_range_valid"),
        CheckConstraint("day_end_time > day_start_time", name="day_hours_valid"),
    )

    period_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_name: Mapped[str] = mapped_column(String(120))
    academic_year_label: Mapped[str] = mapped_column(String(16))
    semester: Mapped[int] = mapped_column(SmallInteger)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    day_start_time: Mapped[dt.time] = mapped_column(Time)
    day_end_time: Mapped[dt.time] = mapped_column(Time)
    include_saturdays: Mapped[bool] = mapped_column(Boolean)
    include_sundays: Mapped[bool] = mapped_column(Boolean)


class SeedExamPeriodExclusion(Base):
    """A date inside an examination period on which no exam may be held."""

    __tablename__ = "seed_exam_period_exclusions"

    period_code: Mapped[str] = mapped_column(
        ForeignKey("seed_exam_periods.period_code", ondelete="RESTRICT"), primary_key=True
    )
    excluded_date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    reason: Mapped[str] = mapped_column(String(120))


SEED_MODELS = (
    SeedModule,
    SeedModuleSpecialization,
    SeedStudent,
    SeedRegistration,
    SeedVenue,
    SeedVenueBlock,
    SeedExamPeriod,
    SeedExamPeriodExclusion,
)
