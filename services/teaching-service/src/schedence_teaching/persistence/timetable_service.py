"""Transactional draft persistence and institution-scoped read application layer.

No environment/configuration loading, automatic execution, API, or solver call.
Callers supply an authenticated institution and an explicit shared session factory.
"""

from contextlib import asynccontextmanager

from sqlalchemy import text

from .application import verify_runtime_security
from .database_read import check_read_security, inspect_owner_security
from .repositories import SqlTeachingRepository, _tenant, teaching_unit_of_work


async def persist_generation_result(factory, institution_id, series_id, snapshot_id, result, *,
                                    expected_previous_version_id, logical_status="VALIDATED",
                                    created_by_identity_user_id=None):
    """Commit run, report, version, bookings and coverage together, or roll back.

    This is an explicit write operation, never called by the preview CLI. Live
    production invocation remains an operational review decision.
    """
    async with teaching_unit_of_work(factory, institution_id) as repository:
        return await persist_in_transaction(repository, series_id, snapshot_id, result,
            expected_previous_version_id=expected_previous_version_id, logical_status=logical_status,
            created_by_identity_user_id=created_by_identity_user_id)


async def persist_in_transaction(repository, series_id, snapshot_id, result, *,
                                 expected_previous_version_id, logical_status="VALIDATED",
                                 created_by_identity_user_id=None):
    """Same reviewed save boundary, allowing atomic durable job finalization.

    The caller owns commit/rollback through teaching_unit_of_work. No additional
    solver or lifecycle privilege is introduced by composing this transaction.
    """
    await verify_runtime_security(repository._session)
    role, tables = await inspect_owner_security(repository._session, "teaching")
    check_read_security(role, tables, "teaching", require_read_only=False)
    return await repository.save_validated_result(series_id, snapshot_id, result,
        expected_previous_version_id=expected_previous_version_id, logical_status=logical_status,
        created_by_identity_user_id=created_by_identity_user_id)


@asynccontextmanager
async def timetable_reader(factory, institution_id):
    """Read application facade: SELECT-only transaction, forced tenant RLS.

    The yielded repository exposes the detached retrieval operations. PostgreSQL
    itself rejects writes even if a caller accidentally invokes a write method.
    """
    from schedence_db import session_scope
    tenant = _tenant(institution_id)
    async with session_scope(factory, commit=False) as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(text("SELECT set_config('schedence.institution_id',:tenant,true)"), {"tenant": str(tenant)})
        role, tables = await inspect_owner_security(session, "teaching")
        check_read_security(role, tables, "teaching")
        yield SqlTeachingRepository(session, tenant)
