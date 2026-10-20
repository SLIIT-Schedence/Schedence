"""Academic Admin scheduling rules mapped onto existing solver contracts. Experimental; never university-approved.

Only settings that an existing solver contract implements are accepted:

* lecturer day-part and day preferences (``LecturerPreferenceSettings``; Not Available stays a hard block),
* workload fairness against each person's own approved target (``FairnessPolicy`` + ``FairnessOptimizationConfig``),
* daily-load balance for students and/or staff (``DailyLoadDistributionConfig``),
* a maximum preferred continuous teaching run and idle-gap minimisation for lecturers and/or student groups
  (``ConsecutivePolicy`` + ``ConsecutiveOptimizationConfig``),

combined through one explicit ``ObjectiveStagePlan``. Clash prevention, staff eligibility, venue suitability and
capacity, availability windows, Not Available blocks, the confirmed student lunch periods and the approved workload
targets/caps are protected: they are read from the prepared snapshot and never changed here. Nothing is solved or
stored; the bound document is validated by the existing replay and the engine's own objective-order checks.
"""

import json
from typing import Annotated, Self

from pydantic import Field, StrictBool, model_validator

from schedence_teaching.persistence.contracts import SnapshotDocument, SourceDocument
from schedence_teaching.persistence.generation_inputs import evidence_source
from schedence_teaching.persistence.snapshots import canonical_json, content_digest, replay_snapshot
from schedence_teaching.schemas.models import InputContract

CALENDAR_SOURCE = "PP1:calendar-configuration"
RULES_SOURCE = "EXPERIMENTAL:scheduling-rules"
PEAK = "DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES"
# The tested P3.1 order (P2.1 thin days is not offered); a plan lists exactly the enabled criteria in this order.
TESTED_ORDER = ("MAX_STUDENT_DAILY_MINUTES", "MAX_STAFF_DAILY_MINUTES", "THIN_TEACHING_DAYS", "CONSECUTIVE_PENALTY",
                "PREFERENCE_PENALTY", "AGGREGATE_TARGET_DEVIATION")
Minute = Annotated[int, Field(strict=True, ge=0, le=1440)]
Weight = Annotated[int, Field(strict=True, ge=0, le=10)]


class RulesError(ValueError):
    """A rule combination the existing contracts reject; reported, never repaired."""


class LecturerPreferenceRules(InputContract):
    morning_start_minute: Minute
    afternoon_start_minute: Minute  # Morning ends where afternoon starts.
    afternoon_end_minute: Minute
    preferred_day_part_weight: Weight
    not_preferred_day_part_weight: Weight
    preferred_free_day_weight: Weight
    weekend_pattern_weight: Weight

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.morning_start_minute < self.afternoon_start_minute < self.afternoon_end_minute:
            raise ValueError("morning must start before afternoon, which must end after it starts")
        if any(value % 30 for value in (self.morning_start_minute, self.afternoon_start_minute, self.afternoon_end_minute)):
            raise ValueError("day-part boundaries must be on the half hour")
        return self


class ConsecutiveRules(InputContract):
    maximum_preferred_continuous_minutes: Annotated[int, Field(strict=True, ge=60, le=600, multiple_of=30)] | None = None
    minimize_idle_gaps: StrictBool = False
    apply_to_lecturers: StrictBool = True
    apply_to_student_groups: StrictBool = False
    penalty_weight: Annotated[int, Field(strict=True, ge=1, le=10)] = 1

    @model_validator(mode="after")
    def explicit(self) -> Self:
        if self.maximum_preferred_continuous_minutes is None and not self.minimize_idle_gaps:
            raise ValueError("choose a maximum preferred continuous run, idle-gap minimisation, or both")
        if not (self.apply_to_lecturers or self.apply_to_student_groups):
            raise ValueError("apply consecutive-teaching rules to lecturers, student groups, or both")
        return self


class SchedulingRules(InputContract):
    lecturer_preferences: LecturerPreferenceRules | None = None
    workload_fairness: StrictBool = False
    balance_student_daily_load: StrictBool = False
    balance_staff_daily_load: StrictBool = False
    consecutive: ConsecutiveRules | None = None


