"""Source-bound module catalog and demand; no invented offerings or populations."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from pydantic import TypeAdapter

from schedence_teaching.excel_import.records import ImportIssue
from schedence_teaching.excel_import.staging import SourceLocation, SourceModuleRecord, WideImportResult
from schedence_teaching.preparation import build_group_hierarchy, prepare_timetable_input
from schedence_teaching.schemas import (
    ModuleOffering, SessionRequirement, SessionType, StudentGroup, TeachingTeamMember, TeachingTimetableInput,
)
from schedence_teaching.schemas.models import AcademicYear, InputContract, PositiveInteger, RequiredText, Semester
from schedence_teaching.schemas.specializations import specialization_labels


# Actual Department_Aligned headers; contact types are never interchanged.
CONTACT_HOUR_COLUMNS = {
    SessionType.LECTURE: "Lecture_Hours_Week",
    SessionType.TUTORIAL: "Tutorial_Hours_Week",
    SessionType.WORKSHOP: "Workshop_Hours_Week",
    SessionType.LAB: "Lab_Hours_Week",
}


class SpecializationStart(InputContract):
    """Explicit supplied starting information, never inferred from first module appearance."""

    specialization: RequiredText
    academic_year: AcademicYear
    semester: Semester | None = None
    source_reference: RequiredText


class OfferingSourceBinding(InputContract):
    """An externally supplied offering/stream, scoped to one source specialization."""

    source: SourceLocation
    offering: ModuleOffering


class NonLecturePattern(InputContract):
    offering_id: RequiredText
    session_type: SessionType
    duration_minutes: PositiveInteger
    sessions_per_week: PositiveInteger
    source_reference: RequiredText


@dataclass(frozen=True)
class ModuleContact:
    source: SourceModuleRecord
    specializations: tuple[str, ...]
    weekly_minutes: dict[SessionType, int]
    lecture_duration_minutes: int | None

    def to_dict(self):
        return dict(source=asdict(self.source.location), source_identity=self.source.location.diagnostic_key,
            source_identity_is_offering_id=False, **self.source.contract_fields,
            applicable_specializations=list(self.specializations),
            weekly_contact_minutes={key.value: value for key, value in self.weekly_minutes.items()},
            lecture_pattern=(dict(duration_minutes=self.lecture_duration_minutes, sessions_per_week=1)
                             if self.lecture_duration_minutes else None))


@dataclass
class AcademicCatalog:
    modules: list[ModuleContact] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    specialization_starts: tuple[SpecializationStart, ...] = ()
    confirmation_reference: str | None = None

    @property
    def valid(self):
        return bool(self.modules) and not any(row.severity == "error" for row in self.issues)

    def select(self, *, academic_year, semester, specialization):
        return tuple(row for row in self.modules
            if row.source.contract_fields["academic_year"] == academic_year
            and row.source.contract_fields["semester"] == semester and specialization in row.specializations)

    def to_dict(self):
        scopes = Counter((row.source.contract_fields["academic_year"], row.source.contract_fields["semester"], spec)
                         for row in self.modules for spec in row.specializations)
        return dict(valid=self.valid,
            data_status="user_confirmed_university_sourced" if self.confirmation_reference else "source_confirmation_not_supplied",
            source_confirmation_reference=self.confirmation_reference,
            module_rows=len(self.modules),
            lecture_duration_counts=dict(Counter(str(row.lecture_duration_minutes or 0) for row in self.modules)),
            weekly_contact_minutes_by_type={kind.value: sum(row.weekly_minutes[kind] for row in self.modules)
                                           for kind in SessionType},
            scope_counts=[dict(academic_year=year, semester=semester, specialization=spec, module_rows=count)
                          for (year, semester, spec), count in sorted(scopes.items())],
            specialization_starts=[row.model_dump(mode="json") for row in self.specialization_starts],
            specialization_starts_inferred=False,
            modules=[row.to_dict() for row in self.modules], issues=[asdict(row) for row in self.issues])


def extract_academic_catalog(source: WideImportResult, *, specialization_starts=()) -> AcademicCatalog:
    """Keep every source row, including repeated codes and raw specialization text."""
    starts = tuple(SpecializationStart.model_validate(row.model_dump() if isinstance(row, SpecializationStart) else row)
                   for row in specialization_starts)
    if len({row.specialization for row in starts}) != len(starts):
        raise ValueError("Duplicate specialization-start records are ambiguous.")
    catalog = AcademicCatalog(issues=list(source.issues), specialization_starts=starts,
                             confirmation_reference=source.source_confirmation_reference)
    if not source.source_modules:
        raise ValueError("No source module records are available.")
    for original in source.source_modules:
        row = deepcopy(original)
        if not row.valid_fields:
            row.location.issue(catalog, "invalid_module_fields", None, "Existing module fields failed source validation.")
            continue
        try:
            specs = specialization_labels(row.contract_fields["specialization"])
        except ValueError:
            row.location.issue(catalog, "invalid_specialization_list", "Specialization", "Specialization list has empty or repeated entries.")
            continue
        minutes = {}
        for kind, column in CONTACT_HOUR_COLUMNS.items():
            value = row.source_values.get(column)
            try:
                if type(value) not in {int, float, Decimal} or column in row.invalid_cells:
                    raise ValueError
                total = Decimal(str(value)) * 60
                if not total.is_finite() or total < 0 or total != total.to_integral_value():
                    raise ValueError
                minutes[kind] = int(total)
            except (ValueError, InvalidOperation):
                row.location.issue(catalog, "invalid_weekly_contact_hours", column,
                    "Supply literal nonnegative weekly hours representing whole minutes; blank/formula values are not zero.")
        if len(minutes) != len(CONTACT_HOUR_COLUMNS):
            continue
        lecture = minutes[SessionType.LECTURE]
        duration = None
        if lecture:
            if source.lecture_hours_interpretation != "ONE_CONTINUOUS_WEEKLY_LECTURE":
                row.location.issue(catalog, "lecture_interpretation_required", CONTACT_HOUR_COLUMNS[SessionType.LECTURE],
                    "A continuous weekly lecture interpretation must be explicitly configured.")
            elif lecture not in {60, 120}:
                row.location.issue(catalog, "unsupported_lecture_hours", CONTACT_HOUR_COLUMNS[SessionType.LECTURE],
                    "The confirmed source rule covers one- and two-hour lectures; no other lecture pattern is inferred.")
            else:
                duration = lecture
        catalog.modules.append(ModuleContact(row, specs, minutes, duration))
    return catalog


def build_specialization_teaching_input(source: WideImportResult, catalog: AcademicCatalog, *,
        offerings: list[OfferingSourceBinding], groups: list[StudentGroup], group_specializations: dict[str, str],
        session_audiences: dict[SessionType, tuple[str, ...]], nonlecture_patterns=(),
        module_attendance: dict[str, set[str]] | None = None,
        group_source_specializations: dict[str, tuple[str, ...]] | None = None) -> TeachingTimetableInput:
    """Generate all applicable group requirements together, ready for ONE global solve.

    Callers supply actual offering IDs, streams, groups and specialization mappings.
    Generated requirement IDs are deterministic planning IDs, not database IDs.
    Non-lecture patterns must explicitly account for the exact source weekly hours.
    """
    if not source.valid or not catalog.valid:
        raise ValueError("Academic source and catalog must be valid before demand generation.")
    # Fresh validated contracts reject mutated models and preserve the caller's originals.
    bindings = [OfferingSourceBinding.model_validate(row.model_dump()) for row in offerings]
    groups = [StudentGroup.model_validate(row.model_dump()) for row in groups]
    patterns = [NonLecturePattern.model_validate(row.model_dump() if isinstance(row, NonLecturePattern) else row)
                for row in nonlecture_patterns]
    group_specializations = TypeAdapter(dict[RequiredText, RequiredText]).validate_python(group_specializations)
    if not bindings or not groups:
        raise ValueError("Supply at least one offering and student group.")
    if set(group_specializations) != {row.group_id for row in groups}:
        raise ValueError("Supply exact group-to-specialization mappings; cohort totals cannot determine them.")
    # A teaching-track label is not a source specialization or enrollment claim.
    # Pooled populations require the same exact source-row curriculum; a union
    # would invent courses for some students and an intersection would drop them.
    applicability = {identity: (spec,) for identity, spec in group_specializations.items()}
    if group_source_specializations is not None:
        from .academic_pathways import curriculum_signature
        applicability = TypeAdapter(dict[RequiredText, tuple[RequiredText, ...]]).validate_python(group_source_specializations)
        if set(applicability) != set(group_specializations):
            raise ValueError("Supply exact source-applicability mappings for every academic group.")
        for group in groups:
            specs = applicability[group.group_id]
            if not specs or len(set(specs)) != len(specs):
                raise ValueError("Each academic group needs unique nonempty source applicability.")
            known_specs = {spec for module in catalog.modules for spec in module.specializations}
            if set(specs) - known_specs:
                raise ValueError("Academic track references an unknown source specialization.")
            signatures = {curriculum_signature(catalog, group.academic_year, group.semester, spec) for spec in specs}
            # A known single track with no modules remains available to the
            # existing blocked-scope diagnostics (e.g. Y4 S1 DS). It is never
            # pooled or given fabricated demand.
            if len(signatures) != 1 or (len(specs) > 1 and not next(iter(signatures))):
                raise ValueError("Academic track populations must have identical nonempty source-row curricula.")
    hierarchy = build_group_hierarchy(groups)
    audiences = TypeAdapter(dict[SessionType, tuple[RequiredText, ...]]).validate_python(session_audiences)
    for identities in audiences.values():
        if not identities or len(set(identities)) != len(identities) or set(identities) - set(group_specializations):
            raise ValueError("Session audiences require unique, known group IDs.")
    by_location = {row.source.location: row for row in catalog.modules}
    if len(by_location) != len(catalog.modules):
        raise ValueError("Source locations must be unique; module codes alone are insufficient.")
    if len({row.offering.offering_id for row in bindings}) != len(bindings):
        raise ValueError("Duplicate supplied offering IDs.")
    if module_attendance is not None:
        module_attendance = TypeAdapter(dict[RequiredText, set[RequiredText]]).validate_python(module_attendance)
        if set(module_attendance) != set(group_specializations):
            raise ValueError("Explicit enrollment mapping must include every group, including empty assignments.")
        offered = {row.offering.offering_id: row.offering for row in bindings}
        for group in groups:
            for identity in module_attendance[group.group_id]:
                offering = offered.get(identity)
                if offering is None or not set(applicability[group.group_id]) <= set(specialization_labels(offering.specialization)) or (
                        group.academic_year, group.semester, group.stream) != (offering.academic_year, offering.semester, offering.stream):
                    raise ValueError("Explicit module enrollment references an unknown or incompatible offering.")
    signatures = [(row.source, spec, row.offering.stream) for row in bindings
                  for spec in specialization_labels(row.offering.specialization)]
    if len(set(signatures)) != len(signatures):
        raise ValueError("Duplicate source/specialization/stream bindings would double-count demand.")
    pattern_map = {(row.offering_id, row.session_type): row for row in patterns}
    if len(pattern_map) != len(patterns) or any(row.session_type == SessionType.LECTURE for row in patterns):
        raise ValueError("Non-lecture patterns must be unique and cannot override the confirmed lecture rule.")
    requirements, teams, used_patterns = [], [], set()
    for binding in bindings:
        module = by_location.get(binding.source)
        if module is None:
            raise ValueError("Offering source row does not exist in this catalog.")
        offering = binding.offering
        for key in ("module_code", "module_name", "academic_year", "semester", "degree_type"):
            if getattr(offering, key) != module.source.contract_fields[key]:
                raise ValueError(f"Offering must preserve the source {key}.")
        for key in ("offering_id", "stream"):
            if key in module.source.contract_fields and getattr(offering, key) != module.source.contract_fields[key]:
                raise ValueError(f"Offering must preserve the supplied source {key}.")
        applicable_specs = specialization_labels(offering.specialization)
        if offering.specialization != module.source.contract_fields["specialization"] and not (
                len(applicable_specs) == 1 and applicable_specs[0] in module.specializations):
            raise ValueError("Offering specialization is absent from the source applicability list.")
        eligible = [row for row in groups if set(applicability[row.group_id]) <= set(applicable_specs)
            and (row.academic_year, row.semester, row.stream) == (offering.academic_year, offering.semester, offering.stream)
            and (module_attendance is None or offering.offering_id in module_attendance[row.group_id])]
        if not eligible:
            raise ValueError("Offering has no matching supplied student groups; no specialization population is invented.")
        members = [member for member in source.source_assignments if member.location == binding.source]
        if not members:
            raise ValueError("Source teaching-team eligibility is missing; no team is invented.")
        for member in members:
            if not member.valid_reference:
                raise ValueError("Teaching assignment has an invalid source reference.")
            teams.append(TeachingTeamMember(offering_id=offering.offering_id, staff_id=member.source_staff_id, role=member.role))
        for kind, weekly_minutes in module.weekly_minutes.items():
            if weekly_minutes == 0:
                continue
            if kind is SessionType.LECTURE:
                duration, frequency = module.lecture_duration_minutes, 1
            else:
                pattern_key = (offering.offering_id, kind)
                pattern = pattern_map.get(pattern_key)
                if pattern is None:
                    raise ValueError(f"Explicit {kind.value} duration/frequency needed for {offering.offering_id}.")
                if pattern.duration_minutes * pattern.sessions_per_week != weekly_minutes:
                    raise ValueError("Non-lecture pattern must preserve the exact source weekly contact minutes.")
                used_patterns.add(pattern_key)
                duration, frequency = pattern.duration_minutes, pattern.sessions_per_week
            # Explicit main/subgroup audiences prevent teaching the same parent
            # population twice. The caller chooses audiences for each session type.
            audience = [row for row in eligible if row.group_id in audiences.get(kind, ())]
            if not audience:
                raise ValueError(f"Explicit applicable {kind.value} student-group audience is required.")
            for index, first in enumerate(audience):
                if any(hierarchy.conflicts(first.group_id, second.group_id) for second in audience[index + 1:]):
                    raise ValueError("An audience cannot include both a parent and its descendant for the same content.")
            for group in audience:
                identity = ":".join(quote(value, safe="") for value in (offering.offering_id, group.group_id, kind.value))
                requirements.append(SessionRequirement(requirement_id=f"demand:{identity}", offering_id=offering.offering_id,
                    group_id=group.group_id, session_type=kind, duration_minutes=duration, sessions_per_week=frequency))
    if set(pattern_map) != used_patterns:
        raise ValueError("Unused or unknown non-lecture patterns must be corrected, not silently ignored.")
    teaching = TeachingTimetableInput(staff_members=deepcopy(source.staff_members), module_offerings=[row.offering for row in bindings],
        student_groups=groups, teaching_team_members=teams, session_requirements=requirements)
    prepare_timetable_input(teaching)
    return teaching
