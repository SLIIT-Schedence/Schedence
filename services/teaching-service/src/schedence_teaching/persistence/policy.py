"""Bind trusted, approved institution configuration to existing input snapshots.

No database access, source editing or implicit selection of a campus preset.
"""
import json

from schedence_teaching.policy.contracts import PolicyRevision, StaffPolicyOverride
from schedence_teaching.policy.resolution import resolve_policy, solver_rules
from schedence_teaching.schemas.canonical import canonical_json, content_digest


def effective_inputs(document):
    return dict(calendar=document.calendar, solver_rules=solver_rules(document.solver_config),
                **document.contexts.model_dump(round_trip=True))


def verify_snapshot_policy(document):
    policy = document.solver_config.resolved_policy
    if policy is None:
        if document.resolved_policy_digest is not None:
            raise ValueError('Snapshot resolved policy seal is missing.')
        return None
    policy.verify()
    if document.resolved_policy_digest != policy.digest:
        raise ValueError('Snapshot resolved policy digest is missing or mismatched.')
    if policy.institution_id != document.institution_id:
        raise ValueError('Resolved policy belongs to another institution.')
    dates = document.calendar.day_dates.values()
    if any(not policy.effective_from <= day <= policy.effective_until for day in dates):
        raise ValueError('Resolved policy is not effective for every teaching date.')
    if canonical_json(effective_inputs(document)) != policy.effective_json:
        raise ValueError('Snapshot scheduling inputs differ from the resolved policy digest.')
    return policy


SCOPE_DEPENDENT_RULES = ("attendance_memberships", "attendance_required_group_ids")
POLICY_CONTEXTS = ("fairness_policy", "fairness_config", "preference_policy", "preference_config", "consecutive_policy",
                   "consecutive_config", "student_lunch_policy", "student_lunch_config", "work_period_config")


def rebind_scoped_snapshot(document, parent):
    """Seal a scoped generation snapshot with its parent's own approved revision (P3.2).

    The scope is a subset of the parent's approved inputs. Every institution rule, policy
    context and assignment policy must be identical; only scope-dependent attendance
    differs. Parents with staff overrides need a separately approved scoped revision.
    """
    from schedence_teaching.policy import PolicyApproval
    policy = verify_snapshot_policy(parent)
    if policy is None:
        raise ValueError('Only a sealed parent snapshot can seal a scoped generation snapshot.')
    if json.loads(policy.staff_overrides_json):
        raise ValueError('Scoped generation from a snapshot with staff policy overrides requires a separately approved scoped revision.')
    revision = PolicyRevision(institution_id=policy.institution_id, policy_reference=policy.policy_reference,
        revision=policy.revision, effective_from=policy.effective_from, effective_until=policy.effective_until,
        approval=PolicyApproval.model_validate(json.loads(policy.approval_json)))
    bound = resolve_snapshot_policy(document, revision)
    scoped = bound.solver_config.resolved_policy
    before, after = json.loads(policy.effective_json), json.loads(scoped.effective_json)
    def rules(effective, drop_preference=False):
        values = {key: value for key, value in effective['solver_rules'].items() if key not in SCOPE_DEPENDENT_RULES}
        plan = values.get('objective_stage_plan')
        if drop_preference and plan:  # The only permitted plan difference: the dropped preference stage.
            values['objective_stage_plan'] = dict(plan, order=[key for key in plan['order'] if key != 'PREFERENCE_PENALTY'])
        return values
    staff = lambda effective: (effective.get('staff_input') or {}).get('assignment_policy')
    # Existing scope selection drops preference contexts together when none applies in the scope; never alters them.
    dropped = all(after.get(name) is None for name in ('preference_input', 'preference_policy', 'preference_config'))
    checked = [name for name in POLICY_CONTEXTS if not (dropped and name.startswith('preference_'))]
    if (scoped.rank_appointment_rules_json != policy.rank_appointment_rules_json or rules(before, dropped) != rules(after)
            or staff(before) != staff(after) or any(before.get(name) != after.get(name) for name in checked)):
        raise ValueError('Scoped generation would change sealed institution rules or policy contexts.')
    return bound


def version_policy_metadata(document):
    policy = verify_snapshot_policy(document)
    return (dict(resolved_policy_digest=policy.digest, institution_policy_revision=policy.revision,
                 institution_policy_reference=policy.policy_reference) if policy else {})


def verify_version_policy_metadata(document, metadata):
    expected = version_policy_metadata(document)
    keys = ('resolved_policy_digest', 'institution_policy_revision', 'institution_policy_reference')
    if any(metadata.get(key) != expected.get(key) for key in keys):
        raise ValueError('Stored version resolved policy reference/digest differs from its snapshot.')
    return document.solver_config.resolved_policy


