"""Mocked m3_demo row sources for Member 3 tests. SYNTHETIC data only; no database.

``compact_tables()`` is a small, internally consistent m3_demo snapshot with the value
types PostgreSQL returns. ``SeedPackSource`` optionally reads the full seed pack CSVs
(typed per manifest.json) when the pack folder exists; tests using it are skipped otherwise.
"""

from __future__ import annotations

import copy
import csv
import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

from app.duty_allocation.demo_database import BEGIN_SNAPSHOT, TABLE_COLUMNS

INST = "11111111-1111-4111-8111-111111111111"
PERIOD = "PP1-2026-S2-FINAL"
DEMO_REF = "DEMO-UNPUBLISHED:compact-test-fixture"
SEED_PACK = Path(os.environ.get("M3_SEED_PACK_DIR", r"C:\Users\Hp\Desktop\Schedence-M3-Seed"))
RANKS = ["Professor", "Associate Professor", "Senior Lecturer I", "Senior Lecturer II", "Lecturer",
         "Assistant Lecturer", "Instructor"]


def sid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 11, 16 + day, hour, minute)


def compact_tables() -> dict[str, list[dict]]:
    """3 sessions over 2 days, 2 venues, 9 students, 8 staff (1 inactive)."""
    t: dict[str, list[dict]] = {name: [] for name in TABLE_COLUMNS}
    t["institutions"] = [dict(institution_id=INST, institution_code="SYN", institution_name="Synthetic Institute",
                              dataset_status="SYNTHETIC_TEST_DATA")]
    t["exam_periods"] = [dict(period_code=PERIOD, period_name="Synthetic period", academic_year_label="2026/27",
                              semester=2, start_date=dt.date(2026, 11, 16), end_date=dt.date(2026, 11, 27),
                              day_start_time=dt.time(8, 0), day_end_time=dt.time(18, 0), include_saturdays=False,
                              include_sundays=False)]
    for code in ("IT1010", "IT1020", "IT1030"):
        t["modules"].append(dict(module_code=code, module_name=f"Module {code}", academic_year=1, semester=2, credits=4,
                                 difficulty=3, exam_type="written", duration_minutes=120, reading_time_minutes=0,
                                 has_extra_reading_time=False))
    students = [f"IT2600000{n}" for n in range(1, 10)]
    t["students"] = [dict(student_id=s, academic_year=1, specialization="IT") for s in students]
    groups = {"IT1010": students[:4], "IT1020": students[4:8], "IT1030": students[:3]}
    t["registrations"] = [dict(student_id=s, module_code=m, registration_type="PROPER")
                          for m, ss in groups.items() for s in ss]
    t["venues"] = [dict(venue_id="H1", venue_name="H1 Hall", venue_type="lecture_hall", building="A", floor=1,
                        normal_capacity=8, exam_capacity=4),
                   dict(venue_id="L1", venue_name="L1 Lab", venue_type="computer_lab", building="B", floor=2,
                        normal_capacity=10, exam_capacity=5)]
    t["capacity_policies"] = [
        dict(venue_id="H1", venue_type="lecture_hall", normal_capacity=8, exam_percentage=50, effective_exam_capacity=4,
             policy_source="PROJECT_EXAMPLE_NOT_INSTITUTIONAL", institution_verified=False, dataset_status="SYNTHETIC_TEST_DATA"),
        dict(venue_id="L1", venue_type="computer_lab", normal_capacity=10, exam_percentage=90, effective_exam_capacity=9,
             policy_source="PROJECT_EXAMPLE_NOT_INSTITUTIONAL", institution_verified=False, dataset_status="SYNTHETIC_TEST_DATA")]
    for venue, rows, cols in (("H1", 2, 4), ("L1", 2, 5)):
        t["physical_layouts"].append(dict(venue_id=venue, layout_id=f"layout-{venue}-v1", layout_status="SYNTHETIC",
                                          verification_status="NOT_PHYSICALLY_VERIFIED", total_seat_positions=rows * cols,
                                          version=1, source="SYNTHETIC_TEST_DATA"))
        for n in range(1, rows * cols + 1):
            r, c = (n - 1) // cols + 1, (n - 1) % cols + 1
            t["physical_seats"].append(dict(layout_id=f"layout-{venue}-v1", venue_id=venue, seat_number=n,
                                            seat_label=f"R{r:02}-S{c:02}", row_number=r, column_number=c,
                                            blocked=False, verification_status="SYNTHETIC_NOT_VERIFIED"))
    sessions = [("X1-IT1010", "IT1010", at(0, 8, 30), at(0, 10, 30), {"H1": groups["IT1010"]}),
                ("X2-IT1020", "IT1020", at(0, 11, 30), at(0, 13, 30), {"H1": groups["IT1020"][:2], "L1": groups["IT1020"][2:]}),
                ("X3-IT1030", "IT1030", at(1, 8, 30), at(1, 10, 30), {"L1": groups["IT1030"]})]
    for session_id, module, start, end, rooms in sessions:
        t["exam_sessions"].append(dict(session_id=session_id, module_code=module, period_code=PERIOD, start_at=start,
                                       end_at=end, timetable_status="SYNTHETIC_DRAFT", publication_status="NOT_PUBLISHED",
                                       source="SYNTHETIC_TEST_DATA"))
        for venue, ss in rooms.items():
            capacity = "4" if venue == "H1" else "9"
            t["exam_room_allocations"].append(dict(session_id=session_id, venue_id=venue, module_code=module,
                                                   candidate_count=len(ss), effective_capacity=capacity,
                                                   allocated_count=len(ss), capacity_source="PROJECT_CAPACITY_RULE",
                                                   allocation_status="IN_MEMORY_SYNTHETIC"))
            for n, s in enumerate(ss, 1):
                t["exam_logical_seats"].append(dict(session_id=session_id, venue_id=venue, student_id=s, module_code=module,
                                                    logical_seat_number=n, provisional_label=f"P-{n:03}",
                                                    seat_status="PROVISIONAL", physical_verified=False))
                t["seat_assignments"].append(dict(plan_id="PLAN-1", session_id=session_id, venue_id=venue, student_id=s,
                                                  logical_seat_number=n, provisional_physical_label=f"R01-S{n:02}",
                                                  layout_id=f"layout-{venue}-v1", verification_status="PROVISIONAL",
                                                  physically_verified=False, source="SYNTHETIC_TEST_DATA"))
            t["duty_requirements"].append(dict(requirement_id=f"RQ-{session_id}-{venue}", session_id=session_id,
                                               venue_id=venue, student_count=len(ss), supervisors_required=1,
                                               invigilators_required=1, staffing_rule_id="BAND-01",
                                               rule_source="SYNTHETIC_EXTENSION", institution_verified=False))
    t["seating_plans"] = [dict(plan_id="PLAN-1", period_code=PERIOD, timetable_reference="IN_MEMORY_SYNTHETIC",
                               plan_state="DRAFT", physical_verification="PROVISIONAL", created_from="SYNTHETIC_TEST_DATA",
                               approved=False)]
    staff_ranks = ["Professor", "Professor", "Associate Professor", "Senior Lecturer II", "Lecturer", "Lecturer",
                   "Assistant Lecturer", "Instructor"]
    for n, rank in enumerate(staff_ranks, 1):
        t["staff"].append(dict(id=sid(n), institution_id=INST, staff_number=f"EMP{n:04}", full_name=f"Synthetic Staff {n}",
                               email=None, academic_rank=rank, employment_status="INACTIVE" if n == 8 else "ACTIVE",
                               identity_user_id=None, created_at=dt.datetime(2026, 10, 20)))
        status = {5: "NOT_COMPUTED", 6: "MISSING_RESPONSE"}.get(n, "COMPUTED")
        t["staff_admin_workload"].append(dict(staff_id=sid(n), institution_id=INST, as_of=dt.date(2026, 11, 16),
                                              aggregate_status=status,
                                              total_admin_hours_per_week=Decimal(2 * n) if status == "COMPUTED" else None,
                                              combination_rule="SUM" if status == "COMPUTED" else None,
                                              available_via_api=status == "COMPUTED", notes=f"SIMULATED_{status}",
                                              dataset_status="SYNTHETIC_TEST_DATA"))
        t["staff_duty_limits"].append(dict(staff_id=sid(n), max_duties_per_day=2, max_duties_per_period=7,
                                           minimum_required_rest_minutes=0, source="SYNTHETIC_OVERRIDES_NONE"))
        t["staff_duty_history"].append(dict(staff_id=sid(n), previous_duties=n % 3, previous_minutes=120 * (n % 3),
                                            history_source="SYNTHETIC_BEFORE_PERIOD", previous_period="2026-S1",
                                            data_status="SYNTHETIC"))
    t["staff_availability"] = [dict(availability_id="AV1", staff_id=sid(1), start_at=at(0, 8), end_at=at(11, 17, 30),
                                     source="SYNTHETIC_REPORTED_WINDOW")]
    t["staff_unavailability"] = [dict(unavailability_id="U1", staff_id=sid(2), start_at=at(0, 7), end_at=at(0, 18),
                                      reason="SYNTHETIC leave", source="SYNTHETIC_STAFF_INPUT")]
    t["staff_day_preferences"] = [dict(preference_id="PR1", staff_id=sid(3), date=dt.date(2026, 11, 17),
                                       preference_kind="AVOID", source="SYNTHETIC_REQUEST"),
                                  dict(preference_id="PR2", staff_id=sid(4), date=dt.date(2026, 11, 16),
                                       preference_kind="PREFER", source="SYNTHETIC_REQUEST")]
    t["staff_restrictions"] = [dict(restriction_id="REST-IT1010-LIC", staff_id=sid(1), module_code="IT1010",
                                    restriction_type="MODULE_LECTURER", effect="EXCLUDE_OWN_MODULE",
                                    source="SYNTHETIC_CONFLICT")]
    t["eligibility_rank_order"] = [dict(priority_order=i, academic_rank=r, supervisor_eligible=i <= 4,
                                        invigilator_eligible=True, policy_source="SYNTHETIC_RANK_POLICY")
                                   for i, r in enumerate(RANKS, 1)]
    t["eligibility_role_rules"] = [
        dict(duty_role="SUPERVISOR", minimum_academic_rank="Senior Lecturer II", allow_unranked=False,
             minimum_gap_minutes=60, policy_source="SYNTHETIC_RULE"),
        dict(duty_role="INVIGILATOR", minimum_academic_rank=None, allow_unranked=True, minimum_gap_minutes=60,
             policy_source="SYNTHETIC_RULE")]
    t["duty_limit_policy"] = [dict(policy_id="default", maximum_duties_per_day=2, maximum_duties_per_period=7,
                                   minimum_gap_minutes=60, source="SYNTHETIC_LIMIT", notes="demo")]
    t["staffing_rules"] = [dict(rule_id=i, min_students=lo, max_students=hi, required_supervisors=s,
                                required_invigilators=s, source="SYNTHETIC_EXTENSION", institution_verified=False)
                           for i, lo, hi, s in (("BAND-01", 1, 59, 1), ("EXAMPLE-60", 60, 60, 1), ("BAND-61", 61, 149, 2),
                                                ("EXAMPLE-150", 150, 150, 2), ("BAND-151", 151, 500, 3))]
    t["fairness_policy"] = [dict(policy_id="M3-FAIR-01", load_unit="DUTIES", supervisor_weight=100, invigilator_weight=100,
                                 history_weight=100, admin_load_per_weekly_hour=Decimal(10), missing_admin_hours="ZERO",
                                 back_to_back_minutes=60, weight_balance=Decimal(1), weight_peak=Decimal(1),
                                 weight_back_to_back=Decimal(1), weight_preference=Decimal(1), hard_avoid_days=False,
                                 source="SYNTHETIC", institution_verified=False)]
    t["duty_roster_example"] = []
    return t