# The provisional v2 test values already exercised in the Year 1 pilots; labelled experimental, not approved.
EXPERIMENTAL_DEFAULTS = SchedulingRules(
    lecturer_preferences=LecturerPreferenceRules(morning_start_minute=510, afternoon_start_minute=750, afternoon_end_minute=1110,
        preferred_day_part_weight=1, not_preferred_day_part_weight=1, preferred_free_day_weight=1, weekend_pattern_weight=1),
    workload_fairness=True, balance_student_daily_load=True, balance_staff_daily_load=True,
    consecutive=ConsecutiveRules(maximum_preferred_continuous_minutes=240, minimize_idle_gaps=False,
                                 apply_to_lecturers=True, apply_to_student_groups=False, penalty_weight=1))


def rules_reference(rules):
    return f"EXPERIMENTAL:scheduling-rules:{content_digest(rules)[:16]}"


def protected_rules(document):
    """The hard rules readiness reports as fixed, read from the snapshot itself."""
    contexts = document.contexts
    lunch = contexts.student_lunch_policy
    workload = contexts.staff_input.workload_input.policy if contexts.staff_input is not None else None
    availability = contexts.staff_input.availability if contexts.staff_input is not None else ()
    windows = sorted({(w.start_minute, w.end_minute) for day in document.solver_config.days for rows in day.windows.values() for w in rows})
    return dict(
        lunch_periods=[dict(academic_year=row.academic_year, start_minute=row.window.start_minute, end_minute=row.window.end_minute)
                       for row in lunch.year_periods] if lunch is not None else [],
        lunch_enforced=contexts.student_lunch_config is not None,
        workload_policy_reference=workload.policy_reference if workload is not None else None,
        weekly_targets=[dict(band=str(getattr(band, "value", band)), weekly_target_minutes=rule.weekly_target_minutes)
                        for band, rule in sorted(workload.band_rules.items(), key=lambda item: str(item[0]))] if workload is not None else [],
        staff_with_weekly_cap=sum(row.weekly_teaching_max_minutes is not None for row in availability),
        staff_with_daily_cap=sum(row.daily_teaching_max_minutes is not None for row in availability),
        teaching_window_minutes=[dict(start_minute=start, end_minute=end) for start, end in windows],
        not_available_handling="HARD_UNAVAILABILITY",
    )


def _replace_configuration_source(raw, configuration):
    """Re-bind the calendar-configuration evidence to the changed rules (its checksum changes with them)."""
    sources = []
    for row in raw["sources"]:
        row = row if isinstance(row, dict) else row.model_dump(round_trip=True)
        if row["label"] == CALENDAR_SOURCE:
            provenance = dict(row["provenance"], configuration=json.loads(canonical_json(configuration)))
            row = dict(row, provenance=provenance, sha256=content_digest(provenance))
        sources.append(row)
    raw["sources"] = sources


def objective_requirements(document):
    """The engine's own pre-model objective planning for a document (solve_timetable), without building a model."""
    from schedence_teaching.solver.consecutive_inputs import checked_consecutive_inputs, consecutive_objective_plan
    from schedence_teaching.solver.deliveries import delivery_alternatives
    from schedence_teaching.solver.eligibility import candidate_pools
    from schedence_teaching.solver.fairness_inputs import checked_fairness_inputs, fairness_objective_plan
    from schedence_teaching.solver.preference_inputs import checked_preference_inputs, objective_plan
    _, prepared, config, contexts = replay_snapshot(document)
    combination = contexts.get("combination_config")
    staff, periods = contexts.get("staff_input"), contexts.get("work_periods")
    preference = fairness = consecutive = None
    preference_order = fairness_order = consecutive_order = None
    if contexts.get("preference_input") is not None:
        preference = checked_preference_inputs(prepared, config, contexts["preference_input"], contexts["preference_calendar"],
            contexts["preference_policy"], contexts["preference_config"], staff_input=staff, work_periods=periods)
        preference_order = objective_plan(prepared, preference, combination)[0]
    if contexts.get("fairness_config") is not None:
        fairness = checked_fairness_inputs(prepared, config, contexts["fairness_policy"], contexts["fairness_calendar"],
            contexts["fairness_config"], staff_input=staff, work_periods=periods)
        fairness_order = fairness_objective_plan(prepared, fairness, preference, combination)[0]
    if contexts.get("consecutive_config") is not None:
        alternatives = delivery_alternatives(prepared, contexts.get("combination_input"), pools=candidate_pools(prepared.input_snapshot))
        consecutive = checked_consecutive_inputs(prepared, config, contexts["consecutive_policy"], contexts["consecutive_calendar"],
            contexts["consecutive_config"], alternatives, staff_input=staff, work_periods=periods,
            preference_calendar=contexts.get("preference_calendar"), fairness_calendar=contexts.get("fairness_calendar"))
        consecutive_order = consecutive_objective_plan(prepared, consecutive, fairness, preference, combination)[0]
    from schedence_teaching.solver.deliveries import DeliveryObjective
    mode = combination.objective if combination else DeliveryObjective.DELIVERIES_THEN_MINUTES
    teaching = {DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
                DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
                DeliveryObjective.FEASIBILITY: ()}[mode]
    base = tuple(fairness_order or preference_order or teaching)
    extra = {row.value for row in config.daily_load_distribution.criteria} if config.daily_load_distribution else set()
    if config.teaching_day_compactness is not None:
        extra.add("THIN_TEACHING_DAYS")
    if consecutive is not None:
        extra.add("CONSECUTIVE_PENALTY")
    return config, base, extra, fairness_order, consecutive_order, fairness is not None


