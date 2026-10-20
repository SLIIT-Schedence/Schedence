"""P2.1 teaching-day compactness on small deterministic synthetic data.

90 minutes is an explicitly labelled synthetic test minimum, never a campus policy.
"""

import pytest
from pydantic import ValidationError

from schedence_teaching.persistence.policy import resolve_snapshot_policy, verify_snapshot_policy
from schedence_teaching.persistence.snapshots import replay_snapshot
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.reporting.timetable_quality import timetable_quality
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import (ScheduledSession, SolverConfig, SolverInputError, SolverStatus, TimeWindow,
                                       solve_timetable, validate_timetable)
from schedence_teaching.solver.compactness_inputs import CRITERION, compactness_report
from schedence_teaching.workload import WorkloadBand

from test_fairness_solver import options, run
from test_staff_solver import demand
from test_teaching_assignment_policy import policy
from test_venue_solver import physical
from test_work_period_solver import scenario

SYNTHETIC_MINIMUM = 90
DAYS = ("D1", "D2", "D3")
PEAK = "DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES"


def compact(config, *, minimum=SYNTHETIC_MINIMUM, placement="AFTER_WORKLOAD_STAGES", reference="SYNTHETIC:COMPACTNESS"):
    return SolverConfig.model_validate({**config.model_dump(round_trip=True), "teaching_day_compactness": dict(
        policy_reference=reference, preferred_minimum_daily_teaching_minutes=minimum,
        minimum_reference="SYNTHETIC:TEST-ONLY-EXAMPLE-90-MINUTES-NOT-CAMPUS-POLICY", placement=placement)})


def thin_days(prepared, schedule, minimum=SYNTHETIC_MINIMUM):
    return timetable_quality(prepared, schedule).summary(preferred_minimum_minutes=minimum)["lecturers"]


def one_lecturer(count, *, duration=60, minutes=240, days=DAYS):
    prepared, config, staff, *_ = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", count)]),
                                          minutes=minutes, days=days, duration=duration)
    return prepared, config, staff


def test_thin_day_is_strictly_below_the_minimum_and_zero_days_are_not_thin():
    prepared, config, staff = one_lecturer(5, duration=30)
    sessions = prepared.sessions
    schedule = [ScheduledSession(sessions[0], "D1", 0, 30, "P"), ScheduledSession(sessions[1], "D1", 30, 60, "P"),
                *(ScheduledSession(sessions[index], "D2", 30 * (index - 2), 30 * (index - 1), "P") for index in (2, 3, 4))]
    validate_timetable(prepared, config, schedule, staff_input=staff)
    report = compactness_report(prepared, compact(config), schedule, ("THIN_TEACHING_DAYS",), (), None)
    # D1 has 60 minutes (thin), D2 exactly 90 (not thin), D3 has no teaching (never penalized).
    assert (report.thin_staff_days, report.thin_days_by_staff, report.active_staff_days) == (1, (("P", 1),), 2)
    assert report.daily_teaching_minutes_distribution == ((60, 1), (90, 1))
    assert thin_days(prepared, schedule)["thin_days_below_preferred_minimum"] == 1


def test_compactness_improves_a_known_feasible_fragmented_schedule_and_reports_the_daily_trade_off():
    prepared, config, staff = one_lecturer(3)
    sessions = prepared.sessions
    fragmented = [ScheduledSession(session, day, 0, 60, "P") for session, day in zip(sessions, DAYS)]
    validate_timetable(prepared, config, fragmented, staff_input=staff)  # A valid baseline, one hour on each day.
    before = thin_days(prepared, fragmented)
    disabled = solve_timetable(prepared, config, staff_input=staff)
    result = solve_timetable(prepared, compact(config), staff_input=staff)
    after = thin_days(prepared, result.schedule)
    assert before["thin_days_below_preferred_minimum"] == 3 and before["maximum_day_minutes"] == 60
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == 3
    assert after["thin_days_below_preferred_minimum"] == 0 <= thin_days(prepared, disabled.schedule)["thin_days_below_preferred_minimum"]
    # The only zero-thin schedule teaches all three hours on one day: compactness raises the daily peak to 180.
    assert after["maximum_day_minutes"] == 180 and after["attendee_days"] == 1
    report = result.teaching_day_compactness_report
    assert (report.stage_status, report.modeled_thin_staff_days, report.thin_staff_days) == ("OPTIMAL", 0, 0)
    assert report.priority_order == ("DELIVERIES", "CONTACT_MINUTES", CRITERION)
    assert result.to_dict()["teaching_day_compactness"]["hard_daily_cap_added"] is False


