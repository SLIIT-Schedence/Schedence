"""P2.2 daily workload distribution on small deterministic synthetic data. No thresholds are campus policy."""

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.persistence.policy import resolve_snapshot_policy
from schedence_teaching.persistence.snapshots import replay_snapshot
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.reporting.timetable_quality import timetable_quality
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (ScheduledSession, SolverConfig, SolverInputError, SolverStatus, TimeWindow,
                                       solve_timetable, validate_timetable)
from schedence_teaching.solver.daily_load_inputs import STAFF, STUDENT, add_student_daily_maximum

from test_fairness_solver import options, run
from test_staff_solver import demand
from test_teaching_assignment_policy import policy
from test_teaching_day_compactness import DAYS, PEAK, compact, overloaded_multi_day
from test_venue_solver import physical
from test_work_period_solver import scenario

TWO_DAYS = ("D1", "D2")


def daily(config, criteria=(STUDENT,), *, placement="AFTER_WORKLOAD_STAGES", relative=None, reference="SYNTHETIC:DAILY-LOAD"):
    return SolverConfig.model_validate({**config.model_dump(round_trip=True), "daily_load_distribution": dict(
        policy_reference=reference, criteria=list(criteria), placement=placement, relative_to_compactness=relative)})


def maxima(prepared, schedule):
    summary = timetable_quality(prepared, schedule).summary()
    return summary["student_groups"]["maximum_day_minutes"], summary["lecturers"]["maximum_day_minutes"]


def checked(prepared, config, staff, result, **contexts):
    assert result.status is SolverStatus.OPTIMAL
    assert len(result.schedule) == len(prepared.sessions)  # Weekly coverage is unchanged.
    assert sum(row.end_minute - row.start_minute for row in result.schedule) == sum(s.duration_minutes for s in prepared.sessions)
    assert {"staff_clashes", "same_group_and_ancestor_clashes"} <= set(result.validation.checks)
    validate_timetable(prepared, config, result.schedule, staff_input=staff, **contexts)
    return result


def test_student_daily_maximum_is_reduced_from_a_known_unbalanced_timetable():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 4)]),
                                          minutes=240, days=TWO_DAYS, duration=60)
    unbalanced = [ScheduledSession(session, "D1", 60 * index, 60 * (index + 1), "P") for index, session in enumerate(prepared.sessions)]
    validate_timetable(prepared, config, unbalanced, staff_input=staff)
    result = checked(prepared, config, staff, solve_timetable(prepared, daily(config), staff_input=staff))
    assert maxima(prepared, unbalanced) == (240, 240) and maxima(prepared, result.schedule)[0] == 120
    report = result.daily_load_report
    assert report.stages == ((STUDENT, "OPTIMAL", 120),) and report.modeled_maxima == ((STUDENT, 120),)
    assert report.maximum_student_daily_minutes == 120 and report.mean_student_daily_minutes == 120.0
    assert result.to_dict()["daily_load_distribution"]["hard_daily_cap_added"] is False


def test_staff_daily_maximum_is_reduced_when_students_are_not_the_bottleneck():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")], "B": [("P", "LIC")]}, [("A", "G1", 2), ("B", "G2", 2)]),
                                          minutes=240, days=TWO_DAYS, duration=60)
    unbalanced = [ScheduledSession(session, "D1", 60 * index, 60 * (index + 1), "P") for index, session in enumerate(prepared.sessions)]
    validate_timetable(prepared, config, unbalanced, staff_input=staff)
    result = checked(prepared, config, staff, solve_timetable(prepared, daily(config, (STAFF,)), staff_input=staff))
    assert maxima(prepared, unbalanced) == (120, 240)
    assert maxima(prepared, result.schedule)[1] == 120 and result.daily_load_report.modeled_maxima == ((STAFF, 120),)


