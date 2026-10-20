"""Read-only access to the SYNTHETIC Member 3 staging schema ``m3_demo`` (development only).

Safety model
------------
* Never reads ``.env`` or environment variables: the caller passes an explicit URL plus
  the host name and database name they confirmed in Neon Console for the
  ``member3-development`` branch, the branch name, and an explicit confirmation flag.
* A matching host name does NOT prove which Neon branch the endpoint serves; only Neon
  Console can show that. The checks here stop typos and known-wrong hosts (pass the
  production host in ``forbidden_hosts``); the human confirmation remains essential.
* One transaction per load, opened with
  ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`` as its first statement
  (one consistent snapshot for every table), verified via ``current_setting`` and always
  rolled back. No commit exists in this module.
* Every statement passes a guard that allows only that SET statement and ``SELECT``;
  table and column names come from a fixed allow-list (the seed manifest), are quoted as
  identifiers, and are never built from caller input.
* Errors never include the URL or credentials (schedence_db.describe_db_error / redact).

Nothing here is wired to the authenticated API or to the shared ``examinations`` /
``academic`` schemas.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from schedence_db.config import normalize_database_url
from schedence_db.exceptions import describe_db_error, redact

SCHEMA = "m3_demo"
DEVELOPMENT_BRANCH = "member3-development"
BEGIN_SNAPSHOT = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_ALLOWED = re.compile(r"^\s*(SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY\s*$|SELECT\b)",
                      re.IGNORECASE)
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|COPY|MERGE|CALL|DO|"
                        r"VACUUM|COMMENT|LOCK|REFRESH|SECURITY|EXECUTE|PREPARE)\b|;", re.IGNORECASE)

# Tables the adapter reads, with the exact columns of the seed pack manifest
# (SCHEDENCE_M3_SYNTHETIC_2026_10). Example-output tables are read for cross-checks only.
TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "institutions": ("institution_id", "institution_code", "institution_name", "dataset_status"),
    "exam_periods": ("period_code", "period_name", "academic_year_label", "semester", "start_date", "end_date",
                     "day_start_time", "day_end_time", "include_saturdays", "include_sundays"),
    "modules": ("module_code", "module_name", "academic_year", "semester", "credits", "difficulty", "exam_type",
                "duration_minutes", "reading_time_minutes", "has_extra_reading_time"),
    "students": ("student_id", "academic_year", "specialization"),
    "registrations": ("student_id", "module_code", "registration_type"),
    "venues": ("venue_id", "venue_name", "venue_type", "building", "floor", "normal_capacity", "exam_capacity"),
    "capacity_policies": ("venue_id", "venue_type", "normal_capacity", "exam_percentage", "effective_exam_capacity",
                          "policy_source", "institution_verified", "dataset_status"),
    "physical_layouts": ("venue_id", "layout_id", "layout_status", "verification_status", "total_seat_positions",
                         "version", "source"),
    "physical_seats": ("layout_id", "venue_id", "seat_number", "seat_label", "row_number", "column_number", "blocked",
                       "verification_status"),
    "exam_sessions": ("session_id", "module_code", "period_code", "start_at", "end_at", "timetable_status",
                      "publication_status", "source"),
    "exam_room_allocations": ("session_id", "venue_id", "module_code", "candidate_count", "effective_capacity",
                              "allocated_count", "capacity_source", "allocation_status"),
    "exam_logical_seats": ("session_id", "venue_id", "student_id", "module_code", "logical_seat_number",
                           "provisional_label", "seat_status", "physical_verified"),
    "staff": ("id", "institution_id", "staff_number", "full_name", "email", "academic_rank", "employment_status",
              "identity_user_id", "created_at"),
    "staff_admin_workload": ("staff_id", "institution_id", "as_of", "aggregate_status", "total_admin_hours_per_week",
                             "combination_rule", "available_via_api", "notes", "dataset_status"),
    "staff_availability": ("availability_id", "staff_id", "start_at", "end_at", "source"),
    "staff_unavailability": ("unavailability_id", "staff_id", "start_at", "end_at", "reason", "source"),
    "staff_day_preferences": ("preference_id", "staff_id", "date", "preference_kind", "source"),
    "staff_duty_history": ("staff_id", "previous_duties", "previous_minutes", "history_source", "previous_period",
                           "data_status"),
    "staff_restrictions": ("restriction_id", "staff_id", "module_code", "restriction_type", "effect", "source"),
    "eligibility_rank_order": ("priority_order", "academic_rank", "supervisor_eligible", "invigilator_eligible",
                               "policy_source"),
    "eligibility_role_rules": ("duty_role", "minimum_academic_rank", "allow_unranked", "minimum_gap_minutes",
                               "policy_source"),
    "duty_limit_policy": ("policy_id", "maximum_duties_per_day", "maximum_duties_per_period", "minimum_gap_minutes",
                          "source", "notes"),
    "staff_duty_limits": ("staff_id", "max_duties_per_day", "max_duties_per_period", "minimum_required_rest_minutes",
                          "source"),
    "staffing_rules": ("rule_id", "min_students", "max_students", "required_supervisors", "required_invigilators",
                       "source", "institution_verified"),
    "fairness_policy": ("policy_id", "load_unit", "supervisor_weight", "invigilator_weight", "history_weight",
                        "admin_load_per_weekly_hour", "missing_admin_hours", "back_to_back_minutes", "weight_balance",
                        "weight_peak", "weight_back_to_back", "weight_preference", "hard_avoid_days", "source",
                        "institution_verified"),
    "duty_requirements": ("requirement_id", "session_id", "venue_id", "student_count", "supervisors_required",
                          "invigilators_required", "staffing_rule_id", "rule_source", "institution_verified"),
    "duty_roster_example": ("assignment_id", "run_id", "session_id", "staff_id", "duty_date", "session_ref",
                            "venue_id", "role", "generation_source", "roster_state", "approved"),
    "seating_plans": ("plan_id", "period_code", "timetable_reference", "plan_state", "physical_verification",
                      "created_from", "approved"),
    "seat_assignments": ("plan_id", "session_id", "venue_id", "student_id", "logical_seat_number",
                         "provisional_physical_label", "layout_id", "verification_status", "physically_verified",
                         "source"),
})


class DemoDatabaseError(Exception):
    code = "M3_DEMO_DATABASE_ERROR"


class DemoTargetRefused(DemoDatabaseError):
    """The connection target was not explicitly verified; nothing was connected."""
    code = "M3_DEMO_TARGET_REFUSED"


class DemoDatabaseUnavailable(DemoDatabaseError):
    code = "M3_DEMO_DATABASE_UNAVAILABLE"


class DemoSchemaError(DemoDatabaseError):
    code = "M3_DEMO_SCHEMA_ERROR"


class ReadOnlyViolation(DemoDatabaseError):
    code = "M3_DEMO_WRITE_REFUSED"


def assert_read_only(statement: str) -> None:
    """Allow only the read-only snapshot SET statement and single SELECT statements."""
    if type(statement) is not str or not _ALLOWED.match(statement) or _FORBIDDEN.search(statement):
        raise ReadOnlyViolation("Only read-only SELECT statements may run against m3_demo.")


class RowSource(Protocol):
    async def begin_read_only(self) -> None: ...
    async def schema_columns(self) -> dict[str, set[str]]: ...
    async def fetch(self, table: str) -> list[dict]: ...


class SqlRowSource:
    """RowSource over an injected SQLAlchemy AsyncSession (live or test double)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _execute(self, statement: str, params: Mapping | None = None):
        assert_read_only(statement)
        try:
            return await self.session.execute(text(statement), dict(params or {}))
        except SQLAlchemyError as exc:
            raise DemoDatabaseUnavailable(f"m3_demo read failed: {describe_db_error(exc)}") from None
        except (OSError, TimeoutError) as exc:
            raise DemoDatabaseUnavailable(f"m3_demo read failed: {describe_db_error(exc)}") from None

    async def begin_read_only(self) -> None:
        await self._execute(BEGIN_SNAPSHOT)
        result = await self._execute("SELECT current_setting('transaction_read_only'), "
                                     "current_setting('transaction_isolation')")
        settings = result.all()
        if settings != [("on", "repeatable read")]:
            raise ReadOnlyViolation("The transaction is not a read-only REPEATABLE READ snapshot; stopping.")

    async def schema_columns(self) -> dict[str, set[str]]:
        result = await self._execute("SELECT table_name, column_name FROM information_schema.columns "
                                     "WHERE table_schema = :schema", {"schema": SCHEMA})
        found: dict[str, set[str]] = {}
        for table, column in result.all():
            found.setdefault(table, set()).add(column)
        return found

    async def fetch(self, table: str) -> list[dict]:
        if table not in TABLE_COLUMNS or not _IDENT.fullmatch(table):
            raise DemoSchemaError(f"Table {table!r} is not on the m3_demo read allow-list.")
        columns = TABLE_COLUMNS[table]
        quoted = ", ".join(f'"{c}"' for c in columns)
        result = await self._execute(f'SELECT {quoted} FROM "{SCHEMA}"."{table}" ORDER BY 1')
        return [dict(zip(columns, row)) for row in result.all()]


