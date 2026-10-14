"""Post-solve fairness contracts and exact independent assessment (no CP objective)."""

from .assessment import FairnessInputError, assess_fairness, validate_fairness_report
from .contracts import (
    BalanceMetric, ComparisonGroup, ComparisonScope, FairnessPolicy,
    MissingEvidenceBehavior, Normalization, StaffComparisonDecision,
)
from .metrics import TeachingTargetMetrics, teaching_target_metrics
from .results import AssignmentOpportunities, FairnessReport, GroupFairnessAssessment, StaffFairnessAssessment

__all__ = [
    "AssignmentOpportunities", "BalanceMetric", "ComparisonGroup", "ComparisonScope",
    "FairnessInputError", "FairnessPolicy", "FairnessReport", "GroupFairnessAssessment",
    "MissingEvidenceBehavior", "Normalization", "StaffComparisonDecision", "StaffFairnessAssessment",
    "TeachingTargetMetrics", "assess_fairness", "teaching_target_metrics", "validate_fairness_report",
]