def test_child_groups_inherit_parent_sessions():
    prepared = demand({"A": [("P", "LIC")], "B": [("Q", "LIC")]}, [("A", "G", 2), ("B", "C", 2)], parents={"C": "G"})
    prepared, config, staff, *_ = scenario(prepared=prepared, minutes=240, days=TWO_DAYS, duration=60)
    sessions = {(row.group_id, row.occurrence_number): row for row in prepared.sessions}
    stacked = [ScheduledSession(sessions["G", 1], "D1", 0, 60, "P"), ScheduledSession(sessions["G", 2], "D1", 60, 120, "P"),
               ScheduledSession(sessions["C", 1], "D1", 120, 180, "Q"), ScheduledSession(sessions["C", 2], "D2", 0, 60, "Q")]
    validate_timetable(prepared, config, stacked, staff_input=staff)
    days = {(row.entity_id, row.day): row.teaching_minutes for row in timetable_quality(prepared, stacked).student_days}
    assert days == {("C", "D1"): 180, ("C", "D2"): 60}  # The parent G is not a leaf; C inherits its two sessions.
    result = checked(prepared, config, staff, solve_timetable(prepared, daily(config), staff_input=staff))
    assert result.daily_load_report.modeled_maxima == ((STUDENT, 120),)


def test_one_delivery_counts_once_per_leaf_even_when_several_of_its_groups_apply():
    prepared = demand({"A": [("P", "LIC")]}, [("A", "G", 1)], parents={})
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    payload["student_groups"].append(dict(group_id="C", academic_year=2, semester=1, stream="WEEKDAY", student_count=5, parent_group_id="G"))
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    _, config, *_ = scenario(prepared=prepared, minutes=240, days=TWO_DAYS, duration=60)
    model, flags = cp_model.CpModel(), {}
    day = model.new_constant(0)

    def flag(identity, index, day_variable):
        flags.setdefault((identity, index), model.new_constant(int(index == 0)))
        return flags[identity, index]

    # A combined-style delivery naming both the parent G and its child C must count once for leaf C.
    maximum = add_student_daily_maximum(model, config, prepared,
                                        [("COMBINED", ("G", "C"), 60, None, day, {prepared.sessions[0].stream})], flag)
    solver = cp_model.CpSolver()
    assert solver.solve(model) == cp_model.OPTIMAL and solver.value(maximum) == 60


def test_venue_capacity_and_availability_are_preserved():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 4)]),
                                          minutes=240, days=DAYS, duration=60)
    rooms = physical([("R1", 30, "Lecture Hall"), ("TINY", 10, "Lecture Hall")])  # Groups have 25 students.
    for calendar in rooms.calendars:
        calendar.available_windows = {"D1": [TimeWindow(start_minute=0, end_minute=240)], "D2": [],
                                      "D3": [TimeWindow(start_minute=0, end_minute=240)]}
    result = checked(prepared, config, staff, solve_timetable(prepared, daily(config), staff_input=staff, venue_input=rooms),
                     venue_input=rooms)
    assert {row.assigned_venue_id for row in result.schedule} == {"R1"} and "D2" not in {row.day for row in result.schedule}
    assert result.daily_load_report.modeled_maxima == ((STUDENT, 120),)  # Balanced across the two open days only.


def test_rank_eligibility_is_unchanged():
    prepared = demand({"LEC": [("L", "LIC"), ("I", "TEAM_MEMBER")], "LABS": [("I", "TEAM_MEMBER"), ("L", "LIC")]},
                      [("LEC", "G", 2), ("LABS", "H", 2)], staff_types={"I": "INSTRUCTOR"})
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][1]["session_type"] = "LAB"
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    prepared, config, staff, *_ = scenario(prepared=prepared, minutes=240, days=TWO_DAYS, duration=60, band="LECTURER")
    staff.assignment_policy = policy(staff)
    result = checked(prepared, config, staff, solve_timetable(prepared, daily(config, (STUDENT, STAFF)), staff_input=staff))
    by_type = {(row.session.session_type.value, row.assigned_staff_id) for row in result.schedule}
    assert by_type == {("LECTURE", "L"), ("LAB", "I")}  # Balancing never gives labs to L or lectures to I.
    assert "rank_appointment_session_eligibility" in result.validation.checks


