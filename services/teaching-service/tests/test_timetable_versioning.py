"""Lifecycle, save identity and detached retrieval using synthetic contracts."""

from copy import deepcopy
import json
from uuid import uuid4

import pytest

from schedence_teaching.persistence.lifecycle import TimetableStatus, academic_limitations, check_transition, draft_status
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.retrieval import assemble_timetable
from schedence_teaching.persistence.snapshots import content_digest, prepare_attempt
from schedence_teaching.persistence.versioning import METADATA_KEY, delivery_digest, run_identity

from test_persistence_contracts import example, snapshot_for


def record_for(document, result):
    plan=prepare_attempt(document,result)
    rows=json.loads(plan.deliveries_json)
    metadata=dict(logical_status="VALIDATED",normalized_deliveries_sha256=delivery_digest(rows))
    run=dict(solver_success=True,expected_occurrences=plan.expected_occurrences,
        scheduled_occurrences=plan.scheduled_occurrences,result_metadata={METADATA_KEY:metadata})
    report=dict(passed=True,hard_violations=0,expected_occurrences=plan.expected_occurrences,
        checked_occurrences=plan.scheduled_occurrences,evidence={METADATA_KEY:deepcopy(metadata)})
    version=dict(solver_success=True,validation_passed=True,lifecycle_status="DRAFT_VALIDATED")
    bookings,coverage=[],[]
    for row in rows:
        identifier=uuid4()
        coverage.extend(dict(item,delivery_id=identifier) for item in row.pop("coverage"))
        bookings.append(dict(row,id=identifier))
    return document,version,run,report,tuple(bookings),tuple(coverage)


def test_save_identity_ignores_runtime_order_and_attached_summary_but_keeps_policy_and_bookings():
    document,result=example()
    first=result.to_dict();second=deepcopy(first)
    second["runtime_seconds"]+=10
    second["constraint_validation"]={"untrusted":True}
    second["schedule"].reverse()
    digest=content_digest(document)
    assert run_identity(digest,first,"VALIDATED")==run_identity(digest,second,"VALIDATED")
    assert run_identity(digest,first,"DRAFT")!=run_identity(digest,first,"VALIDATED")
    assert run_identity("f"*64,first,"VALIDATED")!=run_identity(digest,first,"VALIDATED")
    second["schedule"][0]["assigned_staff_id"]="DIFFERENT"
    assert run_identity(digest,first,"VALIDATED")!=run_identity(digest,second,"VALIDATED")


def test_identity_normalizes_preference_observation_order_without_reordering_objective_priority():
    document,result=example()
    original=result.to_dict()
    original['preference_optimization']=dict(priority_order=['DELIVERIES','PREFERENCE_PENALTY'],
        score=dict(evaluations=[dict(preference_id='SYN-A',penalty=1),dict(preference_id='SYN-B',penalty=0)]),
        modeled_evaluations=[dict(preference_id='SYN-B'),dict(preference_id='SYN-A')])
    changed=deepcopy(original)
    changed['preference_optimization']['score']['evaluations'].reverse()
    changed['preference_optimization']['modeled_evaluations'].reverse()
    digest=content_digest(document)
    assert run_identity(digest,original,'VALIDATED')==run_identity(digest,changed,'VALIDATED')
    changed['preference_optimization']['priority_order'].reverse()
    assert run_identity(digest,original,'VALIDATED')!=run_identity(digest,changed,'VALIDATED')


@pytest.mark.parametrize("target",["DRAFT","VALIDATED"])
def test_only_review_stage_statuses_are_saveable(target):
    assert draft_status(target).value==target


@pytest.mark.parametrize("target",["PENDING_APPROVAL","APPROVED","PUBLISHED","SUPERSEDED"])
def test_saves_cannot_approve_or_publish(target):
    with pytest.raises(PersistenceConflict):draft_status(target)


@pytest.mark.parametrize("current,target",[("DRAFT","VALIDATED"),("VALIDATED","PENDING_APPROVAL"),
    ("PENDING_APPROVAL","APPROVED"),("APPROVED","PUBLISHED"),("PUBLISHED","SUPERSEDED")])
def test_lifecycle_checks_prepare_explicit_authorized_transitions(current,target):
    assert check_transition(current,target,academic_complete=True,limitations={},approval_authorized=True)==TimetableStatus(target)


@pytest.mark.parametrize("limitation",["research_pending","blocked_scopes","experimental_calendar","testing_inputs"])
def test_academic_and_experimental_limits_prevent_publication(limitation):
    with pytest.raises(PersistenceConflict):check_transition("APPROVED","PUBLISHED",academic_complete=True,
        limitations={limitation:True},approval_authorized=True)


@pytest.mark.parametrize("current,target,complete,authorized",[("DRAFT","PUBLISHED",True,True),
    ("APPROVED","PUBLISHED",False,True),("PENDING_APPROVAL","APPROVED",True,False),
    ("PUBLISHED","SUPERSEDED",True,False),("VALIDATED","DRAFT",True,True)])
