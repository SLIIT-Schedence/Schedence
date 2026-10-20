"""Read and assign the institution that owns the imported examination dataset.

Assign it once on a new database (migration 0012 fills it in on an existing one)::

    python -m app.dataset_owner --institution-id <uuid> --dry-run
    python -m app.dataset_owner --institution-id <uuid>

The institution ID is the one the Identity Service gives the institution. An
existing owner is never replaced unless ``--replace`` is given. Credentials and
other ``.env`` values are never printed.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from app.db.dataset_models import ExamDatasetOwner
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

EXIT_OK, EXIT_DB, EXIT_CONFIG, EXIT_REFUSED = 0, 1, 2, 3


async def dataset_owner(session: AsyncSession) -> UUID | None:
    """``None`` when no institution owns the dataset yet."""
    return (await session.execute(select(ExamDatasetOwner.institution_id))).scalar_one_or_none()


async def _assign(settings, institution_id: UUID, *, replace: bool, dry_run: bool) -> tuple[UUID | None, bool]:
    engine = create_db_engine(settings, direct=True, poolclass=NullPool)
    try:
        async with session_scope(create_session_factory(engine), commit=not dry_run) as session:
            row = (await session.execute(select(ExamDatasetOwner).with_for_update())).scalar_one_or_none()
            current = row.institution_id if row else None
            if current == institution_id or (current is not None and not replace):
                return current, False
            if row is None:
                session.add(ExamDatasetOwner(institution_id=institution_id, reason="Assigned with app.dataset_owner"))
            else:
                row.institution_id = institution_id
                row.reason = "Replaced with app.dataset_owner"
            return current, True
    finally:
        await dispose_engine(engine)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.dataset_owner", description=__doc__.splitlines()[0])
    parser.add_argument("--institution-id", type=UUID, required=True, help="Identity Service institution ID.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing, different owner.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing.")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Path to a .env file (default: $SCHEDENCE_ENV_FILE, else the nearest .env).")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file, db_application_name="examination-service-dataset-owner")
    except DatabaseConfigurationError as exc:
        print(exc)
        return EXIT_CONFIG
    try:
        current, changed = run(_assign(settings, args.institution_id, replace=args.replace, dry_run=args.dry_run))
    except (SQLAlchemyError, OSError) as exc:
        print(f"Database error: {describe_db_error(exc)}")
        return EXIT_DB
    if not changed and current != args.institution_id:
        print("Another institution already owns the dataset. Use --replace to change it.")
        return EXIT_REFUSED
    if not changed:
        print("No change: this institution already owns the dataset.")
    else:
        print(("DRY RUN - would assign" if args.dry_run else "Assigned") + " the dataset to the given institution.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
