"""Synthetic joint combination scheduling; no campus calendars or people."""

from dataclasses import replace
from pathlib import Path
import runpy

import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from schedence_teaching.preparation import prepare_group_combinations
from schedence_teaching.schemas import GroupCombinationInput, GroupMembershipEvidence, PopulationRelationship, Stream
from schedence_teaching.solver import (
    CombinationSolverConfig, DeliveryObjective, ScheduledDelivery, SolverInputError, SolverStatus,
    TimetableValidationError, solve_timetable, validate_delivery_result, validate_delivery_timetable,
    VenueDayInterval,
)

from test_group_combinations import make_inputs, shared_content
from test_staff_solver import windows
from test_venue_solver import physical


def inputs(count=2, *, frequency=2, **kwargs):
    payload = make_inputs(count, sizes=[20] * count, **kwargs).model_dump(round_trip=True)
    for row in payload["teaching_input"]["session_requirements"]:
        row.update(duration_minutes=30, sessions_per_week=frequency)
    return GroupCombinationInput.model_validate(payload)


def venues(capacity=40, *, minutes=120, large_minutes=None, rules=(), shared=()):
    rooms = [("ROOM", capacity, "Lecture Hall")]
    if large_minutes is not None:
        rooms.append(("SMALL", 20, "Lecture Hall"))
    result = physical(rooms, rules=rules, shared=shared)
    for calendar in result.calendars:
        calendar.available_windows["DAY"][0].end_minute = (
            large_minutes if calendar.venue_id == "ROOM" and large_minutes is not None else minutes)
    return result


def solve(source=None, *, rooms=None, minutes=120, options=None, config=None):
    combinations = prepare_group_combinations(source or inputs())
    config = config or windows(minutes)
    result = solve_timetable(combinations.prepared, config, combination_input=combinations,
                             combination_config=options, venue_input=rooms)
    if result.status in {SolverStatus.OPTIMAL, SolverStatus.FEASIBLE}:
        assert validate_delivery_result(combinations.prepared, config, result, combination_input=combinations,
                                        combination_config=options, venue_input=rooms) == result.validation
    return combinations, config, result


def test_combined_physical_deliveries_cover_four_originals_once():
    source, rooms = inputs(), venues()
    before = source.model_dump(round_trip=True), rooms.model_dump(round_trip=True)
    combinations, _, result = solve(source, rooms=rooms)
    assert result.status is SolverStatus.OPTIMAL
    assert len(result.schedule) == result.validation.combined_deliveries == 2
    assert result.validation.separate_deliveries == 0
    assert result.validation.expected_occurrences == result.validation.assigned_occurrences == 4
    assert result.validation.lecturer_contact_minutes == 60
    assert result.objective_value == 2 * 121 + 60 and result.objective_status == "OPTIMAL"
    coverage = [row.occurrence_id for item in result.schedule for row in item.covered_sessions]
    assert sorted(coverage) == [row.occurrence_id for row in combinations.separate_sessions]
    for item in result.schedule:
        assert item.is_combined and item.combined_student_count == 40
        assert item.group_ids == ("GROUP-1", "GROUP-2")
        assert item.assigned_staff_id == "STAFF" and item.assigned_venue_id == "ROOM"
        assert item.duration_minutes == 30 and len(item.to_dict()["original_requirements"]) == 2
    output = result.to_dict()
    assert not output["university_ready"] and output["actual_delivery_count"] == 2
    assert output["original_required_occurrence_count"] == 4
    assert output["actual_lecturer_contact_minutes"] == 60
    assert (source.model_dump(round_trip=True), rooms.model_dump(round_trip=True)) == before


def test_capacity_disables_combining_but_preserves_four_separate_deliveries():
    _, _, result = solve(rooms=venues(20))
    assert result.status is SolverStatus.OPTIMAL
    assert len(result.schedule) == result.validation.separate_deliveries == 4
    assert result.validation.combined_deliveries == 0 and result.validation.lecturer_contact_minutes == 120
    assert result.objective_value == 4 * 121 + 120


