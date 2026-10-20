"""Map SYNTHETIC m3_demo records into existing Member 3 (and read-only Member 2) models.

Every function checks types, references (m3_demo has primary keys but no foreign keys),
duplicates and value consistency, and raises DemoDataError listing every problem rather
than guessing. Nothing here is SLIIT data or policy.

Missing Member 2 contract field: DraftTimetable.input_fingerprint is mandatory, but
m3_demo holds no published timetable and no source fingerprint. map_timetable() requires
an explicit caller-supplied reference starting with ``DEMO-UNPUBLISHED:`` so the value can
never be mistaken for a real publication or fingerprint (demo-only; awaiting approval).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from app.duty_allocation.academic_adapter import AcademicStaffData
from app.duty_allocation.academic_client import MissingWorkload
from app.duty_allocation.allocation_policy import DutyLimitPolicy
from app.duty_allocation.demo_database import M3DemoRecords
from app.duty_allocation.eligibility_policy import EligibilityPolicy, RoleRule, StaffRestriction, normalize_rank
from app.duty_allocation.fairness_policy import (
    AdminWorkload, DayPreference, DayPreferenceKind, DutyFairnessPolicy, DutyHistory, FairnessWeights, LoadUnit,
    MissingAdminHours,
)
from app.duty_allocation.orchestration import SupplementalStaffData
from app.duty_allocation.staff_source import import_staff
from app.duty_allocation.staffing_policy import StaffingPolicy, StaffingRule
from app.duty_allocation.student_seating import CapacityRules, Hall, LayoutStatus, Seat
from app.duty_allocation.types import DutyRole, StaffAvailability, StaffUnavailability
from app.scheduling.contracts import SchedulingValidationError
from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation
from app.sources.types import Venue

DEMO_REFERENCE_PREFIX = "DEMO-UNPUBLISHED:"
SYNTHETIC_LABEL = "m3_demo SYNTHETIC (not SLIIT policy)"


class DemoDataError(ValueError):
    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems[:20]) + (" ..." if len(self.problems) > 20 else ""))


class _Problems(list):
    def check(self):
        if self:
            raise DemoDataError(self)


def _typed(row, column, kind, problems, where, *, optional=False):
    value = row.get(column)
    if value is None or value == "":
        if not optional:
            problems.append(f"{where}.{column} is missing.")
        return None
    ok = {"int": type(value) is int, "bool": type(value) is bool, "str": type(value) is str,
          "num": type(value) in (int, float, Decimal), "date": type(value) is dt.date,
          "datetime": type(value) is dt.datetime and value.tzinfo is None}[kind]
    if not ok:
        problems.append(f"{where}.{column} must be {kind}, got {type(value).__name__}.")
        return None
    return value


def _whole(value, label, problems):
    if value is None:
        return None
    if Decimal(str(value)) != Decimal(int(value)):
        problems.append(f"{label} must be a whole number for the existing Member 3 policy, got {value}.")
        return None
    return int(value)


def _unique(rows, key, table, problems):
    seen = Counter(tuple(r.get(k) for k in key) for r in rows)
    problems += [f"m3_demo.{table} has duplicate key {k}." for k, n in seen.items() if n > 1]


def _known_staff_id(value, where, staff_ids, problems):
    if type(value) is not str or value.lower() not in staff_ids:
        problems.append(f"{where} refers to unknown staff {value!r}.")
        return None
    return value.lower()


# --- Institution ------------------------------------------------------------------------------

def check_institution(records: M3DemoRecords, institution_id: uuid.UUID) -> None:
    """The demo institution must be named explicitly and be marked synthetic. No translation
    to, or merging with, the application's real institution is ever done."""
    problems = _Problems()
    rows = records.rows("institutions")
    match = [r for r in rows if r.get("institution_id") == str(institution_id)]
    if len(match) != 1:
        problems.append(f"m3_demo.institutions has no single row for {institution_id}.")
    elif match[0].get("dataset_status") != "SYNTHETIC_TEST_DATA":
        problems.append("m3_demo institution is not marked SYNTHETIC_TEST_DATA; refusing to treat it as demo data.")
    problems.check()


