"""Explicit assessment choices, never university-confirmed fairness defaults."""

from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, StrictBool, model_validator

from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.period_contracts import PeriodProvenance


def _exact(value):
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise ValueError("use an integer, rational string or Fraction; floating-point policy values are not accepted")
    return value


ExactNonnegative = Annotated[Fraction, BeforeValidator(_exact), Field(ge=0)]
ExactPositive = Annotated[Fraction, BeforeValidator(_exact), Field(gt=0)]


class ComparisonScope(StrEnum):
    EXPLICIT_GROUPS = "EXPLICIT_GROUPS"
    IDENTICAL_ELIGIBILITY_POOLS = "IDENTICAL_ELIGIBILITY_POOLS"


class Normalization(StrEnum):
    TEACHING_TARGET = "TEACHING_TARGET"
    RAW_MINUTES = "RAW_MINUTES"


class BalanceMetric(StrEnum):
    MAXIMUM_DEVIATION = "MAXIMUM_DEVIATION"
    MEAN_ABSOLUTE_DEVIATION = "MEAN_ABSOLUTE_DEVIATION"
    VARIANCE = "VARIANCE"


class MissingEvidenceBehavior(StrEnum):
    EXCLUDE = "EXCLUDE"
    ERROR = "ERROR"


class ComparisonGroup(InputContract):
    group_id: RequiredText
    staff_ids: tuple[RequiredText, ...] = Field(min_length=1)
    reason: RequiredText

    @model_validator(mode="after")
    def distinct_staff(self) -> Self:
        if len(set(self.staff_ids)) != len(self.staff_ids):
            raise ValueError("comparison group staff IDs must be unique")
        return self


class StaffComparisonDecision(InputContract):
    staff_id: RequiredText
    include: StrictBool
    reason: RequiredText


class FairnessPolicy(InputContract):
    policy_reference: RequiredText
    week_reference: RequiredText
    provenance: PeriodProvenance
    comparison_scope: ComparisonScope
    normalization: Normalization = Normalization.TEACHING_TARGET
    balance_metric: BalanceMetric = BalanceMetric.MAXIMUM_DEVIATION
    comparison_groups: list[ComparisonGroup] = Field(default_factory=list)
    # Eligibility scope only: workload metrics always cover the complete supplied week.
    eligibility_offering_ids: set[RequiredText] | None = None
    staff_decisions: list[StaffComparisonDecision] = Field(default_factory=list)
    require_availability_evidence: StrictBool = True
    missing_evidence_behavior: MissingEvidenceBehavior = MissingEvidenceBehavior.EXCLUDE
    include_variance: StrictBool = False
    include_maximum_deviation: StrictBool = True
    relative_deviation_threshold: ExactNonnegative | None = None
    staff_weights: dict[RequiredText, ExactPositive] = Field(default_factory=dict)
    weight_reference: RequiredText | None = None

    @model_validator(mode="after")
    def explicit_choices(self) -> Self:
        if (self.comparison_scope is ComparisonScope.EXPLICIT_GROUPS) != bool(self.comparison_groups):
            raise ValueError("explicit scope requires groups; automatic eligibility scope must not supply groups")
        for records, key in ((self.comparison_groups, "group_id"), (self.staff_decisions, "staff_id")):
            ids = [getattr(row, key) for row in records]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {key} in fairness policy")
        if bool(self.staff_weights) != (self.weight_reference is not None):
            raise ValueError("optional weights require a source reference, and vice versa")
        if self.balance_metric is BalanceMetric.VARIANCE and not self.include_variance:
            raise ValueError("variance balance metric requires include_variance")
        if self.balance_metric is BalanceMetric.MAXIMUM_DEVIATION and not self.include_maximum_deviation:
            raise ValueError("maximum-deviation balance metric requires include_maximum_deviation")
        return self
