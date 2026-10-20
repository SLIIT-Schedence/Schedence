"""P2.5: stored day-part preferences under per-institution settings, end to end through PP1 preparation.

Rows use the stored column layout. Boundaries and weights are per-test institution settings, not defaults.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from schedence_teaching.excel_import import VenueImportResult
from schedence_teaching.excel_import.staging import (SourceLocation, SourceModuleRecord, SourceRecord, SourceTeamAssignment,
                                                     WideImportResult)
from schedence_teaching.pilot.pp1 import PP1Configuration, prepare_pp1
from schedence_teaching.pilot.students import CohortCount, GroupSizingPolicy
from schedence_teaching.pilot.allocations import SpecializationAllocations
from schedence_teaching.preferences.product_settings import (LecturerPreferenceSettings, apply_hard_unavailability,
                                                            build_lecturer_preferences, preference_conflicts)
from schedence_teaching.schemas import StaffMember, TeachingRole, Venue
from schedence_teaching.solver import SolverStatus, solve_timetable, validate_timetable

from test_pp1_allocations import configuration

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS = DAYS[:5]
INSTITUTION_A = dict(settings_reference="SYNTHETIC-INSTITUTION-A:lecturer-preferences:v1",
    time_of_day_windows={"MORNING": dict(start_minute=510, end_minute=750), "AFTERNOON": dict(start_minute=750, end_minute=1050)},
    weights={"PREFERRED_DAY_PART": 3, "NOT_PREFERRED_DAY_PART": 5, "PREFERRED_FREE_DAY": 4, "WEEKEND_WORK_PATTERN": 2},
    not_available_handling="HARD_UNAVAILABILITY", preference_streams={"WEEKDAY", "WEEKEND"},
    weekend_pattern_values={"Saturday Only": {"SATURDAY"}, "Sunday Only": {"SUNDAY"}, "Saturday and Sunday": {"SATURDAY", "SUNDAY"}})
INSTITUTION_B = {**INSTITUTION_A, "settings_reference": "SYNTHETIC-INSTITUTION-B:lecturer-preferences:v3",
    "time_of_day_windows": {"MORNING": dict(start_minute=510, end_minute=630), "AFTERNOON": dict(start_minute=630, end_minute=1050)},
    "weights": {"PREFERRED_DAY_PART": 1, "NOT_PREFERRED_DAY_PART": 1, "PREFERRED_FREE_DAY": 1, "WEEKEND_WORK_PATTERN": 9}}


def settings(base=INSTITUTION_A, **updates):
    return LecturerPreferenceSettings.model_validate({**base, **updates})


def preference_row(staff="SYN-LECTURER", cells=None, *, free="No Preference", pattern="Saturday Only", both="No", expected=5, row=5):
    values = {"Staff_ID": staff, "Staff_Name": "Synthetic Name", "Position": "Lecturer", "Department": None,
              "Expected_Working_Days": expected, "Weekend_Work_Pattern": pattern, "Preferred_Free_Day": free,
              "Can_Work_Both_Weekend_Days": both, "Special_Notes": None,
              **{f"{day}_{part}": "Available" for day in DAYS for part in ("Morning", "Afternoon")}, **(cells or {})}
    return SourceRecord(SourceLocation("synthetic-preferences.xlsx", "Lecturer_Preferences", row), values)


def batch(cells=None, *, institution=INSTITUTION_A, stream="WEEKDAY", **row):
    """Two 120-minute lectures for one lecturer, prepared through the existing PP1 path."""
    source = WideImportResult(source_confirmation_reference="synthetic:stored-records",
                              lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    source.staff_members = [StaffMember(staff_id="SYN-LECTURER", staff_name="Synthetic Name", position="Lecturer", staff_type="ACADEMIC")]
    source.source_preferences.append(preference_row(cells=cells, **row))
    allocations = SpecializationAllocations(sizing=GroupSizingPolicy(), specialization_names={"IT": "Synthetic IT", "SE": "Synthetic SE"})
    for index, code in enumerate(("IT", "SE"), 2):
        location = SourceLocation("synthetic-modules.xlsx", "Modules_183", index)
        source.source_modules.append(SourceModuleRecord(location, dict(Lecture_Hours_Week=2, Tutorial_Hours_Week=0,
            Workshop_Hours_Week=0, Lab_Hours_Week=0), contract_fields=dict(module_code="IT2100", module_name=f"Synthetic {code}",
            academic_year=1, semester=1, specialization=code, degree_type="Synthetic")))
        source.source_assignments.append(SourceTeamAssignment(location, "LIC_Staff_ID", "LIC", "SYN-LECTURER", "Synthetic", TeachingRole.LIC))
        allocations.cohorts.append(CohortCount(cohort_id=f"SYN-{code}", academic_year=1, semester=1, stream=stream, student_count=80,
            specialization=code, source=SourceLocation("synthetic-allocation.xlsx", "STUDENT_ALLOCATIONS", 40 + index)))
    venues = VenueImportResult()
    venues.venues.append(Venue(venue_id="SYN-HALL", venue_name="Synthetic Room", venue_type="Lecture Hall", capacity=120))
    venues.source_venues.append(SourceRecord(SourceLocation("synthetic-venues.xlsx", "Venue_Master", 2), {}))
    config = configuration()
    if institution is not None:
        config = PP1Configuration.model_validate({**config.model_dump(round_trip=True), "lecturer_preference_settings": institution})
    return prepare_pp1(allocations, source, venues, config)


def solve(prepared_batch):
    result = solve_timetable(prepared_batch.prepared, prepared_batch.config, **prepared_batch.solver_inputs)
    if result.schedule:
        assert len(result.schedule) == len(prepared_batch.prepared.sessions)  # Nothing is ever dropped.
    return result


def penalty(result):
    return result.preference_report.score.total_weighted_penalty


def blocks(prepared_batch):
    staff = prepared_batch.solver_inputs["staff_input"]
    return sorted((row.day, row.start_minute, row.end_minute) for calendar in staff.availability for row in calendar.blocked_periods)


def closed(*days):
    return {f"{day}_{part}": "Not Available" for day in days for part in ("Morning", "Afternoon")}


def test_preferred_period_is_favoured_within_its_day():
    prepared = batch({**closed("Monday", "Tuesday", "Thursday", "Friday"), "Wednesday_Morning": "Preferred"})
    result = solve(prepared)
    assert result.status is SolverStatus.OPTIMAL and penalty(result) == 0
    assert {(row.day, 510 <= row.start_minute and row.end_minute <= 750) for row in result.schedule} == {("WEDNESDAY", True)}


def test_available_periods_are_neutral():
    # Only neutral cells: no soft criterion applies, so preference optimization is not attached at all.
    neutral = batch()
    assert "preference_input" not in neutral.solver_inputs and blocks(neutral) == []
    assert neutral.metadata["lecturer_preferences"]["soft_preferences_applicable"] is False
    assert solve(neutral).status is SolverStatus.OPTIMAL
    # Available cells never cost anything, even beside a Preferred cell: Tuesday (Available) and
    # Monday morning (Preferred) are both free of penalty; only Monday afternoon would be penalised.
    prepared = batch({**closed("Wednesday", "Thursday", "Friday"), "Monday_Morning": "Preferred"})
    result = solve(prepared)
    assert "preference_input" in prepared.solver_inputs and result.status is SolverStatus.OPTIMAL and penalty(result) == 0
    assert all(row.day == "TUESDAY" or row.end_minute <= 750 for row in result.schedule)


def test_not_preferred_period_is_still_used_when_it_is_the_only_option():
    prepared = batch({**closed("Monday", "Tuesday", "Thursday", "Friday"), "Wednesday_Morning": "Not Preferred",
                      "Wednesday_Afternoon": "Not Available"})
    result = solve(prepared)
    assert result.status is SolverStatus.OPTIMAL
    assert all(row.day == "WEDNESDAY" and row.end_minute <= 750 for row in result.schedule)
    assert penalty(result) == 2 * 5  # Two bookings, each overlapping the Not Preferred morning.


def test_not_available_periods_are_hard_blocked_and_independently_validated():
    prepared = batch({**closed("Monday", "Tuesday", "Thursday", "Friday"), "Wednesday_Morning": "Not Available"})
    assert blocks(prepared) == sorted([(day.upper(), start, end) for day in ("Monday", "Tuesday", "Thursday", "Friday")
                                       for start, end in ((510, 750), (750, 1050))] + [("WEDNESDAY", 510, 750)])
    result = solve(prepared)
    assert result.status is SolverStatus.OPTIMAL and "staff_blocked_periods" in result.validation.checks
    assert all(row.day == "WEDNESDAY" and row.start_minute >= 750 for row in result.schedule)
    validate_timetable(prepared.prepared, prepared.config, result.schedule, venue_input=prepared.solver_inputs["venue_input"],
                       staff_input=prepared.solver_inputs["staff_input"])


def test_unsatisfiable_hard_unavailability_gives_clear_diagnostics_without_dropping_sessions():
    prepared = batch(closed(*WEEKDAYS))
    diagnostics = prepared.metadata["hard_availability_diagnostics"]
    assert len(diagnostics) == len(prepared.prepared.sessions) == 2
    assert {row["code"] for row in diagnostics} == {"NO_AVAILABLE_ELIGIBLE_LECTURER"}
    assert all(row["eligible_candidates"] == ["SYN-LECTURER"] for row in diagnostics)
    result = solve(prepared)
    assert result.status is SolverStatus.INFEASIBLE and result.schedule == ()
    assert "no eligible lecturer available" in result.failure_reason and "hard availability" in result.failure_reason


def test_morning_and_afternoon_boundaries_come_from_each_institution():
    cells = {**closed("Monday", "Tuesday", "Thursday", "Friday"), "Wednesday_Morning": "Not Available"}
    first, second = batch(cells), batch(cells, institution=INSTITUTION_B)
    assert ("WEDNESDAY", 510, 750) in blocks(first) and ("WEDNESDAY", 510, 630) in blocks(second)
    for prepared, morning_end in ((first, 750), (second, 630)):
        result = solve(prepared)
        assert result.status is SolverStatus.OPTIMAL
        assert all(row.start_minute >= morning_end for row in result.schedule if row.day == "WEDNESDAY")
    assert blocks(batch(cells)) == blocks(first)  # Institution A is unaffected by preparing institution B.
    weighted = {name: batch({"Tuesday_Morning": "Preferred"}, institution=base) for name, base in (("A", INSTITUTION_A), ("B", INSTITUTION_B))}
    weights = {name: {row.category.value: row.penalty_weight for row in item.solver_inputs["preference_input"].input_snapshot.preferences}
               for name, item in weighted.items()}
    assert weights == {"A": {"TIME_OF_DAY": 3, "WEEKEND_DAY": 2}, "B": {"TIME_OF_DAY": 1, "WEEKEND_DAY": 9}}
    assert {name: item.solver_inputs["preference_policy"].policy_reference for name, item in weighted.items()} == {
        "A": INSTITUTION_A["settings_reference"], "B": INSTITUTION_B["settings_reference"]}


def test_preferred_free_day_is_avoided():
    prepared = batch(closed("Wednesday", "Thursday", "Friday"), free="Monday")
    result = solve(prepared)
    assert result.status is SolverStatus.OPTIMAL and penalty(result) == 0
    assert {row.day for row in result.schedule} == {"TUESDAY"}


@pytest.mark.parametrize("pattern,free,expected_day,expected_penalty", [
    ("Sunday Only", "No Preference", "SUNDAY", 0),
    # Conflicting fields: the configured weights decide (pattern 2 per booking versus free day 4 per booking).
    ("Sunday Only", "Sunday", "SATURDAY", 2 * 2),
])
def test_weekend_pattern_and_free_day_follow_configured_weights(pattern, free, expected_day, expected_penalty):
    prepared = batch(stream="WEEKEND", pattern=pattern, free=free)
    result = solve(prepared)
    assert result.status is SolverStatus.OPTIMAL and {row.day for row in result.schedule} == {expected_day}
    assert penalty(result) == expected_penalty


def test_cross_field_conflicts_are_reported_not_resolved_silently():
    source = WideImportResult()
    source.source_preferences = [
        preference_row("S1", {"Sunday_Morning": "Preferred"}, free="Sunday", row=5),
        preference_row("S2", {"Sunday_Afternoon": "Preferred"}, row=6),
        preference_row("S3", closed("Saturday"), row=7),
        preference_row("S4", both="Yes", row=8),
        preference_row("S5", closed("Monday", "Tuesday", "Wednesday"), expected=5, row=9),
        preference_row("S6", closed(*DAYS), row=10),
    ]
    found = {(row.staff_id, row.code) for row in preference_conflicts(source, settings())}
    assert {("S1", "FREE_DAY_HAS_PREFERRED_PERIOD"), ("S2", "WEEKEND_PATTERN_EXCLUDES_PREFERRED_DAY"),
            ("S3", "WEEKEND_PATTERN_DAY_NOT_AVAILABLE"), ("S4", "BOTH_WEEKEND_DAYS_FLAG_MISMATCH"),
            ("S5", "EXPECTED_WORKING_DAYS_EXCEED_AVAILABLE_DAYS"), ("S6", "NO_AVAILABLE_PERIOD")} <= found
    assert not any(staff == "S4" and code != "BOTH_WEEKEND_DAYS_FLAG_MISMATCH" for staff, code in found)


@pytest.mark.parametrize("change,message", [
    (dict(time_of_day_windows={"MORNING": dict(start_minute=510, end_minute=750)}), "both MORNING and AFTERNOON"),
    (dict(time_of_day_windows={"MORNING": dict(start_minute=510, end_minute=800),
                               "AFTERNOON": dict(start_minute=750, end_minute=1050)}), "MORNING must end"),
    (dict(weights={"PREFERRED_DAY_PART": 1}), "every preference kind"),
    (dict(weekend_pattern_values={"Weekdays": {"MONDAY"}}), "Saturday and/or Sunday"),
    (dict(not_available_handling="SOFT"), "not_available_handling"),
    (dict(preference_streams=set()), "preference_streams"),
])
def test_invalid_institution_settings_are_rejected(change, message):
    with pytest.raises(ValidationError, match=message):
        settings(**change)


def test_unconfigured_preference_stream_is_rejected():
    source = WideImportResult()
    calendar = configuration().calendar
    with pytest.raises(ValueError, match="configured scheduling streams"):
        build_lecturer_preferences(source, settings(preference_streams={"WEEKEND"}), calendar, configured_streams={"WEEKDAY"})


def test_hard_blocks_merge_with_existing_calendars_and_reject_unknown_staff():
    prepared = batch({"Monday_Morning": "Not Available"})
    staff = prepared.solver_inputs["staff_input"]
    again = apply_hard_unavailability(staff, [("SYN-LECTURER", staff.availability[0].blocked_periods[0])])
    assert again == staff  # Idempotent; existing blocks are kept, never duplicated.
    with pytest.raises(ValueError, match="without a scheduling calendar"):
        apply_hard_unavailability(staff, [("UNKNOWN", staff.availability[0].blocked_periods[0])])


def test_legacy_configurations_and_snapshots_are_unchanged_without_settings():
    legacy = batch(institution=None)
    assert "lecturer_preference_settings" not in configuration().model_dump(round_trip=True)
    assert legacy.solver_inputs["preference_policy"].weight_mode.value == "SYNTHETIC_DEFAULTS" and blocks(legacy) == []
    assert "lecturer_preferences" not in legacy.metadata
    stored = batch().configuration.model_dump(round_trip=True)["lecturer_preference_settings"]
    assert stored["settings_reference"] == INSTITUTION_A["settings_reference"]


def test_database_generation_rebuilds_and_verifies_the_same_preferences_and_blocks():
    from schedence_teaching.persistence.generation_inputs import verify_preference_settings
    from schedence_teaching.persistence.ports import PersistenceConflict
    cells = {"Monday_Morning": "Not Available", "Tuesday_Afternoon": "Preferred"}
    prepared = batch(cells)
    source = WideImportResult(source_confirmation_reference="synthetic:stored-records")
    source.staff_members = [StaffMember(staff_id="SYN-LECTURER", staff_name="Synthetic Name", position="Lecturer", staff_type="ACADEMIC")]
    source.source_preferences.append(preference_row(cells=cells))
    configuration_with = PP1Configuration.model_validate({**configuration().model_dump(round_trip=True),
                                                          "lecturer_preference_settings": INSTITUTION_A})
    contexts = prepared.solver_inputs
    document = SimpleNamespace(calendar=configuration_with.calendar, contexts=SimpleNamespace(
        preference_input=contexts["preference_input"].input_snapshot, preference_policy=contexts["preference_policy"],
        staff_input=contexts["staff_input"]))
    verify_preference_settings(document, source, configuration_with)  # Matching stored rows verify exactly.
    source.source_preferences[0] = preference_row(cells={**cells, "Friday_Morning": "Not Available"})
    with pytest.raises(PersistenceConflict, match="unavailability|preferences differ"):
        verify_preference_settings(document, source, configuration_with)
