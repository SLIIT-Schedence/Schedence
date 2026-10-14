"""Pure synthetic combinations and coverage; no solver or university policy changes."""

from dataclasses import replace
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schedence_teaching.preparation import PreparationError, prepare_group_combinations, validate_delivery_selection
from schedence_teaching.schemas import DeliverySelection, GroupCombinationInput


def make_inputs(count=2, *, sizes=None, separate_offerings=False, staff_pools=None):
    sizes = sizes or [120] * count
    group_ids = [f"GROUP-{index}" for index in range(1, count + 1)]
    offering_ids = [f"OFFER-{index}" for index in range(1, count + 1)] if separate_offerings else ["OFFER"]
    staff_pools = staff_pools or {offering: ["STAFF"] for offering in offering_ids}
    staff_ids = sorted({staff for pool in staff_pools.values() for staff in pool})
    teaching = {
        "staff_members": [{"staff_id": staff, "staff_name": f"Synthetic {staff}", "position": "Synthetic",
                           "staff_type": "ACADEMIC"} for staff in staff_ids],
        "module_offerings": [{"offering_id": offering, "module_code": "SYNTHETIC-SHARED-CODE",
                              "module_name": f"Synthetic {offering}", "academic_year": 2, "semester": 1,
                              "specialization": "Synthetic", "degree_type": "Synthetic", "stream": "WEEKDAY"}
                             for offering in offering_ids],
        "teaching_team_members": [{"offering_id": offering, "staff_id": staff, "role": "TEAM_MEMBER"}
                                  for offering, pool in staff_pools.items() for staff in pool],
        "student_groups": [{"group_id": group, "academic_year": 2, "semester": 1, "stream": "WEEKDAY",
                            "student_count": size} for group, size in zip(group_ids, sizes, strict=True)],
        "session_requirements": [{"requirement_id": f"REQ{index}", "offering_id": f"OFFER-{index}" if separate_offerings else "OFFER",
                                  "group_id": group, "session_type": "LECTURE", "duration_minutes": 120, "sessions_per_week": 2}
                                 for index, group in enumerate(group_ids, 1)],
    }
    return GroupCombinationInput(
        teaching_input=teaching, group_specializations={group: "Synthetic" for group in group_ids},
        policy={"allow_combination": True},
        population_evidence=[{"group_ids": group_ids, "relationship": "DISJOINT", "evidence_reference": "synthetic:partition"}]
        if count >= 2 else [],
    )


def diagnostics(result):
    return {row.code for row in result.diagnostics}


def shared_content(inputs):
    payload = inputs.model_dump(round_trip=True)
    payload["requirement_permissions"] = [{"requirement_id": row["requirement_id"], "shared_content_reference": "synthetic:same-lecture"}
                                         for row in payload["teaching_input"]["session_requirements"]]
    return GroupCombinationInput.model_validate(payload)


def test_two_groups_have_one_optional_candidate_and_preserved_original_demand():
    inputs = make_inputs()
    original = inputs.model_dump(round_trip=True)
    result = prepare_group_combinations(inputs)
    assert len(result.candidates) == 1 and len(result.separate_sessions) == 4
    candidate = result.candidates[0]
    assert candidate.group_ids == ("GROUP-1", "GROUP-2") and candidate.requirement_ids == ("REQ1", "REQ2")
    assert candidate.offering_ids == ("OFFER",) and candidate.content_kind == "offering"
    assert candidate.combined_student_count == 240 and candidate.eligible_staff_ids == ("STAFF",)
    assert candidate.sessions_per_week == len(candidate.occurrences) == 2 and candidate.duration_minutes == 120
    assert candidate.to_dict()["weekly_delivery_minutes"] == 240
    assert [session.occurrence_id for session in candidate.occurrences[0].covered_sessions] == ["REQ1:1", "REQ2:1"]
    assert [session.occurrence_id for session in candidate.occurrences[1].covered_sessions] == ["REQ1:2", "REQ2:2"]
    assert inputs.model_dump(round_trip=True) == original
    assert result.prepared.input_snapshot == inputs.teaching_input
    assert result.prepared.summary.weekly_contact_minutes == 480
    assert result.to_dict()["alternatives_only"] and result.to_dict()["university_ready"] is False


