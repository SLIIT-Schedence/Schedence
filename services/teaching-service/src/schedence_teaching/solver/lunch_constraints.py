"""Exclude positive lunch overlaps using existing start variables; no lunch bookings."""


def add_student_lunch_constraint(model, start, domains, duration_minutes, presence, rules, config):
    if not rules:
        return
    from ortools.sat.python import cp_model
    granularity = config.granularity_minutes
    stride = (1440 + granularity - 1) // granularity
    forbidden = []
    for day_index, lunch_start, lunch_end in rules:
        # Strict positive overlap: start > lunch_start-duration AND start < lunch_end.
        # These integer bounds do not round or move the actual lunch period.
        first = max(0, (lunch_start-duration_minutes)//granularity+1)
        last = min(stride-1, (lunch_end-1)//granularity)
        if first <= last:
            forbidden.append((day_index*stride+first, day_index*stride+last))
    allowed = cp_model.Domain.from_intervals(domains).intersection_with(cp_model.Domain.from_intervals(forbidden).complement())
    constraint = model.add_linear_expression_in_domain(start, allowed)
    if presence is not None:
        constraint.only_enforce_if(presence)
