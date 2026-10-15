"""Optimistic physical-room bounds cannot fabricate a feasibility conclusion."""

from types import SimpleNamespace

import pytest

from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.preferences import PreferenceCalendar
from schedence_teaching.schemas import TeachingTimetableInput, Venue
from schedence_teaching.solver import SolverConfig, SessionVenueRequirement
from schedence_teaching.pilot.resource_capacity import physical_venue_capacity_audit,export_resource_capacity

from test_online_attendance_solver import problem


def laboratory_batch():
    prepared,config,venues=problem(kind="LAB",duration=120,start=510,end=1050,venues=1)
    raw=prepared.input_snapshot.model_dump()
    raw["student_groups"][0]["academic_year"]=1
    raw["module_offerings"][0]["academic_year"]=1
    for row in raw["session_requirements"]:row["sessions_per_week"]=5
    prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(raw))
    options=config.model_dump()
    options["delivery_calendar"]["days"][0]["allowed_session_types"]={"LAB"}
    config=SolverConfig.model_validate(options)
    venues.venues=[Venue(venue_id="SYN-V0",venue_name="Synthetic Lab",venue_type="Computer Lab",capacity=60)]
    venues.requirements=[SessionVenueRequirement(requirement_id=row.requirement_id,allowed_venue_types={"Computer Lab"}) for row in prepared.input_snapshot.session_requirements]
    calendar=PreferenceCalendar(week_reference="SYNTHETIC",week_start="2026-10-19",week_end="2026-10-25",day_dates={"MONDAY":"2026-10-19"},synthetic_data=True)
    lunch=confirmed_student_lunch_policy(policy_reference="synthetic:confirmed-lunch",week_reference=calendar.week_reference,
        provenance=dict(source="synthetic",reference="synthetic:confirmed"),applicable_dates=set(calendar.day_dates.values()),
        applicable_streams={"WEEKEND"},applicability_reference="synthetic:explicit")
    return SimpleNamespace(prepared=prepared,config=config,solver_inputs=dict(venue_input=venues,student_lunch_policy=lunch),configuration=SimpleNamespace(calendar=calendar))


def test_nine_hour_window_does_not_fit_five_continuous_two_hour_labs():
    result=physical_venue_capacity_audit(laboratory_batch())
    total=next(row for row in result["rows"] if row["minimum_students"]==1)
    assert total["required_sessions"]==10
    assert total["raw_room_hours_upper_bound"]==9
    assert total["optimistic_available_whole_session_slots"]==4
    assert total["shortage_sessions"]==6 and total["shortage_contact_room_hours"]==12
    assert result["status"]=="PROVEN_INFEASIBLE"


def test_capacity_thresholds_preserve_actual_room_sizes():
    batch=laboratory_batch()
    batch.solver_inputs["venue_input"].venues[0].capacity=1
    rows=physical_venue_capacity_audit(batch)["rows"]
    size=next(row for row in rows if row["minimum_students"]==2)
    assert size["compatible_venue_count_upper_bound"]==0 and size["shortage_sessions"]==10


def test_report_includes_room_capacity_boundary_even_without_matching_group_size():
    batch=laboratory_batch()
    raw=batch.prepared.input_snapshot.model_dump()
    for group in raw["student_groups"]:group["student_count"]=40
    batch.prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(raw))
    venue=batch.solver_inputs["venue_input"].venues[0]
    venue.capacity=30
    result=physical_venue_capacity_audit(batch)
    boundary=next(row for row in result["rows"] if row["minimum_students"]==31)
    assert boundary["required_sessions"]==10 and boundary["compatible_venue_count_upper_bound"]==0


@pytest.mark.parametrize("extra,slots",[(0,4),(60,4),(120,5),(240,6)])
def test_calendar_scenarios_honor_fixed_lunch_and_never_change_inputs(extra,slots):
    batch=laboratory_batch()
    before=batch.config.model_dump()
    result=physical_venue_capacity_audit(batch,extra_physical_minutes=extra)
    total=next(row for row in result["rows"] if row["minimum_students"]==1)
    assert total["optimistic_available_whole_session_slots"]==slots
    assert batch.config.model_dump()==before
    assert "TEST_SCENARIO" in total["scenario"] if extra else total["scenario"]=="PP1_TEST_CALENDAR_BASELINE"


def test_unknown_or_inapplicable_lunch_is_not_silently_assumed():
    batch=laboratory_batch()
    batch.solver_inputs["student_lunch_policy"].applicable_dates=None
    with pytest.raises(ValueError,match="explicit"):physical_venue_capacity_audit(batch)
    batch.solver_inputs["student_lunch_policy"].applicable_dates=set()
    result=physical_venue_capacity_audit(batch,extra_physical_minutes=60)
    assert result["rows"][0]["optimistic_available_whole_session_slots"]==5


def test_mixed_duration_bound_is_unavailable_rather_than_a_false_proof():
    batch=laboratory_batch()
    raw=batch.prepared.input_snapshot.model_dump()
    raw["session_requirements"][0]["duration_minutes"]=60
    batch.prepared=prepare_timetable_input(TeachingTimetableInput.model_validate(raw))
    result=physical_venue_capacity_audit(batch)
    assert result["status"]=="BOUND_UNAVAILABLE_MIXED_DURATIONS" and not result["rows"]


@pytest.mark.parametrize("extra",[-30,15,1500])
def test_invalid_calendar_extensions_rejected(extra):
    with pytest.raises(ValueError):physical_venue_capacity_audit(laboratory_batch(),extra_physical_minutes=extra)


def test_resource_reports_label_scenarios_and_refuse_overwrite(tmp_path):
    result=export_resource_capacity(laboratory_batch(),tmp_path)
    assert result["status"]=="PROVEN_INFEASIBLE"
    assert "TEST_SCENARIO_UPPER_BOUND_ONLY_NOT_A_TIMETABLE" in (tmp_path/"test-calendar-capacity-comparison.csv").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):export_resource_capacity(laboratory_batch(),tmp_path)