# --- Timetable ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoTimetable:
    draft: DraftTimetable
    venues: tuple[Venue, ...]
    publication_statuses: tuple[str, ...]
    notes: tuple[str, ...]


def map_timetable(records: M3DemoRecords, *, period_code: str, demo_reference: str) -> DemoTimetable:
    problems = _Problems()
    if type(demo_reference) is not str or not demo_reference.startswith(DEMO_REFERENCE_PREFIX) \
            or len(demo_reference) <= len(DEMO_REFERENCE_PREFIX):
        raise DemoDataError([f"demo_reference must be an explicit value starting with {DEMO_REFERENCE_PREFIX!r}; "
                             "m3_demo has no published timetable or source fingerprint."])
    period = next((r for r in records.rows("exam_periods") if r.get("period_code") == period_code), None)
    if period is None:
        problems.append(f"m3_demo.exam_periods has no period {period_code!r}.")
    modules = {r["module_code"] for r in records.rows("modules")}
    students = {r["student_id"] for r in records.rows("students")}
    registered = {(r["student_id"], r["module_code"]) for r in records.rows("registrations")}

    venues = []
    for r in records.rows("venues"):
        where = f"venues[{r.get('venue_id')}]"
        values = [_typed(r, c, k, problems, where) for c, k in (("venue_id", "str"), ("venue_name", "str"),
                  ("venue_type", "str"), ("building", "str"), ("floor", "int"), ("normal_capacity", "int"),
                  ("exam_capacity", "int"))]
        if None not in values:
            venues.append(Venue(*values))
    _unique(records.rows("venues"), ("venue_id",), "venues", problems)
    venue_ids = {v.venue_id for v in venues}

    sessions = [r for r in records.rows("exam_sessions") if r.get("period_code") == period_code]
    _unique(sessions, ("session_id",), "exam_sessions", problems)
    allocations = defaultdict(list)
    for r in records.rows("exam_room_allocations"):
        allocations[r.get("session_id")].append(r)
    seats = defaultdict(list)
    for r in records.rows("exam_logical_seats"):
        seats[(r.get("session_id"), r.get("venue_id"))].append(r)
    every_session = {s.get("session_id") for s in records.rows("exam_sessions")}
    allocated_rooms = {(a.get("session_id"), a.get("venue_id")) for a in records.rows("exam_room_allocations")}
    for session_id, venue_id in seats:
        if session_id not in every_session:
            problems.append(f"exam_logical_seats refer to unknown session {session_id!r}.")
        elif (session_id, venue_id) not in allocated_rooms:
            problems.append(f"exam_logical_seats place students in {session_id}/{venue_id}, which has no room allocation.")
    for session_id, venue_id in allocated_rooms:
        if session_id not in every_session:
            problems.append(f"exam_room_allocations refer to unknown session {session_id!r}.")
    seated: Counter = Counter()
    built, statuses = [], set()
    for s in sorted(sessions, key=lambda r: (str(r.get("start_at")), str(r.get("session_id")))):
        sid = s.get("session_id")
        where = f"exam_sessions[{sid}]"
        module = _typed(s, "module_code", "str", problems, where)
        start = _typed(s, "start_at", "datetime", problems, where)
        end = _typed(s, "end_at", "datetime", problems, where)
        statuses.add(f"{s.get('timetable_status')}/{s.get('publication_status')}")
        if module and module not in modules:
            problems.append(f"{where} refers to unknown module {module!r}.")
        if start and end and start >= end:
            problems.append(f"{where} ends before it starts.")
        rooms = []
        if not allocations.get(sid):
            problems.append(f"{where} has no room allocation.")
        _unique(allocations.get(sid, []), ("session_id", "venue_id"), "exam_room_allocations", problems)
        for a in sorted(allocations.get(sid, []), key=lambda r: str(r.get("venue_id"))):
            venue = a.get("venue_id")
            awhere = f"exam_room_allocations[{sid}/{venue}]"
            if venue not in venue_ids:
                problems.append(f"{awhere} refers to unknown venue {venue!r}.")
            if a.get("module_code") != module:
                problems.append(f"{awhere} module differs from its session.")
            room_seats = seats.get((sid, venue), [])
            allocated = _typed(a, "allocated_count", "int", problems, awhere)
            candidates = _typed(a, "candidate_count", "int", problems, awhere)
            capacity_text = _typed(a, "effective_capacity", "str", problems, awhere)
            if allocated is not None and allocated != len(room_seats):
                problems.append(f"{awhere} allocated_count {allocated} but {len(room_seats)} logical seats.")
            if candidates is not None and allocated is not None and candidates != allocated:
                problems.append(f"{awhere} candidate_count {candidates} differs from allocated_count {allocated}.")
            if capacity_text is not None:
                if not capacity_text.isdigit():
                    problems.append(f"{awhere}.effective_capacity {capacity_text!r} is not a whole number.")
                elif len(room_seats) > int(capacity_text):
                    problems.append(f"{awhere} seats {len(room_seats)} students over capacity {capacity_text}.")
            numbers = [x.get("logical_seat_number") for x in room_seats]
            if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
                problems.append(f"{awhere} logical seat numbers must be unique positive ints.")
            allocation_seats = []
            for x in sorted(room_seats, key=lambda x: (x.get("logical_seat_number") or 0)):
                student = x.get("student_id")
                if student not in students:
                    problems.append(f"{awhere} seats unknown student {student!r}.")
                elif (student, module) not in registered:
                    problems.append(f"{awhere} seats {student} who is not registered for {module}.")
                if x.get("module_code") != module:
                    problems.append(f"{awhere} seat for {student} has a different module.")
                if x.get("physical_verified") is not False:
                    problems.append(f"{awhere} seat for {student} claims physical verification; demo seats must be unverified.")
                seated[(student, module)] += 1
                if type(student) is str and type(x.get("logical_seat_number")) is int:
                    allocation_seats.append(SeatAllocation(student, x["logical_seat_number"]))
            if allocation_seats:
                rooms.append(RoomAllocation(venue, tuple(allocation_seats)))
        if module and start and end and rooms:
            try:
                built.append(ExamSession(sid, module, period_code, start, end, tuple(rooms)))
            except SchedulingValidationError as exc:
                problems += [f"{where}: {v.message}" for v in exc.violations]
    scheduled = {s.get("module_code") for s in sessions}
    for (student, module), count in seated.items():
        if count > 1:
            problems.append(f"Student {student} is seated {count} times for {module}.")
    for student, module in sorted(registered):
        if module in scheduled and seated[(student, module)] == 0:
            problems.append(f"Registered student {student} has no seat for {module}.")
    if not built and not problems:
        problems.append(f"No exam sessions for period {period_code!r}.")
    problems += _integrity_problems(built, period)
    problems.check()
    draft = DraftTimetable(period_code, demo_reference, tuple(built))
    notes = (f"Timetable from {SYNTHETIC_LABEL}; reference {demo_reference!r} is a demo label, "
             "not a published timetable or source fingerprint.",
             "Basic timetable integrity checked (session intervals within the period, no room or student "
             "overlaps, no duplicate seats). This is NOT Member 2's full H1-H11 validation, which needs "
             "Member 2's original input snapshot.")
    return DemoTimetable(draft, tuple(venues), tuple(sorted(statuses)), notes)


