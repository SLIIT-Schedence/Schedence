"""Initial university's confirmed policy data; never a generic solver default."""
from schedence_teaching.schemas import SessionType
from schedence_teaching.workload.contracts import WorkloadBand,StaffAppointment


def initial_university_assignment_policy(*, week_reference, **overrides):
    from schedence_teaching.solver.teaching_policy import TeachingAssignmentPolicy
    lecture={SessionType.LECTURE}
    academic={SessionType.LECTURE,SessionType.TUTORIAL,SessionType.WORKSHOP}
    data=dict(policy_reference='user-confirmed-initial-university-teaching-policy-v1',
        evidence_reference='user-confirmed-complete-eligibility-matrix-and-maximum-six-teaching-dates',
        week_reference=week_reference,institution_teaching_day_limit=6,
        rank_permissions={WorkloadBand.INSTRUCTOR:{SessionType.LAB},
            WorkloadBand.ASSISTANT_LECTURER:set(academic),WorkloadBand.LECTURER:set(academic),
            WorkloadBand.SENIOR_LECTURER:set(academic),WorkloadBand.DR:set(lecture),
            WorkloadBand.ASSISTANT_PROFESSOR:set(lecture),WorkloadBand.PROFESSOR:set(lecture)},
        appointment_permissions={a:set(lecture) for a in StaffAppointment})
    data.update(overrides)
    return TeachingAssignmentPolicy.model_validate(data)