@pytest.mark.parametrize("count,expected", [(3, 4), (4, 11), (5, 26)])
def test_all_eligible_subsets_include_more_than_two_groups(count, expected):
    result = prepare_group_combinations(make_inputs(count))
    assert len(result.candidates) == expected
    largest = max(result.candidates, key=lambda candidate: len(candidate.group_ids))
    assert len(largest.group_ids) == count and largest.combined_student_count == 120 * count
    assert len(result.separate_sessions) == count * 2


def test_supplied_sizes_and_explicit_policy_limits_are_respected():
    inputs = make_inputs(4, sizes=[5, 7, 11, 13])
    inputs.policy.max_groups = 3
    inputs.policy.max_combined_students = 25
    result = prepare_group_combinations(inputs)
    assert all(len(candidate.group_ids) <= 3 and candidate.combined_student_count <= 25 for candidate in result.candidates)
    assert any(candidate.group_ids == ("GROUP-1", "GROUP-2", "GROUP-3") and candidate.combined_student_count == 23
               for candidate in result.candidates)
    assert "student_count_limit" in diagnostics(result)
    assert [group.student_count for group in result.prepared.input_snapshot.student_groups] == [5, 7, 11, 13]


def test_default_permission_is_disabled_and_explicit_local_permissions_can_opt_in():
    payload = make_inputs(3).model_dump(round_trip=True)
    del payload["policy"]
    disabled = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert disabled.candidates == () and len(disabled.separate_sessions) == 6
    payload["requirement_permissions"] = [{"requirement_id": f"REQ{index}", "allow_combination": True} for index in (1, 2)]
    enabled = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert len(enabled.candidates) == 1 and enabled.candidates[0].requirement_ids == ("REQ1", "REQ2")


def test_local_veto_overrides_global_permission():
    payload = make_inputs(3).model_dump(round_trip=True)
    payload["requirement_permissions"] = [{"requirement_id": "REQ2", "allow_combination": False}]
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert len(result.candidates) == 1 and result.candidates[0].requirement_ids == ("REQ1", "REQ3")


def test_same_specialization_and_module_code_do_not_merge_distinct_offerings():
    result = prepare_group_combinations(make_inputs(separate_offerings=True))
    assert result.candidates == () and "incompatible_teaching_content" in diagnostics(result)


def test_explicit_shared_content_can_link_distinct_offerings_without_replacing_ids():
    result = prepare_group_combinations(shared_content(make_inputs(separate_offerings=True)))
    candidate = result.candidates[0]
    assert candidate.offering_ids == ("OFFER-1", "OFFER-2")
    assert candidate.content_kind == "shared_content" and candidate.content_reference == "synthetic:same-lecture"
    assert {session.offering_id for session in candidate.occurrences[0].covered_sessions} == {"OFFER-1", "OFFER-2"}


@pytest.mark.parametrize("problem", ["different", "one_explicit", "namespace"])
def test_conflicting_or_incomplete_shared_content_references_do_not_match(problem):
    payload = make_inputs().model_dump(round_trip=True)
    payload["requirement_permissions"] = [
        {"requirement_id": "REQ1", "shared_content_reference": "OFFER"},
        {"requirement_id": "REQ2", "shared_content_reference": "OTHER" if problem == "different" else "OFFER"},
    ]
    if problem in ("one_explicit", "namespace"):
        payload["requirement_permissions"] = payload["requirement_permissions"][:1]
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and "incompatible_teaching_content" in diagnostics(result)


@pytest.mark.parametrize("field,value,code", [("duration_minutes", 90, "duration_mismatch"),
    ("sessions_per_week", 1, "frequency_mismatch"), ("session_type", "LAB", "unsupported_session_type"),
    ("session_type", "TUTORIAL", "unsupported_session_type"), ("session_type", "WORKSHOP", "unsupported_session_type")])
