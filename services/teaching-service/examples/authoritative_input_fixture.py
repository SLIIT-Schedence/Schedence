"""Complete, explicitly synthetic integration export. No campus records are read."""

from itertools import combinations
from pathlib import Path
import runpy
from uuid import UUID

from schedence_teaching.integration import InputEvidence, SchedulingSubmission
from schedence_teaching.integration.contracts import expected_owner
from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.solver import CombinationSolverConfig, StudentLunchEnforcementConfig


INSTITUTION = UUID("00000000-0000-4000-8000-000000000001")


def build_submission():
    fixture = runpy.run_path(str(Path(__file__).with_name("integration_fixture.py")))
    case = fixture["build_case"](time_limit=5.0)
    contexts = case["inputs"]
    calendar = contexts["preference_calendar"]
    combination = contexts["combination_input"].input_snapshot.model_dump()
    hierarchy = case["prepared"].hierarchy
    proven = {tuple(sorted(row["group_ids"])) for row in combination["population_evidence"]}
    # These additional population assertions are invented FOR THIS SYNTHETIC
    # EXAMPLE ONLY. They are not derived from IDs, specialization or stream.
    for first, second in combinations(sorted(hierarchy.parents), 2):
        if not hierarchy.conflicts(first, second) and (first, second) not in proven:
            combination["population_evidence"].append(dict(group_ids=(first, second),
                relationship="DISJOINT", evidence_reference="synthetic:explicit-example-population"))
    lunch = confirmed_student_lunch_policy(policy_reference="synthetic:fixed-lunch",
        week_reference=calendar.week_reference,
        provenance=dict(source="synthetic:campus-periods-example", reference="synthetic:scope"),
        applicable_dates=set(calendar.day_dates.values()), applicable_streams={"WEEKDAY", "WEEKEND"},
        applicability_reference="synthetic:explicit-both-streams")
    raw = dict(institution_id=INSTITUTION, mode="SYNTHETIC", academic_term_reference="synthetic:term-2026-1",
        semester=1, demand_complete=True,
        academic_staff_references={row.staff_id: UUID(int=100+index) for index, row in
            enumerate(case["prepared"].input_snapshot.staff_members)},
        calendar=calendar.model_dump(), solver_config=case["config"].model_dump(), combination_input=combination,
        combination_config=CombinationSolverConfig().model_dump(),
        work_period_input=contexts["work_periods"].input_snapshot.model_dump(),
        preference_input=contexts["preference_input"].input_snapshot.model_dump(),
        student_lunch_policy=lunch.model_dump(), student_lunch_config=StudentLunchEnforcementConfig(
            applicability_confirmation="CONFIRMED", applicability_confirmation_reference="synthetic:lunch-approved").model_dump())
    for key in ("venue_input", "staff_input", "work_period_config", "preference_policy", "preference_config",
                "fairness_policy", "fairness_config", "consecutive_policy", "consecutive_config"):
        raw[key] = contexts[key].model_dump()
    raw["work_period_input"]["staff_institutions"] = {
        staff_id: str(INSTITUTION) for staff_id in raw["academic_staff_references"]}
    evidence = [InputEvidence(path=(key,), owner=expected_owner(key), institution_id=INSTITUTION,
        status="CONFIRMED", provenance=dict(source="synthetic-export", reference="synthetic:12a-fixture"),
        confirmation_reference="synthetic:example-only", effective_from=calendar.week_start,
        effective_until=calendar.week_end, synthetic=True) for key in raw]
    return SchedulingSubmission(inputs=raw, evidence=evidence)
