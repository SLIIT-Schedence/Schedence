"""Map existing staged cells without rewriting sources or creating hard availability."""

from collections import Counter
from dataclasses import asdict, dataclass, field

from schedence_teaching.preferences.contracts import LecturerPreference, PreferenceConfirmation, PreferenceExpression
from schedence_teaching.schemas import StaffType

from .preference_config import LecturerPreferenceMapping, SlotMeaning
from .records import ImportIssue
from .staging import WideImportResult


@dataclass
class PreferenceMappingResult:
    candidates: list[PreferenceExpression] = field(default_factory=list)
    preferences: list[LecturerPreference] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    row_count: int = 0
    mapped_staff_count: int = 0
    cell_classifications: Counter = field(default_factory=Counter)
    source_confirmation_reference: str | None = None

    @property
    def valid(self):
        return not any(row.severity == "error" for row in self.issues)

    def summary(self):
        groups = Counter(row.alternative_group_id for row in self.candidates if row.alternative_group_id is not None)
        needs_time_policy = any(row.category.value == "TIME_OF_DAY" for row in self.candidates)
        unresolved = any(row.code in {"ambiguous_weekend_work_pattern", "availability_confirmation_required", "availability_scope_required",
            "unmapped_preference_value", "missing_preference_value"} for row in self.issues)
        return {"valid": self.valid, "source_preference_rows": self.row_count,
            "mapped_staff_count": self.mapped_staff_count, "soft_preference_candidates": len(self.candidates),
            "scoped_preference_contracts": len(self.preferences),
            "unscoped_candidates": len(self.candidates) - len(self.preferences),
            "candidates_without_explicit_weight": sum(row.penalty_weight is None for row in self.candidates),
            "candidates_without_stream_scope": sum(row.stream is None for row in self.candidates),
            "unconfirmed_candidates": sum(row.confirmation_status is None or row.confirmation_status.value != "CONFIRMED" for row in self.candidates),
            "candidate_categories": dict(sorted(Counter(row.category.value for row in self.candidates).items())),
            "preferred_alternative_group_count": len(groups),
            "groups_with_multiple_preferred_day_parts": sum(count > 1 for count in groups.values()),
            "cell_classifications": dict(sorted(self.cell_classifications.items())),
            "issue_counts": dict(sorted(Counter(row.code for row in self.issues).items())),
            "issues": [asdict(row) for row in self.issues],
            "source_status": ("user_confirmed_university_sourced" if self.source_confirmation_reference
                              else "accepted_development_inputs_not_independently_verified"),
            "source_confirmation_reference": self.source_confirmation_reference,
            "hard_availability_generated": False, "cp_sat_preference_objective_enabled": False,
            "scoped_soft_contracts_available": self.valid and bool(self.preferences),
            "requires_scoring_calendar": True,
            "requires_time_of_day_policy": needs_time_policy,
            "ready_for_weekly_scoring": self.valid and bool(self.preferences)
                and len(self.preferences) == len(self.candidates) and not needs_time_policy and not unresolved,
            "time_of_day_boundaries_supplied_by_mapping": False, "university_ready": False}


