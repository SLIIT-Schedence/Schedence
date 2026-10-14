"""Fairness audit output: target deviation and descriptive cohort dispersion differ."""

from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import json
from typing import TYPE_CHECKING

from .fairness_constraints import ModeledFairnessWorkload
from .lexicographic import OptimizationStage
from .fairness_inputs import fairness_objective_plan

if TYPE_CHECKING:
    from schedence_teaching.fairness import FairnessReport


@dataclass(frozen=True)
class FairnessOptimizationReport:
    assessment: "FairnessReport"
    optimization_config_json: str
    comparison_cohorts: tuple[tuple[str, tuple[str, ...]], ...]
    exclusions: tuple[tuple[str, tuple[str, ...]], ...]
    input_opportunities: tuple
    targets: tuple[tuple[str, int], ...]
    normalization_scale: int
    weight_scale: int
    integer_weights: tuple[tuple[str, int], ...]
    modeled_workloads: tuple[ModeledFairnessWorkload, ...]
    absolute_target_deviation_minutes: tuple[tuple[str, int], ...]
    normalized_absolute_target_deviations: tuple[tuple[str, Fraction], ...]
    maximum_normalized_target_deviation: Fraction
    aggregate_normalized_target_deviation: Fraction
    priority_order: tuple[str, ...]
    objective_bounds: tuple[tuple[str, int], ...]
    objective_components: tuple[tuple[str, int], ...]
    objective_vector: tuple[int, ...]
    stages: tuple[OptimizationStage, ...]
    objective_status: str
    all_stages_proven_optimal: bool
    # Frozen before solving, independent of selected flexible admin decisions.
    cohort_opportunity_scope: str = "INPUT_ELIGIBILITY_AND_FIXED_CALENDAR_ONLY"

    def to_dict(self, *, include_staff_details=False):
        from schedence_teaching.fairness.results import _json
        result = {"enabled": True, "assessment": self.assessment.to_dict(include_staff_details=include_staff_details),
            "optimization_config": json.loads(self.optimization_config_json),
            "comparison_cohorts": _json(self.comparison_cohorts), "exclusions": _json(self.exclusions),
            "targets": dict(self.targets), "normalization_scale": self.normalization_scale,
            "absolute_target_deviation_minutes": dict(self.absolute_target_deviation_minutes),
            "weight_scale": self.weight_scale, "integer_weights": dict(self.integer_weights),
            "maximum_normalized_target_deviation": _json(self.maximum_normalized_target_deviation),
            "aggregate_normalized_target_deviation": _json(self.aggregate_normalized_target_deviation),
            "normalized_absolute_target_deviations": _json(self.normalized_absolute_target_deviations),
            "priority_order": list(self.priority_order), "objective_bounds": dict(self.objective_bounds),
            "objective_components": dict(self.objective_components), "objective_vector": list(self.objective_vector),
            "stages": [asdict(row) for row in self.stages], "objective_status": self.objective_status,
            "all_stages_proven_optimal": self.all_stages_proven_optimal,
            "cohort_opportunity_scope": self.cohort_opportunity_scope,
            "deviation_reference": "TEACHING_TARGET", "strategy": "STAGED_LEXICOGRAPHIC",
            "priority_is_development_configuration": True, "university_ready": False}
        if include_staff_details:
            result["modeled_workloads"] = [asdict(row) for row in self.modeled_workloads]
            result["input_opportunities"] = _json(self.input_opportunities)
        return result


def fairness_report(prepared, result, context, assessment, modeled, stages, preference_context, combination_config):
    from schedence_teaching.fairness.metrics import teaching_target_metrics
    teaching = {}
    for row in result.schedule:
        teaching[row.assigned_staff_id] = teaching.get(row.assigned_staff_id, 0) + row.end_minute - row.start_minute
    deviations = {identity: teaching_target_metrics(teaching.get(identity, 0), target).absolute_normalized_target_deviation
                  for identity, target in context.targets}
    weights = dict(context.integer_weights)
    maximum = max(deviations.values())
    aggregate = sum((weights[key] * value / context.weight_scale for key, value in deviations.items()), Fraction())
    order, bounds = fairness_objective_plan(prepared, context, preference_context, combination_config)
    components = {"DELIVERIES": len(result.schedule), "CONTACT_MINUTES": sum(teaching.values()),
        "TOTAL_OVER_TARGET_MINUTES":sum(max(0,teaching.get(sid,0)-target) for sid,target in context.targets),
        "PREFERENCE_PENALTY": result.preference_report.score.total_weighted_penalty if result.preference_report else 0,
        "MAXIMUM_TARGET_DEVIATION": int(maximum * context.normalization_scale),
        "AGGREGATE_TARGET_DEVIATION": int(aggregate * context.normalization_scale * context.weight_scale)}
    return FairnessOptimizationReport(replace(assessment, cp_sat_fairness_objective_enabled=True),
        context.config.model_dump_json(), context.cohorts,
        context.exclusions, context.opportunities, context.targets, context.normalization_scale, context.weight_scale, context.integer_weights,
        tuple(modeled), tuple((identity, abs(teaching.get(identity, 0) - target)) for identity, target in context.targets),
        tuple(sorted(deviations.items())), maximum, aggregate, order, tuple(bounds.items()),
        tuple((key, components[key]) for key in order), tuple(components[key] for key in order), tuple(stages),
        result.status.value, len(stages) == len(order) and all(row.status == "OPTIMAL" for row in stages))
