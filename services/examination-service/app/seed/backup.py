"""Backup and restore of the ``examinations.seed_*`` tables and the seating rules.

``export`` reads every seed table and, from migration 0005 on, the seating
tables (``exam_seating_rules``, ``exam_seating_rule_history``), and writes one
JSON-lines file per table plus ``manifest.json`` (row counts, SHA-256
checksums, Alembic revision). Tables that do not exist yet (a database at
0004) are simply left out. It only reads the database.

``restore`` re-inserts rows from a backup. For the seed tables, existing rows
are left alone (``ON CONFLICT DO NOTHING``), so it is safe to rerun and never
overwrites or deletes anything. Identity values (``seed_venue_blocks.block_id``)
are regenerated; rows are matched on their natural keys.

The seating tables keep their IDs (history rows point to rule IDs). See
:func:`restore_seating`: they are restored only when this cannot lose or
overwrite a user's change; otherwise the whole restore stops and changes nothing.

Run from ``services/examination-service``::

    python -m app.seed.backup export [--pooled]
    python -m app.seed.backup verify BACKUP_DIR
    python -m app.seed.backup restore BACKUP_DIR [--pooled] [--dry-run] [--skip-seating]

Backups go to ``backups/`` (git-ignored). Credentials are never written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Time, Uuid, bindparam, literal_column, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from schedence_db import create_db_engine, describe_db_error, dispose_engine, load_settings
from schedence_db.runtime import run

from app.db.base import SCHEMA, Base
from app.db import seating_models, seed_models  # noqa: F401  (registers the tables)
from app.db.seed_models import DEFAULT_EXAM_KIND

PRE_0008_MODULE_DEFAULTS = {"has_final_exam": True, "has_mid_exam": False, "mid_duration_minutes": None,
                            "delivery_mode": "regular", "block_category": None}

BACKUP_ROOT = Path(__file__).resolve().parents[2] / "backups"
# Parents first: the order used for restore (and reversed for any cleanup).
SEED_TABLES = (
    "seed_modules",
    "seed_module_specializations",
    "seed_students",
    "seed_registrations",
    "seed_venues",
    "seed_venue_blocks",
    "seed_exam_periods",
    "seed_exam_period_exclusions",
    "seed_exam_period_modules",  # migration 0009; exported only when present
)
# Created by migration 0005. Exported only when present; IDs are kept.
SEATING_TABLES = ("exam_seating_rules", "exam_seating_rule_history")
TABLES = SEED_TABLES + SEATING_TABLES


class RestoreError(Exception):
    """The backup cannot be restored safely; nothing was changed."""


def _to_json(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.time, dt.datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _from_json(column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        return dt.datetime.fromisoformat(value)
    if isinstance(column.type, Uuid):
        return uuid.UUID(value)
    if isinstance(column.type, Date):
        return dt.date.fromisoformat(value)
    if isinstance(column.type, Time):
        return dt.time.fromisoformat(value)
    if isinstance(column.type, Boolean):
        return bool(value)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- Export ----------------------------------------------------------------------


async def _existing_tables(conn: AsyncConnection) -> set[str]:
    result = await conn.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"), {"s": SCHEMA}
    )
    return {name for (name,) in result}


async def export_tables(conn: AsyncConnection, out_dir: Path) -> dict[str, Any]:
    """Write every existing seed and seating table to ``out_dir``; returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=False)
    present = await _existing_tables(conn)
    revision = (await conn.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version"))).scalar()
    manifest: dict[str, Any] = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "schema": SCHEMA,
        "alembic_revision": revision,
        "tables": {},
    }
    for table in TABLES:
        if table not in present:
            continue
        pk = ", ".join(c.name for c in Base.metadata.tables[f"{SCHEMA}.{table}"].primary_key.columns)
        rows = (await conn.execute(text(f"SELECT * FROM {SCHEMA}.{table} ORDER BY {pk}"))).mappings().all()
        path = out_dir / f"{table}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps({k: _to_json(v) for k, v in row.items()}, sort_keys=True) + "\n")
        manifest["tables"][table] = {
            "rows": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "sha256": _sha256(path),
        }
    manifest["total_rows"] = sum(t["rows"] for t in manifest["tables"].values())
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_backup(backup_dir: Path) -> list[str]:
    """Check files against the manifest; returns a list of problems (empty = OK)."""
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for table, info in manifest["tables"].items():
        path = backup_dir / f"{table}.jsonl"
        if not path.is_file():
            problems.append(f"{table}: file missing")
            continue
        if _sha256(path) != info["sha256"]:
            problems.append(f"{table}: checksum mismatch")
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        if len(lines) != info["rows"]:
            problems.append(f"{table}: {len(lines)} rows in file, manifest says {info['rows']}")
    return problems


# --- Restore -----------------------------------------------------------------------


