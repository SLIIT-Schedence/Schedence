"""Explicit calendar linkage and checked consumption of Step 9A preparation."""

import json
from typing import Annotated

from pydantic import Field, ValidationError

from schedence_teaching.preparation import PreparationError
from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.period_contracts import Weekday
from schedence_teaching.workload.period_preparation import PreparedWorkPeriods, prepare_work_periods

from .results import SolverInputError


Limit = Annotated[int, Field(strict=True, gt=0)]


class WorkPeriodSolverConfig(InputContract):
    """Calendar declarations, not campus policy or invented operating hours."""

    day_weekdays: dict[RequiredText, Weekday]
    day_streams: dict[RequiredText, set[Stream]]
    max_prepared_intervals: Limit = 10_000
    max_flexible_blocks: Limit = 10_000
    max_model_variables: Limit = 200_000
    max_model_constraints: Limit = 200_000


def checked_work_period_inputs(prepared, config, staff_input, periods, options):
    if staff_input is None:
        raise SolverInputError("Work-period mode requires original StaffSchedulingInput and its dated workload policy.")
    if not isinstance(periods, PreparedWorkPeriods) or not isinstance(options, WorkPeriodSolverConfig):
        raise SolverInputError("Work-period mode requires PreparedWorkPeriods and explicit WorkPeriodSolverConfig.")
    try:
        options = WorkPeriodSolverConfig.model_validate(options.model_dump(round_trip=True))
        canonical = prepare_work_periods(prepared.input_snapshot, periods.workload_snapshot,
                                         periods.input_snapshot, max_intervals=options.max_prepared_intervals)
    except (ValidationError, PreparationError) as error:
        if isinstance(error, ValidationError):
            message = "; ".join(row["msg"] for row in error.errors(include_input=False, include_context=False, include_url=False))
        else:
            message = str(error)
        raise SolverInputError(message) from None
    try:
        unchanged = json.dumps(periods.to_dict(), sort_keys=True) == json.dumps(canonical.to_dict(), sort_keys=True)
    except (AttributeError, TypeError, ValueError):
        unchanged = False
    if not unchanged:
        raise SolverInputError("Prepared work periods differ from independently regenerated source contracts.")
    if canonical.workload_snapshot != staff_input.workload_input:
        raise SolverInputError("Work periods must use the original staff scheduling workload snapshot and academic week.")
    days = {row.day: row for row in config.days}
    if set(options.day_weekdays) != days.keys() or set(options.day_streams) != days.keys():
        raise SolverInputError("Explicit weekday and stream declarations are required for every configured day.")
    source = canonical.input_snapshot
    named_weekdays = {row.value for row in Weekday}
    for identity, day in days.items():
        on_date = staff_input.day_dates[identity]
        weekday = list(Weekday)[on_date.weekday()]
        if not source.week_start <= on_date <= source.week_end:
            raise SolverInputError("Scheduling dates must lie in the work-period academic week.")
        if options.day_weekdays[identity] is not weekday or (identity in named_weekdays and identity != weekday.value):
            raise SolverInputError("Scheduling day, declared weekday and supplied calendar date disagree.")
        if options.day_streams[identity] != set(day.windows):
            raise SolverInputError("Declared day streams must match configured teaching stream windows exactly.")
    dated = {row.on_date for row in canonical.protected_intervals}
    dated.update(window.on_date for row in canonical.flexible_candidates for window in row.eligible_windows)
    if dated - set(staff_input.day_dates.values()):
        raise SolverInputError("Every protected or eligible flexible date needs an explicit configured day mapping (including non-teaching days).")
    for candidate in canonical.flexible_candidates:
        if type(candidate.reservation_required) is not bool:
            raise SolverInputError("Approved flexible policy must explicitly declare reservation_required; mandatory/optional is not inferred.")
    if len(canonical.flexible_candidates) > options.max_flexible_blocks:
        raise SolverInputError("Flexible request limit exceeded; no requests were dropped.")
    return canonical, options
