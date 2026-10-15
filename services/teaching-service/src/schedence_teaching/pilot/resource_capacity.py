"""Necessary venue-capacity bounds, independently of CP-SAT search.

These deliberately optimistic bounds ignore staff, other venue bookings and
features. A shortage proves infeasibility; passing never proves feasibility.
Whole-session packing matters: a nine-hour window contains at most four
continuous two-hour labs, even before considering capacity or student lunch.
"""

import json
import csv

from schedence_teaching.schemas import SessionType

from .demand_audit import _csv


def _merge(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    return merged


def physical_venue_capacity_audit(batch, *, session_type=SessionType.LAB, extra_physical_minutes=0):
    """Hall-type size thresholds with an upper bound on whole daily bookings.

    Configured windows and student lunch are honored. Room availability and
    shared-space constraints are relaxed only inside this optimistic PROOF bound,
    never in the solver. Source capacities and teaching demand are unchanged.
    The optional extension is a labelled calendar-bound experiment, not a run.
    """
    if batch.solver_inputs.get("combination_input") is not None:
        raise ValueError("This separate-delivery capacity bound cannot count combined original requirements.")
    if type(extra_physical_minutes) is not int or extra_physical_minutes<0 or extra_physical_minutes%batch.config.granularity_minutes:
        raise ValueError("Scenario extension must be nonnegative whole scheduling ticks.")
    venue_input=batch.solver_inputs["venue_input"]
    groups={row.group_id:row for row in batch.prepared.input_snapshot.student_groups}
    requirements={row.requirement_id:row for row in venue_input.requirements}
    sessions=[row for row in batch.prepared.sessions if row.session_type==session_type]
    durations={row.duration_minutes for row in sessions}
    if len(durations)!=1:
        return dict(status="NOT_APPLICABLE_NO_DEMAND" if not durations else "BOUND_UNAVAILABLE_MIXED_DURATIONS",rows=[],
            limitations="No infeasibility conclusion is drawn for absent or mixed-duration demand.")
    duration=next(iter(durations))
    graph={row.combined_venue_id:row.component_venue_ids for row in venue_input.combined_venues}
    def resolved(identity):
        seen,pending=set(),[identity]
        while pending:
            current=pending.pop()
            if current in seen:continue
            if current in venue_input.unresolved_venue_ids:return False
            seen.add(current)
            pending.extend(graph.get(current,()))
        return True
    policy=batch.solver_inputs["student_lunch_policy"]
    if policy.applicable_dates is None or policy.applicable_streams is None:
        raise ValueError("Capacity audit requires explicit student-lunch applicability.")
    lunches={row.academic_year:row.window for row in policy.year_periods}
    rows=[]
    for stream in sorted({row.stream for row in sessions},key=str):
        stream_sessions=[row for row in sessions if row.stream==stream]
        calendar=batch.config.delivery_calendar
        if calendar and any(row.stream==stream and row.mode.value=="ONLINE" and session_type in row.allowed_session_types for row in calendar.days):
            raise ValueError("Do not charge online-eligible demand to physical venues.")
        allowed_types=set()
        for session in stream_sessions:
            rule=requirements.get(session.requirement_id)
            allowed_types.update(rule.allowed_venue_types if rule and rule.allowed_venue_types is not None else {row.venue_type for row in venue_input.venues})
        pool=[row for row in venue_input.venues if row.venue_type in allowed_types and resolved(row.venue_id)]
        years={groups[row.group_id].academic_year for row in stream_sessions}
        per_day=[]
        for day in batch.config.days:
            if stream not in day.windows:continue
            rule=next((row for row in calendar.days if (row.day,row.stream)==(day.day,stream)),None) if calendar else None
            if rule and (rule.mode.value!="PHYSICAL" or session_type not in rule.allowed_session_types):continue
            windows=_merge([(row.start_minute,row.end_minute+extra_physical_minutes) for row in day.windows[stream]])
            if any(end>1440 for _,end in windows):
                raise ValueError("Test scenario must remain within its original calendar date.")
            candidates=set()
            date=batch.configuration.calendar.day_dates[day.day]
            for start,end in windows:
                for minute in range(start,end-duration+1,batch.config.granularity_minutes):
                    if any(not (date in policy.applicable_dates and stream in policy.applicable_streams)
                        or minute+duration<=lunches[year].start_minute or minute>=lunches[year].end_minute for year in years):
                        candidates.add((minute,minute+duration))
            count,previous=0,-1
            for start,end in sorted(candidates,key=lambda pair:(pair[1],pair[0])):
                if start>=previous:
                    count+=1
                    previous=end
            per_day.append(dict(day=day.day,whole_bookings_per_venue_upper_bound=count,
                raw_window_minutes=sum(end-start for start,end in windows)))
        slots_per_venue=sum(row["whole_bookings_per_venue_upper_bound"] for row in per_day)
        maximum_size=max(groups[row.group_id].student_count for row in stream_sessions)
        thresholds={1,*[groups[row.group_id].student_count for row in stream_sessions]}
        thresholds.update(venue.capacity+1 for venue in pool if venue.capacity<maximum_size)
        for minimum in sorted(thresholds):
            demand=sum(groups[row.group_id].student_count>=minimum for row in stream_sessions)
            rooms=[row for row in pool if row.capacity>=minimum]
            available=slots_per_venue*len(rooms)
            shortage=max(0,demand-available)
            rows.append(dict(scenario="PP1_TEST_CALENDAR_BASELINE" if extra_physical_minutes==0 else "TEST_SCENARIO_UPPER_BOUND_ONLY_NOT_A_TIMETABLE",
                extra_physical_minutes_per_day=extra_physical_minutes,stream=stream.value,session_type=session_type.value,
                session_duration_minutes=duration,minimum_students=minimum,compatible_venue_count_upper_bound=len(rooms),
                physical_days=",".join(row["day"] for row in per_day),required_sessions=demand,
                optimistic_available_whole_session_slots=available,shortage_sessions=shortage,
                shortage_contact_room_hours=shortage*duration/60,
                raw_room_hours_upper_bound=sum(row["raw_window_minutes"] for row in per_day)*len(rooms)/60,
                classification="PROVEN_INSUFFICIENT_PHYSICAL_VENUE_CAPACITY" if shortage else "NO_UPPER_BOUND_VIOLATION_NOT_A_FEASIBILITY_PROOF"))
    return dict(status="PROVEN_INFEASIBLE" if any(row["shortage_sessions"] for row in rows) else "NO_UPPER_BOUND_VIOLATION",
        rows=rows,day_bounds_scope="Per room, any eligible study-year lunch pattern; independent interval packing upper bound.",
        limitations="Ignores staff, competing teaching, features, individual room restrictions and finite counts per year. Bounds overlap and must not be added. No hard scheduling restriction was relaxed.")


def export_resource_capacity(batch,directory):
    baseline=physical_venue_capacity_audit(batch)
    if baseline["rows"]:_csv(directory/"physical-lab-capacity.csv",baseline["rows"])
    else:
        with (directory/"physical-lab-capacity.csv").open("x",encoding="utf-8",newline="") as handle:
            csv.writer(handle).writerow(("scenario","stream","session_type","classification"))
    (directory/"physical-lab-capacity.json").write_text(json.dumps(baseline,indent=2),encoding="utf-8")
    scenarios=[]
    for minutes in (0,60,120,180,240):
        scenarios.extend(physical_venue_capacity_audit(batch,extra_physical_minutes=minutes)["rows"])
    if scenarios:_csv(directory/"test-calendar-capacity-comparison.csv",scenarios)
    return baseline