def _integrity_problems(sessions: list[ExamSession], period) -> list[str]:
    """Basic, Member-3-side checks only; Member 2's scheduler is not re-run or changed."""
    problems = []
    if period is not None:
        first, last = period.get("start_date"), period.get("end_date")
        day_start, day_end = period.get("day_start_time"), period.get("day_end_time")
        for s in sessions:
            if s.start.date() != s.end.date():
                problems.append(f"Session {s.session_id} crosses midnight.")
            if type(first) is dt.date and type(last) is dt.date and not first <= s.start.date() <= last:
                problems.append(f"Session {s.session_id} on {s.start.date()} is outside the period {first}..{last}.")
            if type(day_start) is dt.time and type(day_end) is dt.time and \
                    not (day_start <= s.start.time() and s.end.time() <= day_end):
                problems.append(f"Session {s.session_id} {s.start:%H:%M}-{s.end:%H:%M} is outside the exam day "
                                f"{day_start:%H:%M}-{day_end:%H:%M}.")
    overlap = lambda a, b: a.start < b.end and b.start < a.end  # touching is allowed
    by_venue, by_student = defaultdict(list), defaultdict(list)
    for s in sessions:
        for room in s.rooms:
            by_venue[room.venue_id].append(s)
            for seat in room.seats:
                by_student[seat.student_id].append(s)
    for venue, used in sorted(by_venue.items()):
        used = sorted(set(used), key=lambda s: (s.start, s.session_id))
        for i, a in enumerate(used):
            for b in used[i + 1:]:
                if overlap(a, b):
                    problems.append(f"Venue {venue} holds overlapping sessions {a.session_id} and {b.session_id}.")
    for student, taken in sorted(by_student.items()):
        taken = sorted(taken, key=lambda s: (s.start, s.session_id))
        for i, a in enumerate(taken):
            for b in taken[i + 1:]:
                if a.session_id == b.session_id:
                    problems.append(f"Student {student} has two seats in session {a.session_id}.")
                elif overlap(a, b):
                    problems.append(f"Student {student} has overlapping exams {a.session_id} and {b.session_id}.")
    return problems


