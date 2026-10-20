"""Exam period management errors as HTTP responses. Messages are fixed and safe to show.

``detail`` is always a list of ``{"rule", "message"}``, like the other 409/422 answers.
"""

from __future__ import annotations

from fastapi import HTTPException, status


class ExamPeriodError(HTTPException):
    """``rule`` says why; ``problems`` add details.

    Rules: NOT_FOUND (404); PERIOD_EXISTS, VERSION, PERIOD_LOCKED, BUSY (409);
    PERIOD_YEAR, PERIOD_DATES, PERIOD_HOURS, PERIOD_EXCLUSIONS, PERIOD_CALENDAR,
    CYCLE_MAPPING (422, every problem listed).
    """

    def __init__(self, status_code: int, rule: str, message: str, problems: list[dict[str, str]] = ()) -> None:
        super().__init__(status_code, [{"rule": rule, "message": message}, *problems])
        self.rule = rule


def not_found(period_code: str) -> ExamPeriodError:
    return ExamPeriodError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", f"Exam period {period_code} not found")


def conflict(rule: str, message: str, problems: list[dict[str, str]] = ()) -> ExamPeriodError:
    return ExamPeriodError(status.HTTP_409_CONFLICT, rule, message, problems)


def invalid(problems: list[dict[str, str]]) -> ExamPeriodError:
    first, *rest = problems
    return ExamPeriodError(status.HTTP_422_UNPROCESSABLE_CONTENT, first["rule"], first["message"], rest)


def busy() -> ExamPeriodError:
    return conflict("BUSY", "This exam period or one of its timetables is being changed. Try again.")
