"""Multi-module lab assignment with synthetic inputs; no database access."""
import csv
from dataclasses import replace
from io import StringIO
from uuid import uuid4

import pytest

from schedence_teaching.persistence.retrieval import assemble_timetable
from schedence_teaching.preparation import prepare_timetable_input
from schedence_teaching.reporting.contracts import WorkloadQuery
from schedence_teaching.reporting.exports import workload_csv
from schedence_teaching.reporting.workload import lecturer_workload
from schedence_teaching.schemas import TeachingTimetableInput
from schedence_teaching.solver import SolverStatus, solve_timetable, validate_staff_policy_result
from test_persistence_contracts import snapshot_for
from test_staff_solver import demand
from test_teaching_assignment_policy import policy
from test_timetable_versioning import record_for
from test_work_period_solver import scenario


def lab_case(module_count=4, *, durations=None, days=('MON', 'TUE', 'WED', 'THU'), minutes=360):
    # Four offerings require ten actual occurrences, not four occurrences.
    counts = [2, 2, 3, 3, 1][:module_count]
    pools = {f'M{i}': [('INS', 'TEAM_MEMBER')] for i in range(module_count)}
    p = demand(pools, [(oid, f'G-{oid}', count) for oid, count in zip(pools, counts)],
               staff_types={'INS': 'INSTRUCTOR'})
    data = p.input_snapshot.model_dump()
    for i, requirement in enumerate(data['session_requirements']):
        requirement.update(session_type='LAB', duration_minutes=durations[i] if durations else 120)
    p = prepare_timetable_input(TeachingTimetableInput.model_validate(data))
    p, c, staff, *_ = scenario(prepared=p, days=days, minutes=minutes, band='LECTURER')
    staff.assignment_policy = policy(staff)
    return p, c, staff


def solve_valid(case):
    p, c, staff = case
    result = solve_timetable(p, c, staff_input=staff)
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    validate_staff_policy_result(p, c, result, staff_input=staff)
    return result


@pytest.mark.parametrize('module_count,occurrences,minutes', [(4, 10, 1200), (5, 11, 1320)])
def test_one_instructor_teaches_four_or_more_modules_without_an_invented_cap(module_count, occurrences, minutes):
    case = lab_case(module_count)
    result = solve_valid(case)
    assert len(result.schedule) == occurrences
    assert len({b.session.offering_id for b in result.schedule}) == module_count
    assert {b.assigned_staff_id for b in result.schedule} == {'INS'}
    assert {b.session.session_type.value for b in result.schedule} == {'LAB'}
    row = result.workload_report.staff[0]
    assert row.policy.required_teaching_minutes == 1200
    assert row.scheduled_teaching_minutes == minutes
    assert len(row.teaching_days) <= 6
    for day in {b.day for b in result.schedule}:
        bookings = sorted((b for b in result.schedule if b.day == day), key=lambda b: b.start_minute)
        assert all(a.end_minute <= b.start_minute for a, b in zip(bookings, bookings[1:]))


def test_different_lab_durations_and_weekly_frequencies_determine_actual_workload():
    result = solve_valid(lab_case(durations=[30, 60, 90, 120]))
    assert len(result.schedule) == 10
    assert result.workload_report.staff[0].scheduled_teaching_minutes == 2*30 + 2*60 + 3*90 + 3*120


def test_four_modules_do_not_override_module_team_eligibility():
    p, c, staff = lab_case()
    data = p.input_snapshot.model_dump()
    data['staff_members'].append(dict(staff_id='ACADEMIC', staff_name='Synthetic academic',
                                      position='Synthetic', staff_type='ACADEMIC'))
    for member in data['teaching_team_members']:
        if member['offering_id'] == 'M0':
            member['staff_id'] = 'ACADEMIC'
    p = prepare_timetable_input(TeachingTimetableInput.model_validate(data))
    p, c, staff, *_ = scenario(prepared=p, days=('MON', 'TUE', 'WED', 'THU'), minutes=360, band='LECTURER')
    staff.assignment_policy = policy(staff)
    # The academic is a module member but cannot teach labs under this policy;
    # the instructor is lab-eligible but not a member of M0.
    assert solve_timetable(p, c, staff_input=staff).status is SolverStatus.INFEASIBLE


def test_multiple_module_assignments_still_require_staff_availability():
    p, c, staff = lab_case()
    staff.availability[0].available_windows = {day: [] for day in staff.day_dates}
    assert solve_timetable(p, c, staff_input=staff).status is SolverStatus.INFEASIBLE


