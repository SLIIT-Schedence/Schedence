"""In-memory doubles: academic repositories and a fake Identity Service (httpx MockTransport)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import jwt
from sqlalchemy import inspect as sa_inspect

from app.models import (
    AdministrativePositionType,
    AdministrativeWorkloadPolicy,
    AppointmentAuditLog,
    Staff,
    StaffAdministrativeAppointment,
)
from app.repositories.base import PersistenceConflictError

_TABLES = ("staff", "positions", "policies", "appointments")


def _columns(obj: Any) -> dict[str, Any]:
    return {a.key: getattr(obj, a.key) for a in sa_inspect(type(obj)).column_attrs}


class FakeAcademicStore:
    def __init__(self) -> None:
        self.staff: dict[UUID, Staff] = {}
        self.positions: dict[UUID, AdministrativePositionType] = {}
        self.policies: dict[UUID, AdministrativeWorkloadPolicy] = {}
        self.appointments: dict[UUID, StaffAdministrativeAppointment] = {}
        self.audit: list[AppointmentAuditLog] = []
        self.row_locks: list[tuple[str, UUID]] = []
        self.snapshot()

    def snapshot(self) -> None:
        self._saved = {t: {k: (o, _columns(o)) for k, o in getattr(self, t).items()} for t in _TABLES}
        self._saved_audit = list(self.audit)
        self._mark_flushed()

    def restore(self) -> None:
        for table in _TABLES:
            restored = {}
            for key, (obj, values) in self._saved[table].items():
                for attr, value in values.items():
                    setattr(obj, attr, value)
                restored[key] = obj
            setattr(self, table, restored)
        self.audit = list(self._saved_audit)
        self._mark_flushed()

    def _mark_flushed(self) -> None:
        self._flushed_staff, self._flushed_positions = set(self.staff), set(self.positions)

    def flush(self) -> None:
        """Worst-case unit-of-work order: rows may only reference parents flushed earlier (no relationship())."""
        for p in self.policies.values():
            if p.position_type_id not in self._flushed_positions:
                raise PersistenceConflictError("fk_administrative_workload_policies_position_type_id")
        for a in self.appointments.values():
            if a.staff_id not in self._flushed_staff:
                raise PersistenceConflictError("fk_staff_administrative_appointments_staff_id")
            if a.position_type_id not in self._flushed_positions:
                raise PersistenceConflictError("fk_staff_administrative_appointments_position_type_id")
        self._mark_flushed()

    def check_constraints(self) -> None:
        def unique(values: list, name: str) -> None:
            if len(values) != len(set(values)):
                raise PersistenceConflictError(name)

        unique([(s.institution_id, s.staff_number) for s in self.staff.values()], "uq_staff_institution_id_staff_number")
        unique([(p.institution_id, p.code) for p in self.positions.values()], "uq_administrative_position_types_institution_id_code")
        unique([(p.position_type_id, p.version) for p in self.policies.values()], "uq_administrative_workload_policies_position_type_id_version")


class _Repo:
    def __init__(self, store: FakeAcademicStore) -> None:
        self.s = store


class FakeStaffRepository(_Repo):
    async def get(self, staff_id: UUID, *, for_update: bool = False) -> Staff | None:
        if for_update:
            self.s.row_locks.append(("staff", staff_id))
        return self.s.staff.get(staff_id)

    async def get_by_number(self, institution_id: UUID, staff_number: str) -> Staff | None:
        return next((s for s in self.s.staff.values() if s.institution_id == institution_id and s.staff_number == staff_number), None)

    async def get_many(self, staff_ids: Iterable[UUID]) -> dict[UUID, Staff]:
        return {i: self.s.staff[i] for i in set(staff_ids) if i in self.s.staff}

    async def add(self, staff: Staff) -> None:
        self.s.staff[staff.id] = staff

    async def search(self, institution_ids: frozenset[UUID], query: str | None, offset: int, limit: int) -> tuple[list[Staff], int]:
        rows = [s for s in self.s.staff.values() if s.institution_id in institution_ids
                and (not query or query.lower() in f"{s.full_name} {s.staff_number} {s.email or ''}".lower())]
        rows.sort(key=lambda s: s.full_name)
        return rows[offset: offset + limit], len(rows)


class FakePositionRepository(_Repo):
    async def get(self, position_id: UUID, *, for_update: bool = False) -> AdministrativePositionType | None:
        if for_update:
            self.s.row_locks.append(("position", position_id))
        return self.s.positions.get(position_id)

    async def get_by_code(self, institution_id: UUID, code: str) -> AdministrativePositionType | None:
        return next((p for p in self.s.positions.values() if p.institution_id == institution_id and p.code == code), None)

    async def get_many(self, position_ids: Iterable[UUID]) -> dict[UUID, AdministrativePositionType]:
        return {i: self.s.positions[i] for i in set(position_ids) if i in self.s.positions}

    async def add(self, position: AdministrativePositionType) -> None:
        self.s.positions[position.id] = position

    async def list(self, institution_ids: frozenset[UUID], include_inactive: bool, offset: int, limit: int):
        rows = [p for p in self.s.positions.values() if p.institution_id in institution_ids and (include_inactive or p.is_active)]
        rows.sort(key=lambda p: p.name)
        return rows[offset: offset + limit], len(rows)

    async def policies(self, position_ids: Iterable[UUID]) -> dict[UUID, list[AdministrativeWorkloadPolicy]]:
        ids = set(position_ids)
        result: dict[UUID, list[AdministrativeWorkloadPolicy]] = {i: [] for i in ids}
        for policy in sorted(self.s.policies.values(), key=lambda p: p.version):
            if policy.position_type_id in ids:
                result[policy.position_type_id].append(policy)
        return result

    async def add_policy(self, policy: AdministrativeWorkloadPolicy) -> None:
        self.s.policies[policy.id] = policy


class FakeAppointmentRepository(_Repo):
    async def get(self, appointment_id: UUID, *, for_update: bool = False) -> StaffAdministrativeAppointment | None:
        return self.s.appointments.get(appointment_id)

    async def add(self, appointment: StaffAdministrativeAppointment) -> None:
        self.s.appointments[appointment.id] = appointment

    async def for_staff(self, staff_id: UUID) -> list[StaffAdministrativeAppointment]:
        return sorted((a for a in self.s.appointments.values() if a.staff_id == staff_id), key=lambda a: a.effective_from, reverse=True)

    async def for_position(self, position_type_id: UUID) -> list[StaffAdministrativeAppointment]:
        return [a for a in self.s.appointments.values() if a.position_type_id == position_type_id]


class FakeAuditRepository(_Repo):
    async def add(self, entry: AppointmentAuditLog) -> None:
        self.s.audit.append(entry)

    async def for_entity(self, entity_type: str, entity_id: UUID) -> list[AppointmentAuditLog]:
        return [e for e in self.s.audit if e.entity_type == entity_type and e.entity_id == entity_id]


class FakeAcademicUnitOfWork:
    def __init__(self, store: FakeAcademicStore) -> None:
        self.store = store
        self.staff = FakeStaffRepository(store)
        self.positions = FakePositionRepository(store)
        self.appointments = FakeAppointmentRepository(store)
        self.audit = FakeAuditRepository(store)

    async def commit(self) -> None:
        try:
            self.store.flush()
            self.store.check_constraints()
        except PersistenceConflictError:
            self.store.restore()
            raise
        self.store.snapshot()

    async def flush(self) -> None:
        try:
            self.store.flush()
        except PersistenceConflictError:
            self.store.restore()
            raise

    async def rollback(self) -> None:
        self.store.restore()


def uow_factory(store: FakeAcademicStore):
    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeAcademicUnitOfWork]:
        uow = FakeAcademicUnitOfWork(store)
        try:
            yield uow
        finally:
            await uow.rollback()  # uncommitted work is discarded, as with the real session

    return factory


class FakeIdentityService:
    """Answers GET /api/v1/identity/me/permissions like the real Identity Service and records every request."""

    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.requests: list[tuple[str, str]] = []
        self.down = False

    def register(self, permissions_by_institution: dict[UUID, tuple[list[str], list[str]]], *, status: str = "ACTIVE") -> tuple[str, UUID]:
        sub, user_id = str(uuid.uuid4()), uuid.uuid4()
        self.accounts[sub] = {"user_id": user_id, "status": status, "institutions": permissions_by_institution}
        return sub, user_id

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        if self.down:
            raise httpx.ConnectError("identity service down", request=request)
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        sub = jwt.decode(token, options={"verify_signature": False}).get("sub")  # the real service verifies; the fake only routes
        account = self.accounts.get(sub)
        if account is None:
            return httpx.Response(403, json={"error": {"code": "ACCOUNT_NOT_PROVISIONED", "message": "x"}})
        if account["status"] != "ACTIVE":
            return httpx.Response(403, json={"error": {"code": "ACCOUNT_DISABLED", "message": "x"}})
        return httpx.Response(200, json={
            "user_id": str(account["user_id"]),
            "cognito_sub": sub,
            "account_status": "ACTIVE",
            "institutions": [{"institution_id": str(i), "roles": roles, "permissions": perms}
                             for i, (roles, perms) in account["institutions"].items()],
        })