def test_incompatible_session_requirements_remain_separate(field, value, code):
    payload = make_inputs().model_dump(round_trip=True)
    payload["teaching_input"]["session_requirements"][1][field] = value
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and code in diagnostics(result)
    assert len(result.prepared.input_snapshot.session_requirements) == 2


@pytest.mark.parametrize("field,value", [("academic_year", 3), ("semester", 2), ("stream", "WEEKEND")])
def test_period_mismatches_do_not_combine_even_with_explicit_shared_content(field, value):
    payload = shared_content(make_inputs(separate_offerings=True)).model_dump(round_trip=True)
    payload["teaching_input"]["student_groups"][1][field] = value
    payload["teaching_input"]["module_offerings"][1][field] = value
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and "incompatible_period" in diagnostics(result)


@pytest.mark.parametrize("problem,code", [("different", "incompatible_specialization"), ("missing", "missing_group_specialization")])
def test_specialization_is_explicit_and_not_inferred_from_offering(problem, code):
    payload = make_inputs().model_dump(round_trip=True)
    if problem == "different":
        payload["group_specializations"]["GROUP-2"] = "Different Synthetic Specialization"
    else:
        del payload["group_specializations"]["GROUP-2"]
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and code in diagnostics(result)


@pytest.mark.parametrize("relation,code", [(None, "unknown_population_disjointness"),
    ("UNKNOWN", "unknown_population_disjointness"), ("OVERLAPPING", "overlapping_populations")])
def test_unknown_or_overlapping_populations_cannot_be_added(relation, code):
    payload = make_inputs().model_dump(round_trip=True)
    payload["population_evidence"] = [] if relation is None else [{
        "group_ids": ["GROUP-1", "GROUP-2"], "relationship": relation,
        "evidence_reference": "synthetic:overlap-review" if relation == "OVERLAPPING" else None,
    }]
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and code in diagnostics(result)


def test_disjointness_is_not_transitive_between_evidence_sets():
    payload = make_inputs(3).model_dump(round_trip=True)
    payload["population_evidence"] = [{"group_ids": pair, "relationship": "DISJOINT", "evidence_reference": f"synthetic:partition-{index}"}
                                      for index, pair in enumerate([["GROUP-1", "GROUP-2"], ["GROUP-2", "GROUP-3"]])]
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert {candidate.requirement_ids for candidate in result.candidates} == {("REQ1", "REQ2"), ("REQ2", "REQ3")}
    assert "unknown_population_disjointness" in diagnostics(result)


def test_sibling_hierarchy_does_not_prove_disjointness():
    payload = make_inputs().model_dump(round_trip=True)
    payload["population_evidence"] = []
    parent = {**payload["teaching_input"]["student_groups"][0], "group_id": "PARENT", "student_count": 240}
    payload["teaching_input"]["student_groups"].append(parent)
    for group in payload["teaching_input"]["student_groups"][:2]:
        group["parent_group_id"] = "PARENT"
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and "unknown_population_disjointness" in diagnostics(result)


@pytest.mark.parametrize("assert_disjoint", [False, True])
def test_parent_child_overlap_blocks_or_contradicts_disjoint_evidence(assert_disjoint):
    payload = make_inputs().model_dump(round_trip=True)
    payload["teaching_input"]["student_groups"][1]["parent_group_id"] = "GROUP-1"
    if assert_disjoint:
        with pytest.raises(PreparationError, match="contradicts"):
            prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    else:
        payload["population_evidence"] = []
        result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
        assert not result.candidates and "overlapping_populations" in diagnostics(result)


