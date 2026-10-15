"""Merge reviewed CSV inputs into a copied submission, then reuse Step 12A.

This module prepares no timetable and writes no files. The reviewed base is
supplied by the technical team; it is never loaded from a synthetic fallback.
"""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from schedence_teaching.integration import (
    InputEvidence, ReadinessDiagnostic, ReadinessReport, SchedulingSubmission, assess_readiness,
)
from schedence_teaching.integration.contracts import expected_owner
from schedence_teaching.integration.diagnostics import covering_evidence
from schedence_teaching.lunch import confirmed_student_lunch_policy
from schedence_teaching.preparation import PreparationError, build_group_hierarchy
from schedence_teaching.schemas import (
    GroupPopulationEvidence, ModuleOffering, SessionRequirement, StaffMember, StudentGroup,
)
from schedence_teaching.workload import StaffWorkProfile, confirmed_campus_policy

from .reader import CellError, boolean, identifiers, integer, read_table, text, windows
from .specification import GROUP_FIELDS, OFFERING_FIELDS, POLICY_KEYS, REQUIREMENT_FIELDS, TABLES


PREFIX = ("combination_input", "teaching_input")


@dataclass(frozen=True)
class PilotValidationResult:
    submission: SchedulingSubmission | None
    readiness: ReadinessReport


class _Builder:
    def __init__(self, base, institution, actual):
        self.submission = SchedulingSubmission.model_validate(deepcopy(base.model_dump()))
        self.raw = self.submission.inputs
        self.institution = UUID(str(institution))
        self.synthetic = not actual
        self.issues = []

    def container(self, path):
        current = self.raw
        for part in path:
            if part not in current:
                current[part] = {}
            if not isinstance(current[part], dict):
                raise ValueError("Base context contains an invalid container.")
            current = current[part]
        return current

    def replace(self, path, value):
        self.container(path[:-1])[path[-1]] = value
        self.submission.evidence = [row for row in self.submission.evidence
                                    if row.path[:len(path)] != path]

    def value(self, row, header, parser=text, **kwargs):
        try:
            return parser(row.values[header], **kwargs)
        except (CellError, ValueError):
            status = "MISSING" if not row.values[header].strip() else "INVALID"
            self.issues.append(row.issue(status, "collection_value", header,
                "Supply this required value in the format stated in the data dictionary."))
            return None

    def evidence(self, path, row):
        try:
            source = row.attestation(self.institution, expected_owner(path[0]), self.synthetic)
        except ValueError:
            missing = next((header for header in ("Confirmation Status", "Confirmation Reference", "Source",
                "Source Reference", "Valid From", "Valid Until") if not row.values[header].strip()), None)
            self.issues.append(row.issue("MISSING" if missing else "INVALID", "collection_provenance", missing,
                "Supply valid confirmation/source references and ordered validity dates; no source values are guessed.", path=path))
            return
        if source.status != "CONFIRMED":
            self.issues.append(row.issue("UNVERIFIED", "collection_confirmation", "Confirmation Status",
                "Obtain confirmation before using this row in the pilot.", path=path))
        if row.table == "policy-confirmations" and row.values["Policy Reference"].strip():
            source = source.model_copy(update={"provenance": source.provenance.model_copy(
                update={"source_record_id": row.values["Policy Reference"]})})
        self.submission.evidence = [item for item in self.submission.evidence if item.path != path]
        self.submission.evidence.append(InputEvidence(**source.model_dump(), path=path))

    def validate_record(self, row, model, record, path):
        try:
            model.model_validate(record)
        except ValidationError as error:
            columns = {item.field: item.header for item in TABLES[row.table]}
            for issue in error.errors(include_input=False, include_context=False):
                self.issues.append(row.issue("MISSING" if issue["type"] == "missing" else "INVALID",
                    issue["type"], columns.get(str(issue["loc"][0])) if issue["loc"] else None,
                    "Correct this record to satisfy the existing Teaching Service contract.", path=path + issue["loc"]))