class FakeRowSource:
    """In-memory RowSource. Records every call so tests can prove nothing but reads happened."""

    def __init__(self, tables, *, columns=None, fail_on=None):
        self.tables = tables
        self.columns = columns
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def begin_read_only(self):
        self.calls.append(BEGIN_SNAPSHOT)

    async def schema_columns(self):
        self.calls.append("schema_columns")
        if self.columns is not None:
            return self.columns
        return {name: set(TABLE_COLUMNS[name]) for name in self.tables if name in TABLE_COLUMNS}

    async def fetch(self, table):
        self.calls.append(f"fetch {table}")
        if self.fail_on == table:
            from app.duty_allocation.demo_database import DemoDatabaseUnavailable
            raise DemoDatabaseUnavailable("m3_demo read failed: connection timed out")
        return [{c: row.get(c) for c in TABLE_COLUMNS[table]} for row in self.tables[table]]


def compact_source(change=None, **kwargs) -> FakeRowSource:
    tables = copy.deepcopy(compact_tables())
    if change is not None:
        change(tables)
    return FakeRowSource(tables, **kwargs)


def _convert(value, kind):
    if value == "":
        return None
    return {"INTEGER": int, "NUMERIC": Decimal, "BOOLEAN": lambda v: v.lower() in ("true", "1"),
            "DATE": dt.date.fromisoformat, "TIME": dt.time.fromisoformat,
            "TIMESTAMP": dt.datetime.fromisoformat}.get(kind, str)(value)


def seed_pack_source() -> tuple[FakeRowSource, dict] | None:
    """The full seed pack as a typed mock source, or None if the pack is not present."""
    manifest_path = SEED_PACK / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables = {}
    for name, info in manifest["sheets"].items():
        if not info["seed"]:
            continue
        with (SEED_PACK / "csv" / f"{name.lower()}.csv").open(encoding="utf-8-sig", newline="") as f:
            tables[name.lower()] = [{c: _convert(r[c], info["column_types"][c]) for c in info["headers"]}
                                    for r in csv.DictReader(f)]
    return FakeRowSource(tables), manifest
