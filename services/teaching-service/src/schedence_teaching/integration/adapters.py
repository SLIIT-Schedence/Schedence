"""Explicit normalized-export mappings; source rows are never offering IDs."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError

from schedence_teaching.schemas import (
    ModuleOffering, SessionRequirement, StaffMember, StaffType, StudentGroup,
    TeachingTeamMember, TeachingTimetableInput,
)
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload import AdministrativePolicyResolution

from .contracts import InputEvidence, SchedulingSubmission, SourceAttestation, SourceOwner
from .diagnostics import ReadinessDiagnostic, diagnostic


TABLE_MODELS = dict(staff_members=StaffMember, module_offerings=ModuleOffering,
    teaching_team_members=TeachingTeamMember, student_groups=StudentGroup,
    session_requirements=SessionRequirement)


class AcademicSourceRow(InputContract):
    values: dict[str, Any]
    source: SourceAttestation


class AcademicSourceTable(InputContract):
    table: Literal["staff_members", "module_offerings", "teaching_team_members",
                   "student_groups", "session_requirements"]
    # Canonical existing-contract field -> exact exported column name.
    columns: dict[RequiredText, RequiredText]
    rows: list[AcademicSourceRow]


@dataclass(frozen=True)
class AcademicAdaptation:
    payload: dict
    evidence: tuple[InputEvidence, ...]
    diagnostics: tuple[ReadinessDiagnostic, ...]
    teaching_input: TeachingTimetableInput | None
    source_tables: tuple[AcademicSourceTable, ...]


def adapt_academic_tables(tables: list[AcademicSourceTable]) -> AcademicAdaptation:
    """Map supplied exports without coercing, filling, deduplicating or dropping rows.

    The payload remains available even when invalid. Callers must not pass it to
    scheduling until the readiness gate succeeds. This is not a workbook-layout
    guesser: existing wide/venue importers continue to handle those source files.
    """
    tables = deepcopy(tables)
    payload, evidence, issues = {}, [], []
    prefix = ("combination_input", "teaching_input")
    for table in tables:
        path = prefix + (table.table,)
        if table.table in payload:
            issues.append(ReadinessDiagnostic("AMBIGUOUS", "duplicate_source_table", path,
                "Supply one explicitly mapped export for each academic collection."))
            continue
        model = TABLE_MODELS[table.table]
        if set(table.columns) - model.model_fields.keys() or len(set(table.columns.values())) != len(table.columns):
            issues.append(ReadinessDiagnostic("INVALID", "invalid_mapping", path,
                "Map distinct exported columns to existing contract field names only."))
        records = []
        for index, row in enumerate(table.rows):
            row_path = path + (index,)
            source = row.source.model_dump()
            source["columns"] = dict(table.columns)
            evidence.append(InputEvidence(**source, path=row_path))
            record = {field: deepcopy(row.values[column]) for field, column in table.columns.items()
                      if column in row.values}
            records.append(record)
            submission = SchedulingSubmission(inputs={}, evidence=list(evidence))
            if row.source.owner != SourceOwner.ACADEMIC:
                issues.append(diagnostic(submission, "INVALID", "source_owner", row_path,
                    "Academic records must come from the Academic Data Service."))
            try:
                model.model_validate(record)
            except ValidationError as error:
                for item in error.errors(include_input=False, include_context=False):
                    location = row_path + item["loc"]
                    missing = item["type"] == "missing"
                    issues.append(diagnostic(submission, "MISSING" if missing else "INVALID",
                        item["type"], location, "Supply the required mapped value." if missing else
                        "Correct this value to satisfy the existing teaching contract."))
        payload[table.table] = records
    submission = SchedulingSubmission(inputs={}, evidence=evidence)
    teaching_input = None
    try:
        teaching_input = TeachingTimetableInput.model_validate(payload)
    except ValidationError as error:
        # Nested field failures were already located above. Container validators
        # additionally detect cross-record identity, hierarchy and role failures.
        for item in error.errors(include_input=False, include_context=False):
            if len(item["loc"]) < 2:
                issues.append(diagnostic(submission, "MISSING" if item["type"] == "missing" else "INVALID",
                    item["type"], prefix + item["loc"],
                    "Supply the missing collection or correct its references and unique identifiers."))
    if issues:
        teaching_input = None
    return AcademicAdaptation(payload, tuple(evidence), tuple(issues), teaching_input, tuple(tables))


@dataclass(frozen=True)
class AdaptedAcademicStaff:
    staff_member: StaffMember
    academic_staff_id: UUID
    institution_id: UUID


def adapt_academic_staff(record: dict, *, expected_institution_id: UUID,
        staff_type: StaffType, department: str | None = None) -> AdaptedAcademicStaff:
    """Current ADS StaffOut uses UUID id and a separate preserved staff_number.

    ADS has no typed staff_type/department contract yet: both need explicit
    authoritative supplemental data, never login roles or ID-prefix inference.
    """
    institution = UUID(str(record["institution_id"]))
    if institution != UUID(str(expected_institution_id)):
        raise ValueError("Academic staff institution differs from the requested institution.")
    if record["employment_status"] != "ACTIVE":
        raise ValueError("Only explicitly active academic staff can enter this scheduling export.")
    member = StaffMember(staff_id=record["staff_number"], staff_name=record["full_name"],
        position=record["academic_rank"], staff_type=staff_type, department=department)
    return AdaptedAcademicStaff(member, UUID(str(record["id"])), institution)


def adapt_administrative_resolution(record: dict, *, expected_institution_id: UUID,
        academic_staff_references: dict[str, UUID], source: SourceAttestation,
        resolution_reference: str) -> AdministrativePolicyResolution:
    """Map the ADS aggregate without recalculating its appointment policy.

    ADS AdministrativeWorkloadOut is an as-of-date response, not week-wide
    authorization. Its applicability is restricted to that date. Obtain explicit
    upstream dated appointments/resolutions before using it for an entire week.
    This mapping creates neither calendar reservations nor completed work.
    """
    institution = UUID(str(expected_institution_id))
    if (UUID(str(record["institution_id"])) != institution or source.institution_id != institution
            or source.owner != SourceOwner.ACADEMIC):
        raise ValueError("Administrative resolution requires the same institution and Academic Data Service source.")
    staff_uuid = UUID(str(record["staff_id"]))
    staff_ids = [key for key, value in academic_staff_references.items() if UUID(str(value)) == staff_uuid]
    if len(staff_ids) != 1:
        raise ValueError("Administrative staff UUID requires one explicit preserved staff-ID cross-reference.")
    on_date = record["as_of"]
    if isinstance(on_date, str):
        on_date = date.fromisoformat(on_date)
    if type(on_date) is not date or not source.effective_from <= on_date <= source.effective_until:
        raise ValueError("Administrative as-of date must lie within source attestation validity.")
    aggregate = record["aggregate"]
    hours = aggregate["total_admin_hours_per_week"]
    minutes = None
    if hours is not None:
        try:
            amount = Decimal(str(hours)) * 60
        except InvalidOperation:
            raise ValueError("Administrative hours must convert exactly to whole minutes.") from None
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            raise ValueError("Administrative hours must convert exactly to nonnegative whole minutes.")
        minutes = int(amount)
    return AdministrativePolicyResolution(staff_id=staff_ids[0], institution_id=str(institution),
        appointment_ids=tuple(str(UUID(str(row["appointment_id"]))) for row in record["appointments"]),
        status=aggregate["status"], allocation_minutes=minutes, rule=aggregate["rule"],
        resolution_reference=resolution_reference, effective_from=on_date, effective_until=on_date,
        provenance=source.provenance)
