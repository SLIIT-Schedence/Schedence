"""Interface to Member 2's PUBLISHED timetable, used before a duty roster can be published.

The saved Examination Timetable store is read through its existing contract. Demo
references are never converted into real timetable references. The unavailable
provider remains the fail-closed default outside configured database mode.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.scheduling.types import DraftTimetable
from app.saved_timetables.store import TimetableStore
from app.saved_timetables.content import content_fingerprint, rebuild_timetable


class PublishedTimetableUnavailable(Exception):
    """The published-timetable source cannot be reached or does not exist yet."""


@dataclass(frozen=True)
class PublishedTimetable:
    reference: str  # the publication reference issued by the timetable owner
    fingerprint: str  # DraftTimetable.input_fingerprint of what was published
    draft: DraftTimetable


class PublishedTimetableProvider(Protocol):
    async def get_published(self, institution_id: uuid.UUID, period_code: str) -> PublishedTimetable | None:
        """The published timetable of the period, or ``None`` when nothing is published.
        Raise :class:`PublishedTimetableUnavailable` when the source cannot answer."""


class UnavailablePublishedTimetableProvider:
    """Default: the published-timetable contract is not available, so publishing fails closed."""

    async def get_published(self, institution_id: uuid.UUID, period_code: str) -> PublishedTimetable | None:
        raise PublishedTimetableUnavailable(
            "The published timetable contract is not available, so a duty roster cannot be checked "
            "against a published timetable and cannot be published.")


class SavedPublishedTimetableProvider:
    """Read-only adapter: only an intact, owner-published timetable is accepted."""

    def __init__(self, open_store: Callable[[], AbstractAsyncContextManager[TimetableStore]]) -> None:
        self.open_store = open_store

    async def get_published(self, institution_id: uuid.UUID, period_code: str) -> PublishedTimetable | None:
        try:
            async with self.open_store() as store:
                header = await store.published(institution_id, period_code)
                if header is None:
                    return None
                detail = await store.get(institution_id, header["timetable_id"])
                if (detail is None or detail.header["status"] != "PUBLISHED"
                        or detail.header["period_code"] != period_code
                        or detail.header["revision"] != header["revision"]):
                    raise PublishedTimetableUnavailable("The published timetable changed while it was being checked. Try again.")
                header = detail.header
                if not all(header.get(k) for k in ("validation_passed", "content_fingerprint", "published_at", "published_by", "input_fingerprint")):
                    raise PublishedTimetableUnavailable("The timetable has no verified publication or content fingerprint.")
                sessions, rooms, seats = await store.allocations(header["timetable_id"])
                if not sessions or content_fingerprint(sessions, rooms, seats) != header["content_fingerprint"]:
                    raise PublishedTimetableUnavailable("The published timetable's stored content failed its integrity check.")
                return PublishedTimetable(f"EXAM-TIMETABLE:{header['timetable_id']}", header["input_fingerprint"],
                                          rebuild_timetable(header, sessions, rooms, seats))
        except (HTTPException, SQLAlchemyError, OSError, TimeoutError) as exc:
            raise PublishedTimetableUnavailable("The saved published timetable cannot be verified right now. Try again later.") from exc


def timetable_signature(draft: DraftTimetable) -> tuple[tuple[str, str, dt.datetime, dt.datetime, tuple[str, ...]], ...]:
    """What a duty roster depends on: each session, its module, times and rooms (not the students)."""
    return tuple(sorted((s.session_id, s.module_code, s.start, s.end, tuple(sorted(r.venue_id for r in s.rooms)))
                        for s in draft.sessions))
