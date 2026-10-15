"""Necessary whole-session student attendance bounds, including fixed lunch.

Raw weekly hours can fit while continuous session lengths cannot fit into the
remaining daily segments. Bounds relax resources and shorter-session demand;
failure proves infeasibility, passing does not prove a timetable exists.
"""

from collections import defaultdict
from hashlib import sha256

from schedence_teaching.solver.attendance_inputs import permitted_day

from .resource_capacity import _merge


def _segments(windows, lunch):
    result = []
    for start, end in _merge(windows):
        if lunch is None or end<=lunch.start_minute or lunch.end_minute<=start:
            result.append((start,end))
        else:
            if start<lunch.start_minute:result.append((start,lunch.start_minute))
            if lunch.end_minute<end:result.append((lunch.end_minute,end))
    return result


def attendance_patterns(batch):
    groups = {row.group_id:row for row in batch.plan.groups}
    sessions = defaultdict(dict)
    for row in batch.prepared.sessions:sessions[row.group_id][row.occurrence_id]=row
    rosters = {row.group_id:row for row in batch.config.attendance_memberships}
    students = defaultdict(dict)
    student_cohorts = defaultdict(set)
    for identity, roster in rosters.items():
        if not roster.complete:
            raise ValueError("Attendance packing requires complete supplied roster evidence.")
        for student in roster.student_ids:
            current = identity
            while current is not None:
                students[student].update(sessions[current])
                student_cohorts[student].add(batch.plan.cohort_by_group[current])
                current = batch.prepared.hierarchy.parents[current]
    patterns = {}
    for student, rows in students.items():
        if len(student_cohorts[student])!=1:
            raise ValueError("Cross-cohort attendance needs a shared-calendar student packing assessment.")
        key = tuple(sorted(rows))
        if not key:continue
        if key not in patterns:
            patterns[key] = dict(cohort_id=next(iter(student_cohorts[student])),
                attendance_reference="explicit-pattern:"+sha256("\n".join(key).encode()).hexdigest()[:16],
                student_count=0, sessions=tuple(rows[identity] for identity in key))
        patterns[key]["student_count"] += 1
    result = list(patterns.values())
    for group in groups.values():
        if group.group_id in rosters or batch.prepared.hierarchy.descendants(group.group_id):continue
        rows, current = {}, group.group_id
        while current is not None:
            rows.update(sessions[current])
            current = batch.prepared.hierarchy.parents[current]
        if rows:
            result.append(dict(cohort_id=batch.plan.cohort_by_group[group.group_id],
                attendance_reference=group.group_id,student_count=group.student_count,sessions=tuple(rows.values())))
    return result


def student_packing_audit(batch):
    policy = batch.solver_inputs["student_lunch_policy"]
    if policy.applicable_dates is None or policy.applicable_streams is None:
        raise ValueError("Student packing needs explicit lunch applicability.")
    groups = {row.group_id:row for row in batch.plan.groups}
    lunches = {row.academic_year:row.window for row in policy.year_periods}
    details = []
    for pattern in attendance_patterns(batch):
        for audience, rows in (("ALL_KNOWN_ATTENDANCE",pattern["sessions"]),
                              ("PHYSICAL_ONLY_ATTENDANCE",tuple(row for row in pattern["sessions"] if row.session_type.value!="LECTURE"))):
            if not rows:continue
            first = groups[rows[0].group_id]
            for duration in sorted({row.duration_minutes for row in rows}):
                long = [row for row in rows if row.duration_minutes>=duration]
                capacity, segments = 0, []
                for day in batch.config.days:
                    if first.stream not in day.windows:continue
                    if not any(permitted_day(batch.config,row,day.day) for row in long):continue
                    date = batch.configuration.calendar.day_dates[day.day]
                    lunch = lunches[first.academic_year] if date in policy.applicable_dates and first.stream in policy.applicable_streams else None
                    spans = _segments([(row.start_minute,row.end_minute) for row in day.windows.get(first.stream,())],lunch)
                    capacity += sum((end-start)//duration for start,end in spans)
                    segments.extend(f"{day.day}:{start}-{end}" for start,end in spans)
                details.append(dict(cohort_id=pattern["cohort_id"],attendance_reference=pattern["attendance_reference"],
                    academic_year=first.academic_year,semester=first.semester,stream=first.stream.value,
                    specialization=batch.group_specializations[first.group_id],student_count=pattern["student_count"],
                    audience=audience,minimum_duration_minutes=duration,required_long_occurrences=len(long),
                    optimistic_whole_occurrence_slots=capacity,shortage_occurrences=max(0,len(long)-capacity),
                    known_contact_minutes=sum(row.duration_minutes for row in rows),
                    uninterrupted_calendar_segments=";".join(segments),
                    source_occurrence_ids=";".join(sorted(row.occurrence_id for row in long))))
    blocked = sorted({row["cohort_id"] for row in details if row["shortage_occurrences"]})
    return dict(status="PROVEN_INFEASIBLE" if blocked else "NO_UPPER_BOUND_VIOLATION",
        blocked_cohort_ids=blocked,rows=details,
        limitations="Ignores staff, venue and shorter-session competition. Bounds overlap; do not sum. Unknown Research demand remains separate and is never treated as zero work.")