def check_objective_order(document):
    """The engine's stage-plan checks (unified order and module projections); raises RulesError on any mismatch."""
    from schedence_teaching.solver.objective_stage_plan import _protected, project, unified_order
    try:
        config, base, extra, fairness_order, consecutive_order, fairness_enabled = objective_requirements(document)
        staff = document.contexts.staff_input
        if (staff is not None and staff.assignment_policy is not None and
                staff.assignment_policy.require_target_optimization and not fairness_enabled):
            raise ValueError("the snapshot's teaching assignment policy requires workload fairness")
        quality = config.teaching_day_compactness is not None or config.daily_load_distribution is not None
        if staff is None and (config.teaching_day_compactness is not None or "MAX_STAFF_DAILY_MINUTES" in extra):
            raise ValueError("staff daily load and teaching-day compactness require staff scheduling input")
        plan = config.objective_stage_plan
        if plan is None:
            if quality:
                if consecutive_order is not None or (document.contexts.preference_input is not None and not fairness_enabled):
                    raise ValueError("combining daily-load or compactness with these objectives needs an explicit stage plan")
                from schedence_teaching.solver.daily_load_inputs import quality_order
                return quality_order(base, compactness=config.teaching_day_compactness,
                    daily=config.daily_load_distribution, fairness_enabled=fairness_enabled)
            return tuple(consecutive_order or base)
        order = unified_order(plan, base, extra, compactness=config.teaching_day_compactness,
                              daily=config.daily_load_distribution, fairness_enabled=fairness_enabled)
        if fairness_order is not None:
            project(plan, fairness_order)
        if consecutive_order is not None:
            project(plan, consecutive_order)
        assert set(order) >= set(_protected(base))
        return order
    except (ValueError, AssertionError) as error:
        raise RulesError(str(error)) from None


def apply_rules(document, rules, academic):
    """A NEW detached document with the rules bound; the caller's document is never mutated."""
    try:
        return _apply_rules(document, rules, academic)
    except ValueError as error:
        raise RulesError(str(error)) from None


