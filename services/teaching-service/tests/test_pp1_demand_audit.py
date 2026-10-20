"""Independent reconciliation, full cohort selection and honest private exports."""

from dataclasses import replace
import csv
import json

import pytest

from schedence_teaching.excel_import.staging import SourceLocation, SourceModuleRecord, WideImportResult
from schedence_teaching.pilot.demand_audit import audit_demand, export_demand_audit, _available_minutes
from schedence_teaching.pilot.scopes import select_complete_scope
from schedence_teaching.pilot.pp1_run import run_pp1
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver.evaluation import validate_evaluation_result

from test_pp1_allocations import small_batch, configuration


def source_for(batch, *, lecture=2, lab=2):
    source = WideImportResult(source_confirmation_reference="synthetic:confirmed-source",
        lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    source.staff_members = list(batch.prepared.input_snapshot.staff_members)
    for offering in batch.prepared.input_snapshot.module_offerings:
        location = batch.source_offerings[offering.offering_id]
        source.source_modules.append(SourceModuleRecord(SourceLocation(location["workbook"], location["worksheet"], location["row"]),
            dict(Lecture_Hours_Week=lecture, Tutorial_Hours_Week=0, Workshop_Hours_Week=0, Lab_Hours_Week=lab),
            contract_fields=offering.model_dump(exclude={"offering_id", "stream"})))
    return source


def changed(batch, mutate):
    raw = batch.prepared.input_snapshot.model_dump()
    mutate(raw)
    return replace(batch, prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(raw)))


def test_source_row_identity_preserves_conflicting_module_codes_and_all_audiences():
    batch = small_batch(lab=2)
    audit = audit_demand(batch, source_for(batch))
    assert audit["source_reconciliation_passed"]
    assert not audit["issues"]
    assert audit["source_module_rows"] == 2
    assert {row["source_row"] for row in audit["module_demand"]} == {2, 3}
    assert {row["module_code"] for row in audit["module_demand"]} == {"IT2100"}
    assert audit["occurrence_count"] == 6
    assert all(row["source_minutes"] == row["required_minutes"] == 240 for row in audit["leaf_attendance"])
    assert all(row["session_count"] == 2 for row in audit["leaf_attendance"])
    # One parent's lecture + own lab, never the sibling's lab.
    assert len(audit["leaf_contributions"]) == 8


@pytest.mark.parametrize("mode,code", [("missing", "MISSING_OR_DUPLICATE_REQUIREMENT"),
    ("duplicate", "MISSING_OR_DUPLICATE_REQUIREMENT"), ("split", "REQUIREMENT_SOURCE_MISMATCH"),
    ("shortened", "REQUIREMENT_SOURCE_MISMATCH"), ("omitted-binding", "OMITTED_SOURCE_BINDING"),
    ("altered-specialization", "ALTERED_SOURCE_MODULE")])
def test_importer_defects_prevent_calendar_infeasibility_claims(mode, code):
    batch = small_batch(lab=2)
    source = source_for(batch)
    def mutate(raw):
        if mode == "missing":raw["session_requirements"].pop(0)
        if mode == "duplicate":
            row = dict(raw["session_requirements"][0], requirement_id="synthetic:duplicate")
            raw["session_requirements"].append(row)
        if mode == "split":
            raw["session_requirements"][0].update(duration_minutes=60, sessions_per_week=2)
        if mode == "shortened":raw["session_requirements"][0]["duration_minutes"] = 60
        if mode == "omitted-binding":
            identity = raw["module_offerings"][0]["offering_id"]
            for name in ("module_offerings", "teaching_team_members", "session_requirements"):
                raw[name] = [row for row in raw[name] if row["offering_id"] != identity]
        if mode == "altered-specialization":raw["module_offerings"][0]["specialization"] = "SE"
    audit = audit_demand(changed(batch, mutate), source)
    assert not audit["source_reconciliation_passed"]
    assert code in {row["code"] for row in audit["issues"]}
    assert all(row["classification"] == "SOURCE_OR_EXPANSION_REVIEW_REQUIRED" for row in audit["leaf_attendance"])


@pytest.mark.parametrize("mode", ["missing", "duplicate", "duration", "identity"])
def test_corrupted_occurrences_are_detected_independently(mode):
    batch = small_batch(lab=2)
    sessions = list(batch.prepared.sessions)
    if mode == "missing":sessions.pop()
    if mode == "duplicate":sessions.append(sessions[0])
    if mode == "duration":sessions[0] = replace(sessions[0], duration_minutes=60)
    if mode == "identity":sessions[0] = replace(sessions[0], occurrence_id="corrupt")
    corrupted = replace(batch, prepared=replace(batch.prepared, sessions=tuple(sessions)))
    audit = audit_demand(corrupted, source_for(batch))
    assert any(row["code"] == "OCCURRENCE_EXPANSION_MISMATCH" for row in audit["issues"])


def test_identical_source_records_flagged_without_deleting_or_merging_them():
    batch = small_batch(lab=2)
    source = source_for(batch)
    source.source_modules[1].contract_fields = dict(source.source_modules[0].contract_fields)
    audit = audit_demand(batch, source)
    assert any(row["code"] == "DUPLICATE_SOURCE_MODULE_RECORD" for row in audit["issues"])
    assert len(batch.prepared.sessions) == 6


