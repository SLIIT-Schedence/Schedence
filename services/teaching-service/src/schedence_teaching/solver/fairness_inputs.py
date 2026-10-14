"""Opt-in fairness configuration; reuse Step 10C.1 cohorts and workload policies."""

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from math import lcm
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText
from .results import SolverInputError


class FairnessPriority(StrEnum):
    EXISTING_OBJECTIVES_THEN_FAIRNESS = "EXISTING_OBJECTIVES_THEN_FAIRNESS"
    DELIVERY_COST_THEN_FAIRNESS_THEN_PREFERENCES = "DELIVERY_COST_THEN_FAIRNESS_THEN_PREFERENCES"
    DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES = "DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES"


class FairnessCohortRule(StrEnum):
    INPUT_ELIGIBILITY_AND_FIXED_CALENDAR = "INPUT_ELIGIBILITY_AND_FIXED_CALENDAR"


class FairnessDeviationReference(StrEnum):
    TEACHING_TARGET = "TEACHING_TARGET"


Limit = Annotated[int, Field(strict=True, gt=0, le=2**50)]


class FairnessOptimizationConfig(InputContract):
    """Explicit research strategy, with exact scaling and resource limits."""

    priority: FairnessPriority
    priority_policy_reference: RequiredText
    deviation_reference: FairnessDeviationReference
    cohort_rule: FairnessCohortRule
    # Required for real data; source profiles still carry individual evidence.
    target_verification_reference: RequiredText | None = None
    policy_approval_reference: RequiredText | None = None
    max_normalization_scale: Limit = 1_000_000
    max_weight_scale: Limit = 1_000_000
    max_integer_bound: Limit = 2**50
    max_comparison_staff: Limit = 1_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000
    # Weights reference existing cohort IDs; do not define another cohort model.
    cohort_weights: dict[RequiredText, Fraction] = Field(default_factory=dict)
    cohort_weight_reference: RequiredText | None = None

    @field_validator("cohort_weights", mode="before")
    @classmethod
    def exact_weights(cls, value):
        from schedence_teaching.fairness.contracts import ExactPositive
        from pydantic import TypeAdapter
        if not isinstance(value, dict):
            raise ValueError("cohort weights must be a mapping")
        adapter = TypeAdapter(ExactPositive)
        return {key: adapter.validate_python(weight) for key, weight in value.items()}

    @model_validator(mode="after")
    def referenced_weights(self) -> Self:
        if bool(self.cohort_weights) != (self.cohort_weight_reference is not None):
            raise ValueError("cohort weights require an explicit reference, and vice versa")
        return self


@dataclass(frozen=True)
class CheckedFairnessOptimization:
    policy: object
    calendar: object
    config: FairnessOptimizationConfig
    targets: tuple[tuple[str, int], ...]
    cohorts: tuple[tuple[str, tuple[str, ...]], ...]
    exclusions: tuple[tuple[str, tuple[str, ...]], ...]
    opportunities: tuple
    normalization_scale: int
    weight_scale: int
    integer_weights: tuple[tuple[str, int], ...]
    workload_bounds: tuple[tuple[str, int], ...]
    deviation_bounds: tuple[tuple[str, int], ...]
    maximum_bound: int
    aggregate_bound: int