def test_mixed_weekly_selection_when_large_venue_only_has_one_interval():
    _, _, result = solve(rooms=venues(large_minutes=30))
    assert result.status is SolverStatus.OPTIMAL
    assert result.validation.combined_deliveries == 1 and result.validation.separate_deliveries == 2
    assert result.validation.delivery_count == 3 and result.validation.lecturer_contact_minutes == 90
    combined = next(row for row in result.schedule if row.is_combined)
    assert combined.start_minute == 0 and combined.end_minute == 30
    assert combined.assigned_venue_id == "ROOM" and combined.combined_student_count == 40


@pytest.mark.parametrize("count", [3, 4, 5])
def test_larger_eligible_combinations(count):
    _, _, result = solve(inputs(count), rooms=venues(count * 20))
    assert len(result.schedule) == 2
    assert all(len(row.group_ids) == count and row.combined_student_count == count * 20 for row in result.schedule)


def test_capacity_selects_pair_plus_separate_for_three_groups():
    _, _, result = solve(inputs(3), rooms=venues(40), minutes=180)
    assert result.validation.combined_deliveries == 2
    assert result.validation.separate_deliveries == 2
    assert result.validation.lecturer_contact_minutes == 120


def test_combined_staff_is_intersection_across_explicit_shared_content_offerings():
    source = shared_content(inputs(separate_offerings=True,
                                   staff_pools={"OFFER-1": ["FIRST", "COMMON"], "OFFER-2": ["SECOND", "COMMON"]}))
    _, _, result = solve(source, rooms=venues())
    assert all(row.assigned_staff_id == "COMMON" and row.is_combined for row in result.schedule)


@pytest.mark.parametrize("change", ["permission", "specialization", "unknown_population", "overlap", "content", "frequency", "duration", "staff"])
def test_incompatible_inputs_remain_separate(change):
    source = inputs(separate_offerings=change in {"content", "staff"},
                    staff_pools={"OFFER-1": ["FIRST"], "OFFER-2": ["SECOND"]} if change == "staff" else None)
    if change == "permission":
        source.policy.allow_combination = False
    elif change == "specialization":
        source.group_specializations["GROUP-2"] = "Different"
    elif change == "unknown_population":
        source.population_evidence = []
    elif change == "overlap":
        source.population_evidence[0].relationship = PopulationRelationship.OVERLAPPING
    elif change == "frequency":
        source.teaching_input.session_requirements[1].sessions_per_week = 1
    elif change == "duration":
        source.teaching_input.session_requirements[1].duration_minutes = 15
    combinations, _, result = solve(source, minutes=180)
    assert not combinations.candidates
    assert result.status is SolverStatus.OPTIMAL
    assert result.validation.combined_deliveries == 0


def test_each_covered_requirement_venue_policy_is_enforced():
    rooms = venues(rules=[{"requirement_id": "REQ2", "excluded_venue_ids": ["ROOM"]}], large_minutes=120)
    _, _, result = solve(rooms=rooms)
    assert result.validation.combined_deliveries == 0
    assert all(row.assigned_venue_id == "SMALL" for row in result.schedule if row.group_ids == ("GROUP-2",))


def test_combined_venue_block_prevents_selection_in_only_usable_slot():
    rooms = venues(large_minutes=30)
    rooms.calendars[0].blocked_periods = [VenueDayInterval(day="DAY", start_minute=0, end_minute=30)]
    _, _, result = solve(rooms=rooms)
    assert result.validation.combined_deliveries == 0 and result.validation.separate_deliveries == 4


def add_other(source, group_id, *, parent=None, known_overlap=False):
    payload = source.model_dump(round_trip=True)
    teaching = payload["teaching_input"]
    teaching["staff_members"].append({"staff_id": "OTHER", "staff_name": "Synthetic Other", "position": "Synthetic", "staff_type": "ACADEMIC"})
    offering = {**teaching["module_offerings"][0], "offering_id": "OTHER-OFFER"}
    teaching["module_offerings"].append(offering)
    teaching["teaching_team_members"].append({"offering_id": "OTHER-OFFER", "staff_id": "OTHER", "role": "TEAM_MEMBER"})
    if group_id not in {row["group_id"] for row in teaching["student_groups"]}:
        teaching["student_groups"].append({**teaching["student_groups"][0], "group_id": group_id, "parent_group_id": parent})
    teaching["session_requirements"].append({**teaching["session_requirements"][0], "requirement_id": "OTHER-REQ",
                                              "offering_id": "OTHER-OFFER", "group_id": group_id, "sessions_per_week": 1, "session_type": "TUTORIAL"})
    if known_overlap:
        payload["population_evidence"].append({"group_ids": ["GROUP-2", group_id], "relationship": "OVERLAPPING",
                                               "evidence_reference": "synthetic:known-overlap"})
    return GroupCombinationInput.model_validate(payload)


