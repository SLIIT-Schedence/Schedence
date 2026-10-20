"""Response models of the cross-cycle (protected timetable) summary. Counts and venue slots only; no student data."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProtectedTimetableOut(_Out):
    timetable_id: int
    period_code: str
    period_name: str
    status: Literal["APPROVED", "PUBLISHED"]
    draft_number: int
    start_date: dt.date
    end_date: dt.date
    sessions_in_range: int = Field(description="Sessions overlapping this timetable's dates or the student-break boundary buffer")
    occupied_venue_slots: int = Field(description="Venue and session pairs those sessions hold")


class OccupiedSlotOut(_Out):
    timetable_id: int
    period_code: str
    module_code: str
    venue_id: str
    date: dt.date
    start_time: dt.time
    end_time: dt.time


class CrossTimetableOut(_Out):
    """Which APPROVED / PUBLISHED timetables of other cycles were applied as fixed constraints, and the outcome."""

    protected_timetables: list[ProtectedTimetableOut]
    occupied_slots: list[OccupiedSlotOut] = Field(description="Earliest first, at most 100")
    occupied_slot_total: int
    shared_students: int = Field(description="Candidates of this timetable who also sit an exam in a protected one (count only)")
    student_conflict_status: Literal["none", "clear", "blocked"] = Field(
        description="none: no protected timetable overlaps; clear: no student clash; blocked: a clash or no timetable")
    venue_conflicts: int
    student_conflicts: int
    overlapping_drafts: int = Field(description="DRAFT timetables of other cycles with overlapping dates. They do not block.")
    protected_fingerprint: str
    blocking_issues: list[str]