def _academic(builder, tables, academic_source):
    offerings, groups, requirements, permissions, specializations = [], [], [], [], {}
    source_rows = {row.location: row for row in academic_source.source_modules} if academic_source else {}
    source_ids, source_to_offering = {}, {}
    if academic_source is not None:
        known_staff = {row.staff_id: row for row in academic_source.staff_members}
        for index, staff in enumerate(builder.container(PREFIX).get("staff_members", [])):
            known = known_staff.get(staff.get("staff_id"))
            if known is None or StaffMember.model_validate(staff) != known:
                builder.issues.append(ReadinessDiagnostic("INVALID", "changed_staff_source", PREFIX + ("staff_members", index),
                    "Reuse the preserved staff master attributes; obtain any authoritative correction as a separate source export."))
    for index, row in enumerate(tables["offerings"]):
        record = {field.field: builder.value(row, field.header, integer if field.field in {"academic_year", "semester"} else text)
                  for field in OFFERING_FIELDS if field.field not in {"source_workbook", "source_worksheet", "source_row"}}
        path = PREFIX + ("module_offerings", index)
        builder.validate_record(row, ModuleOffering, record, path)
        if academic_source is not None:
            from schedence_teaching.excel_import.staging import SourceLocation
            location = SourceLocation(builder.value(row, "Source Workbook"), builder.value(row, "Source Worksheet"),
                                      builder.value(row, "Source Row", integer))
            original = source_rows.get(location)
            if original is None:
                builder.issues.append(row.issue("INVALID", "source_module_reference", "Source Row",
                    "Reference an existing inspected source module row; never match on module code alone.", path=path))
            elif location in source_ids:
                builder.issues.append(row.issue("AMBIGUOUS", "duplicate_source_module", "Source Row",
                    "One retained source module row must have one authoritative offering ID in this pilot.", path=path))
            else:
                source_ids[location] = index
                source_to_offering[location] = record["offering_id"]
                for field, value in original.contract_fields.items():
                    if field not in {"offering_id", "stream"} and record.get(field) != value:
                        header = next((item.header for item in OFFERING_FIELDS if item.field == field), None)
                        builder.issues.append(row.issue("INVALID", "changed_source_value", header,
                            "Preserve the existing module reference value; obtain an upstream corrected export separately.", path=path + (field,)))
        offerings.append(record)
    builder.replace(PREFIX + ("module_offerings",), offerings)
    for index, row in enumerate(tables["offerings"]):
        builder.evidence(PREFIX + ("module_offerings", index), row)
    if academic_source is not None:
        # Rebind accepted assignments by exact source-row identity, keeping every
        # original staff ID and role for each selected offering. No reassignment.
        teams, team_rows = [], []
        for assignment in academic_source.source_assignments:
            if assignment.location in source_to_offering:
                teams.append(dict(offering_id=source_to_offering[assignment.location],
                    staff_id=assignment.source_staff_id, role=assignment.role))
                team_rows.append(tables["offerings"][source_ids[assignment.location]])
        builder.replace(PREFIX + ("teaching_team_members",), teams)
        for index, row in enumerate(team_rows):
            builder.evidence(PREFIX + ("teaching_team_members", index), row)
    for index, row in enumerate(tables["student-groups"]):
        record = {field.field: builder.value(row, field.header,
            integer if field.field in {"academic_year", "semester", "student_count"} else text,
            optional=field.field == "parent_group_id") for field in GROUP_FIELDS if field.field != "specialization"}
        path = PREFIX + ("student_groups", index)
        builder.validate_record(row, StudentGroup, record, path)
        if record["group_id"] is not None:
            specializations[record["group_id"]] = builder.value(row, "Specialization")
        groups.append(record)
    builder.replace(PREFIX + ("student_groups",), groups)
    builder.replace(("combination_input", "group_specializations"), specializations)
    for index, row in enumerate(tables["student-groups"]):
        builder.evidence(PREFIX + ("student_groups", index), row)
        if groups[index]["group_id"] is not None:
            builder.evidence(("combination_input", "group_specializations", groups[index]["group_id"]), row)
    try:
        build_group_hierarchy([StudentGroup.model_validate(record) for record in groups])
    except PreparationError:
        for index, row in enumerate(tables["student-groups"]):
            if groups[index]["parent_group_id"] is not None:
                builder.issues.append(row.issue("INVALID", "group_hierarchy", "Parent Group ID",
                    "Review parent existence, cycles and matching year/semester/stream using the supplied hierarchy.",
                    path=PREFIX + ("student_groups", index, "parent_group_id")))
    except ValidationError:
        pass  # Individual record diagnostics already identify these cells.
    for index, row in enumerate(tables["session-requirements"]):
        record = {field.field: builder.value(row, field.header,
            integer if field.field in {"sessions_per_week", "duration_minutes"} else text)
            for field in REQUIREMENT_FIELDS if field.field not in {"shared_content_reference", "allow_combination"}}
        builder.validate_record(row, SessionRequirement, record, PREFIX + ("session_requirements", index))
        requirements.append(record)
        permissions.append(dict(requirement_id=record["requirement_id"],
            shared_content_reference=builder.value(row, "Teaching Content Reference", optional=True),
            allow_combination=builder.value(row, "Combination Allowed", boolean, optional=True)))
    builder.replace(PREFIX + ("session_requirements",), requirements)
    builder.replace(("combination_input", "requirement_permissions"), permissions)
    for index, row in enumerate(tables["session-requirements"]):
        builder.evidence(PREFIX + ("session_requirements", index), row)
        builder.evidence(("combination_input", "requirement_permissions", index), row)
    population = []
    for index, row in enumerate(tables["population-evidence"]):
        record = dict(group_ids=builder.value(row, "Group IDs", identifiers), relationship=builder.value(row, "Relationship"),
                      evidence_reference=builder.value(row, "Population Evidence Reference"))
        builder.validate_record(row, GroupPopulationEvidence, record, ("combination_input", "population_evidence", index))
        population.append(record)
    builder.replace(("combination_input", "population_evidence"), population)
    for index, row in enumerate(tables["population-evidence"]):
        builder.evidence(("combination_input", "population_evidence", index), row)


