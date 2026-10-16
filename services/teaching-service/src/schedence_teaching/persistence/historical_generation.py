"""Read and independently validate an archived draft; never rewrite history."""

from collections import defaultdict

from schedence_teaching.pilot.coordinated_streams import validated_union
from schedence_teaching.solver import ScheduledSession
from schedence_teaching.solver.attendance_inputs import DeliveryMode

from .generation_inputs import require


def historical_draft(evidence, enrolled):
    tables = evidence.teaching
    runs = {row["id"]: row for row in tables["generation_runs"] if row["snapshot_id"] == evidence.snapshot_id}
    versions = [row for row in tables["timetable_versions"] if row["run_id"] in runs]
    require(len(versions) == 1, "select an unambiguous historical version for this snapshot")
    version = versions[0]
    run = runs[version["run_id"]]
    require(run["solver_status"] in {"FEASIBLE", "OPTIMAL"} and version["validation_passed"] and version["solver_success"],
        "historical version is not a validated successful draft")
    reports = [row for row in tables["validation_reports"] if row["id"] == version["validation_report_id"] and row["run_id"] == run["id"]]
    require(len(reports) == 1 and reports[0]["passed"] and reports[0]["hard_violations"] == 0, "historical validation evidence is missing")
    groups = {row.group_id: row for row in enrolled.batch.plan.groups}
    occurrences = {row.occurrence_id: row for row in enrolled.batch.prepared.sessions}
    day_dates = {date: day for day, date in enrolled.batch.configuration.calendar.day_dates.items()}
    coverage = defaultdict(list)
    for row in tables["delivery_coverage"]:
        if row["version_id"] == version["id"]:
            coverage[row["delivery_id"]].append(row)
    bookings, seen = [], set()
    for row in tables["deliveries"]:
        if row["version_id"] != version["id"]:
            continue
        covered = coverage.pop(row["id"], ())
        require(len(covered) == 1, "PP1 historical delivery must cover exactly one original occurrence")
        entry = covered[0]
        identity = entry["occurrence_reference"]
        require(identity in occurrences and identity not in seen, "historical coverage is missing, duplicated or unexpected")
        session = occurrences[identity]
        group = groups[session.group_id]
        expected = dict(requirement_reference=session.requirement_id, offering_reference=session.offering_id,
            group_reference=session.group_id, session_type=session.session_type.value, duration_minutes=session.duration_minutes,
            academic_year=group.academic_year, semester=group.semester, stream=session.stream.value,
            specialization=enrolled.batch.group_specializations[group.group_id])
        require(all(entry[key] == value for key, value in expected.items()), "historical occurrence metadata differs from original input")
        require(row["student_count"] == group.student_count and row["teaching_date"] in day_dates,
            "historical delivery count or date differs from original input")
        seen.add(identity)
        bookings.append(ScheduledSession(session, day_dates[row["teaching_date"]], row["start_minute"], row["end_minute"],
            row["staff_reference"], row["venue_reference"], DeliveryMode(row["delivery_mode"])))
    require(not coverage and seen == set(occurrences), "historical draft does not cover all original occurrences")
    result = validated_union(enrolled, bookings, run["runtime_seconds"])
    return result, dict(status="PASSED", historical_version_id=str(version["id"]),
        recorded_solver_status=run["solver_status"], checked_occurrences=len(bookings), hard_violations=0,
        archived_version_unchanged=True, validation_recomputed_from_database_deliveries=True)


def booking_differences(original, generated):
    def index(result):
        return {row.session.occurrence_id: (row.day, row.start_minute, row.end_minute,
            row.assigned_staff_id, row.assigned_venue_id, row.delivery_mode) for row in result.schedule}
    old, new = index(original), index(generated)
    common = old.keys() & new.keys()
    return dict(same_required_occurrences=old.keys() == new.keys(),
        changed_booking_occurrences=sum(old[key] != new[key] for key in common),
        changed_staff_assignments=sum(old[key][3] != new[key][3] for key in common),
        changed_venue_assignments=sum(old[key][4] != new[key][4] for key in common),
        changed_day_or_time=sum(old[key][:3] != new[key][:3] for key in common),
        added_occurrences=len(new.keys() - old.keys()), missing_occurrences=len(old.keys() - new.keys()),
        historical_bookings_are_comparison_only_not_solver_hints=True)
