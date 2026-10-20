"""Rules binding and API validation only: synthetic owner rows, no database or CP-SAT execution."""

from dataclasses import replace
from itertools import product

import pytest

from schedence_teaching.api.errors import ApiError
from schedence_teaching.generation.contracts import GenerationRequest
from schedence_teaching.generation.inputs import prepare_database_work
from schedence_teaching.generation.readiness import ReadinessScope, assess
from schedence_teaching.generation.scheduling_rules import (
    CALENDAR_SOURCE, RULES_SOURCE, EXPERIMENTAL_DEFAULTS, ConsecutiveRules, RulesError, SchedulingRules,
    apply_rules, check_objective_order, connected_rules, protected_rules, rules_reference,
)
from schedence_teaching.persistence.generation_inputs import academic_source, evidence_source
from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
from schedence_teaching.persistence.snapshots import canonical_json, content_digest, replay_snapshot

from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture
from test_generation_readiness import rows, unbound
from test_lecturer_preference_settings import preference_row
from test_persistence_contracts import TENANT, OTHER_TENANT
from test_product_generation import enrolled_batch, generation_request, product_configuration, product_evidence
from readiness_fixtures import api, forbid_solver
from test_teaching_read_api import grant


def academic(evidence):
    metadata = evidence_source(evidence.document, "PP1:preparation-source-bindings")["metadata"]
    return academic_source(evidence, metadata)


@pytest.mark.parametrize("selection", list(product((False, True), repeat=5)))
def test_every_rule_switch_combination_binds_to_existing_contracts(database_fixture, selection):
    evidence, _ = database_fixture
    preferences, fairness, student, staff, consecutive = selection
    rules = SchedulingRules(lecturer_preferences=EXPERIMENTAL_DEFAULTS.lecturer_preferences if preferences else None,
        workload_fairness=fairness, balance_student_daily_load=student, balance_staff_daily_load=staff,
        consecutive=EXPERIMENTAL_DEFAULTS.consecutive if consecutive else None)
    original = content_digest(evidence.document)
    bound = apply_rules(evidence.document, rules, academic(evidence))
    assert content_digest(evidence.document) == original
    assert bound.teaching_input == evidence.document.teaching_input
    assert bound.contexts.staff_input == evidence.document.contexts.staff_input
    assert bound.contexts.venue_input == evidence.document.contexts.venue_input
    assert protected_rules(bound) == protected_rules(evidence.document)
    assert bound.external_references == evidence.document.external_references
    assert bound.calendar == evidence.document.calendar
    assert bound.solver_config.delivery_calendar == evidence.document.solver_config.delivery_calendar
    assert bound.solver_config.attendance_memberships == evidence.document.solver_config.attendance_memberships
    connected = connected_rules(bound)
    assert connected["workload_fairness"] == fairness
    assert connected["consecutive"] == consecutive
    assert connected["day_part_settings"] == preferences
    assert connected["daily_load"] == (["MAX_STUDENT_DAILY_MINUTES"] if student else []) + (["MAX_STAFF_DAILY_MINUTES"] if staff else [])
    order = check_objective_order(bound)
    assert ("CONSECUTIVE_PENALTY" in order) == consecutive
    assert ("AGGREGATE_TARGET_DEVIATION" in order) == fairness
    assert evidence_source(bound, RULES_SOURCE)["university_approved"] is False
    configuration = evidence_source(bound, CALENDAR_SOURCE)["configuration"]
    assert canonical_json(configuration["solver_config"]) == canonical_json(bound.solver_config)
    assert content_digest(apply_rules(bound, rules, academic(evidence))) == content_digest(bound)


