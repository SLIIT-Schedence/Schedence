"""Repository interfaces for the Academic Data Service (SQL in production, in-memory fakes in tests)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from app.models import (
    AdministrativePositionType,
    AdministrativeWorkloadPolicy,
    AppointmentAuditLog,
    Staff,
    StaffAdministrativeAppointment,
)


class PersistenceError(Exception):
    """A database operation failed (fixed, safe message)."""


class PersistenceConflictError(PersistenceError):
    def __init__(self, constraint: str | None = None) -> None:
        self.constraint = constraint
        super().__init__(f"integrity constraint violated ({constraint or 'unknown'})")


class StaffRepository(Protocol):
    async def get(self, staff_id: UUID, *, for_update: bool = False) -> Staff | None: ...
    async def get_by_number(self, institution_id: UUID, staff_number: str) -> Staff | None: ...
    async def get_many(self, staff_ids: Iterable[UUID]) -> dict[UUID, Staff]: ...
    async def add(self, staff: Staff) -> None: ...
    async def search(self, institution_ids: frozenset[UUID], query: str | None, offset: int, limit: int) -> tuple[list[Staff], int]: ...


class PositionRepository(Protocol):
    async def get(self, position_id: UUID, *, for_update: bool = False) -> AdministrativePositionType | None: ...
    async def get_by_code(self, institution_id: UUID, code: str) -> AdministrativePositionType | None: ...
    async def get_many(self, position_ids: Iterable[UUID]) -> dict[UUID, AdministrativePositionType]: ...
    async def add(self, position: AdministrativePositionType) -> None: ...
    async def list(self, institution_ids: frozenset[UUID], include_inactive: bool, offset: int, limit: int
                   ) -> tuple[list[AdministrativePositionType], int]: ...
    async def policies(self, position_ids: Iterable[UUID]) -> dict[UUID, list[AdministrativeWorkloadPolicy]]:
        """Policy versions per position, oldest first."""
        ...
    async def add_policy(self, policy: AdministrativeWorkloadPolicy) -> None: ...


class AppointmentRepository(Protocol):
    async def get(self, appointment_id: UUID, *, for_update: bool = False) -> StaffAdministrativeAppointment | None: ...
    async def add(self, appointment: StaffAdministrativeAppointment) -> None: ...
    async def for_staff(self, staff_id: UUID) -> list[StaffAdministrativeAppointment]: ...
    async def for_position(self, position_type_id: UUID) -> list[StaffAdministrativeAppointment]: ...


class AuditRepository(Protocol):
    async def add(self, entry: AppointmentAuditLog) -> None: ...
    async def for_entity(self, entity_type: str, entity_id: UUID) -> list[AppointmentAuditLog]: ...


class AcademicUnitOfWork(Protocol):
    staff: StaffRepository
    positions: PositionRepository
    appointments: AppointmentRepository
    audit: AuditRepository

    async def commit(self) -> None: ...

    async def flush(self) -> None:
        """Send pending changes now. The models have foreign keys but no relationship(), so parent rows
        must be flushed before rows that reference them are added."""
        ...

    async def rollback(self) -> None: ...
