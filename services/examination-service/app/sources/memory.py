"""``ExamDataSource`` backed by an in-memory synthetic ``Dataset``.

Gives the same answers as ``DatabaseSource`` for the same data, without a
database. Used by unit and API tests, and usable by the scheduler's tests.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections import Counter
from collections.abc import Iterable
from typing import TypeVar

from app.seed.generator import exam_days
from app.seed.records import Dataset
from app.sources.types import (
    ExamPeriod,
    ExamPeriodExclusion,
    Module,
    Page,
    Registration,
    Student,
    Venue,
    VenueBlock,
)

T = TypeVar("T")


class DatasetSource:
    def __init__(self, data: Dataset):
        specs = data.specializations_by_module()
        counts = Counter(r.module_code for r in data.registrations)
        self._modules = sorted(
            (
                Module(
                    **dataclasses.asdict(m),
                    specializations=tuple(sorted(specs.get(m.module_code, ()))),
                    candidate_count=counts.get(m.module_code, 0),
                )
                for m in data.modules
            ),
            key=lambda m: m.module_code,
        )
        self._students = sorted(
            (Student(**dataclasses.asdict(s)) for s in data.students), key=lambda s: s.student_id
        )
        self._registrations = sorted(
            (Registration(**dataclasses.asdict(r)) for r in data.registrations),
            key=lambda r: (r.student_id, r.module_code),
        )
        self._venues = sorted((Venue(**dataclasses.asdict(v)) for v in data.venues), key=lambda v: v.venue_id)
        self._blocks = sorted(
            (VenueBlock(**dataclasses.asdict(b)) for b in data.venue_blocks),
            key=lambda b: (b.venue_id, b.block_date, b.start_time),
        )
        exclusions = sorted(
            (ExamPeriodExclusion(**dataclasses.asdict(e)) for e in data.exam_period_exclusions),
            key=lambda e: (e.period_code, e.excluded_date),
        )
        self._periods = sorted(
            (
                ExamPeriod(
                    **dataclasses.asdict(p),
                    exclusions=tuple(e for e in exclusions if e.period_code == p.period_code),
                    exam_days=exam_days(p, exclusions),
                )
                for p in data.exam_periods
            ),
            key=lambda p: p.period_code,
        )

    async def list_modules(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        exam_type: str | None = None,
    ) -> list[Module]:
        return [
            m
            for m in self._modules
            if (academic_year is None or m.academic_year == academic_year)
            and (specialization is None or specialization in m.specializations)
            and (exam_type is None or m.exam_type == exam_type)
        ]

    async def get_module(self, module_code: str) -> Module | None:
        return next((m for m in self._modules if m.module_code == module_code), None)

    async def list_students(
        self,
        *,
        academic_year: int | None = None,
        specialization: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Student]:
        return _page(
            (
                s
                for s in self._students
                if (academic_year is None or s.academic_year == academic_year)
                and (specialization is None or s.specialization == specialization)
            ),
            limit,
            offset,
        )

    async def get_student(self, student_id: str) -> Student | None:
        return next((s for s in self._students if s.student_id == student_id), None)

    async def list_registrations(
        self,
        *,
        student_id: str | None = None,
        module_code: str | None = None,
        registration_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[Registration]:
        return _page(
            (
                r
                for r in self._registrations
                if (student_id is None or r.student_id == student_id)
                and (module_code is None or r.module_code == module_code)
                and (registration_type is None or r.registration_type == registration_type)
            ),
            limit,
            offset,
        )

    async def list_venues(self, *, venue_type: str | None = None) -> list[Venue]:
        return [v for v in self._venues if venue_type is None or v.venue_type == venue_type]

    async def list_venue_blocks(
        self,
        *,
        venue_id: str | None = None,
        source: str | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> list[VenueBlock]:
        return [
            b
            for b in self._blocks
            if (venue_id is None or b.venue_id == venue_id)
            and (source is None or b.source == source)
            and (date_from is None or b.block_date >= date_from)
            and (date_to is None or b.block_date <= date_to)
        ]

    async def list_exam_periods(self) -> list[ExamPeriod]:
        return list(self._periods)

    async def get_exam_period(self, period_code: str) -> ExamPeriod | None:
        return next((p for p in self._periods if p.period_code == period_code), None)


def _page(items: Iterable[T], limit: int | None, offset: int) -> Page[T]:
    matched = list(items)
    end = None if limit is None else offset + limit
    return Page(items=tuple(matched[offset:end]), total=len(matched), limit=limit, offset=offset)
