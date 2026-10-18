"""Current-policy assessment of saved bookings, separate from historic validation."""
from collections import Counter, defaultdict
from datetime import date

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.solver.teaching_policy import assignment_issues, teaching_day_limit


def audit_teaching_assignments(record, policy):
    source=record.document.contexts.staff_input
    if source is None:raise ValueError('An explicit dated staff input is required for policy audit.')
    source=source.model_copy(deep=True,update={'assignment_policy':policy})
    prepared=prepare_timetable_input(record.document.teaching_input)
    occurrences={s.occurrence_id:s for s in prepared.sessions}
    profiles={p.staff_id:p for p in source.workload_input.profiles}
    per_staff=defaultdict(list);dates=defaultdict(set);minutes=Counter();violations=[];activity=defaultdict(Counter)
    modules={m.offering_id:m for m in record.document.teaching_input.module_offerings}
    for b in record.deliveries:
        sid=b['staff_reference'];on_date=date.fromisoformat(str(b['teaching_date']))
        codes=set()
        for c in b['coverage']:
            codes.update(assignment_issues(prepared.input_snapshot,source,policy,sid,
                occurrences[c['occurrence_reference']],on_date))
        minutes[sid]+=b['end_minute']-b['start_minute'];dates[sid].add(on_date)
        activity[sid][b['coverage'][0]['session_type']]+=b['end_minute']-b['start_minute']
        if codes:
            item=dict(delivery_reference=b['delivery_reference'],staff_id=sid,date=on_date.isoformat(),
                session_type=b['coverage'][0]['session_type'],offering_ids=sorted({c['offering_reference'] for c in b['coverage']}),
                module_codes=sorted({modules[c['offering_reference']].module_code for c in b['coverage']}),
                group_ids=sorted({c['group_reference'] for c in b['coverage']}),start_minute=b['start_minute'],end_minute=b['end_minute'],
                issue_codes=sorted(codes))
            violations.append(item);per_staff[sid].append(item)
    staff=[]
    from schedence_teaching.workload.assessment import resolve_work_policy
    for person in record.document.teaching_input.staff_members:
        sid=person.staff_id;profile=profiles.get(sid)
        resolved=resolve_work_policy(person,profile,source.workload_input.policy) if profile else None
        target=resolved.required_teaching_minutes if resolved else None
        availability=next((a for a in source.availability if a.staff_id==sid),None)
        limit=teaching_day_limit(source,sid)
        daily=Counter()
        for b in record.deliveries:
            if b['staff_reference']==sid:daily[str(b['teaching_date'])]+=b['end_minute']-b['start_minute']
        cap=availability.weekly_teaching_max_minutes if availability else None
        daily_cap=availability.daily_teaching_max_minutes if availability else None
        staff.append(dict(staff_id=sid,academic_rank=profile.workload_band.value if profile else None,
            appointment=profile.appointment.value if profile and profile.appointment else None,
            source_position=person.position,assigned_minutes=minutes[sid],target_minutes=target,
            activity_minutes=dict(activity[sid]),
            over_target_minutes=max(0,minutes[sid]-target) if target is not None else None,
            weekly_cap_minutes=cap,weekly_cap_excess_minutes=max(0,minutes[sid]-cap) if cap is not None else None,
            daily_cap_violation_dates=sorted(d for d,n in daily.items() if daily_cap is not None and n>daily_cap),
            teaching_days=len(dates[sid]),teaching_day_limit=limit,
            teaching_day_limit_violated=limit is not None and len(dates[sid])>limit,
            seven_teaching_days=len(dates[sid])==7,profile_unresolved=profile is None,
            assignment_violation_count=len(per_staff[sid]),issue_codes=sorted({c for b in per_staff[sid] for c in b['issue_codes']})))
    counts=Counter(c for b in violations for c in b['issue_codes'])
    return dict(version_id=str(record.version['id']),stored_validation_passed=record.validation['passed'],
        policy_reference=policy.policy_reference,current_policy_status='NONCOMPLIANT' if violations or any(r['teaching_day_limit_violated'] for r in staff) else 'COMPLIANT',
        actual_deliveries=len(record.deliveries),assigned_minutes=sum(minutes.values()),staff=staff,
        assignment_violations=violations,issue_counts=dict(counts),
        over_target_staff_count=sum((r['over_target_minutes'] or 0)>0 for r in staff),
        teaching_day_limit_violations=sum(r['teaching_day_limit_violated'] for r in staff),
        seven_day_staff_count=sum(r['seven_teaching_days'] for r in staff),
        unresolved_profile_count=sum(r['profile_unresolved'] for r in staff),
        target_excess_is_not_a_hard_cap=True,historical_record_modified=False)


def candidate_shortages(prepared,staff_input):
    """Complete per-requirement diagnosis; no demand is dropped to fit a policy."""
    from schedence_teaching.solver.eligibility import candidate_pools
    from schedence_teaching.solver.teaching_policy import eligible_assignment_days
    pools=candidate_pools(prepared.input_snapshot);rows=[]
    groups={g.group_id:g for g in prepared.input_snapshot.student_groups}
    for session in prepared.sessions:
        choices={sid:eligible_assignment_days(prepared.input_snapshot,staff_input,staff_input.assignment_policy,sid,(session,))
                 for sid in pools[session.offering_id]}
        if any(choices.values()):continue
        reasons={sid:sorted({code for on_date in staff_input.day_dates.values() for code in assignment_issues(
            prepared.input_snapshot,staff_input,staff_input.assignment_policy,sid,session,on_date)}) for sid in choices}
        rows.append(dict(occurrence_id=session.occurrence_id,requirement_id=session.requirement_id,
            offering_id=session.offering_id,group_id=session.group_id,session_type=session.session_type.value,
            duration_minutes=session.duration_minutes,academic_year=groups[session.group_id].academic_year,semester=groups[session.group_id].semester,
            stream=session.stream.value,candidate_rejections=reasons))
    return rows