def _staff(builder, tables):
    crosswalk, profiles = {}, []
    original_staff = builder.container(PREFIX).get("staff_members", [])
    staff_indices = {row.get("staff_id"): index for index, row in enumerate(original_staff)}
    for index, row in enumerate(tables["staff-crosswalk"]):
        identity = builder.value(row, "Staff ID")
        if identity in crosswalk:
            builder.issues.append(row.issue("AMBIGUOUS", "duplicate_staff_crosswalk", "Staff ID", "Supply one cross-reference per staff member."))
        if identity not in staff_indices:
            builder.issues.append(row.issue("INVALID", "unknown_staff", "Staff ID", "Reference a preserved staff member in the reviewed base submission."))
        if identity is not None:
            crosswalk[identity] = builder.value(row, "Academic Staff UUID", lambda value: str(UUID(text(value))))
        profile = dict(staff_id=identity, workload_band=builder.value(row, "Workload Band"),
            appointment=builder.value(row, "Appointment", optional=True), evidence_reference=builder.value(row, "Source Reference"))
        builder.validate_record(row, StaffWorkProfile, profile, ("staff_input", "workload_input", "profiles", index))
        profiles.append(profile)
    builder.replace(("academic_staff_references",), crosswalk)
    builder.replace(("staff_input", "workload_input", "profiles"), profiles)
    for index, row in enumerate(tables["staff-crosswalk"]):
        identity = profiles[index]["staff_id"]
        if identity is not None:
            builder.evidence(("academic_staff_references", identity), row)
            if identity in staff_indices:
                builder.evidence(PREFIX + ("staff_members", staff_indices[identity]), row)
        builder.evidence(("staff_input", "workload_input", "profiles", index), row)