@pytest.mark.parametrize("relation", ["same", "child", "parent", "explicit", "roster"])
def test_attendance_conflict_for_any_member_of_combination(relation):
    source = inputs(frequency=1)
    source = add_other(source, "GROUP-2" if relation == "same" else "OTHER-GROUP",
                       parent="GROUP-2" if relation == "child" else None,
                       known_overlap=relation == "explicit")
    if relation == "parent":
        source.teaching_input.student_groups[1].parent_group_id = "OTHER-GROUP"
    if relation == "roster":
        source.memberships = [
            GroupMembershipEvidence(group_id=group, student_ids=["synthetic-student"], complete=False, evidence_reference="synthetic:roster")
            for group in ("GROUP-2", "OTHER-GROUP")]
    combinations, config, result = solve(source, minutes=60)
    assert result.validation.combined_deliveries == 1
    combined = next(row for row in result.schedule if row.is_combined)
    other = next(row for row in result.schedule if not row.is_combined)
    assert combined.end_minute <= other.start_minute or other.end_minute <= combined.start_minute
    invalid = [row if row.is_combined else replace(row, start_minute=combined.start_minute, end_minute=combined.end_minute)
               for row in result.schedule]
    with pytest.raises(TimetableValidationError, match="clash"):
        validate_delivery_timetable(combinations.prepared, config, invalid, combination_input=combinations)


def test_unknown_overlap_does_not_add_a_student_policy():
    source = add_other(inputs(frequency=1), "UNRELATED")
    _, _, result = solve(source, minutes=30)
    assert result.status is SolverStatus.OPTIMAL and result.validation.delivery_count == 2
    assert {row.start_minute for row in result.schedule} == {0}


@pytest.mark.parametrize("mode,expected", [(DeliveryObjective.DELIVERIES_THEN_MINUTES, 302),
                                          (DeliveryObjective.DELIVERIES, 2),
                                          (DeliveryObjective.CONTACT_MINUTES, 60),
                                          (DeliveryObjective.FEASIBILITY, None)])
def test_explicit_objective_modes(mode, expected):
    _, _, result = solve(options=CombinationSolverConfig(objective=mode))
    assert result.objective_mode == mode.value
    if mode is not DeliveryObjective.FEASIBILITY:
        assert result.validation.delivery_count == 2 and result.objective_value == expected
        assert result.objective_status == "OPTIMAL"
    else:
        assert result.objective_value is None and result.objective_status is None


def test_fixed_inputs_seed_and_single_worker_produce_repeatable_selection():
    first = solve(rooms=venues(large_minutes=30))[2]
    second = solve(rooms=venues(large_minutes=30))[2]
    assert first.schedule == second.schedule


@pytest.mark.parametrize("reason", ["capacity", "time", "staff", "student"])
def test_infeasible_outputs_are_empty(reason):
    source, rooms, minutes = inputs(), venues(), 120
    if reason == "capacity":
        rooms = venues(19)
    elif reason == "time":
        minutes = 15
    elif reason == "staff":
        source.teaching_input.teaching_team_members = []
    elif reason == "student":
        source = add_other(inputs(frequency=1), "GROUP-2")
        minutes, rooms = 30, None
    _, _, result = solve(source, rooms=rooms, minutes=minutes)
    assert result.status is SolverStatus.INFEASIBLE
    assert result.schedule == () and result.validation is None and result.objective_value is None
    assert result.to_dict()["actual_delivery_count"] == 0