def load_rows(backup_dir: Path, table: str) -> list[dict[str, Any]]:
    """Rows of one table, converted to Python types for the *current* model."""
    model_table = Base.metadata.tables[f"{SCHEMA}.{table}"]
    path = backup_dir / f"{table}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        raw = json.loads(line)
        keep_ids = table in SEATING_TABLES  # history rows refer to rule IDs
        row = {
            c.name: _from_json(c, raw[c.name])
            for c in model_table.columns
            if c.name in raw and (keep_ids or c.identity is None)  # seed identity values are regenerated
        }
        # Backups taken before migration 0003 have no building / floor / source_ref:
        # those keys are simply absent (NULL), which 0003 allows and 0004 does not.
        # Backups taken before migration 0002 have no reading-time flag.
        if table == "seed_modules" and "has_extra_reading_time" not in row:
            row["has_extra_reading_time"] = row["reading_time_minutes"] > 0
        # Backups taken before migration 0007 have no exam kind: those periods were finals.
        if table == "seed_exam_periods" and "exam_kind" not in row:
            row["exam_kind"] = DEFAULT_EXAM_KIND
        # Backups taken before migration 0008: modules had a final examination only.
        if table == "seed_modules":
            for name, value in PRE_0008_MODULE_DEFAULTS.items():
                row.setdefault(name, value)
        rows.append(row)
    return rows


@dataclass(frozen=True)
class SeatingRestore:
    # not_in_backup: a 0004 backup (database left alone); skipped: --skip-seating;
    # replaced_initial: the database had only the migration's 50% default, now the backup's rules;
    # merged: missing rows added (every database row was already in the backup).
    mode: str
    counts: dict[str, tuple[int, int]]  # {table: (rows in backup, rows inserted)}


def _pk(table: str) -> str:
    [column] = Base.metadata.tables[f"{SCHEMA}.{table}"].primary_key.columns
    return column.name


async def _current_rows(conn: AsyncConnection, table: str) -> dict[Any, dict[str, Any]]:
    pk = _pk(table)
    rows = (await conn.execute(text(f"SELECT * FROM {SCHEMA}.{table} ORDER BY {pk}"))).mappings().all()
    return {row[pk]: dict(row) for row in rows}


