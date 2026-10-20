"""Exact linear teaching-target deviation, using existing staff choice variables."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeledFairnessWorkload:
    staff_id: str
    teaching_minutes: int
    scaled_utilization: int
    scaled_absolute_target_deviation: int


def add_fairness_variables(model, context, assignments, guard_model):
    uppers = dict(context.workload_bounds)
    deviation_bounds, weights = dict(context.deviation_bounds), dict(context.integer_weights)
    variables = {}
    scale = context.normalization_scale
    for identity, target in context.targets:
        workload = model.new_int_var(0, uppers[identity], f"fairness_teaching:{identity}")
        model.add(workload == sum(minutes * chosen for _, chosen, _, minutes in assignments.get(identity, ())))
        utilization = model.new_int_var(0, uppers[identity] * (scale // target), f"fairness_utilization:{identity}")
        model.add(utilization == workload * (scale // target))
        deviation = model.new_int_var(0, deviation_bounds[identity], f"fairness_target_deviation:{identity}")
        model.add_abs_equality(deviation, utilization - scale)
        variables[identity] = (workload, utilization, deviation)
        guard_model()
    maximum = model.new_int_var(0, context.maximum_bound, "fairness_maximum_target_deviation")
    model.add_max_equality(maximum, [row[2] for row in variables.values()])
    aggregate = model.new_int_var(0, context.aggregate_bound, "fairness_aggregate_target_deviation")
    model.add(aggregate == sum(weights[identity] * row[2] for identity, row in variables.items()))
    guard_model()
    components={"MAXIMUM_TARGET_DEVIATION": maximum, "AGGREGATE_TARGET_DEVIATION": aggregate}
    from .fairness_inputs import OVERAGE_PRIORITIES, FairnessPriority
    if context.config.priority in OVERAGE_PRIORITIES:
        overages=[]
        for identity,target in context.targets:
            overage=model.new_int_var(0,max(0,uppers[identity]-target),f'teaching_target_excess:{identity}')
            model.add_max_equality(overage,[variables[identity][0]-target,0])
            overages.append(overage);guard_model()
        components['TOTAL_OVER_TARGET_MINUTES']=sum(overages)
        if context.config.priority is FairnessPriority.DELIVERY_COST_THEN_PEAK_OVERAGE_TOTAL_OVERAGE_FAIRNESS_PREFERENCES:
            peak=model.new_int_var(0,max(max(0,uppers[identity]-target) for identity,target in context.targets),
                'teaching_target_maximum_excess')
            model.add_max_equality(peak,overages)
            components['MAXIMUM_OVER_TARGET_MINUTES']=peak;guard_model()
    return variables, components


def decode_fairness_workloads(solver, variables):
    return tuple(ModeledFairnessWorkload(identity, *(solver.value(value) for value in row))
                 for identity, row in sorted(variables.items()))
