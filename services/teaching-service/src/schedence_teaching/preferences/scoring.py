"""Evaluate actual selected deliveries only, after existing independent hard validation."""

from collections import Counter, defaultdict

from schedence_teaching.solver import ScheduledDelivery
from schedence_teaching.solver.inputs import checked_inputs
from schedence_teaching.workload.period_contracts import Weekday

from .contracts import (
    PreferenceCalendar, PreferenceCategory, PreferenceConfirmation, PreferenceDirection,
    PreferenceScoringPolicy, PreferenceWeightMode,
)
from .preparation import (
    PreferenceDiagnostic, PreferenceInputError, PreparedLecturerPreferences,
    preference_units, prepare_lecturer_preferences, snapshot,
)
from .results import PreferenceEvaluation, PreferenceSatisfaction, PreferenceScoreReport, StaffPreferenceSummary


def _validate_result(prepared, config, result, *, venue_input, combination_input, combination_config,
                     staff_input, work_periods, work_period_config, preference_input=None,
                     preference_calendar=None, preference_policy=None, preference_config=None,
                     fairness_policy=None, fairness_calendar=None, fairness_config=None,
                     consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
                     student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    from schedence_teaching.solver.evaluation import _validate_evaluation_projection as validate_evaluation_result
    from schedence_teaching.solver.results import SolverInputError
    if (getattr(result, "fairness_optimization_enabled", False) or getattr(result, "consecutive_optimization_enabled", False) or getattr(result, "student_lunch_enabled", False)) and not result.preference_optimization_enabled:
        preference_input = preference_calendar = preference_policy = None
    try:
        return validate_evaluation_result(prepared, config, result, venue_input=venue_input,
            combination_input=combination_input, combination_config=combination_config,
            staff_input=staff_input, work_periods=work_periods, work_period_config=work_period_config,
            preference_input=preference_input, preference_calendar=preference_calendar,
            preference_policy=preference_policy, preference_config=preference_config,
            fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
    except SolverInputError as error:
        raise PreferenceInputError(str(error)) from None


def _calendar(config, calendar, staff_input, work_periods):
    calendar = snapshot(calendar, PreferenceCalendar)
    if set(calendar.day_dates) != {day.day for day in config.days}:
        raise PreferenceInputError("Every configured day needs its explicit scoring calendar date.")
    weekdays = {row.value for row in Weekday}
    for day, on_date in calendar.day_dates.items():
        if day in weekdays and day != list(Weekday)[on_date.weekday()].value:
            raise PreferenceInputError("Named scheduling day and its calendar weekday disagree.")
    if staff_input is not None and (calendar.day_dates != staff_input.day_dates
                                    or calendar.week_reference != staff_input.workload_input.week_reference):
        raise PreferenceInputError("Preference scoring and staff scheduling must use the same dates and academic week.")
    if work_periods is not None:
        source = work_periods.input_snapshot
        if (calendar.week_reference, calendar.week_start, calendar.week_end) != (source.week_reference, source.week_start, source.week_end):
            raise PreferenceInputError("Preference scoring and protected periods must use the same academic week bounds.")
    return calendar


def _scope_applies(scope, calendar, on_date):
    return ((scope.week_reference is None or scope.week_reference == calendar.week_reference)
            and (scope.effective_from is None or scope.effective_from <= on_date)
            and (scope.effective_until is None or on_date <= scope.effective_until))


def _evaluate(row, delivery, weekday, policy):
    if row.category is PreferenceCategory.WEEKEND_DAY and weekday not in {Weekday.SATURDAY, Weekday.SUNDAY}:
        return None
    if row.category in {PreferenceCategory.WORKING_DAY, PreferenceCategory.WEEKEND_DAY}:
        match = weekday in row.days
    else:
        if row.days and weekday not in row.days:
            return None  # Clock preferences with day selectors apply only on those days.
        window = row.time_window if row.category is PreferenceCategory.TEACHING_WINDOW else policy.time_of_day_windows.get(row.time_of_day)
        if window is None:
            return PreferenceSatisfaction.UNRESOLVED
        match = (window.start_minute <= delivery.start_minute and delivery.end_minute <= window.end_minute
                 if row.direction is PreferenceDirection.PREFER else
                 delivery.start_minute < window.end_minute and window.start_minute < delivery.end_minute)
    satisfied = match if row.direction is PreferenceDirection.PREFER else not match
    return PreferenceSatisfaction.SATISFIED if satisfied else PreferenceSatisfaction.VIOLATED


def _weight(row, policy):
    if policy.weight_mode is PreferenceWeightMode.UNWEIGHTED:
        return None, None
    if row.penalty_weight is not None and row.confirmation_status is PreferenceConfirmation.CONFIRMED:
        return row.penalty_weight, row.weight_reference
    if policy.weight_mode is PreferenceWeightMode.SYNTHETIC_DEFAULTS:
        return policy.synthetic_default_weight, policy.policy_reference
    return None, None


def _metrics(deliveries, calendar, assessment):
    from schedence_teaching.workload.metrics import teaching_metrics
    metrics = teaching_metrics(deliveries, calendar.day_dates)
    target = assessment.policy.required_teaching_minutes if assessment else None
    return (metrics.assigned_delivery_count, metrics.scheduled_teaching_minutes,
            metrics.teaching_days, metrics.teaching_day_count,
            tuple((day, minutes) for day, minutes in metrics.daily_teaching_minutes if minutes),
            metrics.longest_consecutive_teaching_minutes, target,
            metrics.scheduled_teaching_minutes / target if target is not None and target > 0 else None)


def score_lecturer_preferences(prepared, config, result, preferences: PreparedLecturerPreferences,
                               calendar: PreferenceCalendar, policy: PreferenceScoringPolicy, *,
                               venue_input=None, combination_input=None, combination_config=None,
                               staff_input=None, work_periods=None, work_period_config=None, preference_config=None,
                               fairness_policy=None, fairness_calendar=None, fairness_config=None,
                     consecutive_policy=None, consecutive_calendar=None, consecutive_config=None,
                     student_lunch_policy=None, student_lunch_calendar=None, student_lunch_config=None):
    """Descriptive post-solve evaluation only. No solver mutation or preference hard blocks."""
    prepared, config = checked_inputs(prepared, config)
    _validate_result(prepared, config, result, venue_input=venue_input, combination_input=combination_input,
        combination_config=combination_config, staff_input=staff_input, work_periods=work_periods,
        work_period_config=work_period_config, preference_input=preferences, preference_calendar=calendar,
        preference_policy=policy, preference_config=preference_config,
        fairness_policy=fairness_policy, fairness_calendar=fairness_calendar, fairness_config=fairness_config,
            consecutive_policy=consecutive_policy, consecutive_calendar=consecutive_calendar, consecutive_config=consecutive_config,
            student_lunch_policy=student_lunch_policy, student_lunch_calendar=student_lunch_calendar, student_lunch_config=student_lunch_config)
    return _score_validated_timetable(prepared, config, result, preferences, calendar, policy,
                                      staff_input=staff_input, work_periods=work_periods)


def _score_validated_timetable(prepared, config, result, preferences, calendar, policy, *,
                                staff_input=None, work_periods=None):
    """Shared post-validation calculation; never trusts modeled preference penalties."""
    calendar = _calendar(config, calendar, staff_input, work_periods)
    policy = snapshot(policy, PreferenceScoringPolicy)
    from schedence_teaching.policy.resolution import verify_component
    verify_component(config, 'preference_policy', policy)
    if policy.weight_mode is PreferenceWeightMode.SYNTHETIC_DEFAULTS and not calendar.synthetic_data:
        raise PreferenceInputError("Synthetic default preference weights may only be used with explicitly synthetic data.")
    if not isinstance(preferences, PreparedLecturerPreferences):
        raise PreferenceInputError("Expected prepared lecturer preferences.")
    canonical = prepare_lecturer_preferences(prepared.input_snapshot, preferences.input_snapshot)
    if preferences != canonical:
        raise PreferenceInputError("Prepared preference diagnostics differ from regenerated source contracts.")
    records = canonical.input_snapshot.preferences
    diagnostics = set(canonical.diagnostics)
    by_staff = defaultdict(list)
    units = defaultdict(list)
    for row in records:
        by_staff[row.staff_id].append(row)
    for unit in preference_units(canonical.input_snapshot):
        units[unit[0].staff_id].append(unit)
    outcomes, inactive = [], set()
    for row in records:
        if row.confirmation_status is PreferenceConfirmation.REJECTED or not any(
            _scope_applies(row.scope, calendar, day) for day in calendar.day_dates.values()
        ):
            inactive.add(row.preference_id)
    for delivery in result.schedule:
        identity = delivery.delivery_id if isinstance(delivery, ScheduledDelivery) else delivery.session.occurrence_id
        stream = delivery.covered_sessions[0].stream if isinstance(delivery, ScheduledDelivery) else delivery.session.stream
        on_date = calendar.day_dates[delivery.day]
        weekday = list(Weekday)[on_date.weekday()]
        for alternatives in units[delivery.assigned_staff_id]:
            applicable = [row for row in alternatives if row.preference_id not in inactive
                and _scope_applies(row.scope, calendar, on_date) and (row.stream is None or row.stream == stream)]
            evaluated = [(row, _evaluate(row, delivery, weekday, policy)) for row in applicable]
            evaluated = [(row, value) for row, value in evaluated if value is not None]
            if not evaluated:
                continue
            row = evaluated[0][0]
            values = {value for _, value in evaluated}
            satisfaction = (PreferenceSatisfaction.SATISFIED if PreferenceSatisfaction.SATISFIED in values else
                            PreferenceSatisfaction.UNRESOLVED if PreferenceSatisfaction.UNRESOLVED in values else
                            PreferenceSatisfaction.VIOLATED)
            preference_ids = tuple(item.preference_id for item, _ in evaluated)
            preference_id = f"preferred-alternatives:{row.alternative_group_id}" if row.alternative_group_id is not None else row.preference_id
            weight, reference = _weight(row, policy)
            penalty = (weight * int(satisfaction is PreferenceSatisfaction.VIOLATED)
                       if weight is not None and satisfaction is not PreferenceSatisfaction.UNRESOLVED else None)
            outcomes.append(PreferenceEvaluation(identity, row.staff_id, preference_id,
                row.category.value, row.direction.value, satisfaction, weight, reference, penalty, preference_ids))
            if satisfaction is PreferenceSatisfaction.UNRESOLVED:
                diagnostics.add(PreferenceDiagnostic("missing_time_of_day_boundaries", preference_ids,
                    "Time-of-day preference cannot be evaluated without an explicitly referenced boundary policy."))
            if policy.weight_mode is PreferenceWeightMode.CONFIRMED_WEIGHTS and weight is None:
                diagnostics.add(PreferenceDiagnostic("missing_confirmed_weight", preference_ids,
                    "No confirmed penalty weight is available; the weighted total remains incomplete."))
    weighted = policy.weight_mode is not PreferenceWeightMode.UNWEIGHTED

    def totals(rows):
        counts = Counter(row.satisfaction for row in rows)
        known = sum(row.penalty for row in rows if row.penalty is not None) if weighted else None
        complete = all(row.penalty is not None for row in rows) if weighted else None
        return (counts[PreferenceSatisfaction.SATISFIED], counts[PreferenceSatisfaction.VIOLATED],
                counts[PreferenceSatisfaction.UNRESOLVED], known, known if complete else None, complete)

    assessments = {row.policy.staff_id: row for row in result.workload_report.staff} if result.workload_report else {}
    staff = []
    for person in sorted(prepared.input_snapshot.staff_members, key=lambda row: row.staff_id):
        rows = [row for row in outcomes if row.staff_id == person.staff_id]
        selected = [row for row in result.schedule if row.assigned_staff_id == person.staff_id]
        satisfied, violated, unresolved, known, total, _ = totals(rows)
        staff.append(StaffPreferenceSummary(person.staff_id, len(by_staff[person.staff_id]), len(rows),
            satisfied, violated, unresolved, known, total, *_metrics(selected, calendar, assessments.get(person.staff_id))))
    satisfied, violated, unresolved, known, total, complete = totals(outcomes)
    categories = defaultdict(Counter)
    for row in outcomes:
        categories[row.category][row.satisfaction.value] += 1
    return PreferenceScoreReport(calendar.week_reference, policy.policy_reference, policy.weight_mode.value,
        calendar.synthetic_data, len(result.schedule), len(outcomes), satisfied, violated,
        len({row.delivery_id for row in outcomes if row.satisfaction is PreferenceSatisfaction.VIOLATED}),
        unresolved, known, total, complete,
        sum(row.confirmation_status is not PreferenceConfirmation.CONFIRMED for row in records), len(inactive),
        tuple((category, tuple(sorted(counts.items()))) for category, counts in sorted(categories.items())),
        tuple(staff), tuple(outcomes), tuple(sorted(diagnostics, key=lambda row: (row.code, row.preference_ids))),
        result.preference_optimization_enabled)