@dataclass(frozen=True)
class M3DemoRecords:
    """Immutable snapshot of the m3_demo tables, keyed by table name."""
    tables: Mapping[str, tuple[Mapping, ...]]

    def rows(self, table: str) -> tuple[Mapping, ...]:
        return self.tables[table]

    @property
    def total_records(self) -> int:
        return sum(len(rows) for rows in self.tables.values())


async def load_m3_demo_records(source: RowSource) -> M3DemoRecords:
    """Read every allow-listed table in one read-only transaction (opened by the caller's session)."""
    await source.begin_read_only()
    present = await source.schema_columns()
    problems = []
    for table, columns in TABLE_COLUMNS.items():
        if table not in present:
            problems.append(f"m3_demo.{table} is missing.")
        elif not set(columns) <= present[table]:
            problems.append(f"m3_demo.{table} lacks columns: {', '.join(sorted(set(columns) - present[table]))}.")
    if problems:
        raise DemoSchemaError("Incorrect m3_demo schema: " + " ".join(problems))
    tables = {}
    for table, columns in TABLE_COLUMNS.items():
        rows = await source.fetch(table)
        if any(not isinstance(r, Mapping) or tuple(r) != columns for r in rows):
            raise DemoSchemaError(f"m3_demo.{table} returned rows with unexpected columns.")
        tables[table] = tuple(MappingProxyType(dict(r)) for r in rows)
    return M3DemoRecords(MappingProxyType(tables))


