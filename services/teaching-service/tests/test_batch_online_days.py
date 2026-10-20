"""P2.4 batch-specific online lecture day: small sealed synthetic scenarios only."""

import json
from dataclasses import replace
from datetime import date

import pytest
from pydantic import ValidationError

from schedence_teaching.persistence.policy import resolve_snapshot_policy
from schedence_teaching.persistence.snapshots import prepare_attempt, replay_snapshot
from schedence_teaching.preparation import PreparationError, prepare_timetable_input
from schedence_teaching.schemas import SessionType, TeachingTimetableInput
from schedence_teaching.solver import (SolverConfig, SolverInputError, SolverStatus, TimeWindow, TimetableValidationError,
                                       solve_timetable, validate_timetable)
from schedence_teaching.solver.attendance_inputs import DeliveryMode
from schedence_teaching.solver.online_day_inputs import BatchOnlineDayPolicy, checked_batch_online_days

from test_institution_policy import revision
from test_persistence_contracts import snapshot_for
from test_teaching_assignment_policy import policy
from test_venue_solver import physical
from test_work_period_solver import scenario

WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY")
WEEK = (*WEEKDAYS, "SATURDAY")
WINDOW = dict(start_minute=480, end_minute=720)
REFERENCE = "SYNTHETIC:INSTITUTION-POLICY"  # The revision policy reference used by the sealing helper.


def teaching(batches):
    """batches: name -> dict(year, stream, lectures, child_lectures, labs). Labs use the child group."""
    staff, offerings, groups, team, requirements = [], [], [], [], []
    for name, spec in batches.items():
        year, stream = spec["year"], spec.get("stream", "WEEKDAY")
        lecturer = spec.get("lecturer", f"L-{name}")
        staff += [row for row in (dict(staff_id=lecturer, staff_name="Synthetic", position="Lecturer", staff_type="ACADEMIC"),
                  dict(staff_id=f"I-{name}", staff_name="Synthetic", position="Instructor", staff_type="INSTRUCTOR"))
                  if row["staff_id"] not in {member["staff_id"] for member in staff}]
        offerings.append(dict(offering_id=f"O-{name}", module_code=f"SYN-{name}", module_name=f"Synthetic {name}",
                              academic_year=spec.get("offering_year", year), semester=1, specialization="IT", degree_type="Synthetic", stream=stream))
        groups += [dict(group_id=f"{name}-G", academic_year=year, semester=1, stream=stream, student_count=20),
                   dict(group_id=f"{name}-G.L1", academic_year=year, semester=1, stream=stream,
                        student_count=10, parent_group_id=f"{name}-G")]
        team += [dict(offering_id=f"O-{name}", staff_id=lecturer, role="LIC"),
                 dict(offering_id=f"O-{name}", staff_id=f"I-{name}", role="TEAM_MEMBER")]
        for kind, group, count, minutes in (("LECTURE", f"{name}-G", spec.get("lectures", 1), 60),
                                            ("LECTURE", f"{name}-G.L1", spec.get("child_lectures", 0), 60),
                                            ("LAB", f"{name}-G.L1", spec.get("labs", 0), 240)):
            if count:
                requirements.append(dict(requirement_id=f"R-{name}-{group}-{kind}", offering_id=f"O-{name}", group_id=group,
                                         session_type=kind, duration_minutes=minutes, sessions_per_week=count))
    return prepare_timetable_input(TeachingTimetableInput(staff_members=staff, module_offerings=offerings,
        student_groups=groups, teaching_team_members=team, session_requirements=requirements))


