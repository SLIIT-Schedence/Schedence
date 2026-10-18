"""Command-line entry point for the synthetic seed data.

Run from ``services/examination-service``::

    python -m app.seed --validate-only   # generate + validate, no database
    python -m app.seed --dry-run         # compare with the database, write nothing
    python -m app.seed                   # insert/update (never deletes)

Exit codes: 0 success, 1 database error, 2 configuration error,
3 validation failed, 4 seed tables missing (run ``alembic upgrade head``).
Credentials are never printed.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
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

from app.seed.generator import generate_dataset
from app.seed.records import Dataset
from app.seed.seeder import (
    SeedResult,
    SeedTablesMissingError,
    SeedValidationError,
    seed_dataset,
)
from app.seed.validation import validate_dataset

EXIT_OK, EXIT_DB, EXIT_CONFIG, EXIT_INVALID, EXIT_NO_TABLES = 0, 1, 2, 3, 4


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    data = generate_dataset()

    if args.validate_only:
        return _validate_only(data)

    try:
        settings = load_settings(args.env_file, db_application_name="examination-service-seed")
    except DatabaseConfigurationError as exc:
        print(exc)
        return EXIT_CONFIG

    try:
        result = run(_seed(settings, data, dry_run=args.dry_run, direct=not args.pooled))
    except SeedValidationError as exc:
        _print_errors(exc.errors)
        return EXIT_INVALID
    except SeedTablesMissingError as exc:
        print(f"Seed tables are missing in schema 'examinations': {', '.join(exc.missing)}")
        print("Run 'alembic upgrade head' from services/examination-service first.")
        return EXIT_NO_TABLES
    except (SQLAlchemyError, OSError) as exc:
        print(f"Database error: {describe_db_error(exc)}")
        return EXIT_DB

    _print_result(result)
    return EXIT_OK


async def _seed(settings, data: Dataset, *, dry_run: bool, direct: bool) -> SeedResult:
    # Admin task: direct endpoint by default (falls back to DATABASE_URL), no pooling.
    engine = create_db_engine(settings, direct=direct, poolclass=NullPool)
    try:
        factory = create_session_factory(engine)
        # Dry run: never commit (session_scope rolls back/discards on close).
        async with session_scope(factory, commit=not dry_run) as session:
            return await seed_dataset(session, data, dry_run=dry_run)
    finally:
        await dispose_engine(engine)


def _validate_only(data: Dataset) -> int:
    report = validate_dataset(data)
    print("Synthetic dataset (not written anywhere)\n")
    for label, rows in [
        ("modules", data.modules),
        ("module specializations", data.module_specializations),
        ("students", data.students),
        ("registrations", data.registrations),
        ("  of which repeat", [r for r in data.registrations if r.registration_type == "repeat"]),
        ("venues", data.venues),
        ("venue blocks", data.venue_blocks),
        ("exam periods", data.exam_periods),
        ("exam period exclusions", data.exam_period_exclusions),
    ]:
        print(f"  {label:<24}{len(rows):>5}")
    _print_counts(data.registration_counts())
    _print_warnings(report.warnings)
    if not report.ok:
        _print_errors(report.errors)
        return EXIT_INVALID
    print("\nValidation: passed")
    return EXIT_OK


def _print_result(result: SeedResult) -> None:
    mode = "DRY RUN - nothing was written" if result.dry_run else "Seed applied (committed)"
    verb = "would " if result.dry_run else ""
    print(f"Schedence examination seed: {mode}\n")
    print(f"  {'table':<30}{verb + 'insert':>14}{verb + 'update':>14}{'unchanged':>11}{'extra*':>8}")
    for c in result.changes:
        print(f"  {c.table:<30}{len(c.inserts):>14}{len(c.updates):>14}{c.unchanged:>11}{c.extra:>8}")
    print("\n  * extra = rows in the database that are not in the dataset (left untouched)")
    source = "dataset" if result.dry_run else "database"
    _print_counts(result.registration_counts, source)
    _print_warnings(result.warnings)


def _print_counts(counts: dict[str, int], source: str = "dataset") -> None:
    print(f"\nCandidates per module (counted from {source} registrations):")
    for code, n in sorted(counts.items()):
        print(f"  {code:<10}{n:>5}")


def _print_warnings(warnings: list[str]) -> None:
    if warnings:
        print("\nNotes:")
        for w in warnings:
            print(f"  - {w}")


def _print_errors(errors: list[str]) -> None:
    print("\nValidation FAILED, nothing was written:")
    for e in errors:
        print(f"  - {e}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing.")
    mode.add_argument("--validate-only", action="store_true", help="Generate and validate only; no database.")
    parser.add_argument(
        "--pooled",
        action="store_true",
        help="Use the pooled DATABASE_URL instead of DIRECT_DATABASE_URL.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file (default: $SCHEDENCE_ENV_FILE, else the nearest .env).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