@dataclass(frozen=True)
class DemoTarget:
    """A verified target. ``url`` holds credentials and must never be logged or shown;
    ``description`` is the safe summary (no user name or password)."""
    url: str = field(repr=False)  # never appears in repr()/logs
    description: str = ""


def verify_demo_target(url: str, *, expected_host: str, expected_database: str, expected_branch: str,
                       confirmed_in_neon_console: bool, forbidden_hosts: tuple[str, ...] = ()) -> DemoTarget:
    """Refuse unless every explicit check passes. These checks cannot prove the Neon branch
    by themselves: ``confirmed_in_neon_console=True`` records that the user looked up this
    exact host for ``member3-development`` in Neon Console."""
    if confirmed_in_neon_console is not True:
        raise DemoTargetRefused("Confirm the member3-development endpoint in Neon Console first "
                                "(confirmed_in_neon_console=True). Not connecting.")
    if expected_branch != DEVELOPMENT_BRANCH:
        raise DemoTargetRefused(f"Only the {DEVELOPMENT_BRANCH!r} Neon branch may be used. Not connecting.")
    if not all(type(v) is str and v.strip() for v in (url, expected_host, expected_database)):
        raise DemoTargetRefused("An explicit development URL, confirmed host name and database name are required.")
    if not isinstance(forbidden_hosts, tuple) or any(type(h) is not str for h in forbidden_hosts):
        raise DemoTargetRefused("forbidden_hosts must be a tuple of host names.")
    try:
        normalized = normalize_database_url(url)
    except ValueError as exc:
        raise DemoTargetRefused(f"Development URL {redact(str(exc), url)}") from None
    parsed = make_url(normalized)
    host = (parsed.host or "").lower()
    if host in {h.strip().lower() for h in forbidden_hosts}:
        raise DemoTargetRefused("The URL host is on the forbidden (production) list. Not connecting.")
    if host != expected_host.strip().lower():
        raise DemoTargetRefused("The URL host does not match the confirmed member3-development host. Not connecting.")
    if parsed.database != expected_database.strip():
        raise DemoTargetRefused("The URL database does not match the confirmed database name. Not connecting.")
    return DemoTarget(parsed.render_as_string(hide_password=False),
                      f"{DEVELOPMENT_BRANCH} (user-confirmed in Neon Console): host={host}, database={parsed.database}")


@asynccontextmanager
async def read_only_m3_demo(url: str, *, expected_host: str, expected_database: str, expected_branch: str,
                            confirmed_in_neon_console: bool,
                            forbidden_hosts: tuple[str, ...] = ()) -> AsyncIterator[SqlRowSource]:
    """Live read-only source. Only for an explicitly confirmed member3-development branch."""
    target = verify_demo_target(url, expected_host=expected_host, expected_database=expected_database,
                                expected_branch=expected_branch, confirmed_in_neon_console=confirmed_in_neon_console,
                                forbidden_hosts=forbidden_hosts).url
    engine = create_async_engine(target, poolclass=NullPool, connect_args={"connect_timeout": 10})
    try:
        async with AsyncSession(engine) as session:
            try:
                yield SqlRowSource(session)
            finally:
                await session.rollback()  # never commit
    finally:
        await engine.dispose()