def roster_inputs(*, shared=False, complete=True):
    payload = make_inputs(sizes=[2, 2]).model_dump(round_trip=True)
    payload["population_evidence"] = []
    payload["memberships"] = [
        {"group_id": "GROUP-1", "student_ids": ["PRIVATE-SYNTH-STUDENT-A", "PRIVATE-SYNTH-STUDENT-B"] if complete else ["PRIVATE-SYNTH-STUDENT-A"],
         "complete": complete, "evidence_reference": "synthetic:roster-a"},
        {"group_id": "GROUP-2", "student_ids": ["PRIVATE-SYNTH-STUDENT-A" if shared else "PRIVATE-SYNTH-STUDENT-C", "PRIVATE-SYNTH-STUDENT-D"] if complete else ["PRIVATE-SYNTH-STUDENT-A" if shared else "PRIVATE-SYNTH-STUDENT-C"],
         "complete": complete, "evidence_reference": "synthetic:roster-b"},
    ]
    return GroupCombinationInput.model_validate(payload)


def test_complete_memberships_prove_disjointness_without_manual_pairings_or_student_id_reports():
    inputs = roster_inputs()
    result = prepare_group_combinations(inputs)
    assert len(result.candidates) == 1 and result.candidates[0].combined_student_count == 4
    assert result.candidates[0].population_evidence_references == ("synthetic:roster-a", "synthetic:roster-b")
    assert "PRIVATE-SYNTH-STUDENT" not in json.dumps(result.to_dict())
    assert result.input_snapshot.memberships == inputs.memberships


@pytest.mark.parametrize("complete,shared,code", [(True, True, "overlapping_populations"),
    (False, True, "overlapping_populations"), (False, False, "unknown_population_disjointness")])
def test_shared_or_incomplete_rosters_do_not_prove_disjointness(complete, shared, code):
    result = prepare_group_combinations(roster_inputs(shared=shared, complete=complete))
    assert not result.candidates and code in diagnostics(result)


def test_contradictory_roster_and_partition_evidence_is_rejected_without_student_ids_in_error():
    payload = roster_inputs(shared=True).model_dump(round_trip=True)
    payload["population_evidence"] = [{"group_ids": ["GROUP-1", "GROUP-2"], "relationship": "DISJOINT", "evidence_reference": "synthetic:partition"}]
    with pytest.raises(PreparationError) as error:
        prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert error.value.issues[0].code == "conflicting_population_evidence"
    assert "PRIVATE-SYNTH-STUDENT" not in str(error.value)


def test_unknown_assertion_does_not_negate_complete_positive_evidence():
    payload = roster_inputs().model_dump(round_trip=True)
    payload["population_evidence"] = [{"group_ids": ["GROUP-1", "GROUP-2"], "relationship": "UNKNOWN"}]
    assert len(prepare_group_combinations(GroupCombinationInput.model_validate(payload)).candidates) == 1


def test_common_staff_must_be_eligible_for_every_offering():
    inputs = make_inputs(3, separate_offerings=True, staff_pools={
        "OFFER-1": ["STAFF-A", "STAFF-B"], "OFFER-2": ["STAFF-B", "STAFF-C"], "OFFER-3": ["STAFF-A", "STAFF-C"],
    })
    result = prepare_group_combinations(shared_content(inputs))
    assert len(result.candidates) == 3 and all(len(candidate.group_ids) == 2 for candidate in result.candidates)
    assert "no_common_staff" in diagnostics(result)
    assert {candidate.requirement_ids: candidate.eligible_staff_ids for candidate in result.candidates} == {
        ("REQ1", "REQ2"): ("STAFF-B",), ("REQ1", "REQ3"): ("STAFF-A",), ("REQ2", "REQ3"): ("STAFF-C",),
    }


@pytest.mark.parametrize("missing_pool", [False, True])
def test_missing_common_or_individual_staff_eligibility_keeps_separate_requirements(missing_pool):
    pools = {"OFFER-1": ["STAFF-A"], "OFFER-2": [] if missing_pool else ["STAFF-B"]}
    result = prepare_group_combinations(shared_content(make_inputs(separate_offerings=True, staff_pools=pools)))
    assert not result.candidates and len(result.prepared.input_snapshot.session_requirements) == 2
    assert ("missing_staff_eligibility" if missing_pool else "no_common_staff") in diagnostics(result)