# --- Policies -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class DemoPolicies:
    staffing: StaffingPolicy
    eligibility: EligibilityPolicy
    limits: DutyLimitPolicy
    fairness: DutyFairnessPolicy
    notes: tuple[str, ...]


def map_policies(records: M3DemoRecords) -> DemoPolicies:
    problems, notes = _Problems(), []
    ranks = sorted(records.rows("eligibility_rank_order"), key=lambda r: (r.get("priority_order") or 0))
    _unique(ranks, ("priority_order",), "eligibility_rank_order", problems)
    rank_names = [r.get("academic_rank") for r in ranks]
    roles = {r.get("duty_role"): r for r in records.rows("eligibility_role_rules")}
    if set(roles) != {"SUPERVISOR", "INVIGILATOR"} or len(records.rows("eligibility_role_rules")) != 2:
        problems.append("eligibility_role_rules must have exactly one SUPERVISOR and one INVIGILATOR row.")
    limit_rows = records.rows("duty_limit_policy")
    if len(limit_rows) != 1:
        problems.append("duty_limit_policy must have exactly one row.")
    gaps = {r.get("minimum_gap_minutes") for r in roles.values()} | {r.get("minimum_gap_minutes") for r in limit_rows}
    if len(gaps) != 1 or type(next(iter(gaps), None)) is not int:
        problems.append(f"minimum_gap_minutes differs between role rules and duty_limit_policy ({sorted(map(str, gaps))}); "
                        "the existing model has one minimum break.")
    problems.check()

    gap = next(iter(gaps))
    try:
        eligibility = EligibilityPolicy(
            tuple(rank_names),
            tuple(RoleRule(DutyRole(code), roles[code].get("minimum_academic_rank") or None, roles[code].get("allow_unranked"))
                  for code in ("SUPERVISOR", "INVIGILATOR")),
            gap, f"{SYNTHETIC_LABEL}: eligibility_rank_order + eligibility_role_rules")
    except SchedulingValidationError as exc:
        raise DemoDataError([v.message for v in exc.violations]) from None
    for r in ranks:  # the per-rank flags must agree with the minimum-rank rules
        for role, flag in ((DutyRole.SUPERVISOR, "supervisor_eligible"), (DutyRole.INVIGILATOR, "invigilator_eligible")):
            derived = eligibility.rank_decision(r.get("academic_rank"), role) is None
            if r.get(flag) is not derived:
                problems.append(f"eligibility_rank_order[{r.get('academic_rank')}].{flag}={r.get(flag)} contradicts "
                                f"the {role.value} minimum rank rule.")

    lim = limit_rows[0]
    try:
        limits = DutyLimitPolicy(lim.get("maximum_duties_per_day"), lim.get("maximum_duties_per_period"),
                                 f"{SYNTHETIC_LABEL}: duty_limit_policy {lim.get('policy_id')}")
    except SchedulingValidationError as exc:
        problems += [v.message for v in exc.violations]
        limits = None
    for r in records.rows("staff_duty_limits"):  # the existing model has no per-staff limits
        if (r.get("max_duties_per_day"), r.get("max_duties_per_period")) != (lim.get("maximum_duties_per_day"),
                                                                             lim.get("maximum_duties_per_period")):
            problems.append(f"staff_duty_limits[{r.get('staff_id')}] differs from the default; per-staff limits are "
                            "not supported by the existing Member 3 model.")
        rest = r.get("minimum_required_rest_minutes")
        if rest not in (0, None, gap):
            problems.append(f"staff_duty_limits[{r.get('staff_id')}] rest {rest} min differs from the {gap}-min minimum break.")
    if records.rows("staff_duty_limits"):
        notes.append("staff_duty_limits match the default limits; rest value 0 is read as 'no per-staff override' "
                     f"(the {gap}-minute minimum break applies to everyone).")

    rules = []
    for r in records.rows("staffing_rules"):
        try:
            rules.append(StaffingRule(r.get("min_students"), r.get("max_students"), r.get("required_supervisors"),
                                      r.get("required_invigilators")))
        except SchedulingValidationError as exc:
            problems += [f"staffing_rules[{r.get('rule_id')}]: {v.message}" for v in exc.violations]
    if any(r.get("institution_verified") is not False for r in records.rows("staffing_rules")):
        problems.append("staffing_rules claims institution verification; demo rules must be unverified.")
    staffing = None
    if rules:
        try:
            staffing = StaffingPolicy(tuple(rules), f"{SYNTHETIC_LABEL}: staffing_rules "
                                      + ", ".join(sorted(r.get("rule_id") for r in records.rows("staffing_rules"))))
        except SchedulingValidationError as exc:
            problems += [v.message for v in exc.violations]
    else:
        problems.append("staffing_rules is empty.")

    fair_rows = records.rows("fairness_policy")
    fairness = None
    if len(fair_rows) != 1:
        problems.append("fairness_policy must have exactly one row.")
    else:
        f = fair_rows[0]
        weights = [_whole(f.get(c), f"fairness_policy.{c}", problems)
                   for c in ("weight_balance", "weight_peak", "weight_back_to_back", "weight_preference")]
        admin = _whole(f.get("admin_load_per_weekly_hour"), "fairness_policy.admin_load_per_weekly_hour", problems)
        if f.get("institution_verified") is not False:
            problems.append("fairness_policy claims institution verification; demo policy must be unverified.")
        try:
            fairness = DutyFairnessPolicy(
                f"{SYNTHETIC_LABEL}: fairness_policy {f.get('policy_id')}", LoadUnit(f.get("load_unit")),
                f.get("supervisor_weight"), f.get("invigilator_weight"), f.get("history_weight"), admin,
                MissingAdminHours(f.get("missing_admin_hours")), f.get("back_to_back_minutes"),
                FairnessWeights(*weights), f.get("hard_avoid_days"))
        except (SchedulingValidationError, ValueError, TypeError) as exc:
            problems.append(f"fairness_policy is invalid: {getattr(exc, 'violations', None) and exc.violations[0].message or exc}")
    problems.check()
    return DemoPolicies(staffing, eligibility, limits, fairness, tuple(notes))


