"""Explicit opt-in configuration and checked Step 10A preference consumption."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StrictBool, ValidationError

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText

from .deliveries import DeliveryObjective
from .eligibility import candidate_pools
from .results import SolverInputError


class PreferencePriority(StrEnum):
    TEACHING_THEN_PREFERENCES = "TEACHING_THEN_PREFERENCES"
    PREFERENCES_THEN_TEACHING = "PREFERENCES_THEN_TEACHING"
    DELIVERIES_THEN_PREFERENCES_THEN_MINUTES = "DELIVERIES_THEN_PREFERENCES_THEN_MINUTES"


Limit = Annotated[int, Field(strict=True, gt=0)]


class PreferenceOptimizationConfig(InputContract):
    """Research priorities and explicit calendar semantics, not university defaults."""

    priority_policy_reference: RequiredText
    calendar_semantics_reference: RequiredText
    day_streams: dict[RequiredText, set[Stream]]
    unscoped_preferences_apply_to_all_streams: StrictBool
    priority: PreferencePriority = PreferencePriority.TEACHING_THEN_PREFERENCES
    max_preference_records: Limit = 10_000
    max_preference_terms: Limit = 100_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000


@dataclass(frozen=True)
class CheckedPreferenceOptimization:
    preferences: object
    calendar: object
    policy: object
    config: PreferenceOptimizationConfig
    units: tuple
    confirmed_criteria_count: int


def checked_preference_inputs(prepared, config, preferences, calendar, policy, options,
                              *, staff_input=None, work_periods=None):
    # Local imports preserve both fresh import orders of preferences and solver.
    from schedence_teaching.preferences.contracts import (
        PreferenceCategory, PreferenceConfirmation, PreferenceScoringPolicy, PreferenceWeightMode,
    )
    from schedence_teaching.preferences.preparation import (
        PreferenceInputError, PreparedLecturerPreferences, preference_units,
        prepare_lecturer_preferences, snapshot,
    )
    from schedence_teaching.preferences.scoring import _calendar, _scope_applies, _weight
    from schedence_teaching.workload.period_contracts import Weekday

    if not isinstance(preferences, PreparedLecturerPreferences) or not isinstance(options, PreferenceOptimizationConfig):
        raise SolverInputError("Preference optimization requires prepared preferences and explicit PreferenceOptimizationConfig.")
    try:
        options = snapshot(options, PreferenceOptimizationConfig)
        policy = snapshot(policy, PreferenceScoringPolicy)
        calendar = _calendar(config, calendar, staff_input, work_periods)
        if len(preferences.input_snapshot.preferences) > options.max_preference_records:
            raise SolverInputError("Preference record limit exceeded; no criteria were dropped.")
        canonical = prepare_lecturer_preferences(prepared.input_snapshot, preferences.input_snapshot)
        if canonical != preferences:
            raise SolverInputError("Stale preference preparation; regenerate source diagnostics.")
        if set(options.day_streams) != {day.day for day in config.days} or any(
            options.day_streams[day.day] != set(day.windows) for day in config.days
        ):
            raise SolverInputError("Confirmed day/stream semantics must match every configured day's stream windows.")
        if policy.weight_mode is PreferenceWeightMode.UNWEIGHTED:
            raise SolverInputError("Preference optimization requires an explicit weighted policy; UNWEIGHTED is assessment only.")
        if policy.weight_mode is PreferenceWeightMode.SYNTHETIC_DEFAULTS and not calendar.synthetic_data:
            raise SolverInputError("Synthetic default weights cannot activate real-data preference optimization.")
        pools = candidate_pools(prepared.input_snapshot)
        staff_streams = {}
        for session in prepared.sessions:
            for identity in pools[session.offering_id]:
                staff_streams.setdefault(identity, set()).add(session.stream)
        units = []
        for unit in preference_units(canonical.input_snapshot):
            row = unit[0]
            if row.confirmation_status is PreferenceConfirmation.REJECTED:
                continue
            streams = staff_streams.get(row.staff_id, set())
            streams = streams if row.stream is None else streams & {row.stream}
            applies = any(_scope_applies(row.scope, calendar, on_date)
                and (row.category not in {PreferenceCategory.TIME_OF_DAY, PreferenceCategory.TEACHING_WINDOW}
                     or not row.days or list(Weekday)[on_date.weekday()] in row.days)
                and (row.category is not PreferenceCategory.WEEKEND_DAY or on_date.weekday() >= 5)
                and bool(streams & set(day.windows))
                for day in config.days for on_date in [calendar.day_dates[day.day]])
            if not applies:
                continue
            if row.stream is None and not options.unscoped_preferences_apply_to_all_streams:
                raise SolverInputError("Preference stream scope is missing; explicitly confirm all-stream applicability or supply a stream.")
            for member in unit:
                if not calendar.synthetic_data and member.confirmation_status is not PreferenceConfirmation.CONFIRMED:
                    raise SolverInputError("Real-data optimization requires fully confirmed applicable preference contracts.")
                if _weight(member, policy)[0] is None:
                    raise SolverInputError("Applicable preference has no confirmed weight; incomplete scores cannot be optimized.")
                if member.category is PreferenceCategory.TIME_OF_DAY and member.time_of_day not in policy.time_of_day_windows:
                    raise SolverInputError("Applicable time-of-day preference needs explicit configured boundaries.")
            units.append(unit)
        if not units:
            raise SolverInputError("Preference optimization requires at least one applicable eligible-staff criterion.")
        confirmed = sum(all(row.confirmation_status is PreferenceConfirmation.CONFIRMED for row in unit) for unit in units)
        return CheckedPreferenceOptimization(canonical, calendar, policy, options, tuple(units), confirmed)
    except (PreferenceInputError, ValidationError) as error:
        if isinstance(error, ValidationError):
            message = "; ".join(row["msg"] for row in error.errors(include_input=False, include_context=False, include_url=False))
        else:
            message = str(error)
        raise SolverInputError(message) from None


def objective_plan(prepared, context, combination_config):
    """Mixed-radix coefficients dominate every lower-priority feasible change."""
    from schedence_teaching.preferences.scoring import _weight

    mode = combination_config.objective if combination_config is not None else DeliveryObjective.DELIVERIES_THEN_MINUTES
    teaching = {DeliveryObjective.DELIVERIES_THEN_MINUTES: ("DELIVERIES", "CONTACT_MINUTES"),
        DeliveryObjective.DELIVERIES: ("DELIVERIES",), DeliveryObjective.CONTACT_MINUTES: ("CONTACT_MINUTES",),
        DeliveryObjective.FEASIBILITY: ()}[mode]
    if context.config.priority is PreferencePriority.TEACHING_THEN_PREFERENCES:
        order = (*teaching, "PREFERENCE_PENALTY")
    elif context.config.priority is PreferencePriority.PREFERENCES_THEN_TEACHING:
        order = ("PREFERENCE_PENALTY", *teaching)
    else:
        if teaching != ("DELIVERIES", "CONTACT_MINUTES"):
            raise SolverInputError("Between-tier preference priority requires delivery-count then contact-minute mode.")
        order = ("DELIVERIES", "PREFERENCE_PENALTY", "CONTACT_MINUTES")
    staff_weights = {}
    for unit in context.units:
        row = unit[0]
        staff_weights[row.staff_id] = staff_weights.get(row.staff_id, 0) + _weight(row, context.policy)[0]
    # Exact coverage => physical bookings <= original occurrences. Each booking
    # has one staff and cannot violate more than all that staff's criteria.
    bounds = {"DELIVERIES": len(prepared.sessions),
              "CONTACT_MINUTES": sum(row.duration_minutes for row in prepared.sessions),
              "PREFERENCE_PENALTY": len(prepared.sessions) * max(staff_weights.values(), default=0)}
    coefficients, factor = {}, 1
    for criterion in reversed(order):
        coefficients[criterion] = factor
        factor *= bounds[criterion] + 1
    if sum(bounds[key] * coefficients[key] for key in order) > 2**60:
        raise SolverInputError("Preference lexicographic objective exceeds the safe integer bound; narrow inputs or weights.")
    return order, bounds, coefficients
