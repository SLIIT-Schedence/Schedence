"""CP-SAT staff domains and explicit teaching caps across all delivery alternatives."""

from .config import MINUTES_PER_DAY


def staff_start_domains(session, calendar, config, day_dates, *, allowed_days=None):
    granularity = config.granularity_minutes
    stride = (MINUTES_PER_DAY + granularity - 1) // granularity
    domains = []
    for index, day in enumerate(config.days):
        if allowed_days is not None and day.day not in allowed_days:
            continue
        if (day.day not in calendar.allowed_teaching_days
                or not calendar.effective_from <= day_dates[day.day] <= calendar.effective_until):
            continue
        windows = [(max(first.start_minute, second.start_minute), min(first.end_minute, second.end_minute))
                   for first in day.windows.get(session.stream, [])
                   for second in calendar.available_windows[day.day]
                   if max(first.start_minute, second.start_minute) < min(first.end_minute, second.end_minute)]
        for block in calendar.blocked_periods:
            if block.day != day.day:
                continue
            remaining = []
            for start, end in windows:
                if block.end_minute <= start or end <= block.start_minute:
                    remaining.append((start, end))
                else:
                    if start < block.start_minute:
                        remaining.append((start, block.start_minute))
                    if block.end_minute < end:
                        remaining.append((block.end_minute, end))
            windows = remaining
        # Evidence boundaries may be unaligned; round valid starts inward.
        for start, end in windows:
            first = (start + granularity - 1) // granularity
            last = (end - session.duration_minutes) // granularity
            if first <= last:
                domains.append([index * stride + first, index * stride + last])
    return domains


def add_teaching_caps(model, config, inputs, assignments, guard_model):
    from .teaching_policy import teaching_day_limit
    on_day = {}
    for calendar in inputs.availability:
        rows = assignments.get(calendar.staff_id, [])
        if calendar.weekly_teaching_max_minutes is not None:
            model.add(sum(minutes * chosen for _, chosen, _, minutes in rows) <= calendar.weekly_teaching_max_minutes)
        day_limit=teaching_day_limit(inputs,calendar.staff_id)
        used_days=[]
        if calendar.daily_teaching_max_minutes is not None or day_limit is not None:
            for index, day in enumerate(config.days):
                terms = []; chosen_on_day=[]
                for identity, chosen, day_var, minutes in rows:
                    key = (identity, index)
                    if key not in on_day:
                        flag = model.new_bool_var(f"delivery_day:{identity}:{day.day}")
                        model.add(day_var == index).only_enforce_if(flag)
                        model.add(day_var != index).only_enforce_if(flag.Not())
                        on_day[key] = flag
                    teaching = model.new_bool_var(f"staff_day:{identity}:{calendar.staff_id}:{day.day}")
                    model.add(teaching <= chosen)
                    model.add(teaching <= on_day[key])
                    model.add(teaching >= chosen + on_day[key] - 1)
                    terms.append(minutes * teaching)
                    chosen_on_day.append(teaching)
                if calendar.daily_teaching_max_minutes is not None:
                    model.add(sum(terms) <= calendar.daily_teaching_max_minutes)
                if day_limit is not None:
                    used=model.new_bool_var(f'staff_teaches_date:{calendar.staff_id}:{day.day}')
                    if chosen_on_day:model.add_max_equality(used,chosen_on_day)
                    else:model.add(used==0)
                    used_days.append(used)
                guard_model()
        if day_limit is not None:model.add(sum(used_days)<=day_limit)
        guard_model()