def test_explicit_instructor_team_membership_can_be_common_eligibility():
    payload = make_inputs().model_dump(round_trip=True)
    payload["teaching_input"]["staff_members"][0]["staff_type"] = "INSTRUCTOR"
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert result.candidates[0].eligible_staff_ids == ("STAFF",)


def test_lic_and_colic_roles_remain_unchanged_across_shared_content_requirements():
    payload = shared_content(make_inputs(separate_offerings=True)).model_dump(round_trip=True)
    payload["teaching_input"]["teaching_team_members"][0]["role"] = "LIC"
    payload["teaching_input"]["teaching_team_members"][1]["role"] = "CO_LIC"
    inputs = GroupCombinationInput.model_validate(payload)
    original = inputs.teaching_input.model_dump(round_trip=True)
    result = prepare_group_combinations(inputs)
    assert result.candidates[0].eligible_staff_ids == ("STAFF",)
    assert result.prepared.input_snapshot.model_dump(round_trip=True) == original


def test_same_group_cannot_appear_twice_in_combined_demand():
    payload = make_inputs().model_dump(round_trip=True)
    payload["teaching_input"]["session_requirements"][1]["group_id"] = "GROUP-1"
    result = prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert not result.candidates and "same_group" in diagnostics(result)


@pytest.mark.parametrize("problem", ["unknown_specialization_group", "unknown_permission", "duplicate_permission", "unknown_evidence_group",
    "duplicate_evidence_group", "missing_evidence_reference", "ambiguous_overlap", "unknown_membership_group", "duplicate_membership",
    "roster_count", "duplicate_student", "partial_too_large", "nonbool_completeness", "unknown_relationship"])
def test_invalid_contract_evidence_and_references_are_rejected(problem):
    payload = roster_inputs().model_dump(round_trip=True)
    if problem == "unknown_specialization_group":
        payload["group_specializations"]["UNKNOWN"] = "Synthetic"
    elif problem in ("unknown_permission", "duplicate_permission"):
        payload["requirement_permissions"] = [{"requirement_id": "UNKNOWN" if problem == "unknown_permission" else "REQ1"}]
        if problem == "duplicate_permission":
            payload["requirement_permissions"] *= 2
    elif problem in ("unknown_evidence_group", "duplicate_evidence_group", "missing_evidence_reference", "ambiguous_overlap", "unknown_relationship"):
        groups = ["GROUP-1", "UNKNOWN"] if problem == "unknown_evidence_group" else ["GROUP-1", "GROUP-1"] if problem == "duplicate_evidence_group" else ["GROUP-1", "GROUP-2"]
        payload["population_evidence"] = [{"group_ids": groups, "relationship": "INVALID" if problem == "unknown_relationship" else "DISJOINT", "evidence_reference": "synthetic:proof"}]
        if problem == "missing_evidence_reference":
            del payload["population_evidence"][0]["evidence_reference"]
        elif problem == "ambiguous_overlap":
            payload["population_evidence"][0].update(relationship="OVERLAPPING", group_ids=["GROUP-1", "GROUP-2", "UNKNOWN"])
    elif problem == "unknown_membership_group":
        payload["memberships"][0]["group_id"] = "UNKNOWN"
    elif problem == "duplicate_membership":
        payload["memberships"].append(payload["memberships"][0])
    elif problem == "roster_count":
        payload["memberships"][0]["student_ids"] = ["SYNTHETIC-A"]
    elif problem == "duplicate_student":
        payload["memberships"][0]["student_ids"] = ["SYNTHETIC-A", "SYNTHETIC-A"]
    elif problem == "partial_too_large":
        payload["memberships"][0].update(complete=False, student_ids=["A", "B", "C"])
    else:
        payload["memberships"][0]["complete"] = 1
    with pytest.raises(ValidationError):
        GroupCombinationInput.model_validate(payload)


