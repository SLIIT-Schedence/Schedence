from __future__ import annotations

from app.models import AdministrativeWorkloadPolicy, Staff
from app.schemas.academic import (
    AdministrativeWorkloadOut,
    AppointmentDetailOut,
    AppointmentOut,
    AuditEntryOut,
    PolicyOut,
    PositionDetailOut,
    PositionOut,
    StaffOut,
    WorkloadAggregateOut,
    WorkloadAppointmentOut,
    WorkloadPolicyRef,
)
from app.services.appointments import AppointmentView, WorkloadResult
from app.services.positions import PositionView


def _f(value) -> float | None:
    return float(value) if value is not None else None


def policy_out(p: AdministrativeWorkloadPolicy) -> PolicyOut:
    return PolicyOut(id=p.id, version=p.version, admin_hours_per_week=float(p.admin_hours_per_week), combination_rule=p.combination_rule,
                     cap_hours_per_week=_f(p.cap_hours_per_week), policy_reference=p.policy_reference, notes=p.notes,
                     effective_from=p.effective_from, effective_until=p.effective_until, created_at=p.created_at,
                     created_by_user_id=p.created_by_user_id)


def position_out(view: PositionView) -> PositionOut:
    p, current = view.position, view.current_policy
    return PositionOut(id=p.id, institution_id=p.institution_id, code=p.code, name=p.name, description=p.description, unit_type=p.unit_type,
                       is_exclusive_per_unit=p.is_exclusive_per_unit, is_active=p.is_active,
                       default_admin_hours_per_week=_f(current.admin_hours_per_week) if current else None,
                       effective_policy=policy_out(current) if current else None, created_at=p.created_at, updated_at=p.updated_at)


def position_detail_out(view: PositionView) -> PositionDetailOut:
    return PositionDetailOut(**position_out(view).model_dump(), policy_history=[policy_out(p) for p in view.policies])


def staff_out(s: Staff) -> StaffOut:
    return StaffOut.model_validate(s)


def appointment_out(view: AppointmentView) -> AppointmentOut:
    a, p = view.appointment, view.position
    return AppointmentOut(id=a.id, institution_id=a.institution_id, staff_id=a.staff_id, position_type_id=p.id, position_code=p.code,
                          position_name=p.name, unit_type=a.unit_type, unit_code=a.unit_code, unit_name=a.unit_name,
                          effective_from=a.effective_from, effective_until=a.effective_until, status=view.status,
                          admin_hours_override=_f(a.admin_hours_override), override_reason=a.override_reason, notes=a.notes,
                          ended_at=a.ended_at, end_reason=a.end_reason, created_at=a.created_at, updated_at=a.updated_at)


def appointment_detail_out(view: AppointmentView) -> AppointmentDetailOut:
    return AppointmentDetailOut(**appointment_out(view).model_dump(), audit_trail=[
        AuditEntryOut(action=e.action, actor_user_id=e.actor_user_id, occurred_at=e.occurred_at, metadata=dict(e.safe_metadata or {}))
        for e in view.audit_trail
    ])


def workload_out(result: WorkloadResult) -> AdministrativeWorkloadOut:
    return AdministrativeWorkloadOut(
        staff_id=result.staff.id,
        institution_id=result.staff.institution_id,
        as_of=result.as_of,
        appointments=[
            WorkloadAppointmentOut(
                appointment_id=i.appointment.id, position_type_id=i.position.id, position_code=i.position.code,
                position_name=i.position.name, unit_type=i.appointment.unit_type, unit_code=i.appointment.unit_code,
                unit_name=i.appointment.unit_name, effective_from=i.appointment.effective_from,
                effective_until=i.appointment.effective_until, required_admin_hours_per_week=float(i.required_hours),
                hours_source=i.hours_source,
                policy=WorkloadPolicyRef(policy_id=i.policy.id, version=i.policy.version, combination_rule=i.policy.combination_rule,
                                         cap_hours_per_week=_f(i.policy.cap_hours_per_week), policy_reference=i.policy.policy_reference,
                                         effective_from=i.policy.effective_from, effective_until=i.policy.effective_until)
                if i.policy else None,
            )
            for i in result.items
        ],
        aggregate=WorkloadAggregateOut(status=result.aggregate.status, rule=result.aggregate.rule,
                                       total_admin_hours_per_week=_f(result.aggregate.total_hours), reason=result.aggregate.reason),
    )
