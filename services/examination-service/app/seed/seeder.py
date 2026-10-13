"""Safe, idempotent seeding of the synthetic dataset.

Behaviour:

* The dataset is validated first; nothing is written if validation fails.
* Rows are matched on their natural key. Missing rows are inserted, changed
  rows are updated, identical rows are left alone. Rerunning therefore
  never creates duplicates.
* Nothing is ever deleted or truncated. Rows that exist in the database but
  not in the dataset are only *reported* ("extra").
* ``dry_run=True`` reads the database and reports what would change, but
  writes nothing.
* The caller owns the transaction (commit / rollback). The functions here
  never commit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import SCHEMA
from app.db.seed_models import (
    SeedExamPeriod,
    SeedExamPeriodExclusion,
    SeedModule,
    SeedModuleSpecialization,
    SeedRegistration,
    SeedStudent,
    SeedVenue,
    SeedVenueBlock,
)
from app.seed.records import Dataset
from app.seed.validation import validate_dataset

# Arbitrary constant: serialises concurrent seed runs for this transaction only
# (transaction-level locks are safe through Neon's transaction-mode pooler).
_ADVISORY_LOCK_KEY = 7_204_101_901


class SeedValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__(f"{len(errors)} validation error(s)")
        self.errors = errors


class SeedTablesMissingError(Exception):
    def __init__(self, missing: list[str]):
        super().__init__("missing tables: " + ", ".join(missing))
        self.missing = missing


@dataclass(frozen=True)
class TableSpec:
    model: type
    key: tuple[str, ...]
    records: Callable[[Dataset], Iterable[Any]]
    skip: tuple[str, ...] = ()  # legacy columns the seed never reads or writes

    @property
    def table(self) -> str:
        return self.model.__tablename__

    @property
    def columns(self) -> tuple[str, ...]:
        """Columns the seed writes (excludes identity and skipped legacy columns)."""
        return tuple(
            c.name for c in self.model.__table__.columns if c.identity is None and c.name not in self.skip
        )


# Parent tables first, so foreign keys are always satisfied.
TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(SeedModule, ("module_code",), lambda d: d.modules, skip=("specialization",)),
    TableSpec(
        SeedModuleSpecialization, ("module_code", "specialization"), lambda d: d.module_specializations
    ),
    TableSpec(SeedStudent, ("student_id",), lambda d: d.students),
    TableSpec(SeedRegistration, ("student_id", "module_code"), lambda d: d.registrations),
    TableSpec(SeedVenue, ("venue_id",), lambda d: d.venues),
    TableSpec(SeedVenueBlock, ("venue_id", "block_date", "start_time"), lambda d: d.venue_blocks),
    TableSpec(SeedExamPeriod, ("period_code",), lambda d: d.exam_periods),
    TableSpec(SeedExamPeriodExclusion, ("period_code", "excluded_date"), lambda d: d.exam_period_exclusions),
)


@dataclass
class TableChange:
    table: str
    inserts: list[dict[str, Any]] = field(default_factory=list)
    updates: list[tuple[tuple, dict[str, Any]]] = field(default_factory=list)  # (key, changed columns)
    unchanged: int = 0
    extra: int = 0  # in the database but not in the dataset; never touched

    @property
    def has_writes(self) -> bool:
        return bool(self.inserts or self.updates)


@dataclass
class SeedResult:
    dry_run: bool
    changes: list[TableChange]
    registration_counts: dict[str, int]
    warnings: list[str]

    @property
    def total_writes(self) -> int:
        return sum(len(c.inserts) + len(c.updates) for c in self.changes)


def plan_table_change(
    spec: TableSpec, existing_rows: Iterable[Mapping[str, Any]], desired_rows: Iterable[Mapping[str, Any]]
) -> TableChange:
    """Compare desired rows with existing rows by natural key (pure function)."""
    columns = spec.columns
    existing = {tuple(row[k] for k in spec.key): row for row in existing_rows}
    change = TableChange(spec.table)
    seen: set[tuple] = set()

    for row in desired_rows:
        key = tuple(row[k] for k in spec.key)
        seen.add(key)
        current = existing.get(key)
        if current is None:
            change.inserts.append({c: row[c] for c in columns})
            continue
        changed = {c: row[c] for c in columns if c not in spec.key and current[c] != row[c]}
        if changed:
            change.updates.append((key, changed))
        else:
            change.unchanged += 1

    change.extra = sum(1 for key in existing if key not in seen)
    return change


async def missing_tables(session: AsyncSession) -> list[str]:
    result = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
        {"schema": SCHEMA},
    )
    present = {name for (name,) in result}
    return [spec.table for spec in TABLE_SPECS if spec.table not in present]


async def seed_dataset(session: AsyncSession, data: Dataset, *, dry_run: bool) -> SeedResult:
    """Validate ``data`` and upsert it. Does not commit."""
    report = validate_dataset(data)
    if not report.ok:
        raise SeedValidationError(report.errors)

    missing = await missing_tables(session)
    if missing:
        raise SeedTablesMissingError(missing)

    if not dry_run:
        await session.execute(select(func.pg_advisory_xact_lock(_ADVISORY_LOCK_KEY)))

    changes = []
    for spec in TABLE_SPECS:
        model = spec.model
        result = await session.execute(select(*(getattr(model, c) for c in spec.columns)))
        desired = [dataclasses.asdict(record) for record in spec.records(data)]
        change = plan_table_change(spec, result.mappings().all(), desired)
        if not dry_run:
            await _apply(session, spec, change)
        changes.append(change)

    if dry_run:
        counts = data.registration_counts()
    else:
        counts = await registration_counts_from_db(session)
    return SeedResult(dry_run, changes, counts, report.warnings)


async def _apply(session: AsyncSession, spec: TableSpec, change: TableChange) -> None:
    model = spec.model
    if change.inserts:
        await session.execute(model.__table__.insert(), change.inserts)
    for key, values in change.updates:
        conditions = [getattr(model, k) == v for k, v in zip(spec.key, key)]
        await session.execute(
            update(model).where(*conditions).values(**values),
            execution_options={"synchronize_session": False},
        )


async def registration_counts_from_db(session: AsyncSession) -> dict[str, int]:
    """Candidates per module, counted from the stored registration rows."""
    result = await session.execute(
        select(SeedModule.module_code, func.count(SeedRegistration.student_id))
        .outerjoin(SeedRegistration, SeedRegistration.module_code == SeedModule.module_code)
        .group_by(SeedModule.module_code)
        .order_by(SeedModule.module_code)
    )
    return {code: count for code, count in result}