@pytest.mark.parametrize("field,value", [("allow_combination", 1), ("max_groups", 1), ("max_groups", True),
    ("max_combined_students", 0), ("max_combined_students", True)])
def test_invalid_policy_values_are_rejected(field, value):
    payload = make_inputs().model_dump(round_trip=True)
    payload["policy"][field] = value
    with pytest.raises(ValidationError):
        GroupCombinationInput.model_validate(payload)


def test_mutated_input_contracts_cannot_bypass_validation():
    inputs = make_inputs()
    inputs.population_evidence[0].group_ids = ("GROUP-1", "ABSENT")
    with pytest.raises(PreparationError, match="unknown group_id"):
        prepare_group_combinations(inputs)
    with pytest.raises(PreparationError, match="GroupCombinationInput"):
        prepare_group_combinations(inputs.teaching_input)


@pytest.mark.parametrize("limit", [0, -1, True, 1.0])
def test_invalid_operational_limits_are_rejected(limit):
    with pytest.raises(PreparationError, match="positive strict integer"):
        prepare_group_combinations(make_inputs(), max_candidates=limit)


def test_generation_limit_errors_instead_of_returning_partial_alternatives():
    inputs = make_inputs(3)
    original = inputs.model_dump(round_trip=True)
    with pytest.raises(PreparationError) as error:
        prepare_group_combinations(inputs, max_candidates=1)
    assert error.value.issues[0].code == "candidate_limit_exceeded"
    assert inputs.model_dump(round_trip=True) == original
    assert len(prepare_group_combinations(inputs, max_candidates=4).candidates) == 4


def test_identifiers_are_stable_under_reordered_source_records():
    inputs = make_inputs(3)
    payload = inputs.model_dump(round_trip=True)
    for field in ("staff_members", "module_offerings", "teaching_team_members", "student_groups", "session_requirements"):
        payload["teaching_input"][field].reverse()
    payload["population_evidence"][0]["group_ids"] = tuple(reversed(payload["population_evidence"][0]["group_ids"]))
    first, second = prepare_group_combinations(inputs), prepare_group_combinations(GroupCombinationInput.model_validate(payload))
    assert first.candidates == second.candidates
    assert len({occurrence.occurrence_id for candidate in first.candidates for occurrence in candidate.occurrences}) == 8


def test_full_combined_and_full_separate_selections_cover_original_demand_once():
    result = prepare_group_combinations(make_inputs())
    combined = validate_delivery_selection(result, DeliverySelection(
        combined_occurrence_ids=[row.occurrence_id for row in result.candidates[0].occurrences],
    ))
    separate = validate_delivery_selection(result, DeliverySelection(
        separate_occurrence_ids=[row.occurrence_id for row in result.separate_sessions],
    ))
    assert combined.original_occurrences_covered == separate.original_occurrences_covered == 4
    assert combined.delivery_count == combined.combined_deliveries == 2 and combined.delivery_contact_minutes == 240
    assert separate.delivery_count == separate.separate_deliveries == 4 and separate.delivery_contact_minutes == 480
    assert combined.to_dict()["scheduled"] is False


def test_mixed_weekly_combined_and_separate_delivery_is_a_valid_alternative():
    result = prepare_group_combinations(make_inputs())
    summary = validate_delivery_selection(result, DeliverySelection(
        combined_occurrence_ids=[result.candidates[0].occurrences[0].occurrence_id],
        separate_occurrence_ids=[row.occurrence_id for row in result.separate_sessions if row.occurrence_number == 2],
    ))
    assert summary.original_occurrences_covered == 4 and summary.delivery_count == 3
    assert summary.delivery_contact_minutes == 360


