"""Synthetic actual-layout examples; no private campus data enters tests."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from schedence_teaching.excel_import import import_wide_excel
from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
from schedence_teaching.excel_import.staging import SourceLocation, SourceModuleRecord, SourceTeamAssignment, WideImportResult
from schedence_teaching.excel_import.wide_config import AcademicSourceConfirmation
from schedence_teaching.pilot.academic import (
    CONTACT_HOUR_COLUMNS, NonLecturePattern, OfferingSourceBinding, build_specialization_teaching_input, extract_academic_catalog,
)
from schedence_teaching.pilot.academic_cli import write_academic_report
from schedence_teaching.pilot.demo import build_demo_submission
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import ModuleOffering, SessionType, StaffMember, Stream, StudentGroup, TeachingRole
from schedence_teaching.solver import SolverConfig, VenueSchedulingInput, solve_timetable, validate_timetable

from test_student_group_pilot import cohort, reference_sources, solved_pilot, workbook_path
from test_wide_excel_import import wide_dataset


@pytest.fixture
def confirmed_wide(wide_dataset):
    config = wide_dataset["config"]
    config.source_confirmation = AcademicSourceConfirmation(reference="synthetic:user-source-confirmation",
        workbook_sha256={path.name: sha256(path.read_bytes()).hexdigest() for path in wide_dataset["paths"].values()})
    config.lecture_hours_interpretation = "ONE_CONTINUOUS_WEEKLY_LECTURE"
    return wide_dataset


def source(lecture_hours=2, *, tutorial=0, workshop=0, lab=0):
    result = WideImportResult(source_confirmation_reference="synthetic:source-confirmation",
        lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    result.staff_members = [StaffMember(staff_id="SYN-LECTURER", staff_name="Synthetic Lecturer", position="Lecturer", staff_type="ACADEMIC")]
    for index, spec in enumerate(("SYN-A, SYN-B", "SYN-B"), 2):
        location = SourceLocation("synthetic-modules.xlsx", "Modules_183", index)
        result.source_modules.append(SourceModuleRecord(location, {
            "Lecture_Hours_Week": lecture_hours, "Tutorial_Hours_Week": tutorial,
            "Workshop_Hours_Week": workshop, "Lab_Hours_Week": lab,
            "Total_Student_Contact_Hours": f"=SUM(V{index}:Y{index})",
        }, contract_fields=dict(module_code="IT2100", module_name=f"Synthetic Module {index}",
            academic_year=1, semester=1, specialization=spec, degree_type="Synthetic")))
        result.source_assignments.append(SourceTeamAssignment(location, "LIC_Staff_ID", "LIC", "SYN-LECTURER", "Synthetic Lecturer", TeachingRole.LIC))
    return result


def bindings(result):
    return [OfferingSourceBinding(source=row.location, offering=ModuleOffering(
        **{key: value for key, value in row.contract_fields.items() if key != "specialization"},
        specialization=spec, offering_id=f"SYN-OFFERING-{index}", stream="WEEKDAY"))
        for index, (row, spec) in enumerate(zip(result.source_modules, ("SYN-A", "SYN-B")), 1)]


def group_inputs():
    groups = [StudentGroup(group_id=f"SYN-G-{spec}", academic_year=1, semester=1, stream="WEEKDAY", student_count=120)
              for spec in ("A", "B")]
    return dict(groups=groups, group_specializations={row.group_id: f"SYN-{spec}" for row, spec in zip(groups, ("A", "B"))},
        session_audiences={SessionType.LECTURE: tuple(row.group_id for row in groups)})


def build(result, **kwargs):
    return build_specialization_teaching_input(result, extract_academic_catalog(result), offerings=bindings(result), **(kwargs or group_inputs()))


def test_confirmation_preserves_source_labels_names_teams_and_files(confirmed_wide):
    files = confirmed_wide["paths"]
    before = {key: path.read_bytes() for key, path in files.items()}
    result = import_wide_excel(confirmed_wide["config"])
    assert result.valid
    summary = result.summary()
    assert summary["data_status"] == "user_confirmed_university_sourced"
    assert not {"development_data", "source_provenance_unverified", "session_requirements_not_inferred"} & set(summary["issue_counts"])
    assert summary["source_provenance"]["staff_status_counts"]["Demo instructor source"] == 2
    assert len(result.source_assignments) == 14
    assert {key: path.read_bytes() for key, path in files.items()} == before
    assert "Synthetic Person" not in json.dumps(summary)


def test_source_confirmation_does_not_invent_preference_scope_or_weights(confirmed_wide):
    result = import_wide_excel(confirmed_wide["config"])
    preferences = map_lecturer_preferences(result, LecturerPreferenceMapping(
        layout="department-aligned-preferences-v1", source_reference="synthetic:preference-source"))
    assert preferences.summary()["source_status"] == "user_confirmed_university_sourced"
    assert preferences.summary()["unconfirmed_candidates"] == 0
    assert preferences.summary()["unscoped_candidates"] > 0
    assert all(row.penalty_weight is None for row in preferences.candidates)
    assert not preferences.preferences


@pytest.mark.parametrize("key", ["staff", "preferences", "modules"])
def test_confirmation_rejects_any_changed_workbook(confirmed_wide, key):
    # Touch ZIP bytes, without changing any synthetic cell values.
    with confirmed_wide["paths"][key].open("ab") as handle:
        handle.write(b"synthetic-changed-file")
    result = import_wide_excel(confirmed_wide["config"])
    assert not result.valid
    assert result.source_confirmation_reference is None
    assert any(row.code == "source_confirmation_checksum_mismatch" for row in result.issues)


@pytest.mark.parametrize("hours,duration", [(1, 60), (2, 120), (0, None)])
def test_one_continuous_lecture_not_split(hours, duration):
    result = source(hours)
    catalog = extract_academic_catalog(result)
    assert catalog.valid
    assert all(row.lecture_duration_minutes == duration for row in catalog.modules)
    if duration:
        teaching = build(result)
        prepared = prepare_timetable_input(teaching)
        assert len(prepared.sessions) == len(teaching.session_requirements) == 2
        assert all(row.duration_minutes == duration and row.sessions_per_week == 1 for row in teaching.session_requirements)


@pytest.mark.parametrize("value", [None, "2", "=1+1", -1, True, float("inf"), 1.001, 3])
def test_missing_invalid_or_unconfirmed_lecture_patterns_are_not_invented(value):
    catalog = extract_academic_catalog(source(value))
    assert not catalog.valid
    assert all(row.row in {2, 3} and row.column == "Lecture_Hours_Week" for row in catalog.issues)


def test_actual_scope_list_identity_and_start_information_are_preserved():
    result = source()
    before = deepcopy(result)
    starts = [dict(specialization="SYN-B", academic_year=3, semester=1, source_reference="synthetic:supplied-start")]
    catalog = extract_academic_catalog(result, specialization_starts=starts)
    assert catalog.valid and len(catalog.modules) == 2
    assert catalog.modules[0].source.contract_fields["specialization"] == "SYN-A, SYN-B"
    assert len(catalog.select(academic_year=1, semester=1, specialization="SYN-A")) == 1
    assert len(catalog.select(academic_year=1, semester=1, specialization="SYN-B")) == 2
    assert not catalog.select(academic_year=2, semester=1, specialization="SYN-B")
    assert catalog.to_dict()["specialization_starts"] == starts
    assert catalog.to_dict()["specialization_starts_inferred"] is False
    # Year-1 common content is not deleted because specialization starts later.
    assert catalog.modules[0].source.location != catalog.modules[1].source.location
    assert result == before


def test_requirement_ids_are_stable_and_all_source_teams_retained():
    result = source()
    before = deepcopy(result)
    first, second = build(result), build(result)
    assert first == second
    assert len({row.module_code for row in first.module_offerings}) == 1
    assert len({row.offering_id for row in first.module_offerings}) == 2
    assert len(first.teaching_team_members) == len(result.source_assignments)
    assert first.staff_members == result.staff_members and result == before
    assert {row.offering_id for row in first.module_offerings} == {"SYN-OFFERING-1", "SYN-OFFERING-2"}


def test_all_four_session_types_separate_with_explicit_nonlecture_patterns():
    result = source(tutorial=1, workshop=1, lab=2)
    inputs = group_inputs()
    inputs["session_audiences"] = {kind: tuple(row.group_id for row in inputs["groups"]) for kind in SessionType}
    patterns = [NonLecturePattern(offering_id=b.offering.offering_id, session_type=kind,
        duration_minutes=60, sessions_per_week=2 if kind == SessionType.LAB else 1, source_reference="synthetic:explicit-pattern")
        for b in bindings(result) for kind in (SessionType.TUTORIAL, SessionType.WORKSHOP, SessionType.LAB)]
    teaching = build(result, **inputs, nonlecture_patterns=patterns)
    assert len(teaching.session_requirements) == 8
    assert {row.session_type for row in teaching.session_requirements} == set(SessionType)
    assert all(row.sessions_per_week == 1 and row.duration_minutes == 120
               for row in teaching.session_requirements if row.session_type == SessionType.LECTURE)
    assert all(row.sessions_per_week == 2 for row in teaching.session_requirements if row.session_type == SessionType.LAB)


def test_nonlecture_hours_do_not_supply_unconfirmed_patterns():
    with pytest.raises(ValueError, match="Explicit LAB"):
        build(source(lab=2))


def test_inconsistent_nonlecture_weekly_total_is_rejected():
    result = source(lab=2)
    patterns = [dict(offering_id=b.offering.offering_id, session_type="LAB", duration_minutes=60, sessions_per_week=1,
                     source_reference="synthetic:bad-total") for b in bindings(result)]
    with pytest.raises(ValueError, match="exact source"):
        build(result, **group_inputs(), nonlecture_patterns=patterns)


@pytest.mark.parametrize("field,value", [("academic_year", 2), ("semester", 2), ("specialization", "SYN-NOT-SUPPLIED"),
    ("module_name", "Changed"), ("module_code", "REPLACED")])
def test_offering_binding_cannot_replace_university_fields(field, value):
    result = source()
    bound = bindings(result)
    setattr(bound[0].offering, field, value)
    with pytest.raises(ValueError):
        build_specialization_teaching_input(result, extract_academic_catalog(result), offerings=bound, **group_inputs())


def test_aggregate_groups_cannot_be_distributed_to_specializations_implicitly():
    result = source()
    inputs = group_inputs()
    inputs["group_specializations"] = {}
    with pytest.raises(ValueError, match="group-to-specialization"):
        build(result, **inputs)


def test_parent_and_child_are_not_duplicate_attendees_for_the_same_content():
    result = source()
    inputs = group_inputs()
    child = StudentGroup(group_id="SYN-CHILD", parent_group_id=inputs["groups"][0].group_id,
        academic_year=1, semester=1, stream="WEEKDAY", student_count=60)
    inputs["groups"].append(child)
    inputs["group_specializations"][child.group_id] = "SYN-A"
    inputs["session_audiences"][SessionType.LECTURE] += (child.group_id,)
    with pytest.raises(ValueError, match="parent and its descendant"):
        build(result, **inputs)


def test_main_lecture_and_child_lab_use_explicit_audiences_without_double_counting():
    result = source(lab=2)
    inputs = group_inputs()
    for parent in list(inputs["groups"]):
        for index in (1, 2):
            child = parent.model_copy(update=dict(group_id=f"{parent.group_id}-L{index}", parent_group_id=parent.group_id, student_count=60))
            inputs["groups"].append(child)
            inputs["group_specializations"][child.group_id] = inputs["group_specializations"][parent.group_id]
    inputs["session_audiences"][SessionType.LAB] = tuple(row.group_id for row in inputs["groups"] if row.parent_group_id)
    patterns = [dict(offering_id=b.offering.offering_id, session_type="LAB", duration_minutes=120, sessions_per_week=1,
                     source_reference="synthetic:approved-continuous-lab") for b in bindings(result)]
    teaching = build(result, **inputs, nonlecture_patterns=patterns)
    assert len(teaching.session_requirements) == 6
    assert sum(row.student_count for row in teaching.student_groups if row.parent_group_id is None) == 240
    assert sum(row.session_type == SessionType.LECTURE for row in teaching.session_requirements) == 2


def test_two_specializations_share_one_global_staff_and_venue_model():
    prepared = prepare_timetable_input(build(source()))
    config = SolverConfig(days=[dict(day="SYN-DAY", windows={"WEEKDAY": [dict(start_minute=540, end_minute=780)]})],
        granularity_minutes=30, max_time_seconds=5, random_seed=7)
    venues = VenueSchedulingInput(venues=[dict(venue_id="SYN-ROOM", venue_name="Synthetic Room", venue_type="Lecture Hall", capacity=120)], unresolved_venue_ids=[],
        calendars=[dict(venue_id="SYN-ROOM", available_windows={"SYN-DAY": [dict(start_minute=540, end_minute=780)]}, blocked_periods=[])])
    solved = solve_timetable(prepared, config, venue_input=venues)
    assert solved.status.value in {"FEASIBLE", "OPTIMAL"}
    validate_timetable(prepared, config, solved.schedule, venue_input=venues)
    assert len(solved.schedule) == 2
    first, second = sorted(solved.schedule, key=lambda row: row.start_minute)
    assert first.end_minute <= second.start_minute
    assert all(row.end_minute - row.start_minute == 120 and row.assigned_staff_id == "SYN-LECTURER" for row in solved.schedule)


def test_view_index_references_one_global_booking_and_inherits_parent_attendance(solved_pilot):
    plan, submission, run, preparation = solved_pilot
    views = json.loads(open(run.paths["views_json"], encoding="utf-8").read())
    physical = {row.delivery_id: row for row in run.result.schedule}
    assert views["single_global_timetable"]
    assert views["physical_delivery_count"] == len(physical)
    for kind in views["views"].values():
        assert set(delivery for ids in kind.values() for delivery in ids) == set(physical)
        assert all(len(ids) == len(set(ids)) for ids in kind.values())
    for delivery in physical.values():
        for child in plan.groups:
            if child.parent_group_id in delivery.group_ids:
                assert delivery.delivery_id in views["views"]["group"][child.group_id]
    html = open(run.paths["html"], encoding="utf-8").read()
    assert all(f'id="view-{kind}"' in html for kind in views["views"])


def test_private_source_report_has_real_patterns_but_no_invented_counts_or_ids(workbook_path, tmp_path):
    from schedence_teaching.pilot.students import import_student_counts
    report = write_academic_report(source(), import_student_counts(workbook_path), tmp_path / "output")
    assert report["academic_catalog"]["module_rows"] == 2
    assert report["student_counts"]["total_students"] == 6720
    assert not report["specialization_counts_invented"] and not report["ready_for_scheduling"]
    text = (tmp_path / "output" / "specialization-demand-drafts.csv").read_text(encoding="utf-8")
    assert "120,1,DRAFT" in text
    assert "SYN-LECTURER" not in json.dumps(report)
    with pytest.raises(FileExistsError):
        write_academic_report(source(), import_student_counts(workbook_path), tmp_path / "output")


def test_demo_also_preserves_positive_source_lecture_hours():
    from schedence_teaching.pilot.students import generate_groups, GroupSizingPolicy
    academic, venues = reference_sources()
    for row in academic.source_modules:
        row.source_values["Lecture_Hours_Week"] = 2
    submission = build_demo_submission(generate_groups([cohort()], GroupSizingPolicy()), academic, venues)
    requirements = submission.inputs["combination_input"]["teaching_input"]["session_requirements"]
    assert all(row["duration_minutes"] == 120 and row["sessions_per_week"] == 1 for row in requirements if row["session_type"] == "LECTURE")


def test_missing_lecture_interpretation_is_not_a_silent_default():
    result = source()
    result.lecture_hours_interpretation = None
    catalog = extract_academic_catalog(result)
    assert not catalog.valid
    assert {row.code for row in catalog.issues} == {"lecture_interpretation_required"}


def test_zero_lecture_hours_never_create_a_lecture():
    result = source(0, lab=2)
    inputs = group_inputs()
    inputs["session_audiences"] = {SessionType.LAB: tuple(row.group_id for row in inputs["groups"])}
    patterns = [dict(offering_id=b.offering.offering_id, session_type="LAB", duration_minutes=120, sessions_per_week=1,
        source_reference="synthetic:explicit-lab") for b in bindings(result)]
    teaching = build(result, **inputs, nonlecture_patterns=patterns)
    assert len(teaching.session_requirements) == 2
    assert all(row.session_type == SessionType.LAB for row in teaching.session_requirements)


@pytest.mark.parametrize("identities", [("SYN-UNKNOWN",), ("SYN-G-A", "SYN-G-A"), ()])
def test_unknown_repeated_or_empty_audiences_are_rejected(identities):
    inputs = group_inputs()
    inputs["session_audiences"] = {SessionType.LECTURE: identities}
    with pytest.raises(ValueError):
        build(source(), **inputs)


def test_no_staff_team_is_invented_to_make_a_module_schedulable():
    result = source()
    result.source_assignments = []
    with pytest.raises(ValueError, match="eligibility is missing"):
        build(result)


def test_nonlecture_pattern_cannot_override_a_continuous_lecture():
    pattern = dict(offering_id="SYN-OFFERING-1", session_type="LECTURE", duration_minutes=60, sessions_per_week=2,
        source_reference="synthetic:forbidden-split")
    with pytest.raises(ValueError, match="cannot override"):
        build(source(), **group_inputs(), nonlecture_patterns=[pattern])


def test_offering_cannot_be_silently_mapped_to_another_stream():
    result = source()
    bound = bindings(result)
    bound[0].offering.stream = Stream.WEEKEND
    with pytest.raises(ValueError, match="matching supplied student groups"):
        build_specialization_teaching_input(result, extract_academic_catalog(result), offerings=bound, **group_inputs())


def test_duplicate_source_scope_cannot_double_teaching_demand():
    result = source()
    bound = bindings(result)
    duplicate = deepcopy(bound[0])
    duplicate.offering.offering_id = "SYN-ANOTHER-ID"
    with pytest.raises(ValueError, match="double-count"):
        build_specialization_teaching_input(result, extract_academic_catalog(result), offerings=bound + [duplicate], **group_inputs())


def test_missing_nonlecture_hours_are_reported_instead_of_assuming_no_session():
    result = source()
    result.source_modules[0].source_values.pop("Lab_Hours_Week")
    catalog = extract_academic_catalog(result)
    assert not catalog.valid
    assert any(row.code == "invalid_weekly_contact_hours" and row.column == "Lab_Hours_Week" for row in catalog.issues)


def test_one_shared_source_offering_retains_its_full_specialization_list():
    result = source()
    bound = bindings(result)[:1]
    bound[0].offering.specialization = result.source_modules[0].contract_fields["specialization"]
    teaching = build_specialization_teaching_input(result, extract_academic_catalog(result), offerings=bound, **group_inputs())
    assert teaching.module_offerings[0].specialization == "SYN-A, SYN-B"
    assert len(teaching.module_offerings) == 1
    assert len(teaching.session_requirements) == 2
    assert {row.group_id for row in teaching.session_requirements} == {"SYN-G-A", "SYN-G-B"}


def test_shared_specialization_list_passes_existing_readiness_without_new_offering_ids():
    from schedence_teaching.integration import assess_readiness
    from test_authoritative_integration import INSTITUTION
    from pathlib import Path
    import runpy
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "authoritative_input_fixture.py"))
    submission = fixture["build_submission"]()
    offerings = submission.inputs["combination_input"]["teaching_input"]["module_offerings"]
    offerings[0]["specialization"] += ", SYN-ADDITIONAL-APPLICABILITY"
    result = assess_readiness(submission, expected_institution_id=INSTITUTION, actual_campus=False)
    assert result.report.ready_for_generation, result.report.to_dict()