def test_native_tiny_timeout_returns_unknown_without_partial_schedule():
    _, _, result = solve(inputs(5), config=windows(120, limit=1e-12))
    assert result.status is SolverStatus.UNKNOWN
    assert result.schedule == () and result.validation is None and result.objective_status is None


def test_controlled_nonoptimal_feasible_retains_valid_output_without_optimal_claim(monkeypatch):
    native = cp_model.CpSolver.solve

    def feasible(self, model, *args, **kwargs):
        assert native(self, model, *args, **kwargs) == cp_model.OPTIMAL
        return cp_model.FEASIBLE

    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    _, _, result = solve(rooms=venues())
    assert result.status is SolverStatus.FEASIBLE and result.objective_status == "FEASIBLE"
    assert result.validation.valid_for_implemented_constraints


@pytest.mark.parametrize("field,value", [("max_candidates", 1), ("max_delivery_alternatives", 5),
                                         ("max_resource_choices", 1), ("max_model_variables", 1),
                                         ("max_model_constraints", 1)])
def test_explicit_size_guards_fail_without_truncation(field, value):
    with pytest.raises(SolverInputError, match="limit exceeded"):
        solve(inputs(3), options=CombinationSolverConfig(**{field: value}))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_size_limit_is_strict_positive_integer(value):
    with pytest.raises(ValidationError):
        CombinationSolverConfig(max_candidates=value)


def test_stale_or_mismatched_preparation_rejected():
    combinations = prepare_group_combinations(inputs())
    stale = replace(combinations, candidates=())
    with pytest.raises(SolverInputError, match="Stale"):
        solve_timetable(combinations.prepared, windows(), combination_input=stale)
    other = prepare_group_combinations(inputs(3))
    with pytest.raises(SolverInputError, match="incompatible"):
        solve_timetable(other.prepared, windows(), combination_input=combinations)


def test_combination_options_cannot_silently_change_legacy_mode():
    combinations = prepare_group_combinations(inputs())
    with pytest.raises(SolverInputError, match="requires"):
        solve_timetable(combinations.prepared, windows(), combination_config=CombinationSolverConfig())


@pytest.mark.parametrize("corruption", ["unknown", "duplicate", "missing", "count", "bool_count", "groups", "flag", "coverage", "session_metadata", "staff", "time", "venue", "capacity", "additional_unselected"])
def test_independent_validator_rejects_corrupted_deliveries(corruption):
    rooms = venues()
    combinations, config, result = solve(rooms=rooms)
    schedule = list(result.schedule)
    item = schedule[0]
    if corruption == "unknown":
        schedule[0] = replace(item, delivery_id="invented")
    elif corruption == "duplicate":
        schedule.append(item)
    elif corruption == "missing":
        schedule.pop()
    elif corruption == "count":
        schedule[0] = replace(item, combined_student_count=20)
    elif corruption == "bool_count":
        schedule[0] = replace(item, combined_student_count=True)
    elif corruption == "groups":
        schedule[0] = replace(item, group_ids=("GROUP-1",))
    elif corruption == "flag":
        schedule[0] = replace(item, is_combined=False)
    elif corruption == "coverage":
        schedule[0] = replace(item, covered_sessions=item.covered_sessions[:1])
    elif corruption == "session_metadata":
        schedule[0] = replace(item, covered_sessions=(replace(item.covered_sessions[0], duration_minutes=15), item.covered_sessions[1]))
    elif corruption == "staff":
        schedule[0] = replace(item, assigned_staff_id="UNKNOWN")
    elif corruption == "time":
        schedule[0] = replace(item, start_minute=True)
    elif corruption == "venue":
        schedule[0] = replace(item, assigned_venue_id="UNKNOWN")
    elif corruption == "capacity":
        rooms.venues[0].capacity = 20
    elif corruption == "additional_unselected":
        session = item.covered_sessions[0]
        schedule.append(ScheduledDelivery(session.occurrence_id, False, (session,), (session.group_id,), 20,
                                          item.day, item.start_minute, item.end_minute, item.assigned_staff_id, item.assigned_venue_id))
    with pytest.raises(TimetableValidationError):
        validate_delivery_timetable(combinations.prepared, config, schedule, combination_input=combinations, venue_input=rooms)


