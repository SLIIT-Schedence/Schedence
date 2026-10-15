"""Source-capacity and resource utilization diagnostics; never changes demand."""

from collections import Counter
import csv
import json

from schedence_teaching.schemas import SessionType, VenueType
from schedence_teaching.solver.eligibility import candidate_pools
from schedence_teaching.solver.venue_constraints import eligible_venue, venue_start_domains
from schedence_teaching.solver.attendance_inputs import DeliveryMode, mode_on_day

from .demand_audit import _csv
from .resource_capacity import _merge, export_resource_capacity


def lab_diagnostics(batch):
    """All original venues and every lab occurrence, including candidate choices.

    Candidate eligibility is not an assignment or a claim of simultaneous
    resource feasibility. The independent packing bound intentionally remains
    optimistic; the actual solver applies every supplied hard restriction.
    """
    venues = batch.solver_inputs["venue_input"]
    groups = {row.group_id: row for row in batch.plan.groups}
    pools = candidate_pools(batch.prepared.input_snapshot)
    inventory = []
    for venue in venues.venues:
        row = venue.model_dump(mode="json")
        row.update(is_computer_or_byod_lab=venue.venue_type in {VenueType.COMPUTER_LAB, VenueType.BYOD_LAB},
                   unresolved_booking_relationship=venue.venue_id in venues.unresolved_venue_ids,
                   eligible_session_types_from_config=";".join(sorted(kind.value for kind, types in batch.configuration.venue_types.items()
                       if venue.venue_type in types)))
        inventory.append(row)
    demand = []
    for session in batch.prepared.sessions:
        if session.session_type != SessionType.LAB:
            continue
        group = groups[session.group_id]
        candidates = [venue.venue_id for venue in venues.venues
                      if eligible_venue(session, venue, batch.prepared, venues)
                      and venue_start_domains(session, venue.venue_id, venues, batch.config)]
        source = batch.source_offerings[session.offering_id]
        demand.append(dict(occurrence_id=session.occurrence_id, requirement_id=session.requirement_id,
            offering_id=session.offering_id, source_workbook=source["workbook"], source_row=source["row"],
            group_id=group.group_id, parent_group_id=group.parent_group_id,
            academic_year=group.academic_year, semester=group.semester, stream=group.stream.value,
            specialization=batch.group_specializations[group.group_id], student_count=group.student_count,
            duration_minutes=session.duration_minutes, eligible_venue_count=len(candidates),
            eligible_venue_ids=";".join(sorted(candidates)),
            eligible_staff_count=len(pools[session.offering_id]),
            eligible_staff_ids=";".join(pools[session.offering_id])))
    return dict(inventory=inventory, lab_occurrences=demand,
                lab_count=sum(row["is_computer_or_byod_lab"] for row in inventory),
                source_workbook=venues.source_workbook,
                venue_confirmation=venues.verification.model_dump(mode="json") if venues.verification else None,
                candidate_semantics="Compatibility and at least one venue time domain, before simultaneous booking, staff and lunch checks.",
                lab_student_size_counts=dict(Counter(row["student_count"] for row in demand)),
                lab_duration_counts=dict(Counter(row["duration_minutes"] for row in demand)))


def _minutes(intervals):
    return sum(end-start for start, end in _merge(intervals))


def _available_minutes(windows, blocks):
    remaining = _merge(windows)
    for block_start, block_end in _merge(blocks):
        reduced = []
        for start, end in remaining:
            if end <= block_start or block_end <= start:
                reduced.append((start, end))
            else:
                if start < block_start:
                    reduced.append((start, block_start))
                if block_end < end:
                    reduced.append((block_end, end))
        remaining = reduced
    return _minutes(remaining)


