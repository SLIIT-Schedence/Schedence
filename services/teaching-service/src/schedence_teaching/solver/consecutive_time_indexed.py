"""Exact time-indexed consecutive scoring (P2.3), linear in candidate start times.

Equivalent to the successor circuit and to Step 11A sequence metrics: per
date, an attendee's selected deliveries are ordered chronologically across all
streams; only consecutive same-stream deliveries form a gap. Every start and
duration lies on the granularity grid, so on each configured date:

- back-to-back pair: an s-delivery ends at boundary b and another s-delivery starts at b;
- idle slot: free, the last occupied slot before it is s and the next one after it is s;
- short break of m free slots: max(P - G*m, 0) = sum_k w_k [m <= k];
- run: a maximal contiguous same-stream occupancy; excess is charged at its last slot.

No candidate, participant, date or criterion is dropped; no pairwise successor arcs exist.
"""

from dataclasses import dataclass

from ortools.sat.python import cp_model


@dataclass(frozen=True)
class TimeIndexedState:
    kind: str
    entity_id: str
    nodes: dict  # delivery -> (selected, day variable, absolute start minutes, applicable)
    maximum: object
    components: dict
    raw: object
    weighted: object
    source: dict
    config: object
    context: object


def _and(model, literals, name):
    result = model.new_bool_var(name)
    model.add_bool_and(literals).only_enforce_if(result)
    model.add_bool_or([literal.Not() for literal in literals] + [result])
    return result


def _bool(model, expression, name):
    result = model.new_bool_var(name)
    model.add(result == expression)
    return result


def _constant_one(model, variable):
    domain = model.proto.variables[variable.index].domain
    return list(domain) == [1, 1]


