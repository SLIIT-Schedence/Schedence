"""Database-to-existing-PP1 adapter; no Excel reads, SQL calls or new demand."""

from collections import Counter, defaultdict
import json
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from schedence_teaching.excel_import.staging import SourceLocation, SourceModuleRecord, SourceRecord, WideImportResult
from schedence_teaching.excel_import.preference_adapter import map_lecturer_preferences
from schedence_teaching.excel_import.preference_config import LecturerPreferenceMapping
from schedence_teaching.pilot.academic import extract_academic_catalog
from schedence_teaching.pilot.electives import (ElectiveAllocationPolicy, ElectiveSelection, EnrollmentPlan,
    EnrolledPP1, ModuleClassification, select_enrolled_scope, validate_elective_selections)
from schedence_teaching.pilot.elective_io import enrolled_demand_report
from schedence_teaching.pilot.pp1 import PP1Batch, PP1Configuration
from schedence_teaching.pilot.students import CohortCount, GroupSizingPolicy
from schedence_teaching.preparation import build_group_hierarchy
from schedence_teaching.preferences import LecturerPreferenceInput
from schedence_teaching.schemas import StaffMember, StudentGroup, Stream, Venue
from schedence_teaching.schemas.models import AcademicYear, InputContract, RequiredText, Semester
from schedence_teaching.schemas.specializations import specialization_labels

from .ports import PersistenceConflict
from .snapshots import canonical_json, content_digest, replay_snapshot


class DatabaseGenerationRequest(InputContract):
    institution_id: UUID
    snapshot_id: UUID
    semester: Semester
    academic_years: frozenset[AcademicYear] = frozenset((1, 2, 3, 4))
    streams: frozenset[Stream] = frozenset(Stream)
    specializations: frozenset[RequiredText] | None = None
    calendar_label: Literal["PP1_TEST_CALENDAR", "PP1_EXPERIMENTAL_CALENDAR"]
    policy_version: Literal["HISTORICAL_SNAPSHOT"]
    max_time_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 30.0

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.institution_id.int == 0 or not self.academic_years or not self.streams or self.specializations == frozenset():
            raise ValueError("institution and academic scope must be explicit and nonempty")
        return self


@dataclass(frozen=True)
class DatabaseGenerationInputs:
    request: DatabaseGenerationRequest
    enrolled: EnrolledPP1
    equivalence: dict
    provenance: dict
    blocked_scopes: tuple[dict, ...]


def require(condition, message):
    if not condition:
        raise PersistenceConflict(message)


def unique(rows, key):
    result = {}
    for row in rows:
        identity = row[key]
        require(identity not in result, "duplicate owner record identity")
        result[identity] = row
    return result


def evidence_source(document, label):
    matches = [source for source in document.sources if source.label == label]
    require(len(matches) == 1, "required historical policy evidence is missing")
    require(matches[0].sha256 == content_digest(matches[0].provenance), "historical policy evidence checksum is invalid")
    return matches[0].provenance


