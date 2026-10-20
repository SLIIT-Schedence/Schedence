"""Independent source-to-demand reconciliation and necessary calendar bounds.

These bounds prove time shortages, not resource feasibility when time fits.
No staff names, invented availability, or source-workbook corrections enter them.
"""

from collections import Counter, defaultdict
import csv
import json

from schedence_teaching.schemas import SessionType

from .academic import CONTACT_HOUR_COLUMNS, extract_academic_catalog
from .preview import _new_directory


def group_scope(batch, group):
    return (group.academic_year, group.semester, group.stream.value,
            batch.group_specializations[group.group_id])


def _available_minutes(batch, group):
    policy = batch.solver_inputs["student_lunch_policy"]
    if policy.applicable_dates is None or policy.applicable_streams is None:
        raise ValueError("Attendance capacity needs explicit lunch date and stream applicability.")
    lunch = next(row.window for row in policy.year_periods if row.academic_year == group.academic_year)
    available = 0
    for day in batch.config.days:
        intervals = sorted((row.start_minute, row.end_minute) for row in day.windows.get(group.stream, ()))
        merged = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        applies = (batch.configuration.calendar.day_dates[day.day] in policy.applicable_dates
                   and group.stream in policy.applicable_streams)
        available += sum(end - start - (max(0, min(end, lunch.end_minute) - max(start, lunch.start_minute))
                                      if applies else 0) for start, end in merged)
    return available