@pytest.mark.parametrize("field,value", [("original_occurrence_count", 0), ("objective_value", 0),
                                         ("objective_status", "FEASIBLE"), ("objective_mode", "FEASIBILITY"),
                                         ("combination_enabled", False), ("venue_enabled", False)])
def test_result_reporting_is_independently_validated(field, value):
    rooms = venues()
    combinations, config, result = solve(rooms=rooms)
    with pytest.raises(TimetableValidationError, match="result_summary_changed"):
        validate_delivery_result(combinations.prepared, config, replace(result, **{field: value}),
                                  combination_input=combinations, venue_input=rooms)


def test_workload_summary_cannot_be_forged():
    combinations, config, result = solve()
    summary = replace(result.validation, lecturer_contact_minutes=120)
    with pytest.raises(TimetableValidationError, match="result_summary_changed"):
        validate_delivery_result(combinations.prepared, config, replace(result, validation=summary), combination_input=combinations)


def test_validator_checks_all_offerings_not_only_first_covered_offering():
    source = shared_content(inputs(separate_offerings=True,
                                   staff_pools={"OFFER-1": ["FIRST", "COMMON"], "OFFER-2": ["SECOND", "COMMON"]}))
    combinations, config, result = solve(source)
    schedule = [replace(row, assigned_staff_id="FIRST") for row in result.schedule]
    with pytest.raises(TimetableValidationError, match="ineligible_staff"):
        validate_delivery_timetable(combinations.prepared, config, schedule, combination_input=combinations)


def test_physical_delivery_requires_a_venue_and_venue_free_rejects_one():
    rooms = venues()
    combinations, config, result = solve(rooms=rooms)
    with pytest.raises(TimetableValidationError, match="invalid_venue_id"):
        validate_delivery_timetable(combinations.prepared, config, [replace(row, assigned_venue_id=None) for row in result.schedule],
                                     combination_input=combinations, venue_input=rooms)
    with pytest.raises(TimetableValidationError, match="unexpected_venue_assignment"):
        validate_delivery_timetable(combinations.prepared, config, result.schedule, combination_input=combinations)


def test_empty_combination_input_retains_empty_demand_success():
    source = inputs()
    source.teaching_input.session_requirements = []
    _, _, result = solve(source)
    assert result.status is SolverStatus.OPTIMAL and result.schedule == ()
    assert result.objective_value == 0 and result.validation.lecturer_contact_minutes == 0


def two_contents():
    source = inputs(4, frequency=1, separate_offerings=True)
    payload = source.model_dump(round_trip=True)
    payload["requirement_permissions"] = [
        {"requirement_id": f"REQ{index}", "shared_content_reference": "synthetic:short" if index <= 2 else "synthetic:long"}
        for index in range(1, 5)]
    for row in payload["teaching_input"]["session_requirements"][:2]:
        row["duration_minutes"] = 15
    return GroupCombinationInput.model_validate(payload)


def test_contact_minutes_break_equal_delivery_count_tie():
    # Large room can fit either the 30-minute combination or the 15-minute one,
    # never both. Three deliveries are required either way; 60 beats 75 minutes.
    _, _, result = solve(two_contents(), rooms=venues(large_minutes=30))
    assert result.validation.delivery_count == 3
    assert result.validation.lecturer_contact_minutes == 60
    combined = next(row for row in result.schedule if row.is_combined)
    assert combined.group_ids == ("GROUP-3", "GROUP-4")
    assert result.objective_value == 3 * 91 + 60


def test_combined_delivery_and_weekend_session_share_one_staff_resource():
    source = add_other(inputs(frequency=1), "WEEKEND-GROUP")
    source.teaching_input.module_offerings[-1].stream = Stream.WEEKEND
    source.teaching_input.student_groups[-1].stream = Stream.WEEKEND
    source.teaching_input.teaching_team_members[-1].staff_id = "STAFF"
    _, _, impossible = solve(source, config=windows(30, both_streams=True))
    assert impossible.status is SolverStatus.INFEASIBLE
    _, _, result = solve(source, config=windows(60, both_streams=True))
    assert result.status is SolverStatus.OPTIMAL
    assert result.validation.combined_deliveries == 1
    assert len({row.start_minute for row in result.schedule}) == 2


