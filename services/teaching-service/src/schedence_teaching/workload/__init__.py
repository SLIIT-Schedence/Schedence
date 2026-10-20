"""Configurable campus policy and evidence-based assessment; no solver constraints."""

from .contracts import (
    AdministrativeReservation, AdministrativeReservationStatus,
    CampusAttendanceRecord, RecognizedWorkRecord, RecognizedWorkType, StaffAppointment,
    StaffWorkProfile, WeeklyWorkloadInput, WorkloadBand, WorkloadPolicy, WorkloadRule,
    WorkloadTargetBasis, confirmed_campus_policy,
)
from .administration import AdministrativeAllocationAssessment
from .assessment import (
    AttendanceStatus, FulfillmentStatus, ResolvedWorkPolicy, StaffWorkloadAssessment,
    WeeklyWorkloadReport, WorkloadInputError, assess_weekly_workload, resolve_work_policy,
)
from .period_contracts import (
    AdministrativeAggregateStatus, AdministrativePolicyResolution, AdministrativeTimeReservation,
    AppointmentApplicability, EligibleAdministrativeWindow, FlexibleAdministrativePolicy,
    FlexibleAdministrativeRequest, PeriodApprovalStatus, PeriodProtection, PeriodProvenance,
    ResearchDayPolicy, ResearchDayReservation, Weekday, WorkPeriodInput,
)
from .period_preparation import (
    FlexibleAdministrativeCandidate, NormalizedWorkInterval, PreparedWorkPeriods,
    WorkPeriodDiagnostic, prepare_work_periods,
)

__all__ = [
    "AdministrativeAggregateStatus", "AdministrativePolicyResolution", "AdministrativeTimeReservation",
    "AppointmentApplicability", "EligibleAdministrativeWindow", "FlexibleAdministrativePolicy",
    "FlexibleAdministrativeRequest", "PeriodApprovalStatus", "PeriodProtection", "PeriodProvenance",
    "ResearchDayPolicy", "ResearchDayReservation", "Weekday", "WorkPeriodInput",
    "FlexibleAdministrativeCandidate", "NormalizedWorkInterval", "PreparedWorkPeriods",
    "WorkPeriodDiagnostic", "prepare_work_periods",
    "AdministrativeReservation", "AdministrativeReservationStatus", "AdministrativeAllocationAssessment",
    "CampusAttendanceRecord", "RecognizedWorkRecord", "RecognizedWorkType", "StaffAppointment",
    "StaffWorkProfile", "WeeklyWorkloadInput", "WorkloadBand", "WorkloadPolicy", "WorkloadRule",
    "WorkloadTargetBasis", "confirmed_campus_policy", "AttendanceStatus", "FulfillmentStatus",
    "ResolvedWorkPolicy", "StaffWorkloadAssessment", "WeeklyWorkloadReport", "WorkloadInputError",
    "assess_weekly_workload", "resolve_work_policy",
]
