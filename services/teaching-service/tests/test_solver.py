"""Small explicitly configured synthetic schedules; no university policy assumptions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from ortools.sat.python import cp_model

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import SessionRequirement, StudentGroup, TeachingTimetableInput
from schedence_teaching.solver import (
    ScheduledSession, SolverConfig, SolverInputError, SolverStatus,
    TimetableValidationError, solve_timetable, validate_timetable,
)


def prepared_demand(assignments, parents=None, streams=None):
    """Tuples specify group, duration, frequency. Every value is synthetic."""
    parents, streams = parents or {}, streams or {}
    group_ids = sorted({group for group, _, _ in assignments} | set(parents.values()) | set(parents))
    groups = [StudentGroup(
        group_id=group, academic_year=2, semester=1, stream=streams.get(group, "WEEKDAY"),
        student_count=25, parent_group_id=parents.get(group),
    ) for group in group_ids]
    used_streams = sorted({group.stream for group in groups})
    inputs = TeachingTimetableInput(
        # Enough explicit candidates to keep these group/time regression tests
        # independent of the staff capacity constraints introduced in Step 6.
        staff_members=[{
            "staff_id": f"SYNTHETIC-STAFF-{group}", "staff_name": f"Synthetic {group}",
            "position": "Synthetic", "staff_type": "ACADEMIC",
        } for group in group_ids],
        teaching_team_members=[{
            "offering_id": f"SYNTHETIC-{stream.value}",
            "staff_id": f"SYNTHETIC-STAFF-{group}", "role": "TEAM_MEMBER",
        } for stream in used_streams for group in group_ids],
        student_groups=groups,
        module_offerings=[{
            "offering_id": f"SYNTHETIC-{stream.value}", "module_code": "SYNTHETIC-CODE",
            "module_name": "Synthetic Module", "academic_year": 2, "semester": 1,
            "specialization": "Synthetic", "degree_type": "Synthetic", "stream": stream,
        } for stream in used_streams],
        session_requirements=[SessionRequirement(
            requirement_id=f"REQ{index}", offering_id=f"SYNTHETIC-{streams.get(group, 'WEEKDAY')}",
            group_id=group, session_type="LECTURE", duration_minutes=duration, sessions_per_week=frequency,
        ) for index, (group, duration, frequency) in enumerate(assignments, 1)],
    )
    return prepare_timetable_input(inputs)


def config_for(windows=((0, 120),), granularity=15, limit=5):
    return SolverConfig(
        days=[{"day": "SYNTHETIC-DAY", "windows": {"WEEKDAY": [
            {"start_minute": start, "end_minute": end} for start, end in windows
        ]}}], granularity_minutes=granularity, max_time_seconds=limit, random_seed=7,
    )


def test_small_feasible_timetable_preserves_all_occurrences():
    prepared = prepared_demand([("MAIN", 30, 2), ("LAB", 30, 1)], {"LAB": "MAIN"})
    config = config_for()
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL and result.assigned_session_count == 3
    assert [item.session for item in result.schedule] == list(prepared.sessions)
    assert validate_timetable(prepared, config, result.schedule).valid_for_implemented_constraints
    assert result.runtime_seconds >= 0 and result.validation.assigned_occurrences == 3
    assert result.to_dict()["university_ready"] is False
    assert any("overlap is unknown" in text for text in result.to_dict()["limitations"])


@pytest.mark.parametrize(("assignments", "parents"), [
    ([("G", 30, 2)], {}),
    ([("MAIN", 30, 1), ("CHILD", 30, 1)], {"CHILD": "MAIN"}),
    ([("MAIN", 30, 1), ("NESTED", 30, 1)], {"CHILD": "MAIN", "NESTED": "CHILD"}),
])
def test_group_or_ancestor_capacity_is_infeasible(assignments, parents):
    result = solve_timetable(prepared_demand(assignments, parents), config_for(((0, 30),)))
    assert result.status is SolverStatus.INFEASIBLE
    assert result.schedule == () and result.assigned_session_count == 0 and result.validation is None


def test_siblings_can_run_concurrently_while_parent_blocks_both():
    prepared = prepared_demand([("MAIN", 30, 1), ("A", 30, 1), ("B", 30, 1)], {"A": "MAIN", "B": "MAIN"})
    config = config_for(((0, 60),))
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL
    by_group = {item.session.group_id: item for item in result.schedule}
    assert by_group["A"].start_minute == by_group["B"].start_minute
    assert by_group["MAIN"].start_minute != by_group["A"].start_minute
    assert validate_timetable(prepared, config, result.schedule).valid_for_implemented_constraints


def test_unrelated_groups_are_not_assumed_to_have_known_overlap():
    result = solve_timetable(prepared_demand([("A", 30, 1), ("B", 30, 1)]), config_for(((0, 30),)))
    assert result.status is SolverStatus.OPTIMAL
    assert {item.start_minute for item in result.schedule} == {0}
    assert result.to_dict()["university_ready"] is False


def test_both_streams_share_one_model_with_explicit_same_day_windows():
    prepared = prepared_demand([("WD", 30, 1), ("WE", 30, 1)], streams={"WE": "WEEKEND"})
    config = SolverConfig(days=[{"day": "SYNTHETIC-OPEN-DAY", "windows": {
        "WEEKDAY": [{"start_minute": 60, "end_minute": 90}],
        "WEEKEND": [{"start_minute": 60, "end_minute": 90}],
    }}], granularity_minutes=15, max_time_seconds=5)
    result = solve_timetable(prepared, config)
    assert result.assigned_session_count == 2 and result.status is SolverStatus.OPTIMAL
    assert {item.session.stream.value for item in result.schedule} == {"WEEKDAY", "WEEKEND"}
    assert {item.day for item in result.schedule} == {"SYNTHETIC-OPEN-DAY"}
    assert {item.start_minute for item in result.schedule} == {60}


def test_different_durations_fill_capacity_without_rounding():
    prepared = prepared_demand([("MAIN", 15, 1), ("MAIN", 45, 1)])
    result = solve_timetable(prepared, config_for(((0, 60),)))
    assert result.status is SolverStatus.OPTIMAL
    assert sorted(item.end_minute - item.start_minute for item in result.schedule) == [15, 45]


def test_disjoint_windows_exclude_gap_starts_and_straddling():
    config = config_for(((0, 30), (60, 90)))
    result = solve_timetable(prepared_demand([("MAIN", 30, 2)]), config)
    assert result.status is SolverStatus.OPTIMAL
    assert {item.start_minute for item in result.schedule} == {0, 60}
    too_long = solve_timetable(prepared_demand([("MAIN", 45, 1)]), config)
    assert too_long.status is SolverStatus.INFEASIBLE and not too_long.schedule


def test_sessions_can_use_different_days_and_end_at_midnight():
    prepared = prepared_demand([("MAIN", 60, 2)])
    config = SolverConfig(days=[{"day": day, "windows": {"WEEKDAY": [
        {"start_minute": 1380, "end_minute": 1440}
    ]}} for day in ("DAY-A", "DAY-B")], granularity_minutes=15, max_time_seconds=5)
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL and {item.day for item in result.schedule} == {"DAY-A", "DAY-B"}
    assert all(item.to_dict()["end_time"] == "24:00" for item in result.schedule)


def test_granularity_need_not_divide_the_entire_calendar_day():
    prepared = prepared_demand([("MAIN", 14, 2)])
    config = SolverConfig(days=[{"day": day, "windows": {"WEEKDAY": [
        {"start_minute": 0, "end_minute": 14}
    ]}} for day in ("DAY-A", "DAY-B")], granularity_minutes=7, max_time_seconds=5)
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL and len({item.day for item in result.schedule}) == 2


def test_missing_stream_windows_produce_no_partial_timetable():
    prepared = prepared_demand([("WD", 30, 1), ("WE", 30, 1)], streams={"WE": "WEEKEND"})
    result = solve_timetable(prepared, config_for())
    assert result.status is SolverStatus.INFEASIBLE and result.schedule == ()
    assert "no configured window" in result.failure_reason


def test_unsupported_duration_granularity_is_explicit():
    with pytest.raises(SolverInputError, match="not divisible"):
        solve_timetable(prepared_demand([("MAIN", 45, 1)]), config_for(((0, 120),), granularity=30))


@pytest.mark.parametrize(("field", "value"), [
    ("granularity_minutes", 0), ("granularity_minutes", -1), ("granularity_minutes", True),
    ("max_time_seconds", 0), ("max_time_seconds", -1), ("max_time_seconds", float("inf")),
    ("max_time_seconds", float("nan")), ("max_time_seconds", True),
    ("random_seed", -1), ("days", []),
])
def test_invalid_configuration_is_rejected(field, value):
    payload = config_for().model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        SolverConfig.model_validate(payload)


@pytest.mark.parametrize(("start", "end"), [(30, 15), (-15, 30), (0, 1500), (0, 16)])
def test_invalid_or_unaligned_windows_are_rejected(start, end):
    with pytest.raises(ValidationError):
        config_for(((start, end),))


def test_duplicate_day_identifiers_are_rejected():
    payload = config_for().model_dump()
    payload["days"] += payload["days"]
    with pytest.raises(ValidationError, match="unique"):
        SolverConfig.model_validate(payload)


def test_tiny_real_time_limit_returns_unknown_without_partial_output():
    result = solve_timetable(prepared_demand([("MAIN", 30, 3)]), config_for(limit=1e-12))
    assert result.status is SolverStatus.UNKNOWN
    assert result.schedule == () and result.validation is None and result.assigned_session_count == 0


def test_feasible_native_status_is_preserved(monkeypatch):
    real_solve = cp_model.CpSolver.solve
    def solve_as_feasible(self, model):
        status = real_solve(self, model)
        return cp_model.FEASIBLE if status == cp_model.OPTIMAL else status
    monkeypatch.setattr(cp_model.CpSolver, "solve", solve_as_feasible)
    result = solve_timetable(prepared_demand([("MAIN", 30, 1)]), config_for())
    assert result.status is SolverStatus.FEASIBLE and result.validation.valid_for_implemented_constraints


def test_model_invalid_status_returns_no_schedule(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda self, model: cp_model.MODEL_INVALID)
    result = solve_timetable(prepared_demand([("MAIN", 30, 1)]), config_for())
    assert result.status is SolverStatus.MODEL_INVALID and not result.schedule and result.validation is None


def test_repeatability_with_fixed_seed_and_reordered_source_input():
    prepared = prepared_demand([("MAIN", 30, 2), ("CHILD", 30, 1)], {"CHILD": "MAIN"})
    config = config_for()
    first = solve_timetable(prepared, config)
    repeated = solve_timetable(prepared, config)
    reordered = prepared.input_snapshot.model_copy(deep=True)
    reordered.session_requirements.reverse()
    reordered.student_groups.reverse()
    third = solve_timetable(prepare_timetable_input(reordered), config)
    assert first.schedule == repeated.schedule == third.schedule


def test_empty_demand_is_a_complete_empty_solution():
    result = solve_timetable(prepared_demand([]), config_for())
    assert result.status is SolverStatus.OPTIMAL and result.schedule == ()
    assert result.validation.expected_occurrences == result.validation.assigned_occurrences == 0


def test_mutated_preparation_and_configuration_cannot_bypass_validation():
    prepared = prepared_demand([("MAIN", 30, 1)])
    with pytest.raises(SolverInputError, match="no longer match"):
        solve_timetable(replace(prepared, sessions=()), config_for())
    with pytest.raises(SolverInputError, match="prepare_timetable_input"):
        solve_timetable(prepared.input_snapshot, config_for())
    config = config_for()
    config.days[0].windows[next(iter(config.days[0].windows))][0].end_minute = 16
    with pytest.raises(ValidationError, match="align"):
        solve_timetable(prepared, config)


@pytest.mark.parametrize(("start", "end", "code"), [
    (-15, 15, "invalid_time"), (0, 1500, "invalid_time"), (0.0, 30.0, "invalid_time"),
    (False, 30, "invalid_time"), (0, 45, "duration_changed"), (1, 31, "unaligned_time"),
    (120, 150, "outside_window"),
])
def test_independent_validator_detects_invalid_times(start, end, code):
    prepared = prepared_demand([("MAIN", 30, 1)])
    schedule = [ScheduledSession(prepared.sessions[0], "SYNTHETIC-DAY", start, end, "SYNTHETIC-STAFF-MAIN")]
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, config_for(), schedule)
    assert code in {issue.code for issue in error.value.issues}


@pytest.mark.parametrize("problem,code", [
    ("missing", "missing_occurrence"), ("duplicate", "duplicate_occurrence"),
    ("unknown", "unknown_occurrence"), ("metadata", "metadata_changed"), ("day", "invalid_day"),
    ("bad_record", "invalid_record"),
])
def test_independent_validator_detects_coverage_metadata_and_day_errors(problem, code):
    prepared = prepared_demand([("MAIN", 30, 1)])
    original = ScheduledSession(prepared.sessions[0], "SYNTHETIC-DAY", 0, 30, "SYNTHETIC-STAFF-MAIN")
    schedule = [original]
    if problem == "missing":
        schedule = []
    elif problem == "duplicate":
        schedule.append(original)
    elif problem == "unknown":
        schedule = [replace(original, session=replace(original.session, occurrence_id="UNKNOWN"))]
    elif problem == "metadata":
        schedule = [replace(original, session=replace(original.session, offering_id="CHANGED"))]
    elif problem == "day":
        schedule = [replace(original, day="UNKNOWN-DAY")]
    else:
        schedule = [{}]
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, config_for(), schedule)
    assert code in {issue.code for issue in error.value.issues}


@pytest.mark.parametrize("descendant", [False, True])
def test_independent_validator_detects_clashes_without_using_cp_sat(descendant):
    prepared = prepared_demand([("MAIN", 30, 1), ("CHILD" if descendant else "MAIN", 30, 1)],
                               {"CHILD": "MAIN"} if descendant else {})
    schedule = [ScheduledSession(session, "SYNTHETIC-DAY", 0, 30, f"SYNTHETIC-STAFF-{session.group_id}")
                for session in prepared.sessions]
    with pytest.raises(TimetableValidationError) as error:
        validate_timetable(prepared, config_for(), schedule)
    assert ("ancestor_clash" if descendant else "same_group_clash") in {issue.code for issue in error.value.issues}


def test_validator_rejects_wrong_stream_window_and_gap_straddling():
    prepared = prepared_demand([("WE", 30, 1)], streams={"WE": "WEEKEND"})
    schedule = [ScheduledSession(prepared.sessions[0], "SYNTHETIC-DAY", 0, 30, "SYNTHETIC-STAFF-WE")]
    with pytest.raises(TimetableValidationError, match="outside_window"):
        validate_timetable(prepared, config_for(), schedule)
    prepared = prepared_demand([("MAIN", 60, 1)])
    schedule = [ScheduledSession(prepared.sessions[0], "SYNTHETIC-DAY", 15, 75, "SYNTHETIC-STAFF-MAIN")]
    with pytest.raises(TimetableValidationError, match="outside_window"):
        validate_timetable(prepared, config_for(((0, 30), (60, 90))), schedule)


def test_solver_does_not_return_output_if_independent_validation_fails(monkeypatch):
    from schedence_teaching.solver import engine
    from schedence_teaching.solver.results import ScheduleIssue
    def fail_validation(*args):
        raise TimetableValidationError([ScheduleIssue("test_failure", "Synthetic validator failure.")])
    monkeypatch.setattr(engine, "validate_timetable", fail_validation)
    with pytest.raises(TimetableValidationError, match="test_failure"):
        solve_timetable(prepared_demand([("MAIN", 30, 1)]), config_for())


def test_existing_step_four_synthetic_example_is_schedulable():
    path = Path(__file__).resolve().parents[1] / "examples" / "preparation-input.example.json"
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate_json(path.read_text(encoding="utf-8")))
    config = SolverConfig(days=[
        {"day": "SYNTHETIC-MONDAY", "windows": {"WEEKDAY": [{"start_minute": 540, "end_minute": 840}]}},
        {"day": "SYNTHETIC-SATURDAY", "windows": {"WEEKEND": [{"start_minute": 540, "end_minute": 660}]}},
    ], granularity_minutes=15, max_time_seconds=5, random_seed=7)
    result = solve_timetable(prepared, config)
    assert result.status is SolverStatus.OPTIMAL and result.assigned_session_count == 4
    assert result.validation.expected_occurrences == result.validation.assigned_occurrences == 4
