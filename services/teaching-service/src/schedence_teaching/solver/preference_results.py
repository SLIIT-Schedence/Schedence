"""Optional preference score plus bounded lexicographic objective audit details."""

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .preference_constraints import ModeledPreferencePenalty
from .preference_inputs import objective_plan
from .results import ScheduledDelivery

if TYPE_CHECKING:
    from schedence_teaching.preferences.results import PreferenceScoreReport


@dataclass(frozen=True)
class PreferenceOptimizationReport:
    score: "PreferenceScoreReport"
    modeled_evaluations: tuple[ModeledPreferencePenalty, ...]
    applied_policy_references: tuple[str, ...]
    configured_criteria_count: int
    confirmed_criteria_count: int
    priority: str
    priority_policy_reference: str
    priority_order: tuple[str, ...]
    objective_components: tuple[tuple[str, int], ...]
    objective_bounds: tuple[tuple[str, int], ...]
    objective_coefficients: tuple[tuple[str, int], ...]
    objective_value: int
    objective_status: str

    def to_dict(self, *, include_staff_details=False):
        return {"enabled": True, "score": self.score.to_dict(include_staff_details=include_staff_details),
            "modeled_evaluations": [asdict(row) for row in self.modeled_evaluations] if include_staff_details else None,
            "applied_policy_references": list(self.applied_policy_references),
            "configured_criteria_count": self.configured_criteria_count,
            "confirmed_criteria_count": self.confirmed_criteria_count,
            "total_preference_penalty": self.score.total_weighted_penalty,
            "total_confirmed_preference_penalty": self.score.total_weighted_penalty if self.score.weight_mode == "CONFIRMED_WEIGHTS" else None,
            "priority": self.priority, "priority_policy_reference": self.priority_policy_reference,
            "priority_order": list(self.priority_order), "objective_components": dict(self.objective_components),
            "objective_bounds": dict(self.objective_bounds), "objective_coefficients": dict(self.objective_coefficients),
            "objective_value": self.objective_value, "objective_status": self.objective_status,
            "priority_is_development_configuration": True, "university_ready": False}


def preference_report(prepared, config, result, context, modeled, combination_config, *, staff_input=None, work_periods=None):
    from schedence_teaching.preferences.scoring import _score_validated_timetable
    score = _score_validated_timetable(prepared, config, result, context.preferences, context.calendar, context.policy,
                                      staff_input=staff_input, work_periods=work_periods)
    order, bounds, coefficients = objective_plan(prepared, context, combination_config)
    components = {"DELIVERIES": len(result.schedule),
        "CONTACT_MINUTES": sum(row.duration_minutes if isinstance(row, ScheduledDelivery) else row.session.duration_minutes for row in result.schedule),
        "PREFERENCE_PENALTY": score.total_weighted_penalty}
    refs = {context.policy.policy_reference, context.config.priority_policy_reference,
            context.config.calendar_semantics_reference}
    if context.policy.time_window_policy_reference:
        refs.add(context.policy.time_window_policy_reference)
    refs.update(row.weight_reference for unit in context.units for row in unit if row.weight_reference is not None)
    return PreferenceOptimizationReport(score, tuple(modeled), tuple(sorted(refs)), len(context.units),
        context.confirmed_criteria_count, context.config.priority.value, context.config.priority_policy_reference,
        order, tuple((key, components[key]) for key in order), tuple((key, bounds[key]) for key in order),
        tuple((key, coefficients[key]) for key in order), sum(components[key] * coefficients[key] for key in order), result.status.value)
