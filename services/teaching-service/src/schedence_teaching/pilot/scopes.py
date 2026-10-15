"""Select complete cohort scopes without dropping any of their requirements."""

from dataclasses import replace

from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.preferences import prepare_lecturer_preferences
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver.preference_inputs import checked_preference_inputs
from schedence_teaching.solver import SolverInputError

from .demand_audit import group_scope
from .students import PlannedGroups


def select_complete_scope(batch, scopes, *, max_time_seconds=None, retain_applicable_catalog=False):
    """Each key is (study year, semester, stream value, specialization).

    All original main/lab groups, requirements, and eligible staff for a selected
    cohort survive unchanged. Multiple keys are solved together in ONE model.
    Other cohorts are explicitly outside the requested scope, never scheduled
    separately and then claimed to form one globally valid timetable.
    """
    scopes = set(scopes)
    known = {group_scope(batch, row) for row in batch.plan.groups}
    if not scopes or scopes - known:
        raise ValueError("Select nonempty known complete academic scopes.")
    groups = tuple(row for row in batch.plan.groups if group_scope(batch, row) in scopes)
    group_ids = {row.group_id for row in groups}
    raw = batch.prepared.input_snapshot.model_dump()
    requirements = [row for row in raw["session_requirements"] if row["group_id"] in group_ids]
    if not requirements:
        raise ValueError("Selected scopes have no teaching requirements.")
    offering_ids = {row["offering_id"] for row in requirements}
    if retain_applicable_catalog:
        from schedence_teaching.schemas.specializations import specialization_labels
        offering_ids.update(row.offering_id for row in batch.prepared.input_snapshot.module_offerings
            if any((group.academic_year, group.semester, group.stream) == (row.academic_year, row.semester, row.stream)
                and batch.group_specializations[group.group_id] in specialization_labels(row.specialization) for group in groups))
    raw.update(student_groups=[row for row in raw["student_groups"] if row["group_id"] in group_ids],
        session_requirements=requirements,
        module_offerings=[row for row in raw["module_offerings"] if row["offering_id"] in offering_ids],
        teaching_team_members=[row for row in raw["teaching_team_members"] if row["offering_id"] in offering_ids])
    teaching = TeachingTimetableInput.model_validate(raw)
    prepared = prepare_timetable_input(teaching)
    expected = tuple(row for row in batch.prepared.sessions if row.group_id in group_ids)
    if prepared.sessions != expected:
        raise ValueError("Projection must preserve every original selected occurrence exactly.")
    cohorts = {batch.plan.cohort_by_group[identity] for identity in group_ids}
    specializations = {identity: batch.group_specializations[identity] for identity in group_ids}
    plan = replace(batch.plan, cohorts=tuple(row for row in batch.plan.cohorts if row.cohort_id in cohorts), groups=groups,
        cohort_by_group={identity: batch.plan.cohort_by_group[identity] for identity in group_ids}, specializations=specializations)
    contexts = dict(batch.solver_inputs)
    venues = contexts["venue_input"].model_copy(deep=True)
    requirement_ids = {row.requirement_id for row in teaching.session_requirements}
    venues.requirements = [row for row in venues.requirements if row.requirement_id in requirement_ids]
    contexts["venue_input"] = venues
    if "work_periods" in contexts:
        from schedence_teaching.workload import prepare_work_periods
        contexts["work_periods"] = prepare_work_periods(teaching, contexts["staff_input"].workload_input,
            contexts["work_periods"].input_snapshot)
    configuration = batch.configuration.model_copy(deep=True)
    configuration.solver_config.attendance_memberships = tuple(row for row in configuration.solver_config.attendance_memberships if row.group_id in group_ids)
    configuration.solver_config.attendance_required_group_ids &= group_ids
    if max_time_seconds is not None:
        configuration.solver_config.max_time_seconds = float(max_time_seconds)
        # Revalidate instead of letting model_copy bypass type/range validation.
        configuration = type(configuration).model_validate(configuration.model_dump())
    if "preference_input" in contexts:
        contexts["preference_input"] = prepare_lecturer_preferences(teaching, contexts["preference_input"].input_snapshot)
        try:
            checked_preference_inputs(prepared, configuration.solver_config, contexts["preference_input"],
                contexts["preference_calendar"], contexts["preference_policy"], contexts["preference_config"],
                staff_input=contexts["staff_input"], work_periods=contexts.get("work_periods"))
            preference_scope = "ALL_APPLICABLE_ORIGINAL_CRITERIA_PRESERVED"
        except SolverInputError as error:
            if str(error) != "Preference optimization requires at least one applicable eligible-staff criterion.":
                raise
            for key in ("preference_input", "preference_calendar", "preference_policy", "preference_config"):
                del contexts[key]
            preference_scope = "NO_APPLICABLE_ELIGIBLE_STAFF_CRITERIA_IN_SELECTED_SCOPE"
    else:
        preference_scope = "NO_SOURCE_DAY_PREFERENCES"
    metadata = dict(batch.metadata, groups=plan.summary(), session_requirements=len(requirements),
        scoped_local_offering_references=len(offering_ids), mapped_teaching_team_entries=len(teaching.teaching_team_members),
        session_occurrences=len(prepared.sessions), original_global_occurrences=len(batch.prepared.sessions),
        selected_complete_scopes=[dict(academic_year=year, semester=semester, stream=stream, specialization=spec)
                                  for year, semester, stream, spec in sorted(scopes)],
        out_of_scope_occurrences=len(batch.prepared.sessions)-len(prepared.sessions),
        scope_completeness="ALL_ORIGINAL_REQUIREMENTS_FOR_EVERY_SELECTED_GROUP",
        separate_scope_runs_are_not_one_combined_timetable=True,
        scoped_preference_handling=preference_scope,
        solver_config=configuration.solver_config.model_dump(mode="json"))
    metadata["group_count_without_mapped_requirements"] = len(group_ids - {row.group_id for row in teaching.session_requirements})
    from collections import Counter
    metadata["session_type_counts"] = dict(Counter(row.session_type.value for row in prepared.sessions))
    return replace(batch, prepared=prepared, config=configuration.solver_config, configuration=configuration,
        solver_inputs=contexts, plan=plan, group_specializations=specializations,
        source_offerings={key: value for key, value in batch.source_offerings.items() if key in offering_ids}, metadata=metadata)