def start_literals(model, identity, start, ticks_per_day, cache):
    """Shared per-delivery "starts at global tick v" literals; exactly one holds."""
    if identity not in cache:
        values = cp_model.Domain.from_flat_intervals(list(model.proto.variables[start.index].domain)).flattened_intervals()
        literals = {}
        for low, high in zip(values[::2], values[1::2]):
            for tick in range(low, high + 1):
                literal = model.new_bool_var(f"consecutive_start_at:{identity}:{tick}")
                model.add(start == tick).only_enforce_if(literal)
                model.add(start != tick).only_enforce_if(literal.Not())
                literals[tick] = literal
        model.add_exactly_one(list(literals.values()))  # Redundant; the start domain is exactly these ticks.
        cache[identity] = {(tick // ticks_per_day, tick % ticks_per_day): literal for tick, literal in literals.items()}
    return cache[identity]


def time_indexed_entity(model, config, context, kind, entity_id, nodes, source, starts, applicability, bound, guard_model,
                        *, allowed_starts=None):
    """nodes: delivery -> (selected, day, absolute start, applicable); starts: delivery -> {(day, tick): literal}.

    allowed_starts: optional delivery -> global start-tick intervals enforced whenever this entity is selected.
    """
    granularity = config.granularity_minutes
    ticks_per_day = (1440 + granularity - 1) // granularity
    policy = context.policy
    scoring = policy.score_lecturers if kind == "LECTURER" else policy.score_student_groups
    hard = policy.confirmed_hard_minimum_break
    hard_applies = context.config.enforce_confirmed_hard_break and kind in hard.applies_to
    tag = f"consecutive_grid:{kind}:{entity_id}"
    zero = model.new_constant(0)
    terms = {}  # (day, stream) -> {"occ": {t: [..]}, "st": {...}, "en": {...}}
    excess_floor = []  # Valid bound: a run's excess is at least the sum of its own deliveries' excesses.
    floor_applies = scoring and any(row.value == "EXCESS_CONTINUOUS_MINUTES" for row in policy.soft_criteria)
    for identity, (selected, _, _, _) in nodes.items():
        row = source[identity]
        if row.duration_minutes % granularity:
            from .results import SolverInputError
            raise SolverInputError("Time-indexed consecutive scoring requires durations aligned to the scheduling granularity.")
        length, stream = row.duration_minutes // granularity, row.covered_sessions[0].stream
        always = _constant_one(model, selected)
        long = floor_applies and row.duration_minutes > policy.maximum_preferred_continuous_minutes
        if long and all(applicability):  # Linear in the existing assignment literal; LP-visible.
            excess_floor.append((row.duration_minutes - policy.maximum_preferred_continuous_minutes) * selected)
        permitted = None if allowed_starts is None else allowed_starts[identity]
        for (day, tick), literal in starts[identity].items():
            if permitted is not None and not any(low <= day * ticks_per_day + tick <= high for low, high in permitted):
                continue  # selected => start in permitted, so this (selected, start) conjunction is always false.
            chosen = literal if always else _and(model, [selected, literal], f"{tag}:start:{identity}:{day}:{tick}")
            if long and not all(applicability) and applicability[day]:
                excess_floor.append((row.duration_minutes - policy.maximum_preferred_continuous_minutes) * chosen)
            slots = terms.setdefault(day, {}).setdefault(stream, {"occ": {}, "st": {}, "en": {}, "ids": set()})
            slots["ids"].add(identity)
            slots["st"].setdefault(tick, []).append(chosen)
            slots["en"].setdefault(tick + length, []).append(chosen)
            for slot in range(tick, tick + length):
                slots["occ"].setdefault(slot, []).append(chosen)
        guard_model()
    raw_terms = {}
    if scoring:
        raw_terms = {criterion.value: [] for criterion in policy.soft_criteria}
        if policy.idle_gap_minute_penalty is not None:
            raw_terms["IDLE_GAP_MINUTES"] = []
    preferred = policy.preferred_minimum_break_minutes if "SHORT_BREAK" in raw_terms else None
    short_slots = -(-preferred // granularity) if preferred else 0
    hard_slots = -(-hard.minutes // granularity) if hard_applies else 0
    window = max(short_slots, hard_slots)
    # Definitional structures are built only for the criteria that read them. Each is a pure definition
    # referenced nowhere else, so omitting an unread one leaves the feasible schedules and every score unchanged.
    need_touch = "BACK_TO_BACK" in raw_terms
    need_gap = bool(short_slots or hard_slots)
    need_idle = "IDLE_GAP_MINUTES" in raw_terms
    need_neighbours = need_gap or need_idle
    runs = []
    for day, streams in sorted(terms.items()):
        low = min(tick for slots in streams.values() for tick in slots["st"])
        high = max(tick for slots in streams.values() for tick in slots["en"])
        span = range(low, high)
        applies = bool(applicability[day])
        occ = {s: {t: _bool(model, sum(slots["occ"].get(t, [])), f"{tag}:occ:{day}:{s}:{t}") for t in span}
               for s, slots in streams.items()}
        start = {s: {t: _bool(model, sum(slots["st"].get(t, [])), f"{tag}:st:{day}:{s}:{t}") for t in span}
                 for s, slots in streams.items()} if need_touch else {}
        end = {s: {b: _bool(model, sum(slots["en"].get(b, [])), f"{tag}:en:{day}:{s}:{b}") for b in range(low + 1, high + 1)}
               for s, slots in streams.items()} if need_touch or need_gap else {}
        busy = ({t: _bool(model, sum(occ[s][t] for s in streams), f"{tag}:busy:{day}:{t}") for t in span}
                if need_neighbours else {})
        last, following = {}, {}
        for s in streams if need_neighbours else ():
            last[s, low] = zero
            for t in range(low + 1, high):
                carried = _and(model, [busy[t - 1].Not(), last[s, t - 1]], f"{tag}:carry_last:{day}:{s}:{t}")
                last[s, t] = _bool(model, occ[s][t - 1] + carried, f"{tag}:last:{day}:{s}:{t}")
            following[s, high - 1] = zero
            for t in range(high - 2, low - 1, -1):
                carried = _and(model, [busy[t + 1].Not(), following[s, t + 1]], f"{tag}:carry_next:{day}:{s}:{t}")
                following[s, t] = _bool(model, occ[s][t + 1] + carried, f"{tag}:next:{day}:{s}:{t}")
        within = {}  # (b, k): some slot in [b, b + k] is busy
        for b in range(low + 1, high):
            for k in range(window):
                if b + k >= high:
                    within[b, k] = within.get((b, k - 1), zero) if k else zero
                elif k == 0:
                    within[b, k] = busy[b]
                else:
                    within[b, k] = model.new_bool_var(f"{tag}:within:{day}:{b}:{k}")
                    model.add_max_equality(within[b, k], [within[b, k - 1], busy[b + k]])
        for s in streams:
            # Exact: a same-stream run is at most the stream's candidate minutes that day and at most the
            # elapsed day span, so excess is provably zero wherever either bound is within the preferred maximum.
            possible = sum(source[identity].duration_minutes for identity in streams[s]["ids"])
            previous = None
            for t in span:
                run = model.new_int_var(0, len(span) * granularity, f"{tag}:run:{day}:{s}:{t}")
                model.add(run == (previous if previous is not None else 0) + granularity).only_enforce_if(occ[s][t])
                model.add(run == 0).only_enforce_if(occ[s][t].Not())
                runs.append(run)
                if ("EXCESS_CONTINUOUS_MINUTES" in raw_terms and applies
                        and min(possible, (t - low + 1) * granularity) > policy.maximum_preferred_continuous_minutes):
                    after = occ[s][t + 1] if t + 1 < high else zero
                    terminal = _and(model, [occ[s][t], after.Not()], f"{tag}:terminal:{day}:{s}:{t}")
                    over = model.new_int_var(0, len(span) * granularity, f"{tag}:over:{day}:{s}:{t}")
                    model.add_max_equality(over, [run - policy.maximum_preferred_continuous_minutes, 0])
                    scored = model.new_int_var(0, len(span) * granularity, f"{tag}:excess:{day}:{s}:{t}")
                    model.add(scored == over).only_enforce_if(terminal)
                    model.add(scored == 0).only_enforce_if(terminal.Not())
                    raw_terms["EXCESS_CONTINUOUS_MINUTES"].append(scored * policy.excess_continuous_minute_penalty)
                previous = run
            for b in range(low + 1, high) if need_touch or need_gap else ():
                gap_follows = (_and(model, [end[s][b], following[s, b - 1]], f"{tag}:gap_from:{day}:{s}:{b}")
                               if need_gap else None)
                if hard_slots and applies:
                    model.add_bool_or([gap_follows.Not(), within[b, hard_slots - 1].Not()])
                if not applies:
                    continue
                if "BACK_TO_BACK" in raw_terms:
                    touch = _and(model, [end[s][b], start[s][b]], f"{tag}:touch:{day}:{s}:{b}")
                    raw_terms["BACK_TO_BACK"].append(touch * policy.back_to_back_penalty)
                for k in range(short_slots):
                    weight = granularity if k < short_slots - 1 else preferred - granularity * (short_slots - 1)
                    short = _and(model, [gap_follows, within[b, k]], f"{tag}:short:{day}:{s}:{b}:{k}")
                    raw_terms["SHORT_BREAK"].append(short * (weight * policy.short_break_minute_penalty))
            if "IDLE_GAP_MINUTES" in raw_terms and applies:
                for t in span:
                    idle = _and(model, [busy[t].Not(), last[s, t], following[s, t]], f"{tag}:idle:{day}:{s}:{t}")
                    raw_terms["IDLE_GAP_MINUTES"].append(idle * (granularity * policy.idle_gap_minute_penalty))
        guard_model()
    maximum = model.new_int_var(0, 1440, f"{tag}:maximum_continuous")
    model.add_max_equality(maximum, runs or [zero])
    components = {}
    for criterion, values in sorted(raw_terms.items()):
        value = model.new_int_var(0, bound, f"{tag}:penalty:{criterion}")
        model.add(value == sum(values))
        components[criterion] = value
    if excess_floor:
        # Redundant and valid: max(sum(d) - M, 0) >= sum(max(d_i - M, 0)); it only strengthens the proof bound.
        model.add(components["EXCESS_CONTINUOUS_MINUTES"] >= policy.excess_continuous_minute_penalty * sum(excess_floor))
    raw = model.new_int_var(0, bound, f"{tag}:raw_penalty")
    model.add(raw == sum(components.values()))
    weight = context.config.lecturer_penalty_weight if kind == "LECTURER" else context.config.student_group_penalty_weight
    guard_model()
    return TimeIndexedState(kind, entity_id, nodes, maximum, components, raw, raw * weight, source, config, context)


def decode_time_indexed(solver, state):
    """CP penalties and maximum; successors/runs follow the solved chronology (no successor variables exist)."""
    from schedence_teaching.consecutive.metrics import policy_applies
    from .consecutive_constraints import ModeledConsecutiveEntity, ModeledConsecutiveLink
    source, config, context = state.source, state.config, state.context
    events = []
    for identity, (selected, day, start, _) in state.nodes.items():
        if solver.value(selected):
            row = source[identity]
            on_date = context.calendar.day_dates[config.days[solver.value(day)].day]
            minute = solver.value(start)
            events.append((minute, identity, on_date, row.covered_sessions[0].stream, row.duration_minutes))
    events.sort()
    links, prefixes, terminals = [], {}, []
    for index, (minute, identity, on_date, stream, duration) in enumerate(events):
        joined = False
        if index:
            previous = events[index - 1]
            comparable = previous[2] == on_date and previous[3] == stream
            gap = minute - previous[0] - previous[4] if comparable else None
            joined = comparable and gap == 0
            links.append(ModeledConsecutiveLink(previous[1], identity, comparable,
                comparable and policy_applies(context.policy, on_date), gap, joined))
            if not joined:
                terminals.append(previous[1])
        prefixes[identity] = (prefixes[events[index - 1][1]] if joined else 0) + duration
    if events:
        terminals.append(events[-1][1])
    selected = tuple(sorted(identity for _, identity, *_ in events))
    return ModeledConsecutiveEntity(state.kind, state.entity_id, selected,
        tuple(sorted(links, key=lambda row: (row.previous_delivery_id, row.next_delivery_id))),
        tuple((key, prefixes[key]) for key in selected), tuple(sorted(terminals)), solver.value(state.maximum),
        tuple((key, solver.value(value)) for key, value in sorted(state.components.items())),
        solver.value(state.raw), solver.value(state.weighted))
