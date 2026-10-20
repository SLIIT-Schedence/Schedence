"""Add fixed and optional administrative intervals to the existing staff resource."""

from dataclasses import replace

from ortools.sat.python import cp_model

from .results import SolverInputError
from .work_period_results import ScheduledAdministrativePeriod


def add_work_period_intervals(model, config, staff_input, periods, options, staff_intervals, guard):
    granularity = config.granularity_minutes
    stride = (1440 + granularity - 1) // granularity
    day_indices = {staff_input.day_dates[day.day]: index for index, day in enumerate(config.days)}
    for row in periods.protected_intervals:
        base = day_indices[row.on_date] * stride
        # Outward rounding blocks exactly all grid-aligned teaching intersections.
        start = base + row.start_minute // granularity
        end = base + (row.end_minute + granularity - 1) // granularity
        staff_intervals[row.staff_id].append(model.new_fixed_size_interval_var(start, end - start, row.interval_id))
        guard()
    blocks, count = [], 0
    for row in periods.flexible_candidates:
        target = row.planned_duration_minutes // granularity
        minimum = ((row.minimum_period_minutes + granularity - 1) // granularity if row.allow_split else target)
        presence = model.new_bool_var(f"admin-request:{row.request_id}")
        if row.reservation_required:
            model.add(presence == 1)
        else:
            model.add_hint(presence, 0)  # No extra objective; optional reservations may be omitted.
        domains = []
        for window in row.eligible_windows:
            base = day_indices[window.on_date] * stride
            start = base + (window.start_minute + granularity - 1) // granularity
            end = base + window.end_minute // granularity
            if end - start >= max(minimum, 1):
                domains.append((start, end))
        if row.planned_duration_minutes % granularity or target == 0 or not domains or minimum > target:
            model.add(presence == 0)
            guard()
            continue
        maximum_blocks = target // minimum if row.allow_split else 1
        count += maximum_blocks
        if count > options.max_flexible_blocks:
            raise SolverInputError("Flexible block limit exceeded; no mandatory time or alternatives were dropped.")
        durations, previous = [], None
        for index in range(maximum_blocks):
            identity = f"administrative:{row.request_id}:{index + 1}"
            selected = model.new_bool_var(identity + ":selected")
            model.add(selected <= presence)
            size = model.new_int_var(0, target, identity + ":duration")
            model.add(size == 0).only_enforce_if(selected.negated())
            model.add(size >= minimum).only_enforce_if(selected)
            start = model.new_int_var_from_domain(cp_model.Domain.from_intervals(
                [[left, right - minimum] for left, right in domains]), identity + ":start")
            end = model.new_int_var(min(left for left, _ in domains), max(right for _, right in domains), identity + ":end")
            interval = model.new_optional_interval_var(start, size, end, selected, identity)
            windows = []
            for window_index, (left, right) in enumerate(domains):
                chosen = model.new_bool_var(f"{identity}:window:{window_index}")
                model.add(start >= left).only_enforce_if(chosen)
                model.add(end <= right).only_enforce_if(chosen)
                windows.append(chosen)
                guard()
            model.add(sum(windows) == selected)
            if previous is not None:
                prior_presence, prior_end = previous
                model.add(selected <= prior_presence)
                model.add(start >= prior_end).only_enforce_if(selected)
            previous = selected, end
            durations.append(size)
            staff_intervals[row.staff_id].append(interval)
            blocks.append((row, index + 1, selected, start, end))
            guard()
        model.add(sum(durations) == target * presence)
        guard()
    return blocks


def decode_administrative_periods(solver, blocks, config, staff_input):
    granularity = config.granularity_minutes
    stride = (1440 + granularity - 1) // granularity
    output = []
    for row, ordinal, selected, start, end in blocks:
        if not solver.value(selected):
            continue
        day_index, local_start = divmod(solver.value(start), stride)
        day = config.days[day_index].day
        duration = (solver.value(end) - solver.value(start)) * granularity
        output.append(ScheduledAdministrativePeriod(
            f"administrative:{row.request_id}:{ordinal}", row.request_id, row.staff_id, day,
            staff_input.day_dates[day], local_start * granularity, local_start * granularity + duration,
            row.institution_id, row.policy_reference, row.appointment_id, row.protection,
            row.approval_reference, row.provenance))
    ordered = sorted(output, key=lambda row: (row.request_id, row.on_date, row.start_minute, row.end_minute))
    ordinals = {}
    normalized = []
    for row in ordered:
        ordinals[row.request_id] = ordinals.get(row.request_id, 0) + 1
        normalized.append(replace(row, block_id=f"administrative:{row.request_id}:{ordinals[row.request_id]}"))
    return tuple(normalized)
