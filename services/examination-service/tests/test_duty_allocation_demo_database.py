"""m3_demo read-only adapter and mapper: safety, schema checks and record mapping.

All data is SYNTHETIC and mocked; nothing connects to a database.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.duty_allocation import DutyRole, LoadUnit, MissingAdminHours
from app.duty_allocation.demo_database import (
    BEGIN_SNAPSHOT, TABLE_COLUMNS, DemoDatabaseUnavailable, DemoSchemaError, DemoTargetRefused, ReadOnlyViolation, SqlRowSource,
    assert_read_only, load_m3_demo_records, verify_demo_target,
)
from app.duty_allocation.demo_mapper import (
    DEMO_REFERENCE_PREFIX, DemoDataError, check_institution, map_capacity_rules, map_halls, map_policies, map_staff,
    map_supplemental, map_timetable,
)
from app.duty_allocation.student_seating import PROJECT_CAPACITY_RULES, LayoutStatus
from duty_allocation_demo_support import DEMO_REF, INST, PERIOD, compact_source, sid

INSTITUTION = uuid.UUID(INST)


async def records(change=None):
    return await load_m3_demo_records(compact_source(change))


# --- Read-only safety -------------------------------------------------------------------------------

@pytest.mark.parametrize("statement", [
    "INSERT INTO m3_demo.staff VALUES (1)", "UPDATE m3_demo.staff SET x=1", "DELETE FROM m3_demo.staff",
    "TRUNCATE m3_demo.staff", "CREATE TABLE x (a int)", "ALTER TABLE m3_demo.staff ADD c int", "DROP TABLE m3_demo.staff",
    "SELECT 1; DELETE FROM m3_demo.staff", "WITH x AS (DELETE FROM m3_demo.staff RETURNING *) SELECT * FROM x",
    "SET TRANSACTION READ WRITE", "COMMIT", "select * from m3_demo.staff for update; drop table x", "GRANT ALL ON x TO y",
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ WRITE", "SET TRANSACTION READ ONLY; DROP TABLE x",
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY; DELETE FROM m3_demo.staff",
])
def test_write_statements_are_refused(statement):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(statement)


def test_read_statements_are_allowed():
    assert_read_only(BEGIN_SNAPSHOT)
    assert_read_only('SELECT "id", "created_at" FROM "m3_demo"."staff" ORDER BY 1')


class RecordingSession:
    """Stands in for an AsyncSession; records SQL and returns canned rows."""

    def __init__(self, rows=None, error=None, settings=("on", "repeatable read")):
        self.statements, self.rows, self.error, self.settings = [], rows or {}, error, settings

    async def execute(self, clause, params=None):
        sql = str(clause)
        self.statements.append(sql)
        if self.error:
            raise self.error
        table = sql.split('"m3_demo"."')[1].split('"')[0] if '"m3_demo"."' in sql else None
        if "current_setting" in sql:
            result_rows = [self.settings]
        elif table:
            result_rows = self.rows.get(table, [])
        else:
            result_rows = [(t, c) for t, cols in TABLE_COLUMNS.items() for c in cols] if "information_schema" in sql else []
        return type("Result", (), {"all": lambda self_: result_rows})()

    def add(self, *_):
        raise AssertionError("no ORM writes")

    async def commit(self):
        raise AssertionError("the adapter must never commit")


async def test_sql_source_runs_only_a_read_only_transaction_and_selects():
    session = RecordingSession()
    snapshot = await load_m3_demo_records(SqlRowSource(session))
    assert session.statements[0] == BEGIN_SNAPSHOT  # one consistent read-only snapshot
    assert "current_setting('transaction_read_only')" in session.statements[1]
    assert all(s.startswith("SELECT") for s in session.statements[1:])
    assert all(t in snapshot.tables for t in TABLE_COLUMNS)
    assert sum('FROM "m3_demo".' in s for s in session.statements) == len(TABLE_COLUMNS)


@pytest.mark.parametrize("settings", [("off", "repeatable read"), ("on", "read committed")])
async def test_reading_stops_if_the_transaction_is_not_a_read_only_snapshot(settings):
    session = RecordingSession(settings=settings)
    with pytest.raises(ReadOnlyViolation, match="REPEATABLE READ"):
        await load_m3_demo_records(SqlRowSource(session))
    assert not any('FROM "m3_demo".' in s for s in session.statements)  # no table was read


async def test_sql_source_only_reads_allow_listed_tables():
    with pytest.raises(DemoSchemaError):
        await SqlRowSource(RecordingSession()).fetch("pg_shadow")
    with pytest.raises(DemoSchemaError):
        await SqlRowSource(RecordingSession()).fetch('staff"; DROP TABLE x; --')


async def test_database_unavailable_is_reported_without_details():
    error = OperationalError("SELECT 1", {}, Exception("connection to host secret-host.neon.tech failed password=abc"))
    with pytest.raises(DemoDatabaseUnavailable) as excinfo:
        await load_m3_demo_records(SqlRowSource(RecordingSession(error=error)))
    assert "secret-host" not in str(excinfo.value) and "abc" not in str(excinfo.value)
    with pytest.raises(DemoDatabaseUnavailable):
        await load_m3_demo_records(compact_source(fail_on="staff"))


async def test_incorrect_schema_is_refused():
    with pytest.raises(DemoSchemaError, match="m3_demo.staff is missing"):
        await load_m3_demo_records(compact_source(change=lambda t: t.pop("staff")))
    columns = {name: set(cols) for name, cols in TABLE_COLUMNS.items()}
    columns["staff"].discard("academic_rank")
    with pytest.raises(DemoSchemaError, match="lacks columns: academic_rank"):
        await load_m3_demo_records(compact_source(columns=columns))


async def test_only_reads_happen_on_the_mock_source():
    source = compact_source()
    await load_m3_demo_records(source)
    assert source.calls[0] == BEGIN_SNAPSHOT
    assert all(c == "schema_columns" or c.startswith("fetch ") for c in source.calls[1:])


# --- Connection target verification (never connects) --------------------------------------------------

URL = "postgresql://demo_user:not-a-real-secret@ep-dev-branch.example.neon.tech/neondb?sslmode=require"
DEV = dict(expected_host="ep-dev-branch.example.neon.tech", expected_database="neondb",
           expected_branch="member3-development", confirmed_in_neon_console=True)


def test_verified_target_hides_credentials():
    target = verify_demo_target(URL, **DEV)
    assert "member3-development (user-confirmed in Neon Console)" in target.description
    assert "not-a-real-secret" not in target.description and "demo_user" not in target.description
    assert "not-a-real-secret" not in repr(target) and "not-a-real-secret" in target.url


@pytest.mark.parametrize("changes,match", [
    ({"confirmed_in_neon_console": False}, "Neon Console"),
    ({"confirmed_in_neon_console": "yes"}, "Neon Console"),
    ({"expected_branch": "production"}, "member3-development"),
    ({"expected_branch": "main"}, "member3-development"),
    ({"expected_host": "ep-production.example.neon.tech"}, "does not match"),
    ({"expected_database": "otherdb"}, "database does not match"),
    ({"expected_host": "  "}, "explicit development URL"),
    ({"forbidden_hosts": ("EP-DEV-BRANCH.example.neon.tech",)}, "forbidden"),
    ({"forbidden_hosts": "ep-dev-branch.example.neon.tech"}, "tuple of host names"),
])
def test_target_is_refused_unless_every_check_passes(changes, match):
    with pytest.raises(DemoTargetRefused, match=match) as excinfo:
        verify_demo_target(URL, **(DEV | changes))
    assert "not-a-real-secret" not in str(excinfo.value)


def test_non_postgres_url_is_refused_without_leaking_it():
    with pytest.raises(DemoTargetRefused) as excinfo:
        verify_demo_target("mysql://u:p4ss@h/db", **(DEV | {"expected_host": "h", "expected_database": "db"}))
    assert "p4ss" not in str(excinfo.value)


# --- Staff mapping ------------------------------------------------------------------------------------

async def test_staff_mapping_preserves_fields_and_applies_rank_rules():
    snap = await records()
    policies = map_policies(snap)
    staff = map_staff(snap, institution_id=INSTITUTION, eligibility=policies.eligibility)
    by_id = {m.staff_id: m for m in staff.staff.staff}
    assert len(by_id) == 7 and by_id[sid(1)].staff_number == "EMP0001" and by_id[sid(1)].full_name == "Synthetic Staff 1"
    assert by_id[sid(4)].eligible_roles == (DutyRole.SUPERVISOR, DutyRole.INVIGILATOR)  # Senior Lecturer II
    assert by_id[sid(5)].eligible_roles == (DutyRole.INVIGILATOR,)  # Lecturer
    assert [(e.staff_id, e.staff_number) for e in staff.staff.excluded] == [(sid(8), "EMP0008")]


async def test_institution_separation():
    snap = await records()
    with pytest.raises(DemoDataError, match="no single row"):
        check_institution(snap, uuid.UUID(int=99))
    other = await records(lambda t: t["staff"][0].update(institution_id="22222222-2222-4222-8222-222222222222"))
    with pytest.raises(DemoDataError, match="another institution"):
        map_staff(other, institution_id=INSTITUTION, eligibility=map_policies(other).eligibility)
    real = await records(lambda t: t["institutions"][0].update(dataset_status="PRODUCTION"))
    with pytest.raises(DemoDataError, match="not marked SYNTHETIC_TEST_DATA"):
        check_institution(real, INSTITUTION)
    wl = await records(lambda t: t["staff_admin_workload"][0].update(institution_id="22222222-2222-4222-8222-222222222222"))
    with pytest.raises(DemoDataError, match="workload.*another institution"):
        map_staff(wl, institution_id=INSTITUTION, eligibility=map_policies(wl).eligibility)


async def test_admin_workload_keeps_computed_not_computed_and_missing_apart():
    snap = await records()
    staff = map_staff(snap, institution_id=INSTITUTION, eligibility=map_policies(snap).eligibility)
    admin = {a.staff_id: a for a in staff.admin}
    assert admin[sid(1)].hours_per_week == 2.0 and admin[sid(5)].hours_per_week is None
    assert [m.staff_id for m in staff.missing_workload] == [sid(6)] and "MISSING_RESPONSE" in staff.missing_workload[0].reason
    assert sid(8) not in admin  # inactive staff excluded before workload is read


@pytest.mark.parametrize("change,match", [
    (lambda t: t["staff_admin_workload"][0].update(aggregate_status="ESTIMATED"), "unknown aggregate_status"),
    (lambda t: t["staff_admin_workload"][0].update(total_admin_hours_per_week=None), "COMPUTED without valid hours"),
    (lambda t: t["staff_admin_workload"][4].update(total_admin_hours_per_week=Decimal(3)), "NOT_COMPUTED but has hours"),
    (lambda t: t["staff_admin_workload"].append(dict(t["staff_admin_workload"][0], as_of=dt.date(2026, 12, 1))),
     "pass as_of"),
    (lambda t: t["staff_admin_workload"][0].update(staff_id=sid(99)), "unknown staff"),
])
async def test_malformed_workload_records(change, match):
    snap = await records(change)
    with pytest.raises(DemoDataError, match=match):
        map_staff(snap, institution_id=INSTITUTION, eligibility=map_policies(snap).eligibility)


async def test_workload_without_any_row_is_missing_not_zero():
    snap = await records(lambda t: t["staff_admin_workload"].pop(2))  # staff 3
    staff = map_staff(snap, institution_id=INSTITUTION, eligibility=map_policies(snap).eligibility)
    assert sid(3) in {m.staff_id for m in staff.missing_workload} and sid(3) not in {a.staff_id for a in staff.admin}


# --- Policies ----------------------------------------------------------------------------------------

async def test_policies_map_from_m3_demo_and_are_labelled_synthetic():
    p = map_policies(await records())
    assert p.eligibility.rank_order[0] == "Professor" and p.eligibility.minimum_gap_minutes == 60
    assert p.eligibility.rule(DutyRole.SUPERVISOR).minimum_rank == "Senior Lecturer II"
    assert (p.limits.max_duties_per_day, p.limits.max_duties_per_period) == (2, 7)
    assert p.staffing.rule_for(109).supervisors == 2 and p.staffing.rule_for(500).supervisors == 3
    assert p.fairness.load_unit is LoadUnit.DUTIES and p.fairness.missing_admin_hours is MissingAdminHours.ZERO
    assert all("SYNTHETIC" in s and "not SLIIT" in s for s in (p.staffing.source, p.eligibility.source,
                                                                p.limits.source, p.fairness.source))


@pytest.mark.parametrize("change,match", [
    (lambda t: t["eligibility_rank_order"][4].update(supervisor_eligible=True), "contradicts"),
    (lambda t: t["eligibility_role_rules"][0].update(minimum_gap_minutes=30), "minimum_gap_minutes differs"),
    (lambda t: t["staff_duty_limits"][0].update(max_duties_per_day=3), "per-staff limits are not supported"),
    (lambda t: t["staff_duty_limits"][0].update(minimum_required_rest_minutes=15), "rest 15 min"),
    (lambda t: t["fairness_policy"][0].update(weight_balance=Decimal("0.5")), "whole number"),
    (lambda t: t["fairness_policy"][0].update(load_unit="HOURS"), "fairness_policy is invalid"),
    (lambda t: t["fairness_policy"][0].update(institution_verified=True), "claims institution verification"),
    (lambda t: t["staffing_rules"][1].update(min_students=50), "overlap"),
    (lambda t: t["staffing_rules"][0].update(institution_verified=True), "claims institution verification"),
    (lambda t: t["eligibility_role_rules"].pop(), "exactly one SUPERVISOR"),
    (lambda t: t["duty_limit_policy"].append(dict(t["duty_limit_policy"][0], policy_id="x")), "exactly one row"),
])
async def test_invalid_policies(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_policies(await records(change))


async def test_capacities_cross_check_when_consistent():
    assert "every room allocation agree" in map_capacity_rules(await records(), PROJECT_CAPACITY_RULES)[0]


def smart_venue(t, percentage=None, effective=None):
    t["venues"].append(dict(venue_id="S1", venue_name="S1 Smart", venue_type="smart_classroom", building="C", floor=1,
                            normal_capacity=40, exam_capacity=20))
    t["capacity_policies"].append(dict(venue_id="S1", venue_type="smart_classroom", normal_capacity=40,
                                       exam_percentage=percentage, effective_exam_capacity=effective,
                                       policy_source="UNCONFIGURED", institution_verified=False,
                                       dataset_status="SYNTHETIC_TEST_DATA"))


async def test_unconfigured_venue_type_is_accepted_but_never_given_a_percentage():
    assert map_capacity_rules(await records(smart_venue), PROJECT_CAPACITY_RULES)
    invented = await records(lambda t: smart_venue(t, 75, 30))
    with pytest.raises(DemoDataError, match="smart_classroom.*75% -> 30; the project rules give None% -> None"):
        map_capacity_rules(invented, PROJECT_CAPACITY_RULES)


async def test_room_in_an_unconfigured_venue_is_rejected():
    def use_smart(t):
        smart_venue(t)
        t["exam_room_allocations"][0].update(venue_id="S1", effective_capacity="20")
    with pytest.raises(DemoDataError, match="no configured exam capacity \\(smart_classroom\\); no percentage is assumed"):
        map_capacity_rules(await records(use_smart), PROJECT_CAPACITY_RULES)


@pytest.mark.parametrize("change,match", [
    (lambda t: t["exam_room_allocations"][0].update(effective_capacity="5"), "contradicts the venue policy capacity 4"),
    (lambda t: t["exam_room_allocations"][0].update(effective_capacity=" 4"), "is not a whole number"),
    (lambda t: t["exam_room_allocations"][0].update(effective_capacity=4), "is not a whole number"),
    (lambda t: t["capacity_policies"][0].update(effective_exam_capacity=5), "50% -> 5; the project rules give 50% -> 4"),
    (lambda t: t["capacity_policies"][1].update(exam_percentage=100, effective_exam_capacity=10), "90% -> 9"),
    (lambda t: t["capacity_policies"][0].update(normal_capacity=10), "differs from venues"),
    (lambda t: t["capacity_policies"][0].update(venue_type="computer_lab"), "differs from venues"),
    (lambda t: t["capacity_policies"].pop(0), "Venue H1 has 0 capacity_policies rows"),
    (lambda t: t["capacity_policies"].append(dict(t["capacity_policies"][0])), "Venue H1 has 2 capacity_policies rows"),
    (lambda t: t["capacity_policies"].append(dict(t["capacity_policies"][0], venue_id="Z9")), "unknown venue"),
    (lambda t: t["capacity_policies"][0].update(institution_verified=True), "claims institution verification"),
    (lambda t: t["exam_room_allocations"][0].update(allocated_count=5, effective_capacity="4"), "over the policy capacity 4"),
])
async def test_contradictory_capacities_are_rejected(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_capacity_rules(await records(change), PROJECT_CAPACITY_RULES)


# --- Supplemental data (only what is stored) ---------------------------------------------------------

async def test_supplemental_data_maps_exactly_the_stored_rows():
    s = map_supplemental(await records())
    assert [(u.staff_id, u.reason) for u in s.unavailability] == [(sid(2), "SYNTHETIC leave")]
    assert [a.staff_id for a in s.availability] == [sid(1)]
    assert [(p.staff_id, p.kind.value) for p in s.day_preferences] == [(sid(3), "AVOID"), (sid(4), "PREFER")]
    assert {h.staff_id: h.prior_duties for h in s.duty_history}[sid(4)] == 1
    assert [(r.staff_id, r.module_code, r.role) for r in s.restrictions] == [(sid(1), "IT1010", None)]
    assert "SYNTHETIC" in s.source


async def test_empty_supplemental_tables_stay_empty_not_invented():
    def empty(t):
        for name in ("staff_unavailability", "staff_availability", "staff_day_preferences", "staff_restrictions",
                     "staff_duty_history"):
            t[name] = []
    s = map_supplemental(await records(empty))
    assert (s.unavailability, s.availability, s.day_preferences, s.restrictions, s.duty_history) == ((),) * 5


async def test_duty_history_periods_are_summed_and_reported():
    snap = await records(lambda t: t["staff_duty_history"].append(dict(t["staff_duty_history"][3], previous_period="2025-S2")))
    s = map_supplemental(snap)
    assert {h.staff_id: h.prior_duties for h in s.duty_history}[sid(4)] == 2 and "2025-S2, 2026-S1" in s.source


@pytest.mark.parametrize("change,match", [
    (lambda t: t["staff_unavailability"][0].update(staff_id=sid(99)), "unknown staff"),
    (lambda t: t["staff_unavailability"][0].update(end_at=dt.datetime(2026, 11, 16, 6)), "end after it starts"),
    (lambda t: t["staff_unavailability"][0].update(start_at="2026-11-16T07:00"), "must be datetime"),
    (lambda t: t["staff_availability"][0].update(start_at=dt.datetime(2026, 11, 16, 8, tzinfo=dt.UTC)), "must be datetime"),
    (lambda t: t["staff_day_preferences"][0].update(preference_kind="MAYBE"), "MAYBE"),
    (lambda t: t["staff_restrictions"][0].update(restriction_type="SAME_DEPARTMENT"), "unsupported restriction"),
    (lambda t: t["staff_restrictions"][0].update(module_code="XX9999"), "unknown module"),
    (lambda t: t["staff_restrictions"].append(dict(t["staff_restrictions"][0])), "duplicate key"),
    (lambda t: t["staff_duty_history"][0].update(previous_duties=-1), "prior_duties"),
])
async def test_malformed_supplemental_records(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_supplemental(await records(change))


# --- Exam sessions and rooms -------------------------------------------------------------------------

async def test_timetable_maps_to_member2_draft_contract():
    t = map_timetable(await records(), period_code=PERIOD, demo_reference=DEMO_REF)
    d = t.draft
    assert d.period_code == PERIOD and d.input_fingerprint == DEMO_REF
    assert [s.session_id for s in d.sessions] == ["X1-IT1010", "X2-IT1020", "X3-IT1030"]
    x2 = d.sessions[1]
    assert [r.venue_id for r in x2.rooms] == ["H1", "L1"] and [len(r.seats) for r in x2.rooms] == [2, 2]
    assert x2.rooms[0].seats[0].seat_number == 1 and x2.start == dt.datetime(2026, 11, 16, 11, 30)
    assert {v.venue_id: v.venue_type for v in t.venues} == {"H1": "lecture_hall", "L1": "computer_lab"}
    assert t.publication_statuses == ("SYNTHETIC_DRAFT/NOT_PUBLISHED",)
    assert "not a published timetable or source fingerprint" in t.notes[0]


@pytest.mark.parametrize("ref", [None, "", "fp-123", DEMO_REFERENCE_PREFIX, "member2-fingerprint"])
async def test_demo_reference_must_be_explicit_and_labelled(ref):
    with pytest.raises(DemoDataError, match="DEMO-UNPUBLISHED"):
        map_timetable(await records(), period_code=PERIOD, demo_reference=ref)


@pytest.mark.parametrize("change,match", [
    (lambda t: t["exam_room_allocations"][0].update(venue_id="Z9"), "unknown venue"),
    (lambda t: t["exam_sessions"][0].update(module_code="XX0000"), "unknown module"),
    (lambda t: t["exam_logical_seats"][0].update(student_id="IT99999999"), "unknown student"),
    (lambda t: t["exam_logical_seats"][0].update(session_id="NOPE"), "unknown session"),
    (lambda t: t["exam_logical_seats"][0].update(venue_id="L1"), "no room allocation"),
    (lambda t: t["registrations"].pop(0), "not registered"),
    (lambda t: t["exam_logical_seats"].pop(0), "allocated_count 4 but 3"),
    (lambda t: t["exam_logical_seats"][1].update(logical_seat_number=1), "unique positive ints"),
    (lambda t: t["exam_room_allocations"][0].update(effective_capacity="3"), "over capacity"),
    (lambda t: t["exam_room_allocations"][0].update(effective_capacity="lots"), "not a whole number"),
    (lambda t: t["exam_sessions"][0].update(end_at=dt.datetime(2026, 11, 16, 8)), "ends before it starts"),
    (lambda t: t["exam_sessions"][0].update(start_at="2026-11-16 08:30"), "must be datetime"),
    (lambda t: t["exam_logical_seats"][0].update(physical_verified=True), "claims physical verification"),
    (lambda t: t["exam_sessions"].append(dict(t["exam_sessions"][0])), "duplicate key"),
    (lambda t: t["exam_room_allocations"].pop(0), "has no room allocation"),
    (lambda t: t["exam_periods"].clear(), "no period"),
    (lambda t: t["venues"][0].update(floor="1"), "venues\\[H1\\].floor must be int"),
])
async def test_invalid_references_and_malformed_exam_records(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_timetable(await records(change), period_code=PERIOD, demo_reference=DEMO_REF)


def move_session(index, start, end):
    def change(t):
        t["exam_sessions"][index].update(start_at=start, end_at=end)
    return change


@pytest.mark.parametrize("change,match", [
    # X2 (H1 + L1, IT1020) moved onto X1's time in H1.
    (move_session(1, dt.datetime(2026, 11, 16, 9), dt.datetime(2026, 11, 16, 11)),
     "Venue H1 holds overlapping sessions X1-IT1010 and X2-IT1020"),
    # X3 (IT1030: the first three IT1010 students) moved onto X1's time in another venue.
    (move_session(2, dt.datetime(2026, 11, 16, 9), dt.datetime(2026, 11, 16, 10)),
     "has overlapping exams X1-IT1010 and X3-IT1030"),
    (move_session(2, dt.datetime(2026, 11, 30, 9), dt.datetime(2026, 11, 30, 11)), "outside the period"),
    (move_session(2, dt.datetime(2026, 11, 17, 7), dt.datetime(2026, 11, 17, 9)), "outside the exam day 08:00-18:00"),
    (move_session(2, dt.datetime(2026, 11, 17, 17), dt.datetime(2026, 11, 18, 9)), "crosses midnight"),
])
async def test_basic_timetable_integrity_problems_are_detected(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_timetable(await records(change), period_code=PERIOD, demo_reference=DEMO_REF)


async def test_touching_sessions_in_one_room_are_allowed():
    # X2 starts exactly when X1 ends in H1; a student sitting both would also be fine.
    snap = await records(move_session(1, dt.datetime(2026, 11, 16, 10, 30), dt.datetime(2026, 11, 16, 12, 30)))
    assert len(map_timetable(snap, period_code=PERIOD, demo_reference=DEMO_REF).draft.sessions) == 3


async def test_duplicate_student_assignment_in_one_session_is_detected():
    def twice(t):
        t["exam_logical_seats"].append(dict(t["exam_logical_seats"][4], venue_id="L1", logical_seat_number=3))
        t["exam_room_allocations"][2].update(candidate_count=3, allocated_count=3)
    with pytest.raises(DemoDataError, match="seated 2 times|two seats in session"):
        map_timetable(await records(twice), period_code=PERIOD, demo_reference=DEMO_REF)


async def test_integrity_note_does_not_claim_member2_validation():
    notes = map_timetable(await records(), period_code=PERIOD, demo_reference=DEMO_REF).notes
    assert any("NOT Member 2's full H1-H11 validation" in n for n in notes)


async def test_all_problems_are_reported_together():
    def several(t):
        t["exam_room_allocations"][0]["venue_id"] = "Z9"
        t["exam_logical_seats"][1]["student_id"] = "IT99999999"
    with pytest.raises(DemoDataError) as excinfo:
        map_timetable(await records(several), period_code=PERIOD, demo_reference=DEMO_REF)
    assert len(excinfo.value.problems) >= 2


# --- Halls -----------------------------------------------------------------------------------------

async def test_layouts_become_synthetic_halls_never_verified():
    halls, notes = map_halls(await records())
    assert {h.venue_id: (len(h.seats), h.status) for h in halls} == {"H1": (8, LayoutStatus.SYNTHETIC),
                                                                    "L1": (10, LayoutStatus.SYNTHETIC)}
    assert halls[0].seats[4].label == "R02-S01" and "PROVISIONAL" in notes[0]


@pytest.mark.parametrize("change,match", [
    (lambda t: t["physical_layouts"][0].update(layout_status="VERIFIED"), "claims verification"),
    (lambda t: t["physical_layouts"][0].update(verification_status="PHYSICALLY_VERIFIED"), "claims verification"),
    (lambda t: t["physical_layouts"][0].update(total_seat_positions=9), "says 9 seats, found 8"),
    (lambda t: t["physical_layouts"][0].update(venue_id="Z9"), "unknown venue"),
    (lambda t: t["physical_layouts"].append(dict(t["physical_layouts"][0], layout_id="layout-H1-v2")), "choose one version"),
    (lambda t: t["physical_seats"][1].update(seat_label="R01-S01"), "labels must be unique"),
])
async def test_invalid_layouts(change, match):
    with pytest.raises(DemoDataError, match=match):
        map_halls(await records(change))