def test_disabling_rules_removes_previous_objectives_but_preserves_hard_blocks(database_fixture):
    evidence, _ = database_fixture
    source = academic(evidence)
    source.source_preferences = [preference_row("SYN-LEC-1", {"Monday_Morning": "Not Available", "Tuesday_Morning": "Preferred"})]
    source.source_preferences[0].source_values["Staff_Name"] = source.staff_members[0].staff_name
    enabled = apply_rules(evidence.document, EXPERIMENTAL_DEFAULTS, source)
    assert enabled.contexts.preference_input is not None
    blocks = enabled.contexts.staff_input.availability
    assert any(row.blocked_periods for row in blocks)
    disabled = apply_rules(enabled, SchedulingRules(), source)
    assert disabled.contexts.staff_input.availability == blocks
    assert all(getattr(disabled.contexts, name) is None for name in (
        "preference_input", "preference_config", "fairness_policy", "fairness_config", "consecutive_policy", "consecutive_config"))
    assert disabled.solver_config.daily_load_distribution is None
    assert disabled.solver_config.objective_stage_plan is None
    assert not connected_rules(disabled)["lecturer_preferences"]
    assert "lecturer_preference_settings" not in evidence_source(disabled, CALENDAR_SOURCE)["configuration"]


def test_unavailable_rows_require_known_day_part_boundaries(database_fixture):
    evidence, _ = database_fixture
    source = academic(evidence)
    source.source_preferences = [preference_row("SYN-LEC-1", {"Monday_Morning": "Not Available"})]
    with pytest.raises(RulesError, match="explicit day-part boundaries"):
        apply_rules(evidence.document, SchedulingRules(), source)


@pytest.mark.parametrize("maximum,idle", [(240, False), (None, True), (180, True)])
def test_continuous_and_idle_costs_use_existing_consecutive_contract(database_fixture, maximum, idle):
    evidence, _ = database_fixture
    rules = SchedulingRules(consecutive=ConsecutiveRules(maximum_preferred_continuous_minutes=maximum,
        minimize_idle_gaps=idle, apply_to_lecturers=True, apply_to_student_groups=True, penalty_weight=3))
    bound = apply_rules(evidence.document, rules, academic(evidence))
    policy, options = bound.contexts.consecutive_policy, bound.contexts.consecutive_config
    assert policy.maximum_preferred_continuous_minutes == maximum
    assert policy.idle_gap_minute_penalty == (1 if idle else None)
    assert bool(policy.soft_criteria) == (maximum is not None)
    assert options.lecturer_penalty_weight == options.student_group_penalty_weight == 3
    assert options.formulation.value == "TIME_INDEXED"
    replay_snapshot(bound)


@pytest.mark.parametrize("raw", [
    {"workload_fairness": "false"}, {"balance_staff_daily_load": 1}, {"made_up_rule": True},
    {"consecutive": {}}, {"consecutive": {"minimize_idle_gaps": True, "apply_to_lecturers": False}},
    {"consecutive": {"maximum_preferred_continuous_minutes": 61}},
    {"consecutive": {"minimize_idle_gaps": True, "penalty_weight": 0}},
    {"lecturer_preferences": EXPERIMENTAL_DEFAULTS.lecturer_preferences.model_dump() | {"morning_start_minute": 511}},
    {"lecturer_preferences": EXPERIMENTAL_DEFAULTS.lecturer_preferences.model_dump() | {"preferred_free_day_weight": 11}},
])
def test_invalid_or_unsupported_fields_fail_closed(raw):
    with pytest.raises(ValueError):
        SchedulingRules.model_validate(raw)


def test_rules_are_bound_after_owner_validation_and_rebuild_deterministically(database_fixture):
    evidence, _ = database_fixture
    request = generation_request(evidence, scheduling_rules=EXPERIMENTAL_DEFAULTS)
    before = content_digest(evidence.document)
    work = prepare_database_work(evidence, request)
    rebuilt = prepare_database_work(evidence, GenerationRequest.model_validate_json(request.model_dump_json()))
    assert content_digest(work.document) == content_digest(rebuilt.document)
    assert content_digest(evidence.document) == before
    assert work.document.contexts.fairness_config is not None
    assert {row.academic_year for row in work.document.scopes} == {1}
    assert evidence_source(work.document, RULES_SOURCE)["reference"] == rules_reference(EXPERIMENTAL_DEFAULTS)
    selection = evidence_source(work.document, "API:parent-snapshot-selection")
    assert selection["calendar_reference"] == request.calendar_reference
    assert selection["request_sha256"] == content_digest(request)
    other = prepare_database_work(evidence, generation_request(evidence, scheduling_rules=SchedulingRules()))
    assert content_digest(other.document) != content_digest(work.document)
    owner = rows(evidence)
    owner["academic"]["staff"][0]["full_name"] = "Changed owner name"
    with pytest.raises(ValueError, match="staff master differs"):
        prepare_database_work(replace(evidence, academic=owner["academic"]), request)


