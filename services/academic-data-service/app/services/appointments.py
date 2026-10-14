"""Staff administrative appointments and the administrative-workload contract.

Appointments describe responsibility and protected administrative time. They
never touch login roles: nothing in this module calls the Identity Service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.principal import Principal
from app.models import (
    EMPLOYMENT_ACTIVE,
    AdministrativePositionType,
    AdministrativeWorkloadPolicy,
    AppointmentAuditLog,
    Staff,
    StaffAdministrativeAppointment,
)
from app.services.common import (
    PERM_APPOINTMENTS_ASSIGN,
    PERM_APPOINTMENTS_END,
    PERM_APPOINTMENTS_READ,
    PERM_APPOINTMENTS_UPDATE,
    PERM_WORKLOAD_READ,
    ServiceBase,
    overlaps,
)
from app.services.positions import policy_on

UPDATABLE_FIELDS = frozenset({"unit_name", "effective_until", "admin_hours_override", "override_reason", "notes"})


@dataclass(frozen=True)
class AppointmentInput:
    position_type_id: UUID
    effective_from: date
    effective_until: date | None = None
    unit_type: str | None = None
    unit_code: str | None = None
    unit_name: str | None = None
    admin_hours_override: Decimal | None = None
    override_reason: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class AppointmentView:
    appointment: StaffAdministrativeAppointment
    position: AdministrativePositionType
    status: str
    audit_trail: list[AppointmentAuditLog] = field(default_factory=list)


@dataclass(frozen=True)
class WorkloadItem:
    appointment: StaffAdministrativeAppointment
    position: AdministrativePositionType
    policy: AdministrativeWorkloadPolicy | None
    required_hours: Decimal
    hours_source: str  # POLICY | OVERRIDE


@dataclass(frozen=True)
class WorkloadAggregate:
    status: str  # COMPUTED | NOT_COMPUTED
    rule: str | None
    total_hours: Decimal | None
    reason: str | None = None


@dataclass(frozen=True)
class WorkloadResult:
    staff: Staff
    as_of: date
    items: list[WorkloadItem]
    aggregate: WorkloadAggregate


class AppointmentService(ServiceBase):
    # ---------------------------------------------------------------- commands

    async def assign(self, actor: Principal, staff_id: UUID, data: AppointmentInput) -> AppointmentView:
        staff = await self.uow.staff.get(staff_id, for_update=True)  # serialises appointment changes per staff member
        if staff is None:
            raise NotFoundError("Staff member not found.", code="STAFF_NOT_FOUND")
        self._authorize(actor, PERM_APPOINTMENTS_ASSIGN, staff.institution_id, "Staff member")
        if staff.employment_status != EMPLOYMENT_ACTIVE:
            raise ConflictError("The staff member is not active.", code="STAFF_INACTIVE")

        position = await self.uow.positions.get(data.position_type_id, for_update=True)
        if position is None or position.institution_id != staff.institution_id:
            raise NotFoundError("Position type not found in the staff member's institution.", code="POSITION_NOT_FOUND")
        if not position.is_active:
            raise ConflictError("The position type is disabled.", code="POSITION_DISABLED")

        unit_type, unit_code = self._validate_unit(position, data.unit_type, data.unit_code)
        self._validate_dates(data.effective_from, data.effective_until)
        if data.admin_hours_override is None:
            policies = (await self.uow.positions.policies([position.id]))[position.id]
            if policy_on(policies, data.effective_from) is None:
                raise ValidationFailedError("No administrative-hour policy is in effect for this position on the start date.",
                                            code="NO_WORKLOAD_POLICY")

        appointment = StaffAdministrativeAppointment(
            id=uuid.uuid4(), institution_id=staff.institution_id, staff_id=staff.id, position_type_id=position.id,
            unit_type=unit_type, unit_code=unit_code, unit_name=data.unit_name, effective_from=data.effective_from,
            effective_until=data.effective_until, admin_hours_override=data.admin_hours_override,
            override_reason=data.override_reason, notes=data.notes, created_at=self.clock(), updated_at=self.clock(),
            created_by_user_id=actor.user_id, updated_by_user_id=actor.user_id,
        )
        await self._check_conflicts(appointment, position)
        await self.uow.appointments.add(appointment)
        await self._audit(actor, "appointment.assigned", entity_type="APPOINTMENT", entity_id=appointment.id,
                          institution_id=staff.institution_id, staff_id=staff.id,
                          metadata={"position_code": position.code, "unit_code": unit_code, "effective_from": data.effective_from.isoformat(),
                                    "effective_until": data.effective_until.isoformat() if data.effective_until else None,
                                    "hours_override": str(data.admin_hours_override) if data.admin_hours_override is not None else None})
        await self._commit()
        return self._view(appointment, position)

    async def update(self, actor: Principal, appointment_id: UUID, changes: dict) -> AppointmentView:
        unknown = set(changes) - UPDATABLE_FIELDS
        if unknown:
            raise ValidationFailedError("These fields cannot be changed; end the appointment and create a new one instead.",
                                        details={"fields": sorted(unknown)})
        appointment, position = await self._appointment(actor, appointment_id, PERM_APPOINTMENTS_UPDATE, for_update=True)
        if appointment.ended_at is not None:
            raise ConflictError("Ended appointments cannot be modified.", code="APPOINTMENT_ENDED")

        merged = {f: changes.get(f, getattr(appointment, f)) for f in UPDATABLE_FIELDS}
        self._validate_dates(appointment.effective_from, merged["effective_until"])
        if (merged["admin_hours_override"] is None) != (merged["override_reason"] is None):
            raise ValidationFailedError("An hours override and its reason must be set (or cleared) together.", code="OVERRIDE_REASON_REQUIRED")
        if merged["admin_hours_override"] is None and appointment.admin_hours_override is not None:
            policies = (await self.uow.positions.policies([position.id]))[position.id]
            if policy_on(policies, appointment.effective_from) is None:
                raise ValidationFailedError("No policy applies to this appointment, so the override cannot be removed.",
                                            code="NO_WORKLOAD_POLICY")

        changed = sorted(f for f in UPDATABLE_FIELDS if merged[f] != getattr(appointment, f))
        if changed:
            if "effective_until" in changed:
                await self.uow.staff.get(appointment.staff_id, for_update=True)
                candidate = StaffAdministrativeAppointment(**{c.key: getattr(appointment, c.key) for c in appointment.__table__.columns})
                candidate.effective_until = merged["effective_until"]
                await self._check_conflicts(candidate, position, ignore_id=appointment.id)
            for f in changed:
                setattr(appointment, f, merged[f])
            appointment.updated_at, appointment.updated_by_user_id = self.clock(), actor.user_id
            await self._audit(actor, "appointment.updated", entity_type="APPOINTMENT", entity_id=appointment.id,
                              institution_id=appointment.institution_id, staff_id=appointment.staff_id, metadata={"fields": changed})
            await self._commit()
        return self._view(appointment, position)

    async def end(self, actor: Principal, appointment_id: UUID, *, end_date: date | None, reason: str | None) -> AppointmentView:
        appointment, position = await self._appointment(actor, appointment_id, PERM_APPOINTMENTS_END, for_update=True)
        if appointment.ended_at is not None:
            raise ConflictError("The appointment has already been ended.", code="APPOINTMENT_ENDED")
        end_date = end_date or self.today()
        if end_date < appointment.effective_from:
            raise ValidationFailedError("The end date cannot be before the appointment starts.", code="INVALID_EFFECTIVE_DATES")
        if appointment.effective_until is not None and end_date > appointment.effective_until:
            raise ValidationFailedError("The end date cannot be after the current effective-until date.", code="INVALID_EFFECTIVE_DATES")
        appointment.effective_until, appointment.ended_at = end_date, self.clock()
        appointment.ended_by_user_id, appointment.end_reason = actor.user_id, reason
        appointment.updated_at, appointment.updated_by_user_id = self.clock(), actor.user_id
        await self._audit(actor, "appointment.ended", entity_type="APPOINTMENT", entity_id=appointment.id,
                          institution_id=appointment.institution_id, staff_id=appointment.staff_id,
                          metadata={"end_date": end_date.isoformat(), "reason_given": bool(reason)})
        await self._commit()
        return self._view(appointment, position)

    # ---------------------------------------------------------------- queries

    async def list_for_staff(self, actor: Principal, staff_id: UUID, *, include_ended: bool) -> list[AppointmentView]:
        staff = await self._staff(actor, staff_id, PERM_APPOINTMENTS_READ)
        appointments = await self.uow.appointments.for_staff(staff.id)
        positions = await self.uow.positions.get_many(a.position_type_id for a in appointments)
        views = [self._view(a, positions[a.position_type_id]) for a in appointments]
        return views if include_ended else [v for v in views if v.status != "ENDED"]

    async def get(self, actor: Principal, appointment_id: UUID) -> AppointmentView:
        appointment, position = await self._appointment(actor, appointment_id, PERM_APPOINTMENTS_READ)
        trail = await self.uow.audit.for_entity("APPOINTMENT", appointment.id)
        return AppointmentView(appointment, position, appointment.status_on(self.today()), trail)

    async def administrative_workload(self, actor: Principal, staff_id: UUID, *, as_of: date | None) -> WorkloadResult:
        staff = await self._staff(actor, staff_id, PERM_WORKLOAD_READ)
        as_of = as_of or self.today()
        active = [a for a in await self.uow.appointments.for_staff(staff.id) if a.active_on(as_of)]
        positions = await self.uow.positions.get_many(a.position_type_id for a in active)
        policies = await self.uow.positions.policies(positions)
        items = []
        for a in sorted(active, key=lambda a: (a.effective_from, str(a.id))):
            policy = policy_on(policies[a.position_type_id], as_of)
            if a.admin_hours_override is not None:
                items.append(WorkloadItem(a, positions[a.position_type_id], policy, a.admin_hours_override, "OVERRIDE"))
            elif policy is not None:
                items.append(WorkloadItem(a, positions[a.position_type_id], policy, policy.admin_hours_per_week, "POLICY"))
            else:
                items.append(WorkloadItem(a, positions[a.position_type_id], None, Decimal("0"), "NO_POLICY"))
        return WorkloadResult(staff, as_of, items, aggregate(items))

    # ---------------------------------------------------------------- helpers

    async def _check_conflicts(self, candidate: StaffAdministrativeAppointment, position: AdministrativePositionType,
                               *, ignore_id: UUID | None = None) -> None:
        for existing in await self.uow.appointments.for_position(position.id):
            if existing.id == ignore_id or existing.unit_code != candidate.unit_code:
                continue
            if not overlaps(existing.effective_from, existing.effective_until, candidate.effective_from, candidate.effective_until):
                continue
            if existing.staff_id == candidate.staff_id:
                raise ConflictError("The staff member already holds this position for this unit in an overlapping period.",
                                    code="DUPLICATE_APPOINTMENT", details={"existing_appointment_id": str(existing.id)})
            if position.is_exclusive_per_unit:
                raise ConflictError("Another staff member holds this position for this unit in an overlapping period.",
                                    code="POSITION_ALREADY_HELD", details={"existing_appointment_id": str(existing.id)})

    @staticmethod
    def _validate_unit(position: AdministrativePositionType, unit_type: str | None, unit_code: str | None) -> tuple[str | None, str | None]:
        unit_code = unit_code.strip().upper() if unit_code else None
        if position.unit_type is not None:
            if unit_type is not None and unit_type != position.unit_type:
                raise ValidationFailedError(f"This position applies to a {position.unit_type} unit.", code="INVALID_UNIT")
            unit_type = position.unit_type
            if unit_type != "INSTITUTION" and not unit_code:
                raise ValidationFailedError(f"unit_code is required for a {unit_type} position.", code="INVALID_UNIT")
        return unit_type, unit_code

    @staticmethod
    def _validate_dates(effective_from: date, effective_until: date | None) -> None:
        if effective_until is not None and effective_until < effective_from:
            raise ValidationFailedError("effective_until cannot be before effective_from.", code="INVALID_EFFECTIVE_DATES")

    async def _staff(self, actor: Principal, staff_id: UUID, permission: str) -> Staff:
        staff = await self.uow.staff.get(staff_id)
        if staff is None:
            raise NotFoundError("Staff member not found.", code="STAFF_NOT_FOUND")
        self._authorize(actor, permission, staff.institution_id, "Staff member")
        return staff

    async def _appointment(self, actor: Principal, appointment_id: UUID, permission: str, *, for_update: bool = False
                           ) -> tuple[StaffAdministrativeAppointment, AdministrativePositionType]:
        appointment = await self.uow.appointments.get(appointment_id, for_update=for_update)
        if appointment is None:
            raise NotFoundError("Appointment not found.")
        self._authorize(actor, permission, appointment.institution_id, "Appointment")
        position = await self.uow.positions.get(appointment.position_type_id)
        assert position is not None
        return appointment, position

    def _view(self, appointment: StaffAdministrativeAppointment, position: AdministrativePositionType) -> AppointmentView:
        return AppointmentView(appointment, position, appointment.status_on(self.today()))


def aggregate(items: list[WorkloadItem]) -> WorkloadAggregate:
    """Combine concurrent appointments only when the institution has said how.

    * 0 or 1 appointment: the total is unambiguous.
    * Several: computed only if every appointment's policy declares the *same*
      rule (ADDITIVE = sum; CAPPED = sum limited by a shared cap; REPLACEMENT =
      the largest single requirement). Otherwise NOT_COMPUTED, and the
      scheduler must use the per-appointment figures.
    """
    if any(i.hours_source == "NO_POLICY" for i in items):
        return WorkloadAggregate("NOT_COMPUTED", None, None, "An active appointment has no policy in effect on this date.")
    if not items:
        return WorkloadAggregate("COMPUTED", "NO_APPOINTMENTS", Decimal("0"))
    if len(items) == 1:
        return WorkloadAggregate("COMPUTED", "SINGLE_APPOINTMENT", items[0].required_hours)

    rules = {i.policy.combination_rule if i.policy else None for i in items}
    if len(rules) != 1 or None in rules or "UNSPECIFIED" in rules:
        return WorkloadAggregate("NOT_COMPUTED", None, None,
                                 "The policy for combining multiple concurrent appointments is unresolved for this staff member.")
    rule = rules.pop()
    hours = [i.required_hours for i in items]
    if rule == "ADDITIVE":
        return WorkloadAggregate("COMPUTED", rule, sum(hours, Decimal("0")))
    if rule == "REPLACEMENT":
        return WorkloadAggregate("COMPUTED", rule, max(hours))
    caps = {i.policy.cap_hours_per_week for i in items}  # CAPPED
    if len(caps) != 1:
        return WorkloadAggregate("NOT_COMPUTED", rule, None, "The CAPPED policies declare different caps.")
    return WorkloadAggregate("COMPUTED", rule, min(sum(hours, Decimal("0")), caps.pop()))