def test_compactness_and_daily_load_conflict_is_explicit_and_ordered():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 3)]),
                                          minutes=240, days=DAYS, duration=60)
    both = compact(daily(config, (STAFF,)))
    with pytest.raises(SolverInputError, match="relative_to_compactness"):
        solve_timetable(prepared, both, staff_input=staff)
    daily_first = solve_timetable(prepared, compact(daily(config, (STAFF,), relative="DAILY_LOAD_THEN_COMPACTNESS")), staff_input=staff)
    compact_first = solve_timetable(prepared, compact(daily(config, (STAFF,), relative="COMPACTNESS_THEN_DAILY_LOAD")), staff_input=staff)
    assert daily_first.daily_load_report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", STAFF, "THIN_TEACHING_DAYS")
    assert compact_first.daily_load_report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", "THIN_TEACHING_DAYS", STAFF)
    # The objectives conflict: an even spread makes every day thin, while no thin days needs one 180-minute day.
    assert (daily_first.daily_load_report.maximum_staff_daily_minutes, daily_first.teaching_day_compactness_report.thin_staff_days) == (60, 3)
    assert (compact_first.daily_load_report.maximum_staff_daily_minutes, compact_first.teaching_day_compactness_report.thin_staff_days) == (180, 0)


def test_batch_online_day_rules_remain_valid():
    from test_batch_online_days import REFERENCE, assert_online_day_rules, build, selected
    from test_institution_policy import revision
    from test_persistence_contracts import snapshot_for
    prepared, config, staff, rooms = build({"A": dict(year=1, lectures=3, labs=2)})
    document = snapshot_for(prepared, daily(config, (STUDENT, STAFF), reference=REFERENCE),
                            contexts=dict(staff_input=staff, venue_input=rooms))
    _, prepared, config, contexts = replay_snapshot(resolve_snapshot_policy(document, revision(document)))
    result = solve_timetable(prepared, config, **contexts)
    assert result.status is SolverStatus.OPTIMAL and "batch_online_day_selection" in result.validation.checks
    ((_, day),) = selected(result).items()
    assert_online_day_rules(result, "A", day)
    assert [row[0] for row in result.daily_load_report.stages] == [STUDENT, STAFF]


@pytest.mark.parametrize("placement", ["AFTER_WORKLOAD_STAGES", "BEFORE_AGGREGATE_TARGET_DEVIATION"])
def test_priority_one_values_are_protected(placement):
    _, _, baseline = run(overloaded_multi_day(), fairness_config=options(priority=PEAK))
    data = overloaded_multi_day()
    data = (data[0], daily(data[1], (STUDENT, STAFF), placement=placement), *data[2:])
    _, _, result = run(data, fairness_config=options(priority=PEAK))
    before, after = dict(baseline.fairness_report.objective_components), dict(result.fairness_report.objective_components)
    protected = ["DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES", "TOTAL_OVER_TARGET_MINUTES", "MAXIMUM_TARGET_DEVIATION"]
    if placement == "AFTER_WORKLOAD_STAGES":
        protected.append("AGGREGATE_TARGET_DEVIATION")
    assert {key: after[key] for key in protected} == {key: before[key] for key in protected}
    assert result.fairness_report.all_stages_proven_optimal and all(row[1] == "OPTIMAL" for row in result.daily_load_report.stages)
    # The baseline satisfies every earlier fixed stage, so the minimax stages can only match or improve it.
    student, staff = maxima(data[0], baseline.schedule)
    assert dict(result.daily_load_report.modeled_maxima) == {STUDENT: result.daily_load_report.maximum_student_daily_minutes,
                                                            STAFF: result.daily_load_report.maximum_staff_daily_minutes}
    assert result.daily_load_report.maximum_student_daily_minutes <= student