def academic_source(evidence, metadata):
    """Reconstruct the existing catalog/preference adapter's records from SQL rows."""
    tables, document = evidence.academic, evidence.document
    sources = unique(tables["source_datasets"], "id")
    departments = unique(tables["departments"], "id")
    staff = unique(tables["staff"], "staff_number")
    profiles = unique(tables["staff_teaching_profiles"], "staff_number")
    reconstructed = []
    for member in document.teaching_input.staff_members:
        require(member.staff_id in staff and member.staff_id in profiles, "staff/profile reference is missing")
        row, profile = staff[member.staff_id], profiles[member.staff_id]
        department = departments.get(profile["department_id"])
        require(profile["department_id"] is None or department is not None, "staff department reference is missing")
        reconstructed.append(StaffMember(staff_id=row["staff_number"], staff_name=row["full_name"],
            position=profile["source_position"], department=department["name"] if department else None,
            staff_type=profile["staff_type"]))
    require(canonical_json(reconstructed) == canonical_json(document.teaching_input.staff_members), "staff master differs from snapshot")
    academic = WideImportResult(staff_members=reconstructed,
        source_confirmation_reference=metadata.get("source_confirmation_reference"),
        lecture_hours_interpretation="ONE_CONTINUOUS_WEEKLY_LECTURE")
    for row in tables["modules"]:
        require(row["source_id"] in sources, "module source reference is missing")
        fields = {key: row[key] for key in ("module_code", "module_name", "academic_year", "semester", "degree_type")}
        fields["specialization"] = row["source_values"].get("Specialization")
        require(bool(fields["specialization"]), "module specialization mapping is missing")
        academic.source_modules.append(SourceModuleRecord(
            SourceLocation(sources[row["source_id"]]["filename"], row["worksheet"], row["source_row"]),
            row["source_values"], contract_fields=fields))
    for row in tables["lecturer_preferences"]:
        require(row["source_id"] in sources and row["staff_number"] in staff, "preference staff/source reference is missing")
        require(profiles[row["staff_number"]]["staff_type"] == "ACADEMIC", "instructors must not acquire lecturer preference records")
        require(row["source_values"].get("Staff_ID") == row["staff_number"], "preference staff identity mismatch")
        academic.source_preferences.append(SourceRecord(SourceLocation(sources[row["source_id"]]["filename"],
            row["worksheet"], row["source_row"]), row["source_values"]))
    # Restore source ordering before deterministic preference ID generation.
    academic.source_modules.sort(key=lambda row: (row.location.workbook, row.location.worksheet, row.location.row))
    academic.source_preferences.sort(key=lambda row: (row.location.workbook, row.location.worksheet, row.location.row))
    return academic


def verify_sources_and_references(evidence):
    document = evidence.document
    by_owner = {"ACADEMIC_DATA_SERVICE": evidence.academic, "RESOURCE_SERVICE": evidence.resource}
    for owner, tables in by_owner.items():
        sources = unique(tables["source_datasets"], "id")
        for source in document.sources:
            if source.owner.value != owner:
                continue
            versions = [row for row in sources.values() if (row["filename"], row["sha256"]) == (source.label, source.sha256)]
            require(len(versions) == 1, "snapshot source has no unambiguous owning-service provenance version")
            stored = versions[0]
            require((stored["sha256"], stored["provenance_kind"], canonical_json(stored["provenance"])) ==
                (source.sha256, source.provenance_kind.value, canonical_json(source.provenance)),
                "source checksum or provenance differs from the selected snapshot version")
        for name, records in tables.items():
            for row in records:
                require(row["institution_id"] == evidence.institution_id, "owner evidence belongs to another institution")
                if "source_id" in row:
                    require(row["source_id"] in sources, "owner source foreign key is missing")
    reference_tables = {("ACADEMIC_DATA_SERVICE", "STAFF"): (evidence.academic["staff"], "staff_number"),
        ("ACADEMIC_DATA_SERVICE", "MODULE_SOURCE_ROW"): (evidence.academic["modules"], "record_key"),
        ("RESOURCE_SERVICE", "VENUE"): (evidence.resource["venues"], "venue_code")}
    mapped = {}
    for reference in document.external_references:
        key = (reference.owner.value, reference.record_kind)
        require(key in reference_tables, "unsupported external reference kind needs an explicit mapping")
        rows, column = reference_tables[key]
        matching = [row for row in rows if row[column] == reference.local_reference and row["id"] == reference.authoritative_id]
        require(len(matching) == 1, "cross-service owner identifier cannot be resolved")
        mapped[(key[1], reference.local_reference)] = reference.authoritative_id
    for member in document.teaching_input.staff_members:
        require(("STAFF", member.staff_id) in mapped, "staff owner identifier mapping is missing")
    for venue in document.contexts.venue_input.venues:
        require(("VENUE", venue.venue_id) in mapped, "venue owner identifier mapping is missing")
    return mapped


