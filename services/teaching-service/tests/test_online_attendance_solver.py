"""Small independent proofs for overlapping rosters and venue-free online lectures."""

from dataclasses import replace

import pytest

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput, Venue
from schedence_teaching.solver import SolverConfig, VenueSchedulingInput, SolverInputError, TimetableValidationError, solve_timetable, validate_timetable
from schedence_teaching.solver.attendance_inputs import DeliveryMode


def problem(*, shared_students=False, shared_staff=False, venues=2, mode="PHYSICAL", duration=60, start=510, end=570, kind="LECTURE"):
    staff = [dict(staff_id=identity,staff_name="Synthetic",position="Lecturer",staff_type="ACADEMIC") for identity in ("SYN-A","SYN-B")]
    teaching = TeachingTimetableInput(staff_members=staff,
        module_offerings=[dict(offering_id="O-"+identity,module_code="SYN",module_name="Synthetic "+identity,
            academic_year=4,semester=1,specialization="IT",degree_type="Synthetic",stream="WEEKEND") for identity in ("A","B")],
        student_groups=[dict(group_id=identity,academic_year=4,semester=1,stream="WEEKEND",student_count=2) for identity in ("A","B")],
        teaching_team_members=[dict(offering_id="O-"+identity,staff_id="SYN-A" if shared_staff else "SYN-"+identity,role="LIC") for identity in ("A","B")],
        session_requirements=[dict(requirement_id="R-"+identity,offering_id="O-"+identity,group_id=identity,session_type=kind,
            duration_minutes=duration,sessions_per_week=1) for identity in ("A","B")])
    config = SolverConfig(days=[dict(day="MONDAY",windows={"WEEKEND":[dict(start_minute=start,end_minute=end)]})],
        granularity_minutes=30,max_time_seconds=3.0,random_seed=7,
        delivery_calendar=dict(reference="synthetic:explicit-online-day",days=[dict(day="MONDAY",stream="WEEKEND",mode=mode,allowed_session_types={"LECTURE"})]),
        attendance_required_group_ids={"A","B"},attendance_memberships=[dict(group_id="A",student_ids=("PP1_TEST:A1","PP1_TEST:A2"),complete=True,evidence_reference="synthetic:complete"),
            dict(group_id="B",student_ids=("PP1_TEST:A1" if shared_students else "PP1_TEST:B1","PP1_TEST:B2"),complete=True,evidence_reference="synthetic:complete")])
    venue_input=VenueSchedulingInput(unresolved_venue_ids=set(),venues=[Venue(venue_id=f"SYN-V{index}",venue_name="Synthetic",venue_type="Lecture Hall",capacity=2) for index in range(venues)],
        calendars=[dict(venue_id=f"SYN-V{index}",available_windows={"MONDAY":[dict(start_minute=start,end_minute=end)]},blocked_periods=[]) for index in range(venues)])
    return prepare_timetable_input(teaching), config, venue_input


@pytest.mark.parametrize("mode",["ONLINE","PHYSICAL"])
def test_disjoint_populations_can_attend_simultaneously_with_separate_staff_and_resources(mode):
    prepared,config,venues=problem(mode=mode)
    result=solve_timetable(prepared,config,venue_input=venues)
    assert result.status.value=="OPTIMAL" and len(result.schedule)==2
    validate_timetable(prepared,config,result.schedule,venue_input=venues)
    assert result.schedule[0].start_minute==result.schedule[1].start_minute==510
    if mode=="ONLINE":assert all(row.assigned_venue_id is None and row.delivery_mode==DeliveryMode.ONLINE for row in result.schedule)
    else:assert len({row.assigned_venue_id for row in result.schedule})==2


@pytest.mark.parametrize("mode",["ONLINE","PHYSICAL"])
@pytest.mark.parametrize("conflict",["students","lecturer"])
def test_shared_students_and_lecturers_cannot_overlap_even_when_online(mode,conflict):
    prepared,config,venues=problem(mode=mode,shared_students=conflict=="students",shared_staff=conflict=="lecturer")
    result=solve_timetable(prepared,config,venue_input=venues)
    assert result.status.value=="INFEASIBLE" and not result.schedule


def test_one_physical_venue_cannot_host_two_disjoint_groups_at_once():
    prepared,config,venues=problem(venues=1)
    assert solve_timetable(prepared,config,venue_input=venues).status.value=="INFEASIBLE"