def map_capacity_rules(records: M3DemoRecords, rules: CapacityRules) -> tuple[str, ...]:
    """Independent capacity cross-check; nothing is reconfigured and nothing is trusted alone.

    For every venue: exactly one capacity_policies row with the same venue type and full
    capacity; its percentage and effective capacity must equal the Step 6 project rules
    (floor rounding; unconfigured types stay unconfigured). For every room allocation: its
    text effective_capacity must be a whole number equal to that policy capacity, the venue
    must have a configured capacity, and allocated students must fit. venues.exam_capacity
    is a legacy Member 2 field and is not used."""
    problems = _Problems()
    venues = {r.get("venue_id"): r for r in records.rows("venues")}
    policies = defaultdict(list)
    for r in records.rows("capacity_policies"):
        policies[r.get("venue_id")].append(r)
    for venue_id in sorted(set(policies) - set(venues), key=str):
        problems.append(f"capacity_policies[{venue_id}] refers to an unknown venue.")
    capacity = {}
    for venue_id, venue in sorted(venues.items(), key=lambda kv: str(kv[0])):
        rows = policies.get(venue_id, [])
        if len(rows) != 1:
            problems.append(f"Venue {venue_id} has {len(rows)} capacity_policies rows; exactly one is required.")
            continue
        r = rows[0]
        where = f"capacity_policies[{venue_id}]"
        if (r.get("venue_type"), r.get("normal_capacity")) != (venue.get("venue_type"), venue.get("normal_capacity")):
            problems.append(f"{where} type/capacity ({r.get('venue_type')}, {r.get('normal_capacity')}) differs from "
                            f"venues ({venue.get('venue_type')}, {venue.get('normal_capacity')}).")
            continue
        if type(venue.get("normal_capacity")) is not int:
            problems.append(f"venues[{venue_id}].normal_capacity must be int.")
            continue
        expected = rules.exam_capacity(venue.get("venue_type"), venue["normal_capacity"])
        percentage = rules.percentage(venue.get("venue_type"))
        if (r.get("exam_percentage"), r.get("effective_exam_capacity")) != (percentage, expected):
            problems.append(f"{where} ({venue.get('venue_type')}) gives {r.get('exam_percentage')}% -> "
                            f"{r.get('effective_exam_capacity')}; the project rules give {percentage}% -> {expected}.")
            continue
        if r.get("institution_verified") is not False:
            problems.append(f"{where} claims institution verification.")
        capacity[venue_id] = expected  # None = unconfigured
    for a in records.rows("exam_room_allocations"):
        where = f"exam_room_allocations[{a.get('session_id')}/{a.get('venue_id')}]"
        if a.get("venue_id") not in capacity:
            continue  # unknown venue or a policy problem, both reported above / by map_timetable
        expected = capacity[a.get("venue_id")]
        text = a.get("effective_capacity")
        if expected is None:
            problems.append(f"{where} uses a venue with no configured exam capacity "
                            f"({venues[a.get('venue_id')].get('venue_type')}); no percentage is assumed.")
            continue
        if type(text) is not str or not text.isdigit():
            problems.append(f"{where}.effective_capacity {text!r} is not a whole number.")
        elif int(text) != expected:
            problems.append(f"{where}.effective_capacity {text} contradicts the venue policy capacity {expected}.")
        allocated = a.get("allocated_count")
        if type(allocated) is int and allocated > expected:
            problems.append(f"{where} allocates {allocated} students over the policy capacity {expected}.")
    problems.check()
    return ("Capacities cross-checked: venues, capacity_policies, project rules (computer_lab 90%, lecture_hall 50%; "
            "smart_classroom and byod_lab unconfigured) and every room allocation agree.",)