def test_student_and_lecturer_clashes_remain_prohibited_when_compacting():
    # One lecturer and a parent/child pair: compacting onto one day must still serialize every session.
    prepared = demand({"A": [("P", "LIC")], "B": [("P", "LIC")]}, [("A", "G", 2), ("B", "C", 2)], parents={"C": "G"})
    prepared, config, staff, *_ = scenario(prepared=prepared, minutes=240, days=("D1", "D2"), duration=60)
    result = solve_timetable(prepared, compact(config), staff_input=staff)
    assert result.status is SolverStatus.OPTIMAL and result.teaching_day_compactness_report.thin_staff_days == 0
    spans = sorted((row.day, row.start_minute, row.end_minute) for row in result.schedule)
    assert all(a[0] != b[0] or a[2] <= b[1] for a, b in zip(spans, spans[1:]))
    assert {"staff_clashes", "same_group_and_ancestor_clashes"} <= set(result.validation.checks)


def test_rank_eligibility_is_never_relaxed_to_remove_a_thin_day():
    # Instructor I could absorb the lecture on its lab day, but instructors may teach labs only.
    prepared = demand({"LEC": [("L", "LIC"), ("I", "TEAM_MEMBER")], "LABS": [("I", "TEAM_MEMBER")]},
                      [("LEC", "G", 1), ("LABS", "H", 2)], staff_types={"I": "INSTRUCTOR"})
    payload = prepared.input_snapshot.model_dump(round_trip=True)
    payload["session_requirements"][1]["session_type"] = "LAB"
    prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    prepared, config, staff, *_ = scenario(prepared=prepared, minutes=240, days=("D1", "D2"), duration=60, band="LECTURER")
    staff.assignment_policy = policy(staff)
    result = solve_timetable(prepared, compact(config), staff_input=staff)
    assert result.status is SolverStatus.OPTIMAL
    assert {row.assigned_staff_id for row in result.schedule if row.session.offering_id == "LEC"} == {"L"}
    report = result.teaching_day_compactness_report
    # L's single one-hour lecture is an unavoidable thin day; it is reported, not hidden.
    assert report.thin_days_by_staff == (("L", 1),) and report.modeled_thin_staff_days == 1
    assert "rank_appointment_session_eligibility" in result.validation.checks


def test_availability_and_venue_calendars_remain_enforced():
    prepared, config, staff = one_lecturer(3)
    calendar = staff.availability[0]
    calendar.allowed_teaching_days.discard("D1")
    calendar.available_windows.pop("D1")
    rooms = physical([("R1", 30, "Lecture Hall")])
    rooms.calendars[0].available_windows = {"D1": [TimeWindow(start_minute=0, end_minute=240)], "D2": [],
                                            "D3": [TimeWindow(start_minute=0, end_minute=240)]}
    result = solve_timetable(prepared, compact(config), staff_input=staff, venue_input=rooms)
    assert result.status is SolverStatus.OPTIMAL
    assert {(row.day, row.assigned_venue_id) for row in result.schedule} == {("D3", "R1")}  # D1: staff away; D2: room closed.
    assert result.teaching_day_compactness_report.thin_staff_days == 0


def overloaded_multi_day():
    """Four 270-minute modules shared by D (DR) and P over three days: the Priority 1 overage scenario."""
    pools = {f"M{index}": [("D", "LIC"), ("P", "TEAM_MEMBER")] for index in range(1, 5)}
    data = scenario(prepared=demand(pools, [(f"M{index}", f"G{index}", 9) for index in range(1, 5)]),
                    minutes=360, days=DAYS)
    for profile in data[2].workload_input.profiles:
        if profile.staff_id == "D":
            profile.workload_band = WorkloadBand.DR
    return data


@pytest.mark.parametrize("placement", ["AFTER_WORKLOAD_STAGES", "BEFORE_AGGREGATE_TARGET_DEVIATION"])
def test_priority_one_stage_optima_are_preserved(placement):
    _, _, baseline = run(overloaded_multi_day(), fairness_config=options(priority=PEAK))
    data = overloaded_multi_day()
    data = (data[0], compact(data[1], minimum=120, placement=placement), *data[2:])
    _, _, result = run(data, fairness_config=options(priority=PEAK))
    assert baseline.status is result.status is SolverStatus.OPTIMAL
    before = dict(baseline.fairness_report.objective_components)
    after = dict(result.fairness_report.objective_components)
    preserved = ["DELIVERIES", "CONTACT_MINUTES", "MAXIMUM_OVER_TARGET_MINUTES", "TOTAL_OVER_TARGET_MINUTES", "MAXIMUM_TARGET_DEVIATION"]
    if placement == "AFTER_WORKLOAD_STAGES":
        preserved.append("AGGREGATE_TARGET_DEVIATION")
    assert {key: after[key] for key in preserved} == {key: before[key] for key in preserved}
    assert result.fairness_report.priority_order == baseline.fairness_report.priority_order  # Fairness order unchanged.
    report = result.teaching_day_compactness_report
    order = list(report.priority_order)
    assert order.index(CRITERION) == (order.index("AGGREGATE_TARGET_DEVIATION") + 1 if placement == "AFTER_WORKLOAD_STAGES"
                                      else order.index("AGGREGATE_TARGET_DEVIATION") - 1)
    # The baseline satisfies every fixed earlier stage, so compactness can only match or reduce its thin days.
    assert report.stage_status == "OPTIMAL" and report.modeled_thin_staff_days == report.thin_staff_days
    assert report.thin_staff_days <= thin_days(data[0], baseline.schedule, 120)["thin_days_below_preferred_minimum"]
    assert result.fairness_report.all_stages_proven_optimal


