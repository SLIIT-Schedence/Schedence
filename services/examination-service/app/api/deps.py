"""Shared dependencies and input validation rules for the API routes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Literal

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import AuthContext
from app.auth.dependencies import get_auth
from app.database import get_session, get_write_session
from app.exam_periods import DatabaseExamPeriodStore, ExamPeriodService
from app.module_details import DatabaseModuleDetailsStore, ModuleDetailsService
from app.generation.service import TimetableGenerator
from app.saved_timetables import DatabaseTimetableStore, TimetableStore
from app.scheduling.inputs import load_database_snapshot
from app.db.seed_models import BLOCK_SOURCES, EXAM_TYPES, REGISTRATION_TYPES, VENUE_TYPES
from app.scheduling_settings.service import SchedulingSettingsService
from app.scheduling_settings.store import DatabaseSchedulingSettingsStore, SchedulingSettingsStore
from app.seating.service import SeatingService
from app.seating.store import DatabaseSeatingStore, SeatingRuleStore
from app.sources import DatabaseSource, ExamDataSource
from schedence_db import session_scope

# Identifier formats. Anything else is rejected with 422 before a query runs.
MODULE_CODE = r"^[A-Z]{2,4}[0-9]{4}$"  # e.g. IT3041
STUDENT_ID = r"^[A-Z]{2}[0-9]{8}$"  # e.g. IT30315600
SPECIALIZATION = r"^[A-Z_]{2,30}$"  # e.g. SE, COMPUTER_SCIENCE
# Campus codes are kept exactly: e.g. F1301, F1301+F1302, CyberSecLab, "Robotics Lab".
VENUE_ID = r"^[A-Za-z0-9+](?:[A-Za-z0-9+ -]{0,14}[A-Za-z0-9+])?$"
PERIOD_CODE = r"^[A-Z0-9-]{1,32}$"  # e.g. PP1-2026-S2-FINAL

ExamType = Literal[EXAM_TYPES]
RegistrationType = Literal[REGISTRATION_TYPES]
VenueType = Literal[VENUE_TYPES]
BlockSource = Literal[BLOCK_SOURCES]

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500

AcademicYear = Annotated[int | None, Query(ge=1, le=4, description="Academic year 1-4")]
Specialization = Annotated[str | None, Query(pattern=SPECIALIZATION, description="e.g. IT, SE, DS, CYBER")]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size")]
Offset = Annotated[int, Query(ge=0, le=1_000_000, description="Rows to skip")]


def get_source(session: Annotated[AsyncSession, Depends(get_session)]) -> ExamDataSource:
    return DatabaseSource(session)


SourceDep = Annotated[ExamDataSource, Depends(get_source)]


def get_seating_store(session: Annotated[AsyncSession, Depends(get_session)]) -> SeatingRuleStore:
    """Seating rules on the request's read-only session (the same session as ``get_source``)."""
    return DatabaseSeatingStore(session)


def get_seating_reader(
    source: SourceDep, store: Annotated[SeatingRuleStore, Depends(get_seating_store)]
) -> SeatingService:
    return SeatingService(store, source)


def get_seating_writer(session: Annotated[AsyncSession, Depends(get_write_session)]) -> SeatingService:
    """Rules and venues on one writable session (read-only for dry runs)."""
    return SeatingService(DatabaseSeatingStore(session), DatabaseSource(session))


SeatingReaderDep = Annotated[SeatingService, Depends(get_seating_reader)]
SeatingWriterDep = Annotated[SeatingService, Depends(get_seating_writer)]


def get_exam_period_writer(session: Annotated[AsyncSession, Depends(get_write_session)]) -> ExamPeriodService:
    """Period changes on a writable session (read-only for dry runs); the service commits explicitly."""
    return ExamPeriodService(DatabaseExamPeriodStore(session))


ExamPeriodWriterDep = Annotated[ExamPeriodService, Depends(get_exam_period_writer)]


def get_module_details_writer(session: Annotated[AsyncSession, Depends(get_write_session)]) -> ModuleDetailsService:
    """Module detail changes on a writable session (read-only for dry runs); the service commits explicitly."""
    return ModuleDetailsService(DatabaseModuleDetailsStore(session))


ModuleDetailsWriterDep = Annotated[ModuleDetailsService, Depends(get_module_details_writer)]


def get_scheduling_settings_store(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SchedulingSettingsStore:
    """Scheduling settings on the request's read-only session."""
    return DatabaseSchedulingSettingsStore(session)


def get_scheduling_settings_reader(
    store: Annotated[SchedulingSettingsStore, Depends(get_scheduling_settings_store)],
    auth: Annotated[AuthContext, Depends(get_auth)],
) -> SchedulingSettingsService:
    # The institution in which the caller's examination permissions are checked.
    return SchedulingSettingsService(store, auth.settings.institution_id)


def get_scheduling_settings_writer(
    session: Annotated[AsyncSession, Depends(get_write_session)],
    auth: Annotated[AuthContext, Depends(get_auth)],
) -> SchedulingSettingsService:
    """A writable session (read-only for dry runs); the service commits explicitly."""
    return SchedulingSettingsService(DatabaseSchedulingSettingsStore(session), auth.settings.institution_id)


SchedulingSettingsReaderDep = Annotated[SchedulingSettingsService, Depends(get_scheduling_settings_reader)]
SchedulingSettingsWriterDep = Annotated[SchedulingSettingsService, Depends(get_scheduling_settings_writer)]


def get_timetable_generator(
    request: Request, auth: Annotated[AuthContext, Depends(get_auth)],
) -> TimetableGenerator:
    """Reads finish (and their transactions close) before the solver starts; nothing is written."""
    state = request.app.state

    async def load_settings():
        async with session_scope(state.session_factory, commit=False) as session:  # read-only, closed at once
            return await SchedulingSettingsService(
                DatabaseSchedulingSettingsStore(session), auth.settings.institution_id).active()

    async def load_snapshot(period_code, policy, module_codes):
        # One REPEATABLE READ, READ ONLY transaction for every input table.
        return await load_database_snapshot(state.engine, period_code=period_code, policy=policy,
                                            module_codes=module_codes, from_cycle=True)

    return TimetableGenerator(load_snapshot, load_settings, request.app.state.generation_lock)


TimetableGeneratorDep = Annotated[TimetableGenerator, Depends(get_timetable_generator)]


def get_saved_timetable_reader(session: Annotated[AsyncSession, Depends(get_session)]) -> TimetableStore:
    """Saved timetables on the request's read-only session."""
    return DatabaseTimetableStore(session)


TimetableStoreOpener = Callable[[], AbstractAsyncContextManager[TimetableStore]]


def get_saved_timetable_writer(request: Request) -> TimetableStoreOpener:
    """Opens a writable session only when called: after the solver, never during it. The caller commits."""

    @asynccontextmanager
    async def open_store() -> AsyncIterator[TimetableStore]:
        async with session_scope(request.app.state.write_session_factory, commit=False) as session:
            yield DatabaseTimetableStore(session)

    return open_store


SavedTimetableReaderDep = Annotated[TimetableStore, Depends(get_saved_timetable_reader)]
SavedTimetableWriterDep = Annotated[TimetableStoreOpener, Depends(get_saved_timetable_writer)]
