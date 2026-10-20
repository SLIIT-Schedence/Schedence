"""Fail-closed campus readiness, separate from solver feasibility/publication."""

from dataclasses import dataclass
from itertools import combinations
from uuid import UUID

from pydantic import ValidationError

from schedence_teaching.preparation import PreparationError, prepare_timetable_input
from schedence_teaching.preparation.combination_population import population_proofs
from schedence_teaching.schemas import PopulationRelationship
from schedence_teaching.schemas.specializations import specialization_labels

from .contracts import (
    InputMode, NormalizedSchedulingInput, ReadinessStatus, SchedulingSubmission, expected_owner,
)
from .diagnostics import ReadinessReport, covering_evidence, diagnostic
from .preparation import IntegrationContextError, PreparedScheduling, prepare_scheduling


def _leaves(value, path=()):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from _leaves(child, path + (key,))
    elif isinstance(value, (list, tuple)) and value:
        for index, child in enumerate(value):
            yield from _leaves(child, path + (index,))
    elif value is not None:
        yield path


def _lookup(payload, path):
    try:
        for part in path:
            payload = payload[part]
        return True, payload
    except (KeyError, IndexError, TypeError):
        return False, None


@dataclass(frozen=True)
class ReadinessAssessment:
    report: ReadinessReport
    normalized: NormalizedSchedulingInput | None = None
    preparation: PreparedScheduling | None = None