def test_sealed_policies_cannot_be_overridden():
    evidence = product_evidence(combined=False)
    with pytest.raises(ApiError) as error:
        prepare_database_work(evidence, generation_request(evidence, scheduling_rules=EXPERIMENTAL_DEFAULTS))
    assert error.value.status == 400 and error.value.code == "INVALID_SCHEDULING_RULES"
    assert "sealed" in error.value.message


def test_coordinated_strategy_rejects_rules_before_accepting_work(database_fixture):
    with pytest.raises(ValueError, match="monolithic"):
        generation_request(database_fixture[0], strategy="COORDINATED_COHORTS", scheduling_rules=SchedulingRules())


def test_preexisting_compactness_is_preserved_and_checked_without_a_solve(database_fixture):
    evidence, _ = database_fixture
    document = evidence.document.model_copy(deep=True)
    from schedence_teaching.solver.compactness_inputs import TeachingDayCompactnessConfig
    document.solver_config.teaching_day_compactness = TeachingDayCompactnessConfig(policy_reference="SYNTHETIC:thin",
        preferred_minimum_daily_teaching_minutes=120, minimum_reference="SYNTHETIC:test-only",
        placement="BEFORE_AGGREGATE_TARGET_DEVIATION")
    source = academic(evidence)
    bound = apply_rules(document, EXPERIMENTAL_DEFAULTS, source)
    assert bound.solver_config.teaching_day_compactness == document.solver_config.teaching_day_compactness
    assert "THIN_TEACHING_DAYS" in check_objective_order(bound)
    with pytest.raises(RulesError, match="requires teaching-target fairness"):
        apply_rules(document, SchedulingRules(), source)


class LocalReadiness:
    def __init__(self, evidence):
        self.evidence, self.calls = evidence, []

    async def assess(self, institution_id, scope, rules=None):
        self.calls.append((institution_id, scope, rules))
        return assess(unbound(self.evidence), rows(self.evidence), institution_id, scope, rules)


@pytest.mark.parametrize("prefix", ["/api/v1/teaching", "/api/teaching"])
def test_rules_readiness_reports_actual_bindings_and_never_stores(api, database_fixture, prefix):
    api.runtime.readiness = local = LocalReadiness(database_fixture[0])
    response = api.client.post(prefix + "/generation-inputs", headers=api.headers,
        params=[("semester", 1), ("academic_year", 1), ("stream", "WEEKDAY")],
        json=EXPERIMENTAL_DEFAULTS.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] and body["snapshot_stored"] is False and body["university_approved"] is False
    assert body["generation_enabled"] is False
    report = body["scheduling_rules"]
    assert report["selected"] == EXPERIMENTAL_DEFAULTS.model_dump(mode="json")
    assert report["reference"] == rules_reference(EXPERIMENTAL_DEFAULTS)
    assert report["connected"]["workload_fairness"] and report["connected"]["consecutive"]
    assert report["protected"]["not_available_handling"] == "HARD_UNAVAILABILITY"
    assert report["protected"]["lunch_enforced"]
    assert report["objective_order"][:2] == ["DELIVERIES", "CONTACT_MINUTES"]
    assert local.calls == [(TENANT, ReadinessScope(1, frozenset({1}), frozenset({"WEEKDAY"})), EXPERIMENTAL_DEFAULTS)]


def test_rules_readiness_retains_auth_scope_and_body_validation(api, database_fixture):
    api.runtime.readiness = local = LocalReadiness(database_fixture[0])
    url = "/api/v1/teaching/generation-inputs?semester=1"
    assert api.client.post(url, json={}).status_code == 401
    assert api.client.post(url + "&institution_id=" + str(OTHER_TENANT), headers=api.headers, json={}).status_code == 403
    assert api.client.post(url, headers=api.headers, json={"workload_fairness": "yes"}).status_code == 422
    grant(api, "ACADEMIC_ADMIN", ["teaching:timetables:read"])
    assert api.client.post(url, headers=api.headers, json={}).status_code == 403
    assert not local.calls