def verify_catalog(evidence, academic, bindings, mapped):
    document, tables = evidence.document, evidence.academic
    modules = unique(tables["modules"], "record_key")
    specs = unique(tables["specializations"], "id")
    applicability = defaultdict(set)
    for row in tables["module_specializations"]:
        require(row["specialization_id"] in specs, "specialization owner reference is missing")
        applicability[row["module_id"]].add(specs[row["specialization_id"]]["code"])
    catalog = extract_academic_catalog(academic)
    require(catalog.valid, "database module contact-hour contracts are invalid")
    contacts = {row.source.location: row for row in catalog.modules}
    by_offering = {}
    for offering in document.teaching_input.module_offerings:
        require(offering.offering_id in bindings, "offering source-row mapping is missing")
        source = bindings[offering.offering_id]
        require(source.get("identifier_status") == "LOCAL_PP1_SOURCE_ROW_REFERENCE_NOT_DATABASE_ID",
            "unknown offering identity semantics require explicit authoritative mapping")
        key = f"{source['workbook']}:{source['worksheet']}:{source['row']}"
        require(key in modules and ("MODULE_SOURCE_ROW", key) in mapped, "module owner mapping is missing")
        module = modules[key]
        require(mapped[("MODULE_SOURCE_ROW", key)] == module["id"], "module owner mapping differs from source row")
        fields = ("module_code", "module_name", "academic_year", "semester", "degree_type")
        require(all(getattr(offering, field) == module[field] for field in fields), "module metadata differs from snapshot")
        require(set(specialization_labels(offering.specialization)) == applicability[module["id"]],
            "module specialization applicability differs from snapshot")
        location = SourceLocation(source["workbook"], source["worksheet"], source["row"])
        contact = contacts[location]
        require(all(module[f"{kind.value.lower()}_minutes"] == minutes for kind, minutes in contact.weekly_minutes.items()),
            "normalized module minutes disagree with preserved contact columns")
        by_offering[offering.offering_id] = (module, contact)
    expected_teams = Counter((offering, row["staff_number"], row["role"])
        for offering, (module, _) in by_offering.items() for row in tables["module_teaching_teams"] if row["module_id"] == module["id"])
    actual_teams = Counter((row.offering_id, row.staff_id, row.role.value) for row in document.teaching_input.teaching_team_members)
    require(expected_teams == actual_teams, "staff eligibility or source team membership differs from snapshot")
    identities = set()
    for requirement in document.teaching_input.session_requirements:
        key = (requirement.group_id, requirement.offering_id, requirement.session_type)
        require(key not in identities, "duplicate group/offering/session-type demand")
        identities.add(key)
        minutes = by_offering[requirement.offering_id][1].weekly_minutes[requirement.session_type]
        require(requirement.sessions_per_week == 1 and requirement.duration_minutes == minutes,
            "session demand differs from the selected unsplit PP1 source-contact policy")
    return by_offering


def allocation_cohorts(evidence):
    tables = evidence.academic
    totals, specializations = unique(tables["cohort_totals"], "id"), unique(tables["specializations"], "id")
    sums, cohorts = Counter(), {}
    for row in tables["pp1_specialization_allocations"]:
        require(row["cohort_total_id"] in totals and row["specialization_id"] in specializations,
            "PP1 allocation references a missing cohort or specialization")
        cohort = CohortCount.model_validate(row["source_values"]["cohort"])
        original = totals[row["cohort_total_id"]]
        require((cohort.cohort_id, cohort.student_count, cohort.specialization) ==
            (row["local_cohort_reference"], row["student_count"], specializations[row["specialization_id"]]["code"]),
            "PP1 allocation columns disagree with preserved contracts")
        require((cohort.academic_year, cohort.semester, cohort.stream.value) ==
            (original["academic_year"], original["semester"], original["stream"]), "allocation academic scope mismatch")
        require(cohort.cohort_id not in cohorts, "duplicate PP1 cohort identity")
        cohorts[cohort.cohort_id] = cohort
        sums[original["id"]] += cohort.student_count
    require(all(sums[key] == row["student_count"] for key, row in totals.items()), "allocations do not reconcile to university aggregate totals")
    groups = []
    main_counts = Counter()
    allocations = unique(tables["pp1_specialization_allocations"], "id")
    group_rows = unique(tables["pp1_student_groups"], "id")
    for row in group_rows.values():
        require(row["allocation_id"] in allocations, "planning group allocation reference is missing")
        group = StudentGroup.model_validate(row["source_values"]["group"])
        allocation = allocations[row["allocation_id"]]
        cohort = cohorts[allocation["local_cohort_reference"]]
        require((group.academic_year, group.semester, group.stream) ==
            (cohort.academic_year, cohort.semester, cohort.stream), "planning group academic scope mismatch")
        require((group.group_id, group.student_count) == (row["local_group_reference"], row["student_count"]),
            "planning group differs from its source contract")
        parent = group_rows.get(row["parent_id"])
        require((row["parent_id"] is None and group.parent_group_id is None) or
            (parent is not None and parent["local_group_reference"] == group.parent_group_id
                and parent["allocation_id"] == row["allocation_id"] and parent["plan_reference"] == row["plan_reference"]),
            "invalid planning group parent relationship")
        groups.append(group)
        if group.parent_group_id is None:
            main_counts[row["allocation_id"]] += group.student_count
    build_group_hierarchy(groups)
    require(all(main_counts[key] == row["student_count"] for key, row in allocations.items()),
        "planning groups do not preserve approved allocation totals")
    return cohorts


