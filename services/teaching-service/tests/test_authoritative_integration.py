"""Authoritative boundary tests use invented exports, never local campus records."""

from copy import deepcopy
from datetime import date
from pathlib import Path
import runpy
from uuid import UUID

import pytest
from pydantic import ValidationError

from schedence_teaching.integration import (
    AcademicSourceTable, InputEvidence, ReadinessStatus, SchedulingSubmission,
    SourceAttestation, SourceOwner, adapt_academic_staff, adapt_academic_tables, adapt_administrative_resolution,
    assess_readiness, run_scheduling,
)
from schedence_teaching.solver import SolverStatus


INSTITUTION = UUID("00000000-0000-4000-8000-000000000001")
OTHER = UUID("00000000-0000-4000-8000-000000000002")
PREFIX = ("combination_input", "teaching_input")


@pytest.fixture(scope="module")
def complete_template():
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "authoritative_input_fixture.py"))
    return fixture["build_submission"]()


@pytest.fixture
def submission(complete_template):
    return deepcopy(complete_template)


def assess(submission):
    return assess_readiness(submission, expected_institution_id=INSTITUTION, actual_campus=False)


def errors(report):
    return [row for row in report.diagnostics if row.status != ReadinessStatus.CONFIRMED]


def at(payload, path):
    for part in path:
        payload = payload[part]
    return payload


def test_complete_input_preparation_and_preserved_sources(submission):
    before = deepcopy(submission)
    assessment = assess(submission)
    assert assessment.report.ready_for_generation, assessment.report.to_dict()
    assert not assessment.report.ready_for_actual_campus
    assert assessment.normalized.teaching_input == assessment.preparation.prepared.input_snapshot
    assert len(assessment.preparation.prepared.sessions) == 7
    contexts = assessment.preparation.solver_inputs
    assert len(contexts["combination_input"].candidates) == 1
    assert {row.stream.value for row in assessment.preparation.prepared.sessions} == {"WEEKDAY", "WEEKEND"}
    assert submission == before
    assert any(row.status == "CONFIRMED" and row.path == ("venue_input",) for row in assessment.report.diagnostics)


@pytest.mark.parametrize("path", [
    PREFIX + ("module_offerings", 0, "offering_id"),
    PREFIX + ("student_groups", 0, "group_id"),
    PREFIX + ("student_groups", 0, "student_count"),
    PREFIX + ("session_requirements", 0, "duration_minutes"),
    PREFIX + ("session_requirements", 0, "sessions_per_week"),
    ("academic_term_reference",), ("institution_id",), ("calendar",), ("venue_input",), ("staff_input",),
])
def test_missing_required_values(submission, path):
    del at(submission.inputs, path[:-1])[path[-1]]
    assessment = assess(submission)
    assert not assessment.report.ready_for_generation
    assert any(row.status == "MISSING" and row.path[:len(path)] == path for row in assessment.report.diagnostics)


@pytest.mark.parametrize("field,value", [("student_count", 0), ("student_count", -1),
    ("student_count", True), ("academic_year", 5), ("semester", 3), ("stream", "EVENING")])
def test_invalid_group_details(submission, field, value):
    submission.inputs["combination_input"]["teaching_input"]["student_groups"][0][field] = value
    assert any(row.status == "INVALID" for row in assess(submission).report.diagnostics)


@pytest.mark.parametrize("field,value", [("duration_minutes", 0), ("duration_minutes", "60"), ("sessions_per_week", -2)])
def test_invalid_demand(submission, field, value):
    submission.inputs["combination_input"]["teaching_input"]["session_requirements"][0][field] = value
    assert not assess(submission).report.ready_for_generation


def test_module_code_conflict_is_not_identity_conflict(submission):
    offerings = submission.inputs["combination_input"]["teaching_input"]["module_offerings"]
    offerings[0]["module_code"] = offerings[1]["module_code"] = "IT2100"
    assessment = assess(submission)
    assert assessment.report.ready_for_generation, assessment.report.to_dict()
    assert len(assessment.normalized.teaching_input.module_offerings) == 3


