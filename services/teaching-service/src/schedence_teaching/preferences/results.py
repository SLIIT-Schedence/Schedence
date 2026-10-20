"""Preference-delivery evaluations and role-aware descriptive metrics, never an objective."""

from dataclasses import asdict, dataclass
from enum import StrEnum

from .preparation import PreferenceDiagnostic


class PreferenceSatisfaction(StrEnum):
    SATISFIED = "SATISFIED"
    VIOLATED = "VIOLATED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class PreferenceEvaluation:
    delivery_id: str
    staff_id: str
    preference_id: str
    category: str
    direction: str
    satisfaction: PreferenceSatisfaction
    penalty_weight: int | None
    weight_reference: str | None
    penalty: int | None
    preference_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StaffPreferenceSummary:
    staff_id: str
    supplied_preference_count: int
    evaluated_count: int
    satisfied_count: int
    violation_count: int
    unresolved_count: int
    known_weighted_penalty: int | None
    total_weighted_penalty: int | None
    assigned_delivery_count: int
    scheduled_teaching_minutes: int
    teaching_days: tuple[str, ...]
    teaching_day_count: int
    daily_teaching_minutes: tuple[tuple[str, int], ...]
    longest_consecutive_teaching_minutes: int
    required_teaching_minutes: int | None
    teaching_target_ratio: float | None


@dataclass(frozen=True)
class PreferenceScoreReport:
    week_reference: str
    scoring_policy_reference: str
    weight_mode: str
    synthetic_data: bool
    assigned_delivery_count: int
    evaluated_count: int
    satisfied_count: int
    violation_count: int
    violated_delivery_count: int
    unresolved_count: int
    known_weighted_penalty: int | None
    total_weighted_penalty: int | None
    weighted_scoring_complete: bool | None
    unconfirmed_preference_count: int
    inactive_preference_count: int
    category_counts: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    staff: tuple[StaffPreferenceSummary, ...]
    evaluations: tuple[PreferenceEvaluation, ...]
    diagnostics: tuple[PreferenceDiagnostic, ...]
    cp_sat_preference_objective_enabled: bool = False

    def to_dict(self, *, include_staff_details=False):
        """Aggregate output by default; individual preference data stays in memory."""
        result = {key: value for key, value in asdict(self).items() if key not in {"staff", "evaluations", "diagnostics", "category_counts"}}
        result.update({"preference_evaluation_complete": self.unresolved_count == 0,
            "evaluation_scope": "supplied_scoped_soft_contracts_only",
            "category_counts": {category: dict(counts) for category, counts in self.category_counts},
            "diagnostic_counts": {code: sum(row.code == code for row in self.diagnostics)
                                  for code in sorted({row.code for row in self.diagnostics})},
            "synthetic_weights_only": self.weight_mode == "SYNTHETIC_DEFAULTS",
            "cp_sat_preference_objective_enabled": self.cp_sat_preference_objective_enabled, "hard_constraints_overridden": False,
            "university_ready": False})
        if include_staff_details:
            result.update(staff=[asdict(row) for row in self.staff],
                          evaluations=[asdict(row) for row in self.evaluations],
                          diagnostics=[asdict(row) for row in self.diagnostics])
        return result
