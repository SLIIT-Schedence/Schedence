"""Bounded chronological circuits over optional selected physical teaching events."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeledConsecutiveLink:
    previous_delivery_id: str
    next_delivery_id: str
    comparable: bool
    applicable: bool
    gap_minutes: int | None
    back_to_back: bool


@dataclass(frozen=True)
class ModeledConsecutiveEntity:
    kind: str
    entity_id: str
    selected_delivery_ids: tuple[str, ...]
    successors: tuple[ModeledConsecutiveLink, ...]
    run_prefix_minutes: tuple[tuple[str, int], ...]
    terminal_delivery_ids: tuple[str, ...]
    maximum_continuous_minutes: int
    penalty_components: tuple[tuple[str, int], ...]
    raw_penalty: int
    weighted_penalty: int


def _and(model, flags, name):
    result = model.new_bool_var(name)
    for flag in flags:
        model.add(result <= flag)
    model.add(result >= sum(flags) - len(flags) + 1)
    return result


def add_consecutive_variables(model, prepared, config, context, alternatives, decisions, guard_model, *, start_variables=None,
                              staff_start_ticks=None):
    """One chronological circuit per attendee, no all-triples adjacency encoding.

    Dummy node closes the path. Inactive alternatives self-loop. Real arcs are
    time-increasing, hence cannot form a separate cycle or skip an active event.
    Cross-date/stream neighbors reset runs and receive no pair/gap penalty.
    """
    # Local import preserves preference-first and importer fresh import orders.
    from schedence_teaching.consecutive.metrics import policy_applies
    source = {row.delivery_id: row for row in alternatives}
    time_data = {}
    offsets = [(context.calendar.day_dates[day.day] - context.calendar.week_start).days * 1440 for day in config.days]
    applicability = [int(policy_applies(context.policy, context.calendar.day_dates[day.day])) for day in config.days]
    for identity, (day, local_start, choices, presence) in decisions.items():
        date_offset = model.new_int_var(0, 6*1440, f"consecutive_date:{identity}")
        model.add_element(day, offsets, date_offset)
        start = model.new_int_var(0, 7*1440, f"consecutive_start:{identity}")
        model.add(start == date_offset + local_start * config.granularity_minutes)
        applicable = model.new_bool_var(f"consecutive_applicable_date:{identity}")
        model.add_element(day, applicability, applicable)
        time_data[identity] = (day, start, choices, presence, applicable)
        guard_model()
    states, weighted_terms = [], []
    from .consecutive_inputs import ConsecutiveFormulation
    grid = context.config.formulation is ConsecutiveFormulation.TIME_INDEXED
    if grid:
        from .consecutive_time_indexed import start_literals, time_indexed_entity
        from .results import SolverInputError
        if start_variables is None:
            raise SolverInputError("Time-indexed consecutive scoring requires the original start variables.")
        if len(set(context.calendar.day_dates[day.day] for day in config.days)) != len(config.days):
            raise SolverInputError("Time-indexed consecutive scoring requires one configured day per calendar date.")
        ticks_per_day, start_cache = (1440 + config.granularity_minutes - 1) // config.granularity_minutes, {}
    bounds = {(kind, identity): bound for kind, identity, bound in context.entity_bounds}
    for kind, entity_id, possible_ids in context.participants:
        scoring = context.policy.score_lecturers if kind == "LECTURER" else context.policy.score_student_groups
        hard = context.policy.confirmed_hard_minimum_break
        hard_applies = context.config.enforce_confirmed_hard_break and kind in hard.applies_to
        nodes = {}
        for identity in possible_ids:
            if identity not in time_data:
                continue  # Original model has fixed this unusable alternative absent.
            day, start, choices, presence, applicable = time_data[identity]
            selected = choices.get(entity_id) if kind == "LECTURER" else presence
            if selected is not None:
                nodes[identity] = (selected, day, start, applicable)
        if not nodes:
            zero_components = {criterion.value: 0 for criterion in context.policy.soft_criteria} if scoring else {}
            if scoring and context.policy.idle_gap_minute_penalty is not None:
                zero_components["IDLE_GAP_MINUTES"] = 0
            states.append((kind, entity_id, {}, (), {}, {}, 0, zero_components, 0, 0))
            continue
        if grid:
            # A lecturer selected for a delivery is already restricted to that pair's enforced start domain;
            # start ticks outside it can never be chosen, so no literal is created for them (exact domain tightening).
            allowed = ({identity: staff_start_ticks.get((identity, entity_id), ()) for identity in nodes}
                       if kind == "LECTURER" and staff_start_ticks is not None else None)
            state = time_indexed_entity(model, config, context, kind, entity_id, nodes, source,
                {identity: start_literals(model, identity, start_variables[identity], ticks_per_day, start_cache)
                 for identity in nodes}, applicability, bounds[kind, entity_id], guard_model, allowed_starts=allowed)
            weighted_terms.append(state.weighted)
            states.append(state)
            continue
        tag = f"consecutive:{kind}:{entity_id}"
        runs = {identity: model.new_int_var(0, 1440, f"{tag}:run:{identity}") for identity in nodes}
        touch_out = {identity: [] for identity in nodes}
        arcs, links = [], []
        empty = model.new_bool_var(f"{tag}:empty")
        model.add(sum(row[0] for row in nodes.values()) == 0).only_enforce_if(empty)
        model.add(sum(row[0] for row in nodes.values()) >= 1).only_enforce_if(empty.Not())
        arcs.append((0, 0, empty))
        numbers = {identity: index for index, identity in enumerate(nodes, 1)}
        coverage = {identity: {row.occurrence_id for row in source[identity].covered_sessions} for identity in nodes}
        raw_terms = {}
        if scoring:
            raw_terms = {criterion.value: [] for criterion in context.policy.soft_criteria}
            if context.policy.idle_gap_minute_penalty is not None:
                raw_terms["IDLE_GAP_MINUTES"] = []
        for identity, (selected, _, _, _) in nodes.items():
            number = numbers[identity]
            first = model.new_bool_var(f"{tag}:first:{identity}")
            last = model.new_bool_var(f"{tag}:last:{identity}")
            arcs.extend(((number, number, selected.Not()), (0, number, first), (number, 0, last)))
            model.add(runs[identity] == 0).only_enforce_if(selected.Not())
            model.add(runs[identity] == source[identity].duration_minutes).only_enforce_if(first)
        for first, (first_selected, first_day, first_start, _) in nodes.items():
            for second, (second_selected, second_day, second_start, second_active) in nodes.items():
                if first == second or coverage[first] & coverage[second]:
                    continue  # Exact coverage makes these alternatives mutually exclusive.
                chosen = model.new_bool_var(f"{tag}:next:{first}:{second}")
                arcs.append((numbers[first], numbers[second], chosen))
                model.add(chosen <= first_selected)
                model.add(chosen <= second_selected)
                model.add(first_start + source[first].duration_minutes <= second_start).only_enforce_if(chosen)
                same_stream = source[first].covered_sessions[0].stream == source[second].covered_sessions[0].stream
                if same_stream:
                    same_day = model.new_bool_var(f"{tag}:same_day:{first}:{second}")
                    model.add(first_day == second_day).only_enforce_if(same_day)
                    model.add(first_day != second_day).only_enforce_if(same_day.Not())
                    comparable = _and(model, [chosen, same_day], f"{tag}:comparable:{first}:{second}")
                else:
                    comparable = model.new_constant(0)
                applicable = _and(model, [comparable, second_active], f"{tag}:applicable:{first}:{second}")
                gap = model.new_int_var(0, 1440, f"{tag}:gap:{first}:{second}")
                model.add(gap == second_start - first_start - source[first].duration_minutes).only_enforce_if(comparable)
                model.add(gap == 0).only_enforce_if(comparable.Not())
                touch = model.new_bool_var(f"{tag}:touch:{first}:{second}")
                model.add(touch <= comparable)
                model.add(gap == 0).only_enforce_if(touch)
                model.add(gap >= 1).only_enforce_if([comparable, touch.Not()])
                touch_out[first].append(touch)
                model.add(runs[second] == runs[first] + source[second].duration_minutes).only_enforce_if(touch)
                model.add(runs[second] == source[second].duration_minutes).only_enforce_if([chosen, touch.Not()])
                if hard_applies:
                    model.add(gap >= hard.minutes).only_enforce_if(applicable)
                if "BACK_TO_BACK" in raw_terms:
                    active_touch = _and(model, [touch, second_active], f"{tag}:scored_touch:{first}:{second}")
                    raw_terms["BACK_TO_BACK"].append(active_touch * context.policy.back_to_back_penalty)
                if "SHORT_BREAK" in raw_terms:
                    preferred = context.policy.preferred_minimum_break_minutes
                    shortfall = model.new_int_var(0, preferred, f"{tag}:shortfall:{first}:{second}")
                    model.add_max_equality(shortfall, [preferred - gap, 0])
                    scored = model.new_int_var(0, preferred, f"{tag}:scored_shortfall:{first}:{second}")
                    model.add(scored == shortfall).only_enforce_if(applicable)
                    model.add(scored == 0).only_enforce_if(applicable.Not())
                    raw_terms["SHORT_BREAK"].append(scored * context.policy.short_break_minute_penalty)
                if "IDLE_GAP_MINUTES" in raw_terms:
                    idle = model.new_int_var(0, 1440, f"{tag}:idle:{first}:{second}")
                    model.add(idle == gap).only_enforce_if(applicable)
                    model.add(idle == 0).only_enforce_if(applicable.Not())
                    raw_terms["IDLE_GAP_MINUTES"].append(idle * context.policy.idle_gap_minute_penalty)
                links.append((first, second, chosen, comparable, applicable, gap, touch))
                guard_model()
        model.add_circuit(arcs)
        terminals = {}
        for identity, (selected, _, _, applicable) in nodes.items():
            terminal = model.new_bool_var(f"{tag}:terminal:{identity}")
            model.add(terminal + sum(touch_out[identity]) == selected)
            terminals[identity] = terminal
            if "EXCESS_CONTINUOUS_MINUTES" in raw_terms:
                excess = model.new_int_var(0, 1440, f"{tag}:excess:{identity}")
                model.add_max_equality(excess, [runs[identity] - context.policy.maximum_preferred_continuous_minutes, 0])
                active_terminal = _and(model, [terminal, applicable], f"{tag}:active_terminal:{identity}")
                scored = model.new_int_var(0, 1440, f"{tag}:scored_excess:{identity}")
                model.add(scored == excess).only_enforce_if(active_terminal)
                model.add(scored == 0).only_enforce_if(active_terminal.Not())
                raw_terms["EXCESS_CONTINUOUS_MINUTES"].append(scored * context.policy.excess_continuous_minute_penalty)
            guard_model()
        maximum = model.new_int_var(0, 1440, f"{tag}:maximum_continuous")
        model.add_max_equality(maximum, list(runs.values()))
        components = {}
        for criterion, terms in sorted(raw_terms.items()):
            value = model.new_int_var(0, bounds[kind, entity_id], f"{tag}:penalty:{criterion}")
            model.add(value == sum(terms))
            components[criterion] = value
        if "EXCESS_CONTINUOUS_MINUTES" in components:
            # Redundant and valid: a run's excess is at least the sum of its own deliveries' excesses.
            limit = context.policy.maximum_preferred_continuous_minutes
            everywhere = all(policy_applies(context.policy, context.calendar.day_dates[day.day]) for day in config.days)
            floor = [(source[identity].duration_minutes - limit) * (selected if everywhere else
                     _and(model, [selected, applicable], f"{tag}:excess_floor:{identity}"))
                     for identity, (selected, _, _, applicable) in nodes.items() if source[identity].duration_minutes > limit]
            if floor:
                model.add(components["EXCESS_CONTINUOUS_MINUTES"] >= context.policy.excess_continuous_minute_penalty * sum(floor))
        raw = model.new_int_var(0, bounds[kind, entity_id], f"{tag}:raw_penalty")
        model.add(raw == sum(components.values()))
        weight = context.config.lecturer_penalty_weight if kind == "LECTURER" else context.config.student_group_penalty_weight
        weighted = raw * weight
        weighted_terms.append(weighted)
        states.append((kind, entity_id, nodes, tuple(links), runs, terminals, maximum, components, raw, weighted))
        guard_model()
    penalty = model.new_int_var(0, context.penalty_bound, "consecutive_total_penalty")
    model.add(penalty == sum(weighted_terms))
    guard_model()
    return tuple(states), penalty


def decode_consecutive_entities(solver, states):
    from .consecutive_time_indexed import TimeIndexedState, decode_time_indexed
    output = []
    for state in states:
        if isinstance(state, TimeIndexedState):
            output.append(decode_time_indexed(solver, state))
            continue
        kind, identity, nodes, links, runs, terminals, maximum, components, raw, weighted = state
        selected = tuple(sorted(key for key, row in nodes.items() if solver.value(row[0])))
        successors = []
        for first, second, chosen, comparable, applicable, gap, touch in links:
            if solver.value(chosen):
                compared = bool(solver.value(comparable))
                successors.append(ModeledConsecutiveLink(first, second, compared, bool(solver.value(applicable)),
                    solver.value(gap) if compared else None, bool(solver.value(touch))))
        output.append(ModeledConsecutiveEntity(kind, identity, selected,
            tuple(sorted(successors, key=lambda row: (row.previous_delivery_id, row.next_delivery_id))),
            tuple((key, solver.value(runs[key])) for key in selected),
            tuple(key for key in selected if solver.value(terminals[key])), solver.value(maximum),
            tuple((key, solver.value(value)) for key, value in sorted(components.items())),
            solver.value(raw), solver.value(weighted)))
    return tuple(output)