def test_compactness_under_peak_priority_removes_thin_days_with_identical_workload_values():
    # One lecturer: workload is fixed, so every Priority 1 value is identical and only day shape can change.
    from test_fairness_solver import fairness
    data = scenario(prepared=demand({"A": [("P", "LIC")]}, [("A", "G", 3)]), minutes=240, days=DAYS, duration=60)
    sessions = data[0].sessions
    fragmented = [ScheduledSession(session, day, 0, 60, "P") for session, day in zip(sessions, DAYS)]
    validate_timetable(data[0], data[1], fragmented, staff_input=data[2])  # Known-valid fragmented baseline: 3 thin days.
    _, _, plain = run(data, fairness_policy=fairness(["P"]), fairness_config=options(priority=PEAK))
    compacted = (data[0], compact(data[1], placement="BEFORE_AGGREGATE_TARGET_DEVIATION"), *data[2:])
    _, _, result = run(compacted, fairness_policy=fairness(["P"]), fairness_config=options(priority=PEAK))
    assert dict(result.fairness_report.objective_components) == dict(plain.fairness_report.objective_components)
    assert thin_days(data[0], fragmented)["thin_days_below_preferred_minimum"] == 3
    assert result.teaching_day_compactness_report.thin_staff_days == 0 and result.fairness_report.all_stages_proven_optimal


def test_disabled_compactness_leaves_payloads_and_objectives_unchanged():
    prepared, config, staff = one_lecturer(3)
    assert "teaching_day_compactness" not in config.model_dump(round_trip=True)
    result = solve_timetable(prepared, config, staff_input=staff)
    assert result.teaching_day_compactness_report is None and "teaching_day_compactness" not in result.to_dict()
    _, _, fair = run(overloaded_multi_day(), fairness_config=options(priority=PEAK))
    assert CRITERION not in fair.fairness_report.priority_order


@pytest.mark.parametrize("change, message", [
    (dict(preferred_minimum_daily_teaching_minutes=0), "greater than 0"),
    (dict(preferred_minimum_daily_teaching_minutes=1441), "less than or equal"),
    (dict(minimum_reference=None), "minimum_reference"),
    (dict(placement="BEFORE_DELIVERIES"), "placement"),
])
def test_invalid_configuration_is_rejected(change, message):
    _, config, _ = one_lecturer(1)
    data = dict(policy_reference="SYNTHETIC:COMPACTNESS", preferred_minimum_daily_teaching_minutes=90,
                minimum_reference="SYNTHETIC:TEST-ONLY", placement="AFTER_WORKLOAD_STAGES")
    with pytest.raises(ValidationError, match=message):
        SolverConfig.model_validate({**config.model_dump(round_trip=True), "teaching_day_compactness": {**data, **change}})


def test_missing_staff_input_or_fairness_for_aggregate_placement_is_rejected():
    prepared, config, staff = one_lecturer(1)
    with pytest.raises(SolverInputError, match="staff scheduling input"):
        solve_timetable(prepared, compact(config))
    with pytest.raises(SolverInputError, match="requires teaching-target fairness"):
        solve_timetable(prepared, compact(config, placement="BEFORE_AGGREGATE_TARGET_DEVIATION"), staff_input=staff)


def test_sealed_snapshots_bind_the_compactness_reference():
    from test_batch_online_days import REFERENCE, build
    from test_institution_policy import revision
    from test_persistence_contracts import snapshot_for
    prepared, config, staff, rooms = build({"A": dict(year=1, lectures=2)}, enabled=False)
    for reference, accepted in ((REFERENCE, True), ("SYNTHETIC:OTHER-POLICY", False)):
        document = snapshot_for(prepared, compact(config, reference=reference), contexts=dict(staff_input=staff, venue_input=rooms))
        if not accepted:
            with pytest.raises(ValueError, match="compactness policy reference"):
                resolve_snapshot_policy(document, revision(document))
            continue
        bound = resolve_snapshot_policy(document, revision(document))
        verify_snapshot_policy(bound)
        _, sealed_prepared, sealed_config, contexts = replay_snapshot(bound)
        assert sealed_config.teaching_day_compactness.preferred_minimum_daily_teaching_minutes == SYNTHETIC_MINIMUM
        assert solve_timetable(sealed_prepared, sealed_config, **contexts).teaching_day_compactness_report is not None
