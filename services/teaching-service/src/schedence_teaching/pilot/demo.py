"""Explicit fictional scheduling context around accepted reference data.

No real offering identifiers, specialization assignments, population proofs,
calendars or session hours are inferred by this demonstration builder.
"""

from datetime import date
from itertools import combinations
from uuid import UUID

from schedence_teaching.consecutive import ConsecutivePolicy
from schedence_teaching.fairness import FairnessPolicy
from schedence_teaching.integration import InputEvidence, NormalizedSchedulingInput, SchedulingSubmission
from schedence_teaching.integration.contracts import expected_owner
from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.preferences import LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy
from schedence_teaching.preparation import build_group_hierarchy
from schedence_teaching.schemas import GroupCombinationInput, StaffType, VenueType
from schedence_teaching.solver import (
    CombinationSolverConfig, ConsecutiveOptimizationConfig, FairnessOptimizationConfig,
    PreferenceOptimizationConfig, SolverConfig, StaffSchedulingInput,
    StudentLunchEnforcementConfig, WorkPeriodSolverConfig, venues_from_import,
)
from schedence_teaching.workload import WeeklyWorkloadInput, WorkPeriodInput, confirmed_campus_policy

from .students import PlannedGroups


# Exact supported reference titles only. Appointment titles/unknown ranks are
# deliberately excluded from this demo selection; no academic rank is invented.
DEMO_BANDS = {"Professor": "PROFESSOR", "Assistant Professor": "ASSISTANT_PROFESSOR",
    "Senior Lecturer": "SENIOR_LECTURER", "Lecturer": "LECTURER",
    "Assistant Lecturer": "ASSISTANT_LECTURER", "Instructor": "INSTRUCTOR", "Dr.": "DR"}
DEMO_INSTITUTION = UUID("00000000-0000-4000-8000-000000000012")
DEMO_ASSUMPTIONS = (
    "SYNTHETIC_DEMO: source counts are accepted development inputs; the teaching plan is fictional.",
    "Six main groups are explicitly simulated as two fictional specializations, three main groups each; actual specialization is unknown.",
    "Synthetic offering IDs and shared-content scope are demonstration identifiers, never authoritative database IDs.",
    "Supplied lecture hours use one continuous weekly lecture (2 hours = 120 minutes; 1 hour = 60 minutes). Synthetic fixtures without source hours explicitly use 60 minutes. Labs remain an explicitly synthetic 90-minute weekly assumption, never a conversion of source lab hours.",
    "Population assertions describe a fictional disjoint partition; aggregate counts do not verify actual individual membership.",
    "2026-10-19 to 2026-10-25 with Monday/Tuesday/Wednesday 09:00-16:00 windows is a synthetic calendar, not campus opening hours.",
    "All included staff/venues use explicit simulated availability and blocks; only one retained academic and one other retained team member per demo offering have simulated teaching windows. Other source team members remain eligible but have no simulated availability. Daily 240 and weekly 600 teaching-minute caps are synthetic.",
    "Source staff IDs, names, rank text, candidate team roles and venue inventory attributes remain unchanged; DEMO band applicability is not an official HR resolution.",
    "No actual lecturer preferences, protected administrative/research slots or completed work are supplied. One simulated soft avoid-Wednesday-15:00-16:00 preference is used solely for the full-profile demo; empty protection records do not establish real-world absence.",
    "Preference/fairness/consecutive applicability, cohorts, weights and approvals are explicitly synthetic; no hard break is invented.",
    "Fixed Year 1 lunch 12:30-13:30 is enforced on all three synthetic teaching dates for WEEKDAY students only; lecturer lunch is not inferred.",
    "Combination is optional and limited by explicit fictional specialization/content/eligibility/disjointness plus the actual selected venue capacity; 240 is only a planning upper bound.",
    "Synthetic institution and ADS UUID crosswalk values are demonstration-only; no campus identifiers or records are created.",
)


