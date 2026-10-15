"""Seating rule errors as HTTP responses. Messages are fixed and safe to show."""

from __future__ import annotations

from fastapi import HTTPException, status


class SeatingNotConfiguredError(HTTPException):
    """The seating tables or the default rule are missing (migration 0005 not applied)."""

    def __init__(self) -> None:
        super().__init__(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Examination seating rules are not configured. Please try again later.",
        )


class SeatingNotFoundError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, detail)


class SeatingConflictError(HTTPException):
    """The rule changed since the caller read it (or was created at the same time)."""

    def __init__(self, detail: str, current_version: int | None = None) -> None:
        if current_version is not None:
            detail = f"{detail} Current version: {current_version}."
        super().__init__(status.HTTP_409_CONFLICT, f"{detail} Reload the settings and try again.")
        self.current_version = current_version
