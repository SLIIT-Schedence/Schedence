"""Editable CSV contracts and attendance reports; original workbooks stay read-only."""

from collections import Counter, defaultdict
from dataclasses import replace
import csv
import json
from pathlib import Path

from .demand_audit import _available_minutes, _csv
from .electives import ModuleClassification, ElectiveSelection


CLASSIFICATION_COLUMNS = ("source_workbook", "source_worksheet", "source_row", "specialization", "classification", "reference")


def write_classifications(path, classifications):
    rows = [dict(source_workbook=row.source.workbook, source_worksheet=row.source.worksheet, source_row=row.source.row,
        specialization=row.specialization, classification=row.kind.value, reference=row.reference) for row in classifications]
    _csv(Path(path), rows)


def read_classifications(path):
    from schedence_teaching.excel_import.staging import SourceLocation
    result = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CLASSIFICATION_COLUMNS:
            raise ValueError("Classification CSV columns must match the exported editable template exactly.")
        for number, row in enumerate(reader, 2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError
                result.append(ModuleClassification(source=SourceLocation(row["source_workbook"], row["source_worksheet"], int(row["source_row"])),
                    specialization=row["specialization"], kind=row["classification"], reference=row["reference"]))
            except (ValueError, TypeError, KeyError):
                raise ValueError(f"Invalid module classification CSV row {number}.") from None
    return tuple(result)


SELECTION_COLUMNS = ("data_status", "cohort_id", "academic_year", "semester", "stream", "specialization", "student_id", "elective_1_offering_id", "elective_2_offering_id")


def write_selections(path, selections, cohorts, label):
    lookup = {row.cohort_id: row for row in cohorts}
    rows = []
    for selection in selections:
        cohort = lookup[selection.cohort_id]
        rows.append(dict(data_status=label, cohort_id=cohort.cohort_id, academic_year=cohort.academic_year,
            semester=cohort.semester, stream=cohort.stream.value, specialization=cohort.specialization,
            student_id=selection.student_id, elective_1_offering_id=selection.offering_ids[0], elective_2_offering_id=selection.offering_ids[1]))
    _csv(Path(path), rows)


def read_selections(path, cohorts, *, label):
    lookup = {row.cohort_id: row for row in cohorts}
    selections = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != SELECTION_COLUMNS:
            raise ValueError("Registration CSV columns must match the exported editable template exactly.")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Registration CSV row {number} has missing or extra values.")
            cohort = lookup.get(row["cohort_id"])
            if cohort is None or row["data_status"] != label or (
                    row["academic_year"], row["semester"], row["stream"], row["specialization"]) != (
                    str(cohort.academic_year), str(cohort.semester), cohort.stream.value, cohort.specialization):
                raise ValueError(f"Registration CSV row {number} has an unknown/mismatched cohort or provenance.")
            selections.append(ElectiveSelection(cohort_id=cohort.cohort_id, student_id=row["student_id"],
                offering_ids=(row["elective_1_offering_id"], row["elective_2_offering_id"])))
    return tuple(selections)


def enrolled_demand_report(enrolled):
    """Compare actual attendance with selected + compulsory/project source hours."""
    batch = enrolled.batch
    rosters = {row.group_id: row.student_ids for row in enrolled.group_memberships}
    attended = defaultdict(dict)
    for row in batch.prepared.sessions:
        for student in rosters.get(row.group_id, ()):
            attended[student][row.occurrence_id] = row
    catalog = {(row["cohort_id"], row["offering_id"]): row for row in enrolled.catalog_assessment["catalog"]}
    groups = {row.group_id: row for row in batch.plan.groups}
    first_group = {enrolled.batch.plan.cohort_by_group[identity]: group for identity, group in groups.items()}
    rows = []
    for selection in enrolled.selections:
        data = enrolled.catalog_assessment["cohorts"][selection.cohort_id]
        cohort = data["cohort"]
        identities = {*selection.offering_ids, *data["required"]}
        expected = sum(catalog[(cohort.cohort_id, identity)]["weekly_contact_minutes"] for identity in identities)
        sessions = attended[selection.student_id]
        actual = sum(row.duration_minutes for row in sessions.values())
        expected_by_kind = {(identity, kind): minutes for identity, _, module in data["applicable"] if identity in identities
                            for kind, minutes in module.weekly_minutes.items() if minutes}
        expected_counts = {key:1 for key in expected_by_kind}
        supplemental_minutes = 0
        for requirement in enrolled.additional_student_requirements.get(selection.student_id, ()):
            key = (requirement.offering_id,requirement.session_type)
            minutes = requirement.duration_minutes*requirement.sessions_per_week
            expected_by_kind[key] = expected_by_kind.get(key,0)+minutes
            expected_counts[key] = expected_counts.get(key,0)+requirement.sessions_per_week
            supplemental_minutes += minutes
        expected += supplemental_minutes
        actual_by_kind = Counter()
        occurrence_counts = Counter()
        for session in sessions.values():
            key = (session.offering_id, session.session_type)
            actual_by_kind[key] += session.duration_minutes
            occurrence_counts[key] += 1
        if actual != expected or dict(actual_by_kind) != expected_by_kind or dict(occurrence_counts)!=expected_counts:
            raise ValueError("Generated student attendance disagrees with explicitly enrolled source requirements.")
        available = _available_minutes(batch, first_group[cohort.cohort_id])
        physical_demand = sum(row.duration_minutes for row in sessions.values() if row.session_type.value != "LECTURE")
        physical_days = tuple(day for day in batch.config.days if batch.config.delivery_calendar is None or not any(
            rule.day == day.day and rule.stream == cohort.stream and rule.mode.value == "ONLINE" for rule in batch.config.delivery_calendar.days))
        physical_batch = replace(batch, config=batch.config.model_copy(update=dict(days=physical_days)))
        physical_available = _available_minutes(physical_batch, first_group[cohort.cohort_id])
        rows.append(dict(data_status=enrolled.allocation_label, cohort_id=cohort.cohort_id, academic_year=4,
            semester=cohort.semester, stream=cohort.stream.value, specialization=cohort.specialization,
            student_id=selection.student_id, selected_elective_count=2,
            original_catalog_application_hours=sum(row[2].weekly_minutes[kind] for row in data["applicable"] for kind in row[2].weekly_minutes)/60,
            elective_contact_hours=sum(catalog[(cohort.cohort_id,identity)]["weekly_contact_minutes"] for identity in selection.offering_ids)/60,
            research_status="COMPULSORY_RESEARCH_PENDING_SESSION_MAPPING" if cohort.cohort_id in enrolled.research_pending_cohort_ids else "SEE_RESEARCH_DECLARATION",
            research_weekly_session_hours=None if cohort.cohort_id in enrolled.research_pending_cohort_ids else supplemental_minutes/60,
            corrected_attendance_hours=actual/60, available_hours=available/60,
            shortfall_hours=max(0,actual-available)/60, physical_required_hours=physical_demand/60,
            physical_available_hours=physical_available/60, physical_shortfall_hours=max(0,physical_demand-physical_available)/60,
            occurrence_count=len(sessions)))
    return rows


def export_enrollment(enrolled, directory):
    """Private output only; the caller creates a new, ignored output directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    write_classifications(directory / "module-classifications.csv", enrolled.classifications)
    write_selections(directory / "student-elective-allocations.csv", enrolled.selections, enrolled.batch.plan.cohorts, enrolled.allocation_label)
    populations = defaultdict(set)
    for membership in enrolled.group_memberships:
        for identity in enrolled.attendance_by_group[membership.group_id]:
            populations[(enrolled.batch.plan.cohort_by_group[membership.group_id],identity)].update(membership.student_ids)
    catalog = [dict(row, enrolled_students=len(populations[(row["cohort_id"],row["offering_id"])]),
        planning_active=bool(populations[(row["cohort_id"],row["offering_id"])]),
        activity_status="PP1_PLANNING_ACTIVITY_NOT_UNIVERSITY_OFFERING_STATUS") for row in enrolled.catalog_assessment["catalog"]]
    _csv(directory / "elective-module-catalog.csv", catalog)
    demand = enrolled_demand_report(enrolled)
    _csv(directory / "student-attendance-demand.csv", demand)
    with (directory / "corrected-weekend-capacity.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(demand[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(row for row in demand if row["stream"] == "WEEKEND")
    memberships = []
    lookup = {row.group_id: row for row in enrolled.batch.plan.groups}
    for membership in enrolled.group_memberships:
        group = lookup[membership.group_id]
        for student in membership.student_ids:
            for identity in sorted(enrolled.attendance_by_group[group.group_id]):
                memberships.append(dict(data_status=enrolled.allocation_label, student_id=student,
                    cohort_id=enrolled.batch.plan.cohort_by_group[group.group_id], group_id=group.group_id,
                    parent_group_id=group.parent_group_id or "", student_count=group.student_count,
                    offering_id=identity,
                    semester=group.semester, stream=group.stream.value,
                    specialization=enrolled.batch.group_specializations[group.group_id]))
    _csv(directory / "student-to-module-groups.csv", memberships)
    summary = dict(data_status=enrolled.allocation_label, student_registrations=len(enrolled.selections),
        distinct_students=len({row.student_id for row in enrolled.selections}),
        elective_enrollment_entries=2*len(enrolled.selections), complete_roster_count=len(enrolled.group_memberships),
        scheduled_occurrences_prepared=len(enrolled.batch.prepared.sessions), cohort_totals_preserved=True,
        elective_count_per_student=2, no_university_enrollment_claim=True,
        source_classification_status="SUPPLIED_CLASSIFICATIONS_NOT_INFERRED_FROM_APPLICABILITY")
    (directory / "enrollment-report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