def test_duplicate_offering_is_ambiguous_and_row_located(submission):
    offerings = submission.inputs["combination_input"]["teaching_input"]["module_offerings"]
    offerings.append(deepcopy(offerings[0]))
    report = assess(submission).report
    assert any(row.status == "AMBIGUOUS" and row.path == PREFIX + ("module_offerings", 3, "offering_id")
        for row in report.diagnostics)


@pytest.mark.parametrize("collection,field", [("teaching_team_members", "staff_id"),
    ("teaching_team_members", "offering_id"), ("session_requirements", "group_id"),
    ("student_groups", "parent_group_id")])
def test_reference_errors_are_located(submission, collection, field):
    submission.inputs["combination_input"]["teaching_input"][collection][0][field] = "MISSING-SYNTHETIC-ID"
    assert any(row.code == "missing_reference" and row.path == PREFIX + (collection, 0, field)
        for row in assess(submission).report.diagnostics)


def test_instructor_lic_role_has_actionable_location(submission):
    teaching = submission.inputs["combination_input"]["teaching_input"]
    row = next(index for index, member in enumerate(teaching["teaching_team_members"]) if member["staff_id"].endswith("-I"))
    teaching["teaching_team_members"][row]["role"] = "LIC"
    assert any(issue.code == "invalid_academic_role" and issue.path == PREFIX + ("teaching_team_members", row, "role")
        for issue in assess(submission).report.diagnostics)


def test_readiness_missing_offering_retains_excel_column_and_row(submission):
    del submission.inputs["combination_input"]["teaching_input"]["module_offerings"][0]["offering_id"]
    evidence = next(row for row in submission.evidence if row.path == ("combination_input",))
    submission.evidence.append(InputEvidence(**{**evidence.model_dump(),
        "path": PREFIX + ("module_offerings", 0),
        "location": dict(workbook="synthetic.xlsx", worksheet="Offerings", row=17),
        "columns": dict(offering_id="Stable Offering Key")}))
    issue = next(row for row in assess(submission).report.diagnostics if row.path == PREFIX + ("module_offerings", 0, "offering_id"))
    assert (issue.status, issue.row, issue.column) == ("MISSING", 17, "Stable Offering Key")


@pytest.mark.parametrize("path", [("staff_input", "availability"), ("venue_input", "calendars"),
    ("combination_input", "group_specializations"), ("academic_staff_references",)])
def test_empty_source_collections_block(submission, path):
    container = at(submission.inputs, path[:-1])
    container[path[-1]] = type(container[path[-1]])()
    assert not assess(submission).report.ready_for_generation


@pytest.mark.parametrize("field", ["applicable_dates", "applicable_streams", "applicability_reference"])
def test_missing_lunch_scope(submission, field):
    submission.inputs["student_lunch_policy"][field] = None
    assert any(row.status == "MISSING" and row.path == ("student_lunch_policy",) for row in assess(submission).report.diagnostics)


@pytest.mark.parametrize("path", [("work_period_input", "research_reservations"),
    ("venue_input", "combined_venues"), ("combination_input", "policy", "allow_combination"),
    ("staff_input", "workload_input", "attendance")])
def test_defaults_cannot_hide_absent_authoritative_declarations(submission, path):
    del at(submission.inputs, path[:-1])[path[-1]]
    assert any(row.path == path and row.status == "MISSING" for row in assess(submission).report.diagnostics)


def test_unknown_cross_stream_population_blocks(submission):
    combination = submission.inputs["combination_input"]
    weekend_id = combination["teaching_input"]["student_groups"][-1]["group_id"]
    combination["population_evidence"] = [row for row in combination["population_evidence"] if weekend_id not in row["group_ids"]]
    report = assess(submission).report
    assert not report.ready_for_generation
    assert any(row.path == ("combination_input", "population_evidence") for row in errors(report))