def _calendars(builder, table, *, staff):
    key, root = ("Staff ID", "staff_input") if staff else ("Venue ID", "venue_input")
    groups = defaultdict(list)
    week = builder.raw.get("calendar", {}).get("week_reference")
    days = {row["day"] for row in builder.raw.get("solver_config", {}).get("days", [])}
    for row in table:
        identity = builder.value(row, key)
        if identity is not None:
            groups[identity].append(row)
        if builder.value(row, "Academic Week") != week:
            builder.issues.append(row.issue("INVALID", "academic_week", "Academic Week", "Use the reviewed base's actual academic week reference."))
    records, origins = [], []
    old_staff = {row["staff_id"]: row for row in builder.raw.get("staff_input", {}).get("availability", [])}
    cap_sources = {}
    if staff:
        for index, record in enumerate(builder.raw.get("staff_input", {}).get("availability", [])):
            for field in ("daily_teaching_max_minutes", "weekly_teaching_max_minutes"):
                if record.get(field) is not None:
                    cap_sources[record["staff_id"], field] = covering_evidence(builder.submission.evidence,
                        ("staff_input", "availability", index, field))
    for identity, rows in groups.items():
        available, blocked, allowed, supplied_days = {}, [], [], set()
        for row in rows:
            day = builder.value(row, "Day ID")
            if day in supplied_days:
                builder.issues.append(row.issue("AMBIGUOUS", "duplicate_calendar_day", "Day ID", "Supply one row per entity/day; put multiple windows in the same cell."))
            if day not in days:
                builder.issues.append(row.issue("INVALID", "calendar_day", "Day ID", "Reference a day in the reviewed academic-week calendar."))
            supplied_days.add(day)
            windows_value = builder.value(row, "Available Windows", windows)
            blocks = builder.value(row, "Blocked Periods", windows)
            permits = builder.value(row, "Teaching Allowed", boolean) if staff else True
            if staff and permits is False and windows_value:
                builder.issues.append(row.issue("INVALID", "disallowed_day_windows", "Available Windows", "A disallowed teaching day requires NONE available windows."))
            if day is not None and permits:
                available[day] = windows_value
                allowed.append(day)
            blocked.extend(dict(day=day, **item) for item in blocks or [])
        if supplied_days != days:
            builder.issues.append(rows[0].issue("MISSING", "calendar_coverage", "Day ID", "Declare every configured day, including closed days and explicit NONE blocks."))
        record = dict(available_windows=available, blocked_periods=blocked)
        record["staff_id" if staff else "venue_id"] = identity
        if staff:
            record.update(allowed_teaching_days=allowed, effective_from=rows[0].values["Valid From"], effective_until=rows[0].values["Valid Until"])
            for row in rows:
                if (row.values["Valid From"], row.values["Valid Until"]) != (rows[0].values["Valid From"], rows[0].values["Valid Until"]):
                    builder.issues.append(row.issue("INVALID", "calendar_validity", "Valid From", "Use one explicitly confirmed applicability interval per staff/week export."))
            # Existing explicit caps are retained; targets are not converted to caps.
            for field in ("daily_teaching_max_minutes", "weekly_teaching_max_minutes"):
                if field in old_staff.get(identity, {}):
                    record[field] = old_staff[identity][field]
        records.append(record)
        origins.append(rows)
    path = (root, "availability" if staff else "calendars")
    builder.replace(path, records)
    for index, rows in enumerate(origins):
        builder.evidence(path + (index,), rows[0])
        for row in rows:
            day = row.values["Day ID"]
            if day in records[index]["available_windows"]:
                builder.evidence(path + (index, "available_windows", day), row)
            else:
                # Even a disallowed row's confirmation must be checked.
                builder.evidence(path + (index,), row)
        if staff:
            for field in ("daily_teaching_max_minutes", "weekly_teaching_max_minutes"):
                sources = cap_sources.get((records[index]["staff_id"], field), [])
                if len(sources) == 1:
                    builder.submission.evidence.append(InputEvidence(**{
                        **sources[0].model_dump(), "path": path + (index, field)}))
                elif records[index].get(field) is not None:
                    builder.issues.append(rows[0].issue("UNVERIFIED" if not sources else "AMBIGUOUS", "teaching_cap_source", None,
                        "Retain explicit caps with their original source evidence; availability approval does not invent cap policy.", path=path + (index, field)))


