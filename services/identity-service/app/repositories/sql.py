"""SQLAlchemy (async, psycopg 3) implementations of the repository interfaces."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import Select, delete, distinct, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Grant
from app.models import (
    ACCOUNT_ACTIVE,
    OPEN_PROVISIONING_STATUSES,
    IdentityAuditLog,
    Permission,
    Role,
    RolePermission,
    User,
    UserProvisioning,
    UserRole,
)
from app.repositories.base import (
    AssignmentView,
    AuditQuery,
    PersistenceConflictError,
    PersistenceError,
    ProvisioningQuery,
    UserSearch,
)
from schedence_db import describe_db_error

#: Arbitrary fixed 64-bit key for pg_advisory_xact_lock around SUPER_ADMIN-affecting changes.
PRIVILEGED_CHANGES_LOCK_KEY = 7_311_004_211_000_001


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _page(session: AsyncSession, stmt: Select, offset: int, limit: int, *order_by) -> tuple[list, int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = (await session.execute(stmt.order_by(*order_by).offset(offset).limit(limit))).scalars().all()
    return list(rows), int(total or 0)


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_cognito_sub(self, cognito_sub: str) -> User | None:
        return await self.session.scalar(select(User).where(User.cognito_sub == cognito_sub))

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email.lower()))

    async def add(self, user: User) -> None:
        self.session.add(user)

    async def search(self, criteria: UserSearch) -> tuple[list[User], int]:
        institutions = list(criteria.institution_ids)
        visible = select(UserRole.user_id).where(UserRole.institution_id.in_(institutions))
        stmt = select(User).where(User.id.in_(visible))
        if criteria.role_code:
            with_role = (
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.code == criteria.role_code, UserRole.revoked_at.is_(None), UserRole.institution_id.in_(institutions))
            )
            stmt = stmt.where(User.id.in_(with_role))
        if criteria.status:
            stmt = stmt.where(User.account_status == criteria.status)
        if criteria.query:
            pattern = f"%{_escape_like(criteria.query.strip())}%"
            stmt = stmt.where(or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\")))
        return await _page(self.session, stmt, criteria.offset, criteria.limit, User.full_name, User.id)

    async def is_visible_in(self, user_id: UUID, institution_ids: frozenset[UUID]) -> bool:
        if not institution_ids:
            return False
        institutions = list(institution_ids)
        by_role = select(UserRole.id).where(UserRole.user_id == user_id, UserRole.institution_id.in_(institutions))
        by_provisioning = select(UserProvisioning.id).where(
            UserProvisioning.user_id == user_id, UserProvisioning.institution_id.in_(institutions)
        )
        return bool(await self.session.scalar(select(by_role.exists() | by_provisioning.exists())))


class SqlRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_roles(self) -> list[Role]:
        return list((await self.session.execute(select(Role).order_by(Role.code))).scalars())

    async def list_permissions(self) -> list[Permission]:
        return list((await self.session.execute(select(Permission).order_by(Permission.code))).scalars())

    async def get_roles_by_codes(self, codes: Iterable[str]) -> dict[str, Role]:
        codes = list(set(codes))
        if not codes:
            return {}
        rows = (await self.session.execute(select(Role).where(Role.code.in_(codes)))).scalars()
        return {r.code: r for r in rows}

    async def role_permission_codes(self) -> dict[str, frozenset[str]]:
        rows = await self.session.execute(
            select(Role.code, Permission.code)
            .select_from(Role)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
        )
        result: dict[str, set[str]] = {}
        for role_code, permission_code in rows:
            result.setdefault(role_code, set())
            if permission_code:
                result[role_code].add(permission_code)
        return {k: frozenset(v) for k, v in result.items()}

    async def add_role(self, role: Role) -> None:
        self.session.add(role)

    async def add_permission(self, permission: Permission) -> None:
        self.session.add(permission)

    async def add_role_permission(self, role_id: UUID, permission_id: UUID) -> None:
        self.session.add(RolePermission(role_id=role_id, permission_id=permission_id))

    async def remove_role_permission(self, role_id: UUID, permission_id: UUID) -> None:
        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == permission_id)
        )

    async def get_grants(self, user_id: UUID) -> list[Grant]:
        rows = await self.session.execute(
            select(Role.code, UserRole.institution_id, Permission.code)
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .outerjoin(Permission, Permission.id == RolePermission.permission_id)
            .where(UserRole.user_id == user_id, UserRole.revoked_at.is_(None))
        )
        grouped: dict[tuple[str, UUID], set[str]] = {}
        for role_code, institution_id, permission_code in rows:
            perms = grouped.setdefault((role_code, institution_id), set())
            if permission_code:
                perms.add(permission_code)
        return [Grant(role, institution, frozenset(perms)) for (role, institution), perms in grouped.items()]

    async def list_assignments(self, user_ids: Iterable[UUID], *, active_only: bool = True) -> dict[UUID, list[AssignmentView]]:
        ids = list(set(user_ids))
        result: dict[UUID, list[AssignmentView]] = {i: [] for i in ids}
        if not ids:
            return result
        stmt = select(UserRole, Role).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id.in_(ids))
        if active_only:
            stmt = stmt.where(UserRole.revoked_at.is_(None))
        for assignment, role in await self.session.execute(stmt.order_by(UserRole.assigned_at)):
            result[assignment.user_id].append(
                AssignmentView(
                    assignment_id=assignment.id,
                    user_id=assignment.user_id,
                    role_code=role.code,
                    role_name=role.name,
                    institution_id=assignment.institution_id,
                    assigned_at=assignment.assigned_at,
                    assigned_by_user_id=assignment.assigned_by_user_id,
                    revoked_at=assignment.revoked_at,
                    revoked_by_user_id=assignment.revoked_by_user_id,
                )
            )
        return result

    async def active_assignments_in(self, user_id: UUID, institution_id: UUID) -> list[tuple[UserRole, Role]]:
        rows = await self.session.execute(
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, UserRole.institution_id == institution_id, UserRole.revoked_at.is_(None))
        )
        return [(a, r) for a, r in rows]

    async def add_assignment(self, assignment: UserRole) -> None:
        self.session.add(assignment)

    async def count_active_holders(self, role_code: str, institution_id: UUID) -> int:
        count = await self.session.scalar(
            select(func.count(distinct(UserRole.user_id)))
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                Role.code == role_code,
                UserRole.institution_id == institution_id,
                UserRole.revoked_at.is_(None),
                User.account_status == ACCOUNT_ACTIVE,
            )
        )
        return int(count or 0)

    async def list_active_holders(self, role_code: str) -> list[tuple[User, UUID]]:
        rows = await self.session.execute(
            select(User, UserRole.institution_id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == role_code, UserRole.revoked_at.is_(None))
        )
        return [(u, i) for u, i in rows]

    async def active_role_institutions(self, user_id: UUID, role_code: str) -> list[UUID]:
        rows = await self.session.execute(
            select(UserRole.institution_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, Role.code == role_code, UserRole.revoked_at.is_(None))
        )
        return list(rows.scalars())


class SqlProvisioningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, provisioning_id: UUID) -> UserProvisioning | None:
        return await self.session.get(UserProvisioning, provisioning_id)

    async def get_open_by_email(self, email: str) -> UserProvisioning | None:
        return await self.session.scalar(
            select(UserProvisioning).where(
                UserProvisioning.email == email.lower(), UserProvisioning.status.in_(OPEN_PROVISIONING_STATUSES)
            )
        )

    async def add(self, record: UserProvisioning) -> None:
        self.session.add(record)

    async def list(self, query: ProvisioningQuery) -> tuple[list[UserProvisioning], int]:
        stmt = select(UserProvisioning).where(UserProvisioning.institution_id.in_(list(query.institution_ids)))
        if query.statuses:
            stmt = stmt.where(UserProvisioning.status.in_(list(query.statuses)))
        return await _page(self.session, stmt, query.offset, query.limit, UserProvisioning.created_at.desc(), UserProvisioning.id)

    async def list_for_user(self, user_id: UUID, email: str) -> list[UserProvisioning]:
        rows = await self.session.execute(
            select(UserProvisioning)
            .where(or_(UserProvisioning.user_id == user_id, UserProvisioning.email == email.lower()))
            .order_by(UserProvisioning.created_at.desc())
        )
        return list(rows.scalars())


class SqlAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entry: IdentityAuditLog) -> None:
        self.session.add(entry)

    async def list(self, query: AuditQuery) -> tuple[list[IdentityAuditLog], int]:
        stmt = select(IdentityAuditLog).where(IdentityAuditLog.institution_id.in_(list(query.institution_ids)))
        if query.action:
            stmt = stmt.where(IdentityAuditLog.action == query.action)
        if query.actor_user_id:
            stmt = stmt.where(IdentityAuditLog.actor_user_id == query.actor_user_id)
        if query.target_user_id:
            stmt = stmt.where(IdentityAuditLog.target_user_id == query.target_user_id)
        if query.occurred_from:
            stmt = stmt.where(IdentityAuditLog.occurred_at >= query.occurred_from)
        if query.occurred_to:
            stmt = stmt.where(IdentityAuditLog.occurred_at < query.occurred_to)
        return await _page(self.session, stmt, query.offset, query.limit, IdentityAuditLog.occurred_at.desc(), IdentityAuditLog.id)


class SqlIdentityUnitOfWork:
    """One database transaction per unit of work.

    Autoflush is off so integrity errors surface only from :meth:`commit`,
    where they are translated into :class:`PersistenceConflictError`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        session.sync_session.autoflush = False
        self.users = SqlUserRepository(session)
        self.roles = SqlRoleRepository(session)
        self.provisioning = SqlProvisioningRepository(session)
        self.audit = SqlAuditRepository(session)

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

    async def lock_privileged_changes(self) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PRIVILEGED_CHANGES_LOCK_KEY})

    async def _safe_rollback(self) -> None:
        try:
            await self.session.rollback()
        except Exception:  # the connection may be gone; the session is discarded anyway
            pass