def test_necessary_capacity_bound_proves_shortage_only_after_source_reconciliation():
    raw = configuration().model_dump()
    raw["calendar"]["day_dates"] = {"MONDAY": "2026-10-19"}
    raw["solver_config"]["days"] = [dict(day="MONDAY", windows={"WEEKDAY": [dict(start_minute=690, end_minute=870)]})]
    config = type(configuration()).model_validate(raw)
    batch = small_batch(lab=2, config=config)
    before = batch.prepared.input_snapshot.model_dump()
    audit = audit_demand(batch, source_for(batch))
    assert audit["source_reconciliation_passed"]
    assert audit["overloaded_leaf_count"] == 4 and audit["overloaded_scope_count"] == 2
    assert all(row["required_hours"] == 4 and row["available_hours"] == 2 and row["minimum_additional_teaching_hours"] == 2
               and row["classification"] == "PROVEN_CALENDAR_INFEASIBLE" for row in audit["leaf_attendance"])
    assert batch.prepared.input_snapshot.model_dump() == before


@pytest.mark.parametrize("applicability", ["no-dates", "no-streams"])
def test_capacity_bound_respects_explicit_lunch_applicability(applicability):
    batch = small_batch(lab=2)
    policy = batch.solver_inputs["student_lunch_policy"].model_copy(deep=True)
    if applicability == "no-dates":policy.applicable_dates = set()
    if applicability == "no-streams":policy.applicable_streams = set()
    batch = replace(batch, solver_inputs={**batch.solver_inputs, "student_lunch_policy": policy})
    assert _available_minutes(batch, batch.plan.groups[0]) == 45 * 60


def test_audit_exports_headers_when_no_overloads_and_refuses_to_overwrite(tmp_path):
    batch = small_batch(lab=2)
    audit = audit_demand(batch, source_for(batch))
    directory = export_demand_audit(audit, tmp_path / "audit")
    with (directory / "weekend-overloads.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []
    assert json.loads((directory / "demand-audit.json").read_text(encoding="utf-8"))["source_reconciliation_passed"]
    assert "minimum_additional_teaching_hours" in (directory / "calendar-scenario-comparison.csv").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):export_demand_audit(audit, directory)


@pytest.mark.parametrize("scopes", [set(), {(1, 1, "WEEKDAY", "UNKNOWN")}, {(5, 1, "WEEKDAY", "IT")}])
def test_unknown_or_empty_scope_cannot_silently_drop_demand(scopes):
    with pytest.raises(ValueError, match="nonempty known"):
        select_complete_scope(small_batch(lab=2), scopes)


def test_complete_scope_keeps_all_parent_and_lab_requirements_without_mutating_original():
    batch = small_batch(lab=2)
    before = batch.prepared.input_snapshot.model_dump()
    scoped = select_complete_scope(batch, {(1, 1, "WEEKDAY", "IT")}, max_time_seconds=10.0)
    assert len(scoped.prepared.sessions) == 3
    assert {row.group_id for row in scoped.plan.groups} == {row.group_id for row in batch.plan.groups if batch.group_specializations[row.group_id] == "IT"}
    assert scoped.prepared.sessions == tuple(row for row in batch.prepared.sessions if row.group_id in scoped.group_specializations)
    assert scoped.metadata["out_of_scope_occurrences"] == 3
    assert scoped.metadata["original_global_occurrences"] == 6
    assert scoped.plan.summary()["total_students"] == 80
    assert len(scoped.solver_inputs["venue_input"].requirements) == 3
    assert scoped.solver_inputs["staff_input"] == batch.solver_inputs["staff_input"]
    assert scoped.config.days == batch.config.days
    assert scoped.config.max_time_seconds == 10.0 and batch.config.max_time_seconds == 5.0
    assert batch.prepared.input_snapshot.model_dump() == before


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan")])
def test_scope_budget_is_revalidated(seconds):
    with pytest.raises(ValueError):select_complete_scope(small_batch(), {(1, 1, "WEEKDAY", "IT")}, max_time_seconds=seconds)


def test_scope_rejects_preexisting_missing_occurrence_rather_than_replacing_it():
    batch = small_batch(lab=2)
    corrupted = replace(batch, prepared=replace(batch.prepared, sessions=batch.prepared.sessions[1:]))
    with pytest.raises(ValueError, match="every original"):
        select_complete_scope(corrupted, {(1, 1, "WEEKDAY", "IT")})


def test_joint_scope_is_fully_validated_and_exports_real_bookings_and_interactive_views(tmp_path):
    batch = small_batch(lab=2)
    selected = select_complete_scope(batch, {(1, 1, "WEEKDAY", "IT"), (1, 1, "WEEKDAY", "SE")}, max_time_seconds=10.0)
    result, report = run_pp1(selected, tmp_path / "complete")
    validate_evaluation_result(selected.prepared, selected.config, result, **selected.solver_inputs)
    assert report["scheduled_sessions"] == 6 and report["unscheduled_sessions"] == 0
    assert report["clash_validation"] == "PASSED"
    data = json.loads((tmp_path / "complete" / "timetable.json").read_text(encoding="utf-8"))
    assert len(data["scheduled"]) == 6 and all(row["lecturer_id"] == "SYN-LECTURER" for row in data["scheduled"])
    validation = json.loads((tmp_path / "complete" / "independent-validation.json").read_text(encoding="utf-8"))
    assert validation["complete_coverage"] and validation["student_lunch_checked"] and validation["venue_constraints_checked"]
    html = (tmp_path / "complete" / "weekly.html").read_text(encoding="utf-8")
    assert "PP1_TEST_CALENDAR" in html and "SYN-LECTURER" in html and "SYN-HALL" in html
    assert "No separate" not in html
