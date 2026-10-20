"""Roster repositories: in-memory rules, SQL generation (mock session), models and the proposed migration.

Nothing here connects to a database. The SQL repository is exercised through a recording fake
session; the migration is rendered offline (SQL text only) on top of a copy of the active chain.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import io
import re
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.db.base import Base
from app.duty_allocation.persistence import models as m
from app.duty_allocation.persistence.domain import (
    AssignmentRecord, ChangeKind, ChangeRequestRecord, ChangeStatus, PersistenceConflict, RosterRecord, RosterStatus,
    SeatingDraftRecord, SeatingDraftStatus, SeatRecord, assignment_from_json, assignment_to_json, can_transition, WORKING,
)
from app.duty_allocation.persistence.repository import MemoryDutyRosterRepository, MemoryState
from app.duty_allocation.persistence.sql_repository import SqlDutyRosterRepository
from conftest import SERVICE_DIR

INST, OTHER = uuid.uuid4(), uuid.uuid4()
T0 = dt.datetime(2026, 11, 16, 9, 0)
NOW = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
STAFF = [str(uuid.uuid4()) for _ in range(3)]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def roster(period="PP1", version=1, status=RosterStatus.DRAFT, institution=INST, **kw) -> RosterRecord:
    values = dict(roster_id=uuid.uuid4(), institution_id=institution, period_code=period, version=version, status=status,
                  row_version=1, algorithm_mode="FAIR", roster_label="FAIR", timetable_reference="ref", timetable_fingerprint="fp",
                  timetable_verified=False, input_digest="0" * 64, data_source="LOCAL_DEMO", synthetic=True,
                  fairness_metrics={}, validation={}, created_by=uuid.uuid4(), created_at=NOW, updated_at=NOW)
    values.update(kw)
    return RosterRecord(**values)


def duty(session="S1", staff=STAFF[0], role="INVIGILATOR", start=T0, hours=2, venue="H1") -> AssignmentRecord:
    return AssignmentRecord(session, venue, staff, role, "EMP1", "IT1010", start, start + dt.timedelta(hours=hours))


# --- Status rules -----------------------------------------------------------------------------------

def test_lifecycle_transitions():
    S = RosterStatus
    allowed = {(S.DRAFT, S.VALIDATED), (S.DRAFT, S.REJECTED), (S.VALIDATED, S.VALIDATED), (S.VALIDATED, S.APPROVED),
               (S.VALIDATED, S.REJECTED), (S.APPROVED, S.PUBLISHED), (S.APPROVED, S.VALIDATED), (S.APPROVED, S.REJECTED),
               (S.PUBLISHED, S.SUPERSEDED)}
    for old in S:
        for new in S:
            assert can_transition(old, new) == ((old, new) in allowed), (old, new)
    assert set(WORKING) == {S.DRAFT, S.VALIDATED, S.APPROVED}


def test_assignment_json_round_trip():
    a = duty()
    assert assignment_from_json(assignment_to_json(a)) == a


# --- In-memory repository -----------------------------------------------------------------------------

def test_one_working_and_one_published_roster_per_period():
    repo = MemoryDutyRosterRepository(MemoryState())
    first = roster()
    run(repo.insert_roster(first, [duty()]))
    for status in (RosterStatus.DRAFT, RosterStatus.VALIDATED, RosterStatus.APPROVED):
        with pytest.raises(PersistenceConflict) as clash:
            run(repo.insert_roster(roster(version=2, status=status), []))
        assert clash.value.code == "ACTIVE_ROSTER_EXISTS" and clash.value.details["roster_id"] == str(first.roster_id)
    run(repo.insert_roster(roster(period="PP2"), []))  # another period is independent
    run(repo.insert_roster(roster(institution=OTHER), []))  # and so is another institution
    with pytest.raises(PersistenceConflict) as dup:
        run(repo.insert_roster(roster(version=1, status=RosterStatus.REJECTED), []))
    assert dup.value.code == "ROSTER_VERSION_EXISTS"
    run(repo.update_roster(first.roster_id, 1, status=RosterStatus.REJECTED))
    run(repo.insert_roster(roster(version=2), []))  # the working slot is free again


def test_a_published_roster_coexists_with_one_working_revision_only():
    repo = MemoryDutyRosterRepository(MemoryState())
    published = roster(status=RosterStatus.PUBLISHED, timetable_verified=False, synthetic=False,
                       published_timetable_reference="PUB-1")
    with pytest.raises(PersistenceConflict):  # an unverified timetable cannot be published
        run(repo.insert_roster(published, []))
    published = dataclass_replace(published, timetable_verified=True)
    run(repo.insert_roster(published, []))
    run(repo.insert_roster(roster(version=2), []))  # the working revision
    with pytest.raises(PersistenceConflict) as second_published:
        run(repo.insert_roster(roster(version=3, status=RosterStatus.PUBLISHED, timetable_verified=True, synthetic=False,
                                      published_timetable_reference="PUB-2"), []))
    assert second_published.value.code == "ACTIVE_ROSTER_EXISTS"


def dataclass_replace(obj, **kw):
    import dataclasses
    return dataclasses.replace(obj, **kw)


def test_synthetic_or_unverified_rosters_can_never_be_published_even_by_update():
    repo = MemoryDutyRosterRepository(MemoryState())
    r = roster()
    run(repo.insert_roster(r, []))
    with pytest.raises(PersistenceConflict) as refused:
        run(repo.update_roster(r.roster_id, 1, status=RosterStatus.PUBLISHED, timetable_verified=True,
                               published_timetable_reference="PUB"))  # still synthetic
    assert refused.value.code == "PUBLISH_NOT_ALLOWED"


def test_staff_cannot_hold_two_duties_in_one_session():
    repo = MemoryDutyRosterRepository(MemoryState())
    with pytest.raises(PersistenceConflict) as double:
        run(repo.insert_roster(roster(), [duty("S1", STAFF[0], "SUPERVISOR"), duty("S1", STAFF[0], "INVIGILATOR", venue="H2")]))
    assert double.value.code == "STAFF_DOUBLE_BOOKED"
    r = roster()
    run(repo.insert_roster(r, [duty("S1", STAFF[0]), duty("S2", STAFF[1])]))
    with pytest.raises(PersistenceConflict):
        run(repo.apply_assignment_change(r.roster_id, [duty("S2", STAFF[1])], [duty("S1", STAFF[0], "SUPERVISOR", venue="H2")]))
    with pytest.raises(PersistenceConflict) as missing:
        run(repo.apply_assignment_change(r.roster_id, [duty("S9", STAFF[2])], []))
    assert missing.value.code == "STALE_ROSTER"


def test_optimistic_locking_and_row_version():
    repo = MemoryDutyRosterRepository(MemoryState())
    r = roster()
    run(repo.insert_roster(r, []))
    updated = run(repo.update_roster(r.roster_id, 1, status=RosterStatus.VALIDATED))
    assert updated.row_version == 2 and updated.status is RosterStatus.VALIDATED
    with pytest.raises(PersistenceConflict) as stale:
        run(repo.update_roster(r.roster_id, 1, status=RosterStatus.APPROVED))
    assert stale.value.code == "STALE_ROSTER"


def test_commit_and_rollback_are_atomic():
    state = MemoryState()
    repo = MemoryDutyRosterRepository(state)
    r = roster()
    run(repo.insert_roster(r, [duty()]))
    run(repo.add_event(r.roster_id, "CREATED", None, "DRAFT", r.created_by, None, {}))
    run(repo.rollback())
    assert run(repo.get_roster(r.roster_id, INST)) is None and state.data.rosters == {}
    run(repo.insert_roster(r, [duty()]))
    run(repo.commit())
    other = MemoryDutyRosterRepository(state)
    assert run(other.get_roster(r.roster_id, INST)) is not None and len(run(other.get_assignments(r.roster_id))) == 1


def test_concurrent_commits_cannot_lose_updates():
    state = MemoryState()
    a, b = MemoryDutyRosterRepository(state), MemoryDutyRosterRepository(state)
    r = roster()
    run(a.insert_roster(r, []))
    run(a.commit())
    b2 = MemoryDutyRosterRepository(state)  # starts after the first commit
    run(a.update_roster(r.roster_id, 1, status=RosterStatus.VALIDATED))
    run(b2.update_roster(r.roster_id, 1, status=RosterStatus.REJECTED))
    run(a.commit())
    with pytest.raises(PersistenceConflict) as lost:
        run(b2.commit())
    assert lost.value.code == "CONCURRENT_CHANGE"
    assert run(MemoryDutyRosterRepository(state).get_roster(r.roster_id, INST)).status is RosterStatus.VALIDATED


def test_overlapping_assignments_of_other_periods():
    repo = MemoryDutyRosterRepository(MemoryState())
    other = roster(period="PP2")
    run(repo.insert_roster(other, [duty("A", STAFF[0], start=T0)]))
    run(repo.insert_roster(roster(period="PP3", status=RosterStatus.REJECTED), [duty("B", STAFF[0], start=T0)]))
    hit = run(repo.overlapping_assignments(INST, [STAFF[0]], T0 + dt.timedelta(hours=1), T0 + dt.timedelta(hours=3), "PP1"))
    assert [(rid, period) for rid, period, _ in hit] == [(other.roster_id, "PP2")]  # rejected rosters do not count
    assert run(repo.overlapping_assignments(INST, [STAFF[0]], T0 + dt.timedelta(hours=2), T0 + dt.timedelta(hours=4), "PP1")) == []
    assert run(repo.overlapping_assignments(INST, [STAFF[0]], T0, T0 + dt.timedelta(hours=2), "PP2")) == []  # same period excluded
    assert run(repo.overlapping_assignments(OTHER, [STAFF[0]], T0, T0 + dt.timedelta(hours=2), "PP1")) == []
    assert run(repo.overlapping_assignments(INST, [STAFF[1]], T0, T0 + dt.timedelta(hours=2), "PP1")) == []


def test_tenant_scoping_paging_and_audit_trail():
    repo = MemoryDutyRosterRepository(MemoryState())
    mine, theirs = roster(), roster(institution=OTHER)
    run(repo.insert_roster(mine, []))
    run(repo.insert_roster(theirs, []))
    assert run(repo.get_roster(theirs.roster_id, INST)) is None
    items, total = run(repo.list_rosters(INST, period_code=None, status=None, limit=10, offset=0))
    assert [i.roster_id for i in items] == [mine.roster_id] and total == 1
    e1 = run(repo.add_event(mine.roster_id, "A", None, "DRAFT", mine.created_by, "why", {"k": 1}))
    e2 = run(repo.add_event(mine.roster_id, "B", "DRAFT", "VALIDATED", mine.created_by, None, {}))
    assert (e1.event_id, e2.event_id) == (1, 2) and [e.action for e in run(repo.list_events(mine.roster_id))] == ["A", "B"]


def test_change_requests_use_compare_and_set():
    repo = MemoryDutyRosterRepository(MemoryState())
    r = roster()
    run(repo.insert_roster(r, []))
    req = ChangeRequestRecord(uuid.uuid4(), r.roster_id, INST, ChangeKind.SWAP, ChangeStatus.REQUESTED, 1, (duty(),), (duty(),),
                              "why", {}, {}, uuid.uuid4(), NOW)
    run(repo.insert_change_request(req))
    assert run(repo.get_change_request(req.request_id, OTHER)) is None
    done = run(repo.update_change_request(req.request_id, ChangeStatus.REQUESTED, status=ChangeStatus.APPROVED))
    assert done.status is ChangeStatus.APPROVED
    with pytest.raises(PersistenceConflict):
        run(repo.update_change_request(req.request_id, ChangeStatus.REQUESTED, status=ChangeStatus.REJECTED))
    assert run(repo.mark_open_requests_stale(r.roster_id, uuid.uuid4())) == 1


def seating_draft(period="PP1", version=1, state="SYNTHETIC", synthetic=True) -> SeatingDraftRecord:
    return SeatingDraftRecord(uuid.uuid4(), INST, period, version, SeatingDraftStatus.DRAFT, "ref", "LOCAL_DEMO", synthetic, state,
                              "rules", {}, uuid.uuid4(), NOW)


def seat(student="IT1", number=1, status="SYNTHETIC", session="S1") -> SeatRecord:
    return SeatRecord(session, "H1", student, "IT1010", number, number, f"R1-S{number}", status)


def test_seating_drafts_supersede_and_never_verify_synthetic_seats():
    repo = MemoryDutyRosterRepository(MemoryState())
    first = seating_draft()
    run(repo.insert_seating_draft(first, [seat("IT1", 1), seat("IT2", 2)]))
    second = seating_draft(version=2)
    run(repo.insert_seating_draft(second, [seat("IT1", 1)]))
    assert run(repo.get_seating_draft(first.draft_id, INST)).status is SeatingDraftStatus.SUPERSEDED
    assert run(repo.get_seating_draft(second.draft_id, INST)).status is SeatingDraftStatus.DRAFT
    assert run(repo.get_seating_draft(second.draft_id, OTHER)) is None
    assert run(repo.latest_seating_version(INST, "PP1")) == 2
    for bad_draft, seats in ((seating_draft(version=3, state="VERIFIED"), [seat()]),
                             (seating_draft(version=3), [seat(status="VERIFIED")])):
        with pytest.raises(PersistenceConflict) as refused:
            run(repo.insert_seating_draft(bad_draft, seats))
        assert refused.value.code == "SEATS_NOT_VERIFIABLE"
    with pytest.raises(PersistenceConflict) as double:
        run(repo.insert_seating_draft(seating_draft(version=3), [seat("IT1", 1), seat("IT2", 1)]))  # one seat, two students
    assert double.value.code == "SEAT_DOUBLE_BOOKED"
    with pytest.raises(PersistenceConflict):
        run(repo.insert_seating_draft(seating_draft(version=3), [seat("IT1", 1), seat("IT1", 2)]))  # one student, two seats


# --- SQL repository on a recording fake session ---------------------------------------------------------------

class FakeResult:
    def __init__(self, rows=(), rowcount=1, scalar=0):
        self.rows, self.rowcount, self._scalar = list(rows), rowcount, scalar

    def one(self):
        return self.rows[0]

    def one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        return self._scalar

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self, *results, error=None):
        self.statements, self.params, self.results, self.error = [], [], list(results), error
        self.commits = self.rollbacks = 0

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        self.statements.append(str(compiled))
        self.params.append(compiled.params)
        if self.error is not None:
            raise self.error
        return self.results.pop(0) if self.results else FakeResult()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def roster_row(r: RosterRecord):
    values = {c.name: getattr(r, c.name) for c in m.DutyRoster.__table__.columns}
    values["status"] = r.status.value
    return SimpleNamespace(_asdict=lambda: values)


def sql_error(constraint=None, sqlstate=None, kind=IntegrityError):
    diag = SimpleNamespace(constraint_name=constraint)
    return kind("stmt", {}, SimpleNamespace(diag=diag, sqlstate=sqlstate))


def test_sql_repository_only_writes_the_duty_tables_with_schema_qualified_names():
    session = FakeSession()
    repo = SqlDutyRosterRepository(session)
    r = roster()
    run(repo.insert_roster(r, [duty(), duty("S2", STAFF[1])]))
    sql = "\n".join(session.statements)
    assert re.findall(r"INSERT INTO ([\w.]+)", sql) == ["examinations.duty_rosters", "examinations.duty_roster_assignments"]
    assert "m3_demo" not in sql and "academic" not in sql
    assert all(re.search(r"(?:INSERT INTO|UPDATE|FROM|DELETE FROM) examinations\.", s) for s in session.statements)
    assert uuid.UUID(STAFF[0]) in session.params[1].values()  # staff ids are bound as UUID parameters


def test_update_is_an_optimistic_compare_and_set():
    updated = roster(row_version=4)
    session = FakeSession(FakeResult([roster_row(updated)]))
    repo = SqlDutyRosterRepository(session)
    got = run(repo.update_roster(updated.roster_id, 3, status=RosterStatus.VALIDATED, validated_by=uuid.uuid4()))
    sql = session.statements[0]
    where = sql.split(" WHERE ")[1].split(" RETURNING")[0]
    assert "examinations.duty_rosters.roster_id = %(roster_id_1)s" in where and "examinations.duty_rosters.row_version = %(row_version_1)s" in where
    assert "RETURNING" in sql and session.params[0]["row_version_1"] == 3  # the version the caller saw
    assert session.params[0]["row_version"] == 4 and session.params[0]["status"] == "VALIDATED" and got.row_version == 4
    lost = FakeSession(FakeResult([]))
    with pytest.raises(PersistenceConflict) as stale:
        run(SqlDutyRosterRepository(lost).update_roster(updated.roster_id, 3, status=RosterStatus.REJECTED))
    assert stale.value.code == "STALE_ROSTER"


def test_lock_and_tenant_filter_on_reads():
    session = FakeSession(FakeResult([]))
    assert run(SqlDutyRosterRepository(session).get_roster(uuid.uuid4(), INST, lock=True)) is None
    sql = session.statements[0]
    assert sql.rstrip().endswith("FOR UPDATE") and "institution_id = %(institution_id_1)s" in sql
    plain = FakeSession(FakeResult([]))
    run(SqlDutyRosterRepository(plain).get_roster(uuid.uuid4(), INST))
    assert "FOR UPDATE" not in plain.statements[0]


@pytest.mark.parametrize("constraint,code", [
    ("uq_duty_rosters_one_working_per_period", "ACTIVE_ROSTER_EXISTS"),
    ("uq_duty_rosters_one_published_per_period", "ACTIVE_ROSTER_EXISTS"),
    ("uq_duty_rosters_institution_id_period_code_version", "ROSTER_VERSION_EXISTS"),
    ("uq_duty_roster_assignments_roster_session_staff", "STAFF_DOUBLE_BOOKED"),
    ("ck_duty_rosters_published_needs_verified_timetable", "PUBLISH_NOT_ALLOWED"),
    ("uq_seating_draft_seats_draft_session_student", "SEAT_DOUBLE_BOOKED"),
    ("ck_seating_drafts_synthetic_never_verified", "SEATS_NOT_VERIFIABLE"),
    ("something_else", "PERSISTENCE_CONFLICT"),
])
def test_database_constraint_violations_become_coded_conflicts(constraint, code):
    repo = SqlDutyRosterRepository(FakeSession(error=sql_error(constraint)))
    with pytest.raises(PersistenceConflict) as conflict:
        run(repo.insert_roster(roster(), []))
    assert conflict.value.code == code and "stmt" not in conflict.value.message


def test_missing_tables_mean_the_migration_is_not_applied():
    repo = SqlDutyRosterRepository(FakeSession(error=sql_error(sqlstate="42P01", kind=ProgrammingError)))
    with pytest.raises(PersistenceConflict) as missing:
        run(repo.latest_version(INST, "PP1"))
    assert missing.value.code == "PERSISTENCE_NOT_MIGRATED"
    other = SqlDutyRosterRepository(FakeSession(error=sql_error(sqlstate="42601", kind=ProgrammingError)))
    with pytest.raises(ProgrammingError):
        run(other.latest_version(INST, "PP1"))


def test_removing_a_missing_duty_is_a_stale_conflict_and_commit_rollback_delegate():
    session = FakeSession(FakeResult(rowcount=0))
    repo = SqlDutyRosterRepository(session)
    with pytest.raises(PersistenceConflict) as stale:
        run(repo.apply_assignment_change(uuid.uuid4(), [duty()], []))
    assert stale.value.code == "STALE_ROSTER" and session.statements[0].startswith("DELETE FROM examinations.duty_roster_assignments")
    run(repo.commit())
    run(repo.rollback())
    assert (session.commits, session.rollbacks) == (1, 1)


def test_overlap_query_filters_status_period_and_interval():
    session = FakeSession(FakeResult([]))
    run(SqlDutyRosterRepository(session).overlapping_assignments(INST, [STAFF[0]], T0, T0 + dt.timedelta(hours=2), "PP1"))
    sql = session.statements[0]
    for part in ("duty_roster_assignments.starts_at <", "duty_roster_assignments.ends_at >", "duty_rosters.period_code !=",
                 "duty_rosters.status IN", "duty_roster_assignments.staff_id IN"):
        assert part in sql
    flat = [v for value in session.params[0].values() for v in (value if isinstance(value, list) else [value])]
    assert "PP1" in flat and INST in flat and uuid.UUID(STAFF[0]) in flat
    assert run(SqlDutyRosterRepository(FakeSession()).overlapping_assignments(INST, [], T0, T0, "PP1")) == []


# --- Models and the proposed migration -----------------------------------------------------------------------

def test_models_are_isolated_from_the_shared_metadata():
    assert set(m.DutyBase.metadata.tables) == {f"examinations.{n}" for n in (
        "duty_rosters", "duty_roster_assignments", "duty_roster_events", "duty_change_requests", "seating_drafts",
        "seating_draft_seats")}
    assert not set(m.DutyBase.metadata.tables) & set(Base.metadata.tables)  # importing them cannot upset the shared tests
    assert m.DutyBase.metadata is not Base.metadata and m.DutyBase.metadata.schema == "examinations"
    assert all(len(c.name) <= 63 for t in m.DutyBase.metadata.sorted_tables for c in t.constraints if c.name)


def render(revision: str, *, downgrade=False, x=()) -> str:
    """Offline SQL of the proposed migration on top of a COPY of the active chain."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "alembic"
        shutil.copytree(SERVICE_DIR / "alembic", copy, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(SERVICE_DIR / "proposed_migrations" / "20261020_0007_duty_roster_persistence.py", copy / "versions")
        buffer = io.StringIO()
        config = Config(str(SERVICE_DIR / "alembic.ini"), stdout=buffer, cmd_opts=argparse.Namespace(x=list(x)))
        config.set_main_option("script_location", str(copy))
        config.output_buffer = buffer
        (command.downgrade if downgrade else command.upgrade)(config, revision, sql=True)
        return buffer.getvalue()


@pytest.fixture
def migration_sql(monkeypatch) -> str:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake_user:fake_password@ep-example-123456.eu-central-1.aws.neon.tech/neondb")
    return render("0006:0007")


def test_proposed_migration_is_not_in_the_active_chain():
    active = {p.name for p in (SERVICE_DIR / "alembic" / "versions").glob("*.py")}
    assert not any("0007" in name for name in active)  # activating it is a reviewed, separate step
    assert (SERVICE_DIR / "proposed_migrations" / "20261020_0007_duty_roster_persistence.py").is_file()


def test_migration_creates_exactly_the_model_tables(migration_sql):
    created = set(re.findall(r"CREATE TABLE examinations\.(\w+)", migration_sql))
    assert created == {t.name for t in m.DutyBase.metadata.sorted_tables}
    for table in m.DutyBase.metadata.sorted_tables:
        block = re.search(rf"CREATE TABLE examinations\.{table.name} \((.*?)\n\);", migration_sql, re.S).group(1)
        for column in table.columns:
            assert re.search(rf"^\s+{column.name} ", block, re.M), f"{table.name}.{column.name}"
            line = re.search(rf"^\s+{column.name} [^\n]*", block, re.M).group(0)
            assert ("NOT NULL" in line) == (not column.nullable) or column.primary_key, f"{table.name}.{column.name} nullability"


def test_migration_constraint_and_index_names_match_the_models(migration_sql):
    expected = {c.name for t in m.DutyBase.metadata.sorted_tables for c in t.constraints if c.name}
    in_sql = set(re.findall(r"CONSTRAINT (\w+)", migration_sql))
    assert in_sql == expected
    indexes = {i.name for t in m.DutyBase.metadata.sorted_tables for i in t.indexes}
    assert set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", migration_sql)) == indexes
    assert "WHERE status IN ('DRAFT', 'VALIDATED', 'APPROVED')" in migration_sql and "WHERE status = 'PUBLISHED'" in migration_sql
    assert "WHERE status = 'DRAFT'" in migration_sql


def test_migration_is_additive_and_confined_to_examinations(migration_sql):
    body = migration_sql.split("Running upgrade 0006 -> 0007")[1].upper()
    body = "\n".join(line for line in body.splitlines() if "ALEMBIC_VERSION" not in line)  # Alembic's own bookkeeping
    for forbidden in ("DROP ", "DELETE FROM", "TRUNCATE", "UPDATE ", "INSERT INTO", "ALTER TABLE", "CREATE SCHEMA", "M3_DEMO"):
        assert forbidden not in body
    names = re.findall(r"(?:CREATE TABLE|REFERENCES|CREATE (?:UNIQUE )?INDEX \w+ ON) (\S+)", migration_sql.split("0006 -> 0007")[1])
    assert names and all(n.startswith("examinations.") for n in names)
    assert migration_sql.count("BEGIN;") == 1 and "fake_password" not in migration_sql


def test_migration_publish_and_seating_rules_are_in_the_database(migration_sql):
    assert "status <> 'PUBLISHED' OR (timetable_verified AND NOT synthetic AND published_timetable_reference IS NOT NULL)" in migration_sql
    assert "seat_state <> 'VERIFIED' OR NOT synthetic" in migration_sql
    assert "UNIQUE (roster_id, session_id, staff_id)" in migration_sql
    assert "UNIQUE (draft_id, session_id, venue_id, physical_seat_number)" in migration_sql


def test_migration_downgrade_refuses_to_lose_saved_rosters(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake_user:fake_password@ep-example-123456.eu-central-1.aws.neon.tech/neondb")
    guarded = render("0007:0006", downgrade=True)
    assert guarded.index("RAISE EXCEPTION") < guarded.index("DROP TABLE examinations.duty_rosters")
    assert set(re.findall(r"DROP TABLE ([\w.]+)", guarded)) == {f"examinations.{t.name}" for t in m.DutyBase.metadata.sorted_tables}
    assert "RAISE EXCEPTION" not in render("0007:0006", downgrade=True, x=["drop_duty_rosters=true"])