def audit_demand(batch, academic):
    """Rebuild expected audiences/hours from source, independently of expansion.

    Exact source-row identity preserves conflicting codes. A duplicate academic
    row is flagged for review, never silently removed. Every occurrence is also
    checked against its requirement, including stable ordinal and duration.
    """
    catalog = extract_academic_catalog(academic)
    if not catalog.valid:
        raise ValueError("Source catalog must pass validation before demand audit.")
    teaching = batch.prepared.input_snapshot
    groups = {row.group_id: row for row in batch.plan.groups}
    modules = {(row.source.location.workbook, row.source.location.worksheet, row.source.location.row): row
               for row in catalog.modules}
    offerings = {row.offering_id: row for row in teaching.module_offerings}
    issues, expected, actual, details = [], {}, Counter(), []
    fingerprints = defaultdict(list)
    for module in catalog.modules:
        fields = module.source.contract_fields
        fingerprints[(fields["module_code"], fields["module_name"], fields["academic_year"],
                      fields["semester"], tuple(sorted(module.specializations)))].append(module.source.location.row)
    for rows in fingerprints.values():
        if len(rows) > 1:
            issues.append(dict(code="DUPLICATE_SOURCE_MODULE_RECORD", source_rows=rows))
    binding_scopes = set()
    for identity, offering in offerings.items():
        location = batch.source_offerings[identity]
        key = (location["workbook"], location["worksheet"], location["row"])
        if key not in modules:
            issues.append(dict(code="MISSING_SOURCE_MODULE", offering_id=identity))
            continue
        module = modules[key]
        fields = module.source.contract_fields
        if any(getattr(offering, name) != fields[name] for name in (
                "module_code", "module_name", "academic_year", "semester", "specialization", "degree_type")):
            issues.append(dict(code="ALTERED_SOURCE_MODULE", source_row=key[2]))
        binding = (key, offering.stream)
        if binding in binding_scopes:
            issues.append(dict(code="DUPLICATE_SOURCE_BINDING", source_row=key[2], stream=offering.stream.value))
        binding_scopes.add(binding)
        for group in groups.values():
            if (group.academic_year, group.semester, group.stream) != (fields["academic_year"], fields["semester"], offering.stream):
                continue
            if batch.group_specializations[group.group_id] not in module.specializations:
                continue
            for kind, minutes in module.weekly_minutes.items():
                main = batch.configuration.session_audiences[kind] == "MAIN"
                if minutes and (group.parent_group_id is None) == main:
                    expected[(identity, group.group_id, kind)] = minutes
    # A dropped binding must also be detected, rather than comparing only what
    # the importer happened to generate.
    for key, module in modules.items():
        fields = module.source.contract_fields
        for group in groups.values():
            if (group.academic_year, group.semester) == (fields["academic_year"], fields["semester"]) and batch.group_specializations[group.group_id] in module.specializations:
                if (key, group.stream) not in binding_scopes:
                    issues.append(dict(code="OMITTED_SOURCE_BINDING", source_row=key[2], stream=group.stream.value))
                    break
    for requirement in teaching.session_requirements:
        key = (requirement.offering_id, requirement.group_id, requirement.session_type)
        actual[key] += 1
        minutes = expected.get(key)
        if minutes is None or requirement.duration_minutes != minutes or requirement.sessions_per_week != 1:
            issues.append(dict(code="REQUIREMENT_SOURCE_MISMATCH", requirement_id=requirement.requirement_id))
        offering = offerings[requirement.offering_id]
        location = batch.source_offerings[requirement.offering_id]
        module = modules[(location["workbook"], location["worksheet"], location["row"])]
        group = groups[requirement.group_id]
        details.append(dict(source_workbook=location["workbook"], source_worksheet=location["worksheet"], source_row=location["row"],
            module_code=offering.module_code, module_name=offering.module_name,
            academic_year=offering.academic_year, semester=offering.semester, stream=offering.stream.value,
            specialization=batch.group_specializations[group.group_id], source_specialization=offering.specialization,
            **{column: module.source.source_values[column] for column in CONTACT_HOUR_COLUMNS.values()},
            requirement_id=requirement.requirement_id, group_id=group.group_id,
            audience="MAIN" if group.parent_group_id is None else "LAB_SUBGROUP", student_count=group.student_count,
            session_type=requirement.session_type.value, duration_minutes=requirement.duration_minutes,
            sessions_per_week=requirement.sessions_per_week))
    for key in expected.keys() | actual.keys():
        if actual[key] != 1:
            issues.append(dict(code="MISSING_OR_DUPLICATE_REQUIREMENT", offering_id=key[0], group_id=key[1], session_type=key[2].value, count=actual[key]))
    wanted = Counter((f"{row.requirement_id}:{ordinal}", row.requirement_id, row.offering_id, row.group_id,
                      row.session_type, row.duration_minutes, groups[row.group_id].stream)
                     for row in teaching.session_requirements for ordinal in range(1, row.sessions_per_week + 1))
    found = Counter((row.occurrence_id, row.requirement_id, row.offering_id, row.group_id,
                     row.session_type, row.duration_minutes, row.stream) for row in batch.prepared.sessions)
    if wanted != found:
        issues.append(dict(code="OCCURRENCE_EXPANSION_MISMATCH", missing=sum((wanted-found).values()), extra=sum((found-wanted).values())))
    by_group = defaultdict(list)
    for row in batch.prepared.sessions:
        by_group[row.group_id].append(row)
    leaves, contributions = [], []
    for group in groups.values():
        if batch.prepared.hierarchy.descendants(group.group_id):
            continue
        ancestors, current = set(), group.group_id
        while current is not None:
            ancestors.add(current)
            current = batch.prepared.hierarchy.parents[current]
        attended = [row for identity in ancestors for row in by_group[identity]]
        required = sum(row.duration_minutes for row in attended)
        source_minutes = sum(sum(row.weekly_minutes.values()) for row in catalog.modules
            if (row.source.contract_fields["academic_year"], row.source.contract_fields["semester"]) == (group.academic_year, group.semester)
            and batch.group_specializations[group.group_id] in row.specializations)
        if required != source_minutes:
            issues.append(dict(code="LEAF_SOURCE_ATTENDANCE_MISMATCH", group_id=group.group_id, required_minutes=required, source_minutes=source_minutes))
        available = _available_minutes(batch, group)
        leaf = dict(academic_year=group.academic_year, semester=group.semester, stream=group.stream.value,
            specialization=batch.group_specializations[group.group_id], group_id=group.group_id,
            student_count=group.student_count, required_minutes=required, source_minutes=source_minutes,
            available_minutes=available, shortfall_minutes=max(0, required-available),
            required_hours=required/60, available_hours=available/60, minimum_additional_teaching_hours=max(0, required-available)/60,
            session_count=len(attended))
        leaves.append(leaf)
        for row in attended:
            offering = offerings[row.offering_id]
            contributions.append(dict(leaf_group_id=group.group_id, occurrence_id=row.occurrence_id,
                attended_group_id=row.group_id, source_row=batch.source_offerings[row.offering_id]["row"],
                module_code=offering.module_code, module_name=offering.module_name,
                session_type=row.session_type.value, duration_minutes=row.duration_minutes))
    # Classification is withheld if reconciliation fails anywhere.
    for row in leaves:
        row["classification"] = ("SOURCE_OR_EXPANSION_REVIEW_REQUIRED" if issues else
            "PROVEN_CALENDAR_INFEASIBLE" if row["shortfall_minutes"] else "TIME_BOUND_FITS_NOT_A_RESOURCE_FEASIBILITY_PROOF")
    scopes = []
    buckets = defaultdict(list)
    for row in leaves:
        buckets[tuple(row[key] for key in ("academic_year", "semester", "stream", "specialization"))].append(row)
    for scope, rows in sorted(buckets.items()):
        scopes.append(dict(zip(("academic_year", "semester", "stream", "specialization"), scope)) | dict(
            leaf_group_count=len(rows), required_hours=max(row["required_hours"] for row in rows),
            available_hours=min(row["available_hours"] for row in rows),
            minimum_additional_teaching_hours=max(row["minimum_additional_teaching_hours"] for row in rows),
            classification=rows[0]["classification"]))
    return dict(output_label="PP1_TEST_CALENDAR", source_reconciliation_passed=not issues, issues=issues,
        source_module_rows=len(catalog.modules), requirement_count=len(details), occurrence_count=len(batch.prepared.sessions),
        leaf_count=len(leaves), overloaded_leaf_count=sum(row["shortfall_minutes"] > 0 for row in leaves),
        overloaded_scope_count=sum(row["minimum_additional_teaching_hours"] > 0 for row in scopes),
        module_demand=details, leaf_attendance=leaves, scope_capacity=scopes, leaf_contributions=contributions,
        scenario_note="Additional hours are necessary lower bounds, not permission to extend the calendar or a guarantee of resource feasibility.")


def _csv(path, rows):
    if not rows:
        raise ValueError("Audit CSV needs explicit nonempty records.")
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def export_demand_audit(audit, directory):
    directory = _new_directory(directory)
    for name, rows in (("module-demand-audit.csv", audit["module_demand"]),
                       ("leaf-attendance.csv", audit["leaf_attendance"]),
                       ("scope-capacity.csv", audit["scope_capacity"]),
                       ("leaf-session-contributions.csv", audit["leaf_contributions"])):
        _csv(directory / name, rows)
    # Always include headers, including a zero-overload fixture.
    overloads = [row for row in audit["leaf_attendance"] if row["shortfall_minutes"]]
    with (directory / "weekend-overloads.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(audit["leaf_attendance"][0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(row for row in overloads if row["stream"] == "WEEKEND")
    _csv(directory / "calendar-scenario-comparison.csv", [dict(scenario="BASELINE_UNCHANGED_MINIMUM_EXTRA_HOURS_ONLY", **row) for row in audit["scope_capacity"]])
    (directory / "demand-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return directory