def test_unverified_disjointness_never_reaches_solver(submission, monkeypatch):
    original = next(row for row in submission.evidence if row.path == ("combination_input",))
    submission.evidence.append(InputEvidence(**{**original.model_dump(),
        "path": ("combination_input", "population_evidence", 0), "status": "UNVERIFIED"}))
    monkeypatch.setattr("schedence_teaching.integration.service.solve_timetable", lambda *a, **k: pytest.fail("Solver was called"))
    output = run_scheduling(submission, expected_institution_id=INSTITUTION, actual_campus=False)
    assert output.timetable is None
    assert any(row.status == "UNVERIFIED" and row.code == "source_confirmation" for row in output.readiness.diagnostics)


@pytest.mark.parametrize("change", ["owner", "tenant", "unverified", "expired", "missing_reference", "synthetic"])
def test_evidence_scope_and_confirmation(submission, change):
    source = submission.evidence[0]
    if change == "owner":
        source.owner = SourceOwner.IDENTITY
    elif change == "tenant":
        source.institution_id = OTHER
    elif change == "unverified":
        source.status = ReadinessStatus.UNVERIFIED
    elif change == "expired":
        source.effective_until = date(2026, 10, 24)
    elif change == "missing_reference":
        source.confirmation_reference = None
    else:
        source.synthetic = False
    assert not assess(submission).report.ready_for_generation


def test_duplicate_evidence_is_ambiguous(submission):
    submission.evidence.append(deepcopy(submission.evidence[0]))
    assert any(row.status == "AMBIGUOUS" and row.code == "ambiguous_evidence" for row in assess(submission).report.diagnostics)


def test_actual_request_cannot_use_payload_synthetic_mode(submission, monkeypatch):
    monkeypatch.setattr("schedence_teaching.integration.service.solve_timetable", lambda *a, **k: pytest.fail("Synthetic fallback"))
    result = run_scheduling(submission, expected_institution_id=INSTITUTION)
    assert result.timetable is None and not result.readiness.ready_for_actual_campus
    assert any(row.code == "input_mode" for row in result.readiness.diagnostics)


def test_trusted_tenant_cannot_be_overridden(submission):
    result = assess_readiness(submission, expected_institution_id=OTHER, actual_campus=False)
    assert any(row.code == "institution_mismatch" for row in result.report.diagnostics)


@pytest.mark.parametrize("value", [None, [], {"teaching_input": None}, {"teaching_input": []}])
def test_corrupted_outer_academic_payload_reports_without_crashing(submission, value):
    submission.inputs["combination_input"] = value
    assert not assess(submission).report.ready_for_generation


def test_real_generation_missing_all_inputs_is_blocked():
    result = run_scheduling(SchedulingSubmission(inputs={}, evidence=[]), expected_institution_id=INSTITUTION)
    assert result.timetable is None and not result.independently_validated
    assert all(row.status == "MISSING" for row in result.readiness.diagnostics)


def test_readiness_report_never_echoes_private_values(submission):
    submission.inputs["combination_input"]["teaching_input"]["staff_members"][0]["staff_name"] = {"private": "DO-NOT-PRINT"}
    assert "DO-NOT-PRINT" not in str(assess(submission).report.to_dict())


def test_missing_block_declaration_is_not_unrestricted_availability(submission):
    del submission.inputs["staff_input"]["availability"][0]["blocked_periods"]
    assert not assess(submission).report.ready_for_generation


def test_existing_calendar_validation_returns_context_location(submission):
    submission.inputs["staff_input"]["day_dates"]["MONDAY"] = date(2026, 10, 18)
    assessment = assess(submission)
    assert any(row.status == "INVALID" and row.path == ("staff_input",) for row in assessment.report.diagnostics)


