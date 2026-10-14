"""Synthetic Step 9A.2 preparation only; no teaching solver or campus dataset calls."""

import json

from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.workload import (
    AdministrativeTimeReservation, FlexibleAdministrativePolicy, FlexibleAdministrativeRequest,
    ResearchDayPolicy, ResearchDayReservation, WeeklyWorkloadInput, WorkPeriodInput,
    confirmed_campus_policy, prepare_work_periods,
)


def run_scenarios():
    teaching = TeachingTimetableInput(
        staff_members=[{"staff_id": "SYNTHETIC-STAFF", "staff_name": "Synthetic Staff", "position": "Professor", "staff_type": "ACADEMIC"}],
        module_offerings=[], teaching_team_members=[], student_groups=[], session_requirements=[])
    workload = WeeklyWorkloadInput(
        week_reference="SYNTHETIC-WEEK", policy=confirmed_campus_policy(),
        profiles=[{"staff_id": "SYNTHETIC-STAFF", "workload_band": "PROFESSOR", "evidence_reference": "synthetic:profile"}])
    provenance = {"source": "synthetic-demo", "reference": "synthetic:explicit-authorization", "source_record_id": "SYNTHETIC-SOURCE"}
    authorization = {"staff_id": "SYNTHETIC-STAFF", "status": "APPROVED", "approval_reference": "synthetic:approval",
                     "provenance": provenance, "effective_from": "2026-10-19", "effective_until": "2026-10-25"}
    admin = AdministrativeTimeReservation.model_validate({**authorization,
        "policy_reference": workload.policy.policy_reference, "reservation_id": "SYNTHETIC-ADMIN",
        "on_date": "2026-10-19", "window": {"start_minute": 540, "end_minute": 780},
        "planned_duration_minutes": 240, "protection": "HARD"})
    research_policy = ResearchDayPolicy.model_validate({**authorization, "policy_reference": "synthetic:research",
        "allow_full_day": True, "allow_partial_day": True, "allow_hard_protection": True})
    research = ResearchDayReservation.model_validate({**authorization, "policy_reference": "synthetic:research",
        "reservation_id": "SYNTHETIC-RESEARCH", "weekday": "FRIDAY", "full_day": True, "protection": "HARD"})
    base = WorkPeriodInput(week_reference="SYNTHETIC-WEEK", week_start="2026-10-19", week_end="2026-10-25")
    approved = base.model_copy(deep=True)
    approved.administrative_periods = [admin]
    approved.research_policies, approved.research_reservations = [research_policy], [research]
    pending = base.model_copy(deep=True)
    pending_admin = admin.model_dump(round_trip=True)
    pending_admin.update(status="PENDING", approval_reference=None)
    pending.administrative_periods = [AdministrativeTimeReservation.model_validate(pending_admin)]
    soft = base.model_copy(deep=True)
    soft.research_policies = [research_policy]
    soft.research_reservations = [ResearchDayReservation.model_validate({**authorization, "policy_reference": "synthetic:research",
        "reservation_id": "SYNTHETIC-SOFT-RESEARCH", "weekday": "WEDNESDAY", "full_day": False,
        "windows": [{"start_minute": 840, "end_minute": 960}], "protection": "SOFT"})]
    flexible = base.model_copy(deep=True)
    flexible.flexible_policies = [FlexibleAdministrativePolicy.model_validate({**authorization,
        "policy_reference": "synthetic:flexible", "administrative_policy_reference": workload.policy.policy_reference,
        "flexible_scheduling_permitted": True, "allow_hard_protection": True})]
    flexible.flexible_requests = [FlexibleAdministrativeRequest.model_validate({**authorization,
        "policy_reference": "synthetic:flexible", "request_id": "SYNTHETIC-FLEXIBLE-ADMIN",
        "planned_duration_minutes": 240, "allow_split": False, "protection": "HARD",
        "eligible_windows": [{"on_date": "2026-10-20", "start_minute": 540, "end_minute": 780}]})]
    return {name: prepare_work_periods(teaching, workload, inputs).to_dict()
            for name, inputs in [("approved_admin_and_research", approved), ("pending_admin", pending),
                                 ("soft_research", soft), ("flexible_admin", flexible)]}


if __name__ == "__main__":
    print(json.dumps(run_scenarios(), indent=2))
