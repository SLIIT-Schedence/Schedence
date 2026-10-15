"""Compose existing pure preparations and native input validators, without CP."""

from dataclasses import dataclass

from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.solver.consecutive_inputs import checked_consecutive_inputs
from schedence_teaching.solver.deliveries import checked_combinations, delivery_alternatives
from schedence_teaching.solver.fairness_inputs import checked_fairness_inputs
from schedence_teaching.solver.inputs import checked_inputs
from schedence_teaching.solver.lunch_inputs import checked_student_lunch_inputs
from schedence_teaching.solver.preference_inputs import checked_preference_inputs
from schedence_teaching.solver.staff_inputs import checked_staff_inputs
from schedence_teaching.solver.venue_inputs import checked_venue_inputs
from schedence_teaching.solver.work_period_inputs import checked_work_period_inputs
from schedence_teaching.workload import prepare_work_periods

from .contracts import NormalizedSchedulingInput


class IntegrationContextError(ValueError):
    def __init__(self, field):
        self.field = field
        super().__init__("Existing scheduling context validation failed.")


def _checked(field, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError:
        raise IntegrationContextError(field) from None


@dataclass(frozen=True)
class PreparedScheduling:
    prepared: object
    config: object
    solver_inputs: dict


def prepare_scheduling(inputs: NormalizedSchedulingInput) -> PreparedScheduling:
    """Revalidate original contexts; creates no solver model or database state."""
    combinations = prepare_group_combinations(inputs.combination_input)
    prepared, config = checked_inputs(combinations.prepared, inputs.solver_config)
    staff = _checked("staff_input", checked_staff_inputs, prepared, config, inputs.staff_input)
    periods = _checked("work_period_input", prepare_work_periods, inputs.teaching_input, staff.workload_input, inputs.work_period_input)
    periods, period_config = _checked("work_period_config", checked_work_period_inputs, prepared, config, staff, periods, inputs.work_period_config)
    combinations, combination_config = checked_combinations(prepared, combinations, inputs.combination_config)
    venues = _checked("venue_input", checked_venue_inputs, prepared, config, inputs.venue_input)
    preferences = _checked("preference_input", prepare_lecturer_preferences, inputs.teaching_input, inputs.preference_input)
    calendar = inputs.calendar
    _checked("preference_config", checked_preference_inputs, prepared, config, preferences, calendar, inputs.preference_policy,
        inputs.preference_config, staff_input=staff, work_periods=periods)
    _checked("fairness_config", checked_fairness_inputs, prepared, config, inputs.fairness_policy, calendar, inputs.fairness_config,
        staff_input=staff, work_periods=periods)
    alternatives = delivery_alternatives(prepared, combinations)
    _checked("consecutive_config", checked_consecutive_inputs, prepared, config, inputs.consecutive_policy, calendar, inputs.consecutive_config,
        alternatives, staff_input=staff, work_periods=periods,
        preference_calendar=calendar, fairness_calendar=calendar)
    _checked("student_lunch_config", checked_student_lunch_inputs, prepared, config, inputs.student_lunch_policy, calendar, inputs.student_lunch_config,
        alternatives, staff_input=staff, work_periods=periods,
        preference_calendar=calendar, fairness_calendar=calendar, consecutive_calendar=calendar)
    contexts = dict(venue_input=venues, staff_input=staff, combination_input=combinations,
        combination_config=combination_config, work_periods=periods, work_period_config=period_config,
        preference_input=preferences, preference_calendar=calendar, preference_policy=inputs.preference_policy,
        preference_config=inputs.preference_config, fairness_policy=inputs.fairness_policy,
        fairness_calendar=calendar, fairness_config=inputs.fairness_config,
        consecutive_policy=inputs.consecutive_policy, consecutive_calendar=calendar,
        consecutive_config=inputs.consecutive_config, student_lunch_policy=inputs.student_lunch_policy,
        student_lunch_calendar=calendar, student_lunch_config=inputs.student_lunch_config)
    return PreparedScheduling(prepared, config, contexts)