# --- Staff --------------------------------------------------------------------------------------

def map_staff(records: M3DemoRecords, *, institution_id: uuid.UUID, eligibility: EligibilityPolicy,
              as_of: dt.date | None = None) -> AcademicStaffData:
    problems = _Problems()
    institution = str(institution_id)
    staff_rows = records.rows("staff")
    _unique(staff_rows, ("id",), "staff", problems)
    for r in staff_rows:
        if r.get("institution_id") != institution:
            problems.append(f"staff[{r.get('staff_number')}] belongs to another institution; no cross-institution use.")
    problems.check()
    staff_import = import_staff(
        [{k: r.get(k) for k in ("id", "staff_number", "full_name", "academic_rank", "employment_status")}
         for r in staff_rows], eligibility)
    known = {m.staff_id for m in staff_import.staff}
    all_ids = {str(r.get("id")).lower() for r in staff_rows}

    by_staff = defaultdict(list)
    for r in records.rows("staff_admin_workload"):
        if as_of is None or r.get("as_of") == as_of:
            by_staff[str(r.get("staff_id")).lower()].append(r)
    admin, missing = [], []
    for staff_id in sorted(by_staff):
        if staff_id not in all_ids:
            problems.append(f"staff_admin_workload refers to unknown staff {staff_id}.")
    for staff_id in sorted(known):
        rows = by_staff.get(staff_id, [])
        if not rows:
            missing.append(MissingWorkload(staff_id, "m3_demo: no staff_admin_workload row"))
            continue
        if len(rows) > 1:
            problems.append(f"staff {staff_id} has {len(rows)} workload rows; pass as_of to choose one.")
            continue
        r = rows[0]
        if r.get("institution_id") != institution:
            problems.append(f"staff_admin_workload[{staff_id}] belongs to another institution.")
            continue
        status = r.get("aggregate_status")
        hours = r.get("total_admin_hours_per_week")
        if status == "COMPUTED":
            if type(hours) not in (int, float, Decimal) or hours < 0:
                problems.append(f"staff_admin_workload[{staff_id}] is COMPUTED without valid hours.")
            else:
                admin.append(AdminWorkload(staff_id, float(hours), r.get("combination_rule") or ""))
        elif status == "NOT_COMPUTED":
            if hours is not None:
                problems.append(f"staff_admin_workload[{staff_id}] is NOT_COMPUTED but has hours.")
            admin.append(AdminWorkload(staff_id, None, r.get("notes") or "NOT_COMPUTED"))
        elif status == "MISSING_RESPONSE":
            missing.append(MissingWorkload(staff_id, "m3_demo: MISSING_RESPONSE (simulated Academic Data 404)"))
        else:
            problems.append(f"staff_admin_workload[{staff_id}] has unknown aggregate_status {status!r}.")
    problems.check()
    return AcademicStaffData(staff_import, tuple(admin), tuple(missing))