def test_invalid_or_unauthorized_lifecycle_actions_fail_closed(current,target,complete,authorized):
    with pytest.raises(PersistenceConflict):check_transition(current,target,academic_complete=complete,
        limitations={},approval_authorized=authorized)


@pytest.mark.parametrize("mode",["PHYSICAL","ONLINE"])
def test_detached_retrieval_keeps_booking_mode_original_coverage_and_views(mode):
    document,result=example(mode)
    record=assemble_timetable(*record_for(document,result))
    assert len(record.deliveries)==len(record.coverage)==2
    assert all(row["delivery_mode"]==mode for row in record.deliveries)
    assert record.coverage_report()["unscheduled_occurrences"]==0
    assert len(record.specialization_timetable("IT"))==2
    assert len(record.student_group_timetable("A"))==1
    assert len(record.lecturer_timetable("SYN-A"))==1
    assert record.limitations["research_pending"] and not record.limitations["academic_complete"]
    with pytest.raises(PersistenceConflict):record.student_group_timetable("UNKNOWN")
    with pytest.raises(ValueError):record.venue_timetable(None)


@pytest.mark.parametrize("damage",["missing_coverage","duplicate_coverage","wrong_requirement","missing_delivery",
    "changed_time","changed_staff","changed_report","unpassed"])
def test_corrupt_retrieval_is_rejected(damage):
    document,version,run,report,bookings,coverage=record_for(*example())
    if damage=="missing_coverage":coverage=coverage[:-1]
    elif damage=="duplicate_coverage":coverage=(*coverage,coverage[0])
    elif damage=="wrong_requirement":coverage[0]["requirement_reference"]="UNKNOWN"
    elif damage=="missing_delivery":bookings=bookings[:-1]
    elif damage=="changed_time":bookings[0]["end_minute"]+=30
    elif damage=="changed_staff":bookings[0]["staff_reference"]="OTHER"
    elif damage=="changed_report":report["evidence"][METADATA_KEY]["logical_status"]="DRAFT"
    elif damage=="unpassed":report["passed"]=False
    with pytest.raises(PersistenceConflict):assemble_timetable(document,version,run,report,bookings,coverage)


@pytest.mark.parametrize("mixed",[False,True])
def test_combined_retrieval_counts_one_booking_for_each_actual_delivery(mixed):
    from test_combination_solver import inputs,venues
    from test_staff_solver import windows
    from schedence_teaching.preparation import prepare_group_combinations
    from schedence_teaching.solver import CombinationSolverConfig,solve_timetable
    source=inputs();prepared=prepare_group_combinations(source)
    config,rooms,options=windows(120),venues(large_minutes=30) if mixed else venues(),CombinationSolverConfig()
    document=snapshot_for(prepared.prepared,config,specializations=source.group_specializations,
        contexts=dict(venue_input=rooms,combination_input=source,combination_config=options))
    result=solve_timetable(prepared.prepared,config,venue_input=rooms,combination_input=prepared,combination_config=options)
    record=assemble_timetable(*record_for(document,result))
    assert len(record.deliveries)==(3 if mixed else 2) and len(record.coverage)==4
    specialization=next(iter(source.group_specializations.values()))
    assert len(record.specialization_timetable(specialization))==len(record.deliveries)
    assert record.coverage_report()["actual_deliveries"]==len(result.schedule)


def test_group_view_preserves_supplied_elective_population_overlap():
    from test_online_attendance_solver import problem
    from schedence_teaching.solver import solve_timetable
    prepared,config,venues=problem(shared_students=True,end=630)
    document=snapshot_for(prepared,config,contexts={"venue_input":venues})
    result=solve_timetable(prepared,config,venue_input=venues)
    record=assemble_timetable(*record_for(document,result))
    assert len(record.student_group_timetable("A"))==2


def test_group_view_includes_ancestor_and_subgroup_attendance():
    from test_online_attendance_solver import problem
    from schedence_teaching.preparation import prepare_timetable_input
    from schedence_teaching.solver import solve_timetable
    prepared,config,venues=problem(end=630)
    teaching=prepared.input_snapshot.model_copy(deep=True)
    teaching.student_groups[1].parent_group_id="A"
    prepared=prepare_timetable_input(teaching)
    config=config.model_copy(update=dict(attendance_memberships=(),attendance_required_group_ids=frozenset()))
    document=snapshot_for(prepared,config,contexts={"venue_input":venues})
    result=solve_timetable(prepared,config,venue_input=venues)
    record=assemble_timetable(*record_for(document,result))
    assert len(record.student_group_timetable("A"))==2
    assert len(record.student_group_timetable("B"))==2


def test_legacy_draft_has_no_claim_of_full_academic_completion():
    args=list(record_for(*example()))
    args[2]["result_metadata"]={}
    args[3]["evidence"]={}
    record=assemble_timetable(*args)
    assert record.logical_status=="VALIDATED"
    assert record.limitations==academic_limitations(args[0])
    assert record.coverage_report()["academic_complete"] is False