def academic_track_cohorts(evidence, document, academic, cohorts, sizing):
    """Recorded academic-track grouping, re-derived from the stored per-specialization allocations.

    Track cohorts and their planning groups are regenerated with the existing pathway
    grouping from authoritative allocation rows and module curricula; the snapshot must
    match exactly. Snapshots without recorded grouping provenance are unaffected.
    """
    recorded = [row.provenance["academic_grouping"] for row in document.sources if "academic_grouping" in row.provenance]
    if not recorded:
        return {}
    require(len(recorded) == 1, "academic-track grouping provenance is ambiguous")
    from schedence_teaching.pilot.academic_pathways import AcademicPathwayConfiguration, prepare_academic_grouping
    from schedence_teaching.pilot.allocations import SpecializationAllocations
    grouping = recorded[0]
    configuration = AcademicPathwayConfiguration.model_validate(grouping["configuration"])
    datasets = unique(evidence.academic["source_datasets"], "id")
    digests = {datasets[row["source_id"]]["sha256"] for row in evidence.academic["pp1_specialization_allocations"]}
    require(digests == {grouping["original_allocation_source_sha256"]}, "academic-track grouping cites another allocation source")
    rows = lambda values: sorted(canonical_json(value) for value in values)
    require(rows(row.model_dump(mode="json") for row in cohorts.values()) == rows(grouping["original_allocation_rows"]),
        "academic-track grouping differs from the stored per-specialization allocations")
    derived = prepare_academic_grouping(SpecializationAllocations(cohorts=list(cohorts.values()), sizing=sizing,
        source_sha256=digests.pop()), extract_academic_catalog(academic), configuration)
    periods = {(rule.academic_year, rule.semester) for rule in configuration.rules}
    tracks = {row.cohort_id: row for row in derived.plan.cohorts if (row.academic_year, row.semester) in periods}
    members = defaultdict(set)
    for group in derived.plan.groups:
        members[derived.plan.cohort_by_group[group.group_id]].add(group.group_id)
    regenerated = {group.group_id: group for group in derived.plan.groups if derived.plan.cohort_by_group[group.group_id] in tracks}
    recorded_slices = grouping["group_allocation_slices"]
    for scope in document.scopes:
        if scope.scope_key not in tracks:
            continue
        require(set(scope.group_references) == members[scope.scope_key], "academic-track scope groups differ from the derived plan")
        for group_id in scope.group_references:
            snapshot_group = next((row for row in document.teaching_input.student_groups if row.group_id == group_id), None)
            require(snapshot_group == regenerated[group_id], "academic-track planning group differs from the derived plan")
            require(canonical_json(recorded_slices.get(group_id)) == canonical_json(derived.group_allocation_slices[group_id]),
                "academic-track allocation slices differ from the derived plan")
    return tracks


