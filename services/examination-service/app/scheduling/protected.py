"""Protected timetables of other examination cycles, and the independent cross-timetable check.

A timetable is *protected* while it is APPROVED or PUBLISHED: it is fixed, other cycles may
already depend on it, and nothing about it may change. A new timetable of another cycle must
fit around it:

* X1: a venue is never used while a protected session holds it (half-open intervals);
* X2: a student who sits an exam in a protected timetable keeps the minimum break to every
  exam of the new timetable (same rule as H1, applied across cycles; repeat students included).

DRAFT timetables do not protect anything (see the README): they are alternatives that nothing
reserves, and abandoned drafts would otherwise block rooms for ever. They are only counted.

This module has no OR-Tools import: :func:`cross_timetable_violations` is the independent check
used by the validator, Save Draft, Approve and Publish. Messages never contain student ids.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from app.scheduling.contracts import Violation

PROTECTED_STATUSES = ("APPROVED", "PUBLISHED")
CROSS_RULES = ("X1", "X2")
MAX_LISTED = 25  # clashes listed one by one per rule; the rest is summarised


@dataclass(frozen=True)
class ProtectedSession:
    session_id: str
    module_code: str
    start: dt.datetime
    end: dt.datetime
    venue_ids: tuple[str, ...]
    # Only students who are candidates of the timetable being built (never returned by the API).
    student_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtectedTimetable:
    timetable_id: int
    period_code: str
    period_name: str
    status: str
    draft_number: int
    revision: int
    start_date: dt.date
    end_date: dt.date
    sessions: tuple[ProtectedSession, ...]  # only the sessions inside the new timetable's date range


@dataclass(frozen=True)
class ProtectedSchedule:
    timetables: tuple[ProtectedTimetable, ...] = ()
    overlapping_drafts: int = 0  # DRAFT timetables of other cycles with overlapping dates (not blocking)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        content = [[t.timetable_id, t.revision, t.status, [
            [s.session_id, s.start.isoformat(), s.end.isoformat(), list(s.venue_ids),
             hashlib.sha256(",".join(s.student_ids).encode()).hexdigest()] for s in t.sessions]]
            for t in self.timetables]
        object.__setattr__(self, "fingerprint", hashlib.sha256(
            json.dumps(content, separators=(",", ":")).encode()).hexdigest())

    @property
    def sessions(self) -> Iterable[tuple[ProtectedTimetable, ProtectedSession]]:
        return ((t, s) for t in self.timetables for s in t.sessions)


def _recorded_range(inputs: object) -> tuple[dt.date, dt.date] | None:
    """The exam calendar saved with a timetable (``inputs.start_date`` / ``end_date``), if readable."""
    if not isinstance(inputs, dict):
        return None
    try:
        start, end = (value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
                      for value in (inputs["start_date"], inputs["end_date"]))
    except (KeyError, ValueError):
        return None
    return (start, end) if start <= end else None


def overlaps(first: tuple[dt.date, dt.date], second: tuple[dt.date, dt.date]) -> bool:
    return first[0] <= second[1] and second[0] <= first[1]


def count_overlapping_drafts(rows: Iterable[dict], window: tuple[dt.date, dt.date]) -> int:
    """Draft rows (``inputs``) whose saved calendar overlaps the window; unreadable calendars are not counted."""
    return sum(1 for row in rows if (r := _recorded_range(row.get("inputs"))) is not None and overlaps(r, window))


def build_protected_schedule(headers: Iterable[dict], sessions: Iterable[dict], rooms: Iterable[dict],
                             seats: Iterable[dict], *, window: tuple[dt.date, dt.date],
                             student_ids: Collection[str], overlapping_drafts: int = 0,
                             minimum_break_minutes: int = 0) -> ProtectedSchedule:
    """Shape stored rows (any store) into the protected schedule for a date window.

    ``headers`` are APPROVED / PUBLISHED timetables of other cycles. A timetable is kept when its saved
    calendar overlaps the window or one of its sessions falls inside it. Of its sessions only those
    inside the window (including the student-break buffer at either boundary) are kept,
    with their venues and the seats of ``student_ids`` only.
    """
    gap = dt.timedelta(minutes=minimum_break_minutes)
    first = dt.datetime.combine(window[0], dt.time()) - gap
    last = dt.datetime.combine(window[1] + dt.timedelta(days=1), dt.time()) + gap
    wanted = set(student_ids)
    inside: dict[tuple[int, str], dict] = {}
    for s in sessions:
        if s["starts_at"] < last and s["ends_at"] > first:
            inside[s["timetable_id"], s["session_id"]] = s
    venues: dict[tuple[int, str], set[str]] = defaultdict(set)
    for r in rooms:
        if (r["timetable_id"], r["session_id"]) in inside:
            venues[r["timetable_id"], r["session_id"]].add(r["venue_id"])
    students: dict[tuple[int, str], set[str]] = defaultdict(set)
    for seat in seats:
        if seat["student_id"] in wanted and (seat["timetable_id"], seat["session_id"]) in inside:
            students[seat["timetable_id"], seat["session_id"]].add(seat["student_id"])
    timetables = []
    for h in sorted(headers, key=lambda h: h["timetable_id"]):
        own = sorted((s for (tid, _), s in inside.items() if tid == h["timetable_id"]),
                     key=lambda s: (s["starts_at"], s["session_id"]))
        recorded = _recorded_range(h.get("inputs"))
        if recorded is None and own:
            recorded = (min(s["starts_at"].date() for s in own), max(s["ends_at"].date() for s in own))
        if not own and (recorded is None or not overlaps(recorded, window)):
            continue
        timetables.append(ProtectedTimetable(
            timetable_id=h["timetable_id"], period_code=h["period_code"], period_name=h["period_name"],
            status=h["status"], draft_number=h["draft_number"], revision=h["revision"],
            start_date=recorded[0], end_date=recorded[1],
            sessions=tuple(ProtectedSession(
                s["session_id"], s["module_code"], s["starts_at"], s["ends_at"],
                tuple(sorted(venues[h["timetable_id"], s["session_id"]])),
                tuple(sorted(students[h["timetable_id"], s["session_id"]]))) for s in own)))
    return ProtectedSchedule(tuple(timetables), overlapping_drafts)


def _label(start: dt.datetime, end: dt.datetime) -> str:
    return f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"


def cross_timetable_violations(protected: ProtectedSchedule | None, sessions, *,
                               minimum_break_minutes: int) -> tuple[Violation, ...]:
    """X1 / X2 clashes of ``sessions`` (a DraftTimetable's sessions) with the protected timetables.

    Overlaps are half-open intervals. A student clash is a protected exam that overlaps an exam of the
    new timetable or ends less than ``minimum_break_minutes`` before it starts (or starts less than that
    after it ends). Students are only counted, never named.
    """
    if protected is None or not protected.timetables:
        return ()
    gap = dt.timedelta(minutes=minimum_break_minutes)
    by_venue: dict[str, list] = defaultdict(list)
    by_student: dict[str, list] = defaultdict(list)
    for t, p in protected.sessions:
        for venue in p.venue_ids:
            by_venue[venue].append((t, p))
        for student in p.student_ids:
            by_student[student].append((t, p))
    rooms: dict[tuple, set[str]] = defaultdict(set)
    students: dict[tuple, set[str]] = defaultdict(set)
    for s in sessions:
        for room in s.rooms:
            for t, p in by_venue.get(room.venue_id, ()):
                if s.start < p.end and p.start < s.end:
                    rooms[s.session_id, s.start, s.end, t.timetable_id, t.period_code, t.status, p.module_code,
                          room.venue_id, p.start, p.end].add(room.venue_id)
            for seat in room.seats:
                for t, p in by_student.get(seat.student_id, ()):
                    if s.start < p.end + gap and p.start < s.end + gap:
                        students[s.session_id, s.module_code, t.timetable_id, t.period_code, t.status,
                                 p.module_code].add(seat.student_id)
    errors = [Violation("X1", f"Session {key[0]} ({_label(key[1], key[2])}) uses venue {key[7]}, which "
                              f"{key[6]} holds at {_label(key[8], key[9])} in cycle {key[4]} ({key[5]}, "
                              f"timetable {key[3]}).")
              for key in sorted(rooms, key=str)]
    errors += [Violation("X2", f"{len(who)} student(s) sitting {key[1]} (session {key[0]}) also sit {key[5]} in cycle "
                               f"{key[3]} ({key[4]}, timetable {key[2]}) overlapping or within the "
                               f"{minimum_break_minutes}-minute break.")
               for key, who in sorted(students.items(), key=lambda item: str(item[0]))]
    return _bounded(errors)


def _bounded(errors: list[Violation]) -> tuple[Violation, ...]:
    kept, more = [], Counter()
    seen = Counter()
    for e in errors:
        seen[e.constraint] += 1
        if seen[e.constraint] <= MAX_LISTED:
            kept.append(e)
        else:
            more[e.constraint] += 1
    kept += [Violation(rule, f"{count} more {rule} clash(es) with protected timetables are not listed.")
             for rule, count in sorted(more.items())]
    return tuple(kept)


def shared_student_count(protected: ProtectedSchedule | None) -> int:
    """Candidates of the new timetable who also sit a protected exam in the date range."""
    if protected is None:
        return 0
    return len({s for _, p in protected.sessions for s in p.student_ids})
