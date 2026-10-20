"""Exact reified preference costs; none of these expressions remove feasible starts."""

from dataclasses import dataclass

from ortools.sat.python import cp_model

from .results import SolverInputError


@dataclass(frozen=True)
class ModeledPreferencePenalty:
    delivery_id: str
    staff_id: str
    preference_ids: tuple[str, ...]
    violated: bool
    penalty: int


def _domains(session, unit, context, config):
    from schedence_teaching.preferences.contracts import PreferenceCategory, PreferenceDirection
    from schedence_teaching.preferences.scoring import _scope_applies
    from schedence_teaching.workload.period_contracts import Weekday

    row = unit[0]
    if row.stream is not None and row.stream != session.stream:
        return [], []
    g = config.granularity_minutes
    stride = (1440 + g - 1) // g
    last = (1440 - session.duration_minutes) // g
    applicable, satisfied = [], []
    for index, day in enumerate(config.days):
        on_date = context.calendar.day_dates[day.day]
        weekday = list(Weekday)[on_date.weekday()]
        if not _scope_applies(row.scope, context.calendar, on_date) or session.stream not in day.windows:
            continue
        if row.category is PreferenceCategory.WEEKEND_DAY and on_date.weekday() < 5:
            continue
        clock = row.category in {PreferenceCategory.TIME_OF_DAY, PreferenceCategory.TEACHING_WINDOW}
        if clock and row.days and weekday not in row.days:
            continue
        base = index * stride
        applicable.append([base, base + last])
        for member in unit:
            if not clock:
                match = weekday in member.days
                if match == (member.direction is PreferenceDirection.PREFER):
                    satisfied.append([base, base + last])
                continue
            window = (member.time_window if member.category is PreferenceCategory.TEACHING_WINDOW
                      else context.policy.time_of_day_windows[member.time_of_day])
            if member.direction is PreferenceDirection.PREFER:
                ranges = [((window.start_minute + g - 1) // g,
                           (window.end_minute - session.duration_minutes) // g)]
            else:
                ranges = [(0, (window.start_minute - session.duration_minutes) // g),
                          ((window.end_minute + g - 1) // g, last)]
            for left, right in ranges:
                left, right = max(0, left), min(last, right)
                if left <= right:
                    satisfied.append([base + left, base + right])
    return applicable, satisfied


def _membership(model, start, intervals, name):
    selected = model.new_bool_var(name)
    domain = cp_model.Domain.from_intervals(intervals)
    model.add_linear_expression_in_domain(start, domain).only_enforce_if(selected)
    model.add_linear_expression_in_domain(start, domain.complement()).only_enforce_if(selected.negated())
    return selected


def add_preference_penalties(model, alternative, start, choices, context, config, state, guard):
    from schedence_teaching.preferences.scoring import _weight

    for index, unit in enumerate(context.units):
        row = unit[0]
        if row.staff_id not in choices:
            continue
        applicable, good = _domains(alternative.covered_sessions[0], unit, context, config)
        if not applicable:
            continue
        if len(state) >= context.config.max_preference_terms:
            raise SolverInputError("Preference term limit exceeded; no criteria or delivery alternatives were dropped.")
        prefix = f"preference:{alternative.delivery_id}:{row.staff_id}:{index}"
        active = _membership(model, start, applicable, prefix + ":applicable")
        satisfied = _membership(model, start, good, prefix + ":satisfied")
        selected = choices[row.staff_id]
        violated = model.new_bool_var(prefix + ":violated")
        model.add_bool_and([selected, active, satisfied.negated()]).only_enforce_if(violated)
        model.add_bool_or([selected.negated(), active.negated(), satisfied, violated])
        weight = _weight(row, context.policy)[0]
        penalty = model.new_int_var(0, weight, prefix + ":penalty")
        model.add(penalty == weight * violated)
        state.append((alternative.delivery_id, row.staff_id, tuple(member.preference_id for member in unit),
                      selected, active, violated, penalty))
        guard()


def decode_preference_penalties(solver, state):
    return tuple(ModeledPreferencePenalty(identity, staff, ids, bool(solver.value(violated)), solver.value(penalty))
                 for identity, staff, ids, selected, active, violated, penalty in state
                 if solver.value(selected) and solver.value(active))
