"""Saved timetable errors as HTTP responses. Messages are fixed and safe to show."""

from __future__ import annotations

from fastapi import HTTPException, status


class SavedTimetablesNotConfiguredError(HTTPException):
    """The saved timetable tables are missing (migration 0010 not applied)."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE,
                         "Saved examination timetables are not available. Please try again later.")


class SavedTimetableNotFoundError(HTTPException):
    """Unknown timetable, or one of another institution (never revealed)."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "Saved timetable not found.")


class SavedTimetableBusyError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status.HTTP_409_CONFLICT,
                         "Another timetable of this examination cycle is being saved. Try again.")


class TimetableWorkflowError(HTTPException):
    """A status change that is not allowed now (409). ``rule`` says why; ``problems`` add details.

    Rules: STATUS (wrong current status), REVISION (changed since read), INTEGRITY
    (stored content no longer matches), ALREADY_PUBLISHED, STALE_SETTINGS,
    STALE_CAPACITIES, STALE_INPUTS and H1-H11 (fails validation on current inputs).
    """

    def __init__(self, rule: str, message: str, problems: list[dict[str, str]] = ()) -> None:
        super().__init__(status.HTTP_409_CONFLICT, [{"rule": rule, "message": message}, *problems])
        self.rule = rule


class TimetableNotSavedError(HTTPException):
    """Generation gave no independently validated timetable: nothing was saved (422)."""

    def __init__(self, status_: str, problems: list[dict[str, str]]) -> None:
        detail = [{"rule": "NOT_SAVED",
                   "message": f"Not saved: the solver returned no validated timetable (status {status_})."},
                  *problems]
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)
