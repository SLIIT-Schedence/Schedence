"""Offline snapshot orchestration; no SQL, source import, freezing or writes.

This adapter belongs to persistence orchestration because it replays snapshot
contracts. Solver, preparation and pilot modules remain persistence-independent.
"""
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.persistence.contracts import SourceDocument, SnapshotDocument
from schedence_teaching.persistence.snapshots import content_digest, replay_snapshot
from schedence_teaching.solver import FairnessOptimizationConfig, SolverConfig


def prepare_policy_preview(document, policy, *, max_time_seconds=180., hypothetical_target_caps=False, feasibility_only=False):
    """Preserve exact demand/attendance/calendar, replace assignment policy only.

    Every original booking remains unfixed: the monolithic solver is free to
    reassign staff and times across years and streams. Hypothetical caps are a
    separately labelled local experiment, never a change to campus policy.
    """
    from schedence_teaching.solver.teaching_policy import TeachingAssignmentPolicy
    if not isinstance(policy,TeachingAssignmentPolicy) or document.contexts.staff_input is None:
        raise ValueError('A resolved institution assignment policy and explicit dated staff input are required.')
    raw=document.model_dump(round_trip=True)
    staff=document.contexts.staff_input.model_copy(deep=True)
    staff.assignment_policy=policy.model_copy(deep=True)
    known=[p.staff_id for p in staff.workload_input.profiles]
    raw['solver_config']=SolverConfig.model_validate(dict(document.solver_config.model_dump(),
        max_time_seconds=max_time_seconds,num_search_workers=8,lab_variables_first=True))
    raw['contexts']['staff_input']=staff
    if hypothetical_target_caps or feasibility_only:
        staff.assignment_policy.require_target_optimization=False
        raw['contexts']['fairness_policy']=None;raw['contexts']['fairness_config']=None
        for name in ('preference_input','preference_policy','preference_config'):
            raw['contexts'][name]=None
        consecutive=raw['contexts'].get('consecutive_config')
        hard_break=consecutive and consecutive.get('enforce_confirmed_hard_break',False)
        if not hard_break:
            for name in ('consecutive_policy','consecutive_config'):raw['contexts'][name]=None
    if hypothetical_target_caps:
        from schedence_teaching.workload.assessment import resolve_work_policy
        people={p.staff_id:p for p in document.teaching_input.staff_members}
        targets={p.staff_id:resolve_work_policy(people[p.staff_id],p,staff.workload_input.policy).required_teaching_minutes
                 for p in staff.workload_input.profiles}
        for availability in staff.availability:
            target=targets.get(availability.staff_id)
            if target is not None:
                cap=availability.weekly_teaching_max_minutes
                availability.weekly_teaching_max_minutes=min(cap,target) if cap is not None else target
        staff.assignment_policy.require_target_optimization=False
        raw['contexts']['fairness_policy']=None;raw['contexts']['fairness_config']=None
    elif not feasibility_only:
        raw['contexts']['fairness_policy']=FairnessPolicy(policy_reference=policy.policy_reference+':target-deviation',
            week_reference=policy.week_reference,provenance=dict(source='USER_CONFIRMED_TARGETS_AND_PROJECT_OPTIMIZATION',reference=policy.evidence_reference),
            comparison_scope='EXPLICIT_GROUPS',comparison_groups=[dict(group_id='KNOWN-TEACHING-TARGETS',
                staff_ids=known,reason='Target fulfillment optimization, not a claim of equal teaching opportunities.')])
        raw['contexts']['fairness_config']=FairnessOptimizationConfig(priority='DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES',
            priority_policy_reference=policy.policy_reference,deviation_reference='TEACHING_TARGET',
            cohort_rule='INPUT_ELIGIBILITY_AND_FIXED_CALENDAR',target_verification_reference=policy.evidence_reference,
            policy_approval_reference=policy.evidence_reference,max_model_variables=1_000_000,max_model_constraints=2_000_000)
    raw['sources']=(*document.sources,SourceDocument(label='LOCAL:teaching-policy-correction',
        sha256=content_digest(dict(policy=policy,hypothetical_target_caps=hypothetical_target_caps,feasibility_only=feasibility_only)),
        owner='TEACHING_SERVICE',provenance_kind='USER_CONFIRMED_CORRECTION',provenance=dict(
            parent_snapshot_sha256=content_digest(document),policy_reference=policy.policy_reference,
            policy_origin='USER_CONFIRMED_PROJECT_RULES_NOT_EXCEL_PERMISSIONS',
            local_only=True,hypothetical_target_caps=hypothetical_target_caps,feasibility_only=feasibility_only,
            frozen_bookings=0,year1_foundation_grouping_preserved=True)))
    new=SnapshotDocument.model_validate(raw)
    return replay_snapshot(new)
