"""Repository query/transaction contracts, entirely with injected fake sessions."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from schedence_teaching.persistence.models import (
    Delivery, DeliveryCoverage, GenerationRun, InputSnapshot, SnapshotIssue,
    SnapshotScope, SnapshotSource, TimetableSeries, TimetableVersion, ValidationReport,
)
from schedence_teaching.persistence.repositories import PersistenceConflict, SqlTeachingRepository, teaching_unit_of_work
from schedence_teaching.persistence.snapshots import canonical_json, content_digest, snapshot_payload
from schedence_teaching.solver import SolverResult, SolverStatus

from test_persistence_contracts import TENANT, OTHER_TENANT, example


class FakeSession:
    def __init__(self, scalar_values=()):
        self.added = []
        self.queries = []
        self.scalar_values = iter(scalar_values)
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()
        self.execute = AsyncMock()

    def add(self, row):
        self.added.append(row)

    async def scalar(self, query):
        self.queries.append(query)
        return next(self.scalar_values)

    async def scalars(self, query):
        self.queries.append(query)
        return SimpleNamespace(all=lambda: [])


def stored(snapshot):
    payload = snapshot_payload(snapshot)
    return InputSnapshot(id=uuid4(), institution_id=TENANT, content_sha256=content_digest(payload),
        expected_occurrences=2, payload=payload)


def test_store_snapshot_preserves_detached_payload_and_normalized_provenance():
    snapshot, _ = example()
    session = FakeSession()
    identifier = asyncio.run(SqlTeachingRepository(session, TENANT).store_snapshot(snapshot))
    row = next(row for row in session.added if isinstance(row, InputSnapshot))
    assert row.id == identifier and row.expected_occurrences == 2
    assert row.content_sha256 == content_digest(row.payload)
    assert {type(row) for row in session.added} == {InputSnapshot, SnapshotSource, SnapshotScope, SnapshotIssue}
    assert all(row.institution_id == TENANT for row in session.added)
    original = row.payload["document_json"]
    snapshot.teaching_input.staff_members[0].staff_name = "Changed after snapshot"
    assert row.payload["document_json"] == original
    session.commit.assert_not_awaited()


def test_wrong_institution_snapshot_is_rejected_before_adding_rows():
    session = FakeSession()
    with pytest.raises(PersistenceConflict):
        asyncio.run(SqlTeachingRepository(session, OTHER_TENANT).store_snapshot(example()[0]))
    assert not session.added


@pytest.mark.parametrize("damage", ["missing", "tenant", "digest", "count", "payload"])
def test_read_snapshot_fails_closed_for_missing_or_corrupt_history(damage):
    snapshot, _ = example()
    row = stored(snapshot)
    if damage == "missing": row = None
    elif damage == "tenant":
        snapshot.institution_id = OTHER_TENANT
        row.payload = snapshot_payload(snapshot)
        row.content_sha256 = content_digest(row.payload)
    elif damage == "digest": row.content_sha256 = "f"*64
    elif damage == "count": row.expected_occurrences = 1
    elif damage == "payload": row.payload["document_json"] += " "
    session = FakeSession([row])
    with pytest.raises(PersistenceConflict):
        asyncio.run(SqlTeachingRepository(session, TENANT).get_snapshot(uuid4()))
    query = session.queries[0].compile(dialect=postgresql.dialect())
    assert "input_snapshots.institution_id =" in str(query) and TENANT in query.params.values()


@pytest.mark.parametrize("previous_number", [0, 1, 2])
def test_append_validated_version_retains_parent_chain_scope_and_booking_coverage(previous_number):
    snapshot, result = example()
    previous_id = uuid4() if previous_number else None
    latest = SimpleNamespace(id=previous_id, version_number=previous_number) if previous_number else None
    session = FakeSession([stored(snapshot), SimpleNamespace(id=uuid4()), latest])
    receipt = asyncio.run(SqlTeachingRepository(session, TENANT).append_attempt(uuid4(), uuid4(), result,
        expected_previous_version_id=previous_id))
    assert receipt.version_number == previous_number+1
    version = next(row for row in session.added if isinstance(row, TimetableVersion))
    assert version.previous_version_id == previous_id and not version.academic_complete
    assert version.lifecycle_status == "DRAFT_VALIDATED" and version.scope_completion == "COMPLETE_SUPPORTED_SCOPE"
    runs = [row for row in session.added if isinstance(row, GenerationRun)]
    reports = [row for row in session.added if isinstance(row, ValidationReport)]
    assert runs[0].scheduled_occurrences == reports[0].checked_occurrences == 2 and reports[0].passed
    assert len([row for row in session.added if isinstance(row, Delivery)]) == 2
    assert len([row for row in session.added if isinstance(row, DeliveryCoverage)]) == 2
    assert all(row.institution_id == TENANT for row in session.added)
    assert "FOR UPDATE" in str(session.queries[1])
    for query in session.queries:
        assert TENANT in query.compile(dialect=postgresql.dialect()).params.values()
    session.commit.assert_not_awaited()


def test_stale_parent_is_rejected_without_any_new_history():
    snapshot, result = example()
    session = FakeSession([stored(snapshot), SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4(), version_number=1)])
    with pytest.raises(PersistenceConflict, match="series changed"):
        asyncio.run(SqlTeachingRepository(session, TENANT).append_attempt(uuid4(), uuid4(), result,
            expected_previous_version_id=None))
    assert not session.added


def test_missing_series_is_rejected_without_any_new_history():
    snapshot, result = example()
    session = FakeSession([stored(snapshot), None])
    with pytest.raises(PersistenceConflict, match="series not found"):
        asyncio.run(SqlTeachingRepository(session, TENANT).append_attempt(uuid4(), uuid4(), result,
            expected_previous_version_id=None))
    assert not session.added


@pytest.mark.parametrize("status", [SolverStatus.UNKNOWN, SolverStatus.INFEASIBLE, SolverStatus.MODEL_INVALID])
def test_failed_run_creates_no_version_and_is_explicitly_not_assessed(status):
    snapshot, _ = example()
    result = SolverResult(status=status, schedule=(), validation=None, runtime_seconds=0.1, original_occurrence_count=2)
    session = FakeSession([stored(snapshot), SimpleNamespace(id=uuid4()), None])
    receipt = asyncio.run(SqlTeachingRepository(session, TENANT).append_attempt(uuid4(), uuid4(), result,
        expected_previous_version_id=None))
    assert receipt.version_id is receipt.version_number is None
    assert {type(row) for row in session.added} == {GenerationRun, ValidationReport}
    report = next(row for row in session.added if isinstance(row, ValidationReport))
    assert not report.passed and report.evidence["assessment"] == "NOT_ASSESSED"
    assert report.checked_occurrences == 0 and report.hard_violations is None


def test_corrupt_success_never_reaches_database_write_path():
    snapshot, result = example()
    session = FakeSession([stored(snapshot)])
    with pytest.raises(ValueError):
        asyncio.run(SqlTeachingRepository(session, TENANT).append_attempt(uuid4(), uuid4(),
            replace(result, schedule=result.schedule[:-1]), expected_previous_version_id=None))
    assert not session.added


def test_series_creation_and_version_reads_are_tenant_bound():
    session = FakeSession()
    repository = SqlTeachingRepository(session, TENANT)
    series_id = asyncio.run(repository.create_series("SYNTHETIC-SEMESTER-1"))
    assert session.added[0].id == series_id and session.added[0].institution_id == TENANT
    assert asyncio.run(repository.list_versions(series_id)) == ()
    sql = session.queries[-1].compile(dialect=postgresql.dialect())
    assert TENANT in sql.params.values() and series_id in sql.params.values()


@pytest.mark.parametrize("tenant", [None, "not-a-uuid", UUID(int=0)])
def test_tenant_cannot_be_omitted_or_invented(tenant):
    with pytest.raises(ValueError): SqlTeachingRepository(FakeSession(), tenant)


@pytest.mark.parametrize("fail", [False, True])
def test_shared_unit_of_work_sets_local_tenant_and_commits_or_rolls_back(fail):
    session, factory = FakeSession(), Mock()
    factory.return_value = session

    async def execute():
        async with teaching_unit_of_work(factory, TENANT) as repository:
            assert repository.institution_id == TENANT
            if fail: raise RuntimeError("synthetic rollback")

    if fail:
        with pytest.raises(RuntimeError): asyncio.run(execute())
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    else:
        asyncio.run(execute())
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()
    parameters = session.execute.await_args.args[0].compile().params
    assert "schedence.institution_id" in parameters.values() and str(TENANT) in parameters.values()
    assert True in parameters.values()


def test_flush_failure_rolls_back_the_whole_unit_of_work():
    session = FakeSession()
    session.flush.side_effect = RuntimeError("synthetic flush failure")

    async def execute():
        async with teaching_unit_of_work(lambda: session, TENANT) as repository:
            await repository.store_snapshot(example()[0])

    with pytest.raises(RuntimeError): asyncio.run(execute())
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    session.close.assert_awaited_once()
