"""Seed the built-in system roles and permissions (idempotent).

Usage (PowerShell, from services/identity-service):
    python -m scripts.seed_roles --dry-run     # show what would change, write nothing
    python -m scripts.seed_roles               # apply
    python -m scripts.seed_roles --prune       # also remove permissions no longer in the catalog from built-in roles
"""

from __future__ import annotations

import argparse

from sqlalchemy.exc import SQLAlchemyError

from app.repositories.base import PersistenceConflictError, PersistenceError
from app.services.seeding import RbacSeeder, SeedReport
from schedence_db import DatabaseConfigurationError
from schedence_db.runtime import run
from scripts._operator import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_REFUSED,
    database_failure,
    fail,
    operator_unit_of_work,
    unexpected_failure,
)


async def _seed(*, prune: bool, dry_run: bool) -> SeedReport:
    async with operator_unit_of_work() as uow:
        return await RbacSeeder(uow).seed(prune=prune, commit=not dry_run)


def _print(report: SeedReport, dry_run: bool) -> None:
    sections = [
        ("Roles created", report.roles_created),
        ("Roles updated", report.roles_updated),
        ("Permissions created", report.permissions_created),
        ("Permissions updated", report.permissions_updated),
        ("Grants added", report.grants_added),
        ("Grants removed", report.grants_removed),
        ("Extra grants kept (use --prune to remove)", report.extra_grants_kept),
    ]
    print("Schedence role seeding" + (" (DRY RUN - nothing written)" if dry_run else ""))
    for title, items in sections:
        print(f"\n{title}: {len(items)}")
        for item in items:
            print(f"  - {item}")
    print("\nResult:", "changes " + ("would be " if dry_run else "") + "applied" if report.changed else "already up to date")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.seed_roles", description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--debug", action="store_true", help="Show full tracebacks (may include host names)")
    args = parser.parse_args(argv)
    try:
        report = run(_seed(prune=args.prune, dry_run=args.dry_run))
    except DatabaseConfigurationError as exc:
        return fail(str(exc), EXIT_CONFIG)
    except PersistenceConflictError:
        return fail("Another process seeded at the same time. Run the command again.", EXIT_REFUSED)
    except PersistenceError as exc:
        return fail(f"Database error: {exc}", EXIT_REFUSED)
    except (SQLAlchemyError, OSError) as exc:
        if args.debug:
            raise
        return database_failure(exc)
    except Exception as exc:
        if args.debug:
            raise
        return unexpected_failure(exc)
    _print(report, args.dry_run)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