def checked_fairness_inputs(prepared, config, policy, calendar, options, *, staff_input, work_periods=None):
    from schedence_teaching.fairness import FairnessPolicy, Normalization, BalanceMetric
    from schedence_teaching.fairness.assessment import (
        _snapshot, _workload, _appointment_evidence, _groups, _opportunities,
    )
    from schedence_teaching.preferences.scoring import _calendar
    from .eligibility import candidate_pools
    from .staff_inputs import checked_staff_inputs

    if staff_input is None:
        raise SolverInputError("Fairness optimization requires original staff calendars and explicit teaching workload profiles.")
    staff_input = checked_staff_inputs(prepared, config, staff_input)
    try:
        policy, options = _snapshot(policy, FairnessPolicy), _snapshot(options, FairnessOptimizationConfig)
        calendar = _calendar(config, calendar, staff_input, work_periods)
        if policy.week_reference != calendar.week_reference:
            raise ValueError("Fairness policy and calendar must use the applicable academic week.")
        if policy.normalization is not Normalization.TEACHING_TARGET or policy.balance_metric is not BalanceMetric.MAXIMUM_DEVIATION:
            raise ValueError("Fairness scheduling requires teaching-target normalization and maximum-deviation policy; variance/raw policies remain assessment-only.")
        if not calendar.synthetic_data and (options.target_verification_reference is None or options.policy_approval_reference is None):
            raise ValueError("Real-data fairness requires explicit target verification and fairness policy approval references.")
        people = {row.staff_id for row in prepared.input_snapshot.staff_members}
        referenced = ({identity for group in policy.comparison_groups for identity in group.staff_ids}
            | {row.staff_id for row in policy.staff_decisions} | policy.staff_weights.keys())
        if referenced - people:
            raise ValueError("Fairness policy references unknown staff IDs.")
        offerings = {row.offering_id for row in prepared.input_snapshot.module_offerings}
        scope = policy.eligibility_offering_ids if policy.eligibility_offering_ids is not None else offerings
        if scope - offerings:
            raise ValueError("Fairness scope references unknown offering IDs.")
        _, resolved, unresolved = _workload(prepared, None, staff_input)
        pools = candidate_pools(prepared.input_snapshot)
        availability = {row.staff_id: row for row in staff_input.availability}
        opportunities = {identity: _opportunities(identity, prepared, config, calendar, pools, scope,
            availability.get(identity), work_periods, None) for identity in sorted(people)}
        groups = _groups(policy, opportunities)
        group_ids = {row.group_id for row in groups}
        if options.cohort_weights.keys() - group_ids:
            raise ValueError("Cohort weight references an unknown comparison group.")
        requested = {identity for row in groups for identity in row.staff_ids}
        decisions = {row.staff_id: row for row in policy.staff_decisions}
        targets, exclusions = {}, {}
        for identity in sorted(people):
            reasons = []
            if identity not in requested:
                reasons.append("outside_comparison_groups")
            decision = decisions.get(identity)
            if decision is not None and not decision.include:
                reasons.append(f"explicit_exclusion: {decision.reason}")
            if reasons:
                exclusions[identity] = tuple(reasons)
                continue
            target = resolved.get(identity)
            missing = list(_appointment_evidence(identity, work_periods, calendar))
            if identity in unresolved or target is None or target.required_teaching_minutes is None or target.required_teaching_minutes <= 0:
                missing.append("missing_positive_separate_teaching_target")
            if identity not in availability:
                missing.append("missing_staff_availability")
            if missing:
                raise ValueError("Required fairness evidence is unresolved: " + ", ".join(sorted(set(missing))))
            opportunity = opportunities[identity]
            if not opportunity.eligible_occurrence_ids:
                reasons.append("no_eligible_demand_in_scope")
            elif opportunity.individually_available_occurrence_ids == ():
                reasons.append("no_individual_fit_under_supplied_fixed_hard_restrictions")
            if reasons:
                exclusions[identity] = tuple(reasons)
            else:
                targets[identity] = target.required_teaching_minutes
        if not targets:
            raise ValueError("Fairness optimization needs at least one eligible comparison member with a positive teaching target.")
        if len(targets) > options.max_comparison_staff:
            raise ValueError("Fairness comparison staff limit exceeded; no members were dropped.")
        cohorts, owner = [], {}
        weights = {}
        for group in groups:
            members = tuple(sorted(set(group.staff_ids) & targets.keys()))
            cohorts.append((group.group_id, members))
            for identity in members:
                if identity in owner:
                    raise ValueError("Optimization cohorts must be disjoint; resolve overlapping membership explicitly rather than double weighting staff.")
                owner[identity] = group.group_id
                weights[identity] = (policy.staff_weights.get(identity, Fraction(1))
                    * options.cohort_weights.get(group.group_id, Fraction(1)))
        scale = weight_scale = 1
        for target in targets.values():
            scale = lcm(scale, target)
            if scale > options.max_normalization_scale:
                raise ValueError("Exact teaching-target normalization scale limit exceeded; no rounding was applied.")
        for weight in weights.values():
            weight_scale = lcm(weight_scale, weight.denominator)
            if weight_scale > options.max_weight_scale:
                raise ValueError("Exact fairness weight scale limit exceeded; no rounding was applied.")
        integer_weights = {identity: int(weight * weight_scale) for identity, weight in weights.items()}
        workload_bounds = {identity: sum(row.duration_minutes for row in prepared.sessions if identity in pools[row.offering_id])
                           for identity in targets}
        deviation_bounds = {identity: max(scale, abs(workload_bounds[identity] * (scale // target) - scale))
                            for identity, target in targets.items()}
        maximum = max(deviation_bounds.values())
        aggregate = sum(integer_weights[identity] * bound for identity, bound in deviation_bounds.items())
        if max(maximum, aggregate, scale, weight_scale,
               *(workload_bounds[key] * (scale // targets[key]) + scale for key in targets),
               *integer_weights.values()) > options.max_integer_bound:
            raise ValueError("Fairness integer expression bound exceeded; narrow inputs, targets or weights.")
        return CheckedFairnessOptimization(policy, calendar, options, tuple(sorted(targets.items())), tuple(cohorts),
            tuple(sorted(exclusions.items())), tuple(opportunities.items()), scale, weight_scale,
            tuple(sorted(integer_weights.items())), tuple(sorted(workload_bounds.items())),
            tuple(sorted(deviation_bounds.items())), maximum, aggregate)
    except ValueError as error:
        # Existing snapshot helpers already sanitize model validation errors.
        raise SolverInputError(str(error)) from None


def fairness_objective_plan(prepared, context, preference_context, combination_config):
    from .deliveries import DeliveryObjective
    from .preference_inputs import objective_plan
    if preference_context is not None:
        prefix, bounds, _ = objective_plan(prepared, preference_context, combination_config)
    else:
        mode = combination_config.objective if combination_config else DeliveryObjective.DELIVERIES_THEN_MINUTES
        prefix = {DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
            DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
            DeliveryObjective.FEASIBILITY: ()}[mode]
        bounds = {"DELIVERIES": len(prepared.sessions), "CONTACT_MINUTES": sum(row.duration_minutes for row in prepared.sessions)}
    bounds = {**bounds, "MAXIMUM_TARGET_DEVIATION": context.maximum_bound,
              "AGGREGATE_TARGET_DEVIATION": context.aggregate_bound}
    if context.config.priority is FairnessPriority.DELIVERY_COST_THEN_OVERAGE_FAIRNESS_PREFERENCES:
        overage_bound=sum(max(0,upper-dict(context.targets)[sid]) for sid,upper in context.workload_bounds)
        if overage_bound>context.config.max_integer_bound:raise SolverInputError('Teaching overage objective exceeds the configured integer limit.')
        bounds['TOTAL_OVER_TARGET_MINUTES']=overage_bound
        order=(*[key for key in prefix if key!='PREFERENCE_PENALTY'], 'TOTAL_OVER_TARGET_MINUTES',
            'MAXIMUM_TARGET_DEVIATION','AGGREGATE_TARGET_DEVIATION',
            *[key for key in prefix if key=='PREFERENCE_PENALTY'])
    elif context.config.priority is FairnessPriority.DELIVERY_COST_THEN_FAIRNESS_THEN_PREFERENCES:
        order=(*[key for key in prefix if key!='PREFERENCE_PENALTY'],
            'MAXIMUM_TARGET_DEVIATION','AGGREGATE_TARGET_DEVIATION',
            *[key for key in prefix if key=='PREFERENCE_PENALTY'])
    else:
        order = (*prefix, "MAXIMUM_TARGET_DEVIATION", "AGGREGATE_TARGET_DEVIATION")
    return order, {key: bounds[key] for key in order}
