"""Recheck bookings against the original complete registration memberships."""

from collections import defaultdict

from schedence_teaching.solver.evaluation import validate_evaluation_result

from .elective_io import enrolled_demand_report


def validate_elective_result(enrolled, result):
    batch = enrolled.batch
    if batch.config.attendance_memberships != enrolled.group_memberships or batch.config.attendance_required_group_ids != frozenset(row.group_id for row in enrolled.group_memberships):
        raise ValueError("Scheduling roster no longer matches the original enrollment snapshot.")
    validate_evaluation_result(batch.prepared, batch.config, result, **batch.solver_inputs)
    demand = enrolled_demand_report(enrolled)
    rosters = {row.group_id: row.student_ids for row in enrolled.group_memberships}
    calendars = defaultdict(list)
    for item in result.schedule:
        for student in rosters.get(item.session.group_id, ()):
            calendars[student].append(item)
    for sessions in calendars.values():
        ordered = sorted(sessions, key=lambda row: (row.day, row.start_minute, row.end_minute))
        for first, second in zip(ordered, ordered[1:]):
            if first.day == second.day and second.start_minute < first.end_minute:
                raise ValueError("A student's explicitly selected modules have overlapping bookings.")
    return dict(status="PASSED", data_status=enrolled.allocation_label,
        students_checked=len(demand), complete_registration_snapshot_checked=True,
        student_clashes=0, exact_required_occurrences=len(batch.prepared.sessions),
        original_rosters_preserved=True, native_constraints_independently_validated=True)
