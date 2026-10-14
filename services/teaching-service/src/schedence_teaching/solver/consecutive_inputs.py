"""Opt-in consecutive scheduling, using the Step 11A policy and date contract."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StrictBool

from schedence_teaching.schemas.models import InputContract, RequiredText
from .results import SolverInputError


class ConsecutivePriority(StrEnum):
    EXISTING_OBJECTIVES_THEN_CONSECUTIVE = "EXISTING_OBJECTIVES_THEN_CONSECUTIVE"


Limit = Annotated[int, Field(strict=True, gt=0, le=2**50)]
Weight = Annotated[int, Field(strict=True, ge=0, le=2**50)]


class ConsecutiveOptimizationConfig(InputContract):
    priority: ConsecutivePriority
    priority_policy_reference: RequiredText
    lecturer_penalty_weight: Weight = 1
    student_group_penalty_weight: Weight = 1
    enforce_confirmed_hard_break: StrictBool = False
    policy_approval_reference: RequiredText | None = None
    max_participants: Limit = 500
    max_nodes_per_participant: Limit = 60
    max_successor_arcs: Limit = 20_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000
    max_integer_bound: Limit = 2**50


@dataclass(frozen=True)
class CheckedConsecutiveOptimization:
    policy: object
    calendar: object
    config: ConsecutiveOptimizationConfig
    # Existing delivery IDs only. Attendance deduplicated per entity, including descendants.
    participants: tuple[tuple[str, str, tuple[str, ...]], ...]
    entity_bounds: tuple[tuple[str, str, int], ...]
    penalty_bound: int
    estimated_arcs: int


def checked_consecutive_inputs(prepared, config, policy, calendar, options, alternatives, *,
        staff_input=None, work_periods=None, preference_calendar=None, fairness_calendar=None):
    from schedence_teaching.consecutive import ConsecutivePolicy
    from schedence_teaching.consecutive.assessment import _snapshot
    from schedence_teaching.consecutive.metrics import missing_scoring_configuration, policy_applies, scoring_requested
    from schedence_teaching.preferences.scoring import _calendar
    try:
        policy = _snapshot(policy, ConsecutivePolicy)
        options = _snapshot(options, ConsecutiveOptimizationConfig)
        calendar = _calendar(config, calendar, staff_input, work_periods)
        if policy.week_reference != calendar.week_reference:
            raise ValueError("Consecutive policy/calendar academic weeks differ.")
        if any(other is not None and calendar != other for other in (preference_calendar, fairness_calendar)):
            raise ValueError("Consecutive, preference and fairness calendars must agree.")
        if not any(policy_applies(policy, on_date) for on_date in calendar.day_dates.values()):
            raise ValueError("Consecutive policy does not apply to any configured scheduling date.")
        if not calendar.synthetic_data and options.policy_approval_reference is None:
            raise ValueError("Real-data consecutive optimization requires an explicit policy approval reference.")
        missing = missing_scoring_configuration(policy)
        if missing:
            raise ValueError("Missing consecutive optimization policy: " + ", ".join(missing))
        soft = scoring_requested(policy) and (policy.score_lecturers or policy.score_student_groups)
        if not soft and not options.enforce_confirmed_hard_break:
            raise ValueError("Enable explicit soft scoring criteria/scopes or confirmed hard-break enforcement.")
        hard = policy.confirmed_hard_minimum_break
        if options.enforce_confirmed_hard_break and hard is None:
            raise ValueError("Hard-break enforcement requires an explicit confirmed minimum-break policy.")
        participants = {}
        for person in prepared.input_snapshot.staff_members:
            if policy.score_lecturers or (options.enforce_confirmed_hard_break and "LECTURER" in hard.applies_to):
                participants[("LECTURER", person.staff_id)] = set()
        for group in prepared.hierarchy.parents:
            if policy.score_student_groups or (options.enforce_confirmed_hard_break and "STUDENT_GROUP" in hard.applies_to):
                participants[("STUDENT_GROUP", group)] = set()
        for row in alternatives:
            for identity in row.eligible_staff_ids:
                if ("LECTURER", identity) in participants:
                    participants[("LECTURER", identity)].add(row.delivery_id)
            attending = set(row.group_ids)
            for group in row.group_ids:
                attending.update(prepared.hierarchy.descendants(group))
            for identity in attending:
                if ("STUDENT_GROUP", identity) in participants:
                    participants[("STUDENT_GROUP", identity)].add(row.delivery_id)
        if len(participants) > options.max_participants:
            raise ValueError("Consecutive participant limit exceeded; no participants were dropped.")
        total_arcs, bounds, weighted = 0, [], 0
        for (kind, identity), ids in sorted(participants.items()):
            nodes = len(ids)
            if nodes > options.max_nodes_per_participant:
                raise ValueError("Consecutive nodes-per-participant limit exceeded; narrow the input.")
            # Conservative preflight; incompatible coverage arcs are additionally pruned.
            arcs = nodes * (nodes - 1) + 2 * nodes + nodes + 1 if nodes else 0
            total_arcs += arcs
            scoring = policy.score_lecturers if kind == "LECTURER" else policy.score_student_groups
            bound = (arcs * ((policy.back_to_back_penalty or 0)
                + (policy.preferred_minimum_break_minutes or 0) * (policy.short_break_minute_penalty or 0)
                + 1440 * (policy.idle_gap_minute_penalty or 0))
                + nodes * 1440 * (policy.excess_continuous_minute_penalty or 0)) if scoring else 0
            bounds.append((kind, identity, bound))
            weighted += bound * (options.lecturer_penalty_weight if kind == "LECTURER" else options.student_group_penalty_weight)
            if max(bound, weighted) > options.max_integer_bound:
                raise ValueError("Consecutive integer expression bound exceeded; narrow inputs or explicit weights.")
        if total_arcs > options.max_successor_arcs:
            raise ValueError("Consecutive successor-arc limit exceeded; no pairs were silently dropped.")
        return CheckedConsecutiveOptimization(policy, calendar, options,
            tuple((kind, identity, tuple(sorted(ids))) for (kind, identity), ids in sorted(participants.items())),
            tuple(bounds), weighted, total_arcs)
    except ValueError as error:
        raise SolverInputError(str(error)) from None


def consecutive_objective_plan(prepared, context, fairness_context, preference_context, combination_config):
    if fairness_context is not None:
        from .fairness_inputs import fairness_objective_plan
        prefix, bounds = fairness_objective_plan(prepared, fairness_context, preference_context, combination_config)
    elif preference_context is not None:
        from .preference_inputs import objective_plan
        prefix, bounds, _ = objective_plan(prepared, preference_context, combination_config)
    else:
        from .deliveries import DeliveryObjective
        mode = combination_config.objective if combination_config else DeliveryObjective.DELIVERIES_THEN_MINUTES
        prefix = {DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
            DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
            DeliveryObjective.FEASIBILITY: ()}[mode]
        bounds = {"DELIVERIES": len(prepared.sessions), "CONTACT_MINUTES": sum(row.duration_minutes for row in prepared.sessions)}
    order = (*prefix, "CONSECUTIVE_PENALTY")
    return order, {key: (context.penalty_bound if key == "CONSECUTIVE_PENALTY" else bounds[key]) for key in order}