def map_supplemental(records: M3DemoRecords) -> SupplementalStaffData:
    """Only explicitly stored rows; references are checked against m3_demo.staff and modules."""
    problems = _Problems()
    staff_ids = {str(r.get("id")).lower() for r in records.rows("staff")}
    modules = {r.get("module_code") for r in records.rows("modules")}

    def build(table, key, make):
        rows = records.rows(table)
        _unique(rows, key, table, problems)
        out = []
        for r in rows:
            where = f"{table}[{r.get(key[0])}]"
            staff = _known_staff_id(r.get("staff_id"), where, staff_ids, problems)
            if staff is None:
                continue
            try:
                item = make(r, staff, where)
            except (SchedulingValidationError, ValueError, TypeError) as exc:
                problems.append(f"{where}: {getattr(exc, 'violations', None) and exc.violations[0].message or exc}")
                continue
            if item is not None:
                out.append(item)
        return tuple(out)

    def window(cls, extra=lambda r: ()):
        def make(r, staff, where):
            start = _typed(r, "start_at", "datetime", problems, where)
            end = _typed(r, "end_at", "datetime", problems, where)
            return cls(staff, start, end, *extra(r)) if start and end else None
        return make

    unavailability = build("staff_unavailability", ("unavailability_id",),
                           window(StaffUnavailability, lambda r: (r.get("reason") or "",)))
    availability = build("staff_availability", ("availability_id",), window(StaffAvailability))
    preferences = build("staff_day_preferences", ("preference_id",),
                        lambda r, s, w: DayPreference(s, _typed(r, "date", "date", problems, w),
                                                      DayPreferenceKind(r.get("preference_kind"))))

    def restriction(r, staff, where):
        if (r.get("restriction_type"), r.get("effect")) != ("MODULE_LECTURER", "EXCLUDE_OWN_MODULE"):
            problems.append(f"{where} has unsupported restriction {r.get('restriction_type')}/{r.get('effect')}.")
            return None
        if r.get("module_code") not in modules:
            problems.append(f"{where} refers to unknown module {r.get('module_code')!r}.")
            return None
        return StaffRestriction(staff, f"{r.get('restriction_type')} ({r.get('restriction_id')})",
                                module_code=r.get("module_code"))
    restrictions = build("staff_restrictions", ("restriction_id",), restriction)

    history_rows = records.rows("staff_duty_history")
    _unique(history_rows, ("staff_id", "previous_period"), "staff_duty_history", problems)
    totals: dict[str, list[int]] = {}
    for r in history_rows:
        where = f"staff_duty_history[{r.get('staff_id')}/{r.get('previous_period')}]"
        staff = _known_staff_id(r.get("staff_id"), where, staff_ids, problems)
        duties = _typed(r, "previous_duties", "int", problems, where)
        minutes = _typed(r, "previous_minutes", "int", problems, where)
        if staff and duties is not None and minutes is not None:
            total = totals.setdefault(staff, [0, 0])
            total[0] += duties
            total[1] += minutes
    try:
        history = tuple(DutyHistory(s, d, m) for s, (d, m) in sorted(totals.items()))
    except SchedulingValidationError as exc:
        problems += [v.message for v in exc.violations]
        history = ()
    problems.check()
    periods = sorted({r.get("previous_period") for r in history_rows})
    return SupplementalStaffData(f"{SYNTHETIC_LABEL} (history periods summed: {', '.join(periods) or 'none'})",
                                 unavailability, availability, history, preferences, restrictions)


