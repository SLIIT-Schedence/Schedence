"""Actual delivery accounting, with explicit policy provenance and no solver calls."""
from collections import Counter, defaultdict
from datetime import date
from dataclasses import asdict
from fractions import Fraction
from types import SimpleNamespace
from uuid import UUID

from schedence_teaching.fairness.metrics import distribution, teaching_target_metrics
from schedence_teaching.workload.assessment import resolve_work_policy, WorkloadInputError
from schedence_teaching.workload.contracts import WorkloadTargetBasis
from schedence_teaching.workload.metrics import teaching_metrics
from schedence_teaching.persistence.ports import PersistenceConflict
from .contracts import WorkloadQuery, VerifiedParticipation


def _fraction(value):
    return None if value is None else dict(numerator=value.numerator, denominator=value.denominator, value=float(value))


def _interval_minutes(items):
    """Union reservations on an actual date, without adding overlapping plans."""
    by_date=defaultdict(list)
    for item in items:
        by_date[str(item['on_date'])].append((item['start_minute'],item['end_minute']))
    total=0
    for windows in by_date.values():
        end=-1
        for start,stop in sorted(windows):
            total+=max(0,stop-max(start,end));end=max(end,stop)
    return total


def lecturer_workload(record, query: WorkloadQuery, *, participations=()):
    """One saved version, institution and representative week at a time.

    Filtered minutes describe the selected slice. Weekly targets and cap checks
    always use the complete selected version so module/stream filters cannot
    masquerade as a full-week workload deficit or hide a weekly cap breach.
    """
    query=WorkloadQuery.model_validate(query.model_dump())
    doc=record.document
    from schedence_teaching.persistence.policy import verify_version_policy_metadata
    try:
        resolved_policy = verify_version_policy_metadata(doc, record.run.get('result_metadata', {}).get('_teaching_persistence', {}))
    except ValueError as error:
        raise PersistenceConflict(str(error)) from None
    if query.version_id != record.version['id'] or query.institution_id not in (None,doc.institution_id):
        raise PersistenceConflict('Report version/institution does not match the selected stored record.')
    if not record.validation['passed'] or record.validation['hard_violations'] or not record.version['validation_passed']:
        raise PersistenceConflict('Workload reporting requires a validated saved timetable.')
    if len(record.deliveries)>20_000:
        raise ValueError('Report delivery limit exceeded; use a reviewed bounded reporting scope.')
    people={p.staff_id:p for p in doc.teaching_input.staff_members}
    modules={m.offering_id:m for m in doc.teaching_input.module_offerings}
    source=doc.contexts.staff_input
    workload=source.workload_input if source else None
    profiles={p.staff_id:p for p in workload.profiles} if workload else {}
    availability={a.staff_id:a for a in source.availability} if source else {}
    deliveries={b['delivery_reference']:b for b in record.deliveries}
    if len(deliveries)!=len(record.deliveries):
        raise ValueError('Duplicate actual delivery identities cannot be reported.')
    evidence={}
    for original in participations:
        p=VerifiedParticipation.model_validate(original.model_dump())
        b=deliveries.get(p.delivery_reference)
        if p.institution_id!=doc.institution_id or p.version_id!=query.version_id or b is None or p.staff_id not in people:
            raise ValueError('Participation evidence references another tenant/version or unknown booking/staff.')
        if any(i.start_minute<b['start_minute'] or i.end_minute>b['end_minute'] for i in p.intervals):
            raise ValueError('Verified participation must lie inside the actual delivery.')
        key=(p.delivery_reference,p.staff_id)
        if key in evidence:raise ValueError('Duplicate participation evidence for one delivery/staff.')
        evidence[key]=p
    full=defaultdict(list);selected=defaultdict(list);module_rows=defaultdict(dict)
    assigned=defaultdict(list)
    def matches(c):
        m=modules[c['offering_reference']]
        return (query.academic_year in (None,c['academic_year']) and query.semester in (None,c['semester'])
            and query.stream in (None,c['stream']) and query.module_code in (None,m.module_code)
            and query.offering_id in (None,m.offering_id))
    for identity,b in deliveries.items():
        coverage=b['coverage']
        if not coverage or b['end_minute']<=b['start_minute'] or b['staff_reference'] not in people:
            raise ValueError('Stored actual delivery metadata is invalid.')
        if any(c['duration_minutes']!=b['end_minute']-b['start_minute'] for c in coverage):
            raise ValueError('Actual delivery duration differs from its preserved teaching requirements.')
        kinds={c['session_type'] for c in coverage};streams={c['stream'] for c in coverage}
        if len(kinds)!=1 or len(streams)!=1:raise ValueError('A delivery must retain one session type and stream.')
        ids={b['staff_reference']}|{sid for did,sid in evidence if did==identity}
        included=[c for c in coverage if matches(c)]
        for sid in sorted(ids):
            proof=evidence.get((identity,sid))
            intervals=proof.intervals if proof else (SimpleNamespace(start_minute=b['start_minute'],end_minute=b['end_minute']),)
            minutes=sum(i.end_minute-i.start_minute for i in intervals)
            day=b['teaching_date'].isoformat() if hasattr(b['teaching_date'],'isoformat') else str(b['teaching_date'])
            item=dict(delivery_reference=identity,staff_id=sid,date=day,
                start_minute=b['start_minute'],end_minute=b['end_minute'],participation_minutes=minutes,
                participation_intervals=[dict(start_minute=i.start_minute,end_minute=i.end_minute) for i in intervals],
                participation_evidence=proof.evidence_reference if proof else 'SAVED_RESPONSIBLE_STAFF_ASSIGNMENT',
                session_type=next(iter(kinds)),stream=next(iter(streams)),delivery_mode=b['delivery_mode'],
                venue_id=b['venue_reference'],group_ids=sorted({c['group_reference'] for c in coverage}),
                modules=[dict(offering_id=oid,module_code=modules[oid].module_code,module_name=modules[oid].module_name)
                    for oid in sorted({c['offering_reference'] for c in coverage})])
            full[sid].append(item)
            for interval in intervals:
                assigned[sid].append(SimpleNamespace(day=day,start_minute=interval.start_minute,end_minute=interval.end_minute))
            if included:
                selected[sid].append(item)
                for oid in {c['offering_reference'] for c in included}:
                    module_rows[sid].setdefault(oid,[]).append(item)
    day_dates={d.isoformat():d for d in doc.calendar.day_dates.values()}
    for sid,items in assigned.items():
        by_day=defaultdict(list)
        for i in items:by_day[i.day].append(i)
        for windows in by_day.values():
            ordered=sorted(windows,key=lambda i:i.start_minute)
            if any(a.end_minute>b.start_minute for a,b in zip(ordered,ordered[1:])):
                raise ValueError('Verified assigned teaching overlaps for the same staff member.')
    metadata=record.run.get('result_metadata',{})
    periods=metadata.get('work_period_report') or {}
    administrative=metadata.get('administrative_schedule') or []
    fixed_admin=periods.get('fixed_administrative_periods',[])
    research=periods.get('research_periods',[])
    rows=[];assignments=[];breakdown=[];diagnostics=[]
    for sid,p in sorted(people.items()):
        profile=profiles.get(sid);policy=None
        if profile:
            try:policy=resolve_work_policy(p,profile,workload.policy)
            except WorkloadInputError:diagnostics.append(dict(staff_id=sid,code='UNRESOLVED_EXPLICIT_WORK_POLICY'))
        if query.lecturer not in (None,sid):continue
        if query.academic_rank is not None and (not profile or profile.workload_band!=query.academic_rank):continue
        if query.appointment is not None and (not profile or profile.appointment!=query.appointment):continue
        totals=Counter();actual=sum(i['participation_minutes'] for i in full[sid]);minutes=sum(i['participation_minutes'] for i in selected[sid])
        full_offerings={m['offering_id'] for i in full[sid] for m in i['modules']}
        full_lab_offerings={m['offering_id'] for i in full[sid] if i['session_type']=='LAB' for m in i['modules']}
        selected_lab_offerings={oid for oid,items in module_rows[sid].items() if any(i['session_type']=='LAB' for i in items)}
        for i in selected[sid]:
            for key in (i['session_type'],i['delivery_mode'],i['stream']):totals[key]+=i['participation_minutes']
        target=policy.required_teaching_minutes if policy else None
        comparison=teaching_target_metrics(actual,target)
        a=availability.get(sid);weekly_cap=a.weekly_teaching_max_minutes if a else None;daily_cap=a.daily_teaching_max_minutes if a else None
        metrics=teaching_metrics(assigned[sid],day_dates)
        daily=dict(metrics.daily_teaching_minutes)
        weekly_reservations=[r for r in workload.administrative_reservations if r.staff_id==sid] if workload else []
        ledger=[r for r in workload.recognized_work if r.staff_id==sid] if workload else []
        row=dict(staff_id=sid,staff_name=p.staff_name,staff_type=p.staff_type.value,source_position=p.position,
            academic_rank=profile.workload_band.value if profile else None,appointment=profile.appointment.value if profile and profile.appointment else None,
            policy_reference=policy.policy_reference if policy else None,classification_evidence=profile.evidence_reference if profile else None,
            rule_source=policy.rule_source if policy else None,target_basis=policy.target_basis.value if policy else None,
            lecture_minutes=totals['LECTURE'],tutorial_minutes=totals['TUTORIAL'],workshop_minutes=totals['WORKSHOP'],lab_minutes=totals['LAB'],
            assigned_teaching_minutes=minutes,assigned_teaching_hours=minutes/60,online_minutes=totals['ONLINE'],physical_minutes=totals['PHYSICAL'],
            weekday_minutes=totals['WEEKDAY'],weekend_minutes=totals['WEEKEND'],assigned_delivery_count=len(selected[sid]),
            assigned_module_offering_count=len(module_rows[sid]),assigned_module_offering_ids=sorted(module_rows[sid]),
            assigned_lab_module_offering_count=len(selected_lab_offerings),
            assigned_lab_delivery_count=sum(i['session_type']=='LAB' for i in selected[sid]),
            full_version_module_offering_count=len(full_offerings),full_version_lab_module_offering_count=len(full_lab_offerings),
            module_count_basis='DISTINCT_OFFERING_IDS_NOT_MODULE_CODES',
            teaching_day_count=len({i['date'] for i in selected[sid]}),teaching_dates=sorted({i['date'] for i in selected[sid]}),
            full_version_teaching_minutes=actual,full_version_teaching_day_count=metrics.teaching_day_count,
            maximum_daily_teaching_minutes=metrics.maximum_daily_teaching_minutes,longest_continuous_teaching_minutes=metrics.longest_consecutive_teaching_minutes,
            weekly_teaching_target_minutes=target,difference_from_target_minutes=actual-target if target is not None else None,
            target_status=comparison.status,target_shortfall_minutes=comparison.shortfall_minutes,target_excess_minutes=comparison.excess_minutes,
            target_utilization=_fraction(comparison.utilization),target_comparison_scope='FULL_SELECTED_VERSION',
            weekly_teaching_cap_minutes=weekly_cap,daily_teaching_cap_minutes=daily_cap,
            weekly_cap_excess_minutes=max(actual-weekly_cap,0) if weekly_cap is not None else None,
            daily_cap_violations=[dict(date=d,minutes=n,excess_minutes=n-daily_cap) for d,n in daily.items() if daily_cap is not None and n>daily_cap],
            administrative_allocation_minutes=policy.administrative_allocation_minutes if policy else None,
            weekly_approved_admin_reservation_minutes=sum(r.duration_minutes for r in weekly_reservations if r.status.value=='APPROVED'),
            weekly_proposed_admin_reservation_minutes=sum(r.duration_minutes for r in weekly_reservations if r.status.value=='PROPOSED'),
            timed_reserved_admin_minutes=_interval_minutes([r for r in [*fixed_admin,*administrative] if r['staff_id']==sid]),
            timed_reserved_research_minutes=_interval_minutes([r for r in research if r['staff_id']==sid]),
            verified_completed_admin_minutes=sum(r.duration_minutes for r in ledger if r.work_type.value=='ADMINISTRATIVE'),
            recognized_work_ledger_complete=sid in workload.recognized_work_complete_for if workload else False,
            campus_attendance_inferred=False,diagnostics=[])
        if profile is None:row['diagnostics'].append('MISSING_EXPLICIT_WORKLOAD_PROFILE_NO_TITLE_INFERENCE')
        if policy and policy.target_basis is WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK:
            row['diagnostics'].append('LEGACY_COMBINED_TARGET_IS_NOT_A_SEPARATE_TEACHING_TARGET')
        for key in ('lecture_minutes','tutorial_minutes','workshop_minutes','lab_minutes','online_minutes','physical_minutes',
            'weekday_minutes','weekend_minutes','weekly_teaching_target_minutes','difference_from_target_minutes',
            'weekly_teaching_cap_minutes','administrative_allocation_minutes','timed_reserved_admin_minutes','timed_reserved_research_minutes'):
            row[key.removesuffix('_minutes')+'_hours']=row[key]/60 if row[key] is not None else None
        row['hard_cap_violated']=bool(row['daily_cap_violations'] or (row['weekly_cap_excess_minutes'] or 0)>0)
        rows.append(row)
        assignments.extend(dict(i,staff_name=p.staff_name) for i in selected[sid])
        for oid,items in sorted(module_rows[sid].items()):
            m=modules[oid]
            breakdown.append(dict(staff_id=sid,staff_name=p.staff_name,offering_id=oid,module_code=m.module_code,module_name=m.module_name,
                delivery_count=len(items),assigned_minutes=sum(i['participation_minutes'] for i in items),
                session_type_minutes={kind:sum(i['participation_minutes'] for i in items if i['session_type']==kind) for kind in ('LECTURE','TUTORIAL','WORKSHOP','LAB')}))
    fairness=[]
    cohorts=defaultdict(list)
    for r in rows:
        if r['target_utilization'] is not None:cohorts[r['academic_rank'],r['appointment']].append(r)
    for (rank,appointment),members in sorted(cohorts.items(),key=lambda k:str(k[0])):
        values={r['staff_id']:Fraction(r['full_version_teaching_minutes'],r['weekly_teaching_target_minutes']) for r in members}
        mean,variance,mad,maximum,relative=distribution(values,{key:Fraction(1) for key in values})
        fairness.append(dict(academic_rank=rank,appointment=appointment,staff_count=len(members),mean_utilization=_fraction(mean),
            variance=_fraction(variance),mean_absolute_deviation=_fraction(mad),maximum_deviation=_fraction(maximum),
            maximum_relative_deviation=_fraction(relative),interpretation='DESCRIPTIVE_ONLY_NOT_OPPORTUNITY_ADJUSTED_FAIRNESS'))
    current_policy=None
    if source is not None and source.assignment_policy is not None:
        from .teaching_audit import audit_teaching_assignments
        audit=audit_teaching_assignments(record,source.assignment_policy)
        statuses={r['staff_id']:r for r in audit['staff']}
        for r in rows:
            s=statuses[r['staff_id']]
            r.update(current_assignment_issue_codes=s['issue_codes'],weekly_teaching_day_limit=s['teaching_day_limit'],
                teaching_day_limit_violated=s['teaching_day_limit_violated'],
                current_policy_status='NONCOMPLIANT' if s['assignment_violation_count'] or s['teaching_day_limit_violated'] else
                    ('UNRESOLVED_PROFILE' if s['profile_unresolved'] else 'COMPLIANT'))
        current_policy={k:audit[k] for k in ('policy_reference','current_policy_status','issue_counts',
            'teaching_day_limit_violations','unresolved_profile_count')}
        if participations:
            current_policy['current_policy_status']='SAVED_PRIMARY_ASSIGNMENTS_ASSESSED_ADDITIONAL_PARTICIPATION_REQUIRES_OWNER_POLICY_REVIEW'
    return dict(institution_id=str(doc.institution_id),version_id=str(record.version['id']),version_number=record.version['version_number'],
        lifecycle_status=record.version['lifecycle_status'],logical_status=record.logical_status,calendar_label=doc.calendar_label,
        academic_term_reference=doc.academic_term_reference,week_reference=doc.calendar.week_reference,
        week_start=doc.calendar.week_start.isoformat(),week_end=doc.calendar.week_end.isoformat(),timezone=doc.timezone_name,
        validation_passed=True,hard_violations=0,academic_complete=record.version.get('academic_complete',False),
        validation_basis='STORED_VERSION_POLICY',current_teaching_policy=current_policy,
        resolved_policy_digest=resolved_policy.digest if resolved_policy else None,
        institution_policy_revision=resolved_policy.revision if resolved_policy else None,
        report_source='LOCAL_POLICY_REPLACEMENT_PREVIEW' if any(s.label=='LOCAL:teaching-policy-correction' for s in doc.sources) else
            ('CORRECTED_FOUNDATION_LOCAL_PREVIEW' if any(s.label=='LOCAL:academic-grouping-correction' for s in doc.sources) else 'SAVED_TIMETABLE_VERSION'),
        scopes=[s.model_dump(mode='json') for s in doc.scopes],limitations=record.limitations,
        sources=[dict(label=s.label,sha256=s.sha256,kind=s.provenance_kind.value,owner=s.owner.value) for s in doc.sources],
        filters=query.model_dump(mode='json'),staff=rows,assignments=assignments,modules=breakdown,
        totals=dict(staff_count=len(rows),academic_staff_count=sum(r['staff_type']=='ACADEMIC' for r in rows),
            instructor_count=sum(r['staff_type']=='INSTRUCTOR' for r in rows),zero_assigned_count=sum(r['assigned_teaching_minutes']==0 for r in rows),
            assigned_teaching_minutes=sum(r['assigned_teaching_minutes'] for r in rows),
            assigned_teaching_hours=sum(r['assigned_teaching_minutes'] for r in rows)/60,
            staff_with_hard_cap_violations=sum(r['hard_cap_violated'] for r in rows),
            unique_actual_deliveries=len({r['delivery_reference'] for r in assignments}),
            staff_delivery_participations=len(assignments)),
        fairness=dict(descriptive_comparisons=fairness,source_policy_assessment_available=bool(doc.contexts.fairness_policy),
            stored_assessment=metadata.get('fairness_optimization'),missing_target_staff_ids=[r['staff_id'] for r in rows if r['weekly_teaching_target_minutes'] is None]),
        module_totals_additive=not any(len(i['modules'])>1 for i in assignments),diagnostics=diagnostics,
        notes=['One saved version only; semester alternatives are never combined.',
            'Distinct module-offering counts are separate from deliveries and minutes; module codes are not unique. No module-count maximum is inferred.',
            'Filters affect displayed assignments; weekly target and cap comparisons use the full selected version.',
            'Reserved administrative/research time is not completed work; untimed and timed reservations are not added together.',
            'Teaching-team eligibility is not an assignment. No co-teaching is inferred.',
            'Rank/appointment comparisons are descriptive; equal opportunity or fairness is not established.'])
