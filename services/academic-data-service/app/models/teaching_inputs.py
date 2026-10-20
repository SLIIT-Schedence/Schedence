"""Academic-owned source records used by Teaching, not solver persistence.

Source-row identity is ingestion identity, never an authoritative offering ID.
PP1 allocation and classification tables deliberately remain separate from
university master facts. Existing staff and appointment tables are unchanged.
"""

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKeyConstraint,
    Integer, String, Table, Text, UniqueConstraint, Uuid, func)
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


def owned(name, *columns, checks=(), references=()):
    return Table(name, Base.metadata,
        Column("id", Uuid, primary_key=True),
        Column("institution_id", Uuid, nullable=False),
        Column("record_key", Text, nullable=False),
        *columns,
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        UniqueConstraint("institution_id", "id"),
        UniqueConstraint("institution_id", "record_key"),
        *[CheckConstraint(expression, name=label) for label, expression in checks],
        *references)


def ref(column, table, target="id"):
    return ForeignKeyConstraint(["institution_id", column],
        [f"academic.{table}.institution_id", f"academic.{table}.{target}"],
        name=f"fk_{column}_{table}", ondelete="RESTRICT")


def source():
    return Column("source_id", Uuid, nullable=False)


def payload():
    return Column("source_values", JSONB, nullable=False)


source_datasets = owned("source_datasets",
    Column("filename", Text, nullable=False), Column("sha256", String(64), nullable=False),
    Column("provenance_kind", String(40), nullable=False),
    Column("provenance", JSONB, nullable=False),
    checks=(("checksum", "sha256 ~ '^[0-9a-f]{64}$'"),))

departments = owned("departments", Column("name", Text, nullable=False), source(), payload(),
    references=(ref("source_id", "source_datasets"),))

staff_teaching_profiles = owned("staff_teaching_profiles",
    Column("staff_number", String(50), nullable=False),
    Column("staff_type", String(20), nullable=False),
    Column("source_position", Text, nullable=False), Column("department_id", Uuid),
    source(), Column("source_row", Integer, nullable=False), payload(),
    checks=(("staff_type", "staff_type IN ('ACADEMIC','INSTRUCTOR')"),),
    references=(ref("staff_number", "staff", "staff_number"), ref("department_id", "departments"),
                ref("source_id", "source_datasets")))

specializations = owned("specializations", Column("code", String(60), nullable=False),
    Column("name", Text, nullable=False), source(), payload(),
    references=(ref("source_id", "source_datasets"),))

modules = owned("modules", Column("module_code", String(60), nullable=False),
    Column("module_name", Text, nullable=False), Column("academic_year", Integer, nullable=False),
    Column("semester", Integer, nullable=False), Column("degree_type", Text, nullable=False),
    Column("lecture_minutes", Integer, nullable=False), Column("tutorial_minutes", Integer, nullable=False),
    Column("workshop_minutes", Integer, nullable=False), Column("lab_minutes", Integer, nullable=False),
    source(), Column("worksheet", Text, nullable=False), Column("source_row", Integer, nullable=False), payload(),
    checks=(("year", "academic_year BETWEEN 1 AND 4"), ("semester", "semester BETWEEN 1 AND 2"),
            ("hours", "lecture_minutes >= 0 AND tutorial_minutes >= 0 AND workshop_minutes >= 0 AND lab_minutes >= 0")),
    references=(ref("source_id", "source_datasets"),))

module_specializations = owned("module_specializations",
    Column("module_id", Uuid, nullable=False), Column("specialization_id", Uuid, nullable=False),
    references=(ref("module_id", "modules"), ref("specialization_id", "specializations")))

module_teaching_teams = owned("module_teaching_teams",
    Column("module_id", Uuid, nullable=False), Column("staff_number", String(50), nullable=False),
    Column("source_slot", String(60), nullable=False), Column("role", String(20), nullable=False),
    payload(), checks=(("role", "role IN ('LIC','CO_LIC','TEAM_MEMBER')"),),
    references=(ref("module_id", "modules"), ref("staff_number", "staff", "staff_number")))

lecturer_preferences = owned("lecturer_preferences",
    Column("staff_number", String(50), nullable=False), source(),
    Column("worksheet", Text, nullable=False), Column("source_row", Integer, nullable=False), payload(),
    references=(ref("staff_number", "staff", "staff_number"), ref("source_id", "source_datasets")))

cohort_totals = owned("cohort_totals", Column("cohort_reference", Text, nullable=False),
    Column("academic_year", Integer, nullable=False), Column("semester", Integer, nullable=False),
    Column("stream", String(10), nullable=False), Column("student_count", Integer, nullable=False),
    source(), payload(), checks=(("year", "academic_year BETWEEN 1 AND 4"),
        ("semester", "semester BETWEEN 1 AND 2"), ("stream", "stream IN ('WEEKDAY','WEEKEND')"),
        ("count", "student_count > 0")), references=(ref("source_id", "source_datasets"),))

pp1_specialization_allocations = owned("pp1_specialization_allocations",
    Column("cohort_total_id", Uuid, nullable=False), Column("specialization_id", Uuid, nullable=False),
    Column("local_cohort_reference", Text, nullable=False), Column("student_count", Integer, nullable=False),
    source(), payload(), checks=(("count", "student_count > 0"),),
    references=(ref("source_id", "source_datasets"), ref("cohort_total_id", "cohort_totals"),
                ref("specialization_id", "specializations")))

pp1_student_groups = owned("pp1_student_groups",
    Column("allocation_id", Uuid, nullable=False), Column("plan_reference", Text, nullable=False),
    Column("local_group_reference", Text, nullable=False), Column("parent_id", Uuid),
    Column("student_count", Integer, nullable=False), payload(),
    checks=(("count", "student_count > 0"), ("self_parent", "parent_id IS NULL OR parent_id <> id")),
    references=(ref("allocation_id", "pp1_specialization_allocations"), ref("parent_id", "pp1_student_groups")))

module_classification_reviews = owned("module_classification_reviews",
    Column("module_id", Uuid, nullable=False), source(),
    Column("academic_classification", String(40), nullable=False),
    Column("pp1_test_candidate", Boolean, nullable=False), payload(),
    checks=(("classification", "academic_classification IN ('UNCLASSIFIED','COMPULSORY','ELECTIVE')"),),
    references=(ref("module_id", "modules"), ref("source_id", "source_datasets")))

for table, columns in (
    (departments, ("name",)), (specializations, ("code",)),
    (modules, ("source_id", "worksheet", "source_row")),
    (module_specializations, ("module_id", "specialization_id")),
    (module_teaching_teams, ("module_id", "source_slot")),
    (lecturer_preferences, ("source_id", "staff_number")),
    (cohort_totals, ("source_id", "academic_year", "semester", "stream")),
    (pp1_specialization_allocations, ("source_id", "cohort_total_id", "specialization_id")),
    (pp1_student_groups, ("plan_reference", "local_group_reference")),
    (module_classification_reviews, ("source_id", "module_id")),
):
    table.append_constraint(UniqueConstraint("institution_id", *columns))

TEACHING_INPUT_TABLES = tuple(table for table in Base.metadata.tables.values()
    if table.name in {"source_datasets", "departments", "staff_teaching_profiles", "specializations", "modules",
        "module_specializations", "module_teaching_teams", "lecturer_preferences", "cohort_totals",
        "pp1_specialization_allocations", "pp1_student_groups", "module_classification_reviews"})