def resolve_snapshot_policy(document, revision, *, staff_overrides=()):
    """Create a NEW detached policy-bound document; never mutate the caller.

    Weekly overrides must cover every configured date. Permissions can restrict,
    never widen, rank/appointment rules, source permissions or module membership.
    Availability and student/venue conflict evidence remain independent inputs.
    """
    from .contracts import SnapshotDocument
    from schedence_teaching.solver.teaching_policy import SessionTypePermission
    from schedence_teaching.workload.assessment import resolve_work_policy
    revision = PolicyRevision.model_validate(revision.model_dump(round_trip=True))
    if revision.institution_id != document.institution_id:
        raise ValueError('Cannot resolve another institution policy for this snapshot.')
    raw = document.model_dump(round_trip=True)
    raw['solver_config'].pop('resolved_policy', None)
    raw.pop('resolved_policy_digest', None)
    detached = SnapshotDocument.model_validate(raw)
    dates = tuple(detached.calendar.day_dates.values())
    if any(not revision.effective_from <= day <= revision.effective_until for day in dates):
        raise ValueError('Policy revision does not cover every teaching date.')
    if detached.contexts.staff_input is None or detached.contexts.staff_input.assignment_policy is None:
        raise ValueError('Current policy resolution requires explicit staff, workload and assignment contracts.')
    staff = detached.contexts.staff_input
    assignment = staff.assignment_policy
    original_defaults = effective_inputs(detached)
    original_defaults['parent_snapshot_digest'] = content_digest(document)
    original_defaults['parent_resolved_policy_digest'] = (
        document.solver_config.resolved_policy.digest if document.solver_config.resolved_policy else None)
    rank_rules = dict(assignment=dict(rank_permissions=assignment.rank_permissions,
        appointment_permissions=assignment.appointment_permissions),
        workload=staff.workload_input.policy.model_dump(exclude={'staff_overrides'}))
    people = {p.staff_id:p for p in detached.teaching_input.staff_members}
    profiles = {p.staff_id:p for p in staff.workload_input.profiles}
    availability = {a.staff_id:a for a in staff.availability}
    overrides = [StaffPolicyOverride.model_validate(o.model_dump(round_trip=True)) for o in staff_overrides]
    seen = set()
    for override in overrides:
        sid = override.staff_id
        if sid in seen or sid not in profiles or sid not in availability:
            raise ValueError('Staff override requires one known, explicitly classified staff/calendar record.')
        seen.add(sid)
        if not (revision.effective_from <= override.effective_from <= override.effective_until <= revision.effective_until):
            raise ValueError('Staff override dates must lie within the approved policy revision.')
        if any(not override.effective_from <= day <= override.effective_until for day in dates):
            raise ValueError('Weekly staff overrides must cover every configured teaching date.')
        if override.weekly_teaching_target_minutes is not None:
            resolve_work_policy(people[sid], profiles[sid], staff.workload_input.policy)
            base = staff.workload_input.policy.staff_overrides.get(sid) or (
                staff.workload_input.policy.appointment_overrides.get(profiles[sid].appointment)
                if profiles[sid].appointment else staff.workload_input.policy.band_rules[profiles[sid].workload_band])
            staff.workload_input.policy.staff_overrides[sid] = type(base).model_validate(dict(
                base.model_dump(), weekly_target_minutes=override.weekly_teaching_target_minutes))
        for name in ('daily_teaching_max_minutes', 'weekly_teaching_max_minutes'):
            value = getattr(override, name)
            if value is not None:
                setattr(availability[sid], name, value)
        if override.maximum_teaching_days is not None:
            if override.maximum_teaching_days > assignment.institution_teaching_day_limit:
                raise ValueError('Staff override cannot exceed the institution teaching-day ceiling.')
            assignment.staff_teaching_day_limits[sid] = override.maximum_teaching_days
            availability[sid].weekly_teaching_max_days = override.maximum_teaching_days
        if override.session_types is not None:
            allowed = set(assignment.rank_permissions.get(profiles[sid].workload_band, ()))
            if profiles[sid].appointment:
                allowed &= assignment.appointment_permissions.get(profiles[sid].appointment, set())
            if not override.session_types <= allowed:
                raise ValueError('Staff permission override cannot broaden rank/appointment eligibility.')
            assignment.permissions.append(SessionTypePermission(staff_id=sid,
                session_types=override.session_types, effective_from=override.effective_from,
                effective_until=override.effective_until, evidence_reference=override.approval.approval_reference))
    # Validate changes before sealing; no Pydantic mutation/model_copy bypass.
    detached = SnapshotDocument.model_validate(detached.model_dump(round_trip=True))
    for day in (detached.solver_config.delivery_calendar.days if detached.solver_config.delivery_calendar else ()):
        if day.mode_policy_reference not in (None, revision.policy_reference):
            raise ValueError('Online mode policy reference must identify the approved revision policy.')
    online_days = detached.solver_config.batch_online_days
    if online_days is not None and online_days.policy_reference != revision.policy_reference:
        raise ValueError('Batch online-day policy reference must identify the approved revision policy.')
    compactness = detached.solver_config.teaching_day_compactness
    if compactness is not None and compactness.policy_reference != revision.policy_reference:
        raise ValueError('Teaching-day compactness policy reference must identify the approved revision policy.')
    daily = detached.solver_config.daily_load_distribution
    if daily is not None and daily.policy_reference != revision.policy_reference:
        raise ValueError('Daily-load distribution policy reference must identify the approved revision policy.')
    stage_plan = detached.solver_config.objective_stage_plan
    if stage_plan is not None and stage_plan.plan_reference != revision.policy_reference:
        raise ValueError('Objective stage plan reference must identify the approved revision policy.')
    policy = resolve_policy(revision, defaults=original_defaults, rank_appointment_rules=rank_rules,
        staff_overrides=overrides, effective=effective_inputs(detached))
    config = detached.solver_config.model_dump(round_trip=True)
    config['resolved_policy'] = policy
    data = detached.model_dump(round_trip=True)
    data['solver_config'] = config
    data['resolved_policy_digest'] = policy.digest
    bound = SnapshotDocument.model_validate(data)
    # This also checks references, permissions, matching calendars and features.
    from .snapshots import replay_snapshot
    return replay_snapshot(bound)[0]