def _relationships(builder, rows):
    combined, shared, combined_rows, shared_rows = [], [], [], []
    for row in rows:
        kind = builder.value(row, "Relationship Type")
        if kind == "COMBINED":
            combined.append(dict(combined_venue_id=builder.value(row, "Combined Venue ID"),
                component_venue_ids=builder.value(row, "Component Venue IDs", identifiers)))
            combined_rows.append(row)
        elif kind == "SHARED":
            shared.append(dict(resource_id=builder.value(row, "Shared Resource ID"),
                venue_ids=builder.value(row, "Venue IDs", identifiers)))
            shared_rows.append(row)
        else:
            builder.issues.append(row.issue("INVALID", "relationship_type", "Relationship Type", "Use COMBINED or SHARED with explicit existing venue references."))
    if rows:
        for field, values, sources in (("combined_venues", combined, combined_rows), ("shared_resources", shared, shared_rows)):
            builder.replace(("venue_input", field), values)
            for index, row in enumerate(sources):
                builder.evidence(("venue_input", field, index), row)
        venue = builder.container(("venue_input",))
        resolved = {record["combined_venue_id"] for record in combined}
        venue["unresolved_venue_ids"] = [identity for identity in venue.get("unresolved_venue_ids", []) if identity not in resolved]


def _preference_and_fairness_details(builder, tables):
    preferences = builder.raw.get("preference_input", {}).get("preferences", [])
    indices = {row.get("preference_id"): index for index, row in enumerate(preferences)}
    seen = set()
    for row in tables["preference-approvals"]:
        identity = builder.value(row, "Preference ID")
        if identity not in indices or identity in seen:
            builder.issues.append(row.issue("AMBIGUOUS" if identity in seen else "INVALID", "preference_reference",
                "Preference ID", "Reference one retained staged/base preference per approval row; no preferences are generated."))
            continue
        seen.add(identity)
        index = indices[identity]
        original = preferences[index]
        if builder.value(row, "Staff ID") != original.get("staff_id"):
            builder.issues.append(row.issue("INVALID", "preference_staff", "Staff ID", "Retain the preference's existing staff ID."))
        if builder.value(row, "Academic Week") != builder.raw.get("calendar", {}).get("week_reference"):
            builder.issues.append(row.issue("INVALID", "academic_week", "Academic Week", "Use the actual requested academic week."))
        original.update(penalty_weight=builder.value(row, "Penalty Weight", integer),
            weight_reference=builder.value(row, "Weight Reference"), stream=builder.value(row, "Stream"),
            scope=dict(week_reference=row.values["Academic Week"]), confirmation_status=row.values["Confirmation Status"])
        builder.evidence(("preference_input", "preferences", index), row)
    windows_by_label, references = {}, set()
    for row in tables["time-of-day-windows"]:
        label = builder.value(row, "Day Part")
        if label not in {"MORNING", "AFTERNOON"} or label in windows_by_label:
            builder.issues.append(row.issue("INVALID", "day_part", "Day Part", "Supply one explicitly approved window per known label."))
            continue
        periods = builder.value(row, "Time Window", windows)
        if periods is None or len(periods) != 1:
            builder.issues.append(row.issue("MISSING", "day_part_window", "Time Window", "Supply exactly one numeric interval; no morning/afternoon boundary is inferred."))
            continue
        windows_by_label[label] = periods[0]
        references.add(builder.value(row, "Policy Reference"))
    if tables["time-of-day-windows"]:
        if len(references) != 1 or None in references:
            builder.issues.append(ReadinessDiagnostic("AMBIGUOUS", "day_part_policy", ("preference_policy",),
                "Use one approved numeric day-part policy reference."))
        else:
            policy = builder.container(("preference_policy",))
            policy.update(time_of_day_windows=windows_by_label, time_window_policy_reference=next(iter(references)))
            for row in tables["time-of-day-windows"]:
                if row.values["Day Part"] in windows_by_label:
                    builder.evidence(("preference_policy", "time_of_day_windows", row.values["Day Part"]), row)
    if tables["fairness-cohorts"]:
        cohorts = []
        for row in tables["fairness-cohorts"]:
            cohorts.append(dict(group_id=builder.value(row, "Cohort ID"), staff_ids=builder.value(row, "Staff IDs", identifiers),
                                reason=builder.value(row, "Reason")))
            if builder.value(row, "Policy Reference") != builder.raw.get("fairness_policy", {}).get("policy_reference"):
                builder.issues.append(row.issue("INVALID", "policy_reference", "Policy Reference", "Use the reviewed fairness policy reference."))
        builder.replace(("fairness_policy", "comparison_groups"), cohorts)
        builder.container(("fairness_policy",))["comparison_scope"] = "EXPLICIT_GROUPS"
        for index, row in enumerate(tables["fairness-cohorts"]):
            builder.evidence(("fairness_policy", "comparison_groups", index), row)


