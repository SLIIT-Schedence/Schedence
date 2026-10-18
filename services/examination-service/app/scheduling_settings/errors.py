"""Scheduling settings errors as HTTP responses. Messages are fixed and safe to show."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.scheduling.contracts import Violation


class SchedulingSettingsNotConfiguredError(HTTPException):
    """The settings tables are missing (migration 0006 not applied)."""

    def __init__(self) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Examination scheduling settings are not available. Please try again later.",
        )


class SchedulingSettingsInvalidStoredError(HTTPException):
    """The saved document no longer passes validation: nothing may schedule with it until it is repaired."""

    def __init__(self) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The saved examination scheduling settings are invalid. An Examination Admin must repair them.",
        )


class SchedulingSettingsConflictError(HTTPException):
    """The settings changed since the caller read them (or were created at the same time)."""

    def __init__(self, detail: str, current_version: int | None = None) -> None:
        if current_version is not None:
            detail = f"{detail} Current version: {current_version}."
        super().__init__(status.HTTP_409_CONFLICT, f"{detail} Reload the settings and try again.")
        self.current_version = current_version


class SchedulingSettingsInvalidError(HTTPException):
    """The settings document failed validation; every problem is listed."""

    def __init__(self, violations: tuple[Violation, ...]) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [{"rule": v.constraint, "message": v.message} for v in violations],
        )
