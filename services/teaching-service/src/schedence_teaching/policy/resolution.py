"""Pure resolution/sealing and verification, without academic master records."""
import json

from schedence_teaching.schemas.canonical import canonical_json, content_digest
from .contracts import PolicyRevision, ResolvedInstitutionPolicy, StaffPolicyOverride

SEARCH_FIELDS = frozenset({'max_time_seconds', 'random_seed', 'num_search_workers', 'lab_variables_first'})


def solver_rules(config):
    return config.model_dump(round_trip=True, exclude={'resolved_policy', *SEARCH_FIELDS})


def resolve_policy(revision, *, defaults, rank_appointment_rules, staff_overrides, effective):
    revision = PolicyRevision.model_validate(revision.model_dump(round_trip=True))
    overrides = [StaffPolicyOverride.model_validate(o.model_dump(round_trip=True)) for o in staff_overrides]
    values = dict(format_version=1, institution_id=revision.institution_id,
        policy_reference=revision.policy_reference, revision=revision.revision,
        effective_from=revision.effective_from, effective_until=revision.effective_until,
        approval_json=canonical_json(revision.approval), defaults_json=canonical_json(defaults),
        rank_appointment_rules_json=canonical_json(rank_appointment_rules),
        staff_overrides_json=canonical_json(sorted(overrides, key=lambda o: (o.staff_id, o.effective_from))),
        effective_json=canonical_json(effective))
    return ResolvedInstitutionPolicy(**values, digest=content_digest(values))


def verify_config(config):
    policy = config.resolved_policy
    custom_modes = config.delivery_calendar and any(d.mode_policy_reference is not None for d in config.delivery_calendar.days)
    if policy is None:
        if custom_modes:
            raise ValueError('Configured online mode permissions require a resolved institution policy.')
        if config.batch_online_days is not None:
            raise ValueError('Batch online-day policy requires a resolved institution policy.')
        return None
    policy.verify()
    effective = json.loads(policy.effective_json)
    if canonical_json(solver_rules(config)) != canonical_json(effective['solver_rules']):
        raise ValueError('Solver rules differ from the resolved institution policy.')
    return effective


def verify_component(config, name, value):
    effective = verify_config(config)
    if effective is not None and canonical_json(value) != canonical_json(effective[name]):
        raise ValueError(f'{name} differs from the resolved institution policy.')


def verify_runtime_contexts(config, contexts, *, require_all=False):
    """Check provided detached runtime contracts against the one policy seal.

    Engine entry uses require_all. Individual independent assessment projections
    intentionally omit the feature currently being assessed; provided contexts
    must still be identical and persistence validates the complete input document.
    """
    effective = verify_config(config)
    if effective is None:
        return
    for name, value in contexts.items():
        target = {'work_periods': 'work_period_input'}.get(name, name)
        if name.endswith('_calendar'):
            target = 'calendar'
            if value is None:
                continue
        if target not in effective:
            continue
        if value is not None and name in {'work_periods', 'preference_input', 'combination_input'}:
            value = value.input_snapshot
        if (value is not None or require_all) and canonical_json(value) != canonical_json(effective[target]):
            raise ValueError(f'{name} differs from the resolved institution policy.')