def build(batches, *, candidates=None, windows=None, enabled=True, away=None):
    """away: staff ID -> days that person cannot teach (L-<batch> lecturer, I-<batch> instructor)."""
    """Weekday stream Mon-Fri; weekend stream online-only Monday plus physical Saturday."""
    weekend = any(spec.get("stream") == "WEEKEND" for spec in batches.values())
    windows = windows or {}
    days = []
    for day in WEEK:
        streams = {}
        if day in WEEKDAYS:
            streams["WEEKDAY"] = [windows.get(day, WINDOW)]
        if weekend and day in ("MONDAY", "SATURDAY"):
            streams["WEEKEND"] = [WINDOW]
        days.append(dict(day=day, windows=streams))
    rows = [dict(day=day["day"], stream=stream, mode="ONLINE" if (day["day"], stream) == ("MONDAY", "WEEKEND") else "PHYSICAL",
                 allowed_session_types={"LECTURE"} if (day["day"], stream) == ("MONDAY", "WEEKEND")
                 else {"LECTURE", "LAB", "TUTORIAL", "WORKSHOP"}) for day in days for stream in day["windows"]]
    candidates = candidates or {"WEEKDAY": set(WEEKDAYS), **({"WEEKEND": {"MONDAY"}} if weekend else {})}
    online = dict(policy_reference=REFERENCE, batch_key="ACADEMIC_YEAR_SEMESTER_STREAM", candidate_days=candidates,
                  selected_day_online_lectures_only=True, require_online_lecture_on_selected_day=True)
    config = SolverConfig(days=days, granularity_minutes=60, max_time_seconds=10.0, random_seed=7,
                          delivery_calendar=dict(reference="SYNTHETIC:CALENDAR", days=rows),
                          batch_online_days=online if enabled else None)
    prepared = teaching(batches)
    _, _, staff, *_ = scenario(prepared=prepared, days=WEEK, band="LECTURER")
    staff.assignment_policy = policy(staff)
    for row in staff.availability:
        for day in (away or {}).get(row.staff_id, ()):
            row.allowed_teaching_days.discard(day)
            row.available_windows.pop(day, None)
    rooms = physical([("HALL", 100, "Lecture Hall"), *((f"LAB-{name}", 100, "Computer Lab") for name in batches)])
    for calendar in rooms.calendars:
        calendar.available_windows = {day: [TimeWindow(start_minute=0, end_minute=1440)] for day in WEEK}
    return prepared, config, staff, rooms


def sealed(*args, **kwargs):
    prepared, config, staff, rooms = build(*args, **kwargs)
    document = snapshot_for(prepared, config, contexts=dict(staff_input=staff, venue_input=rooms))
    bound = resolve_snapshot_policy(document, revision(document))
    _, prepared, config, contexts = replay_snapshot(bound)
    return bound, prepared, config, contexts


def solve(*args, **kwargs):
    bound, prepared, config, contexts = sealed(*args, **kwargs)
    return bound, (prepared, config, contexts), solve_timetable(prepared, config, **contexts)


def selected(result):
    return {(year, stream): day for year, _, stream, day in result.batch_online_days}


def batch_rows(result, name):
    return [row for row in result.schedule if row.session.offering_id == f"O-{name}"]


def assert_online_day_rules(result, name, day):
    for row in batch_rows(result, name):
        lecture = row.session.session_type is SessionType.LECTURE
        if row.day == day:
            assert lecture and row.delivery_mode is DeliveryMode.ONLINE and row.assigned_venue_id is None
        elif row.session.stream.value == "WEEKDAY" or row.day != "MONDAY":
            assert row.delivery_mode is DeliveryMode.PHYSICAL and row.assigned_venue_id is not None


def test_each_batch_gets_exactly_one_fully_online_lecture_day_shared_by_its_subgroups():
    _, (prepared, config, contexts), result = solve({"A": dict(year=1, lectures=3, child_lectures=1, labs=2)})
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule) == 6
    ((key, day),) = selected(result).items()
    assert key == (1, "WEEKDAY") and day in WEEKDAYS
    assert_online_day_rules(result, "A", day)
    online = [row for row in result.schedule if row.delivery_mode is DeliveryMode.ONLINE]
    assert online and {row.day for row in online} == {day}  # Decision 3 plus one day, parent and child alike.
    assert "batch_online_day_selection" in result.validation.checks
    assert result.to_dict()["batch_online_days"] == [dict(academic_year=1, semester=1, stream="WEEKDAY", online_day=day)]
    validate_timetable(prepared, config, result.schedule, venue_input=contexts["venue_input"], staff_input=contexts["staff_input"])


def test_day_is_selected_automatically_and_is_not_wednesday_by_default():
    # Four full-window labs need four distinct days; the only instructor cannot teach on Thursday.
    _, _, result = solve({"A": dict(year=1, lectures=1, labs=4)}, away={"I-A": ("THURSDAY",)})
    assert result.status is SolverStatus.OPTIMAL and selected(result) == {(1, "WEEKDAY"): "THURSDAY"}
    assert_online_day_rules(result, "A", "THURSDAY")


