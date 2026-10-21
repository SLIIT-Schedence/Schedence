"""SQLAlchemy implementations. ``for_update=True`` takes a row lock (SELECT ... FOR UPDATE)
so concurrent appointment changes for the same staff member or position are serialised."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdministrativePositionType,
    AdministrativeWorkloadPolicy,
    AppointmentAuditLog,
    Staff,
    StaffAdministrativeAppointment,
)
from app.models.teaching_inputs import TEACHING_INPUT_TABLES
from app.repositories.base import PersistenceConflictError, PersistenceError
from schedence_db import describe_db_error


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _page(session: AsyncSession, stmt: Select, offset: int, limit: int, *order_by) -> tuple[list, int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await session.execute(stmt.order_by(*order_by).offset(offset).limit(limit))).scalars().all()
    return list(rows), int(total or 0)


async def _get(session: AsyncSession, model, key: UUID, for_update: bool):
    stmt = select(model).where(model.id == key)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


class SqlStaffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, staff_id: UUID, *, for_update: bool = False) -> Staff | None:
        return await _get(self.session, Staff, staff_id, for_update)

    async def get_by_number(self, institution_id: UUID, staff_number: str) -> Staff | None:
        return await self.session.scalar(select(Staff).where(Staff.institution_id == institution_id, Staff.staff_number == staff_number))

    async def get_many(self, staff_ids: Iterable[UUID]) -> dict[UUID, Staff]:
        ids = list(set(staff_ids))
        if not ids:
            return {}
        return {s.id: s for s in (await self.session.execute(select(Staff).where(Staff.id.in_(ids)))).scalars()}

    async def add(self, staff: Staff) -> None:
        self.session.add(staff)

    async def search(self, institution_ids: frozenset[UUID], query: str | None, offset: int, limit: int) -> tuple[list[Staff], int]:
        stmt = select(Staff).where(Staff.institution_id.in_(list(institution_ids)))
        if query:
            pattern = f"%{_escape_like(query.strip())}%"
            stmt = stmt.where(or_(Staff.full_name.ilike(pattern, escape="\\"), Staff.staff_number.ilike(pattern, escape="\\"),
                                  Staff.email.ilike(pattern, escape="\\")))
        return await _page(self.session, stmt, offset, limit, Staff.full_name, Staff.id)


class SqlPositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, position_id: UUID, *, for_update: bool = False) -> AdministrativePositionType | None:
        return await _get(self.session, AdministrativePositionType, position_id, for_update)

    async def get_by_code(self, institution_id: UUID, code: str) -> AdministrativePositionType | None:
        return await self.session.scalar(select(AdministrativePositionType).where(
            AdministrativePositionType.institution_id == institution_id, AdministrativePositionType.code == code))

    async def get_many(self, position_ids: Iterable[UUID]) -> dict[UUID, AdministrativePositionType]:
        ids = list(set(position_ids))
        if not ids:
            return {}
        rows = await self.session.execute(select(AdministrativePositionType).where(AdministrativePositionType.id.in_(ids)))
        return {p.id: p for p in rows.scalars()}

    async def add(self, position: AdministrativePositionType) -> None:
        self.session.add(position)

    async def list(self, institution_ids: frozenset[UUID], include_inactive: bool, offset: int, limit: int
                   ) -> tuple[list[AdministrativePositionType], int]:
        stmt = select(AdministrativePositionType).where(AdministrativePositionType.institution_id.in_(list(institution_ids)))
        if not include_inactive:
            stmt = stmt.where(AdministrativePositionType.is_active.is_(True))
        return await _page(self.session, stmt, offset, limit, AdministrativePositionType.name, AdministrativePositionType.id)

    async def policies(self, position_ids: Iterable[UUID]) -> dict[UUID, list[AdministrativeWorkloadPolicy]]:
        ids = list(set(position_ids))
        result: dict[UUID, list[AdministrativeWorkloadPolicy]] = {i: [] for i in ids}
        if ids:
            rows = await self.session.execute(
                select(AdministrativeWorkloadPolicy)
                .where(AdministrativeWorkloadPolicy.position_type_id.in_(ids))
                .order_by(AdministrativeWorkloadPolicy.position_type_id, AdministrativeWorkloadPolicy.version)
            )
            for policy in rows.scalars():
                result[policy.position_type_id].append(policy)
        return result

    async def add_policy(self, policy: AdministrativeWorkloadPolicy) -> None:
        self.session.add(policy)


class SqlAppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, appointment_id: UUID, *, for_update: bool = False) -> StaffAdministrativeAppointment | None:
        return await _get(self.session, StaffAdministrativeAppointment, appointment_id, for_update)

    async def add(self, appointment: StaffAdministrativeAppointment) -> None:
        self.session.add(appointment)

    async def for_staff(self, staff_id: UUID) -> list[StaffAdministrativeAppointment]:
        rows = await self.session.execute(select(StaffAdministrativeAppointment)
                                          .where(StaffAdministrativeAppointment.staff_id == staff_id)
                                          .order_by(StaffAdministrativeAppointment.effective_from.desc()))
        return list(rows.scalars())

    async def for_position(self, position_type_id: UUID) -> list[StaffAdministrativeAppointment]:
        rows = await self.session.execute(select(StaffAdministrativeAppointment)
                                          .where(StaffAdministrativeAppointment.position_type_id == position_type_id))
        return list(rows.scalars())


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entry: AppointmentAuditLog) -> None:
        self.session.add(entry)

    async def for_entity(self, entity_type: str, entity_id: UUID) -> list[AppointmentAuditLog]:
        rows = await self.session.execute(select(AppointmentAuditLog)
                                          .where(AppointmentAuditLog.entity_type == entity_type, AppointmentAuditLog.entity_id == entity_id)
                                          .order_by(AppointmentAuditLog.occurred_at))
        return list(rows.scalars())


class SqlTeachingInputRepository:
    """Read-only export of the tables Teaching validates generation inputs against."""

    TABLES = (Staff.__table__, *TEACHING_INPUT_TABLES)
    # One statement for every table: per-table queries cost a Neon round trip each and pushed the export past
    # Teaching's owner timeout. Identifiers come only from the fixed metadata above, never from the request.
    EXPORT = text("SELECT json_build_object(" + ", ".join(
        f"'{t.name}', (SELECT coalesce(json_agg(r ORDER BY r.id), '[]'::json) FROM {t.schema}.{t.name} r"
        " WHERE r.institution_id = :tenant)" for t in TABLES) + ")")

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def export(self, institution_id: UUID) -> dict[str, list[dict]]:
        try:
            # The teaching-input tables use forced institution RLS keyed on this transaction-local setting.
            await self.session.execute(text("SELECT set_config('schedence.institution_id', :tenant, true)"),
                                       {"tenant": str(institution_id)})
            return await self.session.scalar(self.EXPORT, {"tenant": institution_id})
        except (SQLAlchemyError, OSError) as exc:
            raise PersistenceError(describe_db_error(exc)) from None


class SqlAcademicUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        session.sync_session.autoflush = False
        self.staff = SqlStaffRepository(session)
        self.positions = SqlPositionRepository(session)
        self.appointments = SqlAppointmentRepository(session)
        self.audit = SqlAuditRepository(session)
        self.teaching_inputs = SqlTeachingInputRepository(session)

    async def commit(self) -> None:
        await self._translate(self.session.commit())

    async def flush(self) -> None:
        await self._translate(self.session.flush())

    async def _translate(self, operation) -> None:
        try:
            await operation
        except IntegrityError as exc:
            await self._safe_rollback()
            constraint = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
            raise PersistenceConflictError(constraint) from None
        except (SQLAlchemyError, OSError) as exc:
            await self._safe_rollback()
            raise PersistenceError(describe_db_error(exc)) from None

    async def rollback(self) -> None:
        await self._safe_rollback()

    async def _safe_rollback(self) -> None:
        try:
            await self.session.rollback()
        except Exception:
            pass
