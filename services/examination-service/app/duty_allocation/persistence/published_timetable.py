"""Interface to Member 2's PUBLISHED timetable, used before a duty roster can be published.

No published-timetable contract exists in this service yet (Member 2 publishes no reference
for ``m3_demo``). Instead of inventing a reference, the default provider FAILS CLOSED:
publishing is refused with ``PUBLISHED_TIMETABLE_UNAVAILABLE``. When the contract exists,
implement :class:`PublishedTimetableProvider` and pass it to the roster service.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.scheduling.types import DraftTimetable


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


def timetable_signature(draft: DraftTimetable) -> tuple[tuple[str, str, dt.datetime, dt.datetime, tuple[str, ...]], ...]:
    """What a duty roster depends on: each session, its module, times and rooms (not the students)."""
    return tuple(sorted((s.session_id, s.module_code, s.start, s.end, tuple(sorted(r.venue_id for r in s.rooms)))
                        for s in draft.sessions))