@pytest.mark.parametrize("closed_b, expected_b", [("THURSDAY", "THURSDAY"), ("TUESDAY", "TUESDAY")])
def test_batches_may_share_or_differ_and_one_batch_never_restricts_another(closed_b, expected_b):
    _, _, result = solve({"A": dict(year=1, lectures=1, labs=4), "B": dict(year=2, lectures=1, labs=4)},
                         away={"I-A": ("THURSDAY",), "I-B": (closed_b,)})
    assert result.status is SolverStatus.OPTIMAL
    assert selected(result) == {(1, "WEEKDAY"): "THURSDAY", (2, "WEEKDAY"): expected_b}
    if expected_b == "TUESDAY":  # A teaches a physical lab on B's online day, and vice versa.
        assert any(row.day == "TUESDAY" and row.session.session_type is SessionType.LAB for row in batch_rows(result, "A"))
        assert any(row.day == "THURSDAY" and row.session.session_type is SessionType.LAB for row in batch_rows(result, "B"))
    for name, day in (("A", "THURSDAY"), ("B", expected_b)):
        assert_online_day_rules(result, name, day)


def test_year_and_stream_are_separate_batches_and_weekend_uses_its_monday_online_day():
    _, _, result = solve({"W": dict(year=1, lectures=2, labs=1), "E": dict(year=1, stream="WEEKEND", lectures=2, labs=1)},
                         away={"I-W": ("THURSDAY",)})
    assert result.status is SolverStatus.OPTIMAL
    days = selected(result)
    assert set(days) == {(1, "WEEKDAY"), (1, "WEEKEND")} and days[1, "WEEKEND"] == "MONDAY"
    weekend = batch_rows(result, "E")
    assert all(row.delivery_mode is DeliveryMode.ONLINE for row in weekend if row.day == "MONDAY")
    assert all(row.delivery_mode is DeliveryMode.PHYSICAL and row.assigned_venue_id for row in weekend if row.day == "SATURDAY")
    assert_online_day_rules(result, "W", days[1, "WEEKDAY"])


def test_shared_lecturer_never_double_booked_across_batches_sharing_an_online_day():
    # Both batches are forced to Thursday; one lecturer teaches all four online-or-physical lectures.
    _, (prepared, config, contexts), result = solve(
        {"A": dict(year=1, lectures=2, labs=4, lecturer="L-SHARED"), "B": dict(year=2, lectures=2, labs=4, lecturer="L-SHARED")},
        away={"I-A": ("THURSDAY",), "I-B": ("THURSDAY",)})
    assert result.status is SolverStatus.OPTIMAL and set(selected(result).values()) == {"THURSDAY"}
    spans = sorted((row.day, row.start_minute, row.end_minute) for row in result.schedule if row.assigned_staff_id == "L-SHARED")
    assert len(spans) == 4 and all(a[0] != b[0] or a[2] <= b[1] for a, b in zip(spans, spans[1:]))
    assert {"staff_clashes", "batch_online_day_selection", "staff_availability"} <= set(result.validation.checks)


def test_lab_forced_onto_the_only_candidate_day_is_infeasible_not_relaxed():
    # The instructor can teach only on Wednesday, which is also the only candidate online day.
    _, _, result = solve({"A": dict(year=1, lectures=1, labs=1)}, candidates={"WEEKDAY": {"WEDNESDAY"}},
                         away={"I-A": ("MONDAY", "TUESDAY", "THURSDAY", "FRIDAY", "SATURDAY")})
    assert result.status is SolverStatus.INFEASIBLE and not result.schedule and result.batch_online_days is None


def test_no_online_lecture_opportunity_on_any_candidate_day_is_infeasible():
    # The only lecturer cannot teach on the only candidate day, so an online lecture is impossible there.
    _, _, control = solve({"A": dict(year=1, lectures=1)}, candidates={"WEEKDAY": {"WEDNESDAY"}})
    assert control.status is SolverStatus.OPTIMAL and selected(control) == {(1, "WEEKDAY"): "WEDNESDAY"}
    _, _, result = solve({"A": dict(year=1, lectures=1)}, candidates={"WEEKDAY": {"WEDNESDAY"}}, away={"L-A": ("WEDNESDAY",)})
    assert result.status is SolverStatus.INFEASIBLE and not result.schedule  # Never an empty online day.


