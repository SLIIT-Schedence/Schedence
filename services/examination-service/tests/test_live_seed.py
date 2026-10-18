"""Opt-in seed idempotency test against the real configured database.

Skipped unless requested:  python -m pytest --live
Requires the migration to be applied. Everything runs inside one transaction
that is ROLLED BACK at the end, so the shared database is left unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from schedence_db import create_db_engine, dispose_engine, load_settings

from app.seed.generator import generate_dataset
from app.seed.seeder import missing_tables, seed_dataset

pytestmark = pytest.mark.live


async def test_live_seed_twice_is_idempotent_and_rolled_back() -> None:
    engine = create_db_engine(load_settings(), direct=True, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint")
                if await missing_tables(session):
                    pytest.skip("seed tables missing; run 'alembic upgrade head' first")
                data = generate_dataset()

                await seed_dataset(session, data, dry_run=False)
                second = await seed_dataset(session, data, dry_run=False)

                assert second.total_writes == 0
                counts = {c.table: c.unchanged for c in second.changes}
                assert counts["seed_students"] == len(data.students)
                assert counts["seed_registrations"] == len(data.registrations)
                assert second.registration_counts == data.registration_counts()
                await session.close()
            finally:
                await transaction.rollback()
    finally:
        await dispose_engine(engine)
