"""Compulsory Research stays pending; explicit future sessions reuse the solver."""

from dataclasses import replace
import csv
import json

import pytest

from schedence_teaching.pilot.elective_config import ElectiveRunConfiguration
from schedence_teaching.pilot.elective_io import enrolled_demand_report, export_enrollment
from schedence_teaching.pilot.electives import (ElectiveAllocationPolicy, ModuleKind, confirmed_year4_candidate_catalog,
    generate_test_electives, select_enrolled_scope)
from schedence_teaching.pilot.elective_validation import validate_elective_result
from schedence_teaching.pilot.pp1_run import export_pp1
from schedence_teaching.pilot.research_requirements import (PENDING_RESEARCH, ResearchRequirementInput,
    ResearchSessionMapping, apply_research_requirements)
from schedence_teaching.solver import solve_timetable

from test_elective_enrollment import elective_fixture,enrolled_fixture


def pending(enrolled,original):
    return apply_research_requirements(enrolled,original,ResearchRequirementInput(policy_reference="synthetic:compulsory-research-pending"))


def supplied_input(base, *, stream="WEEKEND", frequency=2):
    cohorts=[row for row in base.plan.cohorts if row.academic_year==4 and row.stream.value==stream]
    assert len(cohorts)==1
    return ResearchRequirementInput(policy_reference="synthetic:explicit-research-contract",complete_cohort_ids={cohorts[0].cohort_id},
        session_mappings=[dict(reference="synthetic:research-session-evidence",cohort_ids=[cohorts[0].cohort_id],
            offering=dict(offering_id="SYN-RESEARCH-"+stream,module_code="SYN-RESEARCH",module_name="Synthetic Research",
                academic_year=4,semester=1,specialization="IT",degree_type="Synthetic",stream=stream),
            teaching_team_members=[dict(offering_id="SYN-RESEARCH-"+stream,staff_id="SYN-LEC-1",role="LIC")],
            patterns=[dict(session_type="WORKSHOP",duration_minutes=60,sessions_per_week=frequency,audience="MAIN")])])


def test_pending_research_never_creates_fake_modules_staff_or_hours(elective_fixture):
    original,*_=elective_fixture
    enrolled=enrolled_fixture(elective_fixture)
    before=enrolled.batch.prepared.input_snapshot.model_dump()
    with_research=pending(enrolled,original)
    assert with_research.batch.prepared.input_snapshot.model_dump()==before
    assert len(with_research.research_pending_cohort_ids)==2
    obligations=with_research.batch.metadata["research_obligations"]
    assert sum(row["student_count"] for row in obligations)==262
    assert all(row["status"]==PENDING_RESEARCH and row["compulsory"] and row["academic_work_hours"] is None and row["weekly_session_minutes_known"] is None for row in obligations)
    assert all(row["research_weekly_session_hours"] is None for row in enrolled_demand_report(with_research))
    assert not with_research.additional_student_requirements


def test_pending_research_allows_validated_elective_component_but_never_academic_completeness(elective_fixture,tmp_path):
    original,*_=elective_fixture
    enrolled=select_enrolled_scope(pending(enrolled_fixture(elective_fixture),original),{(4,1,"WEEKEND","IT")},max_time_seconds=10.0)
    enrolled=replace(enrolled,batch=replace(enrolled.batch,metadata=dict(enrolled.batch.metadata,
        output_component="YEAR4_ELECTIVE_COMPONENT",research_pending_cohort_count=len(enrolled.research_pending_cohort_ids))))
    result=solve_timetable(enrolled.batch.prepared,enrolled.batch.config,**enrolled.batch.solver_inputs)
    assert validate_elective_result(enrolled,result)["students_checked"]==131
    report=export_pp1(enrolled.batch,result,tmp_path/"preview")
    assert report["completion_status"]=="ELECTIVE_COMPONENT_COMPLETE"
    assert report["complete_input_component_generated"] and not report["complete_timetable_generated"]
    assert not report["complete_year4_academic_timetable_generated"]
    assert report["unscheduled_sessions"]==0
    assert PENDING_RESEARCH in (tmp_path/"preview/timetable.html").read_text(encoding="utf-8")
    assert "not a complete Year 4 academic timetable" in (tmp_path/"preview/weekly.html").read_text(encoding="utf-8")


def test_future_supplied_research_sessions_preserve_original_elective_requirements(elective_fixture):
    original,*_=elective_fixture
    enrolled=enrolled_fixture(elective_fixture)
    inputs=supplied_input(original)
    updated=apply_research_requirements(enrolled,original,inputs)
    old={row.occurrence_id:row for row in enrolled.batch.prepared.sessions}
    assert {row.occurrence_id:row for row in updated.batch.prepared.sessions if row.occurrence_id in old}==old
    assert len(updated.batch.prepared.sessions)==len(old)+4 # two main groups, twice weekly.
    assert len(updated.research_pending_cohort_ids)==1 # Weekday mapping remains absent.
    assert updated.batch.plan.summary()["total_students"]==327
    report=enrolled_demand_report(updated)
    assert all(row["research_weekly_session_hours"]==2 and 8<=row["corrected_attendance_hours"]<=10 for row in report if row["stream"]=="WEEKEND")
    assert all(row["research_weekly_session_hours"] is None for row in report if row["stream"]=="WEEKDAY")
    assert all(not row["academic_work_completed"] for row in updated.batch.metadata["research_obligations"])