def test_independent_validator_rejects_broken_online_day_rules():
    _, (prepared, config, contexts), result = solve({"A": dict(year=1, lectures=3, labs=1)})
    day = selected(result)[1, "WEEKDAY"]
    rows = list(result.schedule)

    def codes(schedule):
        with pytest.raises(TimetableValidationError) as error:
            validate_timetable(prepared, config, schedule, venue_input=contexts["venue_input"], staff_input=contexts["staff_input"])
        return {issue.code for issue in error.value.issues}

    online = next(i for i, row in enumerate(rows) if row.delivery_mode is DeliveryMode.ONLINE)
    physical_lectures = [i for i, row in enumerate(rows) if row.session.session_type is SessionType.LECTURE
                         and row.delivery_mode is DeliveryMode.PHYSICAL]
    lab = next(i for i, row in enumerate(rows) if row.session.session_type is SessionType.LAB)
    only_online = [replace(row, delivery_mode=DeliveryMode.PHYSICAL, assigned_venue_id="HALL") if row.delivery_mode is DeliveryMode.ONLINE
                   else row for row in rows]
    assert "missing_batch_online_lecture" in codes(only_online)
    if physical_lectures:
        second = [replace(row, delivery_mode=DeliveryMode.ONLINE, assigned_venue_id=None) if i == physical_lectures[0] else row
                  for i, row in enumerate(rows)]
        assert "multiple_batch_online_days" in codes(second)
    moved = [replace(row, day=day) if i == lab else row for i, row in enumerate(rows)]
    assert "batch_online_day_not_online_lecture" in codes(moved)
    assert rows[online].day == day


def test_persisted_bookings_carry_the_batch_specific_mode():
    bound, _, result = solve({"A": dict(year=1, lectures=2, labs=1)}, away={"I-A": ("THURSDAY",)})
    bookings = json.loads(prepare_attempt(bound, result).deliveries_json)
    day = selected(result)[1, "WEEKDAY"]
    calendar = {value: key for key, value in bound.calendar.day_dates.items()}
    for row in bookings:
        on_day = calendar[date.fromisoformat(str(row["teaching_date"]))] == day
        assert row["delivery_mode"] == ("ONLINE" if on_day else "PHYSICAL")
        assert (row["venue_reference"] is None) is on_day


@pytest.mark.parametrize("change, message", [
    (dict(candidate_days={"WEEKDAY": {"SATURDAY"}}), "Monday to Friday"),
    (dict(selected_day_online_lectures_only=False), "True"),
    (dict(require_online_lecture_on_selected_day=False), "True"),
    (dict(candidate_days={"WEEKDAY": set()}), "at least one"),
])
def test_policy_contract_records_only_the_confirmed_rules(change, message):
    data = dict(policy_reference=REFERENCE, batch_key="ACADEMIC_YEAR_SEMESTER_STREAM", candidate_days={"WEEKDAY": {"MONDAY"}},
                selected_day_online_lectures_only=True, require_online_lecture_on_selected_day=True)
    with pytest.raises(ValidationError, match=message):
        BatchOnlineDayPolicy.model_validate({**data, **change})


@pytest.mark.parametrize("candidates, message", [
    ({"WEEKDAY": set(WEEKDAYS)}, "Every online delivery-calendar day"),  # Weekend Monday is online-only but not a candidate.
    ({"WEEKDAY": set(WEEKDAYS), "WEEKEND": {"MONDAY", "TUESDAY"}}, "teaching windows"),
])
def test_calendar_and_policy_must_agree(candidates, message):
    with pytest.raises(ValidationError, match=message):
        build({"E": dict(year=1, stream="WEEKEND")}, candidates=candidates)


def test_batch_metadata_disagreement_is_rejected_not_guessed():
    # Preparation already rejects group/offering disagreement before any batch key is formed.
    with pytest.raises(PreparationError, match="academic_year must match"):
        build({"A": dict(year=1, offering_year=2, labs=1)})
    # Defence in depth: the batch check also refuses a mismatched prepared input that bypassed preparation.
    prepared, config, *_ = build({"A": dict(year=1, labs=1)})
    offering = prepared.input_snapshot.module_offerings[0]
    tampered = replace(prepared, input_snapshot=prepared.input_snapshot.model_copy(update=dict(
        module_offerings=[offering.model_copy(update=dict(academic_year=2))])))
    with pytest.raises(SolverInputError, match="disagree on academic batch"):
        checked_batch_online_days(tampered, config)


def test_unsealed_batch_policy_is_rejected():
    prepared, config, staff, rooms = build({"A": dict(year=1)})
    with pytest.raises(SolverInputError, match="resolved institution policy"):
        solve_timetable(prepared, config, venue_input=rooms, staff_input=staff)


def test_disabled_policy_keeps_legacy_payloads_byte_compatible():
    prepared, config, staff, rooms = build({"A": dict(year=1)}, enabled=False)
    assert "batch_online_days" not in config.model_dump(round_trip=True)
    result = solve_timetable(prepared, config, venue_input=rooms, staff_input=staff)
    assert result.status is SolverStatus.OPTIMAL and result.batch_online_days is None
    assert "batch_online_days" not in result.to_dict()
    # Legacy all-physical weekday calendar: every booking is physical with a venue.
    assert all(row.delivery_mode is DeliveryMode.PHYSICAL and row.assigned_venue_id for row in result.schedule)
