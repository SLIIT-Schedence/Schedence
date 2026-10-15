"""Synthetic scenarios preserve exact demand and independently checked unions."""

from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from schedence_teaching.pilot.coordinated_streams import (
    reserve_previous_bookings, solve_coordinated_streams, solve_coordinated_cohorts, validated_union)
from schedence_teaching.pilot.lab_diagnostics import lab_diagnostics, resource_utilization, _available_minutes
from schedence_teaching.pilot.research_requirements import apply_research_requirements, ResearchRequirementInput
from schedence_teaching.pilot.semester_inputs import RegisteredSnapshot, load_registered_snapshot
from schedence_teaching.pilot.semester_scenarios import (
    SemesterScenario, SemesterScenarioSettings, select_scenario, run_semester_scenarios)
from schedence_teaching.solver import SolverConfig, SolverResult, SolverStatus, solve_timetable

from test_elective_enrollment import elective_fixture, enrolled_fixture


def settings(**overrides):
    return SemesterScenarioSettings(reference="synthetic:semester-alternatives", max_time_seconds=10.0,
        stress_test_seconds=10.0, scenarios=[dict(name="A", semesters=[1])], **overrides)


def snapshot(data):
    base, *_ = data
    enrolled = apply_research_requirements(enrolled_fixture(data), base,
        ResearchRequirementInput(policy_reference="synthetic:compulsory-research"))
    return RegisteredSnapshot(enrolled, (dict(cohort_id="SYN-BLOCKED", semester=1, student_count=7),),
        tuple(base.plan.cohorts), "SYNTHETIC_TEST_SNAPSHOT")


@pytest.mark.parametrize("workers", [0,33,True,1.5])
def test_invalid_search_worker_configuration_rejected(workers):
    with pytest.raises(ValueError):
        settings(num_search_workers=workers)


def test_search_defaults_preserve_existing_solver_behavior(elective_fixture):
    config=elective_fixture[0].config
    assert config.num_search_workers==1 and config.lab_variables_first is False
    assert SolverConfig.model_validate(config.model_dump())==config


@pytest.mark.parametrize("semesters", [[],[0],[3],[True]])
def test_invalid_semester_scenarios_rejected(semesters):
    with pytest.raises(ValueError):SemesterScenario(name="A",semesters=semesters)


@pytest.mark.parametrize("name", ["../unsafe","nested/name",""])
def test_scenario_name_cannot_escape_output_directory(name):
    with pytest.raises(ValueError):SemesterScenario(name=name,semesters=[1])


def test_missing_semester_and_duplicate_scenario_names_rejected(elective_fixture):
    source=snapshot(elective_fixture)
    with pytest.raises(ValueError,match="semester"):
        select_scenario(source,SemesterScenario(name="B",semesters=[2]),settings())
    raw=settings().model_dump()
    raw["scenarios"]*=2
    with pytest.raises(ValueError,match="unique"):SemesterScenarioSettings.model_validate(raw)


def test_scenario_preserves_requirements_registrations_rooms_and_calendar(elective_fixture):
    source=snapshot(elective_fixture)
    original=source.enrolled.batch
    chosen=select_scenario(source,SemesterScenario(name="A",semesters=[1]),settings(num_search_workers=2,lab_variables_first=True))
    assert chosen.batch.prepared.sessions==original.prepared.sessions
    assert chosen.selections==source.enrolled.selections
    assert chosen.batch.config.days==original.config.days
    assert chosen.batch.config.delivery_calendar==original.config.delivery_calendar
    assert chosen.batch.solver_inputs["venue_input"].model_dump()==original.solver_inputs["venue_input"].model_dump()
    assert chosen.research_pending_cohort_ids==source.enrolled.research_pending_cohort_ids
    assert chosen.batch.metadata["blocked_student_count"]==7
    assert original.config.num_search_workers==1