def test_duplicate_staff_uuid_is_ambiguous(submission):
    refs = submission.inputs["academic_staff_references"]
    keys = list(refs)
    refs[keys[0]] = refs[keys[1]]
    assert any(row.status == "AMBIGUOUS" and row.code == "staff_crosswalk" for row in assess(submission).report.diagnostics)


@pytest.mark.parametrize("collection", ["administrative_periods", "research_reservations"])
def test_other_institution_work_period_is_never_accepted(submission, collection):
    submission.inputs["work_period_input"][collection][0]["institution_id"] = str(OTHER)
    assert any(row.code == "institution_mismatch" and row.path == ("work_period_input", collection, 0, "institution_id")
        for row in assess(submission).report.diagnostics)


def test_empty_staff_institution_map_is_not_source_authorization(submission):
    submission.inputs["work_period_input"]["staff_institutions"] = {}
    assert any(row.status == "MISSING" and row.path == ("work_period_input", "staff_institutions")
        for row in assess(submission).report.diagnostics)


def test_complete_native_run_preserves_coverage_and_policies(submission):
    before = deepcopy(submission)
    output = run_scheduling(submission, expected_institution_id=INSTITUTION, actual_campus=False)
    assert output.independently_validated
    assert output.timetable.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    coverage = [row.occurrence_id for delivery in output.timetable.schedule for row in delivery.covered_sessions]
    assert len(coverage) == len(set(coverage)) == 7
    assert len(output.timetable.schedule) == 6
    assert output.timetable.validation.lecturer_contact_minutes == 315
    assert output.timetable.student_lunch_enabled
    assert output.timetable.work_period_enabled
    assert submission == before


def source():
    return SourceAttestation(owner="ACADEMIC_DATA_SERVICE", institution_id=INSTITUTION,
        status="CONFIRMED", provenance=dict(source="synthetic-export", reference="synthetic:source"),
        confirmation_reference="synthetic:confirmed", effective_from=date(2026, 10, 19),
        effective_until=date(2026, 10, 25), synthetic=True,
        location=dict(workbook="synthetic.xlsx", worksheet="Export", row=42))


def test_explicit_adapter_preserves_ids_conflicting_codes_and_provenance(input_payload):
    tables = [AcademicSourceTable(table=name, columns={key: f"Export_{key}" for key in rows[0]},
        rows=[dict(values={f"Export_{key}": value for key, value in row.items()}, source=source()) for row in rows])
        for name, rows in input_payload.items()]
    # Parent IDs are optional and can differ among rows, so explicitly map them.
    group_table = next(row for row in tables if row.table == "student_groups")
    group_table.columns["parent_group_id"] = "Export_parent_group_id"
    before = deepcopy(tables)
    result = adapt_academic_tables(tables)
    assert not result.diagnostics
    assert result.teaching_input.staff_members[0].staff_id == "LEC001"
    assert len(result.teaching_input.module_offerings) == 2
    assert {row.module_code for row in result.teaching_input.module_offerings} == {"IT2100"}
    assert result.evidence[0].location.row == 42
    assert result.evidence[0].provenance.reference == "synthetic:source"
    assert tables == before
    assert result.source_tables[0].rows[0].values == tables[0].rows[0].values


def test_adapter_missing_column_is_actionable_and_does_not_drop_row():
    table = AcademicSourceTable(table="staff_members", columns=dict(staff_id="Stable ID", staff_name="Name",
        position="Rank", staff_type="Kind"), rows=[dict(values={"Name": "Private Example", "Rank": "Lecturer", "Kind": "ACADEMIC"}, source=source())])
    result = adapt_academic_tables([table])
    issue = next(row for row in result.diagnostics if row.path[-1] == "staff_id")
    assert (issue.status, issue.workbook, issue.worksheet, issue.row, issue.column) == (
        "MISSING", "synthetic.xlsx", "Export", 42, "Stable ID")
    assert len(result.payload["staff_members"]) == 1
    assert result.teaching_input is None
    assert "Private Example" not in str(result.diagnostics)