def _apply_rules(document, rules, academic):
    from schedence_teaching.consecutive.contracts import ConsecutivePolicy
    from schedence_teaching.fairness import FairnessPolicy
    from schedence_teaching.fairness.assessment import _workload
    from schedence_teaching.pilot.pp1 import PP1Configuration
    from schedence_teaching.preferences.product_settings import (LecturerPreferenceSettings, apply_hard_unavailability,
        build_lecturer_preferences)
    from schedence_teaching.solver import FairnessOptimizationConfig, SolverConfig
    from schedence_teaching.solver.consecutive_inputs import ConsecutiveOptimizationConfig
    from schedence_teaching.solver.preference_inputs import PreferenceOptimizationConfig

    rules = SchedulingRules.model_validate(rules.model_dump() if isinstance(rules, SchedulingRules) else rules)
    reference = rules_reference(rules)
    _, prepared, config, contexts = replay_snapshot(document)
    if config.resolved_policy is not None:
        raise RulesError("sealed institution snapshots keep their approved rules; experimental rules apply to unsealed snapshots only")
    if contexts.get("consecutive_policy") is not None and contexts["consecutive_policy"].confirmed_hard_minimum_break is not None:
        raise RulesError("snapshots with a confirmed hard minimum break keep that policy; experimental replacement is unsupported")
    if contexts.get("staff_input") is None:
        raise RulesError("scheduling rules require the snapshot's staff scheduling input")
    assignment = contexts["staff_input"].assignment_policy
    if assignment is not None and assignment.require_target_optimization and not rules.workload_fairness:
        raise RulesError("the snapshot's teaching assignment policy requires workload fairness")
    configuration = PP1Configuration.model_validate(evidence_source(document, CALENDAR_SOURCE)["configuration"])
    week = document.calendar.week_reference
    raw = document.model_dump(round_trip=True)
    solver = config.model_dump(round_trip=True)
    # This contract is a complete selection, so disabled switches must remove old soft objectives.
    solver.update(daily_load_distribution=None, objective_stage_plan=None)
    for name in ("preference_input", "preference_policy", "preference_config", "fairness_policy", "fairness_config",
                 "consecutive_policy", "consecutive_config"):
        raw["contexts"][name] = None
    daily = [name for name, enabled in (("MAX_STUDENT_DAILY_MINUTES", rules.balance_student_daily_load),
                                        ("MAX_STAFF_DAILY_MINUTES", rules.balance_staff_daily_load)) if enabled]
    if daily:
        solver["daily_load_distribution"] = dict(policy_reference=reference, criteria=daily,
            placement="BEFORE_AGGREGATE_TARGET_DEVIATION" if rules.workload_fairness else "AFTER_WORKLOAD_STAGES",
            relative_to_compactness="DAILY_LOAD_THEN_COMPACTNESS" if config.teaching_day_compactness is not None else None)
    solver = SolverConfig.model_validate(solver)
    staff_input = contexts["staff_input"]
    settings = None
    if rules.lecturer_preferences is not None:
        p = rules.lecturer_preferences
        settings = LecturerPreferenceSettings.model_validate(dict(settings_reference=f"{reference}:lecturer-preferences",
            time_of_day_windows=dict(MORNING=dict(start_minute=p.morning_start_minute, end_minute=p.afternoon_start_minute),
                                     AFTERNOON=dict(start_minute=p.afternoon_start_minute, end_minute=p.afternoon_end_minute)),
            weights=dict(PREFERRED_DAY_PART=p.preferred_day_part_weight, NOT_PREFERRED_DAY_PART=p.not_preferred_day_part_weight,
                         PREFERRED_FREE_DAY=p.preferred_free_day_weight, WEEKEND_WORK_PATTERN=p.weekend_pattern_weight),
            not_available_handling="HARD_UNAVAILABILITY", preference_streams={stream for day in solver.days for stream in day.windows},
            weekend_pattern_values=configuration.weekend_preference_values))
        streams = {stream for day in solver.days for stream in day.windows}
        build = build_lecturer_preferences(academic, settings, configuration.calendar, configured_streams=streams)
        staff_input = apply_hard_unavailability(staff_input, build.blocked_periods)
        preference_config = PreferenceOptimizationConfig(priority_policy_reference=settings.settings_reference,
            calendar_semantics_reference=settings.settings_reference, day_streams={row.day: set(row.windows) for row in solver.days},
            unscoped_preferences_apply_to_all_streams=build.unscoped_preferences_apply_to_all_streams,
            max_model_variables=configuration.max_model_variables, max_model_constraints=configuration.max_model_constraints)
        from schedence_teaching.preferences import prepare_lecturer_preferences
        from schedence_teaching.solver import SolverInputError
        from schedence_teaching.solver.preference_inputs import NO_APPLICABLE_PREFERENCES, checked_preference_inputs
        try:
            checked_preference_inputs(prepared, solver, prepare_lecturer_preferences(document.teaching_input, build.preferences),
                document.calendar, build.scoring_policy, preference_config, staff_input=staff_input,
                work_periods=contexts.get("work_periods"))
        except SolverInputError as error:
            if str(error) != NO_APPLICABLE_PREFERENCES:
                raise
        else:
            raw["contexts"].update(preference_input=build.preferences, preference_policy=build.scoring_policy,
                preference_config=preference_config)
        raw["contexts"]["staff_input"] = staff_input
    elif configuration.lecturer_preference_settings is None and any(
            value == "Not Available" for row in academic.source_preferences for value in row.source_values.values()):
        raise RulesError("stored Not Available periods need explicit day-part boundaries; enable lecturer preferences to supply them")
    if rules.workload_fairness:
        _, resolved, _ = _workload(prepared, None, staff_input)
        holders = sorted(key for key, value in resolved.items() if value.required_teaching_minutes and value.required_teaching_minutes > 0)
        if not holders:
            raise RulesError("workload fairness needs staff with an approved positive teaching target; none are configured")
        target_reference = staff_input.workload_input.policy.policy_reference
        raw["contexts"].update(
            fairness_policy=FairnessPolicy(policy_reference=f"{reference}:teaching-target", week_reference=week,
                provenance=dict(source="APPROVED_WORKLOAD_TARGETS_IN_PREPARED_SNAPSHOT", reference=target_reference),
                comparison_scope="EXPLICIT_GROUPS", comparison_groups=[dict(group_id="TEACHING-TARGET-HOLDERS", staff_ids=holders,
                    reason="Every staff member with an approved positive teaching target; deviation is relative to each own target.")]),
            fairness_config=FairnessOptimizationConfig(priority=PEAK, priority_policy_reference=f"{reference}:fairness-priority",
                deviation_reference="TEACHING_TARGET", cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR",
                target_verification_reference=target_reference, policy_approval_reference=f"{reference}:NOT_UNIVERSITY_APPROVED",
                max_model_variables=1_000_000, max_model_constraints=2_000_000))
    if rules.consecutive is not None:
        c = rules.consecutive
        criteria = {"EXCESS_CONTINUOUS_MINUTES"} if c.maximum_preferred_continuous_minutes is not None else set()
        raw["contexts"].update(
            consecutive_policy=ConsecutivePolicy.model_validate(dict(policy_reference=f"{reference}:consecutive", week_reference=week,
                provenance=dict(source="EXPERIMENTAL_SCHEDULING_RULES", reference=reference),
                score_lecturers=c.apply_to_lecturers, score_student_groups=c.apply_to_student_groups, soft_criteria=criteria,
                maximum_preferred_continuous_minutes=c.maximum_preferred_continuous_minutes,
                excess_continuous_minute_penalty=1 if criteria else None,
                idle_gap_minute_penalty=1 if c.minimize_idle_gaps else None,
                idle_gap_policy_reference=f"{reference}:idle-gaps" if c.minimize_idle_gaps else None)),
            consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
                priority_policy_reference=f"{reference}:consecutive-priority", lecturer_penalty_weight=c.penalty_weight,
                student_group_penalty_weight=c.penalty_weight, formulation="TIME_INDEXED",
                max_model_variables=500_000, max_model_constraints=1_000_000))

    def bind(solver_config):
        bound = dict(raw, solver_config=solver_config)
        updated = PP1Configuration.model_validate(configuration.model_dump(round_trip=True) | dict(
            solver_config=solver_config, lecturer_preference_settings=settings))
        _replace_configuration_source(bound, updated)
        bound["sources"] = [row for row in bound["sources"] if row["label"] != RULES_SOURCE] + [SourceDocument(
            label=RULES_SOURCE, owner="TEACHING_SERVICE", provenance_kind="EXPERIMENTAL_CONFIGURATION",
            sha256=content_digest(dict(rules=rules.model_dump(mode="json"), reference=reference, university_approved=False)),
            provenance=dict(rules=rules.model_dump(mode="json"), reference=reference, university_approved=False))]
        return SnapshotDocument.model_validate(bound)

    try:
        unplanned = bind(solver)
        _, base, extra, fairness_order, consecutive_order, _ = objective_requirements(unplanned)
        protected = len(_protected_prefix(base))
        required = set(base[protected:]) | extra
        if fairness_order is not None or consecutive_order is not None or extra:
            unknown = required - set(TESTED_ORDER)
            if unknown:
                raise RulesError(f"no tested stage order exists for {sorted(unknown)}")
            solver = SolverConfig.model_validate(solver.model_dump(round_trip=True) | dict(
                objective_stage_plan=dict(plan_reference=reference, order=[key for key in TESTED_ORDER if key in required])))
        document = bind(solver)
        check_objective_order(document)
        return document
    except RulesError:
        raise
    except ValueError as error:
        raise RulesError(str(error)) from None


def _protected_prefix(base):
    from schedence_teaching.solver.objective_stage_plan import _protected
    return _protected(base)


def connected_rules(document):
    """What the bound document actually hands the solver (for reporting), read back from its own contracts."""
    config, contexts = document.solver_config, document.contexts
    settings = evidence_source(document, CALENDAR_SOURCE)["configuration"].get("lecturer_preference_settings")
    return dict(
        lecturer_preferences=contexts.preference_input is not None,
        day_part_settings=settings is not None,
        workload_fairness=contexts.fairness_config is not None,
        daily_load=[row.value for row in config.daily_load_distribution.criteria] if config.daily_load_distribution else [],
        consecutive=contexts.consecutive_policy is not None,
        idle_gaps=bool(contexts.consecutive_policy and contexts.consecutive_policy.idle_gap_minute_penalty is not None),
        objective_order=[row.value for row in config.objective_stage_plan.order] if config.objective_stage_plan else [],
    )
