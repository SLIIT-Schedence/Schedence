"""Safe import preparation and transaction behavior without live database writes."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from schedence_teaching.persistence import application
from schedence_teaching.persistence.application import (
    import_validated_snapshots, load_generation_inputs, verify_runtime_security, verify_source_checksums)
from schedence_teaching.persistence.contracts import SourceDocument
from schedence_teaching.persistence.models import Base, InputSnapshot
from schedence_teaching.persistence.ports import PersistenceConflict
from schedence_teaching.persistence.pp1 import capture_pp1_snapshot
from schedence_teaching.persistence.repositories import SqlTeachingRepository
from schedence_teaching.persistence.snapshots import content_digest, snapshot_payload

from test_persistence_contracts import TENANT, OTHER_TENANT, example
from test_persistence_repositories import FakeSession, stored
from test_elective_enrollment import elective_fixture
from test_semester_scenarios import snapshot


def test_reimport_returns_existing_identity_without_mutating_history():
    document, _ = example()
    existing = stored(document)
    session = FakeSession()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [existing]))
    repository = SqlTeachingRepository(session, TENANT)
    assert asyncio.run(repository.store_snapshot(document)) == existing.id
    assert not session.added
    session.flush.assert_not_awaited()
    lock = session.execute.await_args.args[0]
    assert "pg_advisory_xact_lock" in str(lock)
    query = session.scalars.await_args.args[0]
    assert "institution_id" in str(query) and TENANT in query.compile().params.values()


def test_changed_validated_demand_gets_a_new_snapshot_without_replacing_old_one():
    document, _ = example()
    existing = stored(document)
    document.teaching_input.session_requirements[0].duration_minutes = 30
    session = FakeSession()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [existing]))
    identifier = asyncio.run(SqlTeachingRepository(session, TENANT).store_snapshot(document))
    assert identifier != existing.id
    assert sum(isinstance(row, InputSnapshot) for row in session.added) == 1
    assert existing.expected_occurrences == 2


def test_corrupt_existing_import_is_not_silently_reused_or_overwritten():
    document, _ = example()
    existing = stored(document)
    existing.content_sha256 = "0"*64
    session = FakeSession()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [existing]))
    with pytest.raises(PersistenceConflict): asyncio.run(SqlTeachingRepository(session, TENANT).store_snapshot(document))
    assert not session.added


def test_semantic_reimport_survives_original_map_order_changes():
    document, _ = example()
    existing = stored(document)
    document.sources[0].provenance = {"second": 2, "first": 1}
    previous = document.model_copy(deep=True)
    previous.sources[0].provenance = {"first": 1, "second": 2}
    existing.payload = snapshot_payload(previous)
    existing.content_sha256 = content_digest(existing.payload)
    session = FakeSession()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [existing]))
    assert asyncio.run(SqlTeachingRepository(session, TENANT).store_snapshot(document)) == existing.id
    assert not session.added


@pytest.mark.parametrize("damage", ["changed", "missing", "traversal", "empty"])
def test_source_manifest_rejects_drift_missing_files_and_paths(tmp_path, damage):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"synthetic fixture bytes")
    from hashlib import sha256
    expected = {source.name: sha256(source.read_bytes()).hexdigest()}
    assert verify_source_checksums(tmp_path, expected) == expected
    if damage == "changed": source.write_bytes(b"changed")
    elif damage == "missing": source.rename(tmp_path / "other.xlsx")
    elif damage == "traversal": expected = {"../source.xlsx": "a"*64}
    elif damage == "empty": expected = {}
    with pytest.raises(ValueError): verify_source_checksums(tmp_path, expected)


def security_session(*, privileged=False, missing=False, weakened=None):
    rows = [dict(relname=table.name, relrowsecurity=True, relforcerowsecurity=True,
        can_select=True, can_insert=True, owns_history=False) for table in Base.metadata.tables.values()]
    if missing: rows.pop()
    if weakened:
        field, value = weakened
        rows[0][field] = value
    role = dict(rolsuper=False, rolbypassrls=privileged, can_assume_privileged_role=False)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: role)),
        SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))]))
    return session


@pytest.mark.parametrize("failure", [None, "privileged", "missing", "rls_disabled", "rls_not_forced", "no_select", "no_insert", "owner"])
def test_runtime_security_fails_closed(failure):
    fields = dict(rls_disabled=("relrowsecurity",False),rls_not_forced=("relforcerowsecurity",False),
                  no_select=("can_select",False),no_insert=("can_insert",False),owner=("owns_history",True))
    session = security_session(privileged=failure=="privileged", missing=failure=="missing", weakened=fields.get(failure))
    if failure:
        with pytest.raises(PersistenceConflict): asyncio.run(verify_runtime_security(session))
    else: asyncio.run(verify_runtime_security(session))


def test_pp1_capture_preserves_original_contracts_and_unresolved_research(elective_fixture):
    registered = snapshot(elective_fixture)
    # The shared fixture's intentionally minimal blocked row is not a real scope;
    # this test supplies a complete, synthetic DS block explicitly.
    blocked = (dict(cohort_id="SYN-DS", academic_year=4, semester=1, stream="WEEKEND",
                    specialization="DS", student_count=7),)
    source = SourceDocument(label="Synthetic source", sha256="a"*64, owner="ACADEMIC_DATA_SERVICE",
        provenance_kind="UNIVERSITY_SOURCED", provenance={"dataset_category":"UNIVERSITY_SOURCE", "synthetic_fixture":True})
    original = registered.enrolled.batch.prepared.input_snapshot.model_dump(round_trip=True)
    captured = capture_pp1_snapshot(registered.enrolled, institution_id=TENANT, sources=(source,),
        blocked_scopes=blocked, academic_term_reference="SYNTHETIC-PP1-TEST",timezone_name="Asia/Colombo",
        registration_policy={"reference":"synthetic fixture", "random_seed":7})
    assert captured.teaching_input.model_dump(round_trip=True) == original
    assert captured.solver_config.attendance_memberships == registered.enrolled.group_memberships
    assert captured.scopes[-1].student_count == 7 and captured.scopes[-1].scope_status == "BLOCKED_MISSING_ACADEMIC_MAPPING"
    assert {row.issue_code for row in captured.issues} == {"RESEARCH_PENDING_SESSION_MAPPING", "BLOCKED_MISSING_ACADEMIC_MAPPING"}
    evidence = next(row.provenance for row in captured.sources if row.provenance.get("dataset_category")=="PP1_SYNTHETIC_ELECTIVES")
    assert evidence["university_individual_registrations"] is False
    assert all(len(row["offering_ids"])==2 for row in evidence["selections"])


@pytest.mark.parametrize("failure", [None, "readback", "source_drift", "tenant", "owner"])
def test_import_is_atomic_checks_sources_and_compares_readback(tmp_path, monkeypatch, failure):
    document, _ = example()
    from hashlib import sha256
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"synthetic fixture")
    checksums = {source.name:sha256(source.read_bytes()).hexdigest()}
    document.sources = (SourceDocument(label=source.name, sha256=checksums[source.name], owner="TEACHING_SERVICE",
        provenance_kind="SYNTHETIC_PP1", provenance={}),)
    readback = document.model_copy(deep=True)
    if failure == "readback": readback.academic_term_reference = "CHANGED"
    if failure == "tenant": document.institution_id = OTHER_TENANT
    repository = SimpleNamespace(_session=security_session(privileged=failure=="owner"),
        store_snapshot=AsyncMock(return_value=uuid4()),get_snapshot=AsyncMock(return_value=readback))
    outcomes=[]

    @asynccontextmanager
    async def uow(*args):
        try:
            yield repository
            outcomes.append("COMMIT")
        except BaseException:
            outcomes.append("ROLLBACK")
            raise

    monkeypatch.setattr(application,"teaching_unit_of_work",uow)
    if failure == "source_drift":
        async def changed(*args):
            source.write_bytes(b"edited mid-import")
            return readback
        repository.get_snapshot.side_effect = changed
    async def execute():
        return await import_validated_snapshots(None,TENANT,(document,),source_directory=tmp_path,expected_checksums=checksums)
    if failure:
        with pytest.raises(ValueError): asyncio.run(execute())
        if failure=="tenant": assert outcomes==[]
        else: assert outcomes==["ROLLBACK"]
        if failure=="owner": repository.store_snapshot.assert_not_awaited()
    else:
        result=asyncio.run(execute())
        assert result.readback_equal and result.expected_occurrences==(2,) and outcomes==["COMMIT"]


def test_loaded_native_inputs_escape_the_database_transaction_before_solver_use(monkeypatch):
    document, _ = example()
    outcomes=[]
    repository=SimpleNamespace(_session=security_session(),get_snapshot=AsyncMock(return_value=document))
    @asynccontextmanager
    async def uow(*args):
        yield repository
        outcomes.append("CLOSED")
    monkeypatch.setattr(application,"teaching_unit_of_work",uow)
    restored,prepared,config,contexts=asyncio.run(load_generation_inputs(None,TENANT,uuid4()))
    assert outcomes==["CLOSED"] and restored==document and len(prepared.sessions)==2
    assert config==document.solver_config and contexts["venue_input"] is not None