def _policies(builder, rows):
    paths = dict(workload=[("staff_input", "workload_input", "policy")],
        preferences=[("preference_input",), ("preference_policy",), ("preference_config",)],
        fairness=[("fairness_policy",), ("fairness_config",)],
        administration_research=[("work_period_input",)],
        combination=[("combination_input", "policy")],
        consecutive=[("consecutive_policy",), ("consecutive_config",)],
        venue_relationships=[("venue_input", "combined_venues"), ("venue_input", "shared_resources"), ("venue_input", "unresolved_venue_ids")])
    seen = set()
    for row in rows:
        key = builder.value(row, "Policy Key")
        if key not in POLICY_KEYS or key in seen:
            builder.issues.append(row.issue("AMBIGUOUS" if key in seen else "INVALID", "policy_key", "Policy Key", "Supply exactly one row for each documented policy key."))
            continue
        seen.add(key)
        decision = builder.value(row, "Decision")
        reference = builder.value(row, "Policy Reference")
        primary = {"workload": builder.raw.get("staff_input", {}).get("workload_input", {}).get("policy", {}),
            "preferences": builder.raw.get("preference_policy", {}), "fairness": builder.raw.get("fairness_policy", {}),
            "consecutive": builder.raw.get("consecutive_policy", {})}.get(key)
        if primary is not None and reference != primary.get("policy_reference"):
            builder.issues.append(row.issue("INVALID", "policy_reference", "Policy Reference",
                "Confirm the exact policy reference present in the reviewed technical base; do not substitute an unrelated policy."))
        valid = {"combination": {"ALLOW", "DISALLOW"}, "preferences": {"APPLY", "NONE"},
                 "administration_research": {"APPLY", "NONE"}, "venue_relationships": {"APPLY", "NONE"}}.get(key, {"APPLY"})
        if decision not in valid:
            builder.issues.append(row.issue("UNVERIFIED", "policy_decision", "Decision", "Obtain an explicit applicable decision; this profile cannot infer or silently disable policies."))
            continue
        if key == "student_lunch":
            dates = builder.value(row, "Applicable Dates", identifiers, allow_none=True)
            streams = builder.value(row, "Applicable Streams", identifiers, allow_none=True)
            try:
                policy = confirmed_student_lunch_policy(policy_reference=reference,
                    week_reference=builder.raw.get("calendar", {}).get("week_reference"),
                    provenance=dict(source=builder.value(row, "Source"), reference=builder.value(row, "Source Reference")),
                    applicable_dates=[date.fromisoformat(item) for item in dates] if dates is not None else None,
                    applicable_streams=streams, applicability_reference=builder.value(row, "Confirmation Reference"))
                builder.replace(("student_lunch_policy",), policy.model_dump(mode="json"))
                config = builder.container(("student_lunch_config",))
                config.update(applicability_confirmation="CONFIRMED",
                              applicability_confirmation_reference=row.values["Confirmation Reference"])
            except ValueError:
                builder.issues.append(row.issue("INVALID", "lunch_scope", "Applicable Dates", "Supply valid explicit dates/streams and confirmation references for the fixed periods."))
            for path in (("student_lunch_policy",), ("student_lunch_config",)):
                builder.evidence(path, row)
        elif key == "combination":
            builder.container(("combination_input", "policy"))["allow_combination"] = decision == "ALLOW"
        if key != "student_lunch":
            for path in paths[key]:
                parent = builder.container(path[:-1])
                if path[-1] not in parent:
                    builder.issues.append(row.issue("MISSING", "reviewed_policy_context", "Policy Reference",
                        "The technical base must contain the reviewed existing contract referenced by this confirmation.", path=path))
                else:
                    builder.evidence(path, row)
        if decision == "NONE":
            if key == "preferences":
                values = builder.raw.get("preference_input", {}).get("preferences", [])
            elif key == "venue_relationships":
                values = builder.raw.get("venue_input", {}).get("combined_venues", []) + builder.raw.get("venue_input", {}).get("shared_resources", [])
            else:
                periods = builder.raw.get("work_period_input", {})
                values = [record for field in ("appointments", "administrative_resolutions", "administrative_periods", "research_policies",
                    "research_reservations", "flexible_policies", "flexible_requests") for record in periods.get(field, [])]
            if values:
                builder.issues.append(row.issue("INVALID", "policy_none_conflict", "Decision", "NONE cannot discard existing applicable records; review the supplied context explicitly."))
    if set(POLICY_KEYS) - seen:
        builder.issues.append(ReadinessDiagnostic("MISSING", "policy_rows", ("policies",),
            "Complete all eight applicability/approval rows; known policy values need not be resubmitted.", "policy-confirmations.csv", "CSV", 1))
    # Keep the owner-confirmed values separate; do not accept legacy combined
    # teaching/admin semantics in an actual pilot or convert targets to caps.
    policy = builder.raw.get("staff_input", {}).get("workload_input", {}).get("policy")
    if policy is not None:
        from schedence_teaching.workload import WorkloadPolicy
        try:
            typed = WorkloadPolicy.model_validate(policy)
            confirmed = confirmed_campus_policy()
            if typed.target_basis != confirmed.target_basis or typed.band_rules != confirmed.band_rules or typed.appointment_overrides != confirmed.appointment_overrides:
                raise ValueError("Different campus policy values.")
        except ValueError:
            builder.issues.append(ReadinessDiagnostic("INVALID", "confirmed_workload_values", ("staff_input", "workload_input", "policy"),
                "Retain the confirmed separate teaching targets, administrative allocations and attendance rules."))


