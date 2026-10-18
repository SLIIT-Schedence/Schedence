"""Confirmed restrictions use synthetic records, never real source edits."""
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import TeachingTimetableInput, SessionType
from schedence_teaching.solver import solve_timetable,SolverStatus,validate_timetable,TimetableValidationError,SolverInputError
from schedence_teaching.solver.teaching_policy import TeachingAssignmentPolicy,assignment_issues
from schedence_teaching.reporting.teaching_audit import candidate_shortages
from schedence_teaching.workload.teaching_presets import initial_university_assignment_policy
from test_staff_solver import demand
from test_work_period_solver import scenario
from test_fairness_solver import allocation_data,context


def policy(staff,**updates):
    return initial_university_assignment_policy(policy_reference='SYNTHETIC:CONFIRMED-RULE-REGRESSION',
        evidence_reference='SYNTHETIC:USER-POLICY',week_reference=staff.workload_input.week_reference,
        require_target_optimization=False,**updates)


def example(band='LECTURER',kind='LECTURE',appointment=None,permission=True):
    p=demand({'OFFER':[('STAFF','TEAM_MEMBER')]},staff_types={'STAFF':'INSTRUCTOR'} if band=='INSTRUCTOR' else None)
    data=p.input_snapshot.model_dump()
    data['session_requirements'][0]['session_type']=kind
    p=prepare_timetable_input(TeachingTimetableInput.model_validate(data))
    p,c,s,*_=scenario(prepared=p,band=band,appointment_name=appointment)
    permissions=[dict(staff_id='STAFF',session_types=[kind],effective_from=s.policy_effective_from,
        effective_until=s.policy_effective_until,evidence_reference='SYNTHETIC:SESSION-GRANT')] if permission else []
    s.assignment_policy=policy(s,permissions=permissions)
    return p,c,s


@pytest.mark.parametrize('kind',['LECTURE','TUTORIAL','WORKSHOP'])
def test_instructor_non_lab_removed_from_solver_domain(kind):
    p,c,s=example('INSTRUCTOR',kind)
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.INFEASIBLE and not result.schedule
    assert 'rank_session_type_forbidden' in assignment_issues(p.input_snapshot,s,s.assignment_policy,'STAFF',p.sessions[0],next(iter(s.day_dates.values())))


def test_instructor_lab_permitted_when_module_eligible():
    p,c,s=example('INSTRUCTOR','LAB')
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule)==1
    assert result.workload_report.staff[0].policy.required_teaching_minutes==1200
    assert 'rank_appointment_session_eligibility' in result.validation.checks


@pytest.mark.parametrize('band',['SENIOR_LECTURER','DR','ASSISTANT_PROFESSOR','PROFESSOR'])
@pytest.mark.parametrize('kind',['TUTORIAL','WORKSHOP','LAB'])
def test_restricted_rank_activities(band,kind):
    p,c,s=example(band,kind)
    result=solve_timetable(p,c,staff_input=s)
    allowed=band=='SENIOR_LECTURER' and kind!='LAB'
    assert (result.status is SolverStatus.OPTIMAL)==allowed


@pytest.mark.parametrize('appointment',['HOD','DEAN'])
@pytest.mark.parametrize('kind',['LECTURE','TUTORIAL','WORKSHOP','LAB'])
def test_effective_appointment_requires_lectures_only(appointment,kind):
    p,c,s=example('LECTURER',kind,appointment)
    result=solve_timetable(p,c,staff_input=s)
    assert (result.status is SolverStatus.OPTIMAL)==(kind=='LECTURE')
    if kind=='LECTURE':assert result.workload_report.staff[0].policy.required_teaching_minutes==240


@pytest.mark.parametrize('band',['LECTURER','ASSISTANT_LECTURER'])
def test_team_membership_without_session_permission_is_not_enough(band):
    p,c,s=example(band,permission=False)
    s.assignment_policy.rank_permissions.pop(band)
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.INFEASIBLE
    assert candidate_shortages(p,s)[0]['candidate_rejections']['STAFF']==['missing_explicit_session_type_permission']


def test_module_and_type_permission_must_both_pass():
    p,c,s=example()
    other=replace(p.sessions[0],offering_id='NOT-A-MEMBER')
    assert 'module_teaching_team_ineligible' in assignment_issues(p.input_snapshot,s,s.assignment_policy,'STAFF',other,next(iter(s.day_dates.values())))
    s.assignment_policy.permissions[0].session_types={SessionType.LAB}
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.INFEASIBLE


def test_independent_validator_rejects_policy_invalid_legacy_assignment():
    p,c,s=example('INSTRUCTOR','LECTURE')
    legacy=s.model_copy(deep=True,update={'assignment_policy':None})
    old=solve_timetable(p,c,staff_input=legacy)
    assert old.status is SolverStatus.OPTIMAL
    with pytest.raises(TimetableValidationError,match='rank_session_type_forbidden'):
        validate_timetable(p,c,old.schedule,staff_input=s)


