"""Explicit mappings from already-validated import results to owner records."""

import json

from .master_import import MasterRecord as Row, RecordLink as Link
from .snapshots import canonical_json


def json_values(value):
    return json.loads(canonical_json(value))


def academic_records(academic, catalog, original, allocations, reviews, sources):
    rows = []
    source_keys = {source.label: f"{source.label}:{source.sha256}" for source in sources}
    for source in sources:
        rows.append(Row("source_datasets", source_keys[source.label], dict(filename=source.label,
            sha256=source.sha256, provenance_kind=source.provenance_kind.value, provenance=source.provenance)))
    def source(name): return Link("source_datasets", source_keys[name])
    staff_source = academic.source_staff[0].location.workbook
    allocation_source = allocations.cohorts[0].source.workbook
    original_source = original.cohorts[0].source.workbook
    review_source = next(name for name in source_keys if "Classification_Review" in name)
    departments = sorted({member.department for member in academic.staff_members if member.department is not None})
    for name in departments:
        rows.append(Row("departments", name, dict(name=name, source_values={"Department": name}),
            {"source_id": source(staff_source)}))
    for member, record in zip(academic.staff_members, academic.source_staff, strict=True):
        if record.source_values["Staff_ID"] != member.staff_id:
            raise ValueError("staff records must retain validated source order")
        references = {"source_id": source(record.location.workbook)}
        if member.department is not None:
            references["department_id"] = Link("departments", member.department)
        rows.append(Row("staff_teaching_profiles", member.staff_id, dict(staff_number=member.staff_id,
            staff_type=member.staff_type.value, source_position=member.position, department_id=None,
            source_row=record.location.row, source_values=json_values(record.source_values)), references))
    for code, name in allocations.specialization_names.items():
        rows.append(Row("specializations", code, dict(code=code, name=name,
            source_values={"Code": code, "Specialization": name,
                "names_source": "user-supplied PP1 allocation workbook", "academic_applicability_source": "module workbook"}),
            {"source_id": source(allocation_source)}))
    module_keys = {}
    for module in catalog.modules:
        record, fields = module.source, module.source.contract_fields
        key = f"{record.location.workbook}:{record.location.worksheet}:{record.location.row}"
        module_keys[record.location.row] = key
        rows.append(Row("modules", key, dict(module_code=fields["module_code"], module_name=fields["module_name"],
            academic_year=fields["academic_year"], semester=fields["semester"], degree_type=fields["degree_type"],
            **{f"{kind.value.lower()}_minutes": minutes for kind, minutes in module.weekly_minutes.items()},
            worksheet=record.location.worksheet, source_row=record.location.row, source_values=json_values(record.source_values)),
            {"source_id": source(record.location.workbook)}))
    for module in catalog.modules:
        key = module_keys[module.source.location.row]
        for code in module.specializations:
            rows.append(Row("module_specializations", f"{key}:{code}", {},
                {"module_id": Link("modules", key), "specialization_id": Link("specializations", code)}))
    for record in academic.source_assignments:
        key = module_keys[record.location.row]
        if not record.valid_reference:
            raise ValueError("invalid staff assignment cannot be imported")
        rows.append(Row("module_teaching_teams", f"{key}:{record.id_column}",
            dict(staff_number=record.source_staff_id, source_slot=record.id_column, role=record.role.value,
                source_values={record.id_column: record.source_staff_id, record.name_column: record.source_display_name}),
            {"module_id": Link("modules", key)}))
    for record in academic.source_preferences:
        rows.append(Row("lecturer_preferences", f"{record.location.workbook}:{record.location.worksheet}:{record.location.row}",
            dict(staff_number=record.source_values["Staff_ID"], worksheet=record.location.worksheet,
                source_row=record.location.row, source_values=json_values(record.source_values)),
            {"source_id": source(record.location.workbook)}))
    by_scope = {}
    for cohort in original.cohorts:
        key = cohort.cohort_id
        by_scope[(cohort.academic_year, cohort.semester, cohort.stream)] = key
        rows.append(Row("cohort_totals", key, dict(cohort_reference=key, academic_year=cohort.academic_year,
            semester=cohort.semester, stream=cohort.stream.value, student_count=cohort.student_count,
            source_values=json_values(cohort)), {"source_id": source(original_source)}))
    for cohort in allocations.cohorts:
        rows.append(Row("pp1_specialization_allocations", cohort.cohort_id, dict(local_cohort_reference=cohort.cohort_id,
            student_count=cohort.student_count, source_values=dict(cohort=json_values(cohort),
                official_individual_enrollment=False, original_aggregate_totals_preserved=True)),
            {"source_id": source(allocation_source),
             "cohort_total_id": Link("cohort_totals", by_scope[(cohort.academic_year, cohort.semester, cohort.stream)]),
             "specialization_id": Link("specializations", cohort.specialization)}))
    plan = allocations.groups()
    for group in sorted(plan.groups, key=lambda row: (row.parent_group_id is not None, row.group_id)):
        refs = {"allocation_id": Link("pp1_specialization_allocations", plan.cohort_by_group[group.group_id])}
        if group.parent_group_id is not None:
            refs["parent_id"] = Link("pp1_student_groups", group.parent_group_id)
        rows.append(Row("pp1_student_groups", group.group_id, dict(plan_reference="PP1_PROPORTIONAL_BASE_ALLOCATION",
            local_group_reference=group.group_id, parent_id=None, student_count=group.student_count,
            source_values=dict(group=group.model_dump(mode="json"), sizing=allocations.sizing.model_dump(mode="json"),
                official_university_group_id=False)), refs))
    for review in reviews:
        rows.append(Row("module_classification_reviews", f"{source_keys[review_source]}:{review.source_row}",
            dict(academic_classification=review.academic_classification, pp1_test_candidate=review.pp1_test_candidate,
                source_values=json_values(review.source_values)),
            {"source_id": source(review_source), "module_id": Link("modules", module_keys[review.source_row])}))
    return tuple(rows)


def resource_records(venues, sources, corrections):
    rows, keys = [], {}
    for source in sources:
        key = f"{source.label}:{source.sha256}"
        keys[source.label] = key
        rows.append(Row("source_datasets", key, dict(filename=source.label, sha256=source.sha256,
            provenance_kind=source.provenance_kind.value, provenance=source.provenance)))
    for venue, record in zip(venues.venues, venues.source_venues, strict=True):
        if record.source_values["Venue_ID"] != venue.venue_id:
            raise ValueError("venue records must retain validated source order")
        rows.append(Row("venues", venue.venue_id, dict(venue_code=venue.venue_id, venue_name=venue.venue_name,
            venue_type=venue.venue_type.value, capacity=venue.capacity, building=venue.building, floor=venue.floor,
            lab_specialization=venue.lab_specialization, source_row=record.location.row,
            source_values=json_values(record.source_values), confirmation=dict(
                campus_confirmation=venues.verification.model_dump(mode="json"), location_corrections=corrections)),
            {"source_id": Link("source_datasets", keys[record.location.workbook])}))
    return tuple(rows)
