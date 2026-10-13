from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

T = TypeVar("T")
HoursIn = Annotated[Decimal, Field(ge=0, le=168, max_digits=5, decimal_places=2)]
UnitType = Literal["INSTITUTION", "FACULTY", "DEPARTMENT", "PROGRAM", "OTHER"]
CombinationRule = Literal["UNSPECIFIED", "ADDITIVE", "CAPPED", "REPLACEMENT"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ------------------------------------------------------------------ policies


class PolicyFields(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "admin_hours_per_week": 8, "combination_rule": "ADDITIVE", "policy_reference": "Workload circular 2026/02",
        "effective_from": "2026-11-01"}]})

    admin_hours_per_week: HoursIn = Field(description="Required administrative hours per week under this policy (configurable, no default).")
    combination_rule: CombinationRule = Field(default="UNSPECIFIED", description="How this position's hours combine with other concurrent appointments.")
    cap_hours_per_week: HoursIn | None = None
    policy_reference: str | None = Field(default=None, max_length=200, description="e.g. the circular or senate decision that sets the hours")
    notes: str | None = Field(default=None, max_length=2000)
    effective_from: date | None = Field(default=None, description="Defaults to today (institution time zone).")

    @model_validator(mode="after")
    def _cap_only_for_capped(self) -> PolicyFields:
        if (self.combination_rule == "CAPPED") != (self.cap_hours_per_week is not None):
            raise ValueError("cap_hours_per_week is required for CAPPED and not allowed otherwise")
        return self


class PolicyOut(ApiModel):
    id: UUID
    version: int
    admin_hours_per_week: float
    combination_rule: str
    cap_hours_per_week: float | None
    policy_reference: str | None
    notes: str | None
    effective_from: date
    effective_until: date | None
    created_at: datetime
    created_by_user_id: UUID | None


# ------------------------------------------------------------------ positions


class PositionCreateIn(PolicyFields):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "institution_id": "00000000-0000-4000-8000-000000000000", "code": "PROGRAM_COORDINATOR", "name": "Program Coordinator",
        "description": "Responsible for coordinating an academic program", "unit_type": "PROGRAM",
        "is_exclusive_per_unit": False, "admin_hours_per_week": 6, "combination_rule": "UNSPECIFIED",
        "effective_from": "2026-01-01"}]})

    institution_id: UUID
    code: str = Field(min_length=2, max_length=60, pattern=r"^[A-Z][A-Z0-9_]*$", examples=["PROGRAM_COORDINATOR"])
    name: str = Field(min_length=1, max_length=150, examples=["Program Coordinator"])
    description: str = Field(default="", max_length=2000)
    unit_type: UnitType | None = Field(default=None, description="Kind of unit the position is held for, e.g. DEPARTMENT for HOD")
    is_exclusive_per_unit: bool = Field(default=False, description="Only one holder per unit at a time (e.g. one HOD per department)")


class PositionUpdateIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"description": "Coordinates one undergraduate program"}]})

    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    unit_type: UnitType | None = None
    is_exclusive_per_unit: bool | None = None


class PositionOut(ApiModel):
    id: UUID
    institution_id: UUID
    code: str
    name: str
    description: str
    unit_type: str | None
    is_exclusive_per_unit: bool
    is_active: bool
    default_admin_hours_per_week: float | None = Field(description="Hours under the policy in effect today")
    effective_policy: PolicyOut | None
    created_at: datetime
    updated_at: datetime


class PositionDetailOut(PositionOut):
    policy_history: list[PolicyOut]


# ------------------------------------------------------------------ staff


class StaffCreateIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "institution_id": "00000000-0000-4000-8000-000000000000", "staff_number": "EMP001", "full_name": "Example Lecturer",
        "email": "lecturer1@example.edu", "academic_rank": "Senior Lecturer", "identity_user_id": None}]})

    institution_id: UUID
    staff_number: str = Field(min_length=1, max_length=50)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    academic_rank: str | None = Field(default=None, max_length=100, examples=["Senior Lecturer"])
    identity_user_id: UUID | None = Field(default=None, description="Optional link to a Schedence login; staff may have none")