def build_demo_submission(plan: PlannedGroups, academic_source, venue_source, *, time_limit=15.0):
    if not academic_source.valid or not venue_source.valid:
        raise ValueError("Demo requires valid existing academic and venue imports.")
    main = [row for row in plan.groups if row.parent_group_id is None]
    if len(main) != 6 or any((row.academic_year, row.semester, row.stream.value) != (1, 1, "WEEKDAY") for row in plan.groups):
        raise ValueError("This explicit first demo requires Year 1 semester 1 weekday, six main groups and their lab subgroups.")
    staff_by_id = {row.staff_id: row for row in academic_source.staff_members}
    selected = []
    for module in academic_source.source_modules:
        if (module.contract_fields.get("academic_year"), module.contract_fields.get("semester")) != (1, 1):
            continue
        team = [row for row in academic_source.source_assignments if row.location == module.location]
        if team and all(row.source_staff_id in staff_by_id and staff_by_id[row.source_staff_id].position in DEMO_BANDS for row in team):
            selected.append((module, team))
        if len(selected) == 2:
            break
    if len(selected) != 2:
        raise ValueError("Two retained Year 1 semester 1 modules with explicit supported staff classification are needed; no team is altered to make the demo fit.")
    first, last = date(2026, 10, 19), date(2026, 10, 25)
    dates = {"MONDAY": first, "TUESDAY": date(2026, 10, 20), "WEDNESDAY": date(2026, 10, 21)}
    week = "SYNTHETIC-DEMO-PILOT-WEEK"
    provenance = dict(source="explicit-synthetic-pilot-assumptions", reference="synthetic:step12a2-only")
    window = dict(start_minute=540, end_minute=960)
    config = SolverConfig(days=[dict(day=day, windows={"WEEKDAY": [window]}) for day in dates],
        granularity_minutes=30, max_time_seconds=time_limit, random_seed=7)
    calendar = PreferenceCalendar(week_reference=week, week_start=first, week_end=last, day_dates=dates, synthetic_data=True)
    offerings, teams, requirements, specializations = [], [], [], {}
    main_specialization = {row.group_id: f"SYNTHETIC-SPECIALIZATION-{'A' if index < 3 else 'B'}" for index, row in enumerate(main)}
    for row in plan.groups:
        specializations[row.group_id] = main_specialization[row.parent_group_id or row.group_id]
    for index, (module, members) in enumerate(selected):
        source_lecture_hours = module.source_values.get("Lecture_Hours_Week")
        if source_lecture_hours is None:
            lecture_duration = 60  # Explicit synthetic-fixture demand only.
        elif type(source_lecture_hours) in {int, float} and source_lecture_hours in {1, 2}:
            lecture_duration = int(source_lecture_hours * 60)
        else:
            raise ValueError("Demo source lecture must have a confirmed positive one- or two-hour pattern; no split or replacement is inferred.")
        offering = f"SYNTHETIC-DEMO-OFFERING-{'A' if index == 0 else 'B'}"
        specialization = f"SYNTHETIC-SPECIALIZATION-{'A' if index == 0 else 'B'}"
        offerings.append(dict(**{key: module.contract_fields[key] for key in (
            "module_code", "module_name", "academic_year", "semester", "degree_type")},
            offering_id=offering, stream="WEEKDAY", specialization=specialization))
        teams.extend(dict(offering_id=offering, staff_id=member.source_staff_id, role=member.role) for member in members)
        for group in plan.groups:
            if specializations[group.group_id] == specialization:
                lecture = group.parent_group_id is None
                requirements.append(dict(requirement_id=f"SYNTHETIC-REQ:{group.group_id}", offering_id=offering,
                    group_id=group.group_id, session_type="LECTURE" if lecture else "LAB",
                    duration_minutes=lecture_duration if lecture else 90, sessions_per_week=1))
    staff_ids = {row["staff_id"] for row in teams}
    staff = [staff_by_id[identity] for identity in sorted(staff_ids)]
    active = set()
    for _, members in selected:
        identities = [row.source_staff_id for row in members]
        academic = next(identity for identity in identities if staff_by_id[identity].staff_type is StaffType.ACADEMIC)
        other = next((identity for identity in identities if staff_by_id[identity].staff_type is StaffType.INSTRUCTOR),
                     next(identity for identity in identities if identity != academic))
        active.update((academic, other))
    hierarchy = build_group_hierarchy(plan.groups)
    population = [dict(group_ids=(first_id, second_id), relationship="DISJOINT",
        evidence_reference="synthetic:fictional-cohort-partition-not-individual-proof")
        for first_id, second_id in combinations(sorted(hierarchy.parents), 2) if not hierarchy.conflicts(first_id, second_id)]
    combination = GroupCombinationInput(teaching_input=dict(staff_members=staff, module_offerings=offerings,
        teaching_team_members=teams, student_groups=plan.groups, session_requirements=requirements),
        group_specializations=specializations, policy=dict(allow_combination=True, max_groups=2,
            max_combined_students=plan.sizing.combined_planning_maximum), population_evidence=population)
    imported = venues_from_import(venue_source)
    bookable = [row for row in imported.venues if row.venue_id not in imported.unresolved_venue_ids]
    halls = sorted((row for row in bookable if row.venue_type is VenueType.LECTURE_HALL),
                   key=lambda row: (-row.capacity, row.venue_id))[:1]
    halls += sorted((row for row in bookable if row.venue_type is VenueType.LECTURE_HALL and row.capacity == 120),
                    key=lambda row: row.venue_id)[:1]
    labs = sorted((row for row in bookable if row.venue_type is VenueType.COMPUTER_LAB and
                   row.capacity >= max(item.student_count for item in plan.groups if item.parent_group_id)),
                  key=lambda row: row.venue_id)[:2]
    if not halls or not labs:
        raise ValueError("Demo needs actual capacity-suitable lecture and computer-lab inventory; no capacities are changed.")
    venues = venues_from_import(venue_source,
        calendars=[dict(venue_id=row.venue_id, available_windows={day: [window] for day in dates},
            blocked_periods=[dict(day="WEDNESDAY", start_minute=930, end_minute=960)]) for row in imported.venues],
        requirements=[dict(requirement_id=row["requirement_id"], eligible_venue_ids={room.venue_id for room in
            (halls if row["session_type"] == "LECTURE" else labs)},
            allowed_venue_types={"Lecture Hall"} if row["session_type"] == "LECTURE" else {"Computer Lab"}) for row in requirements])
    workload = WeeklyWorkloadInput(week_reference=week, policy=confirmed_campus_policy(), profiles=[
        dict(staff_id=row.staff_id, workload_band=DEMO_BANDS[row.position], evidence_reference="synthetic:demo-band-applicability") for row in staff])
    staff_input = StaffSchedulingInput(workload_input=workload, day_dates=dates, policy_effective_from=first,
        policy_effective_until=last, availability=[dict(staff_id=row.staff_id, allowed_teaching_days=set(dates) if row.staff_id in active else set(),
            available_windows={day: [window] for day in dates} if row.staff_id in active else {},
            blocked_periods=[dict(day="MONDAY", start_minute=630, end_minute=660)] if row.staff_id in active else [],
            effective_from=first, effective_until=last, daily_teaching_max_minutes=240, weekly_teaching_max_minutes=600) for row in staff])
    periods = WorkPeriodInput(week_reference=week, week_start=first, week_end=last,
        staff_institutions={row.staff_id: str(DEMO_INSTITUTION) for row in staff})
    cohorts = [dict(group_id=f"SYNTHETIC-{kind.value}-COHORT", staff_ids=[row.staff_id for row in staff if row.staff_type is kind],
        reason="Explicit fictional comparison cohort using retained source staff type") for kind in StaffType
        if any(row.staff_type is kind for row in staff)]
    values = NormalizedSchedulingInput(institution_id=DEMO_INSTITUTION, mode="SYNTHETIC", academic_term_reference="SYNTHETIC-TERM",
        semester=1, demand_complete=True, academic_staff_references={row.staff_id: UUID(int=1000+index) for index, row in enumerate(staff)},
        calendar=calendar, solver_config=config, combination_input=combination, combination_config=CombinationSolverConfig(),
        venue_input=venues, staff_input=staff_input, work_period_input=periods,
        work_period_config=WorkPeriodSolverConfig(day_weekdays={day: day for day in dates}, day_streams={day: {"WEEKDAY"} for day in dates}),
        preference_input=LecturerPreferenceInput(preferences=[dict(preference_id="SYNTHETIC-DEMO-PREFERENCE",
            staff_id=next(row.staff_id for row in staff if row.staff_type is StaffType.ACADEMIC and row.staff_id in active),
            category="TEACHING_WINDOW", direction="AVOID", days={"WEDNESDAY"}, stream="WEEKDAY",
            time_window=dict(start_minute=900, end_minute=960), penalty_weight=1,
            weight_reference="synthetic:example-weight", confirmation_status="CONFIRMED",
            scope=dict(week_reference=week), provenance=provenance)]),
        preference_policy=PreferenceScoringPolicy(policy_reference="synthetic:explicit-soft-demo-preference", weight_mode="CONFIRMED_WEIGHTS"),
        preference_config=PreferenceOptimizationConfig(priority_policy_reference="synthetic:existing-objective-order",
            calendar_semantics_reference="synthetic:three-day-calendar", day_streams={day: {"WEEKDAY"} for day in dates},
            unscoped_preferences_apply_to_all_streams=False),
        fairness_policy=FairnessPolicy(policy_reference="synthetic:fairness", week_reference=week, provenance=provenance,
            comparison_scope="EXPLICIT_GROUPS", comparison_groups=cohorts),
        fairness_config=FairnessOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_FAIRNESS",
            priority_policy_reference="synthetic:fairness-priority", cohort_rule="INPUT_ELIGIBILITY_AND_FIXED_CALENDAR", deviation_reference="TEACHING_TARGET"),
        consecutive_policy=ConsecutivePolicy(policy_reference="synthetic:consecutive", week_reference=week, provenance=provenance,
            effective_from=first, effective_until=last, score_lecturers=True, score_student_groups=True,
            soft_criteria={"BACK_TO_BACK"}, back_to_back_penalty=1),
        consecutive_config=ConsecutiveOptimizationConfig(priority="EXISTING_OBJECTIVES_THEN_CONSECUTIVE",
            priority_policy_reference="synthetic:consecutive-last"),
        student_lunch_policy=confirmed_student_lunch_policy(policy_reference="synthetic:fixed-lunch", week_reference=week,
            provenance=provenance, applicable_dates=set(dates.values()), applicable_streams={"WEEKDAY"}, applicability_reference="synthetic:explicit-scope"),
        student_lunch_config=StudentLunchEnforcementConfig(applicability_confirmation="CONFIRMED", applicability_confirmation_reference="synthetic:example-only"))
    raw = values.model_dump()
    return SchedulingSubmission(inputs=raw, evidence=[InputEvidence(path=(key,), owner=expected_owner(key),
        institution_id=DEMO_INSTITUTION, status="CONFIRMED", confirmation_reference="synthetic:example-only",
        provenance=provenance, effective_from=first, effective_until=last, synthetic=True) for key in raw])