def validate_collection_pack(directory: Path, base: SchedulingSubmission, *, expected_institution_id: UUID,
        actual_campus: bool = True, academic_source=None) -> PilotValidationResult:
    """Read all templates and call the existing readiness gate; never call CP-SAT.

    base supplies existing staff/venue records, institution/week/time semantics,
    eligibility and fully reviewed policy contracts. CSV confirmations do not
    invent weights, cohorts, appointments, calendars or policy JSON content.
    """
    if type(actual_campus) is not bool:
        raise TypeError("actual_campus must be an explicit boolean")
    builder = _Builder(base, expected_institution_id, actual_campus)
    tables = {}
    for table in TABLES:
        tables[table], issues = read_table(Path(directory), table)
        builder.issues.extend(issues)
    try:
        _academic(builder, tables, academic_source)
        _staff(builder, tables)
        _calendars(builder, tables["staff-availability"], staff=True)
        _calendars(builder, tables["venue-calendars"], staff=False)
        _relationships(builder, tables["venue-relationships"])
        _preference_and_fairness_details(builder, tables)
        _policies(builder, tables["policy-confirmations"])
        assessment = assess_readiness(builder.submission, expected_institution_id=expected_institution_id, actual_campus=actual_campus)
        report = ReadinessReport(assessment.report.mode, tuple(builder.issues) + assessment.report.diagnostics)
    except (ValueError, TypeError, KeyError):
        builder.issues.append(ReadinessDiagnostic("INVALID", "base_context", ("base_submission",),
            "Supply structurally valid reviewed context and use the documented CSV types; no source values were substituted."))
        report = ReadinessReport(None, tuple(builder.issues))
    return PilotValidationResult(builder.submission if report.ready_for_generation else None, report)