@pytest.mark.parametrize('limit',[5,6])
def test_seven_distinct_dates_rejected_across_weekday_weekend_and_modes(limit):
    days=tuple('D'+str(i) for i in range(7))
    p=demand({'A':[('STAFF','LIC')]},[('A','G',7)])
    p,c,s,*_=scenario(prepared=p,minutes=30,days=days,band='PROFESSOR')
    legacy=solve_timetable(p,c,staff_input=s)
    assert legacy.status is SolverStatus.OPTIMAL and len({b.day for b in legacy.schedule})==7
    s.assignment_policy=policy(s,institution_teaching_day_limit=limit)
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.INFEASIBLE
    with pytest.raises(TimetableValidationError,match='weekly_teaching_day_limit'):
        validate_timetable(p,c,legacy.schedule,staff_input=s)


def test_explicit_five_day_limit_does_not_come_from_campus_attendance():
    p,c,s=example('PROFESSOR')
    assert s.workload_input.policy.band_rules['PROFESSOR'].campus_day_exempt
    s.availability[0].weekly_teaching_max_days=5
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.OPTIMAL
    with pytest.raises(ValidationError):s.assignment_policy.model_validate(dict(s.assignment_policy.model_dump(),institution_teaching_day_limit=8))


def test_workload_optimization_required_by_current_policy_unless_explicit_feasibility_diagnostic():
    p,c,s=example('PROFESSOR');s.assignment_policy.require_target_optimization=True
    with pytest.raises(SolverInputError,match='requires explicit teaching-target'):
        solve_timetable(p,c,staff_input=s)


def test_current_policy_reuses_native_exact_target_deviation_optimizer():
    p,c,s,*_=allocation_data(shared=4,fixed=2,minutes=300)
    s.assignment_policy=policy(s);s.assignment_policy.require_target_optimization=True
    original=context((p,c,s))
    result=solve_timetable(p,c,**original)
    assert result.status is SolverStatus.OPTIMAL
    assert result.fairness_report is not None and len(result.schedule)==len(p.sessions)
    assert not any(a.weekly_teaching_max_minutes is not None for a in s.availability)


def test_policy_and_new_day_field_preserve_old_snapshot_serialization():
    p,c,s=example('PROFESSOR');s.assignment_policy=None
    raw=s.model_dump()
    assert 'assignment_policy' not in raw
    assert all('weekly_teaching_max_days' not in a for a in raw['availability'])


def test_permissions_and_appointments_are_date_effective():
    p,c,s=example('LECTURER')
    s.assignment_policy.permissions[0].effective_from=s.policy_effective_until+timedelta(days=1)
    s.assignment_policy.permissions[0].effective_until=s.policy_effective_until+timedelta(days=2)
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.INFEASIBLE


def test_missing_rank_is_not_recovered_from_arbitrary_display_title():
    p,c,s=example('PROFESSOR');s.workload_input.profiles=[];s.allow_unclassified_staff_for_testing=True
    p.input_snapshot.staff_members[0].position='Professor'
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.INFEASIBLE


@pytest.mark.parametrize('band',['LECTURER','ASSISTANT_LECTURER'])
@pytest.mark.parametrize('kind',['LECTURE','TUTORIAL','WORKSHOP','LAB'])
def test_newly_confirmed_project_permissions_not_extracted_from_excel(band,kind):
    p,c,s=example(band,kind,permission=False)
    result=solve_timetable(p,c,staff_input=s)
    assert (result.status is SolverStatus.OPTIMAL)==(kind!='LAB')


def test_weekly_days_and_workload_are_unioned_across_both_streams_and_modes():
    from schedence_teaching.solver.staff_validation import staff_schedule_issues
    from schedence_teaching.solver.attendance_inputs import DeliveryMode
    days=tuple('DAY'+str(i) for i in range(7))
    prepared=demand({'WD':[('STAFF','LIC')],'WE':[('STAFF','LIC')]},
        [('WD','WD-G',4),('WE','WE-G',3)],streams={'WD':'WEEKDAY','WE':'WEEKEND'})
    p,c,s,*_=scenario(prepared=prepared,minutes=30,days=days,band='PROFESSOR',both_streams=True)
    old=solve_timetable(p,c,staff_input=s)
    assert old.status is SolverStatus.OPTIMAL
    bookings=tuple(replace(b,delivery_mode=DeliveryMode.ONLINE if i%2 else DeliveryMode.PHYSICAL) for i,b in enumerate(old.schedule))
    s.assignment_policy=policy(s)
    assert 'weekly_teaching_day_limit' in {i.code for i in staff_schedule_issues(p,s,bookings)}
    s.availability[0].weekly_teaching_max_minutes=180
    assert 'weekly_teaching_cap' in {i.code for i in staff_schedule_issues(p,s,bookings)}
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.INFEASIBLE


