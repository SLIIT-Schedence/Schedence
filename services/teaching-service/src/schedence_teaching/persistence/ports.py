"""Database-independent repository interfaces for a future application layer."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from schedence_teaching.solver import SolverResult

from .contracts import SnapshotDocument


class PersistenceConflict(ValueError):
    """Safe structural error; no credentials or personal source values."""


@dataclass(frozen=True)
class StoredAttempt:
    run_id: UUID
    validation_report_id: UUID
    version_id: UUID | None
    version_number: int | None
    reused: bool = False


@dataclass(frozen=True)
class VersionSummary:
    id: UUID
    series_id: UUID
    run_id: UUID
    version_number: int
    previous_version_id: UUID | None
    lifecycle_status: str
    scope_completion: str
    academic_complete: bool


class SnapshotRepository(Protocol):
    async def store_snapshot(self, document: SnapshotDocument) -> UUID: ...
    async def get_snapshot(self, snapshot_id: UUID) -> SnapshotDocument: ...


class TimetableRepository(Protocol):
    async def create_series(self, reference: str) -> UUID: ...

    async def append_attempt(self, series_id: UUID, snapshot_id: UUID, result: SolverResult, *,
        expected_previous_version_id: UUID | None,
        created_by_identity_user_id: UUID | None = None) -> StoredAttempt: ...

    async def list_versions(self, series_id: UUID) -> tuple[VersionSummary, ...]: ...

    async def save_validated_result(self, series_id: UUID, snapshot_id: UUID, result: SolverResult, *,
        expected_previous_version_id: UUID | None, logical_status: str = "VALIDATED",
        created_by_identity_user_id: UUID | None = None) -> StoredAttempt: ...

    async def get_series(self, series_id: UUID) -> dict: ...
    async def get_latest_draft(self, series_id: UUID): ...
    async def get_version(self, series_id: UUID, version_number: int): ...
    async def get_timetable(self, version_id: UUID): ...
