"""Explicit opt-in live checks; synthetic writes always roll back.

Never enabled just because DATABASE_URL exists. A verified recovery manifest,
explicit target branch, institution and a separate restricted runtime URL are
required. Default/full-suite execution skips these tests without connecting.
"""

from schedence_db.runtime import run
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from schedence_teaching.persistence.application import verify_runtime_security
from schedence_teaching.persistence.models import InputSnapshot, TimetableSeries
from schedence_teaching.persistence.repositories import SqlTeachingRepository
from schedence_teaching.persistence.snapshots import snapshot_payload, content_digest

from test_persistence_contracts import example

pytestmark = pytest.mark.skipif(os.environ.get("SCHEDENCE_TEACHING_DB_TESTS") != "1",
    reason="live database tests require explicit opt-in, verified recovery and restricted runtime credentials")


@asynccontextmanager
async def live_session():
    from schedence_db import load_settings, create_db_engine
    tenant = UUID(os.environ["TEACHING_TEST_INSTITUTION_ID"])
    expected_branch = os.environ["TEACHING_TEST_BRANCH_ID"]
    recovery = json.loads(Path(os.environ["TEACHING_TEST_RECOVERY_MANIFEST"]).read_text(encoding="utf-8"))
    if recovery.get("verified") is not True or recovery.get("target_branch_id") != expected_branch or not recovery.get("recovery_reference"):
        raise AssertionError("verified recovery evidence must match the explicit target branch")
    settings = load_settings(env_prefix="TEACHING_",db_echo=False)
    engine = create_db_engine(settings,hide_parameters=True)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                branch = (await connection.execute(text("SELECT current_setting('neon.branch_id',true)"))).scalar_one()
                if branch != expected_branch:
                    raise AssertionError("runtime connection points to a different Neon branch")
                await connection.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"),{"tenant":str(tenant)})
                session = AsyncSession(bind=connection,expire_on_commit=False)
                try:
                    await verify_runtime_security(session)
                    yield session, tenant
                finally:
                    await session.close()
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await engine.dispose()


def test_live_idempotent_import_and_exact_readback():
    async def execute():
        async with live_session() as (session, tenant):
            document, _ = example()
            document.institution_id = tenant
            # Unique synthetic term prevents accidental identity reuse with a
            # concurrently running test; every inserted row rolls back below.
            document.academic_term_reference = f"SYNTHETIC_ROLLBACK_TEST:{uuid4()}"
            repository = SqlTeachingRepository(session,tenant)
            first = await repository.store_snapshot(document)
            second = await repository.store_snapshot(document)
            restored = await repository.get_snapshot(first)
            assert first == second and content_digest(restored) == content_digest(document)
    run(execute())


def test_live_rls_hides_other_institutions_even_without_repository_filter():
    async def execute():
        async with live_session() as (session,tenant):
            document, _ = example()
            document.institution_id = tenant
            document.academic_term_reference=f"SYNTHETIC_ROLLBACK_TEST:{uuid4()}"
            identifier = await SqlTeachingRepository(session,tenant).store_snapshot(document)
            await session.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"),{"tenant":str(uuid4())})
            assert await session.scalar(select(InputSnapshot).where(InputSnapshot.id==identifier)) is None
    run(execute())


def test_live_rls_rejects_mismatched_institution_insert():
    async def execute():
        async with live_session() as (session,tenant):
            document, _ = example()
            payload = snapshot_payload(document)
            with pytest.raises(DBAPIError) as caught:
                async with session.begin_nested():
                    session.add(InputSnapshot(id=uuid4(),institution_id=uuid4(),schema_version=1,
                        content_sha256=content_digest(payload),academic_term_reference="SYNTHETIC_REJECTED_TEST",
                        timezone_name="Asia/Colombo",calendar_label="PP1_TEST_CALENDAR",
                        expected_occurrences=2,payload=payload))
                    await session.flush()
            assert getattr(caught.value.orig,"sqlstate",None)=="42501"
    run(execute())


def test_live_history_update_is_rejected_by_immutability_trigger():
    async def execute():
        async with live_session() as (session,tenant):
            reference=f"SYNTHETIC_ROLLBACK_TEST:{uuid4()}"
            identifier = await SqlTeachingRepository(session,tenant).create_series(reference)
            # Runtime has the narrow column UPDATE privilege required by series
            # locking. The shared history trigger must still prohibit mutation.
            with pytest.raises(DBAPIError) as caught:
                async with session.begin_nested():
                    await session.execute(update(TimetableSeries).where(TimetableSeries.id==identifier,
                        TimetableSeries.institution_id==tenant).values(series_reference="CHANGED"))
            assert getattr(caught.value.orig,"sqlstate",None)=="55000"
            assert await session.scalar(select(TimetableSeries.series_reference).where(
                TimetableSeries.id==identifier, TimetableSeries.institution_id==tenant))==reference
    run(execute())