def test_explicit_calendar_subset_is_never_labelled_full_source_scope(elective_fixture,monkeypatch):
    from schedence_teaching.pilot import semester_scenarios
    source=snapshot(elective_fixture)
    excluded=next(row.cohort_id for row in source.enrolled.batch.plan.cohorts if row.academic_year==1)
    monkeypatch.setattr(semester_scenarios,"student_packing_audit",lambda batch:dict(blocked_cohort_ids=[excluded]))
    chosen=select_scenario(source,SemesterScenario(name="explicit-subset",semesters=[1],
        exclude_proven_calendar_infeasible=True),settings())
    assert not chosen.batch.metadata["full_source_scope_included"]
    assert chosen.batch.metadata["excluded_proven_calendar_infeasible_cohorts"]==[excluded]
    ids={row.group_id for row in chosen.batch.plan.groups}
    assert chosen.batch.prepared.sessions==tuple(row for row in source.enrolled.batch.prepared.sessions if row.group_id in ids)


def test_lab_diagnostics_use_actual_room_capacity_and_candidate_staff(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    rows=lab_diagnostics(enrolled.batch)
    assert rows["lab_count"]==1 and rows["lab_occurrences"]
    assert all(row["eligible_venue_ids"]=="SYN-LAB" and row["student_count"]<=60 for row in rows["lab_occurrences"])
    assert all(row["eligible_staff_count"]==2 and row["duration_minutes"]==120 for row in rows["lab_occurrences"])
    venue=next(row for row in rows["inventory"] if row["venue_id"]=="SYN-LAB")
    assert venue["capacity"]==60


def test_union_of_windows_and_blocks_never_double_counts_capacity():
    assert _available_minutes([(0,120),(60,180)],[(30,60),(45,90)])==120


def test_failed_run_utilization_is_unknown_not_zero(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    failed=SolverResult(SolverStatus.UNKNOWN,(),0,None,"synthetic budget exhausted")
    report=resource_utilization(enrolled.batch,failed)
    assert not report["utilization_known"]
    assert all(row["scheduled_minutes"] is None and row["utilization_percent"] is None for row in report["venues"])
    assert all(row["scheduled_teaching_minutes"] is None for row in report["lecturers"])


def test_coordinated_union_covers_all_sessions_without_resource_clashes(elective_fixture):
    source=snapshot(elective_fixture)
    original=source.enrolled.batch.solver_inputs["staff_input"].model_dump()
    result,stages=solve_coordinated_streams(source.enrolled,seconds_per_part=10.0)
    assert result.status is SolverStatus.FEASIBLE
    assert len(result.schedule)==len(source.enrolled.batch.prepared.sessions)
    assert len(stages)==2 and stages[1]["previous_bookings_reserved"]==stages[0]["scheduled"]
    assert result.student_lunch_report
    assert source.enrolled.batch.solver_inputs["staff_input"].model_dump()==original
    utilization=resource_utilization(source.enrolled.batch,result)
    assert utilization["utilization_known"]
    assert sum(row["scheduled_teaching_minutes"] for row in utilization["lecturers"])==sum(row.session.duration_minutes for row in result.schedule)


def test_coordinated_union_recomputes_original_global_preferences(elective_fixture):
    from test_preferences import preference
    from test_preference_solver import inputs
    enrolled=snapshot(elective_fixture).enrolled
    batch=enrolled.batch
    contexts=inputs(batch.prepared,batch.config,
        [preference(staff_id="SYN-LEC-1",category="WORKING_DAY",days=["MONDAY"],
            scope=dict(week_reference=batch.configuration.calendar.week_reference))],dates=batch.configuration.calendar)
    enrolled=replace(enrolled,batch=replace(batch,solver_inputs=dict(batch.solver_inputs,**contexts)))
    result,_=solve_coordinated_streams(enrolled,seconds_per_part=10.0)
    assert result.status is SolverStatus.FEASIBLE and result.preference_report
    assert result.preference_report.objective_status=="FEASIBLE"


def test_cohort_decomposition_reserves_every_previous_resource_booking(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    result,stages=solve_coordinated_cohorts(enrolled,seconds_per_part=10.0)
    assert result.status is SolverStatus.FEASIBLE
    assert len(result.schedule)==len(enrolled.batch.prepared.sessions)
    assert len(stages)==len(enrolled.batch.plan.cohorts)
    assert stages[-1]["previous_bookings_reserved"]==sum(row["scheduled"] for row in stages[:-1])


def test_reservations_preserve_original_inputs_and_debit_explicit_caps(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    result=solve_timetable(enrolled.batch.prepared,enrolled.batch.config,**enrolled.batch.solver_inputs)
    assert result.status in {SolverStatus.OPTIMAL,SolverStatus.FEASIBLE}
    booking=result.schedule[0]
    source=enrolled.batch.solver_inputs["staff_input"]
    staff=next(row for row in source.availability if row.staff_id==booking.assigned_staff_id)
    staff.weekly_teaching_max_minutes=600
    staff.daily_teaching_max_minutes=300
    before=source.model_dump()
    reserved=reserve_previous_bookings(enrolled,[booking])
    amended=next(row for row in reserved.batch.solver_inputs["staff_input"].availability if row.staff_id==booking.assigned_staff_id)
    assert amended.weekly_teaching_max_minutes==600-booking.session.duration_minutes
    assert amended.daily_teaching_max_minutes==300-booking.session.duration_minutes
    assert source.model_dump()==before
    assert amended.blocked_periods[-1].start_minute==booking.start_minute


def test_failed_restricted_part_is_unknown_with_no_partial_timetable(elective_fixture,monkeypatch):
    from schedence_teaching.pilot import coordinated_streams
    enrolled=snapshot(elective_fixture).enrolled
    monkeypatch.setattr(coordinated_streams,"solve_timetable",lambda *a,**k: SolverResult(SolverStatus.INFEASIBLE,(),0,None,"synthetic restricted-part failure"))
    result,stages=solve_coordinated_streams(enrolled)
    assert result.status is SolverStatus.UNKNOWN and not result.schedule
    assert stages[0]["native_status"]=="INFEASIBLE"


def test_incomplete_or_corrupted_union_rejected(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    with pytest.raises(ValueError):validated_union(enrolled,[],0.1)


def test_unsupported_joint_policy_and_cross_stream_membership_fail_closed(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    modified=replace(enrolled,batch=replace(enrolled.batch,solver_inputs=dict(enrolled.batch.solver_inputs,work_periods=object())))
    with pytest.raises(ValueError,match="monolithic"):solve_coordinated_streams(modified)
    rosters=list(enrolled.group_memberships)
    groups={row.group_id:row for row in enrolled.batch.plan.groups}
    first=rosters[0]
    second=next(row for row in rosters if groups[row.group_id].stream!=groups[first.group_id].stream)
    changed=second.model_copy(update=dict(student_ids=first.student_ids))
    modified=replace(enrolled,group_memberships=tuple(changed if row.group_id==second.group_id else row for row in rosters))
    with pytest.raises(ValueError,match="Cross-stream"):solve_coordinated_streams(modified)


def test_end_to_end_synthetic_scenario_exports_honest_completion(elective_fixture,tmp_path):
    root=tmp_path/"new-scenarios"
    reports=run_semester_scenarios(snapshot(elective_fixture),settings(strategy="COORDINATED_STREAMS"),root)
    row=reports[0]
    assert row["known_session_scope_status"]=="COMPLETE" and row["solver_status"]=="FEASIBLE"
    assert row["hard_constraint_violations"]==0 and not row["year4_academic_timetable_complete"]
    assert row["research_pending_cohorts"]==2
    assert (root/"A-diagnostics/lab-demand.csv").is_file()
    assert (root/"A/weekly.html").is_file()
    assert json.loads((root/"A/views.json").read_text())["single_global_run"] is False
    with pytest.raises(FileExistsError):run_semester_scenarios(snapshot(elective_fixture),settings(),root)


def test_snapshot_outside_private_data_rejected(tmp_path):
    with pytest.raises(ValueError,match="ignored"):load_registered_snapshot(tmp_path)
