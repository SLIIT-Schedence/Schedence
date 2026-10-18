"""Small synthetic source fixtures for scheduler behavior tests (never a database)."""

from __future__ import annotations

import datetime as dt

from app.scheduling import SchedulingPolicy, load_snapshot
from app.seating.memory import MemorySeatingStore
from app.seed.records import (
    Dataset, ExamPeriod, ExamPeriodExclusion, Module, ModuleSpecialization, Registration, Student, Venue,
)
from app.sources import DatasetSource

MONDAY = dt.date(2026, 11, 16)


async def make_case(
    *, candidates=(("IT30000001", "IT30000002"),), exam_types=None, durations=None, readings=None,
    venue_types=("lecture_hall", "lecture_hall"), full_capacities=None, percentage=50,
    blocks=(), policy=None, start_date=MONDAY, end_date=MONDAY, excluded=(),
    include_saturdays=False, include_sundays=False, day_start=dt.time(8, 30), day_end=dt.time(17, 30),
):
    count = len(candidates)
    exam_types = exam_types or ("written",) * count
    durations = durations or (60,) * count
    readings = readings or (0,) * count
    full_capacities = full_capacities or (4,) * len(venue_types)
    codes = tuple(f"IT{1001 + i}" for i in range(count))
    data = Dataset(
        modules=tuple(Module(code, f"Synthetic exam {i}", 1, 2, 3, 1, exam_types[i], durations[i],
                             readings[i] > 0, readings[i]) for i, code in enumerate(codes)),
        module_specializations=tuple(ModuleSpecialization(code, "IT") for code in codes),
        students=tuple(Student(s, 1, "IT") for s in sorted({s for group in candidates for s in group})),
        registrations=tuple(Registration(s, code, "repeat" if i else "normal")
                            for i, (code, group) in enumerate(zip(codes, candidates)) for s in group),
        venues=tuple(Venue(f"R{i + 1}", f"Room {i + 1}", kind, "R", 1, full_capacities[i],
                           full_capacities[i] // 2) for i, kind in enumerate(venue_types)),
        venue_blocks=tuple(blocks),
        exam_periods=(ExamPeriod("TEST", "SYNTHETIC TEST PERIOD", "2026/2027", 2, start_date, end_date,
                                day_start, day_end, include_saturdays, include_sundays),),
        exam_period_exclusions=tuple(ExamPeriodExclusion("TEST", day, "SIMULATED exclusion") for day in excluded),
    )
    source = DatasetSource(data)
    store = MemorySeatingStore(percentage)
    snapshot = await load_snapshot(source, store, period_code="TEST", policy=policy or SchedulingPolicy())
    return snapshot, source, store