@pytest.mark.parametrize("columns", [dict(unknown_field="ID"), dict(staff_id="ID", staff_name="ID")])
def test_adapter_rejects_invalid_or_ambiguous_mapping(columns):
    result = adapt_academic_tables([AcademicSourceTable(table="staff_members", columns=columns, rows=[])])
    assert any(row.code == "invalid_mapping" for row in result.diagnostics)
    assert result.teaching_input is None


def test_duplicate_export_is_not_silently_merged():
    table = AcademicSourceTable(table="staff_members", columns=dict(staff_id="ID"), rows=[])
    assert any(row.status == "AMBIGUOUS" for row in adapt_academic_tables([table, table]).diagnostics)


def staff_export():
    return dict(id=str(OTHER), institution_id=str(INSTITUTION), staff_number="LEC001",
        full_name="Synthetic Staff", academic_rank="Lecturer", employment_status="ACTIVE")


def test_ads_staff_uuid_is_crosswalk_not_replacement_id():
    result = adapt_academic_staff(staff_export(), expected_institution_id=INSTITUTION, staff_type="ACADEMIC")
    assert result.staff_member.staff_id == "LEC001"
    assert result.academic_staff_id == OTHER
    assert result.staff_member.department is None


@pytest.mark.parametrize("change", ["institution", "inactive", "rank"])
def test_ads_staff_rejects_missing_or_wrong_authority(change):
    payload = staff_export()
    payload[{"institution": "institution_id", "inactive": "employment_status", "rank": "academic_rank"}[change]] = (
        str(OTHER) if change == "institution" else "INACTIVE" if change == "inactive" else None)
    with pytest.raises((ValueError, ValidationError)):
        adapt_academic_staff(payload, expected_institution_id=INSTITUTION, staff_type="ACADEMIC")


def administrative_export():
    return dict(staff_id=str(OTHER), institution_id=str(INSTITUTION), as_of="2026-10-19",
        appointments=[dict(appointment_id="00000000-0000-4000-8000-000000000003")],
        aggregate=dict(status="COMPUTED", rule="SINGLE_APPOINTMENT", total_admin_hours_per_week=4))


def adapt_resolution(payload, **kwargs):
    return adapt_administrative_resolution(payload, expected_institution_id=INSTITUTION,
        academic_staff_references=kwargs.get("crosswalk", {"LEC001": OTHER}), source=source(),
        resolution_reference="synthetic:ADS-response")


def test_admin_resolution_preserves_aggregate_and_daily_scope():
    payload = administrative_export()
    before = deepcopy(payload)
    result = adapt_resolution(payload)
    assert result.allocation_minutes == 240 and result.staff_id == "LEC001"
    assert result.effective_from == result.effective_until == date(2026, 10, 19)
    assert result.rule == "SINGLE_APPOINTMENT"
    assert payload == before


def test_unresolved_admin_aggregate_is_not_recomputed():
    payload = administrative_export()
    payload["aggregate"] = dict(status="NOT_COMPUTED", rule=None, total_admin_hours_per_week=None)
    result = adapt_resolution(payload)
    assert result.status == "NOT_COMPUTED" and result.allocation_minutes is None


@pytest.mark.parametrize("hours", [True, -1, "NaN", "Infinity", 0.001])
def test_admin_conversion_never_rounds_or_accepts_invalid_values(hours):
    payload = administrative_export()
    payload["aggregate"]["total_admin_hours_per_week"] = hours
    with pytest.raises(ValueError):
        adapt_resolution(payload)


@pytest.mark.parametrize("crosswalk", [{}, {"LEC001": OTHER, "LEC002": OTHER}])
def test_admin_crosswalk_requires_unique_existing_staff(crosswalk):
    with pytest.raises(ValueError):
        adapt_resolution(administrative_export(), crosswalk=crosswalk)
