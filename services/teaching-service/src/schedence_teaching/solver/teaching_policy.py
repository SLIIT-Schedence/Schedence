"""Versioned assignment policy. Source eligibility is never inferred from a title."""
from datetime import date
from typing import Annotated

from pydantic import Field, StrictBool, model_validator

from schedence_teaching.schemas import SessionType, StaffType
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.workload.contracts import WorkloadBand, StaffAppointment

TeachingDays = Annotated[int, Field(strict=True, ge=1, le=7)]


class SessionTypePermission(InputContract):
    staff_id: RequiredText
    offering_id: RequiredText | None = None
    session_types: set[SessionType] = Field(min_length=1)
    effective_from: date
    effective_until: date
    evidence_reference: RequiredText

    @model_validator(mode='after')
    def valid_dates(self):
        if self.effective_until < self.effective_from:
            raise ValueError('Session permission end precedes its start.')
        return self


class ActiveTeachingAppointment(InputContract):
    staff_id: RequiredText
    appointment: StaffAppointment
    effective_from: date
    effective_until: date
    evidence_reference: RequiredText

    @model_validator(mode='after')
    def valid_dates(self):
        if self.effective_until < self.effective_from:
            raise ValueError('Appointment end precedes its start.')
        return self


class TeachingAssignmentPolicy(InputContract):
    policy_reference: RequiredText
    week_reference: RequiredText
    evidence_reference: RequiredText
    rank_permissions: dict[WorkloadBand,set[SessionType]]
    appointment_permissions: dict[StaffAppointment,set[SessionType]]
    institution_teaching_day_limit: TeachingDays
    staff_teaching_day_limits: dict[RequiredText, TeachingDays] = Field(default_factory=dict)
    appointment_teaching_day_limits: dict[StaffAppointment, TeachingDays] = Field(default_factory=dict)
    permissions: list[SessionTypePermission] = Field(default_factory=list)
    active_appointments: list[ActiveTeachingAppointment] = Field(default_factory=list)
    # Disable only for explicitly labelled feasibility-only diagnostics.
    require_target_optimization: StrictBool = True

    @model_validator(mode='after')
    def unambiguous_evidence(self):
        for rows, keys in ((self.permissions, ('staff_id','offering_id')),
                           (self.active_appointments, ('staff_id',))):
            for i, first in enumerate(rows):
                for second in rows[i+1:]:
                    same_scope=(first.staff_id==second.staff_id and (not hasattr(first,'offering_id') or
                        first.offering_id is None or second.offering_id is None or first.offering_id==second.offering_id))
                    if same_scope and (
                        max(first.effective_from,second.effective_from)<=min(first.effective_until,second.effective_until)):
                        raise ValueError('Overlapping permission/appointment records require one authoritative resolution.')
        return self


def assignment_issues(inputs, staff_input, policy, staff_id, session, on_date):
    """Direct source checks shared by audit, independent validation and domains.

    This does not call the CP-SAT candidate builder. Rank rules can restrict a
    module member but cannot grant module membership. Rank permissions are
    separately confirmed project policy; owner-specific permissions can further
    restrict assignments without overriding rank, appointment or membership.
    """
    people={p.staff_id:p for p in inputs.staff_members}
    person=people.get(staff_id)
    if person is None:return ('unknown_staff',)
    issues=[]
    if not any(m.offering_id==session.offering_id and m.staff_id==staff_id for m in inputs.teaching_team_members):
        issues.append('module_teaching_team_ineligible')
    profiles={p.staff_id:p for p in staff_input.workload_input.profiles}
    profile=profiles.get(staff_id)
    band=profile.workload_band if profile else (WorkloadBand.INSTRUCTOR if person.staff_type is StaffType.INSTRUCTOR else None)
    appointments=[a for a in policy.active_appointments if a.staff_id==staff_id and a.effective_from<=on_date<=a.effective_until]
    appointment=appointments[0].appointment if appointments else (profile.appointment if profile else None)
    if person.staff_type is StaffType.ACADEMIC and band is None:
        issues.append('unresolved_academic_rank')
    # Snapshot profiles represent applicability to this explicitly dated week;
    # dated appointment evidence, when supplied, takes precedence for its dates.
    if any(a.staff_id==staff_id for a in policy.active_appointments) and not appointments:
        appointment=None
    if person.staff_type is StaffType.INSTRUCTOR:
        if band is not WorkloadBand.INSTRUCTOR or appointment is not None:issues.append('staff_type_classification_conflict')
    elif band is WorkloadBand.INSTRUCTOR:
        issues.append('staff_type_classification_conflict')
    if band in policy.rank_permissions and session.session_type not in policy.rank_permissions[band]:
        issues.append('rank_session_type_forbidden')
    if appointment is not None:
        if appointment not in policy.appointment_permissions:issues.append('missing_explicit_appointment_permission')
        elif session.session_type not in policy.appointment_permissions[appointment]:issues.append('appointment_session_type_forbidden')
    permissions=[p for p in policy.permissions if p.staff_id==staff_id and p.offering_id in (None,session.offering_id)
                 and p.effective_from<=on_date<=p.effective_until]
    if not permissions and any(p.staff_id==staff_id and p.offering_id in (None,session.offering_id) for p in policy.permissions):
        issues.append('inactive_source_session_type_permission')
    if band is not None and band not in policy.rank_permissions:
        issues.append('missing_explicit_session_type_permission')
    if permissions and session.session_type not in permissions[0].session_types:
        issues.append('source_session_type_forbidden')
    return tuple(issues)


def teaching_day_limit(staff_input, staff_id):
    """Weekly union across streams/modes; campus attendance creates no limit."""
    policy=staff_input.assignment_policy
    explicit=next((a.weekly_teaching_max_days for a in staff_input.availability if a.staff_id==staff_id),None)
    if policy is None:return explicit
    limit=min(policy.staff_teaching_day_limits.get(staff_id,policy.institution_teaching_day_limit),
        policy.institution_teaching_day_limit)
    profile=next((p for p in staff_input.workload_input.profiles if p.staff_id==staff_id),None)
    appointments={a.appointment for a in policy.active_appointments if a.staff_id==staff_id
                  and any(a.effective_from<=d<=a.effective_until for d in staff_input.day_dates.values())}
    if not appointments and profile and profile.appointment:appointments={profile.appointment}
    for appointment in appointments:
        if appointment in policy.appointment_teaching_day_limits:
            limit=min(limit,policy.appointment_teaching_day_limits[appointment])
    return min(limit,explicit) if explicit is not None else limit


def eligible_assignment_days(inputs, staff_input, policy, staff_id, sessions):
    return {day for day,on_date in staff_input.day_dates.items() if all(
        not assignment_issues(inputs,staff_input,policy,staff_id,session,on_date) for session in sessions)}