class StaffOut(ApiModel):
    id: UUID
    institution_id: UUID
    staff_number: str
    full_name: str
    email: str | None
    academic_rank: str | None
    employment_status: str
    identity_user_id: UUID | None
    created_at: datetime


# ------------------------------------------------------------------ appointments


class AppointmentCreateIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "position_type_id": "00000000-0000-4000-8000-000000000000", "unit_code": "COMPUTING", "unit_name": "Department of Computing",
        "effective_from": "2026-11-01", "effective_until": None}]})

    position_type_id: UUID
    unit_type: UnitType | None = None
    unit_code: str | None = Field(default=None, min_length=1, max_length=60, examples=["COMPUTING"])
    unit_name: str | None = Field(default=None, max_length=150, examples=["Department of Computing"])
    effective_from: date
    effective_until: date | None = Field(default=None, description="Inclusive last day; omit for open-ended")
    admin_hours_override: HoursIn | None = Field(default=None, description="Use instead of the position policy (reason required)")
    override_reason: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate(self) -> AppointmentCreateIn:
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until cannot be before effective_from")
        if (self.admin_hours_override is None) != (self.override_reason is None):
            raise ValueError("admin_hours_override and override_reason must be given together")
        return self


class AppointmentUpdateIn(RequestModel):
    """Only these fields may change. To change staff, position, unit or start date, end the appointment and create a new one."""

    unit_name: str | None = Field(default=None, max_length=150)
    effective_until: date | None = None
    admin_hours_override: HoursIn | None = None
    override_reason: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class AppointmentEndIn(RequestModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"end_date": "2027-10-31", "reason": "Term completed"}]})

    end_date: date | None = Field(default=None, description="Last day of the appointment; defaults to today")
    reason: str | None = Field(default=None, max_length=500)


class AppointmentOut(ApiModel):
    id: UUID
    institution_id: UUID
    staff_id: UUID
    position_type_id: UUID
    position_code: str
    position_name: str
    unit_type: str | None
    unit_code: str | None
    unit_name: str | None
    effective_from: date
    effective_until: date | None
    status: Literal["SCHEDULED", "ACTIVE", "ENDED"]
    admin_hours_override: float | None
    override_reason: str | None
    notes: str | None
    ended_at: datetime | None
    end_reason: str | None
    created_at: datetime
    updated_at: datetime


class AuditEntryOut(ApiModel):
    action: str
    actor_user_id: UUID | None
    occurred_at: datetime
    metadata: dict[str, Any]


class AppointmentDetailOut(AppointmentOut):
    audit_trail: list[AuditEntryOut]


# ------------------------------------------------------------------ workload contract


class WorkloadPolicyRef(ApiModel):
    policy_id: UUID
    version: int
    combination_rule: str
    cap_hours_per_week: float | None
    policy_reference: str | None
    effective_from: date
    effective_until: date | None


class WorkloadAppointmentOut(ApiModel):
    appointment_id: UUID
    position_type_id: UUID
    position_code: str
    position_name: str
    unit_type: str | None
    unit_code: str | None
    unit_name: str | None
    effective_from: date
    effective_until: date | None
    required_admin_hours_per_week: float
    hours_source: Literal["POLICY", "OVERRIDE", "NO_POLICY"]
    policy: WorkloadPolicyRef | None


class WorkloadAggregateOut(ApiModel):
    status: Literal["COMPUTED", "NOT_COMPUTED"]
    rule: str | None
    total_admin_hours_per_week: float | None
    reason: str | None


class AdministrativeWorkloadOut(ApiModel):
    """Contract for future scheduling services (teaching / examinations)."""

    staff_id: UUID
    institution_id: UUID
    as_of: date
    appointments: list[WorkloadAppointmentOut]
    aggregate: WorkloadAggregateOut
