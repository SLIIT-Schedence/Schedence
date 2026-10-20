"""Isolated snapshot discovery/preflight with synthetic rows only. No SQL connection, job or CP-SAT."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from schedence_teaching.api.errors import ApiError
from schedence_teaching.api.runtime import validate_prepared_mode
from schedence_teaching.generation.inputs import prepare_database_work
from schedence_teaching.generation.prepared_inputs import IsolatedPreparedInputs, PreparedInputManifest
from schedence_teaching.generation.readiness import ReadinessScope
from schedence_teaching.generation.scheduling_rules import EXPERIMENTAL_DEFAULTS, SchedulingRules
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.snapshots import content_digest
from sqlalchemy.engine import make_url

from readiness_fixtures import api, forbid_solver
from test_database_generation import database_fixture
from test_elective_enrollment import elective_fixture
from test_generation_readiness import rows
from test_persistence_contracts import TENANT, OTHER_TENANT
from test_product_generation import generation_request


class MemoryRepository:
    def __init__(self, documents, children):
        self.documents, self.children, self.calls = documents, children, []
        self.engine = SimpleNamespace(url=make_url("postgresql+psycopg://127.0.0.1:5432/schedence_step12b4_test"))

    @asynccontextmanager
    async def transaction(self, tenant, *, read=False):
        assert read, "prepared inputs must never open a write transaction"
        self.calls.append(tenant)

        async def get_snapshot(identifier):
            return self.documents[identifier].model_copy(deep=True)

        async def execute(statement, params):
            assert params["tenant"] == TENANT
            name = str(statement).split("teaching.")[1].split()[0]
            return SimpleNamespace(mappings=lambda: self.children[name])

        yield SimpleNamespace(get_snapshot=get_snapshot, _session=SimpleNamespace(execute=execute))


@pytest.fixture
def isolated(database_fixture, monkeypatch):
    evidence, _ = database_fixture
    request = generation_request(evidence, scheduling_rules=EXPERIMENTAL_DEFAULTS)
    work = prepare_database_work(evidence, request)
    validated_id = uuid4()
    manifest = PreparedInputManifest(institution_code="SYNTHETIC-CAMPUS", revision=1, request=request,
        source_digest=content_digest(evidence.document), validated_snapshot_id=validated_id, validated_digest=content_digest(work.document))
    repository = MemoryRepository({evidence.snapshot_id: evidence.document, validated_id: work.document}, evidence.teaching)
    reads = []

    async def owner_rows(engines, institution_id, target):
        reads.append(institution_id)
        return rows(evidence)

    monkeypatch.setattr("schedence_teaching.generation.prepared_inputs.load_owner_rows", owner_rows)
    provider = IsolatedPreparedInputs(repository, {"academic": object(), "resource": object()}, object(), manifest)
    return SimpleNamespace(provider=provider, manifest=manifest, evidence=evidence, repository=repository, reads=reads, work=work)


def test_stored_source_and_rule_bound_version_rebuild_exactly(isolated):
    work = asyncio.run(isolated.provider.prepare(isolated.manifest.request))
    assert content_digest(work.document) == isolated.manifest.validated_digest
    assert work.document.contexts.fairness_config is not None
    assert isolated.repository.calls == isolated.reads == [TENANT]


def test_readiness_discovers_input_version_without_timetable_history(isolated):
    result = asyncio.run(isolated.provider.assess(TENANT, ReadinessScope(1, frozenset({1}), frozenset({"WEEKDAY"})), EXPERIMENTAL_DEFAULTS))
    assert result["ready"], result["checks"]
    stored = result["stored_input"]
    assert stored["snapshot_id"] == isolated.manifest.request.snapshot_id
    assert stored["validated_snapshot_id"] == isolated.manifest.validated_snapshot_id
    assert stored["revision"] == 1 and stored["persistence"] == "ISOLATED_POSTGRESQL"
    assert stored["academic_years"] == [1] and stored["streams"] == ["WEEKDAY"]


def test_other_tenants_and_scopes_cannot_discover_or_prepare_the_version(isolated):
    result = asyncio.run(isolated.provider.assess(OTHER_TENANT, ReadinessScope(1), EXPERIMENTAL_DEFAULTS))
    assert not result["ready"] and "stored_input" not in result
    request = isolated.manifest.request.model_copy(update={"academic_years": frozenset({4})})
    with pytest.raises(ApiError) as error:
        asyncio.run(isolated.provider.prepare(request))
    assert error.value.code == "PREPARED_SCOPE_MISMATCH"
    assert not isolated.repository.calls and not isolated.reads


def test_prepared_input_requires_explicit_rules_before_owner_reads(isolated):
    request = isolated.manifest.request.model_copy(update={"scheduling_rules": None})
    with pytest.raises(ApiError) as error:
        asyncio.run(isolated.provider.prepare(request))
    assert error.value.code == "SCHEDULING_RULES_REQUIRED"
    assert not isolated.repository.calls and not isolated.reads


def test_remote_snapshot_repository_is_rejected_before_any_connection(isolated):
    isolated.repository.engine.url = make_url("postgresql+psycopg://example.neon.tech:5432/production")
    with pytest.raises(ValueError, match="isolated loopback"):
        IsolatedPreparedInputs(isolated.repository, {"academic": object(), "resource": object()}, object(), isolated.manifest)
    assert not isolated.repository.calls and not isolated.reads


@pytest.mark.parametrize("target", ["source", "validated", "children"])
def test_mutated_stored_documents_or_normalized_metadata_fail_closed(isolated, target):
    if target == "children":
        isolated.repository.children["snapshot_sources"] = ()
    else:
        identifier = isolated.manifest.request.snapshot_id if target == "source" else isolated.manifest.validated_snapshot_id
        isolated.repository.documents[identifier].academic_term_reference = "CHANGED"
    with pytest.raises(PersistenceConflict):
        asyncio.run(isolated.provider.prepare(isolated.manifest.request))


def test_modified_owner_rows_are_revalidated_instead_of_using_a_cached_success(isolated):
    isolated.evidence.academic["staff"][0]["full_name"] = "Changed owner record"
    with pytest.raises(PersistenceConflict, match="staff master differs"):
        asyncio.run(isolated.provider.prepare(isolated.manifest.request))


def test_other_supported_rules_can_be_checked_without_claiming_the_saved_derivation(isolated):
    result = asyncio.run(isolated.provider.assess(TENANT, ReadinessScope(1, frozenset({1}), frozenset({"WEEKDAY"})), SchedulingRules()))
    assert result["ready"] and result["stored_input"]["validated_snapshot_id"] is None
    changed = isolated.manifest.request.model_copy(update={"scheduling_rules": SchedulingRules()})
    work = asyncio.run(isolated.provider.prepare(changed))
    assert work.document.contexts.fairness_config is None
    assert content_digest(work.document) != isolated.manifest.validated_digest


def test_api_reports_a_stored_isolated_input_without_fabricating_a_timetable(api, isolated):
    api.runtime.readiness = isolated.provider
    response = api.client.post("/api/v1/teaching/generation-inputs?semester=1&academic_year=1&stream=WEEKDAY",
        headers=api.headers, json=EXPERIMENTAL_DEFAULTS.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] and body["snapshot_stored"]
    assert body["stored_input"]["snapshot_id"] == str(isolated.manifest.request.snapshot_id)
    assert body["generation_enabled"] is False  # A stored input alone does not create a worker.
    assert "timetable_version_id" not in body


@pytest.mark.parametrize("field,value", [("app_env", "production"), ("app_env", "staging"),
    ("teaching_generation_mode", "DEVELOPMENT"), ("teaching_read_store", "LOCAL_PREVIEW"),
    ("teaching_database_env_file", None), ("teaching_generation_readiness", "DISABLED")])
def test_prepared_runtime_requires_explicit_development_isolation(field, value):
    values = dict(app_env="development", teaching_generation_prepared_manifest="fixture.json",
        teaching_generation_mode="DURABLE_ISOLATED", teaching_read_store="DATABASE", teaching_database_env_file="restricted.env",
        teaching_generation_readiness="DEVELOPMENT")
    validate_prepared_mode(SimpleNamespace(**values))
    values[field] = value
    with pytest.raises(ValueError, match="development-only isolated"):
        validate_prepared_mode(SimpleNamespace(**values))
