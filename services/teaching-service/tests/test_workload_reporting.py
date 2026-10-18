"""Synthetic saved-delivery accounting; no real personal data or database writes."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO, StringIO
import csv
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schedence_teaching.reporting.contracts import VerifiedParticipation, WorkloadQuery
from schedence_teaching.reporting.exports import workload_csv, workload_pdf
from schedence_teaching.reporting.workload import lecturer_workload
from schedence_teaching.schemas import StaffMember, TeachingRole
from schedence_teaching.solver.staff_inputs import StaffSchedulingInput
from schedence_teaching.workload.contracts import confirmed_campus_policy
from schedence_teaching.persistence.ports import PersistenceConflict
from test_teaching_read_api import make_record
from test_persistence_contracts import OTHER_TENANT


@pytest.fixture(scope='module')
def saved():
    return make_record()


def report(saved, **filters):
    return lecturer_workload(saved, WorkloadQuery(version_id=saved.version['id'], **filters))


def with_policy(saved, band='LECTURER', appointment=None, cap=None, **extra):
    doc=saved.document.model_copy(deep=True)
    if band=='INSTRUCTOR':
        values=doc.teaching_input.staff_members[0].model_dump()
        doc.teaching_input.staff_members[0]=StaffMember.model_validate(dict(values,staff_type='INSTRUCTOR'))
        for member in doc.teaching_input.teaching_team_members:
            if member.staff_id=='SYN-A':member.role=TeachingRole.TEAM_MEMBER
    dates=doc.calendar.day_dates
    doc.contexts.staff_input=StaffSchedulingInput(
        workload_input=dict(week_reference=doc.calendar.week_reference,policy=confirmed_campus_policy(),
            profiles=[dict(staff_id='SYN-A',workload_band=band,appointment=appointment,evidence_reference='SYNTHETIC-OWNER-PROFILE')],**extra),
        day_dates=dates,policy_effective_from=doc.calendar.week_start,policy_effective_until=doc.calendar.week_end,
        availability=[dict(staff_id='SYN-A',allowed_teaching_days=set(dates),available_windows={d:[] for d in dates},
            blocked_periods=[],effective_from=doc.calendar.week_start,effective_until=doc.calendar.week_end,
            weekly_teaching_max_minutes=cap,daily_teaching_max_minutes=cap)])
    # Revalidate the source staff type after the synthetic instructor variation.
    doc.teaching_input.staff_members[0]=StaffMember.model_validate(doc.teaching_input.staff_members[0].model_dump())
    return replace(saved,document=doc)


def row(result,sid='SYN-A'):
    return next(r for r in result['staff'] if r['staff_id']==sid)


def test_actual_minutes_and_duplicate_module_codes_remain_distinct(saved):
    result=report(saved)
    assert result['totals']['assigned_teaching_minutes']==120
    assert result['totals']['unique_actual_deliveries']==2
    assert {m['offering_id'] for m in result['modules']}=={'O-A','O-B'}
    assert {m['module_code'] for m in result['modules']}=={'SYN'}
    assert sum(m['assigned_minutes'] for m in result['modules'])==120
    assert row(result)['teaching_day_count']==1
    assert row(result)['campus_attendance_inferred'] is False


@pytest.mark.parametrize('band,hours', [('INSTRUCTOR',20),('ASSISTANT_LECTURER',18),('LECTURER',16),
    ('SENIOR_LECTURER',12),('DR',8),('ASSISTANT_PROFESSOR',6),('PROFESSOR',6)])
def test_all_confirmed_targets_from_explicit_policy_not_title(saved,band,hours):
    result=report(with_policy(saved,band))
    assert row(result)['weekly_teaching_target_minutes']==hours*60
    assert row(result)['difference_from_target_minutes']==60-hours*60
    assert row(result)['weekly_teaching_cap_minutes'] is None
    assert not row(result)['hard_cap_violated']


@pytest.mark.parametrize('appointment',['HOD','DEAN'])
def test_appointment_override_preserves_underlying_rank(saved,appointment):
    result=report(with_policy(saved,'SENIOR_LECTURER',appointment))
    assert row(result)['academic_rank']=='SENIOR_LECTURER'
    assert row(result)['weekly_teaching_target_minutes']==240
    assert row(result)['administrative_allocation_minutes']==240


def test_missing_profile_never_infers_target_from_position(saved):
    result=report(saved)
    assert row(result)['source_position']=='Lecturer'
    assert row(result)['weekly_teaching_target_minutes'] is None
    assert row(result)['target_status']=='MISSING_TARGET'
    assert len(result['fairness']['missing_target_staff_ids'])==2


def test_combined_parent_and_subgroup_views_count_one_actual_booking(saved):
    deliveries=deepcopy(saved.deliveries)
    first=deliveries[0]
    first['coverage'].extend([dict(first['coverage'][0],group_reference='A.1'),
        dict(first['coverage'][0],group_reference='A.2')])
    result=report(replace(saved,deliveries=deliveries))
    assert row(result)['assigned_teaching_minutes']==60
    assert row(result)['assigned_delivery_count']==1
    assert result['totals']['unique_actual_deliveries']==2
    assert result['assignments'][0]['group_ids']==['A','A.1','A.2']


def test_shared_content_multi_offering_breakdown_is_explicitly_nonadditive(saved):
    deliveries=deepcopy(saved.deliveries)
    deliveries[0]['coverage'].append(dict(deliveries[0]['coverage'][0],offering_reference='O-B'))
    result=report(replace(saved,deliveries=deliveries))
    assert row(result)['assigned_teaching_minutes']==60
    assert not result['module_totals_additive']


def zero_staff(saved):
    doc=saved.document.model_copy(deep=True)
    doc.teaching_input.staff_members.append(StaffMember(staff_id='SYN-ZERO',staff_name='Synthetic Zero',position='Lecturer',staff_type='ACADEMIC'))
    return replace(saved,document=doc)


def test_zero_assignment_staff_and_eligible_team_are_not_charged(saved):
    record=zero_staff(saved)
    record.document.teaching_input.teaching_team_members.append(
        record.document.teaching_input.teaching_team_members[0].model_copy(update={'staff_id':'SYN-ZERO','role':'TEAM_MEMBER'}))
    result=report(record)
    assert result['totals']['staff_count']==3
    assert result['totals']['zero_assigned_count']==1
    assert row(result,'SYN-ZERO')['assigned_teaching_minutes']==0


def participation(saved,sid='SYN-ZERO',**updates):
    b=saved.deliveries[0]
    values=dict(institution_id=saved.document.institution_id,version_id=saved.version['id'],
        delivery_reference=b['delivery_reference'],staff_id=sid,
        intervals=[dict(start_minute=b['start_minute'],end_minute=b['start_minute']+30)],evidence_reference='SYNTHETIC-VERIFIED-OWNER-ASSIGNMENT')
    values.update(updates)
    return VerifiedParticipation(**values)


def test_verified_partial_co_teaching_only_credits_assigned_participation(saved):
    record=zero_staff(saved)
    result=lecturer_workload(record,WorkloadQuery(version_id=record.version['id']),
        participations=[participation(record),participation(record,'SYN-A')])
    assert row(result,'SYN-A')['assigned_teaching_minutes']==30
    assert row(result,'SYN-ZERO')['assigned_teaching_minutes']==30
    assert result['totals']['assigned_teaching_minutes']==120
    assert result['totals']['unique_actual_deliveries']==2
    assert result['totals']['staff_delivery_participations']==3


@pytest.mark.parametrize('change',['tenant','version','outside','duplicate','unknown','overlap'])
def test_invalid_participation_rejected(saved,change):
    record=zero_staff(saved);updates={}
    if change=='tenant':updates['institution_id']=OTHER_TENANT
    if change=='version':updates['version_id']=uuid4()
    if change=='unknown':updates['staff_id']='UNKNOWN'
    if change=='outside':updates['intervals']=[dict(start_minute=0,end_minute=30)]
    evidence=[participation(record,**updates)]
    if change=='duplicate':evidence*=2
    if change=='overlap':evidence=[participation(record,'SYN-B')]
    with pytest.raises(ValueError):lecturer_workload(record,WorkloadQuery(version_id=record.version['id']),participations=evidence)


def test_overlapping_participation_intervals_rejected(saved):
    with pytest.raises(ValidationError):participation(saved,intervals=[dict(start_minute=510,end_minute=550),dict(start_minute=530,end_minute=570)])


@pytest.mark.parametrize('kind',['LECTURE','TUTORIAL','WORKSHOP','LAB'])
def test_all_teaching_activity_breakdowns(saved,kind):
    deliveries=deepcopy(saved.deliveries)
    for b in deliveries:b['coverage'][0]['session_type']=kind
    result=report(replace(saved,deliveries=deliveries))
    assert row(result)[kind.lower()+'_minutes']==60
    assert sum(row(result)[k+'_minutes'] for k in ('lecture','tutorial','workshop','lab'))==60


def test_online_physical_and_weekday_weekend_are_independent(saved):
    deliveries=deepcopy(saved.deliveries)
    deliveries[0]['delivery_mode']='ONLINE';deliveries[0]['venue_reference']=None
    deliveries[0]['coverage'][0]['stream']='WEEKDAY'
    result=report(replace(saved,deliveries=deliveries))
    assert row(result)['online_minutes']==row(result)['weekday_minutes']==60
    assert row(result,'SYN-B')['physical_minutes']==row(result,'SYN-B')['weekend_minutes']==60


def test_filtered_scope_does_not_hide_full_week_cap_violation(saved):
    result=report(with_policy(saved,cap=30),offering_id='O-B')
    a=row(result)
    assert a['assigned_teaching_minutes']==0 and a['full_version_teaching_minutes']==60
    assert a['weekly_cap_excess_minutes']==30 and a['hard_cap_violated']
    assert a['daily_cap_violations'][0]['excess_minutes']==30
    assert a['difference_from_target_minutes']==60-960


def test_exceeding_a_teaching_target_is_not_a_maximum_violation(saved):
    record=with_policy(saved)
    record.document.contexts.staff_input.workload_input.policy.band_rules['LECTURER'].weekly_target_minutes=30
    a=row(report(record))
    assert a['difference_from_target_minutes']==30 and a['target_excess_minutes']==30
    assert a['weekly_teaching_cap_minutes'] is None and not a['hard_cap_violated']


@pytest.mark.parametrize('filters',[{'academic_year':1},{'semester':2},{'stream':'WEEKDAY'},{'module_code':'ABSENT'}])
def test_empty_filtered_slice_keeps_zero_staff_and_version_target_basis(saved,filters):
    result=report(with_policy(saved),**filters)
    assert result['totals']['assigned_teaching_minutes']==0
    assert row(result)['full_version_teaching_minutes']==60


def test_exact_lecturer_rank_and_appointment_filters(saved):
    record=with_policy(saved,appointment='HOD')
    assert len(report(record,lecturer='SYN-A')['staff'])==1
    assert len(report(record,academic_rank='LECTURER',appointment='HOD')['staff'])==1
    assert not report(record,academic_rank='PROFESSOR')['staff']


def test_separate_admin_allocation_reservations_and_verified_completion(saved):
    record=with_policy(saved,'PROFESSOR',administrative_reservations=[dict(reservation_id='RES',staff_id='SYN-A',
        duration_minutes=240,status='APPROVED',evidence_reference='SYNTHETIC',approval_reference='SYNTHETIC-APPROVAL')])
    period=dict(staff_id='SYN-A',on_date='2026-10-19',start_minute=600,end_minute=660)
    record=replace(record,run=dict(record.run,result_metadata=dict(work_period_report=dict(
        fixed_administrative_periods=[period],research_periods=[period]),administrative_schedule=[period])))
    a=row(report(record))
    assert a['weekly_teaching_target_minutes']==360 and a['administrative_allocation_minutes']==240
    assert a['weekly_approved_admin_reservation_minutes']==240
    assert a['timed_reserved_admin_minutes']==60 # duplicate timed projections union, not 120
    assert a['timed_reserved_research_minutes']==60
    assert a['verified_completed_admin_minutes']==0 and not a['recognized_work_ledger_complete']
    record.document.contexts.staff_input.workload_input.recognized_work=[]


def test_verified_admin_ledger_is_separate_from_assigned_teaching(saved):
    record=with_policy(saved,'PROFESSOR',recognized_work=[dict(work_id='WORK',staff_id='SYN-A',work_type='ADMINISTRATIVE',
        duration_minutes=90,evidence_reference='SYNTHETIC-COMPLETION')],recognized_work_complete_for={'SYN-A'})
    a=row(report(record))
    assert a['verified_completed_admin_minutes']==90 and a['recognized_work_ledger_complete']
    assert a['assigned_teaching_minutes']==60 and a['difference_from_target_minutes']==-300


def test_legacy_combined_target_not_misrepresented_as_teaching_target(saved):
    record=with_policy(saved)
    record.document.contexts.staff_input.workload_input.policy=confirmed_campus_policy(target_basis='TOTAL_RECOGNIZED_WORK')
    a=row(report(record))
    assert a['weekly_teaching_target_minutes'] is None
    assert 'LEGACY_COMBINED_TARGET_IS_NOT_A_SEPARATE_TEACHING_TARGET' in a['diagnostics']


def test_fairness_uses_existing_exact_normalization_and_excludes_missing(saved):
    result=report(with_policy(saved))
    comparison=result['fairness']['descriptive_comparisons'][0]
    assert comparison['staff_count']==1
    assert comparison['mean_utilization']==dict(numerator=1,denominator=16,value=1/16)
    assert comparison['variance']['value']==0
    assert result['fairness']['missing_target_staff_ids']==['SYN-B']
    assert 'DESCRIPTIVE_ONLY' in comparison['interpretation']


@pytest.mark.parametrize('change',['tenant','version','failed','duplicate','duration'])
def test_wrong_version_tenant_or_corrupt_delivery_cannot_produce_report(saved,change):
    record=deepcopy(saved);filters={}
    if change=='tenant':filters['institution_id']=OTHER_TENANT
    if change=='version':
        with pytest.raises(PersistenceConflict):lecturer_workload(record,WorkloadQuery(version_id=uuid4()))
        return
    if change=='failed':record.validation['passed']=False
    if change=='duplicate':record=replace(record,deliveries=(*record.deliveries,record.deliveries[0]))
    if change=='duration':record.deliveries[0]['end_minute']+=30
    with pytest.raises((PersistenceConflict,ValueError)):report(record,**filters)


@pytest.mark.parametrize('kind',['summary','assignments','targets','fairness'])
def test_csv_exact_minutes_and_version_metadata(saved,kind):
    result=report(with_policy(saved))
    rows=list(csv.DictReader(StringIO(workload_csv(result,kind).decode('utf-8-sig'))))
    assert len(rows)==2
    assert {r['version_id'] for r in rows}=={str(saved.version['id'])}
    if kind=='summary':assert sum(int(r['assigned_teaching_minutes']) for r in rows)==120
    if kind=='assignments':assert sum(int(r['participation_minutes']) for r in rows)==120
    if kind=='targets':assert rows[0]['difference_from_target_minutes']=='-900'


def test_csv_formula_guard_preserves_numeric_negative_deviation(saved):
    result=report(with_policy(saved));result['staff'][0]['staff_name']='=UNTRUSTED()'
    rows=list(csv.DictReader(StringIO(workload_csv(result).decode('utf-8-sig'))))
    assert rows[0]['staff_name']=="'=UNTRUSTED()"
    assert rows[0]['difference_from_target_minutes']=='-900'


@pytest.mark.parametrize('kind',['summary','individual','detailed'])
def test_pdf_contains_saved_version_totals_and_draft_provenance(saved,kind):
    pypdf=pytest.importorskip('pypdf');pytest.importorskip('reportlab')
    result=report(zero_staff(saved))
    pdf=workload_pdf(result,kind)
    pages=pypdf.PdfReader(BytesIO(pdf)).pages
    text=' '.join(p.extract_text() for p in pages)
    assert str(saved.version['id']) in text
    assert 'NOT OFFICIALLY PUBLISHED' in text and '2.00 hours' in text
    assert 'SYNTHETIC_PP1' in text
    assert 'SYN-ZERO' in text if kind!='detailed' else 'Synthetic A' in text
    assert all(float(p.mediabox.width)>float(p.mediabox.height) for p in pages)


@pytest.mark.parametrize('field,value',[('academic_year',5),('semester',3),('stream','OTHER'),('unknown','secret')])
def test_invalid_filters_rejected(saved,field,value):
    with pytest.raises(ValidationError):WorkloadQuery(version_id=saved.version['id'],**{field:value})
