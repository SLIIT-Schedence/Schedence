"""In-memory fixtures for scoring and optimization; never institutional data."""
import datetime as dt
from dataclasses import replace

from app.scheduling.types import DraftTimetable, ExamSession, RoomAllocation, SeatAllocation
from scheduling_support import MONDAY, make_case


async def optimization_case(count=4, difficulties=None, credits=None, **kwargs):
    kwargs.setdefault("end_date", MONDAY + dt.timedelta(days=10))
    kwargs.setdefault("candidates", (("SYNTHETIC",),) * count)
    snapshot, source, store = await make_case(**kwargs)
    snapshot = replace(snapshot, modules=tuple(replace(m,
        difficulty=(difficulties or (5, 5, 1, 1)[:count])[i],
        credits=(credits or (4,) * count)[i]) for i, m in enumerate(snapshot.modules)))
    return snapshot


def timed_draft(snapshot, times):
    """Explicit times; callers intentionally use small capacity-compatible cases."""
    sessions = []
    for m, start in zip(snapshot.modules, times):
        students = sorted(r.student_id for r in snapshot.registrations if r.module_code == m.module_code)
        offset, rooms = 0, []
        for venue in snapshot.capacities.available_venues():
            if venue.venue_type not in snapshot.policy.allowed_venue_types(m.exam_type, m.module_code):
                continue
            selected = students[offset:offset + venue.exam_capacity]
            if selected:
                rooms.append(RoomAllocation(venue.venue_id, tuple(SeatAllocation(s, i + 1) for i, s in enumerate(selected))))
                offset += len(selected)
        sessions.append(ExamSession(m.module_code, m.module_code, snapshot.period.period_code, start,
            start + dt.timedelta(minutes=m.duration_minutes + m.reading_time_minutes), tuple(rooms)))
    return DraftTimetable(snapshot.period.period_code, snapshot.fingerprint, tuple(sessions))


def starts_on(*days):
    return tuple(dt.datetime.combine(MONDAY + dt.timedelta(days=d), dt.time(8, 30)) for d in days)
