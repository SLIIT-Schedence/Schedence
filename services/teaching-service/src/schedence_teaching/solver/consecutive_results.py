"""Consecutive optimization audit and visible teaching-day/span/idle trade-offs."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date
import json
from typing import TYPE_CHECKING

from .consecutive_constraints import ModeledConsecutiveEntity, ModeledConsecutiveLink
from .consecutive_inputs import consecutive_objective_plan
from .lexicographic import OptimizationStage

if TYPE_CHECKING:
    from schedence_teaching.consecutive import ConsecutiveReport


@dataclass(frozen=True)
class DailyTeachingSpan:
    on_date: date
    delivery_count: int
    teaching_minutes: int
    first_start_minute: int
    last_end_minute: int
    span_minutes: int
    idle_minutes: int
    gaps: tuple[int, ...]


@dataclass(frozen=True)
class TeachingDistribution:
    kind: str
    entity_id: str
    teaching_day_count: int
    total_daily_span_minutes: int
    total_daily_idle_minutes: int
    maximum_daily_idle_gap_minutes: int
    days: tuple[DailyTeachingSpan, ...]


@dataclass(frozen=True)
class ConsecutiveOptimizationReport:
    assessment: "ConsecutiveReport"
    optimization_config_json: str
    modeled_entities: tuple[ModeledConsecutiveEntity, ...]
    teaching_distributions: tuple[TeachingDistribution, ...]
    total_weighted_penalty: int
    priority_order: tuple[str, ...]
    objective_bounds: tuple[tuple[str, int], ...]
    objective_components: tuple[tuple[str, int], ...]
    objective_vector: tuple[int, ...]
    stages: tuple[OptimizationStage, ...]
    objective_status: str
    all_stages_proven_optimal: bool
    estimated_successor_arcs: int

    def to_dict(self, *, include_details=False):
        from schedence_teaching.consecutive.results import json_value
        calendar = json.loads(self.assessment.calendar_snapshot_json)
        options = json.loads(self.optimization_config_json)
        output = {"enabled": True, "assessment": self.assessment.to_dict(
                include_lecturer_details=include_details, include_student_group_details=include_details),
            "optimization_config": options, "total_configured_consecutive_penalty": self.total_weighted_penalty,
            "total_confirmed_consecutive_penalty": self.total_weighted_penalty if not calendar["synthetic_data"] else None,
            "priority_order": list(self.priority_order), "objective_bounds": dict(self.objective_bounds),
            "objective_components": dict(self.objective_components), "objective_vector": list(self.objective_vector),
            "stages": [asdict(row) for row in self.stages], "objective_status": self.objective_status,
            "all_stages_proven_optimal": self.all_stages_proven_optimal,
            "estimated_successor_arcs": self.estimated_successor_arcs,
            "priority_is_development_configuration": True,
            "lecturer_teaching_days": sum(row.teaching_day_count for row in self.teaching_distributions if row.kind == "LECTURER"),
            "student_group_teaching_days": sum(row.teaching_day_count for row in self.teaching_distributions if row.kind == "STUDENT_GROUP"),
            "idle_time_is_teaching_gap_not_verified_rest": True, "university_ready": False}
        if include_details:
            output["modeled_entities"] = json_value([asdict(row) for row in self.modeled_entities])
            output["teaching_distributions"] = json_value([asdict(row) for row in self.teaching_distributions])
        return output


def optimized_assessment(assessment, enforce_hard):
    """Use the same Step 11A metrics while accurately labelling actual solver mode."""
    def diagnostics(codes):
        return tuple("confirmed_hard_break_enforced" if enforce_hard and code ==
            "confirmed_hard_break_assessed_not_enforced_by_existing_solver" else code for code in codes)
    return replace(assessment, cp_sat_consecutive_objective_enabled=True,
        confirmed_hard_break_enforced_by_solver=enforce_hard,
        lecturers=tuple(replace(row, diagnostics=diagnostics(row.diagnostics)) for row in assessment.lecturers),
        student_groups=tuple(replace(row, diagnostics=diagnostics(row.diagnostics)) for row in assessment.student_groups),
        diagnostics=diagnostics(assessment.diagnostics))


def expected_consecutive_entities(result, context, assessment):
    """Independent audit from Step 11A sequences; never inspect CP variables/arcs."""
    from schedence_teaching.consecutive.metrics import delivery_id, delivery_stream, policy_applies
    bookings = {delivery_id(row): row for row in result.schedule}
    assessed = {(row.kind, row.entity_id): row for row in (*assessment.lecturers, *assessment.student_groups)}
    output = []
    for kind, identity, _ in context.participants:
        row = assessed[kind, identity]
        selected = tuple(sorted((*row.direct_delivery_ids, *row.inherited_delivery_ids)))
        chronological = sorted((bookings[key] for key in selected),
            key=lambda item: (context.calendar.day_dates[item.day], item.start_minute, delivery_id(item)))
        links, prefixes, terminals = [], {}, []
        for first, second in zip(chronological, chronological[1:]):
            comparable = context.calendar.day_dates[first.day] == context.calendar.day_dates[second.day] and delivery_stream(first) == delivery_stream(second)
            applicable = comparable and policy_applies(context.policy, context.calendar.day_dates[second.day])
            gap = second.start_minute - first.end_minute if comparable else None
            links.append(ModeledConsecutiveLink(delivery_id(first), delivery_id(second), comparable, applicable, gap, comparable and gap == 0))
        for sequence in row.sequences:
            for run in sequence.uninterrupted_runs:
                minutes = 0
                for key in run.delivery_ids:
                    minutes += bookings[key].end_minute - bookings[key].start_minute
                    prefixes[key] = minutes
                terminals.append(run.delivery_ids[-1])
        raw = row.total_soft_penalty if row.scoring_enabled and row.total_soft_penalty is not None else 0
        weight = context.config.lecturer_penalty_weight if kind == "LECTURER" else context.config.student_group_penalty_weight
        output.append(ModeledConsecutiveEntity(kind, identity, selected,
            tuple(sorted(links, key=lambda item: (item.previous_delivery_id, item.next_delivery_id))),
            tuple(sorted(prefixes.items())), tuple(sorted(terminals)), row.maximum_continuous_minutes,
            row.penalty_components, raw, raw * weight))
    return tuple(output)


def teaching_distributions(result, context, assessment):
    from schedence_teaching.consecutive.metrics import delivery_id
    bookings = {delivery_id(row): row for row in result.schedule}
    output = []
    for row in (*assessment.lecturers, *assessment.student_groups):
        days = defaultdict(list)
        for key in (*row.direct_delivery_ids, *row.inherited_delivery_ids):
            booking = bookings[key]
            days[context.calendar.day_dates[booking.day]].append(booking)
        profiles = []
        for on_date, records in sorted(days.items()):
            records = sorted(records, key=lambda item: item.start_minute)
            minutes = sum(item.end_minute - item.start_minute for item in records)
            span = records[-1].end_minute - records[0].start_minute
            profiles.append(DailyTeachingSpan(on_date, len(records), minutes, records[0].start_minute,
                records[-1].end_minute, span, span - minutes,
                tuple(second.start_minute - first.end_minute for first, second in zip(records, records[1:]))))
        output.append(TeachingDistribution(row.kind, row.entity_id, len(profiles), sum(item.span_minutes for item in profiles),
            sum(item.idle_minutes for item in profiles), max((gap for item in profiles for gap in item.gaps), default=0), tuple(profiles)))
    return tuple(output)


def consecutive_report(prepared, result, context, assessment, modeled, stages, fairness_context, preference_context, combination_config):
    assessment = optimized_assessment(assessment, context.config.enforce_confirmed_hard_break)
    expected = expected_consecutive_entities(result, context, assessment)
    total = sum(row.weighted_penalty for row in expected)
    order, bounds = consecutive_objective_plan(prepared, context, fairness_context, preference_context, combination_config)
    components = {"DELIVERIES": len(result.schedule), "CONTACT_MINUTES": assessment.scheduled_teaching_minutes,
        "PREFERENCE_PENALTY": result.preference_report.score.total_weighted_penalty if result.preference_report else 0,
        "CONSECUTIVE_PENALTY": total}
    if result.fairness_report is not None:
        components.update(dict(result.fairness_report.objective_components))
    return ConsecutiveOptimizationReport(assessment, context.config.model_dump_json(), tuple(modeled),
        teaching_distributions(result, context, assessment), total, order, tuple(bounds.items()),
        tuple((key, components[key]) for key in order), tuple(components[key] for key in order), tuple(stages),
        result.status.value, len(stages) == len(order) and all(row.status == "OPTIMAL" for row in stages), context.estimated_arcs)