def test_above_target_teaching_stays_feasible_until_a_separate_hard_cap_is_configured():
    p,c,s=example('INSTRUCTOR','LAB')
    data=p.input_snapshot.model_dump();data['session_requirements'][0]['sessions_per_week']=41
    p=prepare_timetable_input(TeachingTimetableInput.model_validate(data))
    from test_staff_solver import windows
    c=windows(minutes=1440)
    s.day_dates={'DAY':next(iter(s.day_dates.values()))}
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.OPTIMAL and len(result.schedule)==41
    assert result.workload_report.staff[0].scheduled_teaching_minutes==1230
    assert result.workload_report.staff[0].target_shortfall_minutes==0
    s.availability[0].weekly_teaching_max_minutes=1200
    assert solve_timetable(p,c,staff_input=s).status is SolverStatus.INFEASIBLE


def test_search_hints_are_not_frozen_assignments():
    p,c,s=example('PROFESSOR')
    original=solve_timetable(p,c,staff_input=s)
    hint=replace(original.schedule[0],start_minute=0,end_minute=30)
    from schedence_teaching.solver.staff_inputs import StaffBlockedInterval
    s.availability[0].blocked_periods=[StaffBlockedInterval(day=hint.day,start_minute=0,end_minute=30)]
    result=solve_timetable(p,c,staff_input=s,schedule_hints=(hint,))
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].start_minute>=30


def test_current_local_preview_requires_a_resolved_policy_without_guessing_one():
    from test_teaching_read_api import make_record
    from schedence_teaching.persistence.teaching_policy_preview import prepare_policy_preview
    record=make_record();doc=record.document
    with pytest.raises(ValueError,match='resolved institution assignment policy'):
        prepare_policy_preview(doc,None)


def test_historical_workload_report_distinguishes_stored_validation_from_current_policy():
    from test_workload_reporting import with_policy
    from test_teaching_read_api import make_record
    from schedence_teaching.reporting.workload import lecturer_workload
    from schedence_teaching.reporting.contracts import WorkloadQuery
    record=with_policy(make_record(),'INSTRUCTOR')
    record.document.contexts.staff_input.assignment_policy=policy(record.document.contexts.staff_input)
    result=lecturer_workload(record,WorkloadQuery(version_id=record.version['id']))
    assert result['validation_passed'] is True and result['validation_basis']=='STORED_VERSION_POLICY'
    assert result['current_teaching_policy']['current_policy_status']=='NONCOMPLIANT'
    assert result['staff'][0]['current_policy_status']=='NONCOMPLIANT'


def test_other_institutions_supply_different_eligibility_targets_and_day_limits():
    p,c,s=example('INSTRUCTOR','LECTURE',permission=False)
    s.assignment_policy=TeachingAssignmentPolicy(policy_reference='OTHER-UNIVERSITY',
        week_reference=s.workload_input.week_reference,evidence_reference='SYNTHETIC-OTHER-INSTITUTION',
        rank_permissions={'INSTRUCTOR':{'LECTURE'}},appointment_permissions={},
        institution_teaching_day_limit=7,require_target_optimization=False)
    s.workload_input.policy.band_rules['INSTRUCTOR'].weekly_target_minutes=600
    result=solve_timetable(p,c,staff_input=s)
    assert result.status is SolverStatus.OPTIMAL
    assert result.workload_report.staff[0].policy.required_teaching_minutes==600


def test_generic_contract_has_no_initial_university_policy_defaults():
    with pytest.raises(ValidationError):TeachingAssignmentPolicy(policy_reference='MISSING',
        week_reference='WEEK',evidence_reference='SOURCE')


def test_report_does_not_apply_initial_university_rules_without_resolved_snapshot_policy():
    from test_workload_reporting import with_policy
    from test_teaching_read_api import make_record
    from schedence_teaching.reporting.workload import lecturer_workload
    from schedence_teaching.reporting.contracts import WorkloadQuery
    record=with_policy(make_record(),'LECTURER')
    result=lecturer_workload(record,WorkloadQuery(version_id=record.version['id']))
    assert result['current_teaching_policy'] is None


def test_staff_override_cannot_exceed_configured_institution_maximum():
    from schedence_teaching.solver.teaching_policy import teaching_day_limit
    p,c,s=example('PROFESSOR')
    s.assignment_policy.staff_teaching_day_limits['STAFF']=7
    assert teaching_day_limit(s,'STAFF')==6


def test_explicit_overage_objective_is_modeled_and_independently_recomputed():
    from test_fairness_solver import options
    data=allocation_data(shared=4,fixed=2,minutes=300)
    original=context(data,config=options(priority='DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES'))
    result=solve_timetable(data[0],data[1],**original)
    assert result.status is SolverStatus.OPTIMAL
    assert 'TOTAL_OVER_TARGET_MINUTES' in result.fairness_report.priority_order
    actual={r.policy.staff_id:r.scheduled_teaching_minutes for r in result.workload_report.staff}
    expected=sum(max(0,actual[sid]-target) for sid,target in result.fairness_report.targets)
    assert dict(result.fairness_report.objective_components)['TOTAL_OVER_TARGET_MINUTES']==expected