def resource_utilization(batch, result):
    """Utilization is observed bookings / configured raw available minutes.

    This is not completed workload evidence or a room-packing feasibility proof.
    Excluded shared rooms have no usable denominator. On unsuccessful runs,
    utilization is unknown (null), rather than a misleading zero utilization.
    """
    successful = result.status.value in {"OPTIMAL", "FEASIBLE"}
    venue_input = batch.solver_inputs["venue_input"]
    staff_input = batch.solver_inputs["staff_input"]
    staff_minutes, venue_minutes = Counter(), Counter()
    staff_bookings, venue_bookings = Counter(), Counter()
    for booking in result.schedule:
        staff_minutes[booking.assigned_staff_id] += booking.session.duration_minutes
        staff_bookings[booking.assigned_staff_id] += 1
        if booking.assigned_venue_id is not None:
            venue_minutes[booking.assigned_venue_id] += booking.session.duration_minutes
            venue_bookings[booking.assigned_venue_id] += 1
    venue_rows, staff_rows = [], []
    for venue in venue_input.venues:
        availability = next(row for row in venue_input.calendars if row.venue_id == venue.venue_id)
        available = 0
        for day in batch.config.days:
            physical = [(window.start_minute, window.end_minute) for stream, spans in day.windows.items()
                        if mode_on_day(batch.config, day.day, stream) == DeliveryMode.PHYSICAL
                        for window in spans]
            intersections = [(max(start, window.start_minute), min(end, window.end_minute))
                             for start, end in physical for window in availability.available_windows.get(day.day, [])
                             if max(start, window.start_minute) < min(end, window.end_minute)]
            blocks = [(row.start_minute, row.end_minute) for row in availability.blocked_periods or () if row.day == day.day]
            available += _available_minutes(intersections, blocks)
        excluded = venue.venue_id in venue_input.unresolved_venue_ids
        denominator = None if excluded else available
        venue_rows.append(dict(venue_id=venue.venue_id, venue_type=venue.venue_type.value, capacity=venue.capacity,
            excluded_unresolved_relationship=excluded, configured_raw_available_minutes=denominator,
            scheduled_minutes=venue_minutes[venue.venue_id] if successful else None,
            scheduled_bookings=venue_bookings[venue.venue_id] if successful else None,
            utilization_percent=round(venue_minutes[venue.venue_id]*100/available, 2) if successful and available and not excluded else None))
    for staff in staff_input.availability:
        available = 0
        for day in batch.config.days:
            date = staff_input.day_dates[day.day]
            if day.day not in staff.allowed_teaching_days or not staff.effective_from <= date <= staff.effective_until:
                continue
            intersections = [(max(window.start_minute, allowed.start_minute), min(window.end_minute, allowed.end_minute))
                for spans in day.windows.values() for window in spans for allowed in staff.available_windows.get(day.day, [])
                if max(window.start_minute, allowed.start_minute) < min(window.end_minute, allowed.end_minute)]
            blocks = [(row.start_minute, row.end_minute) for row in staff.blocked_periods if row.day == day.day]
            available += _available_minutes(intersections, blocks)
        staff_rows.append(dict(staff_id=staff.staff_id, configured_raw_available_minutes=available,
            scheduled_teaching_minutes=staff_minutes[staff.staff_id] if successful else None,
            scheduled_bookings=staff_bookings[staff.staff_id] if successful else None,
            utilization_percent=round(staff_minutes[staff.staff_id]*100/available, 2) if successful and available else None))
    return dict(venues=venue_rows, lecturers=staff_rows,
        denominator_semantics="Union of configured availability/windows minus explicit availability blocks; raw time, not a workload target or usable whole-session packing. Protected work-period constraints are separately enforced by the solver.",
        utilization_known=successful)


def export_lab_diagnostics(batch, directory):
    diagnostic = lab_diagnostics(batch)
    capacity = export_resource_capacity(batch, directory)
    _csv(directory / "venue-inventory.csv", diagnostic["inventory"])
    if diagnostic["lab_occurrences"]:
        _csv(directory / "lab-demand.csv", diagnostic["lab_occurrences"])
    else:
        with (directory / "lab-demand.csv").open("x", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(("occurrence_id", "eligible_venue_count"))
    (directory / "lab-diagnostics.json").write_text(json.dumps(diagnostic, indent=2), encoding="utf-8")
    return capacity