def assess_readiness(submission: SchedulingSubmission, *, expected_institution_id: UUID,
        actual_campus: bool = True) -> ReadinessAssessment:
    """No CP model, network, environment configuration or database access.

    The institution/mode arguments come from a trusted caller. Payload metadata
    cannot opt an actual-campus request into a synthetic fallback. Attestations
    must themselves be authenticated/authorized by a future service boundary.
    """
    if type(actual_campus) is not bool:
        raise TypeError("actual_campus must be an explicit boolean")
    submission = SchedulingSubmission.model_validate(submission.model_dump())
    expected_institution_id = UUID(str(expected_institution_id))
    raw, issues = submission.inputs, []
    mode = raw.get("mode")
    mode = str(mode) if isinstance(mode, str) and mode in set(InputMode) else None

    def add(status, code, path, message):
        issues.append(diagnostic(submission, status, code, path, message))

    def missing(path, message):
        add("MISSING", "required_input", path, message)

    def result(normalized=None, prepared=None):
        return ReadinessAssessment(ReadinessReport(mode, tuple(issues)), normalized, prepared)

    # Locate identity and reference errors before container-level model validators.
    prefix = ("combination_input", "teaching_input")
    teaching = raw.get("combination_input", {}).get("teaching_input", {}) if isinstance(raw.get("combination_input"), dict) else {}
    teaching = teaching if isinstance(teaching, dict) else {}
    collections = {name: teaching.get(name, []) for name in (
        "staff_members", "module_offerings", "student_groups", "session_requirements", "teaching_team_members")}
    identities = {}
    for name, field in (("staff_members", "staff_id"), ("module_offerings", "offering_id"),
                        ("student_groups", "group_id"), ("session_requirements", "requirement_id")):
        seen = set()
        rows = collections[name]
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not isinstance(row.get(field), str):
                continue
            value = row[field]
            if value in seen:
                add("AMBIGUOUS", "duplicate_identity", prefix + (name, index, field),
                    "Supply distinct authoritative identifiers; module codes are not offering identifiers.")
            seen.add(value)
        identities[name] = seen
    for name, fields in (("teaching_team_members", {"staff_id": "staff_members", "offering_id": "module_offerings"}),
                         ("session_requirements", {"offering_id": "module_offerings", "group_id": "student_groups"}),
                         ("student_groups", {"parent_group_id": "student_groups"})):
        rows = collections[name]
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for field, target in fields.items():
                if isinstance(row.get(field), str) and row[field] not in identities.get(target, set()):
                    add("INVALID", "missing_reference", prefix + (name, index, field),
                        "Reference an existing record in the corresponding academic collection.")
    staff_rows = collections["staff_members"]
    staff_types = {}
    if isinstance(staff_rows, list):
        staff_types = {row["staff_id"]: row.get("staff_type") for row in staff_rows
                       if isinstance(row, dict) and isinstance(row.get("staff_id"), str)}
    team_rows = collections["teaching_team_members"]
    if isinstance(team_rows, list):
        for index, row in enumerate(team_rows):
            if not isinstance(row, dict) or not isinstance(row.get("staff_id"), str):
                continue
            if row.get("role") in ("LIC", "CO_LIC") and staff_types.get(row["staff_id"]) == "INSTRUCTOR":
                add("INVALID", "invalid_academic_role", prefix + ("teaching_team_members", index, "role"),
                    "LIC and Co-LIC must reference explicitly academic staff; preserve instructors as team members.")
    raw_lunch = raw.get("student_lunch_policy")
    if isinstance(raw_lunch, dict) and any(raw_lunch.get(field) is None for field in (
            "applicable_dates", "applicable_streams", "applicability_reference")):
        missing(("student_lunch_policy",), "Confirm the fixed student lunch periods' exact date and stream applicability.")
    try:
        inputs = NormalizedSchedulingInput.model_validate(raw)
    except ValidationError as error:
        for item in error.errors(include_input=False, include_context=False):
            is_missing = item["type"] == "missing"
            add("MISSING" if is_missing else "INVALID", item["type"], item["loc"],
                "Supply this required scheduling input." if is_missing else
                "Correct this field or collection to satisfy its existing contract and references.")
        return result()

    if inputs.institution_id != expected_institution_id:
        add("INVALID", "institution_mismatch", ("institution_id",), "Use only data for the caller's authorized institution.")
    required_mode = InputMode.ACTUAL_CAMPUS if actual_campus else InputMode.SYNTHETIC
    if inputs.mode != required_mode or inputs.calendar.synthetic_data is actual_campus:
        add("INVALID", "input_mode", ("mode",), "Request mode and calendar must explicitly match the caller's campus/synthetic mode.")
    if not inputs.demand_complete:
        missing(("demand_complete",), "Obtain confirmation that this export contains the complete requested teaching demand.")

    # Require provenance for every supplied value. A collection export can attest
    # its records; a more specific row/field claim takes precedence. Null optional
    # metadata does not require invented provenance. Empty collections do.
    duplicate_paths, confirmed_sources = set(), set()
    for evidence in submission.evidence:
        exists, _ = _lookup(raw, evidence.path)
        if not exists:
            add("INVALID", "evidence_path", evidence.path, "Point source evidence at a supplied field or collection.")
    for path in _leaves(raw):
        sources = covering_evidence(submission.evidence, path)
        if not sources:
            add("UNVERIFIED", "source_evidence", path, "Supply an owner, dated provenance and confirmation for this value.")
            continue
        if len(sources) != 1:
            key = sources[0].path
            if key not in duplicate_paths:
                add("AMBIGUOUS", "ambiguous_evidence", key, "Resolve competing source attestations at this path.")
                duplicate_paths.add(key)
            continue
        source = sources[0]
        if source.institution_id != expected_institution_id or source.owner != expected_owner(str(path[0])):
            add("INVALID", "source_scope", path, "Use the correct authoritative service and institution for this input.")
        elif source.synthetic is actual_campus:
            add("UNVERIFIED", "synthetic_evidence", path, "Campus requests require non-synthetic evidence; example requests require explicit synthetic evidence.")
        elif source.status != ReadinessStatus.CONFIRMED or source.confirmation_reference is None:
            add(source.status if source.status != ReadinessStatus.CONFIRMED else "UNVERIFIED",
                "source_confirmation", path, "Obtain explicit upstream verification and a confirmation reference.")
        elif source.effective_from > inputs.calendar.week_start or source.effective_until < inputs.calendar.week_end:
            add("INVALID", "source_dates", path, "Supply an export attestation valid for the entire requested academic week.")
        else:
            confirmed_sources.add(source.path)

    for path in sorted(confirmed_sources, key=lambda item: tuple(str(part) for part in item)):
        if not any(item.path[:len(path)] == path and item.status != ReadinessStatus.CONFIRMED for item in issues):
            add("CONFIRMED", "source_confirmed", path, "Supplied values have a correctly scoped, dated upstream confirmation claim.")

    # Defaults in reusable contracts must not conceal absent source declarations.
    explicit = [("combination_input", "policy", "allow_combination"),
                ("combination_config", "objective"),
                ("venue_input", "combined_venues"), ("venue_input", "shared_resources")]
    explicit += [("work_period_input", field) for field in (
        "staff_institutions", "appointments", "administrative_resolutions", "administrative_periods",
        "research_policies", "research_reservations", "flexible_policies", "flexible_requests")]
    explicit += [("staff_input", "workload_input", field) for field in (
        "recognized_work", "recognized_work_complete_for", "attendance", "administrative_reservations")]
    for path in explicit:
        if not _lookup(raw, path)[0]:
            missing(path, "Supply an explicit declaration, including an attested empty collection when none applies.")
    for name, rows in collections.items():
        if not rows:
            missing(prefix + (name,), "Supply the complete nonempty academic collection for this scheduling request.")
    staff_ids = {row.staff_id for row in inputs.teaching_input.staff_members}
    if set(inputs.academic_staff_references) != staff_ids:
        missing(("academic_staff_references",), "Supply an institution-scoped ADS UUID cross-reference for every preserved staff ID.")
    if len(set(inputs.academic_staff_references.values())) != len(inputs.academic_staff_references):
        add("AMBIGUOUS", "staff_crosswalk", ("academic_staff_references",), "One ADS staff UUID must identify one preserved staff ID.")
    offerings = {row.offering_id: row for row in inputs.teaching_input.module_offerings}
    if inputs.mode is InputMode.ACTUAL_CAMPUS and inputs.staff_input.allow_unclassified_staff_for_testing:
        add("INVALID", "testing_workload_override", ("staff_input", "allow_unclassified_staff_for_testing"),
            "Actual-campus scheduling requires complete workload profiles; the unclassified-staff option is testing-only.")
    groups = {row.group_id: row for row in inputs.teaching_input.student_groups}
    for name, rows in (("module_offerings", inputs.teaching_input.module_offerings),
                       ("student_groups", inputs.teaching_input.student_groups)):
        for index, row in enumerate(rows):
            if row.semester != inputs.semester:
                add("INVALID", "semester_scope", prefix + (name, index, "semester"), "Record semester must match the requested academic term scope.")
    for index, row in enumerate(inputs.teaching_input.student_groups):
        if row.group_id not in inputs.combination_input.group_specializations:
            missing(("combination_input", "group_specializations", row.group_id), "Supply this group's authoritative specialization.")
    for index, row in enumerate(inputs.teaching_input.session_requirements):
        specialization = inputs.combination_input.group_specializations.get(row.group_id)
        try:
            applicable_specs = specialization_labels(offerings[row.offering_id].specialization)
        except ValueError:
            applicable_specs = ()
        if specialization is not None and specialization not in applicable_specs:
            add("INVALID", "specialization_scope", prefix + ("session_requirements", index),
                "Group specialization must belong to the supplied offering specialization list; matching module codes do not establish shared content.")
        if not any(team.offering_id == row.offering_id for team in inputs.teaching_input.teaching_team_members):
            missing(prefix + ("session_requirements", index, "offering_id"), "Supply eligible staff references for this teaching requirement.")
        if not any(rule.requirement_id == row.requirement_id for rule in inputs.venue_input.requirements):
            missing(("venue_input", "requirements"), "Supply an explicit venue-eligibility declaration for every session requirement.")
    available_staff = {row.staff_id for row in inputs.staff_input.availability}
    if not staff_ids <= available_staff:
        missing(("staff_input", "availability"), "Supply dated availability and explicit blocked periods for every staff record.")
    for index, calendar in enumerate(raw["staff_input"]["availability"]):
        for field in ("allowed_teaching_days", "available_windows", "blocked_periods"):
            if field not in calendar:
                missing(("staff_input", "availability", index, field),
                    "Declare staff calendars explicitly; an attested empty block list means no known blocks.")
    calendars = {row.venue_id: row for row in inputs.venue_input.calendars}
    days = {row.day for row in inputs.solver_config.days}
    for index, venue in enumerate(inputs.venue_input.venues):
        if venue.venue_id in inputs.venue_input.unresolved_venue_ids:
            # Existing solver excludes unresolved spaces, preserving inventory.
            continue
        calendar = calendars.get(venue.venue_id)
        if calendar is None or calendar.available_windows is None or calendar.blocked_periods is None:
            missing(("venue_input", "venues", index), "Supply availability and confirmed blocked periods, or explicitly exclude an unresolved venue.")
        elif set(calendar.available_windows) != days:
            missing(("venue_input", "calendars"), "Declare each configured day explicitly; an empty list means confirmed closed.")
    lunch = inputs.student_lunch_policy
    if lunch.applicable_dates is None or lunch.applicable_streams is None or lunch.applicability_reference is None:
        missing(("student_lunch_policy",), "Confirm date and weekday/weekend applicability of the fixed year-specific lunch periods.")
    for staff, institution in inputs.work_period_input.staff_institutions.items():
        if institution != str(expected_institution_id):
            add("INVALID", "institution_mismatch", ("work_period_input", "staff_institutions", staff), "Work-period staff must belong to the requested institution.")
    if set(inputs.work_period_input.staff_institutions) != staff_ids:
        missing(("work_period_input", "staff_institutions"),
            "Supply an explicit institution mapping for every preserved staff ID, including records without their own institution field.")
    for field in ("administrative_periods", "research_policies", "research_reservations",
                  "flexible_policies", "flexible_requests"):
        for index, record in enumerate(getattr(inputs.work_period_input, field)):
            if record.institution_id is not None and record.institution_id != str(expected_institution_id):
                add("INVALID", "institution_mismatch", ("work_period_input", field, index, "institution_id"),
                    "Work-period policy and reservation records must belong to the requested institution.")
    for field in ("appointments", "administrative_resolutions"):
        for index, record in enumerate(getattr(inputs.work_period_input, field)):
            if record.institution_id is None or record.institution_id != str(expected_institution_id):
                add("INVALID", "institution_mismatch", ("work_period_input", field, index, "institution_id"),
                    "Appointment and resolution records require the same explicit institution.")
    if any(item.status != ReadinessStatus.CONFIRMED for item in issues):
        return result(inputs)
    try:
        prepared_groups = prepare_timetable_input(inputs.teaching_input)
        proofs = population_proofs(inputs.combination_input, prepared_groups.hierarchy)
        for first, second in combinations(groups, 2):
            pair = tuple(sorted((first, second)))
            if not prepared_groups.hierarchy.conflicts(first, second) and (
                    pair not in proofs or proofs[pair].relationship == PopulationRelationship.UNKNOWN):
                missing(("combination_input", "population_evidence"),
                    "Confirm each unrelated group pair's population relationship, including cross-stream overlap; IDs alone prove nothing.")
        if any(item.status != ReadinessStatus.CONFIRMED for item in issues):
            return result(inputs)
        prepared = prepare_scheduling(inputs)
    except PreparationError as error:
        for item in error.issues:
            add("INVALID", item.code, ("combination_input",) + item.location,
                "Correct the referenced academic relationship or requirement before preparing sessions.")
        return result(inputs)
    except IntegrationContextError as error:
        add("INVALID", "policy_context", (error.field,),
            "Correct this context's references, calendar dates, confirmed policies and applicability using its existing scheduling contract.")
        return result(inputs)
    except ValueError:
        # Existing validators may mention staff or source values. Retain privacy
        # and require review of the original typed contexts, rather than echoing.
        add("INVALID", "policy_context", ("scheduling_context",),
            "Existing scheduling validators rejected the calendar, eligibility or policy context. Review dated staff/venue, workload, preference, fairness, consecutive and lunch contracts.")
        return result(inputs)
    issues.append(diagnostic(submission, "CONFIRMED", "complete_input", (),
        "Required input, provenance, population relationships and existing scheduling contexts passed preflight; feasibility and approval remain separate."))
    return result(inputs, prepared)
