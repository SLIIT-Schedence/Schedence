"""Experimental horizon, unchanged academic demand and honest result labels."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from schedence_teaching.pilot.calendar_experiment import (
    CalendarExperiment, extend_weekend_online_calendar, placement_diagnostics, coverage_report)
from schedence_teaching.pilot.semester_scenarios import SemesterScenario, select_scenario, export_scenario
from schedence_teaching.pilot.electives import select_enrolled_scope
from schedence_teaching.pilot.resource_capacity import physical_venue_capacity_audit
from schedence_teaching.pilot.pp1_run import export_pp1, _page
from schedence_teaching.pilot.semester_inputs import RegisteredSnapshot
from schedence_teaching.pilot.student_packing import student_packing_audit
from schedence_teaching.schemas import Stream
from schedence_teaching.solver import SolverConfig, SolverResult, SolverStatus, solve_timetable

from test_elective_enrollment import elective_fixture
from test_semester_scenarios import snapshot, settings
from test_student_packing import batch as packing_batch


def experiment(**updates):
    raw=json.loads((Path(__file__).resolve().parents[1]/"examples/pp1-experimental-calendar.json").read_text(encoding="utf-8"))
    raw["seconds_per_cohort"]=10.0
    raw.update(updates)
    return CalendarExperiment.model_validate(raw)


@pytest.mark.parametrize("end",[1080,1100,1140,True])
def test_unapproved_further_extensions_and_invalid_end_rejected(end):
    with pytest.raises(ValueError):experiment(experimental_end_minute=end)


def test_only_weekend_monday_window_changes_and_all_demand_is_identical(elective_fixture):
    original=snapshot(elective_fixture)
    before=original.enrolled.batch.configuration.model_dump()
    changed=extend_weekend_online_calendar(original,experiment())
    base,trial=original.enrolled.batch,changed.enrolled.batch
    for old,new in zip(base.config.days,trial.config.days):
        if old.day=="MONDAY":
            assert old.windows[Stream.WEEKEND][0].end_minute==1080
            assert new.windows[Stream.WEEKEND][0].end_minute==1110
            assert new.windows[Stream.WEEKDAY]==old.windows[Stream.WEEKDAY]
        else:assert old==new
    assert trial.prepared is base.prepared
    assert changed.enrolled.selections==original.enrolled.selections
    assert trial.config.delivery_calendar==base.config.delivery_calendar
    assert trial.configuration.calendar==base.configuration.calendar
    assert trial.solver_inputs["venue_input"] is base.solver_inputs["venue_input"]
    assert trial.solver_inputs["student_lunch_policy"] is base.solver_inputs["student_lunch_policy"]
    assert original.enrolled.batch.configuration.model_dump()==before
    assert trial.configuration.output_label=="PP1_EXPERIMENTAL_CALENDAR"


def test_generated_staff_test_availability_follows_only_extended_template(elective_fixture):
    original=snapshot(elective_fixture)
    base=original.enrolled.batch
    before=base.solver_inputs["staff_input"].model_dump()
    changed=extend_weekend_online_calendar(original,experiment()).enrolled.batch
    new=changed.solver_inputs["staff_input"]
    assert new.workload_input==base.solver_inputs["staff_input"].workload_input
    for old,trial in zip(base.solver_inputs["staff_input"].availability,new.availability):
        assert old.weekly_teaching_max_minutes==trial.weekly_teaching_max_minutes
        assert old.daily_teaching_max_minutes==trial.daily_teaching_max_minutes
        assert old.blocked_periods==trial.blocked_periods
        assert max(window.end_minute for window in trial.available_windows["MONDAY"])==1110
        assert {key:value for key,value in trial.available_windows.items() if key!="MONDAY"}=={key:value for key,value in old.available_windows.items() if key!="MONDAY"}
    assert base.solver_inputs["staff_input"].model_dump()==before


def test_supplied_staff_availability_is_never_extended(elective_fixture):
    original=snapshot(elective_fixture)
    batch=original.enrolled.batch
    configuration=batch.configuration.model_copy(deep=True)
    configuration.staff_input=batch.solver_inputs["staff_input"].model_copy(deep=True)
    original=replace(original,enrolled=replace(original.enrolled,batch=replace(batch,configuration=configuration)))
    changed=extend_weekend_online_calendar(original,experiment()).enrolled.batch
    assert changed.solver_inputs["staff_input"] is batch.solver_inputs["staff_input"]
    assert max(w.end_minute for w in changed.solver_inputs["staff_input"].availability[0].available_windows["MONDAY"])==1080
    assert changed.metadata["staff_availability_scope"]=="SUPPLIED_OVERRIDE_UNCHANGED"


def test_non_template_staff_calendar_is_not_silently_overwritten(elective_fixture):
    original=snapshot(elective_fixture)
    original.enrolled.batch.solver_inputs["staff_input"].availability[0].available_windows["MONDAY"]=[]
    with pytest.raises(ValueError,match="differs"):extend_weekend_online_calendar(original,experiment())


@pytest.mark.parametrize("change",["not_monday","physical","already_extended"])
def test_wrong_baselines_are_rejected_instead_of_replaced(elective_fixture,change):
    original=snapshot(elective_fixture)
    if change=="not_monday":
        with pytest.raises(ValueError,match="Monday"):extend_weekend_online_calendar(original,experiment(online_day="WEDNESDAY"))
    elif change=="already_extended":
        extended=extend_weekend_online_calendar(original,experiment())
        with pytest.raises(ValueError,match="original"):extend_weekend_online_calendar(extended,experiment())
    else:
        batch=original.enrolled.batch
        raw=batch.config.model_dump()
        rule=next(row for row in raw["delivery_calendar"]["days"] if row["day"]=="MONDAY" and row["stream"]==Stream.WEEKEND)
        rule["mode"]="PHYSICAL"
        config=SolverConfig.model_validate(raw)
        original=replace(original,enrolled=replace(original.enrolled,batch=replace(batch,config=config)))
        with pytest.raises(ValueError,match="lecture-only"):extend_weekend_online_calendar(original,experiment())


def test_half_hour_extension_adds_exactly_one_continuous_two_hour_slot():
    baseline=packing_batch()
    old=student_packing_audit(baseline)
    raw=baseline.config.model_dump()
    raw["days"][0]["windows"][Stream.WEEKEND][0]["end_minute"]=1110
    extended=packing_batch()
    extended.config=SolverConfig.model_validate(raw)
    new=student_packing_audit(extended)
    pick=lambda result:next(row for row in result["rows"] if row["audience"]=="ALL_KNOWN_ATTENDANCE" and row["minimum_duration_minutes"]==120)
    assert pick(old)["required_long_occurrences"]==pick(new)["required_long_occurrences"]==10
    assert pick(old)["optimistic_whole_occurrence_slots"]==9
    assert pick(new)["optimistic_whole_occurrence_slots"]==10
    assert old["status"]=="PROVEN_INFEASIBLE" and new["status"]=="NO_UPPER_BOUND_VIOLATION"


def test_possible_placements_preserve_lunch_modes_and_physical_windows(elective_fixture):
    enrolled=extend_weekend_online_calendar(snapshot(elective_fixture),experiment()).enrolled
    rows=placement_diagnostics(enrolled)["placements"]
    weekend_groups={row.group_id for row in enrolled.batch.plan.groups if row.stream==Stream.WEEKEND}
    rows=[row for row in rows if row["group_id"] in weekend_groups]
    assert any(row["day"]=="MONDAY" and row["start_time"]=="16:30" and row["end_time"]=="18:30" for row in rows)
    for row in rows:
        assert row["end_minute"]<=810 or row["start_minute"]>=870
        if row["day"]=="MONDAY":assert row["delivery_mode"]=="ONLINE" and row["session_type"]=="LECTURE"
        else:assert row["day"] in {"SATURDAY","SUNDAY"} and row["end_minute"]<=1050


def test_native_experimental_result_labels_and_exact_coverage(elective_fixture,tmp_path):
    trial=extend_weekend_online_calendar(snapshot(elective_fixture),experiment())
    enrolled=select_scenario(trial,SemesterScenario(name="SYN-SEMESTER",semesters=[1]),settings())
    batch=enrolled.batch
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    report=export_pp1(batch,result,tmp_path/"new-output")
    assert report["independently_validated"] and not report["complete_year4_academic_timetable_generated"]
    assert all(row["coverage"]=="EXACTLY_ONCE" for row in coverage_report(enrolled,result))
    for filename in ("timetable.json","solver-result.json","views.json","configuration.json"):
        assert json.loads((tmp_path/"new-output"/filename).read_text(encoding="utf-8"))["output_label"]=="PP1_EXPERIMENTAL_CALENDAR"
    assert 'class="label">PP1_EXPERIMENTAL_CALENDAR' in (tmp_path/"new-output/weekly.html").read_text(encoding="utf-8")
    assert "separate semester previews are alternatives" in (tmp_path/"new-output/weekly.html").read_text(encoding="utf-8")
    assert "EXPERIMENTAL_CALENDAR_RESULT" in (tmp_path/"new-output/timetable.html").read_text(encoding="utf-8")


def test_missing_duplicate_and_corrupted_occurrences_cannot_claim_complete(elective_fixture):
    enrolled=snapshot(elective_fixture).enrolled
    failure=SolverResult(SolverStatus.UNKNOWN,(),0,None,"synthetic")
    assert all(row["coverage"]=="UNSCHEDULED" for row in coverage_report(enrolled,failure))
    result=solve_timetable(enrolled.batch.prepared,enrolled.batch.config,**enrolled.batch.solver_inputs)
    with pytest.raises(ValueError,match="duplicated"):
        coverage_report(enrolled,replace(result,schedule=(*result.schedule,result.schedule[0])))
    original=result.schedule[0]
    changed=replace(original,session=replace(original.session,duration_minutes=original.session.duration_minutes+30))
    with pytest.raises(ValueError,match="metadata"):
        coverage_report(enrolled,replace(result,schedule=(changed,*result.schedule[1:])))


def test_legacy_html_label_is_unchanged():
    assert 'class="label">PP1_TEST_CALENDAR' in _page("Synthetic","")


def test_non_year4_scope_exports_explicitly_empty_elective_attendance_report(elective_fixture,tmp_path):
    source=extend_weekend_online_calendar(snapshot(elective_fixture),experiment())
    enrolled=select_scenario(source,SemesterScenario(name="year1-only",semesters=[1]),settings())
    enrolled=select_enrolled_scope(enrolled,{(1,1,"WEEKDAY","IT")})
    batch=enrolled.batch
    result=solve_timetable(batch.prepared,batch.config,**batch.solver_inputs)
    summary=export_scenario(enrolled,result,tmp_path/"only-year1",physical_venue_capacity_audit(batch))
    assert summary["independently_validated"]
    assert not summary["year4_academic_timetable_complete"]
    report=json.loads((tmp_path/"only-year1/run-report.json").read_text(encoding="utf-8"))
    assert not report["complete_year4_academic_timetable_generated"]
    assert len((tmp_path/"only-year1/student-attendance-demand.csv").read_text(encoding="utf-8").splitlines())==1