def test_three_group_shared_delivery_fulfills_six_original_occurrences_with_two_bookings():
    result = prepare_group_combinations(make_inputs(3))
    largest = next(candidate for candidate in result.candidates if len(candidate.group_ids) == 3)
    summary = validate_delivery_selection(result, DeliverySelection(
        combined_occurrence_ids=[row.occurrence_id for row in largest.occurrences],
    ))
    assert largest.combined_student_count == 360
    assert summary.original_occurrences_covered == 6 and summary.delivery_count == 2
    assert summary.delivery_contact_minutes == 240


@pytest.mark.parametrize("problem,code", [("missing", "missing_fulfillment"), ("duplicate_separate", "duplicate_fulfillment"),
    ("duplicate_combined", "duplicate_fulfillment"), ("both_modes", "duplicate_fulfillment"),
    ("unknown_separate", "unknown_separate_occurrence"), ("unknown_combined", "unknown_combined_occurrence")])
def test_invalid_coverage_never_returns_a_valid_projection(problem, code):
    result = prepare_group_combinations(make_inputs())
    separate = [row.occurrence_id for row in result.separate_sessions]
    combined = [row.occurrence_id for row in result.candidates[0].occurrences]
    selection = {"separate_occurrence_ids": separate}
    if problem == "missing":
        selection["separate_occurrence_ids"] = separate[:-1]
    elif problem == "duplicate_separate":
        selection["separate_occurrence_ids"] = separate + separate[:1]
    elif problem == "duplicate_combined":
        selection = {"combined_occurrence_ids": combined + combined[:1]}
    elif problem == "both_modes":
        selection["combined_occurrence_ids"] = combined
    elif problem == "unknown_separate":
        selection["separate_occurrence_ids"] = separate + ["UNKNOWN"]
    else:
        selection["combined_occurrence_ids"] = ["UNKNOWN"]
    with pytest.raises(PreparationError) as error:
        validate_delivery_selection(result, DeliverySelection.model_validate(selection))
    assert code in {issue.code for issue in error.value.issues}


def test_overlapping_group_combinations_cannot_duplicate_fulfillment():
    result = prepare_group_combinations(make_inputs(3))
    chosen = [candidate for candidate in result.candidates if len(candidate.group_ids) == 2]
    with pytest.raises(PreparationError) as error:
        validate_delivery_selection(result, DeliverySelection(
            combined_occurrence_ids=[row.occurrence_id for candidate in chosen for row in candidate.occurrences],
        ))
    assert {issue.code for issue in error.value.issues} == {"duplicate_fulfillment"}


def test_stale_or_corrupted_preparation_cannot_change_combined_counts():
    result = prepare_group_combinations(make_inputs())
    selection = DeliverySelection(combined_occurrence_ids=[row.occurrence_id for row in result.candidates[0].occurrences])
    forged = replace(result, candidates=(replace(result.candidates[0], combined_student_count=239),))
    with pytest.raises(PreparationError, match="no longer match"):
        validate_delivery_selection(forged, selection)
    result.input_snapshot.teaching_input.student_groups[0].student_count = 121
    with pytest.raises(PreparationError, match="no longer match"):
        validate_delivery_selection(result, selection)


def test_empty_and_single_group_inputs_keep_original_delivery_only():
    for count in (0, 1):
        result = prepare_group_combinations(make_inputs(count))
        assert not result.candidates and len(result.separate_sessions) == 2 * count
        summary = validate_delivery_selection(result, DeliverySelection(separate_occurrence_ids=[row.occurrence_id for row in result.separate_sessions]))
        assert summary.delivery_count == 2 * count


def test_checked_in_example_matches_120_plus_120_optional_delivery():
    path = Path(__file__).resolve().parents[1] / "examples" / "group-combination-input.example.json"
    inputs = GroupCombinationInput.model_validate_json(path.read_text(encoding="utf-8"))
    result = prepare_group_combinations(inputs)
    assert len(result.candidates) == 1 and result.candidates[0].combined_student_count == 240
    assert result.candidates[0].eligible_staff_ids == ("SYNTHETIC-STAFF",)
    assert result.candidates[0].academic_year == 1 and result.candidates[0].semester == 1