def verify_preference_settings(document, academic, configuration):
    """P2.5: current stored preference rows must rebuild the snapshot's soft preferences and hard blocks exactly."""
    from schedence_teaching.preferences.product_settings import build_lecturer_preferences
    contexts = document.contexts
    require(contexts.preference_input is not None and contexts.preference_policy is not None and contexts.staff_input is not None,
        "institution preference settings require their preference and staff contexts")
    build = build_lecturer_preferences(academic, configuration.lecturer_preference_settings, document.calendar,
        configured_streams={stream for day in configuration.solver_config.days for stream in day.windows})
    require(canonical_json(build.preferences) == canonical_json(contexts.preference_input),
        "database lecturer preferences differ from snapshot")
    require(canonical_json(build.scoring_policy) == canonical_json(contexts.preference_policy),
        "institution preference scoring settings differ from snapshot")
    base = {row.staff_id: row.blocked_periods for row in configuration.staff_input.availability} if configuration.staff_input else {}
    expected = {}
    for staff, block in build.blocked_periods:
        expected.setdefault(staff, set()).add(canonical_json(block))
    for row in contexts.staff_input.availability:
        actual = {canonical_json(block) for block in row.blocked_periods}
        required = {canonical_json(block) for block in base.get(row.staff_id, ())} | expected.get(row.staff_id, set())
        require(actual == required, "hard lecturer unavailability differs from stored Not Available records")