async def _insert_keeping_ids(conn: AsyncConnection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [c.name for c in Base.metadata.tables[f"{SCHEMA}.{table}"].columns]
    statement = text(
        f"INSERT INTO {SCHEMA}.{table} ({', '.join(columns)}) OVERRIDING SYSTEM VALUE "
        f"VALUES ({', '.join(':' + c for c in columns)})"
    ).bindparams(*(bindparam(c, type_=Base.metadata.tables[f"{SCHEMA}.{table}"].c[c].type) for c in columns))
    await conn.execute(statement, [{c: row.get(c) for c in columns} for row in rows])


async def _reset_identity(conn: AsyncConnection, table: str) -> None:
    """Next generated ID = highest restored ID + 1, so new rows never collide."""
    pk = _pk(table)
    await conn.execute(text(
        f"SELECT setval(pg_get_serial_sequence('{SCHEMA}.{table}', '{pk}'), "
        f"(SELECT coalesce(max({pk}), 0) + 1 FROM {SCHEMA}.{table}), false)"
    ))


async def restore_seating(conn: AsyncConnection, backup_dir: Path, *, skip: bool = False) -> SeatingRestore:
    """Restore the seating rules and their history without losing any change.

    * Backup without seating tables (taken at 0004): the database is not touched.
    * Database still in its initial state (only the 50% default written by
      migration 0005, no user change): those rows are replaced by the backup.
    * Otherwise every database row must already be in the backup, unchanged;
      the missing backup rows are added. If any row differs or is newer, a
      :class:`RestoreError` stops the restore (the caller rolls back).
    """
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    if not any(t in manifest["tables"] for t in SEATING_TABLES):
        return SeatingRestore("not_in_backup", {})
    if skip:
        return SeatingRestore("skipped", {})
    if not set(SEATING_TABLES) <= await _existing_tables(conn):
        raise RestoreError("the backup has seating rules but the database has no seating tables: "
                           "run 'alembic upgrade 0005' first, or use --skip-seating")
    backup = {t: {row[_pk(t)]: row for row in load_rows(backup_dir, t)} for t in SEATING_TABLES}
    if not any(r["scope"] == "default" for r in backup["exam_seating_rules"].values()):
        raise RestoreError("the backup has no default seating rule")

    # Lock out concurrent changes while comparing and writing.
    await conn.execute(text(f"LOCK TABLE {SCHEMA}.exam_seating_rules, {SCHEMA}.exam_seating_rule_history "
                            "IN SHARE ROW EXCLUSIVE MODE"))
    current = {t: await _current_rows(conn, t) for t in SEATING_TABLES}
    initial = all(r["updated_by"] is None for r in current["exam_seating_rules"].values()) and all(
        r["changed_by"] is None for r in current["exam_seating_rule_history"].values())

    if initial:
        # Only rows written by the migration (changed_by / updated_by NULL) are removed.
        await conn.execute(text(f"DELETE FROM {SCHEMA}.exam_seating_rule_history WHERE changed_by IS NULL"))
        await conn.execute(text(f"DELETE FROM {SCHEMA}.exam_seating_rules WHERE updated_by IS NULL"))
        current = {t: {} for t in SEATING_TABLES}
    else:
        for table in SEATING_TABLES:
            for key, row in current[table].items():
                if backup[table].get(key) != row:
                    raise RestoreError(f"{table} row {key} is not in the backup or differs from it: the database "
                                       "has seating changes newer than the backup; nothing was restored")

    counts = {}
    for table in SEATING_TABLES:  # rules first: history refers to them only by value
        missing = [row for key, row in backup[table].items() if key not in current[table]]
        await _insert_keeping_ids(conn, table, missing)
        await _reset_identity(conn, table)
        counts[table] = (len(backup[table]), len(missing))
    return SeatingRestore("replaced_initial" if initial else "merged", counts)


async def restore_tables(
    conn: AsyncConnection, backup_dir: Path, *, dry_run: bool, skip_seating: bool = False
) -> tuple[dict[str, tuple[int, int]], SeatingRestore]:
    """Insert missing rows; returns ``({table: (rows in backup, rows inserted)}, seating result)``.

    Everything runs in the caller's transaction; any error leaves it to be rolled back.
    """
    problems = verify_backup(backup_dir)
    if problems:
        raise ValueError("backup failed verification: " + "; ".join(problems))
    result = {}
    for table in SEED_TABLES:
        rows = load_rows(backup_dir, table)
        inserted = 0
        if rows:
            statement = (
                insert(Base.metadata.tables[f"{SCHEMA}.{table}"])
                .on_conflict_do_nothing()
                .returning(literal_column("1"))
            )
            for row in rows:
                # rowcount is -1 here (psycopg); count the RETURNING rows instead.
                inserted += len((await conn.execute(statement, row)).all())
        result[table] = (len(rows), inserted)
    # After the seed tables: venue overrides need their venues.
    seating = await restore_seating(conn, backup_dir, skip=skip_seating)
    if dry_run:
        await conn.rollback()
    return result, seating


# --- CLI ------------------------------------------------------------------------


async def _run_export(settings, direct: bool) -> tuple[Path, dict[str, Any]]:
    out_dir = BACKUP_ROOT / f"seed_backup_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            # One REPEATABLE READ transaction: every table comes from the same snapshot.
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                return out_dir, await export_tables(conn, out_dir)
    finally:
        await dispose_engine(engine)


async def _run_restore(settings, backup_dir: Path, direct: bool, dry_run: bool, skip_seating: bool):
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            try:
                result = await restore_tables(conn, backup_dir, dry_run=dry_run, skip_seating=skip_seating)
            except BaseException:
                await conn.rollback()
                raise
            if not dry_run:
                await conn.commit()
            return result
    finally:
        await dispose_engine(engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed.backup", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Write all seed tables to backups/")
    export.add_argument("--pooled", action="store_true", help="Use DATABASE_URL instead of DIRECT_DATABASE_URL.")
    verify = commands.add_parser("verify", help="Check a backup against its manifest (no database)")
    verify.add_argument("backup_dir", type=Path)
    restore = commands.add_parser("restore", help="Insert rows from a backup that are missing")
    restore.add_argument("backup_dir", type=Path)
    restore.add_argument("--pooled", action="store_true", help="Use DATABASE_URL instead of DIRECT_DATABASE_URL.")
    restore.add_argument("--dry-run", action="store_true", help="Roll back instead of committing.")
    restore.add_argument("--skip-seating", action="store_true",
                         help="Restore only the seed tables, not the seating rules and history.")
    args = parser.parse_args(argv)

    if args.command == "verify":
        problems = verify_backup(args.backup_dir)
        print("Backup OK" if not problems else "\n".join(problems))
        return 0 if not problems else 1

    try:
        settings = load_settings(db_application_name="examination-service-backup")
        if args.command == "export":
            out_dir, manifest = run(_run_export(settings, direct=not args.pooled))
            print(f"Backup written: {out_dir}")
            print(f"Alembic revision: {manifest['alembic_revision']}")
            for table, info in manifest["tables"].items():
                print(f"  {table:<30}{info['rows']:>6}")
            print(f"  {'total':<30}{manifest['total_rows']:>6}")
            problems = verify_backup(out_dir)
            print("Verification:", "OK" if not problems else "; ".join(problems))
            return 0 if not problems else 1

        result, seating = run(_run_restore(settings, args.backup_dir, direct=not args.pooled,
                                           dry_run=args.dry_run, skip_seating=args.skip_seating))
        print("Restore", "DRY RUN (rolled back)" if args.dry_run else "committed")
        for table, (in_backup, inserted) in {**result, **seating.counts}.items():
            print(f"  {table:<30} in backup {in_backup:>5}  inserted {inserted:>5}")
        print(f"Seating rules: {seating.mode}")
        return 0
    except RestoreError as exc:
        print(f"Restore stopped, nothing changed: {exc}")
        return 1
    except (SQLAlchemyError, OSError) as exc:
        print(f"Database error: {describe_db_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
