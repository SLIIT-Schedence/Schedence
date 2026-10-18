"""Two synthetic institutions; no production accounts, source files or database."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schedence_teaching.policy import PolicyApproval, PolicyRevision, StaffPolicyOverride, ResolvedInstitutionPolicy
from schedence_teaching.persistence.policy import resolve_snapshot_policy, verify_snapshot_policy, version_policy_metadata
from schedence_teaching.persistence.snapshots import (canonical_json, content_digest, prepare_attempt,
    replay_snapshot, restore_snapshot, snapshot_payload, LegacyPolicySnapshotError)
from schedence_teaching.persistence.retrieval import assemble_timetable
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.reporting.contracts import WorkloadQuery
from schedence_teaching.reporting.workload import lecturer_workload
from schedence_teaching.solver import solve_timetable, SolverStatus, SolverInputError, validate_staff_policy_result
from schedence_teaching.solver.attendance_inputs import DeliveryCalendar
from schedence_teaching.lunch import StudentLunchPolicy
from schedence_teaching.schemas import SessionType
from schedence_teaching.solver import StudentLunchEnforcementConfig, TimeWindow
from test_teaching_assignment_policy import example
from test_persistence_contracts import snapshot_for, TENANT, OTHER_TENANT
from test_timetable_versioning import record_for


def approval():
    return PolicyApproval(approved_by_reference='SYNTHETIC:CONFIGURATION_OWNER',
        authorization_reference='SYNTHETIC:EXPLICIT_POLICY_EDIT_PERMISSION_CHECK',
        approval_reference='SYNTHETIC:APPROVED_REVISION', approved_at=datetime(2026,10,18,tzinfo=timezone.utc))


def revision(document, **updates):
    return PolicyRevision(**(dict(institution_id=document.institution_id,
        policy_reference='SYNTHETIC:INSTITUTION-POLICY', revision='v1',
        effective_from=document.calendar.week_start, effective_until=document.calendar.week_end,
        approval=approval()) | updates))


def document(*, kind='LAB', tenant=TENANT, other_rules=False, online=False, lunch=False):
    p,c,staff = example('INSTRUCTOR', kind, permission=False)
    if other_rules:
        staff.assignment_policy.rank_permissions['INSTRUCTOR'] = {SessionType.LECTURE, SessionType.TUTORIAL, SessionType.LAB}
        staff.assignment_policy.institution_teaching_day_limit = 4
        staff.workload_input.policy.band_rules['INSTRUCTOR'].weekly_target_minutes = 600
    if online:
        c.delivery_calendar = DeliveryCalendar(reference='SYNTHETIC:MODE-CONFIG', days=[dict(day='DAY',
            stream='WEEKDAY', mode='ONLINE', allowed_session_types={kind},
            mode_policy_reference='SYNTHETIC:INSTITUTION-POLICY')])
    contexts = dict(staff_input=staff)
    if not online:
        from test_venue_solver import physical
        rooms=physical([('SYNTHETIC-ROOM',30,'Computer Lab' if kind=='LAB' else 'Lecture Hall')])
        rooms.calendars[0].available_windows={'DAY':[TimeWindow(start_minute=0,end_minute=300)]}
        contexts['venue_input']=rooms
    doc = snapshot_for(p,c,contexts=contexts)
    doc.institution_id = tenant
    if lunch:
        doc.contexts.student_lunch_policy = StudentLunchPolicy(policy_reference='SYNTHETIC:LUNCH',
            week_reference=doc.calendar.week_reference, provenance=dict(source='SYNTHETIC',reference='APPROVED'),
            period_confirmation='CONFIRMED', configured_academic_years={2},
            year_periods=[dict(academic_year=2, duration_minutes=45, window=dict(start_minute=0,end_minute=45))],
            applicable_dates=set(doc.calendar.day_dates.values()), applicable_streams={'WEEKDAY'},
            applicability_reference='SYNTHETIC:EXPLICIT-APPLICABILITY')
        doc.contexts.student_lunch_config = StudentLunchEnforcementConfig(applicability_confirmation='CONFIRMED',
            applicability_confirmation_reference='SYNTHETIC:LUNCH-APPROVAL')
        doc = type(doc).model_validate(doc.model_dump(round_trip=True))
    return doc


def run_bound(doc, **updates):
    bound = resolve_snapshot_policy(doc, revision(doc), **updates)
    _,p,c,ctx = replay_snapshot(bound)
    result = solve_timetable(p,c,**ctx)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        prepare_attempt(bound,result)
    return bound,result,(p,c,ctx)


def test_two_institutions_have_distinct_rank_target_and_teaching_day_policies():
    initial,first,_ = run_bound(document(kind='LECTURE'))
    second,result,_ = run_bound(document(kind='LECTURE', tenant=OTHER_TENANT, other_rules=True))
    assert first.status is SolverStatus.INFEASIBLE
    assert result.status is SolverStatus.OPTIMAL
    assert result.workload_report.staff[0].policy.required_teaching_minutes == 600
    assert second.contexts.staff_input.assignment_policy.institution_teaching_day_limit == 4
    assert initial.contexts.staff_input.assignment_policy.institution_teaching_day_limit == 6
    assert initial.solver_config.resolved_policy.digest != second.solver_config.resolved_policy.digest


@pytest.mark.parametrize('kind',['LECTURE','TUTORIAL','WORKSHOP'])
def test_initial_university_still_forbids_instructor_non_lab(kind):
    _,result,_ = run_bound(document(kind=kind))
    assert result.status is SolverStatus.INFEASIBLE


def test_institution_lunch_is_fixed_45_minutes_for_its_explicit_study_year():
    bound,result,_ = run_bound(document(lunch=True, online=True))
    assert result.status is SolverStatus.OPTIMAL
    assert result.schedule[0].start_minute >= 45
    assert result.student_lunch_report.assessment.violation_count == 0
    assert bound.contexts.student_lunch_policy.configured_academic_years == {2}


def test_generic_lunch_contract_can_describe_a_longer_degree_without_changing_academic_records():
    doc=document(lunch=True)
    data=doc.contexts.student_lunch_policy.model_dump(round_trip=True)
    data['configured_academic_years']={6};data['year_periods'][0]['academic_year']=6
    policy=StudentLunchPolicy.model_validate(data)
    assert policy.year_periods[0].academic_year == 6
    # Existing Academic Data input contracts still retain their reviewed year range.
    assert doc.teaching_input.student_groups[0].academic_year == 2


def test_lunch_requires_periods_for_every_attending_year():
    doc=document(lunch=True)
    doc.contexts.student_lunch_policy.configured_academic_years=frozenset({1})
    doc.contexts.student_lunch_policy.year_periods[0].academic_year=1
    with pytest.raises(ValueError,match='attending study year'):
        resolve_snapshot_policy(doc,revision(doc))


@pytest.mark.parametrize('kind',['LAB','TUTORIAL'])
def test_explicit_other_institution_can_allow_online_session_types(kind):
    _,result,_=run_bound(document(kind=kind,other_rules=True,online=True,tenant=OTHER_TENANT))
    assert result.status is SolverStatus.OPTIMAL
    assert result.schedule[0].delivery_mode.value == 'ONLINE'
    assert result.schedule[0].assigned_venue_id is None


def test_unsealed_custom_online_permission_is_rejected():
    doc=document(online=True)
    from schedence_teaching.preparation import prepare_timetable_input
    with pytest.raises(SolverInputError,match='resolved institution'):
        solve_timetable(prepare_timetable_input(doc.teaching_input),doc.solver_config,staff_input=doc.contexts.staff_input)


def override(doc, **changes):
    return StaffPolicyOverride(staff_id='STAFF', effective_from=doc.calendar.week_start,
        effective_until=doc.calendar.week_end, approval=approval(), **changes)


def test_approved_staff_override_is_separate_from_rank_rules_and_soft_target_not_cap():
    doc=document(online=True)
    bound,result,_=run_bound(doc,staff_overrides=[override(doc,weekly_teaching_target_minutes=1500,
        maximum_teaching_days=4,weekly_teaching_max_minutes=1800,session_types={'LAB'})])
    assert result.status is SolverStatus.OPTIMAL
    row=result.workload_report.staff[0]
    assert row.policy.required_teaching_minutes == 1500
    assert row.policy.rule_source == 'STAFF_OVERRIDE'
    staff=bound.contexts.staff_input
    assert staff.workload_input.policy.band_rules['INSTRUCTOR'].weekly_target_minutes == 1200
    assert staff.availability[0].weekly_teaching_max_minutes == 1800
    assert staff.assignment_policy.staff_teaching_day_limits['STAFF'] == 4
    assert json.loads(bound.solver_config.resolved_policy.staff_overrides_json)[0]['approval']['approval_reference']
    assert not doc.contexts.staff_input.workload_input.policy.staff_overrides


@pytest.mark.parametrize('failure',['unknown','day_ceiling','eligibility','partial_week','duplicate'])
def test_staff_overrides_cannot_infer_ranks_or_broaden_unapproved_eligibility(failure):
    doc=document()
    item=override(doc,weekly_teaching_target_minutes=1300)
    if failure=='unknown':item.staff_id='NOT-A-STAFF-RECORD'
    if failure=='day_ceiling':item.maximum_teaching_days=7
    if failure=='eligibility':item.session_types={SessionType.LECTURE}
    if failure=='partial_week':item.effective_from+=timedelta(days=1)
    with pytest.raises(ValueError):
        resolve_snapshot_policy(doc,revision(doc),staff_overrides=[item,item] if failure=='duplicate' else [item])


def test_seal_is_deeply_immutable_and_detached_decoding_cannot_mutate_it():
    bound,_,_=run_bound(document(online=True))
    seal=bound.solver_config.resolved_policy
    with pytest.raises(ValidationError):seal.revision='MUTATED'
    decoded=json.loads(seal.effective_json);decoded['staff_input']['assignment_policy']['rank_permissions']={}
    assert json.loads(seal.effective_json)['staff_input']['assignment_policy']['rank_permissions']
    with pytest.raises(ValidationError):seal.model_copy(update={'revision':'MUTATED'}).verify()


@pytest.mark.parametrize('change',['rank','target','days','window','digest','institution'])
def test_mutated_runtime_or_snapshot_policy_is_rejected(change):
    bound,_,(p,c,ctx)=run_bound(document(online=True))
    if change=='rank':ctx['staff_input'].assignment_policy.rank_permissions['INSTRUCTOR']={SessionType.LECTURE}
    if change=='target':ctx['staff_input'].workload_input.policy.band_rules['INSTRUCTOR'].weekly_target_minutes=99
    if change=='days':ctx['staff_input'].assignment_policy.institution_teaching_day_limit=7
    if change=='window':c.days[0].windows[next(iter(c.days[0].windows))][0].end_minute=1440
    if change=='digest':object.__setattr__(c.resolved_policy,'digest','0'*64)
    if change=='institution':
        bound.institution_id=OTHER_TENANT
        with pytest.raises(ValueError,match='institution'):verify_snapshot_policy(bound)
        return
    with pytest.raises(ValueError):solve_timetable(p,c,**ctx)


def test_independent_validator_rejects_staff_policy_tampering():
    _,result,(p,c,ctx)=run_bound(document(online=True))
    ctx['staff_input'].assignment_policy.rank_permissions['INSTRUCTOR']={SessionType.LECTURE}
    with pytest.raises(ValueError,match='resolved institution policy'):
        validate_staff_policy_result(p,c,result,staff_input=ctx['staff_input'])


def test_omitting_sealed_hard_context_is_not_allowed_at_solver_entry():
    _,_,(p,c,ctx)=run_bound(document(lunch=True,online=True))
    ctx.pop('student_lunch_policy');ctx.pop('student_lunch_config');ctx.pop('student_lunch_calendar')
    with pytest.raises(SolverInputError,match='resolved institution policy'):solve_timetable(p,c,**ctx)


def test_full_independent_validation_cannot_strip_lunch_mode_to_skip_its_policy():
    from schedence_teaching.solver.evaluation import validate_evaluation_result
    _,result,(p,c,ctx)=run_bound(document(lunch=True,online=True))
    projected=replace(result,student_lunch_enabled=False,student_lunch_report=None)
    ctx.pop('student_lunch_policy');ctx.pop('student_lunch_config');ctx.pop('student_lunch_calendar')
    with pytest.raises(SolverInputError,match='resolved institution policy'):
        validate_evaluation_result(p,c,projected,**ctx)


def test_descriptive_lunch_assessment_rejects_a_different_positional_policy():
    from schedence_teaching.lunch import assess_student_lunch
    _,result,(p,c,ctx)=run_bound(document(lunch=True,online=True))
    other=ctx['student_lunch_policy'].model_copy(deep=True)
    other.year_periods[0].window.start_minute=60
    other.year_periods[0].window.end_minute=105
    with pytest.raises(ValueError,match='resolved institution policy'):
        assess_student_lunch(p,c,result,ctx['student_lunch_calendar'],other,**ctx)


def test_canonical_digest_roundtrip_and_operational_budget_changes():
    doc=document(online=True)
    first=resolve_snapshot_policy(doc,revision(doc));second=resolve_snapshot_policy(doc,revision(doc))
    assert first.solver_config.resolved_policy.digest==second.solver_config.resolved_policy.digest
    restored=restore_snapshot(snapshot_payload(first))
    assert content_digest(restored)==content_digest(first)
    _,p,c,ctx=replay_snapshot(restored)
    c=c.model_copy(update={'max_time_seconds':1.0,'num_search_workers':1})
    assert solve_timetable(p,c,**ctx).status is SolverStatus.OPTIMAL
    attempt=prepare_attempt(first,solve_timetable(p,c,**ctx))
    assert json.loads(attempt.validation_json)['resolved_policy_digest']==first.solver_config.resolved_policy.digest


def test_wrong_institution_effective_dates_or_naive_approval_are_rejected():
    doc=document()
    with pytest.raises(ValueError):resolve_snapshot_policy(doc,revision(doc,institution_id=OTHER_TENANT))
    with pytest.raises(ValueError):resolve_snapshot_policy(doc,revision(doc,effective_from=doc.calendar.week_end))
    data=approval().model_dump();data['approved_at']=datetime(2026,10,18)
    with pytest.raises(ValidationError):PolicyApproval.model_validate(data)


def test_legacy_incomplete_diagnostic_never_receives_invented_defaults():
    doc=document();payload=snapshot_payload(doc)
    raw=json.loads(payload['document_json'])
    del raw['contexts']['staff_input']['assignment_policy']['appointment_permissions']
    original=canonical_json(raw)
    with pytest.raises(LegacyPolicySnapshotError,match='No defaults were invented'):
        restore_snapshot({'document_json':original})
    assert canonical_json(raw)==original
    # Genuine historical snapshots without assignment policy still round-trip.
    doc.contexts.staff_input.assignment_policy=None
    assert content_digest(restore_snapshot(snapshot_payload(doc)))==content_digest(doc)


def test_removing_snapshot_policy_seal_is_rejected():
    bound,_,_=run_bound(document(online=True))
    bound.solver_config.resolved_policy=None
    with pytest.raises(ValueError,match='seal is missing'):verify_snapshot_policy(bound)


def test_binding_revision_preserves_parent_policy_provenance():
    first,_,_=run_bound(document(online=True))
    second=resolve_snapshot_policy(first,revision(first,revision='v2'))
    provenance=json.loads(second.solver_config.resolved_policy.defaults_json)
    assert provenance['parent_resolved_policy_digest']==first.resolved_policy_digest
    assert provenance['parent_snapshot_digest']==content_digest(first)
    assert second.resolved_policy_digest!=first.resolved_policy_digest


def saved_policy_record():
    bound,result,_=run_bound(document(online=True))
    doc,version,run,report,bookings,coverage=record_for(bound,result)
    version.update(id=uuid4(),series_id=uuid4(),version_number=1,academic_complete=False)
    run.update(snapshot_id=uuid4(),solver_status=result.status.value,runtime_seconds=result.runtime_seconds)
    metadata=run['result_metadata']['_teaching_persistence']
    metadata.update(version_policy_metadata(doc),snapshot_sha256=content_digest(doc))
    report['evidence']['_teaching_persistence']=deepcopy(metadata)
    return assemble_timetable(doc,version,run,report,bookings,coverage)


def test_reporting_uses_the_exact_version_policy_digest():
    import csv
    from io import StringIO
    from schedence_teaching.reporting.exports import workload_csv
    record=saved_policy_record()
    report=lecturer_workload(record,WorkloadQuery(version_id=record.version['id']))
    assert report['resolved_policy_digest']==record.document.solver_config.resolved_policy.digest
    assert report['institution_policy_revision']=='v1'
    exported=list(csv.DictReader(StringIO(workload_csv(report).decode('utf-8-sig'))))
    assert exported[0]['resolved_policy_digest']==report['resolved_policy_digest']
    assert exported[0]['institution_policy_revision']=='v1'


@pytest.mark.parametrize('key',['resolved_policy_digest','institution_policy_revision','institution_policy_reference'])
def test_reporting_rejects_version_policy_metadata_tampering(key):
    record=saved_policy_record();run=deepcopy(record.run)
    run['result_metadata']['_teaching_persistence'][key]='CORRUPT'
    with pytest.raises(PersistenceConflict):
        lecturer_workload(replace(record,run=run),WorkloadQuery(version_id=record.version['id']))


def test_bound_policy_preserves_combination_preferences_fairness_and_protected_periods():
    from test_scheduling_integration import build_case
    from schedence_teaching.persistence.contracts import SchedulingContexts
    from schedence_teaching.solver import CombinationSolverConfig
    from schedence_teaching.solver.teaching_policy import TeachingAssignmentPolicy
    case=build_case(administrative_mode='flexible',time_limit=10.0)
    original=case['inputs']
    staff=original['staff_input']
    # Explicit synthetic institution rules, not the initial campus preset.
    staff.assignment_policy=TeachingAssignmentPolicy(policy_reference='SYNTHETIC:JOINT-INSTITUTION',
        week_reference=staff.workload_input.week_reference,evidence_reference='SYNTHETIC:APPROVED',
        rank_permissions={p.workload_band:({SessionType.LAB} if p.workload_band.value=='INSTRUCTOR' else set(SessionType))
                          for p in staff.workload_input.profiles},appointment_permissions={},institution_teaching_day_limit=7)
    raw={key:value for key,value in original.items() if key in SchedulingContexts.model_fields}
    raw.update(combination_input=original['combination_input'].input_snapshot,
        work_period_input=original['work_periods'].input_snapshot,
        preference_input=original['preference_input'].input_snapshot)
    raw.setdefault('combination_config',CombinationSolverConfig())
    source=snapshot_for(case['prepared'],case['config'],contexts=raw,calendar=original['consecutive_calendar'])
    bound=resolve_snapshot_policy(source,revision(source))
    _,p,c,ctx=replay_snapshot(bound)
    result=solve_timetable(p,c,**ctx)
    assert result.status in {SolverStatus.OPTIMAL,SolverStatus.FEASIBLE}
    attempt=prepare_attempt(bound,result)
    assert attempt.scheduled_occurrences==attempt.expected_occurrences==7
    assert result.combination_enabled and result.work_period_enabled
    assert result.preference_optimization_enabled and result.fairness_optimization_enabled
    assert result.consecutive_optimization_enabled and result.administrative_schedule


def test_bound_policy_adds_neither_a_four_module_maximum_nor_a_twenty_hour_cap():
    from test_instructor_multiple_modules import lab_case
    from test_venue_solver import physical
    p,c,staff=lab_case(5)
    rooms=physical([('SYNTHETIC-LAB',30,'Computer Lab')])
    rooms.calendars[0].available_windows={day:[TimeWindow(start_minute=0,end_minute=360)] for day in staff.day_dates}
    source=snapshot_for(p,c,contexts={'staff_input':staff,'venue_input':rooms})
    bound=resolve_snapshot_policy(source,revision(source))
    _,p,c,ctx=replay_snapshot(bound)
    result=solve_timetable(p,c,**ctx)
    assert result.status in {SolverStatus.OPTIMAL,SolverStatus.FEASIBLE}
    assert len({s.session.offering_id for s in result.schedule})==5 and len(result.schedule)==11
    assert result.workload_report.staff[0].scheduled_teaching_minutes==1320
    assert result.workload_report.staff[0].policy.required_teaching_minutes==1200
    assert prepare_attempt(bound,result).scheduled_occurrences==11
