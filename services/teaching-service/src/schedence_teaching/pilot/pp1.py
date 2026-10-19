"""One PP1 test batch across semesters/streams; the native solver stays unchanged."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from pydantic import Field, model_serializer, model_validator

from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.preferences import LecturerPreferenceInput, PreferenceCalendar, PreferenceScoringPolicy, prepare_lecturer_preferences
from schedence_teaching.preferences.product_settings import (
    LecturerPreferenceSettings, apply_hard_unavailability, build_lecturer_preferences, hard_availability_diagnostics)
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.schemas import ModuleOffering, SessionType, VenueType
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.solver import (
    SolverConfig, StaffSchedulingInput, StudentLunchEnforcementConfig, PreferenceOptimizationConfig,
    WorkPeriodSolverConfig, venues_from_import,
)
from schedence_teaching.workload import WeeklyWorkloadInput, WorkloadBand, WorkPeriodInput, confirmed_campus_policy, prepare_work_periods

from .academic import OfferingSourceBinding, NonLecturePattern, build_specialization_teaching_input, extract_academic_catalog


class PP1Configuration(InputContract):
    output_label: Literal["PP1_TEST_CALENDAR", "PP1_EXPERIMENTAL_CALENDAR"]
    configuration_reference: RequiredText
    calendar: PreferenceCalendar
    solver_config: SolverConfig
    nonlecture_pattern: Literal["UNSPLIT_WEEKLY_CONTACT"]
    session_audiences: dict[SessionType, Literal["MAIN", "LAB_SUBGROUP"]]
    venue_types: dict[SessionType, set[VenueType]]
    staff_calendar_mode: Literal["PP1_TEST_WINDOWS_OR_SUPPLIED_OVERRIDE"]
    workload_band_by_source_position: dict[RequiredText, WorkloadBand]
    weekend_preference_values: dict[RequiredText, set[str]]
    test_soft_preference_weight: int = Field(strict=True, ge=0)
    max_model_variables: int = Field(strict=True, gt=0)
    max_model_constraints: int = Field(strict=True, gt=0)
    staff_input: StaffSchedulingInput | None = None
    protected_periods: WorkPeriodInput | None = None
    protected_period_config: WorkPeriodSolverConfig | None = None
    # P2.5: one institution's interpretation of its stored day-part preferences; omitted when unset.
    lecturer_preference_settings: LecturerPreferenceSettings | None = None

    @model_serializer(mode="wrap")
    def historical_payload(self, handler):
        result = handler(self)
        if self.lecturer_preference_settings is None:
            result.pop("lecturer_preference_settings", None)  # Existing configurations stay byte-identical.
        return result

    @model_validator(mode="after")
    def explicit_scope(self):
        if not self.calendar.synthetic_data:
            raise ValueError("The PP1 calendar is a testing reference, not an actual university term calendar.")
        if set(self.session_audiences) != set(SessionType) or set(self.venue_types) != set(SessionType) or not all(self.venue_types.values()):
            raise ValueError("Configure audiences and venue types for each contact type.")
        if set(self.calendar.day_dates) != {row.day for row in self.solver_config.days}:
            raise ValueError("Every configured teaching day needs exactly one date.")
        from schedence_teaching.preferences.scoring import _calendar
        _calendar(self.solver_config, self.calendar, self.staff_input, None)
        if (self.protected_periods is None) != (self.protected_period_config is None):
            raise ValueError("Protected-period input and configuration must be supplied together.")
        return self


@dataclass(frozen=True)
class PP1Batch:
    prepared: object
    config: SolverConfig
    solver_inputs: dict
    plan: object
    group_specializations: dict[str, str]
    source_offerings: dict[str, dict]
    configuration: PP1Configuration
    metadata: dict


def prepare_pp1(allocations, academic, venue_source, configuration: PP1Configuration, *, academic_pathways=None) -> PP1Batch:
    """Source-row offering IDs are explicitly local test IDs, never ADS identifiers."""
    configuration = PP1Configuration.model_validate(configuration.model_dump())
    if not allocations.valid or not academic.valid or not venue_source.valid:
        raise ValueError("All original import checks must pass before PP1 preparation.")
    plan = allocations.groups()
    catalog = extract_academic_catalog(academic)
    grouping = None
    if academic_pathways is not None:
        from .academic_pathways import prepare_academic_grouping
        grouping = prepare_academic_grouping(allocations, catalog, academic_pathways)
        plan = grouping.plan
    applicability = grouping.group_source_specializations if grouping else {
        identity: (spec,) for identity, spec in plan.specializations.items()}
    known_specializations = {code for module in catalog.modules for code in module.specializations}
    if {spec for specs in applicability.values() for spec in specs} - known_specializations:
        raise ValueError("Allocation specialization code has no source module mapping; no specialization demand is silently omitted.")
    bindings, patterns, source_offerings = [], [], {}
    for module in catalog.modules:
        for stream in sorted({row.stream for row in plan.groups}, key=str):
            fields = module.source.contract_fields
            if not any((row.academic_year, row.semester, row.stream) == (fields["academic_year"], fields["semester"], stream)
                       and set(applicability[row.group_id]) <= set(module.specializations) for row in plan.groups):
                continue
            identity = f"PP1:{quote(module.source.location.diagnostic_key, safe='')}:{stream.value}"
            supplied = {key: value for key, value in fields.items() if key not in {"offering_id", "stream"}}
            # If an actual source ID/stream ever exists, it must be used explicitly
            # rather than silently broadening it to every allocated stream.
            if "offering_id" in fields or "stream" in fields:
                raise ValueError("Use an explicit reviewed offering binding when the source supplies offering IDs/streams.")
            bindings.append(OfferingSourceBinding(source=module.source.location,
                offering=ModuleOffering(**supplied, offering_id=identity, stream=stream)))
            source_offerings[identity] = dict(workbook=module.source.location.workbook, worksheet=module.source.location.worksheet,
                row=module.source.location.row, identifier_status="LOCAL_PP1_SOURCE_ROW_REFERENCE_NOT_DATABASE_ID")
            for kind, minutes in module.weekly_minutes.items():
                if kind != SessionType.LECTURE and minutes:
                    patterns.append(NonLecturePattern(offering_id=identity, session_type=kind, duration_minutes=minutes,
                        sessions_per_week=1, source_reference=configuration.configuration_reference + ":unsplit-contact-column"))
    audiences = {kind: tuple(row.group_id for row in plan.groups if
        (row.parent_group_id is None) == (audience == "MAIN")) for kind, audience in configuration.session_audiences.items()}
    teaching = build_specialization_teaching_input(academic, catalog, offerings=bindings, groups=list(plan.groups),
        group_specializations=plan.specializations, session_audiences=audiences, nonlecture_patterns=patterns,
        group_source_specializations=applicability if grouping else None)
    prepared = prepare_timetable_input(teaching)
    windows = {day.day: [window.model_dump() for spans in day.windows.values() for window in spans] for day in configuration.solver_config.days}
    venues = venues_from_import(venue_source, calendars=[dict(venue_id=venue.venue_id,
        available_windows=windows, blocked_periods=[]) for venue in venue_source.venues],
        requirements=[dict(requirement_id=row.requirement_id, allowed_venue_types=configuration.venue_types[row.session_type])
                      for row in teaching.session_requirements])
    calendar = configuration.calendar
    profiles = [dict(staff_id=row.staff_id, workload_band=configuration.workload_band_by_source_position[row.position],
        evidence_reference=configuration.configuration_reference + ":explicit-source-position-band-map")
        for row in teaching.staff_members if row.position in configuration.workload_band_by_source_position]
    workload = WeeklyWorkloadInput(week_reference=calendar.week_reference, policy=confirmed_campus_policy(), profiles=profiles)
    staff = configuration.staff_input or StaffSchedulingInput(workload_input=workload, day_dates=calendar.day_dates,
        policy_effective_from=calendar.week_start, policy_effective_until=calendar.week_end,
        allow_unclassified_staff_for_testing=len(profiles) < len(teaching.staff_members),
        availability=[dict(staff_id=row.staff_id, allowed_teaching_days=set(windows), available_windows=windows,
            blocked_periods=[], effective_from=calendar.week_start, effective_until=calendar.week_end)
            for row in teaching.staff_members])
    settings, preference_build = configuration.lecturer_preference_settings, None
    if settings is not None:
        preference_build = build_lecturer_preferences(academic, settings, calendar,
            configured_streams={stream for day in configuration.solver_config.days for stream in day.windows})
        staff = apply_hard_unavailability(staff, preference_build.blocked_periods)
    provenance = dict(source="user-configured-PP1-test-calendar", reference=configuration.configuration_reference)
    lunch = confirmed_student_lunch_policy(policy_reference="confirmed-year-specific-student-lunch", week_reference=calendar.week_reference,
        provenance=provenance, applicable_dates=set(calendar.day_dates.values()),
        applicable_streams={stream for day in configuration.solver_config.days for stream in day.windows},
        applicability_reference=configuration.configuration_reference)
    contexts = dict(venue_input=venues, staff_input=staff, student_lunch_policy=lunch, student_lunch_calendar=calendar,
        student_lunch_config=StudentLunchEnforcementConfig(applicability_confirmation="CONFIRMED",
            applicability_confirmation_reference=configuration.configuration_reference,
            max_model_variables=configuration.max_model_variables, max_model_constraints=configuration.max_model_constraints))
    if configuration.protected_periods is not None:
        contexts.update(work_periods=prepare_work_periods(teaching, staff.workload_input, configuration.protected_periods),
                        work_period_config=configuration.protected_period_config)
    mapping = map_lecturer_preferences(academic, LecturerPreferenceMapping(layout="department-aligned-preferences-v1",
        source_reference=academic.source_confirmation_reference or configuration.configuration_reference,
        scope=dict(week_reference=calendar.week_reference),
        weekend_pattern_values=configuration.weekend_preference_values,
        weekend_mapping_confirmation_reference=configuration.configuration_reference))
    # Numerical day-part windows are absent; preserve them as diagnostics instead
    # of inventing MORNING/AFTERNOON cutoffs or treating preferences as hard blocks.
    day_preferences = [row for row in mapping.preferences if row.category.value in {"WORKING_DAY", "WEEKEND_DAY"}]
    soft_preferences_applicable = None
    if preference_build is not None:
        from schedence_teaching.solver import SolverInputError
        from schedence_teaching.solver.preference_inputs import NO_APPLICABLE_PREFERENCES, checked_preference_inputs
        soft = dict(preference_input=prepare_lecturer_preferences(teaching, preference_build.preferences),
            preference_calendar=calendar, preference_policy=preference_build.scoring_policy,
            preference_config=PreferenceOptimizationConfig(priority_policy_reference=settings.settings_reference,
                calendar_semantics_reference=settings.settings_reference,
                day_streams={row.day: set(row.windows) for row in configuration.solver_config.days},
                unscoped_preferences_apply_to_all_streams=preference_build.unscoped_preferences_apply_to_all_streams,
                max_model_variables=configuration.max_model_variables, max_model_constraints=configuration.max_model_constraints))
        try:  # The solver's own applicability check; hard blocks above apply either way.
            checked_preference_inputs(prepared, configuration.solver_config, soft["preference_input"], calendar,
                soft["preference_policy"], soft["preference_config"], staff_input=staff)
            soft_preferences_applicable = True
            contexts.update(soft)
        except SolverInputError as error:
            if str(error) != NO_APPLICABLE_PREFERENCES:
                raise
            soft_preferences_applicable = False
    elif day_preferences:
        contexts.update(preference_input=prepare_lecturer_preferences(teaching, LecturerPreferenceInput(preferences=day_preferences)),
            preference_calendar=calendar, preference_policy=PreferenceScoringPolicy(
                policy_reference=configuration.configuration_reference + ":equal-test-soft-weights", weight_mode="SYNTHETIC_DEFAULTS",
                synthetic_default_weight=configuration.test_soft_preference_weight),
            preference_config=PreferenceOptimizationConfig(priority_policy_reference=configuration.configuration_reference,
                calendar_semantics_reference=configuration.configuration_reference,
                day_streams={row.day: set(row.windows) for row in configuration.solver_config.days},
                unscoped_preferences_apply_to_all_streams=True,
                max_model_variables=configuration.max_model_variables, max_model_constraints=configuration.max_model_constraints))
    known = {row["staff_id"] for row in profiles}
    metadata = dict(output_label=configuration.output_label, source_academic_status="USER_CONFIRMED_UNIVERSITY_SOURCED",
        source_confirmation_reference=academic.source_confirmation_reference,
        allocation=allocations.summary(), groups=plan.summary(), source_module_rows=len(catalog.modules),
        scoped_local_offering_references=len(bindings), source_teaching_team_entries=len(academic.source_assignments),
        mapped_teaching_team_entries=len(teaching.teaching_team_members), session_requirements=len(teaching.session_requirements),
        venue_inventory_count=len(venues.venues), unresolved_booking_venue_count=len(venues.unresolved_venue_ids),
        bookable_venue_count=len(venues.venues)-len(venues.unresolved_venue_ids),
        specialization_start_policy_required=False, module_applicability_used_directly=True,
        session_occurrences=len(prepared.sessions), session_type_counts=dict(Counter(row.session_type.value for row in prepared.sessions)),
        group_count_without_mapped_requirements=len({row.group_id for row in teaching.student_groups}
            - {row.group_id for row in teaching.session_requirements}),
        holiday_optimization=False, shared_lecture_combining=False, cross_specialization_overlap_optimization=False,
        calendar=calendar.model_dump(mode="json"), solver_config=configuration.solver_config.model_dump(mode="json"),
        staff_availability_scope="SUPPLIED_OVERRIDE" if configuration.staff_input else "PP1_TEST_WINDOWS_ONLY_NOT_ACTUAL_STAFF_AVAILABILITY",
        workload_profile_count=len(profiles), unresolved_workload_classification_count=len(teaching.staff_members)-len(known),
        administrative_allocation_is_not_completed_work=True,
        protected_period_scope="SUPPLIED_RULES_ENFORCED" if configuration.protected_periods else "NO_TIMED_PROTECTED_PERIOD_INPUT_SUPPLIED",
        unresolved_source_availability_daypart_count=sum(row.code == "availability_scope_required" for row in mapping.issues),
        soft_day_preference_count=len(day_preferences), deferred_time_of_day_preference_count=sum(row.category.value == "TIME_OF_DAY" for row in mapping.preferences),
        soft_weights_are_testing_parameters=True, confirmed_campus_policy=confirmed_campus_policy().model_dump(mode="json"),
        limitations=["Specialization counts are approved proportional test estimates, not exact enrollment.",
            "Local PP1 offering references are not authoritative database identifiers.",
            "Unless supplied as overrides, staff windows are PP1 test availability, not individual campus availability evidence.",
            "No protected calendar blocks or completed administrative work are invented from weekly allocations.",
            "Underlying academic ranks of appointment-only titles remain unresolved; source titles are preserved.",
            "Source day-part availability/preferences require numerical boundaries; no hard blocks or clock preferences are inferred.",
            "Cross-specialization overlap and shared lectures are excluded from PP1; true student membership is not asserted."])
    if preference_build is not None:
        metadata.update(lecturer_preferences={**preference_build.summary(), "soft_preferences_applicable": soft_preferences_applicable},
            hard_availability_diagnostics=list(hard_availability_diagnostics(prepared, configuration.solver_config, staff)))
    if grouping:
        metadata.update(academic_grouping=grouping.report, group_source_specializations=applicability,
            group_count_without_mapped_requirements=len({row.group_id for row in teaching.student_groups}
                - {row.group_id for row in teaching.session_requirements}),
            academic_track_labels_are_not_official_enrollment_starts=True)
    return PP1Batch(prepared, configuration.solver_config, contexts, plan, plan.specializations, source_offerings, configuration, metadata)


def attendance_capacity_diagnostics(batch: PP1Batch) -> list[dict]:
    """A necessary time-capacity bound for each leaf, including ancestor teaching."""
    hierarchy = batch.prepared.hierarchy
    groups = {row.group_id: row for row in batch.plan.groups}
    minutes = defaultdict(int)
    for occurrence in batch.prepared.sessions:
        minutes[occurrence.group_id] += occurrence.duration_minutes
    lunches = {row.academic_year: row.window for row in batch.solver_inputs["student_lunch_policy"].year_periods}
    failures = []
    for identity, group in groups.items():
        if hierarchy.descendants(identity):
            continue
        demand, current = 0, identity
        while current is not None:
            demand += minutes[current]
            current = hierarchy.parents[current]
        available = 0
        for day in batch.config.days:
            intervals = sorted((row.start_minute, row.end_minute) for row in day.windows.get(group.stream, ()))
            merged = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(end, merged[-1][1])
                else:
                    merged.append([start, end])
            lunch = lunches[group.academic_year]
            available += sum(end-start-max(0, min(end, lunch.end_minute)-max(start, lunch.start_minute)) for start, end in merged)
        if demand > available:
            failures.append(dict(code="STUDENT_WEEKLY_TIME_CAPACITY", group_id=identity,
                specialization=batch.group_specializations[identity], academic_year=group.academic_year,
                semester=group.semester, stream=group.stream.value, required_minutes=demand,
                available_minutes=available, shortfall_minutes=demand-available))
    return failures
