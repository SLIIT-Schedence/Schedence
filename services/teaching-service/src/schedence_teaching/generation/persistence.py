"""Explicit test-only bridge to the reviewed transactional draft service."""

from schedence_teaching.api.errors import ApiError


class NoDraftPersistence:
    mode = "DISABLED"

    async def preflight(self, request):
        if request.timetable_series_id is not None:
            raise ApiError(400, "PERSISTENCE_DISABLED", "Draft persistence is disabled in this execution environment.")

    async def save(self, work, result, job_id, actor):
        return None, None


class IsolatedDraftPersistence:
    """Fail closed before connecting: no production/Neon write adapter is exposed.

    This step deliberately does not implement a production write flag. A future
    durable worker deployment and reviewed security control must supply it.
    """
    mode = "ISOLATED_POSTGRESQL"

    def __init__(self, url):
        from sqlalchemy.engine import make_url
        parsed = make_url(url)
        if (parsed.host != "127.0.0.1" or parsed.database != "schedence_step12b4_test" or
                not parsed.port or parsed.query):
            raise ValueError("generation writes require the dedicated isolated loopback database")
        self._url = parsed

    def _engine(self):
        from sqlalchemy.ext.asyncio import create_async_engine
        return create_async_engine(self._url, echo=False, hide_parameters=True)

    async def preflight(self, request):
        if request.timetable_series_id is None:
            return
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from schedence_teaching.persistence.timetable_service import timetable_reader
        engine = self._engine()
        try:
            async with timetable_reader(async_sessionmaker(engine, expire_on_commit=False), request.institution_id) as repo:
                await repo.get_series(request.timetable_series_id)
                latest = await repo.get_latest_draft(request.timetable_series_id)
                if (latest.version["id"] if latest else None) != request.expected_previous_version_id:
                    raise ApiError(409, "STALE_VERSION", "The timetable history has changed. Select its latest draft.")
        finally:
            await engine.dispose()

    async def save(self, work, result, job_id, actor):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from schedence_teaching.persistence.application import verify_runtime_security
        from schedence_teaching.persistence.repositories import teaching_unit_of_work
        from schedence_teaching.persistence.timetable_service import persist_generation_result
        engine = self._engine()
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with teaching_unit_of_work(factory, work.request.institution_id) as repo:
                await verify_runtime_security(repo._session)
                if (await repo._session.scalar(text("SELECT current_database()")) != "schedence_step12b4_test" or
                        await repo._session.scalar(text("SELECT current_setting('neon.project_id',true)")) is not None):
                    raise ValueError("generation writes are restricted to disposable PostgreSQL")
                snapshot = await repo.store_snapshot(work.document)
                series = work.request.timetable_series_id or await repo.create_series(f"DEVELOPMENT_GENERATION:{job_id}")
            stored = await persist_generation_result(factory, work.request.institution_id, series, snapshot, result,
                expected_previous_version_id=work.request.expected_previous_version_id,
                logical_status="VALIDATED", created_by_identity_user_id=actor)
            return stored.version_id, snapshot
        finally:
            await engine.dispose()
