"""Synthetic joint teaching/admin/research scheduling and independent corruption checks."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import runpy

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.preparation import prepare_group_combinations, prepare_timetable_input
from schedence_teaching.schemas import Stream, TeachingTimetableInput
from schedence_teaching.solver import (
    SolverInputError, SolverStatus, StaffAvailability, StaffSchedulingInput,
    TimetableValidationError, TimeWindow, WorkPeriodSolverConfig, solve_timetable,
    validate_delivery_result, validate_staff_policy_result, validate_work_period_result,
)
from schedence_teaching.workload import (
    RecognizedWorkRecord, StaffWorkProfile, Weekday, WeeklyWorkloadInput,
    WorkPeriodInput, assess_weekly_workload, confirmed_campus_policy, prepare_work_periods,
)

from test_combination_solver import inputs as combination_inputs, venues
from test_staff_solver import demand, windows
from test_work_periods import (
    FIRST, LAST, admin, appointment, flexible_policy, flexible_request,
    research, research_policy,
)


def scenario(*, minutes=300, count=1, duration=30, days=("DAY",), band="PROFESSOR",
             appointment_name=None, prepared=None, both_streams=False, limit=5.0):
    prepared = prepared or demand({"OFFER": [("STAFF", "LIC")]}, requirements=[("OFFER", "GROUP", count)])
    if duration != 30:
        payload = prepared.input_snapshot.model_dump(round_trip=True)
        for row in payload["session_requirements"]:
            row["duration_minutes"] = duration
        prepared = prepare_timetable_input(TeachingTimetableInput.model_validate(payload))
    config = windows(minutes, days=days, both_streams=both_streams, limit=limit)
    workload = WeeklyWorkloadInput(week_reference="SYNTHETIC-WEEK", policy=confirmed_campus_policy(),
        profiles=[StaffWorkProfile(staff_id=row.staff_id,
            workload_band="INSTRUCTOR" if row.staff_type.value == "INSTRUCTOR" else band,
            appointment=appointment_name, evidence_reference="synthetic:classification")
            for row in prepared.input_snapshot.staff_members])
    staff = StaffSchedulingInput(workload_input=workload,
        day_dates={day.day: FIRST + timedelta(days=index) for index, day in enumerate(config.days)},
        policy_effective_from=FIRST, policy_effective_until=LAST,
        availability=[StaffAvailability(staff_id=row.staff_id, allowed_teaching_days=set(days),
            available_windows={day: [TimeWindow(start_minute=0, end_minute=1440)] for day in days},
            blocked_periods=[], effective_from=FIRST, effective_until=LAST)
            for row in prepared.input_snapshot.staff_members])
    periods = WorkPeriodInput(week_reference=workload.week_reference, week_start=FIRST, week_end=LAST)
    options = WorkPeriodSolverConfig(
        day_weekdays={day: list(Weekday)[staff.day_dates[day].weekday()] for day in days},
        day_streams={day.day: set(day.windows) for day in config.days})
    return prepared, config, staff, periods, options


def with_flexible(staff, periods, *, required=True, identity="FLEX", duration=240,
                  eligible=None, split=False, minimum=None, protection="HARD"):
    periods.flexible_policies = [flexible_policy(staff.workload_input, reservation_required=required)]
    periods.flexible_requests.append(flexible_request(request_id=identity, planned_duration_minutes=duration,
        eligible_windows=eligible or [{"on_date": FIRST, "start_minute": 0, "end_minute": 300}],
        allow_split=split, minimum_period_minutes=minimum, protection=protection))


def run(data, **kwargs):
    prepared, config, staff, source, options = data
    periods = prepare_work_periods(prepared.input_snapshot, staff.workload_input, source)
    inputs = {"staff_input": staff, "work_periods": periods, "work_period_config": options, **kwargs}
    result = solve_timetable(prepared, config, **inputs)
    summary = validate_work_period_result(prepared, config, result, **inputs)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert summary == result.validation
        assert assess_weekly_workload(prepared, config, result, staff.workload_input, **inputs) == result.workload_report
    else:
        assert summary is None and result.schedule == result.administrative_schedule == ()
        assert result.validation is result.workload_report is result.work_period_report is None
    return result, inputs


@pytest.mark.parametrize("end", [15, 30, 45, 60])
def test_fixed_admin_protects_staff_and_preserves_exact_source(end):
    data = scenario(minutes=90)
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 0, "end_minute": end}, planned_duration_minutes=end)]
    result, _ = run(data)
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].start_minute >= end
    row = result.work_period_report.staff[0]
    assert row.fixed_reserved_minutes == row.reserved_administrative_minutes == end
    assert row.scheduled_teaching_minutes == 30 and row.verified_completed_administrative_minutes == 0
    assert row.administrative_completion_status == "NOT_VERIFIED" and row.administrative_completion_fulfilled is None
    assert row.campus_attendance_status == "EXEMPT" and row.evidenced_campus_days == ()
    assert result.work_period_report.fixed_administrative_periods[0].to_dict()["work_minutes_credited"] == 0


@pytest.mark.parametrize("full_day", [True, False])
def test_hard_research_blocks_full_or_partial_day_without_work_credit(full_day):
    data = scenario(minutes=60, days=("MONDAY", "TUESDAY"))
    data[3].research_policies = [research_policy()]
    data[3].research_reservations = [research(on_date=FIRST, full_day=full_day,
        windows=[] if full_day else [{"start_minute": 0, "end_minute": 60}])]
    result, _ = run(data)
    assert result.schedule[0].day == "TUESDAY"
    row = result.work_period_report.staff[0]
    assert row.reserved_administrative_minutes == row.verified_other_recognized_minutes == 0
    assert result.work_period_report.research_periods[0].full_day is full_day


@pytest.mark.parametrize("kind", ["admin", "research"])
@pytest.mark.parametrize("status", ["PENDING", "PROPOSED", "CANCELLED", "EXPIRED", "SOFT", "OLD_DATES", "OTHER_WEEK"])
def test_unapproved_soft_or_expired_periods_never_block_teaching(kind, status):
    data = scenario(minutes=30)
    updates = {"on_date": FIRST}
    if status == "SOFT":
        updates["protection"] = "SOFT"
    elif status == "OLD_DATES":
        updates.update(on_date=FIRST - timedelta(days=7), effective_from=FIRST - timedelta(days=7), effective_until=FIRST - timedelta(days=1))
    elif status == "OTHER_WEEK":
        updates["week_reference"] = "OTHER-WEEK"
    else:
        updates.update(status=status, approval_reference=None)
    if kind == "admin":
        data[3].administrative_periods = [admin(data[2].workload_input,
            window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30, **updates)]
    else:
        data[3].research_policies = [research_policy()]
        data[3].research_reservations = [research(**updates)]
    result, _ = run(data)
    assert result.status is SolverStatus.OPTIMAL and result.schedule[0].start_minute == 0
    assert result.work_period_report.fixed_administrative_periods == result.work_period_report.research_periods == ()


def test_recurring_approved_weekday_protects_the_supplied_calendar_date():
    data = scenario(minutes=30, days=("MONDAY", "TUESDAY"))
    data[3].administrative_periods = [admin(data[2].workload_input, on_date=None, weekday="MONDAY",
        window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    result, _ = run(data)
    assert result.schedule[0].day == "TUESDAY"
    assert result.work_period_report.fixed_administrative_periods[0].on_date == FIRST


@pytest.mark.parametrize("cause", ["missing_options", "missing_staff", "week", "outside_week", "weekday", "day_label", "stream", "missing_declaration", "unmapped_protection", "unmapped_flexible", "tampered_preparation", "changed_workload"])
def test_invalid_calendar_link_or_snapshot_rejected(cause):
    data = scenario(minutes=300, days=("MONDAY",))
    prepared, config, staff, source, options = data
    source.administrative_periods = [admin(staff.workload_input)]
    periods = prepare_work_periods(prepared.input_snapshot, staff.workload_input, source)
    if cause == "missing_options":
        options = None
    elif cause == "missing_staff":
        staff = None
    elif cause == "week":
        periods.input_snapshot.week_reference = "OTHER"
    elif cause == "outside_week":
        staff.day_dates["MONDAY"] = FIRST - timedelta(days=7)
        staff.policy_effective_from = FIRST - timedelta(days=7)
    elif cause == "weekday":
        options.day_weekdays["MONDAY"] = Weekday.TUESDAY
    elif cause == "day_label":
        staff.day_dates["MONDAY"] = FIRST + timedelta(days=1)
        options.day_weekdays["MONDAY"] = Weekday.TUESDAY
    elif cause == "stream":
        options.day_streams["MONDAY"] = {Stream.WEEKEND}
    elif cause == "missing_declaration":
        options.day_weekdays = {}
    elif cause in {"unmapped_protection", "unmapped_flexible"}:
        source.administrative_periods = []
        if cause == "unmapped_protection":
            source.administrative_periods = [admin(staff.workload_input, on_date=FIRST + timedelta(days=1))]
        else:
            with_flexible(staff, source, eligible=[{"on_date": FIRST + timedelta(days=1), "start_minute": 0, "end_minute": 240}])
        periods = prepare_work_periods(prepared.input_snapshot, staff.workload_input, source)
    elif cause == "tampered_preparation":
        periods = replace(periods, protected_intervals=())
    elif cause == "changed_workload":
        staff.workload_input.recognized_work_complete_for = {"STAFF"}
    with pytest.raises(SolverInputError):
        solve_timetable(prepared, config, staff_input=staff, work_periods=periods, work_period_config=options)


def test_solver_options_require_preparation_and_policy_mandatory_nature_is_explicit():
    data = scenario()
    with pytest.raises(SolverInputError):
        solve_timetable(data[0], data[1], work_period_config=data[4])
    with_flexible(data[2], data[3], required=None)
    with pytest.raises(SolverInputError, match="reservation_required"):
        run(data)
    with pytest.raises(ValidationError):
        flexible_policy(data[2].workload_input, reservation_required=1)


@pytest.mark.parametrize("required", [True, False])
def test_flexible_continuous_reservation_fits_approved_windows(required):
    data = scenario()
    with_flexible(data[2], data[3], required=required)
    result, _ = run(data)
    assert result.status is SolverStatus.OPTIMAL
    selected = result.administrative_schedule
    assert len(selected) == 1 if required else len(selected) in {0, 1}
    if selected:
        assert selected[0].end_minute - selected[0].start_minute == 240
        assert selected[0].approval_reference == "synthetic:approval"
    row = result.work_period_report.staff[0]
    assert row.flexible_reserved_minutes == 240 * len(selected) and row.verified_completed_administrative_minutes == 0
    assert not result.to_dict()["university_ready"]


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("cause", ["short_windows", "teaching", "grid", "fixed", "research", "other_flexible"])
def test_impossible_flexible_is_infeasible_only_when_mandatory(required, cause):
    data = scenario(minutes=270)
    prepared, config, staff, source, _ = data
    eligible = [{"on_date": FIRST, "start_minute": 0, "end_minute": 240}]
    if cause == "short_windows":
        eligible[0]["end_minute"] = 120
    elif cause == "teaching":
        config.days[0].windows["WEEKDAY"][0].end_minute = 240
    elif cause == "grid":
        eligible[0].update(start_minute=1, end_minute=241)
    elif cause == "fixed":
        source.administrative_periods = [admin(staff.workload_input,
            window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    elif cause == "research":
        source.research_policies = [research_policy()]
        source.research_reservations = [research(on_date=FIRST, full_day=False,
            windows=[{"start_minute": 0, "end_minute": 30}])]
    elif cause == "other_flexible":
        with_flexible(staff, source, identity="OTHER", duration=30, eligible=eligible)
    with_flexible(staff, source, required=required, eligible=eligible)
    # Both requests share the explicitly supplied policy; make OTHER mandatory.
    if cause == "other_flexible" and not required:
        other_policy = flexible_policy(staff.workload_input, policy_reference="synthetic:other-flex", reservation_required=True)
        source.flexible_policies.append(other_policy)
        source.flexible_requests[0].policy_reference = other_policy.policy_reference
    result, _ = run(data)
    assert result.status is (SolverStatus.INFEASIBLE if required else SolverStatus.OPTIMAL)
    if not required:
        assert all(row.request_id != "FLEX" for row in result.administrative_schedule)
        assert "FLEX" in result.work_period_report.omitted_optional_request_ids


def test_authorized_split_reservation_uses_explicit_minimum_and_exact_total():
    data = scenario(minutes=300, days=("MONDAY", "TUESDAY"))
    with_flexible(data[2], data[3], split=True, minimum=120, eligible=[
        {"on_date": FIRST, "start_minute": 0, "end_minute": 120},
        {"on_date": FIRST + timedelta(days=1), "start_minute": 0, "end_minute": 120}])
    result, _ = run(data)
    assert len(result.administrative_schedule) == 2
    assert [row.block_id for row in result.administrative_schedule] == ["administrative:FLEX:1", "administrative:FLEX:2"]
    assert sum(row.end_minute - row.start_minute for row in result.administrative_schedule) == 240


def test_split_minimum_rounds_up_without_fabricating_duration():
    data = scenario(minutes=120)
    with_flexible(data[2], data[3], duration=60, split=True, minimum=20, eligible=[
        {"on_date": FIRST, "start_minute": 0, "end_minute": 30},
        {"on_date": FIRST, "start_minute": 60, "end_minute": 90}])
    result, _ = run(data)
    assert [row.end_minute - row.start_minute for row in result.administrative_schedule] == [30, 30]


def test_admin_only_calendar_day_has_no_invented_teaching_stream_or_hours():
    data = scenario(days=("MONDAY", "TUESDAY"))
    data[1].days[1].windows = {}
    data[4].day_streams["TUESDAY"] = set()
    with_flexible(data[2], data[3], eligible=[
        {"on_date": FIRST + timedelta(days=1), "start_minute": 0, "end_minute": 240}])
    result, _ = run(data)
    assert result.schedule[0].day == "MONDAY" and result.administrative_schedule[0].day == "TUESDAY"


def test_out_of_order_abstract_days_preserve_dates_and_stable_block_ordinals():
    data = scenario(minutes=300, days=("A", "B"))
    data[2].day_dates = {"A": FIRST + timedelta(days=1), "B": FIRST}
    data[4].day_weekdays = {"A": Weekday.TUESDAY, "B": Weekday.MONDAY}
    with_flexible(data[2], data[3], split=True, minimum=120, eligible=[
        {"on_date": FIRST, "start_minute": 0, "end_minute": 120},
        {"on_date": FIRST + timedelta(days=1), "start_minute": 0, "end_minute": 120}])
    result, _ = run(data)
    assert [row.day for row in result.administrative_schedule] == ["B", "A"]


@pytest.mark.parametrize("band,appt,teaching", [("PROFESSOR", None, 360), ("LECTURER", "HOD", 240), ("LECTURER", "DEAN", 240)])
def test_professor_hod_dean_teaching_and_admin_are_separate(band, appt, teaching):
    data = scenario(minutes=600, duration=teaching, band=band, appointment_name=appt)
    if appt:
        data[3].appointments = [appointment(position_code=appt)]
    with_flexible(data[2], data[3], eligible=[{"on_date": FIRST, "start_minute": 0, "end_minute": 600}])
    if appt:
        data[3].flexible_requests[0].appointment_id = "APPT-HOD"
        data[3].flexible_policies[0].administrative_policy_reference = "ads:hod-policy"
    result, _ = run(data)
    row = result.work_period_report.staff[0]
    assert row.required_teaching_minutes == row.scheduled_teaching_minutes == teaching
    assert row.administrative_allocation_minutes == row.reserved_administrative_minutes == 240
    assert row.teaching_requirement_status == "MET" and row.administrative_completion_status == "NOT_VERIFIED"
    assert row.campus_attendance_status == "EXEMPT" and "administrative_work_completion" in row.missing_evidence
    assert row.verified_completed_administrative_minutes == 0
    assert result.workload_report.staff[0].policy.workload_band.value == band


@pytest.mark.parametrize("band", ["PROFESSOR", "ASSISTANT_PROFESSOR", "LECTURER"])
def test_allocation_does_not_create_calendar_reservations_or_attendance(band):
    data = scenario(band=band)
    result, _ = run(data)
    assert result.administrative_schedule == () and result.work_period_report.fixed_administrative_periods == ()
    row = result.work_period_report.staff[0]
    assert row.reserved_administrative_minutes == 0 and row.evidenced_campus_days == ()
    assert row.campus_attendance_status == ("EXEMPT" if band == "PROFESSOR" else "NOT_VERIFIED")
    assert row.administrative_allocation_minutes == (240 if band == "PROFESSOR" else None)


def test_completed_work_comes_only_from_explicit_evidence():
    data = scenario(duration=360, minutes=600)
    data[2].workload_input.recognized_work = [RecognizedWorkRecord(work_id="CREDIT", staff_id="STAFF",
        work_type="ADMINISTRATIVE", duration_minutes=240, evidence_reference="synthetic:completed-admin")]
    with_flexible(data[2], data[3], eligible=[{"on_date": FIRST, "start_minute": 0, "end_minute": 600}])
    result, _ = run(data)
    row = result.work_period_report.staff[0]
    assert row.verified_completed_administrative_minutes == row.reserved_administrative_minutes == 240
    assert row.administrative_completion_status == "MET" and row.administrative_completion_fulfilled is True
    assert row.scheduled_teaching_minutes == 360 and row.evidenced_campus_days == ()
    assert "untimed_work_evidence" in {row.code for row in result.work_period_report.diagnostics}


@pytest.mark.parametrize("mixed", [True, False])
def test_combined_and_mixed_bookings_share_protection_and_count_actual_work_once(mixed):
    combinations = prepare_group_combinations(combination_inputs())
    data = scenario(minutes=120, prepared=combinations.prepared)
    # Protect the last 30 minutes, preserving ROOM's first-30-minute combined option.
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 90, "end_minute": 120}, planned_duration_minutes=30)]
    result, inputs = run(data, combination_input=combinations,
                         venue_input=venues(large_minutes=30) if mixed else venues())
    assert result.validation.combined_deliveries == (1 if mixed else 2)
    assert result.validation.separate_deliveries == (2 if mixed else 0)
    actual = 90 if mixed else 60
    assert result.work_period_report.staff[0].scheduled_teaching_minutes == actual
    assert result.objective_value == len(result.schedule) * 121 + actual
    assert all(row.end_minute <= 90 for row in result.schedule)
    assert validate_delivery_result(data[0], data[1], result, **inputs) == result.validation


def test_combination_coverage_and_objective_cannot_drop_mandatory_admin():
    combinations = prepare_group_combinations(combination_inputs())
    data = scenario(minutes=60, prepared=combinations.prepared)
    with_flexible(data[2], data[3], duration=30, eligible=[{"on_date": FIRST, "start_minute": 0, "end_minute": 60}])
    result, _ = run(data, combination_input=combinations)
    assert result.status is SolverStatus.INFEASIBLE


def test_cross_stream_teaching_conflicts_with_same_staff_protection():
    prepared = demand({"WD": [("STAFF", "LIC")], "WE": [("STAFF", "LIC")]},
        streams={"WD": "WEEKDAY", "WE": "WEEKEND"})
    data = scenario(minutes=90, prepared=prepared, both_streams=True)
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 0, "end_minute": 45}, planned_duration_minutes=45)]
    result, _ = run(data)
    assert result.status is SolverStatus.INFEASIBLE  # 60 teaching minutes cannot fit remaining 45.


@pytest.mark.parametrize("field,value", [("request_id", "FORGED"), ("staff_id", "OTHER"), ("on_date", date(2026, 10, 20)),
    ("day", "UNKNOWN"), ("start_minute", True), ("end_minute", 285), ("approval_reference", "FORGED"),
    ("policy_reference", "FORGED"), ("block_id", "FORGED"), ("protection", "SOFT"), ("institution_id", "FORGED")])
def test_independent_validator_rejects_corrupted_admin_booking(field, value):
    data = scenario()
    with_flexible(data[2], data[3])
    result, inputs = run(data)
    damaged = replace(result, administrative_schedule=(replace(result.administrative_schedule[0], **{field: value}),))
    with pytest.raises(TimetableValidationError):
        validate_work_period_result(data[0], data[1], damaged, **inputs)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "summary", "fixed_source", "mode", "workload_credit", "runtime"])
def test_independent_validator_rejects_missing_or_fabricated_report_data(corruption):
    data = scenario()
    with_flexible(data[2], data[3])
    result, inputs = run(data)
    if corruption == "missing":
        result = replace(result, administrative_schedule=())
    elif corruption == "duplicate":
        result = replace(result, administrative_schedule=result.administrative_schedule * 2)
    elif corruption == "summary":
        staff = replace(result.work_period_report.staff[0], reserved_administrative_minutes=0)
        result = replace(result, work_period_report=replace(result.work_period_report, staff=(staff,)))
    elif corruption == "fixed_source":
        result = replace(result, work_period_report=replace(result.work_period_report,
            fixed_administrative_periods=(replace(inputs["work_periods"].flexible_candidates[0], protection="HARD"),)))
    elif corruption == "mode":
        result = replace(result, work_period_enabled=False)
    elif corruption == "workload_credit":
        staff = replace(result.workload_report.staff[0], administrative_minutes=240)
        result = replace(result, workload_report=replace(result.workload_report, staff=(staff,)))
    else:
        result = replace(result, runtime_seconds=float("nan"))
    with pytest.raises(TimetableValidationError):
        validate_work_period_result(data[0], data[1], result, **inputs)


def test_existing_result_validators_require_original_work_period_context():
    data = scenario()
    with_flexible(data[2], data[3])
    result, _ = run(data)
    with pytest.raises(TimetableValidationError, match="work_period_mode_changed"):
        validate_staff_policy_result(data[0], data[1], result, staff_input=data[2])


@pytest.mark.parametrize("kind", ["fixed", "flexible"])
def test_validator_detects_overlap_even_if_model_builder_is_bypassed(monkeypatch, kind):
    import schedence_teaching.solver.engine as engine
    data = scenario(minutes=30)
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 0, "end_minute": 30}, planned_duration_minutes=30)]
    if kind == "fixed":
        monkeypatch.setattr(engine, "add_work_period_intervals", lambda *args: [])
        with pytest.raises(TimetableValidationError, match="teaching_protected_period"):
            run(data)
    else:
        data = scenario(minutes=300)
        with_flexible(data[2], data[3])
        result, inputs = run(data)
        booking = result.administrative_schedule[0]
        teaching = replace(result.schedule[0], start_minute=booking.start_minute, end_minute=booking.start_minute + 30)
        with pytest.raises(TimetableValidationError, match="work_period_staff_clash"):
            validate_work_period_result(data[0], data[1], replace(result, schedule=(teaching,)), **inputs)


@pytest.mark.parametrize("limit", ["max_flexible_blocks", "max_model_variables", "max_model_constraints", "max_prepared_intervals"])
def test_model_limits_reject_instead_of_dropping_requirements(limit):
    data = scenario()
    with_flexible(data[2], data[3], split=True, minimum=15)
    if limit == "max_prepared_intervals":
        data[3].administrative_periods = [admin(data[2].workload_input, identity=f"FIXED-{index}",
            window={"start_minute": 300 + index * 30, "end_minute": 330 + index * 30}, planned_duration_minutes=30)
            for index in range(2)]
    setattr(data[4], limit, 1)
    with pytest.raises(SolverInputError, match="limit"):
        run(data)


def test_unknown_and_infeasible_outputs_emit_no_partial_bookings(monkeypatch):
    data = scenario(limit=1e-12)
    with_flexible(data[2], data[3])
    result, inputs = run(data)
    assert result.status is SolverStatus.UNKNOWN
    broken = replace(result, schedule=("PARTIAL",))
    with pytest.raises(TimetableValidationError, match="partial_failure_output"):
        validate_work_period_result(data[0], data[1], broken, **inputs)
    data = scenario(minutes=30)
    data[3].research_policies = [research_policy()]
    data[3].research_reservations = [research(on_date=FIRST)]
    result, _ = run(data)
    assert result.status is SolverStatus.INFEASIBLE


def test_feasible_objective_status_preserved_without_claiming_optimality(monkeypatch):
    native = cp_model.CpSolver.solve
    def feasible(self, model, *args, **kwargs):
        assert native(self, model, *args, **kwargs) == cp_model.OPTIMAL
        return cp_model.FEASIBLE
    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    combinations = prepare_group_combinations(combination_inputs())
    data = scenario(prepared=combinations.prepared)
    with_flexible(data[2], data[3], duration=30)
    result, _ = run(data, combination_input=combinations)
    assert result.status is SolverStatus.FEASIBLE and result.objective_status == "FEASIBLE"


def test_synthetic_demonstrations(capsys):
    import json
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / "work-period-solver-demo.py"), run_name="__main__")
    output = capsys.readouterr().out
    records = []
    decoder = json.JSONDecoder()
    while output.strip():
        output = output.lstrip()
        record, position = decoder.raw_decode(output)
        records.append(record)
        output = output[position:]
    assert [row["status"] for row in records] == ["OPTIMAL", "OPTIMAL", "INFEASIBLE", "INFEASIBLE"]
    assert records[0]["example"] == "hard-protection" and records[1]["example"] == "flexible-administration"
    assert all(row["staff_summary"]["scheduled_teaching_minutes"] == 360 and
               row["staff_summary"]["reserved_administrative_minutes"] == 240 and
               row["staff_summary"]["administrative_completion_status"] == "NOT_VERIFIED" for row in records[:2])
    assert all(row["teaching"] == row["flexible_administration"] == [] and row["staff_summary"] is None for row in records[2:])


@pytest.mark.parametrize("corruption", ["split", "minimum", "outside_window", "fixed_clash", "flex_clash"])
def test_independent_admin_duration_split_and_resource_checks(corruption):
    data = scenario(minutes=600)
    with_flexible(data[2], data[3], split=corruption == "minimum", minimum=120 if corruption == "minimum" else None,
        eligible=[{"on_date": FIRST, "start_minute": 0, "end_minute": 300}])
    if corruption == "fixed_clash":
        data[3].administrative_periods = [admin(data[2].workload_input,
            window={"start_minute": 300, "end_minute": 540}, planned_duration_minutes=240)]
    if corruption == "flex_clash":
        with_flexible(data[2], data[3], identity="OTHER", eligible=[
            {"on_date": FIRST, "start_minute": 300, "end_minute": 540}])
    result, inputs = run(data)
    row = result.administrative_schedule[0]
    expected = {"split": "unauthorized_administrative_split", "minimum": "administrative_minimum_block",
                "outside_window": "outside_administrative_windows", "fixed_clash": "work_period_staff_clash",
                "flex_clash": "work_period_staff_clash"}[corruption]
    if corruption == "split":
        admin_schedule = (replace(row, end_minute=row.start_minute + 120),
                          replace(row, block_id="administrative:FLEX:2", start_minute=row.start_minute + 120))
    elif corruption == "minimum":
        admin_schedule = (replace(row, end_minute=row.start_minute + 15),)
    elif corruption in {"outside_window", "fixed_clash"}:
        admin_schedule = (replace(row, start_minute=300, end_minute=540),)
    else:
        # Keep metadata valid for each request, but corrupt the physical time.
        second = result.administrative_schedule[1]
        admin_schedule = (row, replace(second, start_minute=row.start_minute, end_minute=row.end_minute))
    with pytest.raises(TimetableValidationError, match=expected):
        validate_work_period_result(data[0], data[1], replace(result, administrative_schedule=admin_schedule), **inputs)


def test_raw_weekly_plans_are_not_added_to_calendar_reservation_totals():
    from schedence_teaching.workload import AdministrativeReservation
    data = scenario()
    data[2].workload_input.administrative_reservations = [AdministrativeReservation(
        reservation_id="LEGACY-PLAN", staff_id="STAFF", duration_minutes=240, status="APPROVED",
        evidence_reference="synthetic:plan", approval_reference="synthetic:approval")]
    with_flexible(data[2], data[3])
    result, _ = run(data)
    assert result.workload_report.staff[0].administration.approved_reservation_minutes == 240
    assert result.work_period_report.staff[0].reserved_administrative_minutes == 240
    assert result.work_period_report.staff[0].verified_completed_administrative_minutes == 0


def test_extra_reserved_time_is_reported_without_inventing_an_allocation_cap():
    data = scenario(minutes=600)
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 300, "end_minute": 540}, planned_duration_minutes=240)]
    with_flexible(data[2], data[3])
    result, _ = run(data)
    row = result.work_period_report.staff[0]
    assert row.administrative_allocation_minutes == 240 and row.reserved_administrative_minutes == 480
    assert row.excess_reserved_minutes == 240 and row.verified_completed_administrative_minutes == 0


@pytest.mark.parametrize("end", [16, 29, 31])
def test_non_grid_fixed_protection_uses_exact_source_minutes_and_safe_bounds(end):
    data = scenario(minutes=90)
    data[3].administrative_periods = [admin(data[2].workload_input,
        window={"start_minute": 0, "end_minute": end}, planned_duration_minutes=end)]
    result, _ = run(data)
    assert result.schedule[0].start_minute >= end
    assert result.work_period_report.fixed_administrative_periods[0].end_minute == end
    assert result.work_period_report.staff[0].fixed_reserved_minutes == end


def test_workload_target_is_not_an_automatic_teaching_cap_in_work_period_mode():
    data = scenario(minutes=600, count=4, duration=120)
    result, _ = run(data)
    assert result.work_period_report.staff[0].scheduled_teaching_minutes == 480
    assert result.work_period_report.staff[0].teaching_requirement_status == "EXCEEDED"


def test_fresh_import_orders_reuse_one_timewindow_contract():
    import subprocess
    import sys
    for imports in ("import schedence_teaching.workload; import schedence_teaching.solver",
                    "import schedence_teaching.solver; import schedence_teaching.workload"):
        output = subprocess.run([sys.executable, "-B", "-c", imports +
            "; from schedence_teaching.solver import TimeWindow; from schedence_teaching.schemas.time import TimeWindow as Shared; assert TimeWindow is Shared"],
            capture_output=True, text=True, timeout=30)
        assert output.returncode == 0, output.stderr


def test_explicit_legacy_combined_target_is_not_mislabeled_as_teaching_requirement():
    from schedence_teaching.workload import WorkloadTargetBasis
    data = scenario()
    data[2].workload_input.policy = confirmed_campus_policy(target_basis=WorkloadTargetBasis.TOTAL_RECOGNIZED_WORK)
    result, _ = run(data)
    assert result.work_period_report.staff[0].required_teaching_minutes is None
    assert result.work_period_report.staff[0].teaching_requirement_status == "UNSPECIFIED"


def test_instructor_policy_stays_unspecified_and_no_admin_time_is_created():
    prepared = demand({"OFFER": [("STAFF", "TEAM_MEMBER")]}, staff_types={"STAFF": "INSTRUCTOR"})
    data = scenario(prepared=prepared)
    result, _ = run(data)
    row = result.work_period_report.staff[0]
    assert row.required_teaching_minutes == 1200 and row.administrative_allocation_minutes is None
    assert row.campus_attendance_status == "UNSPECIFIED" and "campus_attendance_policy" in row.missing_evidence
    assert result.administrative_schedule == ()