def test_future_research_sessions_use_original_lunch_staff_venue_and_population_validator(elective_fixture,tmp_path):
    original,*_=elective_fixture
    updated=apply_research_requirements(enrolled_fixture(elective_fixture),original,supplied_input(original))
    scoped=select_enrolled_scope(updated,{(4,1,"WEEKEND","IT")},max_time_seconds=10.0)
    result=solve_timetable(scoped.batch.prepared,scoped.batch.config,**scoped.batch.solver_inputs)
    assert result.status.value in {"OPTIMAL","FEASIBLE"}
    assert validate_elective_result(scoped,result)["student_clashes"]==0
    research=[row for row in result.schedule if row.session.offering_id.startswith("SYN-RESEARCH")]
    assert len(research)==4 and all(row.assigned_staff_id=="SYN-LEC-1" for row in research)
    assert all(row.assigned_venue_id and row.day in {"SATURDAY","SUNDAY"} for row in research)
    assert not scoped.research_pending_cohort_ids
    export_enrollment(scoped,tmp_path/"enrollment")
    with (tmp_path/"enrollment/student-to-module-groups.csv").open(encoding="utf-8",newline="") as handle:
        rows=list(csv.DictReader(handle))
    assert len({row["student_id"] for row in rows if row["offering_id"]=="SYN-RESEARCH-WEEKEND"})==131


@pytest.mark.parametrize("damage",["unknown-cohort","wrong-stream","unknown-staff","existing-offering","false-complete"])
def test_invalid_or_unsupplied_research_mappings_cannot_silently_pass(elective_fixture,damage):
    original,*_=elective_fixture
    inputs=supplied_input(original).model_dump()
    if damage=="unknown-cohort":inputs["session_mappings"][0]["cohort_ids"]=["UNKNOWN"]
    if damage=="wrong-stream":inputs["session_mappings"][0]["offering"]["stream"]="WEEKDAY"
    if damage=="unknown-staff":inputs["session_mappings"][0]["teaching_team_members"][0]["staff_id"]="UNKNOWN"
    if damage=="existing-offering":
        identity=original.prepared.input_snapshot.module_offerings[0].offering_id
        inputs["session_mappings"][0]["offering"]["offering_id"]=identity
        inputs["session_mappings"][0]["teaching_team_members"][0]["offering_id"]=identity
    if damage=="false-complete":inputs["session_mappings"]=[]
    with pytest.raises(ValueError):
        apply_research_requirements(enrolled_fixture(elective_fixture),original,ResearchRequirementInput.model_validate(inputs))


def test_confirmed_candidate_catalog_keeps_explicit_compulsory_and_project_overrides(elective_fixture):
    _,source,classes,_=elective_fixture
    overrides=[row for row in classes if row.kind in {ModuleKind.COMPULSORY,ModuleKind.PROJECT}]
    classified=confirmed_year4_candidate_catalog(source,reference="synthetic:confirmed-candidate-rule",overrides=overrides)
    assert [row.kind.value for row in classified].count("ELECTIVE")==3
    assert [row.kind.value for row in classified].count("COMPULSORY")==1
    assert [row.kind.value for row in classified].count("PROJECT")==1


def test_balanced_test_pairs_are_reproducible_and_cover_every_candidate_with_registrations(elective_fixture):
    base,source,classes,policy=elective_fixture
    policy=policy.model_copy(update=dict(selection_strategy="BALANCED_TEST_PAIRS"))
    rows=generate_test_electives(base,source,classes,policy)
    assert rows==generate_test_electives(base,source,classes,policy)
    for cohort in {row.cohort_id for row in rows}:
        selected=[row for row in rows if row.cohort_id==cohort]
        assert len({identity for row in selected for identity in row.offering_ids})==3
        assert all(len(set(row.offering_ids))==2 for row in selected)
        counts=[sum(row.offering_ids==pair for row in selected) for pair in {row.offering_ids for row in selected}]
        assert max(counts)-min(counts)<=1


def test_confirmed_settings_keep_both_online_windows_editable_and_research_pending():
    from pathlib import Path
    file=Path(__file__).resolve().parents[1]/"examples/pp1-elective-test-settings.json"
    settings=ElectiveRunConfiguration.model_validate_json(file.read_text(encoding="utf-8"))
    assert settings.weekday_online_day=="WEDNESDAY" and settings.weekend_online_day=="MONDAY"
    assert settings.weekday_online_window.start_minute==510 and settings.weekday_online_window.end_minute==1080
    assert settings.weekend_online_window==settings.weekday_online_window
    assert len(settings.blocked_scopes)==2
    assert all(row.specialization=="DS" and row.semester==1 and row.academic_year==4 for row in settings.blocked_scopes)
    assert not settings.research.session_mappings and not settings.research.complete_cohort_ids
