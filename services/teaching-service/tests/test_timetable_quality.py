"""P2.0 read-only quality metrics: exact hand-built schedules plus one solved online calendar."""

from schedence_teaching.reporting.timetable_quality import timetable_quality
from schedence_teaching.solver import ScheduledSession, solve_timetable
from schedence_teaching.solver.attendance_inputs import DeliveryMode

from test_online_attendance_solver import problem
from test_staff_solver import demand


def booked(prepared, placements):
    """placements: (session index, day, start, staff, mode or None); every session lasts 30 minutes."""
    sessions = prepared.sessions
    return tuple(ScheduledSession(sessions[index], day, start, start + sessions[index].duration_minutes, staff, None, mode)
                 for index, day, start, staff, mode in placements)


def _with_c2():
    """P is the parent of leaves C1 and C2; Q is a separate leaf. Lecturers A and B."""
    from schedence_teaching.preparation import prepare_timetable_input
    from schedence_teaching.schemas import TeachingTimetableInput
    base = demand({"OP": [("A", "LIC"), ("B", "LIC")], "OC": [("A", "LIC")], "OQ": [("B", "LIC")]},
                  [("OP", "P", 2), ("OC", "C1", 1), ("OQ", "Q", 2)], parents={"C1": "P"})
    payload = base.input_snapshot.model_dump(round_trip=True)
    payload["student_groups"].append(dict(group_id="C2", academic_year=2, semester=1, stream="WEEKDAY",
                                          student_count=5, parent_group_id="P"))
    return prepare_timetable_input(TeachingTimetableInput.model_validate(payload))


def index(prepared):
    return {(row.group_id, row.occurrence_number): position for position, row in enumerate(prepared.sessions)}


def test_lecturer_runs_gaps_spans_and_teaching_days_are_exact():
    prepared = _with_c2()
    at = index(prepared)
    schedule = booked(prepared, [(at["P", 1], "MON", 0, "A", None), (at["C1", 1], "MON", 30, "A", None),
                                 (at["P", 2], "MON", 120, "A", None), (at["Q", 1], "TUE", 0, "B", None),
                                 (at["Q", 2], "WED", 60, "B", None)])
    report = timetable_quality(prepared, schedule)
    monday = next(r for r in report.lecturer_days if (r.entity_id, r.day) == ("A", "MON"))
    assert (monday.sessions, monday.teaching_minutes, monday.back_to_back_pairs) == (3, 90, 1)
    assert (monday.idle_gap_minutes, monday.longest_continuous_minutes, monday.span_minutes) == (60, 60, 150)
    assert dict(report.lecturer_teaching_days) == {"A": 1, "B": 2}
    summary = report.summary()
    assert summary["lecturers"]["teaching_days_histogram"] == {1: 1, 2: 1}
    assert "days_at_or_below_thin_minutes" not in summary["lecturers"]  # No threshold is defaulted.
    assert report.summary(thin_day_minutes=30)["lecturers"]["days_at_or_below_thin_minutes"] == 2


def test_students_are_lowest_level_groups_and_inherit_parent_sessions():
    prepared = _with_c2()
    at = index(prepared)
    schedule = booked(prepared, [(at["P", 1], "MON", 0, "A", None), (at["C1", 1], "MON", 30, "A", None),
                                 (at["P", 2], "MON", 120, "B", None)])
    days = {(r.entity_id, r.day): r for r in timetable_quality(prepared, schedule).student_days}
    assert set(days) == {("C1", "MON"), ("C2", "MON")}  # The parent P is not reported as its own attendee.
    assert (days["C1", "MON"].teaching_minutes, days["C1", "MON"].back_to_back_pairs) == (90, 1)
    assert (days["C2", "MON"].teaching_minutes, days["C2", "MON"].idle_gap_minutes) == (60, 90)
    report = timetable_quality(prepared, schedule)
    assert report.summary(long_student_day_minutes=60)["student_groups"]["days_above_day_minutes"] == 1


def test_online_mix_and_online_days_are_reported_per_batch():
    prepared = _with_c2()
    at = index(prepared)
    online, physical = DeliveryMode.ONLINE, DeliveryMode.PHYSICAL
    schedule = booked(prepared, [(at["P", 1], "WED", 0, "A", online), (at["P", 2], "MON", 0, "A", physical),
                                 (at["C1", 1], "FRI", 0, "A", online), (at["Q", 1], "WED", 60, "B", online),
                                 (at["Q", 2], "WED", 120, "B", online)])
    report = timetable_quality(prepared, schedule)
    (batch,) = report.batch_lectures
    assert (batch.academic_year, batch.semester, batch.stream) == (2, 1, "WEEKDAY")
    assert (batch.lectures, batch.online_lectures, batch.online_lecture_days) == (5, 4, ("FRI", "WED"))
    # OP has one physical lecture; OC and OQ are entirely online.
    assert (batch.offerings_with_lectures, batch.offerings_without_physical_lecture) == (3, 2)
    assert report.summary()["lectures"]["batches_with_multiple_online_days"] == 1
    assert ("WEEKDAY", "WED", "ONLINE", 90) in report.stream_day_mode_minutes


def test_solved_online_calendar_reports_venue_free_online_lectures():
    prepared, config, venues = problem(mode="ONLINE")
    result = solve_timetable(prepared, config, venue_input=venues)
    report = timetable_quality(prepared, result.schedule, config=config)
    (batch,) = report.batch_lectures
    assert (batch.stream, batch.lectures, batch.online_lectures, batch.online_lecture_days) == ("WEEKEND", 2, 2, ("MONDAY",))
    assert report.to_dict()["descriptive_only"] is True
