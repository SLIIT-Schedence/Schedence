"""Continuous-session packing differs from raw weekly attendance hours."""

from types import SimpleNamespace

import pytest

from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverConfig
from schedence_teaching.pilot.student_packing import student_packing_audit, _segments


def batch(*, year=3, lectures=6, labs=4):
    raw=TeachingTimetableInput.model_validate(dict(
        staff_members=[dict(staff_id="SYN-STAFF",staff_name="Synthetic",position="Lecturer",staff_type="ACADEMIC")],
        module_offerings=[dict(offering_id="SYN-OFFER",module_code="SYN-CODE",module_name="Synthetic module",
            academic_year=year,semester=1,specialization="IT",degree_type="Synthetic",stream="WEEKEND")],
        teaching_team_members=[dict(offering_id="SYN-OFFER",staff_id="SYN-STAFF",role="LIC")],
        student_groups=[dict(group_id="SYN-G",academic_year=year,semester=1,stream="WEEKEND",student_count=20)],
        session_requirements=[dict(requirement_id="SYN-L",offering_id="SYN-OFFER",group_id="SYN-G",
            session_type="LECTURE",duration_minutes=120,sessions_per_week=lectures),
            dict(requirement_id="SYN-LAB",offering_id="SYN-OFFER",group_id="SYN-G",
            session_type="LAB",duration_minutes=120,sessions_per_week=labs),
            dict(requirement_id="SYN-W",offering_id="SYN-OFFER",group_id="SYN-G",
            session_type="WORKSHOP",duration_minutes=60,sessions_per_week=2)]))
    days=[dict(day=day,windows=dict(WEEKEND=[dict(start_minute=510,end_minute=end)]))
          for day,end in (("MONDAY",1080),("SATURDAY",1050),("SUNDAY",1050))]
    config=SolverConfig.model_validate(dict(days=days,granularity_minutes=30,max_time_seconds=1.0,
        delivery_calendar=dict(reference="synthetic:hybrid",days=[dict(day=row["day"],stream="WEEKEND",
            mode="ONLINE" if row["day"]=="MONDAY" else "PHYSICAL",
            allowed_session_types=["LECTURE"] if row["day"]=="MONDAY" else ["LECTURE","LAB","WORKSHOP"])
            for row in days])))
    calendar=PreferenceCalendar(week_reference="SYNTHETIC",week_start="2026-10-19",week_end="2026-10-25",
        synthetic_data=True,day_dates=dict(MONDAY="2026-10-19",SATURDAY="2026-10-24",SUNDAY="2026-10-25"))
    lunch=confirmed_student_lunch_policy(policy_reference="synthetic:lunch",week_reference=calendar.week_reference,
        provenance=dict(source="synthetic",reference="synthetic:confirmed"),
        applicable_dates=set(calendar.day_dates.values()),applicable_streams={"WEEKEND"},
        applicability_reference="synthetic:explicit-both-streams")
    return SimpleNamespace(prepared=prepare_timetable_input(raw),config=config,
        configuration=SimpleNamespace(calendar=calendar),solver_inputs=dict(student_lunch_policy=lunch),
        plan=SimpleNamespace(groups=tuple(raw.student_groups),cohort_by_group={"SYN-G":"SYN-COHORT"}),
        group_specializations={"SYN-G":"IT"})


def test_raw_twenty_two_hours_fit_but_ten_two_hour_sessions_do_not():
    result=student_packing_audit(batch())
    row=next(row for row in result["rows"] if row["audience"]=="ALL_KNOWN_ATTENDANCE" and row["minimum_duration_minutes"]==120)
    assert row["known_contact_minutes"]==22*60
    assert row["required_long_occurrences"]==10 and row["optimistic_whole_occurrence_slots"]==9
    assert row["shortage_occurrences"]==1 and result["blocked_cohort_ids"]==["SYN-COHORT"]


def test_year_specific_lunch_changes_contiguous_packing_without_inventing_hours():
    result=student_packing_audit(batch(year=1))
    row=next(row for row in result["rows"] if row["audience"]=="ALL_KNOWN_ATTENDANCE" and row["minimum_duration_minutes"]==120)
    assert row["optimistic_whole_occurrence_slots"]==12 and not row["shortage_occurrences"]
    assert result["status"]=="NO_UPPER_BOUND_VIOLATION"


def test_passing_bound_is_not_a_feasibility_claim():
    original=batch(lectures=5)
    before=original.prepared.input_snapshot.model_dump()
    result=student_packing_audit(original)
    assert result["status"]=="NO_UPPER_BOUND_VIOLATION"
    assert original.prepared.input_snapshot.model_dump()==before


def test_labs_cannot_consume_online_day_capacity():
    result=student_packing_audit(batch(lectures=1,labs=7))
    row=next(row for row in result["rows"] if row["audience"]=="PHYSICAL_ONLY_ATTENDANCE" and row["minimum_duration_minutes"]==120)
    assert row["required_long_occurrences"]==7 and row["optimistic_whole_occurrence_slots"]==6


def test_unknown_lunch_applicability_rejected():
    original=batch()
    original.solver_inputs["student_lunch_policy"].applicable_streams=None
    with pytest.raises(ValueError,match="explicit"):student_packing_audit(original)


def test_overlapping_windows_are_merged_and_lunch_removed_once():
    from schedence_teaching.schemas.time import TimeWindow
    assert _segments([(510,900),(600,1050)],TimeWindow(start_minute=810,end_minute=870))==[(510,810),(870,1050)]
