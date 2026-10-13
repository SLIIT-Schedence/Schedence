"""Unit tests for the read-only data sources (no database needed)."""

from __future__ import annotations

import datetime as dt
import inspect
from collections import Counter

import pytest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from app.seed.generator import generate_dataset
from app.sources import DatabaseSource, DatasetSource, ExamDataSource

DATA = generate_dataset()


def _signature(function) -> list[tuple]:
    return [(p.name, p.kind, p.default) for p in inspect.signature(function).parameters.values()]


@pytest.mark.parametrize("cls", [DatabaseSource, DatasetSource])
def test_source_implements_the_interface(cls) -> None:
    for name, method in inspect.getmembers(ExamDataSource, inspect.isfunction):
        if name.startswith("_"):
            continue
        implementation = getattr(cls, name)
        assert inspect.iscoroutinefunction(implementation), name
        assert _signature(implementation) == _signature(method), name


@pytest.fixture
def source() -> DatasetSource:
    return DatasetSource(DATA)


# --- DatasetSource ------------------------------------------------------------------


async def test_modules_are_sorted_and_counted_from_registrations(source: DatasetSource) -> None:
    modules = await source.list_modules()
    assert len(modules) == 42
    assert [m.module_code for m in modules] == sorted(m.module_code for m in DATA.modules)
    counts = Counter(r.module_code for r in DATA.registrations)
    assert all(m.candidate_count == counts[m.module_code] for m in modules)
    assert sum(m.candidate_count for m in modules) == 1700


async def test_module_keeps_every_specialization(source: DatasetSource) -> None:
    modules = await source.list_modules()
    assert sum(len(m.specializations) for m in modules) == 91
    assert all(list(m.specializations) == sorted(m.specializations) for m in modules)


async def test_module_filters(source: DatasetSource) -> None:
    year2 = await source.list_modules(academic_year=2)
    assert year2 and all(m.academic_year == 2 for m in year2)
    cyber = await source.list_modules(specialization="CYBER")
    assert cyber and all("CYBER" in m.specializations for m in cyber)
    hybrid = await source.list_modules(exam_type="hybrid")
    assert len(hybrid) == 19
    assert await source.list_modules(academic_year=1, exam_type="written", specialization="NOPE") == []


async def test_get_module(source: DatasetSource) -> None:
    module = await source.get_module("IT3041")
    assert module is not None and module.module_name == "Information Retrieval and Web Analytics"
    assert await source.get_module("XX0000") is None


async def test_students_page(source: DatasetSource) -> None:
    first = await source.list_students(limit=50)
    second = await source.list_students(limit=50, offset=50)
    assert first.total == second.total == 390
    assert len(first.items) == len(second.items) == 50
    assert first.items[-1].student_id < second.items[0].student_id
    last = await source.list_students(limit=50, offset=380)
    assert len(last.items) == 10
    everything = await source.list_students()
    assert len(everything.items) == 390 and everything.limit is None


async def test_student_filters(source: DatasetSource) -> None:
    page = await source.list_students(academic_year=1, specialization="IT")
    assert page.total == 35
    assert all(s.academic_year == 1 and s.specialization == "IT" for s in page.items)


async def test_registration_filters_and_paging(source: DatasetSource) -> None:
    repeat = await source.list_registrations(registration_type="repeat")
    assert repeat.total == 63
    by_module = await source.list_registrations(module_code="IT1010", limit=10)
    assert by_module.total == 111 and len(by_module.items) == 10
    student = DATA.registrations[0].student_id
    mine = await source.list_registrations(student_id=student)
    assert mine.items and all(r.student_id == student for r in mine.items)


async def test_venues_and_blocks(source: DatasetSource) -> None:
    assert len(await source.list_venues()) == 9
    labs = await source.list_venues(venue_type="computer_lab")
    assert len(labs) == 4
    blocks = await source.list_venue_blocks()
    assert len(blocks) == 27
    assert len(await source.list_venue_blocks(source="maintenance")) == 5
    day = blocks[0].block_date
    same_day = await source.list_venue_blocks(date_from=day, date_to=day)
    assert same_day and all(b.block_date == day for b in same_day)


async def test_exam_period_has_exclusions_and_exam_days(source: DatasetSource) -> None:
    periods = await source.list_exam_periods()
    assert len(periods) == 1
    period = periods[0]
    assert [e.excluded_date for e in period.exclusions] == [dt.date(2026, 11, 24)]
    assert len(period.exam_days) == 9
    assert dt.date(2026, 11, 24) not in period.exam_days
    assert all(day.weekday() < 5 for day in period.exam_days)
    assert await source.get_exam_period(period.period_code) == period
    assert await source.get_exam_period("UNKNOWN") is None


# --- DatabaseSource (statement checks with a recording session) ----------------------


class _EmptyResult:
    def all(self):
        return []

    def one_or_none(self):
        return None

    def scalar_one(self):
        return 0

    def __iter__(self):
        return iter(())


class RecordingSession:
    """Stands in for AsyncSession: records statements, returns empty results."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return _EmptyResult()


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


async def test_database_source_only_runs_selects() -> None:
    session = RecordingSession()
    src = DatabaseSource(session)  # type: ignore[arg-type]
    await src.list_modules(academic_year=1, specialization="IT", exam_type="written")
    await src.get_module("IT1010")
    await src.list_students(academic_year=1, specialization="IT", limit=10, offset=20)
    await src.get_student("IT30000001")
    await src.list_registrations(student_id="IT30000001", module_code="IT1010", registration_type="normal", limit=5)
    await src.list_venues(venue_type="exam_hall")
    await src.list_venue_blocks(venue_id="V-LAB1", source="maintenance", date_from=dt.date(2026, 11, 16))
    await src.list_exam_periods()
    await src.get_exam_period("PP1-2026-S2-FINAL")

    assert session.statements
    for statement in session.statements:
        assert isinstance(statement, Select)
        assert _sql(statement).lstrip().upper().startswith("SELECT")
        assert "examinations." in _sql(statement)


async def test_database_source_pages_are_ordered_and_limited() -> None:
    session = RecordingSession()
    page = await DatabaseSource(session).list_students(specialization="SE", limit=10, offset=20)  # type: ignore[arg-type]
    count_sql, page_sql = (_sql(s) for s in session.statements)
    assert "count(*)" in count_sql and "specialization" in count_sql
    assert "ORDER BY examinations.seed_students.student_id" in page_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql
    assert (page.total, page.limit, page.offset, page.items) == (0, 10, 20, ())


async def test_database_source_never_reads_legacy_specialization_column() -> None:
    session = RecordingSession()
    await DatabaseSource(session).list_modules()  # type: ignore[arg-type]
    assert "seed_modules.specialization" not in _sql(session.statements[0])