# --- Halls ----------------------------------------------------------------------------------------

def map_halls(records: M3DemoRecords) -> tuple[tuple[Hall, ...], tuple[str, ...]]:
    """Physical layouts become SYNTHETIC halls. Nothing from m3_demo is ever VERIFIED."""
    problems, notes = _Problems(), []
    venue_ids = {r.get("venue_id") for r in records.rows("venues")}
    layouts = records.rows("physical_layouts")
    _unique(layouts, ("layout_id",), "physical_layouts", problems)
    seats = defaultdict(list)
    for s in records.rows("physical_seats"):
        seats[s.get("layout_id")].append(s)
    _unique(records.rows("physical_seats"), ("layout_id", "seat_number"), "physical_seats", problems)
    per_venue = Counter(r.get("venue_id") for r in layouts)
    halls = []
    for layout in sorted(layouts, key=lambda r: str(r.get("venue_id"))):
        lid, venue = layout.get("layout_id"), layout.get("venue_id")
        if venue not in venue_ids:
            problems.append(f"physical_layouts[{lid}] refers to unknown venue {venue!r}.")
            continue
        if per_venue[venue] > 1:
            problems.append(f"Venue {venue} has {per_venue[venue]} layouts; choose one version explicitly.")
            continue
        if layout.get("layout_status") == "VERIFIED" or "NOT" not in str(layout.get("verification_status")):
            problems.append(f"physical_layouts[{lid}] claims verification; m3_demo layouts are synthetic only.")
            continue
        rows = sorted(seats.get(lid, []), key=lambda s: (s.get("seat_number") or 0))
        if any(s.get("venue_id") != venue for s in rows):
            problems.append(f"physical_seats of {lid} refer to another venue.")
        if len(rows) != layout.get("total_seat_positions"):
            problems.append(f"physical_layouts[{lid}] says {layout.get('total_seat_positions')} seats, found {len(rows)}.")
        try:
            hall = Hall(venue, tuple(Seat(s.get("seat_number"), s.get("seat_label"), s.get("row_number"),
                                          s.get("column_number")) for s in rows),
                        tuple(s.get("seat_number") for s in rows if s.get("blocked") is True),
                        f"{SYNTHETIC_LABEL}: {lid} v{layout.get('version')} ({layout.get('verification_status')})",
                        LayoutStatus.SYNTHETIC)
        except SchedulingValidationError as exc:
            problems += [f"{lid}: {v.message}" for v in exc.violations]
            continue
        halls.append(hall)
    problems.check()
    notes.append("All m3_demo physical layouts are SYNTHETIC; every seat stays unverified (PROVISIONAL).")
    return tuple(halls), tuple(notes)
