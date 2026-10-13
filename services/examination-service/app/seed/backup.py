"""Backup and restore of the ``examinations.seed_*`` tables.

``export`` reads every seed table and writes one JSON-lines file per table plus
``manifest.json`` (row counts, SHA-256 checksums, Alembic revision). It only
reads the database.

``restore`` re-inserts rows from a backup. Existing rows are left alone
(``ON CONFLICT DO NOTHING``), so it is safe to rerun and never overwrites or
deletes anything. Identity values (``seed_venue_blocks.block_id``) are
regenerated; rows are matched on their natural keys.

Run from ``services/examination-service``::

    python -m app.seed.backup export [--pooled]
    python -m app.seed.backup verify BACKUP_DIR
    python -m app.seed.backup restore BACKUP_DIR [--pooled] [--dry-run]

Backups go to ``backups/`` (git-ignored). Credentials are never written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Date, Time, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from schedence_db import create_db_engine, describe_db_error, dispose_engine, load_settings
from schedence_db.runtime import run

from app.db.base import SCHEMA, Base
from app.db import seed_models  # noqa: F401  (registers the tables)

BACKUP_ROOT = Path(__file__).resolve().parents[2] / "backups"
# Parents first: the order used for restore (and reversed for any cleanup).
TABLES = (
    "seed_modules",
    "seed_module_specializations",
    "seed_students",
    "seed_registrations",
    "seed_venues",
    "seed_venue_blocks",
    "seed_exam_periods",
    "seed_exam_period_exclusions",
)


def _to_json(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.time, dt.datetime)):
        return value.isoformat()
    return value


def _from_json(column, value: Any) -> Any:
    if value is None:
        return None
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
    """Write every existing seed table to ``out_dir``; returns the manifest."""
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
        row = {
            c.name: _from_json(c, raw[c.name])
            for c in model_table.columns
            if c.name in raw and c.identity is None  # identity values are regenerated
        }
        # Backups taken before migration 0002 have no reading-time flag.
        if table == "seed_modules" and "has_extra_reading_time" not in row:
            row["has_extra_reading_time"] = row["reading_time_minutes"] > 0
        rows.append(row)
    return rows


async def restore_tables(conn: AsyncConnection, backup_dir: Path, *, dry_run: bool) -> dict[str, tuple[int, int]]:
    """Insert missing rows; returns ``{table: (rows in backup, rows inserted)}``."""
    problems = verify_backup(backup_dir)
    if problems:
        raise ValueError("backup failed verification: " + "; ".join(problems))
    result = {}
    for table in TABLES:
        rows = load_rows(backup_dir, table)
        inserted = 0
        if rows:
            statement = insert(Base.metadata.tables[f"{SCHEMA}.{table}"]).on_conflict_do_nothing()
            for row in rows:
                inserted += (await conn.execute(statement, row)).rowcount
        result[table] = (len(rows), inserted)
    if dry_run:
        await conn.rollback()
    return result


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


async def _run_restore(settings, backup_dir: Path, direct: bool, dry_run: bool):
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await restore_tables(conn, backup_dir, dry_run=dry_run)
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

        result = run(_run_restore(settings, args.backup_dir, direct=not args.pooled, dry_run=args.dry_run))
        print("Restore", "DRY RUN (rolled back)" if args.dry_run else "committed")
        for table, (in_backup, inserted) in result.items():
            print(f"  {table:<30} in backup {in_backup:>5}  inserted {inserted:>5}")
        return 0
    except (SQLAlchemyError, OSError) as exc:
        print(f"Database error: {describe_db_error(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