def reconstruct_enrollment(evidence):
    document, prepared, config, contexts = replay_snapshot(evidence.document)
    require(document.institution_id == evidence.institution_id, "snapshot institution mismatch")
    require(document.contexts.venue_input is not None, "venue allocation contracts are missing")
    mapped = verify_sources_and_references(evidence)
    provenance = evidence_source(document, "PP1:preparation-source-bindings")
    metadata, bindings = dict(provenance["metadata"]), provenance["source_offering_bindings"]
    academic = academic_source(evidence, metadata)
    verify_catalog(evidence, academic, bindings, mapped)
    venue_rows = unique(evidence.resource["venues"], "venue_code")
    for venue in document.contexts.venue_input.venues:
        row = venue_rows[venue.venue_id]
        reconstructed = Venue(venue_id=row["venue_code"], **{key: row[key] for key in
            ("venue_name", "venue_type", "capacity", "building", "floor", "lab_specialization")})
        require(reconstructed == venue, "venue capacity, type or location differs from snapshot")
    configuration = PP1Configuration.model_validate(evidence_source(document, "PP1:calendar-configuration")["configuration"])
    # A sealed snapshot's config carries its resolved policy; the configured rules themselves must be identical.
    # Search budgets, seeds and workers are operational, outside the policy seal and outside this comparison.
    from schedence_teaching.policy.resolution import solver_rules
    require(canonical_json(solver_rules(configuration.solver_config)) == canonical_json(solver_rules(config))
        and configuration.calendar == document.calendar, "selected calendar or timetable rules differ from snapshot")
    if configuration.lecturer_preference_settings is not None:
        verify_preference_settings(document, academic, configuration)
    elif document.contexts.preference_input is not None:
        # Extending the teaching calendar did not rewrite the independently
        # versioned interpretation of the source weekend preference cells.
        references = {row.provenance.interpretation_reference
            for row in document.contexts.preference_input.preferences
            if row.provenance.column == "Weekend_Work_Pattern"}
        require(not configuration.weekend_preference_values or len(references) == 1 and None not in references,
            "historical weekend preference interpretation reference is missing")
        weekend_reference = next(iter(references), None)
        mapping = map_lecturer_preferences(academic, LecturerPreferenceMapping(layout="department-aligned-preferences-v1",
            source_reference=academic.source_confirmation_reference or configuration.configuration_reference,
            scope=dict(week_reference=document.calendar.week_reference), weekend_pattern_values=configuration.weekend_preference_values,
            weekend_mapping_confirmation_reference=weekend_reference))
        preferences = LecturerPreferenceInput(preferences=[row for row in mapping.preferences
            if row.category.value in {"WORKING_DAY", "WEEKEND_DAY"}])
        require(canonical_json(preferences) == canonical_json(document.contexts.preference_input),
            "database lecturer preferences differ from snapshot")
    sizing = GroupSizingPolicy.model_validate(metadata["groups"]["effective_sizing"])
    cohorts = allocation_cohorts(evidence)
    cohorts |= academic_track_cohorts(evidence, document, academic, cohorts, sizing)
    supported = [row for row in document.scopes if row.scope_status == "SUPPORTED"]
    links = {group: scope.scope_key for scope in supported for group in scope.group_references}
    for scope in document.scopes:
        require(scope.scope_key in cohorts, "snapshot scope has no preserved PP1 allocation")
        cohort = cohorts[scope.scope_key]
        require((scope.student_count, scope.academic_year, scope.semester, scope.stream, scope.specialization) ==
            (cohort.student_count, cohort.academic_year, cohort.semester, cohort.stream, cohort.specialization),
            "snapshot scope differs from its approved allocation")
    require(all(group.student_count <= (sizing.main_group_maximum if group.parent_group_id is None else sizing.lab_subgroup_maximum)
        for group in document.teaching_input.student_groups), "enrollment group exceeds its supplied planning cap")
    plan = EnrollmentPlan(tuple(cohorts[row.scope_key] for row in supported), tuple(document.teaching_input.student_groups),
        links, dict(document.group_specializations), sizing)
    batch = PP1Batch(prepared, config, contexts, plan, dict(document.group_specializations), bindings, configuration, metadata)
    registration = evidence_source(document, "PP1:synthetic-elective-registration-evidence")
    require(registration["allocation_label"] == "PP1_TEST_ELECTIVE_ALLOCATION" and registration["university_individual_registrations"] is False,
        "unsupported registration provenance")
    classifications = tuple(ModuleClassification.model_validate(row) for row in registration["classifications"])
    selections = tuple(ElectiveSelection.model_validate(row) for row in registration["selections"])
    if any(row.academic_year == 4 for row in plan.cohorts):
        selections, assessed = validate_elective_selections(batch, academic, classifications, selections,
            policy=ElectiveAllocationPolicy.model_validate(registration["policy"]))
    else:
        require(not selections, "elective registrations reference a cohort outside the selected snapshot")
        assessed = dict(cohorts={}, catalog=[], diagnostics=[], valid=True)
    attendance = {row.group_id: set() for row in plan.groups}
    for requirement in document.teaching_input.session_requirements:
        attendance[requirement.group_id].add(requirement.offering_id)
    pending = frozenset(scope for issue in document.issues if issue.issue_code == "RESEARCH_PENDING_SESSION_MAPPING"
        for scope in issue.details["supported_pending_cohort_ids"])
    enrolled = EnrolledPP1(batch, selections,
        classifications,
        config.attendance_memberships, attendance, assessed, registration["allocation_label"], research_pending_cohort_ids=pending)
    require(set(config.attendance_required_group_ids) == {row.group_id for row in config.attendance_memberships},
        "elective attendance evidence is incomplete")
    enrolled_demand_report(enrolled)  # Exact two-elective + compulsory contact coverage per test student.
    return enrolled


def compare_snapshot_inputs(document, earlier):
    """Earlier cached validated JSON is allowed; never reopen its Excel sources."""
    fields = ("institution_id", "academic_term_reference", "timezone_name", "calendar_label", "calendar",
        "teaching_input", "solver_config", "contexts", "group_specializations", "scopes", "issues")
    checks = {field: canonical_json(getattr(document, field)) == canonical_json(getattr(earlier, field)) for field in fields}
    current = {row.label: row for row in document.sources}
    checks["historical_source_provenance"] = all(row.label in current and row == current[row.label] for row in earlier.sources)
    require(all(checks.values()), "database input is not equivalent to the earlier validated source snapshot")
    return dict(status="PASSED", checks=checks, extra_owner_uuid_and_export_provenance_are_additive=True)


