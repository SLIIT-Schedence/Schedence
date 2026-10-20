"""Replace the old synthetic (SYN) seed data with the current dataset, atomically.

Everything happens in ONE database transaction:

1. verify the backup's checksums (nothing is touched if they fail);
2. take a transaction-level advisory lock (no concurrent seed runs);
3. delete old rows, children first. Only rows whose natural key is in the
   verified backup AND not in the new dataset can be deleted, and only from
   the ``examinations.seed_*`` tables;
4. insert/update the new dataset (same code as ``python -m app.seed``);
5. check the final state: every table holds exactly the dataset's rows, no
   leftovers, and no orphaned foreign keys.

Any error or failed check raises, and the whole transaction is rolled back,
including deletions and the updated venue block. ``--dry-run`` performs all
steps and then rolls back (a full rehearsal).

Run from ``services/examination-service``::

    python -m app.seed.replace --backup backups/seed_backup_... --pooled --dry-run
    python -m app.seed.replace --backup backups/seed_backup_... --pooled
"""

from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from schedence_db import (
    DatabaseConfigurationError,
    create_db_engine,
    create_session_factory,
    describe_db_error,
    dispose_engine,
    load_settings,
    session_scope,
)
from schedence_db.runtime import run

from app.db.base import SCHEMA
from app.seed.backup import load_rows, verify_backup
from app.seed.generator import generate_dataset
from app.seed.records import Dataset
from app.seed.seeder import TABLE_SPECS, SeedResult, seed_dataset

# Foreign keys to check after the replacement: (child table, columns, parent table, columns).
FOREIGN_KEYS = (
    ("seed_module_specializations", "module_code", "seed_modules", "module_code"),
    ("seed_registrations", "student_id", "seed_students", "student_id"),
    ("seed_registrations", "module_code", "seed_modules", "module_code"),
    ("seed_venue_blocks", "venue_id", "seed_venues", "venue_id"),
    ("seed_exam_period_exclusions", "period_code", "seed_exam_periods", "period_code"),
    ("seed_exam_period_modules", "period_code", "seed_exam_periods", "period_code"),
    ("seed_exam_period_modules", "module_code", "seed_modules", "module_code"),
)


class ReplacementError(Exception):
    """A safety check failed; the transaction must be rolled back."""


@dataclass
class ReplaceResult:
    deleted: dict[str, int]
    seed: SeedResult
    final_counts: dict[str, int]


def plan_deletions(backup_rows: Mapping[str, list[dict[str, Any]]], data: Dataset) -> dict[str, list[tuple]]:
    """Keys to delete per table, children first (pure function).

    A row is deleted only if it is in the backup and its natural key is not
    part of the new dataset. Shared rows (e.g. identical venues) are kept.
    """
    plan = {}
    for spec in reversed(TABLE_SPECS):
        new_keys = {tuple(getattr(r, k) for k in spec.key) for r in spec.records(data)}
        old_keys = {tuple(row[k] for k in spec.key) for row in backup_rows.get(spec.table, [])}
        plan[spec.table] = sorted(old_keys - new_keys)
    return plan


def expected_counts(data: Dataset) -> dict[str, int]:
    return {spec.table: len(spec.records(data)) for spec in TABLE_SPECS}


async def replace_dataset(session: AsyncSession, data: Dataset, backup_dir: Path) -> ReplaceResult:
    """Delete old rows and seed ``data`` in the caller's transaction. Never commits."""
    problems = verify_backup(backup_dir)
    if problems:
        raise ReplacementError("backup failed verification: " + "; ".join(problems))

    await session.execute(select(func.pg_advisory_xact_lock(7_204_101_902)))

    specs = {spec.table: spec for spec in TABLE_SPECS}
    backup_rows = {table: load_rows(backup_dir, table) for table in specs}
    deleted = {}
    for table, keys in plan_deletions(backup_rows, data).items():
        model = specs[table].model
        if model.__table__.schema != SCHEMA:  # defensive: only ever touch our own schema
            raise ReplacementError(f"refusing to touch {model.__table__.fullname}")
        deleted[table] = 0
        if keys:
            columns = tuple_(*(getattr(model, k) for k in specs[table].key))
            result = await session.execute(
                model.__table__.delete().where(columns.in_(keys)),
                execution_options={"synchronize_session": False},
            )
            if result.rowcount > len(keys):
                raise ReplacementError(f"{table}: deleted {result.rowcount} rows, expected at most {len(keys)}")
            deleted[table] = result.rowcount

    seed = await seed_dataset(session, data, dry_run=False)
    leftovers = {c.table: c.extra for c in seed.changes if c.extra}
    if leftovers:
        raise ReplacementError(f"rows not in the dataset remain: {leftovers}")

    final = {}
    for table, spec in specs.items():
        final[table] = (await session.execute(select(func.count()).select_from(spec.model))).scalar_one()
    if final != expected_counts(data):
        raise ReplacementError(f"final counts {final} differ from expected {expected_counts(data)}")

    for child, child_col, parent, parent_col in FOREIGN_KEYS:
        orphans = (await session.execute(text(
            f"SELECT count(*) FROM {SCHEMA}.{child} c LEFT JOIN {SCHEMA}.{parent} p "
            f"ON p.{parent_col} = c.{child_col} WHERE p.{parent_col} IS NULL"
        ))).scalar_one()
        if orphans:
            raise ReplacementError(f"{orphans} orphaned rows in {child}.{child_col}")

    return ReplaceResult(deleted, seed, final)


async def _run(settings, backup_dir: Path, *, dry_run: bool, direct: bool) -> ReplaceResult:
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        factory = create_session_factory(engine)
        # commit only when not a dry run; any exception rolls back everything.
        async with session_scope(factory, commit=not dry_run) as session:
            return await replace_dataset(session, generate_dataset(), backup_dir)
    finally:
        await dispose_engine(engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed.replace", description=__doc__.splitlines()[0])
    parser.add_argument("--backup", type=Path, required=True, help="Verified backup of the current seed data")
    parser.add_argument("--dry-run", action="store_true", help="Do everything, then roll back.")
    parser.add_argument("--pooled", action="store_true", help="Use DATABASE_URL instead of DIRECT_DATABASE_URL.")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(db_application_name="examination-service-replace")
        result = run(_run(settings, args.backup, dry_run=args.dry_run, direct=not args.pooled))
    except DatabaseConfigurationError as exc:
        print(exc)
        return 2
    except ReplacementError as exc:
        print(f"Safety check failed, everything rolled back: {exc}")
        return 3
    except (SQLAlchemyError, OSError) as exc:
        print(f"Database error, everything rolled back: {describe_db_error(exc)}")
        return 1

    print("DRY RUN - everything rolled back" if args.dry_run else "Replacement committed")
    print(f"\n  {'table':<30}{'deleted':>9}{'inserted':>10}{'updated':>9}{'unchanged':>11}{'final':>7}")
    for change in result.seed.changes:
        print(f"  {change.table:<30}{result.deleted[change.table]:>9}{len(change.inserts):>10}"
              f"{len(change.updates):>9}{change.unchanged:>11}{result.final_counts[change.table]:>7}")
    print("\n  Checks passed: no leftover rows, expected final counts, no orphaned foreign keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