def test_multi_module_labs_cannot_overlap_to_fit_an_undersized_calendar():
    p, c, staff = lab_case(days=('MON',), minutes=120)
    assert solve_timetable(p, c, staff_input=staff).status is SolverStatus.INFEASIBLE


def test_module_count_does_not_bypass_configured_teaching_day_limit():
    p, c, staff = lab_case()
    staff.availability[0].weekly_teaching_max_days = 3
    # 10 x 2-hour labs need four supplied 6-hour days.
    assert solve_timetable(p, c, staff_input=staff).status is SolverStatus.INFEASIBLE


def test_explicit_hard_time_cap_remains_separate_from_soft_target():
    p, c, staff = lab_case(5)
    staff.availability[0].weekly_teaching_max_minutes = 1200
    assert solve_timetable(p, c, staff_input=staff).status is SolverStatus.INFEASIBLE


@pytest.fixture(scope='module')
def saved_labs():
    from schedence_teaching.solver import TimeWindow
    from test_venue_solver import physical
    p, c, staff = lab_case()
    rooms = physical([('SYN-LAB', 30, 'Computer Lab')], rules=[
        dict(requirement_id=r.requirement_id, allowed_venue_types=['Computer Lab'])
        for r in p.input_snapshot.session_requirements])
    rooms.calendars[0].available_windows = {
        day: [TimeWindow(start_minute=0, end_minute=360)] for day in staff.day_dates}
    result = solve_timetable(p, c, staff_input=staff, venue_input=rooms)
    assert result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}
    validate_staff_policy_result(p, c, result, staff_input=staff, venue_input=rooms)
    document = snapshot_for(p, c, contexts={'staff_input': staff, 'venue_input': rooms})
    document, version, run, validation, bookings, coverage = record_for(document, result)
    version.update(id=uuid4(), series_id=uuid4(), version_number=1, academic_complete=False)
    run.update(snapshot_id=uuid4(), solver_status=result.status.value, runtime_seconds=result.runtime_seconds)
    return assemble_timetable(document, version, run, validation, bookings, coverage)


def test_workload_json_and_csv_count_modules_separately_from_actual_labs(saved_labs):
    report = lecturer_workload(saved_labs, WorkloadQuery(version_id=saved_labs.version['id']))
    row = report['staff'][0]
    assert row['assigned_module_offering_count'] == row['assigned_lab_module_offering_count'] == 4
    assert row['assigned_module_offering_ids'] == ['M0', 'M1', 'M2', 'M3']
    assert row['assigned_lab_delivery_count'] == row['assigned_delivery_count'] == 10
    assert row['lab_minutes'] == row['assigned_teaching_minutes'] == 1200
    assert row['weekly_teaching_target_minutes'] == 1200
    # The fixture deliberately shares one module code across four offerings.
    assert len({m['module_code'] for m in report['modules']}) == 1
    exported = list(csv.DictReader(StringIO(workload_csv(report).decode('utf-8-sig'))))[0]
    assert exported['assigned_lab_module_offering_count'] == '4'
    assert exported['assigned_lab_delivery_count'] == '10'
    assert exported['lab_minutes'] == '1200'


def test_filtered_module_counts_do_not_replace_full_week_counts_or_targets(saved_labs):
    report = lecturer_workload(saved_labs, WorkloadQuery(version_id=saved_labs.version['id'], offering_id='M0'))
    row = report['staff'][0]
    assert row['assigned_module_offering_count'] == row['assigned_lab_module_offering_count'] == 1
    assert row['assigned_lab_delivery_count'] == 2 and row['lab_minutes'] == 240
    assert row['full_version_module_offering_count'] == row['full_version_lab_module_offering_count'] == 4
    assert row['full_version_teaching_minutes'] == row['weekly_teaching_target_minutes'] == 1200


def test_duplicate_group_coverage_does_not_multiply_module_or_session_counts(saved_labs):
    from copy import deepcopy
    deliveries = deepcopy(saved_labs.deliveries)
    deliveries[0]['coverage'].append(dict(deliveries[0]['coverage'][0], group_reference='SYNTHETIC-SUBGROUP'))
    report = lecturer_workload(replace(saved_labs, deliveries=deliveries), WorkloadQuery(version_id=saved_labs.version['id']))
    row = report['staff'][0]
    assert row['assigned_lab_module_offering_count'] == 4
    assert row['assigned_lab_delivery_count'] == 10 and row['assigned_teaching_minutes'] == 1200