def test_disabled_daily_load_leaves_payloads_unchanged():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 2)]), minutes=240, days=TWO_DAYS)
    assert "daily_load_distribution" not in config.model_dump(round_trip=True)
    result = solve_timetable(prepared, config, staff_input=staff)
    assert result.daily_load_report is None and "daily_load_distribution" not in result.to_dict()


@pytest.mark.parametrize("change, message", [
    (dict(criteria=[]), "at least 1"),
    (dict(criteria=[STUDENT, STUDENT]), "distinct"),
    (dict(criteria=["MAX_ROOM_DAILY_MINUTES"]), "criteria"),
    (dict(placement="FIRST"), "placement"),
    (dict(relative_to_compactness="EITHER"), "relative_to_compactness"),
])
def test_invalid_configuration_is_rejected(change, message):
    _, config, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 1)]), days=TWO_DAYS)
    data = dict(policy_reference="SYNTHETIC:DAILY-LOAD", criteria=[STUDENT], placement="AFTER_WORKLOAD_STAGES")
    with pytest.raises(ValidationError, match=message):
        SolverConfig.model_validate({**config.model_dump(round_trip=True), "daily_load_distribution": {**data, **change}})


def test_unsupported_combinations_are_rejected_and_student_only_needs_no_staff_input():
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 2)]), minutes=240, days=TWO_DAYS, duration=60)
    with pytest.raises(SolverInputError, match="staff scheduling input"):
        solve_timetable(prepared, daily(config, (STAFF,)))
    with pytest.raises(SolverInputError, match="requires teaching-target fairness"):
        solve_timetable(prepared, daily(config, placement="BEFORE_AGGREGATE_TARGET_DEVIATION"), staff_input=staff)
    result = solve_timetable(prepared, daily(config))
    assert result.status is SolverStatus.OPTIMAL and result.daily_load_report.modeled_maxima == ((STUDENT, 60),)


def test_sealed_snapshots_bind_the_daily_load_reference():
    from test_batch_online_days import REFERENCE, build
    from test_institution_policy import revision
    from test_persistence_contracts import snapshot_for
    prepared, config, staff, rooms = build({"A": dict(year=1, lectures=2)}, enabled=False)
    contexts = dict(staff_input=staff, venue_input=rooms)
    document = snapshot_for(prepared, daily(config, reference="SYNTHETIC:OTHER-POLICY"), contexts=contexts)
    with pytest.raises(ValueError, match="Daily-load distribution policy reference"):
        resolve_snapshot_policy(document, revision(document))
    document = snapshot_for(prepared, daily(config, reference=REFERENCE), contexts=contexts)
    _, sealed_prepared, sealed_config, sealed_contexts = replay_snapshot(resolve_snapshot_policy(document, revision(document)))
    assert solve_timetable(sealed_prepared, sealed_config, **sealed_contexts).daily_load_report is not None


def test_a_feasible_daily_stage_after_proven_fairness_is_reported_honestly(monkeypatch):
    native, calls = cp_model.CpSolver.solve, []

    def stop_at_student_stage(self, model, *args, **kwargs):
        status = native(self, model, *args, **kwargs)
        calls.append(status)
        # Peak priority solves peak, total, maximum and aggregate deviation natively, then the student stage.
        return cp_model.FEASIBLE if len(calls) == 5 else status

    monkeypatch.setattr(cp_model.CpSolver, "solve", stop_at_student_stage)
    data = overloaded_multi_day()
    data = (data[0], daily(data[1], (STUDENT, STAFF)), *data[2:])
    _, _, result = run(data, fairness_config=options(priority=PEAK))  # The harness re-validates fairness independently.
    assert result.status is SolverStatus.FEASIBLE and result.fairness_report.all_stages_proven_optimal
    assert [(row[0], row[1]) for row in result.daily_load_report.stages] == [(STUDENT, "FEASIBLE")]  # The staff stage never ran.
    assert len(result.schedule) == len(data[0].sessions)