def test_online_booking_does_not_require_a_suitable_physical_venue():
    prepared,config,venues=problem(mode="ONLINE",venues=1)
    venues.venues[0].capacity=1  # Below both groups' sizes, deliberately unsuitable.
    result=solve_timetable(prepared,config,venue_input=venues)
    assert result.status.value=="OPTIMAL" and all(row.assigned_venue_id is None for row in result.schedule)
    validate_timetable(prepared,config,result.schedule,venue_input=venues)


@pytest.mark.parametrize("corruption",["physical-venue","wrong-mode","missing-mode"])
def test_independent_validator_rejects_corrupt_online_bookings(corruption):
    prepared,config,venues=problem(mode="ONLINE")
    result=solve_timetable(prepared,config,venue_input=venues)
    first=result.schedule[0]
    if corruption=="physical-venue":first=replace(first,assigned_venue_id="SYN-V0")
    if corruption=="wrong-mode":first=replace(first,delivery_mode=DeliveryMode.PHYSICAL)
    if corruption=="missing-mode":first=replace(first,delivery_mode=None)
    with pytest.raises(TimetableValidationError):validate_timetable(prepared,config,(first,*result.schedule[1:]),venue_input=venues)


def test_independent_validator_checks_selected_student_overlap_directly():
    prepared,config,venues=problem(shared_students=True,end=630)
    result=solve_timetable(prepared,config,venue_input=venues)
    assert result.status.value=="OPTIMAL"
    first,second=result.schedule
    corrupted=replace(second,start_minute=first.start_minute,end_minute=first.end_minute)
    with pytest.raises(TimetableValidationError) as error:validate_timetable(prepared,config,(first,corrupted),venue_input=venues)
    assert any(row.code=="student_membership_clash" for row in error.value.issues)


def test_online_day_does_not_silently_allow_labs():
    prepared,config,venues=problem(mode="ONLINE",kind="LAB")
    assert solve_timetable(prepared,config,venue_input=venues).status.value=="INFEASIBLE"
    raw=config.model_dump()
    raw["delivery_calendar"]["days"][0]["allowed_session_types"]={"LAB"}
    with pytest.raises(ValueError):SolverConfig.model_validate(raw)


@pytest.mark.parametrize("damage",["missing","incomplete","wrong-count","unknown-group","duplicate-roster"])
def test_required_rosters_cannot_be_silently_dropped_or_fabricated(damage):
    prepared,config,venues=problem()
    if damage=="missing":config.attendance_memberships=config.attendance_memberships[:1]
    if damage=="incomplete":config.attendance_memberships[0].complete=False
    if damage=="wrong-count":config.attendance_memberships[0].student_ids=("PP1_TEST:A1",)
    if damage=="unknown-group":config.attendance_memberships[0].group_id="UNKNOWN"
    if damage=="duplicate-roster":config.attendance_memberships=(*config.attendance_memberships,config.attendance_memberships[0])
    with pytest.raises(SolverInputError):solve_timetable(prepared,config,venue_input=venues)


@pytest.mark.parametrize("start,end,expected",[(810,870,"INFEASIBLE"),(750,810,"OPTIMAL"),(870,930,"OPTIMAL")])
def test_confirmed_year4_student_lunch_applies_to_online_sessions(start,end,expected):
    from schedence_teaching.lunch import confirmed_student_lunch_policy
    from schedence_teaching.preferences import PreferenceCalendar
    from schedence_teaching.solver import StudentLunchEnforcementConfig
    prepared,config,venues=problem(mode="ONLINE",start=start,end=end)
    calendar=PreferenceCalendar(week_reference="SYNTHETIC",week_start="2026-10-19",week_end="2026-10-25",day_dates={"MONDAY":"2026-10-19"},synthetic_data=True)
    lunch=confirmed_student_lunch_policy(policy_reference="synthetic:confirmed-lunch",week_reference=calendar.week_reference,
        provenance=dict(source="synthetic",reference="synthetic:confirmed"),applicable_dates=set(calendar.day_dates.values()),
        applicable_streams={"WEEKEND"},applicability_reference="synthetic:both-streams")
    result=solve_timetable(prepared,config,venue_input=venues,student_lunch_policy=lunch,student_lunch_calendar=calendar,
        student_lunch_config=StudentLunchEnforcementConfig(applicability_confirmation="CONFIRMED",applicability_confirmation_reference="synthetic:scope"))
    assert result.status.value==expected
