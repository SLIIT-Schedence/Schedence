"""Institution-configurable semantics for stored lecturer day-part preferences (P2.5).

The stored records are accepted institution input. Per institution, a versioned
settings contract decides how they are scored; nothing here is a universal rule:

- Preferred      -> weighted soft PREFER (existing TIME_OF_DAY preference objective);
- Available      -> neutral, no term;
- Not Preferred  -> weighted soft AVOID; scheduling stays permitted;
- Not Available  -> hard staff blocked period for that configured day part.

Preferred_Free_Day and Weekend_Work_Pattern keep their existing soft translations.
Can_Work_Both_Weekend_Days and Expected_Working_Days are preserved for diagnostics only.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from schedence_teaching.schemas import Stream
from schedence_teaching.schemas.models import InputContract, RequiredText
from schedence_teaching.schemas.time import TimeWindow
from schedence_teaching.workload.period_contracts import Weekday

from .contracts import LecturerPreference, LecturerPreferenceInput, PreferenceScoringPolicy, TimeOfDay


class PreferenceWeightKey(StrEnum):
    PREFERRED_DAY_PART = "PREFERRED_DAY_PART"
    NOT_PREFERRED_DAY_PART = "NOT_PREFERRED_DAY_PART"
    PREFERRED_FREE_DAY = "PREFERRED_FREE_DAY"
    WEEKEND_WORK_PATTERN = "WEEKEND_WORK_PATTERN"


class NotAvailableHandling(StrEnum):
    HARD_UNAVAILABILITY = "HARD_UNAVAILABILITY"


Weight = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
WEEKEND = {Weekday.SATURDAY, Weekday.SUNDAY}


class LecturerPreferenceSettings(InputContract):
    """One institution's versioned interpretation of its stored preference records."""

    settings_reference: RequiredText
    time_of_day_windows: dict[TimeOfDay, TimeWindow]
    weights: dict[PreferenceWeightKey, Weight]
    not_available_handling: NotAvailableHandling
    preference_streams: set[Stream] = Field(min_length=1)
    weekend_pattern_values: dict[RequiredText, set[Weekday]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete_settings(self) -> Self:
        if set(self.time_of_day_windows) != set(TimeOfDay):
            raise ValueError("configure both MORNING and AFTERNOON boundaries")
        morning, afternoon = self.time_of_day_windows[TimeOfDay.MORNING], self.time_of_day_windows[TimeOfDay.AFTERNOON]
        if morning.end_minute > afternoon.start_minute:
            raise ValueError("MORNING must end at or before AFTERNOON starts")
        if set(self.weights) != set(PreferenceWeightKey):
            raise ValueError("configure a weight for every preference kind (0 disables a kind)")
        if any(not days or days - WEEKEND for days in self.weekend_pattern_values.values()):
            raise ValueError("weekend pattern translations must name Saturday and/or Sunday")
        return self


@dataclass(frozen=True)
class PreferenceConflict:
    code: str
    staff_id: str
    detail: str


@dataclass(frozen=True)
class LecturerPreferenceBuild:
    preferences: LecturerPreferenceInput
    scoring_policy: PreferenceScoringPolicy
    blocked_periods: tuple[tuple[str, object], ...]
    conflicts: tuple[PreferenceConflict, ...]
    unscoped_preferences_apply_to_all_streams: bool

    def summary(self):
        return {"settings_reference": self.scoring_policy.policy_reference,
                "soft_preferences": len(self.preferences.preferences),
                "soft_by_category": dict(sorted(Counter(row.category.value + ":" + row.direction.value
                                                        for row in self.preferences.preferences).items())),
                "hard_blocked_periods": len(self.blocked_periods),
                "staff_with_hard_blocks": len({staff for staff, _ in self.blocked_periods}),
                "conflicts": dict(sorted(Counter(row.code for row in self.conflicts).items()))}


_KEYS = {("TIME_OF_DAY", "PREFER"): PreferenceWeightKey.PREFERRED_DAY_PART,
         ("TIME_OF_DAY", "AVOID"): PreferenceWeightKey.NOT_PREFERRED_DAY_PART,
         ("WORKING_DAY", "AVOID"): PreferenceWeightKey.PREFERRED_FREE_DAY,
         ("WEEKEND_DAY", "PREFER"): PreferenceWeightKey.WEEKEND_WORK_PATTERN}


def build_lecturer_preferences(academic, settings, calendar, *, configured_streams):
    """Map stored source rows with the existing adapter, then apply one institution's settings."""
    from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
    from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
    from schedence_teaching.solver.staff_inputs import StaffBlockedInterval
    settings = LecturerPreferenceSettings.model_validate(settings.model_dump(round_trip=True))
    configured = set(configured_streams)
    if settings.preference_streams - configured:
        raise ValueError("Preference streams must be configured scheduling streams.")
    all_streams = settings.preference_streams == configured
    mapping = map_lecturer_preferences(academic, LecturerPreferenceMapping(layout="department-aligned-preferences-v1",
        source_reference=settings.settings_reference, scope=dict(week_reference=calendar.week_reference),
        stream=None if all_streams else next(iter(settings.preference_streams)), confirmation_status="CONFIRMED",
        weekend_pattern_values=settings.weekend_pattern_values,
        weekend_mapping_confirmation_reference=settings.settings_reference if settings.weekend_pattern_values else None))
    if not mapping.valid:
        errors = sorted({row.code for row in mapping.issues if row.severity == "error"})
        raise ValueError("Stored lecturer preferences have blocking source errors: " + ", ".join(errors))
    if not all_streams and len(settings.preference_streams) != 1:
        raise ValueError("Restrict preferences to exactly one stream or to every configured stream.")
    preferences = LecturerPreferenceInput(preferences=[LecturerPreference.model_validate({
        **row.model_dump(round_trip=True), "penalty_weight": settings.weights[_KEYS[row.category.value, row.direction.value]],
        "weight_reference": settings.settings_reference}) for row in mapping.preferences])
    policy = PreferenceScoringPolicy(policy_reference=settings.settings_reference, weight_mode="CONFIRMED_WEIGHTS",
        time_of_day_windows=settings.time_of_day_windows, time_window_policy_reference=settings.settings_reference)
    days_by_weekday = defaultdict(list)
    for day, on_date in sorted(calendar.day_dates.items(), key=lambda item: item[1]):
        days_by_weekday[list(Weekday)[on_date.weekday()]].append(day)
    blocked = []
    for staff, weekday, part, _ in mapping.unavailable_slots:
        window = settings.time_of_day_windows[part]
        for day in days_by_weekday.get(weekday, ()):
            blocked.append((staff, StaffBlockedInterval(day=day, start_minute=window.start_minute, end_minute=window.end_minute)))
    return LecturerPreferenceBuild(preferences, policy, tuple(blocked), preference_conflicts(academic, settings), all_streams)


def preference_conflicts(academic, settings):
    """Cross-field contradictions in the stored rows; diagnostics only, scheduling semantics unchanged."""
    from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
    mapping = LecturerPreferenceMapping(layout="department-aligned-preferences-v1", source_reference=settings.settings_reference)
    output = []
    for row in academic.source_preferences:
        values, staff = row.source_values, row.source_values.get("Staff_ID")
        if not isinstance(staff, str):
            continue
        cells = {(slot.day, slot.time_of_day): values.get(column) for column, slot in mapping.slot_columns.items()}

        def day_values(day):
            return [value for (cell_day, _), value in cells.items() if cell_day is day]

        def add(code, detail):
            output.append(PreferenceConflict(code, staff, detail))

        free = mapping.free_day_values.get(values.get("Preferred_Free_Day"))
        pattern = settings.weekend_pattern_values.get(values.get("Weekend_Work_Pattern"))
        open_days = {day for day in Weekday if any(value != "Not Available" for value in day_values(day))}
        if free is not None and "Preferred" in day_values(free):
            add("FREE_DAY_HAS_PREFERRED_PERIOD", f"{free.value} is the preferred free day and also has a Preferred period")
        if free is not None and pattern is not None and free in pattern:
            add("FREE_DAY_IS_WEEKEND_WORK_DAY", f"{free.value} is both the preferred free day and a weekend work day")
        if pattern is not None:
            for day in WEEKEND - pattern:
                if "Preferred" in day_values(day):
                    add("WEEKEND_PATTERN_EXCLUDES_PREFERRED_DAY", f"{day.value} has a Preferred period outside the weekend pattern")
            for day in pattern:
                if day not in open_days:
                    add("WEEKEND_PATTERN_DAY_NOT_AVAILABLE", f"{day.value} is a weekend work day but every period is Not Available")
            both = values.get("Can_Work_Both_Weekend_Days")
            if both in {"Yes", "No"} and (both == "Yes") != (pattern == WEEKEND):
                add("BOTH_WEEKEND_DAYS_FLAG_MISMATCH", f"Can_Work_Both_Weekend_Days={both} disagrees with the weekend pattern")
        expected = values.get("Expected_Working_Days")
        if isinstance(expected, int) and expected > len(open_days):
            add("EXPECTED_WORKING_DAYS_EXCEED_AVAILABLE_DAYS", f"{expected} expected days but {len(open_days)} days have an available period")
        if not open_days:
            add("NO_AVAILABLE_PERIOD", "every day part is Not Available")
    return tuple(output)


def apply_hard_unavailability(staff_input, blocked_periods):
    """Add blocked periods to existing staff calendars; existing blocks and windows are kept."""
    from schedence_teaching.solver.staff_inputs import StaffSchedulingInput
    additions = defaultdict(list)
    for staff, block in blocked_periods:
        additions[staff].append(block.model_dump())
    data = staff_input.model_dump(round_trip=True)
    known = {row["staff_id"] for row in data["availability"]}
    if set(additions) - known:
        raise ValueError("Hard unavailability references staff without a scheduling calendar.")
    for row in data["availability"]:
        merged = list(row["blocked_periods"])
        for block in additions.get(row["staff_id"], ()):
            if block not in merged:
                merged.append(block)
        row["blocked_periods"] = sorted(merged, key=lambda item: (item["day"], item["start_minute"], item["end_minute"]))
    return StaffSchedulingInput.model_validate(data)


def hard_availability_diagnostics(prepared, config, staff_input):
    """Necessary conditions only: occurrences with no eligible lecturer able to start in any window."""
    from schedence_teaching.solver.eligibility import candidate_pools
    from schedence_teaching.solver.staff_constraints import staff_start_domains
    from schedence_teaching.solver.teaching_policy import eligible_assignment_days
    calendars = {row.staff_id: row for row in staff_input.availability}
    pools, output = candidate_pools(prepared.input_snapshot), []
    for session in prepared.sessions:
        candidates = sorted(pools[session.offering_id])
        usable = []
        for staff in candidates:
            if staff not in calendars:
                continue
            allowed = (eligible_assignment_days(prepared.input_snapshot, staff_input, staff_input.assignment_policy, staff, (session,))
                       if staff_input.assignment_policy is not None else None)
            if staff_start_domains(session, calendars[staff], config, staff_input.day_dates, allowed_days=allowed):
                usable.append(staff)
        if not usable:
            output.append({"code": "NO_AVAILABLE_ELIGIBLE_LECTURER", "occurrence_id": session.occurrence_id,
                           "offering_id": session.offering_id, "group_id": session.group_id,
                           "session_type": session.session_type.value, "eligible_candidates": candidates})
    return tuple(output)