def blocked_scope_rows(document, scopes):
    """Original blocked-cohort rows, exactly as recorded in each scope's academic-mapping issue.

    Snapshot capture consumes these rows (cohort identity, period, counts and reason); the stored
    scope summary alone would drop the recorded reason, so the issue details are the source.
    """
    recorded = {row.scope_key: row.details for row in document.issues if row.issue_code == "BLOCKED_MISSING_ACADEMIC_MAPPING"}
    rows = []
    for scope in scopes:
        details = recorded.get(scope.scope_key)
        require(details is not None and details.get("cohort_id") == scope.scope_key and
            (details.get("academic_year"), details.get("semester"), details.get("stream"), details.get("specialization"),
             details.get("student_count")) == (scope.academic_year, scope.semester, scope.stream.value, scope.specialization,
                                               scope.student_count),
            "blocked scope has no matching recorded academic-mapping issue")
        rows.append(json.loads(canonical_json(details)))
    return tuple(rows)


def build_database_generation(evidence, request):
    request = DatabaseGenerationRequest.model_validate(request.model_dump())
    document = evidence.document
    require((request.institution_id, request.snapshot_id) == (evidence.institution_id, evidence.snapshot_id), "request belongs to another institution or snapshot")
    require(request.calendar_label == document.calendar_label, "experimental/calendar selection must match the explicitly selected snapshot")
    enrolled = reconstruct_enrollment(evidence)
    scopes = [scope for scope in document.scopes if scope.semester == request.semester and scope.academic_year in request.academic_years
        and scope.stream in request.streams and (request.specializations is None or scope.specialization in request.specializations)]
    require(bool(scopes), "requested academic scope has no source mapping")
    if request.specializations is not None:
        require(request.specializations <= {row.specialization for row in scopes}, "a requested specialization is missing")
    supported = [row for row in scopes if row.scope_status == "SUPPORTED"]
    blocked = blocked_scope_rows(document, [row for row in scopes if row.scope_status != "SUPPORTED"])
    require(bool(supported), "BLOCKED_MISSING_ACADEMIC_MAPPING: selected scope has no supported teaching demand")
    keys = {(row.academic_year, row.semester, row.stream.value, row.specialization) for row in supported}
    selected = select_enrolled_scope(enrolled, keys, max_time_seconds=request.max_time_seconds)
    bindings = enrolled.batch.source_offerings
    excluded_modules = {row["module_id"] for row in evidence.academic["module_classification_reviews"] if not row["pp1_test_candidate"]}
    modules = unique(evidence.academic["modules"], "record_key")
    differences = []
    for offering in selected.batch.prepared.input_snapshot.module_offerings:
        source = bindings[offering.offering_id]
        key = f"{source['workbook']}:{source['worksheet']}:{source['row']}"
        if modules[key]["id"] in excluded_modules:
            occurrences = sum(row.offering_id == offering.offering_id for row in selected.batch.prepared.sessions)
            if occurrences:
                differences.append(dict(module_code=offering.module_code, source_row=source["row"],
                    stream=offering.stream.value,
                    original_occurrences=occurrences, policy_difference="CURRENT_REVIEW_EXCLUDES_HISTORICAL_TEST_CANDIDATE",
                    historical_policy_preserved=True, new_policy_regeneration_requires_review=True))
    report = dict(status="PASSED", snapshot_semantic_sha256=content_digest(document),
        database_evidence_sha256=evidence.fingerprint(), selected_original_occurrences=len(selected.batch.prepared.sessions),
        original_requirements_preserved=True, module_contact_hours_checked=True, staff_eligibility_checked=True,
        groups_and_approved_allocations_checked=True, elective_attendance_checked=True,
        venue_attributes_checked=True, calendar_and_lunch_replayed=True, preferences_checked=True,
        source_checksums_checked=True, policy_differences=differences)
    return DatabaseGenerationInputs(request, selected, report,
        dict(input_origin="POSTGRESQL_VALIDATED_SNAPSHOT_AND_OWNER_RECORDS", snapshot_id=str(evidence.snapshot_id),
            institution_id=str(evidence.institution_id), calendar_label=document.calendar_label,
            policy_version=request.policy_version, security=evidence.security,
            no_excel_files_read=True, database_read_only=True, semesters_are_separate_alternatives=True), blocked)
