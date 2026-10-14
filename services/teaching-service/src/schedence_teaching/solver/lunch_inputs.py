"""Explicit lunch enablement/confirmation; all lunch rules remain Step 11D.1 contracts."""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from schedence_teaching.schemas.models import InputContract, RequiredText
from .results import SolverInputError


Limit = Annotated[int, Field(strict=True, gt=0, le=2**50)]


class StudentLunchEnforcementConfig(InputContract):
    applicability_confirmation: Literal["CONFIRMED"]
    applicability_confirmation_reference: RequiredText
    max_rule_windows: Limit = 20_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000


@dataclass(frozen=True)
class CheckedStudentLunch:
    policy: object
    calendar: object
    config: StudentLunchEnforcementConfig
    # Unique (day index, start minute, end minute) windows for each possible delivery.
    delivery_rules: tuple[tuple[str, tuple[tuple[int, int, int], ...]], ...]


def checked_student_lunch_inputs(prepared, config, policy, calendar, options, alternatives, *,
        staff_input=None, work_periods=None, preference_calendar=None, fairness_calendar=None, consecutive_calendar=None):
    from schedence_teaching.lunch import StudentLunchPolicy, confirmed_student_lunch_policy
    from schedence_teaching.lunch.assessment import _snapshot
    from schedence_teaching.lunch.applicability import attending_group_ids, lunch_applicability
    from schedence_teaching.preferences.scoring import _calendar
    try:
        policy = _snapshot(policy, StudentLunchPolicy)
        options = _snapshot(options, StudentLunchEnforcementConfig)
        calendar = _calendar(config, calendar, staff_input, work_periods)
        if policy.week_reference != calendar.week_reference:
            raise ValueError("Student lunch policy/calendar academic weeks differ.")
        if any(other is not None and calendar != other for other in (preference_calendar, fairness_calendar, consecutive_calendar)):
            raise ValueError("Student lunch and original scheduling calendars must agree.")
        if policy.applicable_dates is None or policy.applicable_streams is None or policy.applicability_reference is None:
            raise ValueError("Lunch enforcement requires explicitly confirmed date and weekday/weekend stream applicability.")
        if any(not calendar.week_start <= day <= calendar.week_end for day in policy.applicable_dates):
            raise ValueError("Lunch applicability dates must lie in the configured academic week.")
        from schedence_teaching.policy.resolution import verify_component
        if config.resolved_policy is not None:
            verify_component(config, 'student_lunch_policy', policy)
            verify_component(config, 'student_lunch_config', options)
            verify_component(config, 'calendar', calendar)
        else:
            if policy.configured_academic_years is not None:
                raise ValueError('Institution lunch requires a resolved institution policy.')
            expected = confirmed_student_lunch_policy(policy_reference=policy.policy_reference,
                week_reference=policy.week_reference, provenance=policy.provenance)
            actual_windows = sorted((row.academic_year, row.window.start_minute, row.window.end_minute) for row in policy.year_periods)
            if actual_windows != [(row.academic_year, row.window.start_minute, row.window.end_minute) for row in expected.year_periods]:
                raise ValueError("Lunch enforcement requires the confirmed fixed year-specific periods; periods cannot be moved or optimized.")
        groups = {row.group_id: row for row in prepared.input_snapshot.student_groups}
        periods = {row.academic_year: row.window for row in policy.year_periods}
        if any(group.academic_year not in periods for group in groups.values()):
            raise ValueError('Resolved lunch policy has no period for an attending study year.')
        rules, total = [], 0
        for delivery in alternatives:
            windows = set()
            for identity in attending_group_ids(prepared, delivery.group_ids):
                group = groups[identity]
                for index, day in enumerate(config.days):
                    applies, _ = lunch_applicability(policy, calendar.day_dates[day.day], group.stream, day.windows)
                    if applies is None:
                        raise ValueError("Student lunch applicability is unresolved for an attending group/date.")
                    if applies:
                        lunch = periods[group.academic_year]
                        windows.add((index, lunch.start_minute, lunch.end_minute))
            total += len(windows)
            if total > options.max_rule_windows:
                raise ValueError("Student lunch rule-window limit exceeded; no groups or occurrences were dropped.")
            rules.append((delivery.delivery_id, tuple(sorted(windows))))
        return CheckedStudentLunch(policy, calendar, options, tuple(rules))
    except ValueError as error:
        raise SolverInputError(str(error)) from None
