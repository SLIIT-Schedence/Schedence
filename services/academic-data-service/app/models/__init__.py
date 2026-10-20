from app.models.base import SCHEMA, Base
from app.models.entities import (
    COMBINATION_RULES,
    EMPLOYMENT_ACTIVE,
    EMPLOYMENT_INACTIVE,
    UNIT_TYPES,
    AdministrativePositionType,
    AdministrativeWorkloadPolicy,
    AppointmentAuditLog,
    Staff,
    StaffAdministrativeAppointment,
)
from app.models import teaching_inputs  # Register academic-owned scheduling input tables.

__all__ = [
    "COMBINATION_RULES",
    "EMPLOYMENT_ACTIVE",
    "EMPLOYMENT_INACTIVE",
    "SCHEMA",
    "UNIT_TYPES",
    "AdministrativePositionType",
    "AdministrativeWorkloadPolicy",
    "AppointmentAuditLog",
    "Base",
    "Staff",
    "StaffAdministrativeAppointment",
]