def map_lecturer_preferences(source: WideImportResult, mapping: LecturerPreferenceMapping):
    """Pure conversion of validated staff references plus retained per-cell source data.

    Missing optional semantics remain staging/diagnostics. Instructors need no rows.
    Original imported values and source records are never mutated.
    """
    if not isinstance(source, WideImportResult) or not isinstance(mapping, LecturerPreferenceMapping):
        raise TypeError("Expected WideImportResult and LecturerPreferenceMapping.")
    mapping = LecturerPreferenceMapping.model_validate(mapping.model_dump(round_trip=True))
    if source.source_confirmation_reference and mapping.confirmation_status is None:
        mapping = mapping.model_copy(update={"confirmation_status": PreferenceConfirmation.CONFIRMED})
    result = PreferenceMappingResult(row_count=len(source.source_preferences),
        source_confirmation_reference=source.source_confirmation_reference)
    source_books = {row.location.workbook for row in source.source_preferences}
    result.issues.extend(row for row in source.issues if row.severity == "error" and
                         (row.workbook in source_books or row.worksheet == "Lecturer_Preferences"))
    staff = {row.staff_id: row for row in source.staff_members}
    identities = Counter(row.source_values.get("Staff_ID") for row in source.source_preferences
                         if isinstance(row.source_values.get("Staff_ID"), str))
    mapped_staff = set()

    def note(row, column, code, message, severity="warning"):
        row.location.issue(result, code, column, message, severity=severity)

    def add(row, column, **fields):
        candidate = PreferenceExpression(
            preference_id=f"source-preference:{row.location.workbook}:{row.location.worksheet}:{row.location.row}:{column}",
            staff_id=row.source_values["Staff_ID"], stream=mapping.stream,
            confirmation_status=mapping.confirmation_status,
            provenance={"source": "department-aligned-workbook", "reference": mapping.source_reference,
                "source_record_id": row.location.diagnostic_key, "workbook": row.location.workbook,
                "worksheet": row.location.worksheet, "row": row.location.row, "column": column,
                "interpretation_reference": mapping.weekend_mapping_confirmation_reference
                    if column == mapping.weekend_pattern_column else mapping.source_reference}, **fields)
        result.candidates.append(candidate)
        mapped_staff.add(candidate.staff_id)
        if mapping.scope is not None:
            result.preferences.append(candidate.with_scope(mapping.scope))

    for row in source.source_preferences:
        identity = row.source_values.get("Staff_ID")
        member = staff.get(identity) if isinstance(identity, str) else None
        if member is None or "Staff_ID" in row.invalid_cells:
            note(row, "Staff_ID", "unknown_preference_staff", "Preference ID must resolve exactly to a valid Staff Master record.", "error")
            continue
        if identities[identity] != 1:
            note(row, "Staff_ID", "duplicate_preference_staff", "Duplicate source preference rows require review; none is chosen or merged.", "error")
            continue
        if member.staff_type is not StaffType.ACADEMIC:
            note(row, "Staff_ID", "nonacademic_source_preference", "This lecturer workbook maps academic staff only; instructors do not require preferences.", "error")
            continue
        if any(column in row.invalid_cells or row.source_values.get(column) != expected for column, expected in (
            ("Staff_Name", member.staff_name), ("Position", member.position), ("Department", member.department))):
            note(row, "Staff_ID", "preference_profile_conflict", "Preference profile differs from Staff Master; source values are retained for review.", "error")
            continue
        before = len(result.candidates)
        for column, selector in mapping.slot_columns.items():
            value = row.source_values.get(column)
            if column in row.invalid_cells:
                continue
            if value is None or isinstance(value, str) and not value.strip():
                result.cell_classifications["missing_slot_value"] += 1
                note(row, column, "missing_preference_value", "No preference is supplied; no selection is invented.")
                continue
            meaning = mapping.slot_values.get(value) if isinstance(value, str) else None
            if meaning is None:
                result.cell_classifications["unmapped_slot_value"] += 1
                note(row, column, "unmapped_preference_value", "Source slot selection has no explicit semantic mapping.")
            elif meaning in {SlotMeaning.PREFER, SlotMeaning.AVOID}:
                result.cell_classifications[meaning.value] += 1
                add(row, column, category="TIME_OF_DAY", direction=meaning.value, days={selector.day}, time_of_day=selector.time_of_day,
                    alternative_group_id=f"{row.location.diagnostic_key}:preferred-day-parts:{selector.day.value}"
                        if meaning is SlotMeaning.PREFER else None)
                note(row, column, "time_of_day_boundaries_required", "Label is preserved; numerical boundaries need an explicit scoring policy.")
            elif meaning is SlotMeaning.NEUTRAL:
                result.cell_classifications["neutral_available"] += 1
            else:
                result.cell_classifications["source_availability_assertion"] += 1
                if source.source_confirmation_reference:
                    note(row, column, "availability_scope_required", "University-sourced unavailability is retained; dated numerical availability windows are still required for hard scheduling inputs.")
                else:
                    note(row, column, "availability_confirmation_required", "Source asserts unavailability; confirm through hard availability inputs. It is not converted into a soft preference or a hard block.")
        column = mapping.free_day_column
        value = row.source_values.get(column)
        if column not in row.invalid_cells:
            if value is None or isinstance(value, str) and not value.strip():
                note(row, column, "missing_preference_value", "No preferred free day is supplied.")
            elif isinstance(value, str) and value in mapping.free_day_values:
                day = mapping.free_day_values[value]
                if day is not None:
                    add(row, column, category="WORKING_DAY", direction="AVOID", days={day})
                    result.cell_classifications["preferred_free_day"] += 1
                else:
                    result.cell_classifications["unspecified_free_day"] += 1
            else:
                note(row, column, "unmapped_preference_value", "Preferred free day has no explicit mapping.")
        column = mapping.weekend_pattern_column
        value = row.source_values.get(column)
        if column not in row.invalid_cells:
            days = mapping.weekend_pattern_values.get(value) if isinstance(value, str) else None
            if days is not None:
                add(row, column, category="WEEKEND_DAY", direction="PREFER", days=days)
                result.cell_classifications["confirmed_weekend_pattern_mapping"] += 1
            elif value is None or isinstance(value, str) and not value.strip():
                note(row, column, "missing_preference_value", "No weekend work pattern is supplied.")
            else:
                note(row, column, "ambiguous_weekend_work_pattern", "Work pattern may describe preference or availability; soft weekend translation requires separate confirmation.")
        for column, code, message in (
            ("Expected_Working_Days", "attendance_assertion_separate", "Source working-day count is not teaching-day preference or authority to override attendance policy."),
            ("Can_Work_Both_Weekend_Days", "weekend_availability_assertion_separate", "Source capability is retained for availability confirmation, without creating hard restrictions."),
            ("Special_Notes", "unstructured_preference_notes", "Unstructured notes are retained in source staging; their meaning is not parsed or printed."),
        ):
            value = row.source_values.get(column)
            if value is not None and (not isinstance(value, str) or value.strip()):
                note(row, column, code, message)
        if mapping.scope is None and len(result.candidates) > before:
            note(row, None, "preference_scope_required", "Academic week/effective dates are absent; expressions remain unscoped staging candidates.")
    result.mapped_staff_count = len(mapped_staff)
    return result
