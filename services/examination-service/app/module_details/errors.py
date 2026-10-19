"""Module examination details errors as HTTP responses. Messages are fixed and safe to show.

``detail`` is always a list of ``{"rule", "message"}``, like the other 409/422 answers.
Rules: NOT_FOUND (404); VERSION, MODULE_LOCKED, BUSY (409); MODULE_DETAILS,
MODULE_ELIGIBILITY, CYCLE_MAPPING (422, every problem listed).
"""

from __future__ import annotations

from fastapi import HTTPException, status


class ModuleDetailsError(HTTPException):
    def __init__(self, status_code: int, rule: str, message: str, problems: list[dict[str, str]] = ()) -> None:
        super().__init__(status_code, [{"rule": rule, "message": message}, *problems])
        self.rule = rule


def not_found(module_code: str) -> ModuleDetailsError:
    return ModuleDetailsError(status.HTTP_404_NOT_FOUND, "NOT_FOUND", f"Module {module_code} not found")


def conflict(rule: str, message: str, problems: list[dict[str, str]] = ()) -> ModuleDetailsError:
    return ModuleDetailsError(status.HTTP_409_CONFLICT, rule, message, problems)


def invalid(problems: list[dict[str, str]]) -> ModuleDetailsError:
    first, *rest = problems
    return ModuleDetailsError(status.HTTP_422_UNPROCESSABLE_CONTENT, first["rule"], first["message"], rest)


def busy() -> ModuleDetailsError:
    return conflict("BUSY", "This module or a timetable that uses it is being changed. Try again.")