def test_combined_delivery_and_unrelated_content_share_a_physical_resource():
    source = add_other(inputs(frequency=1), "OTHER-GROUP")
    rooms = venues(large_minutes=60, shared=[{"resource_id": "synthetic:shared", "venue_ids": ["ROOM", "SMALL"]}],
                   rules=[{"requirement_id": "OTHER-REQ", "eligible_venue_ids": ["SMALL"]}])
    combinations, config, result = solve(source, rooms=rooms, minutes=60)
    combined = next(row for row in result.schedule if row.is_combined)
    other = next(row for row in result.schedule if not row.is_combined)
    assert combined.assigned_venue_id == "ROOM" and other.assigned_venue_id == "SMALL"
    assert combined.end_minute <= other.start_minute or other.end_minute <= combined.start_minute
    invalid = [row if row.is_combined else replace(row, start_minute=combined.start_minute, end_minute=combined.end_minute)
               for row in result.schedule]
    with pytest.raises(TimetableValidationError, match="shared_space_clash"):
        validate_delivery_timetable(combinations.prepared, config, invalid, combination_input=combinations, venue_input=rooms)


@pytest.mark.parametrize("field", ["delivery_count", "combined_deliveries", "separate_deliveries", "lecturer_contact_minutes"])
def test_all_reported_delivery_counts_and_minutes_reject_corruption(field):
    combinations, config, result = solve()
    summary = replace(result.validation, **{field: 999})
    with pytest.raises(TimetableValidationError, match="result_summary_changed"):
        validate_delivery_result(combinations.prepared, config, replace(result, validation=summary), combination_input=combinations)


def test_unknown_solver_status_does_not_emit_a_completed_combination_timetable(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args: cp_model.MODEL_INVALID)
    _, _, result = solve()
    assert result.status is SolverStatus.MODEL_INVALID
    assert result.schedule == () and result.validation is None


def test_sibling_combination_deduplicates_interval_in_parent_conflict_chain():
    source = inputs(frequency=1)
    payload = source.model_dump(round_trip=True)
    groups = payload["teaching_input"]["student_groups"]
    groups.append({**groups[0], "group_id": "PARENT", "student_count": 40})
    groups[0]["parent_group_id"] = groups[1]["parent_group_id"] = "PARENT"
    _, _, result = solve(GroupCombinationInput.model_validate(payload), minutes=30)
    assert result.status is SolverStatus.OPTIMAL and result.validation.combined_deliveries == 1


def test_known_parent_overlap_does_not_invent_overlap_between_disjoint_children():
    source = inputs(frequency=1)
    payload = source.model_dump(round_trip=True)
    groups = payload["teaching_input"]["student_groups"]
    groups.extend([{**groups[0], "group_id": "PARENT-1"}, {**groups[1], "group_id": "PARENT-2"}])
    groups[0]["parent_group_id"], groups[1]["parent_group_id"] = "PARENT-1", "PARENT-2"
    payload["population_evidence"].append({"group_ids": ["PARENT-1", "PARENT-2"], "relationship": "OVERLAPPING",
                                           "evidence_reference": "synthetic:parent-overlap"})
    _, _, result = solve(GroupCombinationInput.model_validate(payload), minutes=30)
    assert result.status is SolverStatus.OPTIMAL and result.validation.combined_deliveries == 1


def test_documented_synthetic_example_has_combined_separate_and_mixed_outputs():
    script = Path(__file__).parents[1] / "examples" / "combined-delivery-demo.py"
    outputs = runpy.run_path(str(script))["run_scenarios"]()
    for name, count, minutes in [("combined", 2, 240), ("separate", 4, 480), ("mixed", 3, 360)]:
        assert outputs[name]["status"] == "OPTIMAL"
        assert outputs[name]["actual_delivery_count"] == count
        assert outputs[name]["actual_lecturer_contact_minutes"] == minutes
        assert outputs[name]["constraint_validation"]["assigned_occurrences"] == 4