def test_get_readiness_reports_snapshot_rules_without_applying_defaults(database_fixture):
    evidence, _ = database_fixture
    result = assess(unbound(evidence), rows(evidence), TENANT, ReadinessScope(1))
    assert result["ready"]
    assert result["scheduling_rules"]["selected"] is None
    assert not result["scheduling_rules"]["connected"]["workload_fairness"]
    assert not result["scheduling_rules"]["connected"]["consecutive"]


@pytest.mark.parametrize("fairness,consecutive,preferences", list(product((False, True), repeat=3)))
def test_real_preference_rows_project_into_each_combined_plan(fairness, consecutive, preferences):
    enrolled, source, policy, _ = enrolled_batch(product_configuration(combined=False))
    document = capture_pp1_snapshot(enrolled, institution_id=TENANT, sources=(), blocked_scopes=(),
        academic_term_reference="SYNTHETIC_RULES", timezone_name="Asia/Colombo", registration_policy=policy.model_dump(mode="json"))
    rules = SchedulingRules(lecturer_preferences=EXPERIMENTAL_DEFAULTS.lecturer_preferences if preferences else None,
        workload_fairness=fairness, balance_student_daily_load=True, balance_staff_daily_load=True,
        consecutive=EXPERIMENTAL_DEFAULTS.consecutive if consecutive else None)
    bound = apply_rules(document, rules, source)
    order = check_objective_order(bound)
    assert ("PREFERENCE_PENALTY" in order) == preferences
    assert ("CONSECUTIVE_PENALTY" in order) == consecutive
    assert ("AGGREGATE_TARGET_DEVIATION" in order) == fairness
    assert all(original.blocked_periods == changed.blocked_periods[:len(original.blocked_periods)]
        for original, changed in zip(document.contexts.staff_input.availability, bound.contexts.staff_input.availability))


def test_optional_rules_preserve_historical_request_digest(database_fixture):
    request = generation_request(database_fixture[0])
    assert "scheduling_rules" not in request.model_dump()
    assert "scheduling_rules" not in request.model_dump_json()
    restored = GenerationRequest.model_validate_json(request.model_dump_json())
    assert restored.scheduling_rules is None and content_digest(restored) == content_digest(request)


def test_legacy_consecutive_order_without_shared_plan_is_still_valid(database_fixture):
    evidence, _ = database_fixture
    bound = apply_rules(evidence.document, SchedulingRules(consecutive=EXPERIMENTAL_DEFAULTS.consecutive), academic(evidence))
    bound.solver_config.objective_stage_plan = None
    assert check_objective_order(bound) == ("DELIVERIES", "CONTACT_MINUTES", "CONSECUTIVE_PENALTY")


def test_rules_do_not_hide_invalid_calendar_evidence(database_fixture):
    evidence, _ = database_fixture
    changed = evidence.document.model_copy(deep=True)
    next(row for row in changed.sources if row.label == CALENDAR_SOURCE).sha256 = "0" * 64
    with pytest.raises(RulesError, match="checksum"):
        apply_rules(changed, EXPERIMENTAL_DEFAULTS, academic(evidence))


def test_semantically_invalid_rules_are_reported_as_missing_not_ready():
    evidence = product_evidence(combined=False)
    result = assess(unbound(evidence), rows(evidence), TENANT, ReadinessScope(1), EXPERIMENTAL_DEFAULTS)
    assert not result["ready"] and result["inputs"] is None
    assert result["checks"][-1]["key"] == "scheduling-rules"
    assert result["checks"][-1]["status"] == "MISSING"
    assert "sealed" in result["checks"][-1]["detail"]


def test_rules_validation_is_documented_and_secured_in_openapi(api):
    schema = api.client.get("/openapi.json").json()
    for prefix in ("/api/teaching", "/api/v1/teaching"):
        operation = schema["paths"][prefix + "/generation-inputs"]["post"]
        assert operation["security"] == [{"BearerToken": []}]
        assert operation["requestBody"]["required"]
    reference = schema["paths"]["/api/v1/teaching/generations"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    generation = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
    assert "scheduling_rules" in generation["properties"]
