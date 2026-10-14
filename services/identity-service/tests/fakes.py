"""In-memory test doubles. No database, no AWS.

``FakeStore`` mimics the parts of PostgreSQL the services rely on: unique
constraints are checked at commit time, and a failed commit or a rollback
restores the last committed state.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import inspect as sa_inspect

from app.core.principal import Grant
from app.integrations.cognito import (
    STATUS_CONFIRMED,
    STATUS_FORCE_CHANGE_PASSWORD,
    CognitoError,
    CognitoUser,
    CognitoUserExistsError,
    CognitoUserNotFoundError,
)
from app.models import (
    ACCOUNT_ACTIVE,
    OPEN_PROVISIONING_STATUSES,
    IdentityAuditLog,
    Permission,
    Role,
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

_ENTITY_STORES = ("users", "roles", "permissions", "user_roles", "provisioning")


def _columns(obj: Any) -> dict[str, Any]:
    return {attr.key: getattr(obj, attr.key) for attr in sa_inspect(type(obj)).column_attrs}


class FakeStore:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.roles: dict[UUID, Role] = {}
        self.permissions: dict[UUID, Permission] = {}
        self.role_permissions: set[tuple[UUID, UUID]] = set()
        self.user_roles: dict[UUID, UserRole] = {}
        self.provisioning: dict[UUID, UserProvisioning] = {}
        self.audit: list[IdentityAuditLog] = []
        self.fail_next_commits: list[PersistenceError] = []
        self.commits = 0
        self.locks = 0
        self.flushes = 0
        self._snapshot: dict[str, Any] = {}
        self.snapshot()

    # -- transaction simulation
    def snapshot(self) -> None:
        self._snapshot = {name: {k: (o, _columns(o)) for k, o in getattr(self, name).items()} for name in _ENTITY_STORES}
        self._snapshot["role_permissions"] = set(self.role_permissions)
        self._snapshot["audit"] = list(self.audit)
        self._mark_flushed()

    def restore(self) -> None:
        for name in _ENTITY_STORES:
            restored = {}
            for key, (obj, values) in self._snapshot[name].items():
                for attr, value in values.items():
                    setattr(obj, attr, value)
                restored[key] = obj
            setattr(self, name, restored)
        self.role_permissions = set(self._snapshot["role_permissions"])
        self.audit = list(self._snapshot["audit"])
        self._mark_flushed()

    def _mark_flushed(self) -> None:
        self._flushed_users = set(self.users)
        self._flushed_roles = set(self.roles)
        self._flushed_permissions = set(self.permissions)

    def flush(self) -> None:
        """Emulate the worst case of SQLAlchemy's unit of work.

        The models declare foreign keys but no relationship(), so the ORM may
        INSERT/UPDATE rows of different tables in any order within one flush.
        A row may therefore only reference parents that were flushed *earlier*.
        """
        users, roles, permissions = self._flushed_users, self._flushed_roles, self._flushed_permissions

        def require(value: UUID | None, known: set[UUID], constraint: str) -> None:
            if value is not None and value not in known:
                raise PersistenceConflictError(constraint)

        for a in self.user_roles.values():
            require(a.user_id, users, "fk_user_roles_user_id_users")
            require(a.role_id, roles, "fk_user_roles_role_id_roles")
            require(a.assigned_by_user_id, users, "fk_user_roles_assigned_by_user_id_users")
            require(a.revoked_by_user_id, users, "fk_user_roles_revoked_by_user_id_users")
        for p in self.provisioning.values():
            require(p.user_id, users, "fk_user_provisioning_user_id_users")
            require(p.requested_role_id, roles, "fk_user_provisioning_requested_role_id_roles")
            require(p.requested_by_user_id, users, "fk_user_provisioning_requested_by_user_id_users")
        for e in self.audit:
            require(e.actor_user_id, users, "fk_identity_audit_logs_actor_user_id_users")
            require(e.target_user_id, users, "fk_identity_audit_logs_target_user_id_users")
        for role_id, permission_id in self.role_permissions:
            require(role_id, roles, "fk_role_permissions_role_id_roles")
            require(permission_id, permissions, "fk_role_permissions_permission_id_permissions")
        self._mark_flushed()
        self.flushes += 1

    def check_constraints(self) -> None:
        def unique(values: Iterable[Any], name: str) -> None:
            values = [v for v in values if v is not None]
            if len(values) != len(set(values)):
                raise PersistenceConflictError(name)

        unique((u.email for u in self.users.values()), "uq_users_email")
        unique((u.cognito_sub for u in self.users.values()), "uq_users_cognito_sub")
        unique((r.code for r in self.roles.values()), "uq_roles_code")
        unique((p.code for p in self.permissions.values()), "uq_permissions_code")
        unique((p.email for p in self.provisioning.values() if p.status in OPEN_PROVISIONING_STATUSES), "uq_user_provisioning_open_email")
        unique(((a.user_id, a.role_id, a.institution_id) for a in self.user_roles.values() if a.revoked_at is None),
               "uq_user_roles_active_assignment")

    # -- helpers for tests
    def role(self, code: str) -> Role:
        return next(r for r in self.roles.values() if r.code == code)

    def active_role_codes(self, user_id: UUID, institution_id: UUID | None = None) -> set[str]:
        return {self.roles[a.role_id].code for a in self.user_roles.values()
                if a.user_id == user_id and a.revoked_at is None and (institution_id is None or a.institution_id == institution_id)}

    def actions(self) -> list[str]:
        return [e.action for e in self.audit]


class FakeUserRepository:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    async def get(self, user_id: UUID) -> User | None:
        return self.s.users.get(user_id)

    async def get_by_cognito_sub(self, cognito_sub: str) -> User | None:
        return next((u for u in self.s.users.values() if u.cognito_sub == cognito_sub), None)

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.s.users.values() if u.email == email.lower()), None)

    async def add(self, user: User) -> None:
        self.s.users[user.id] = user

    async def search(self, criteria: UserSearch) -> tuple[list[User], int]:
        visible = {a.user_id for a in self.s.user_roles.values() if a.institution_id in criteria.institution_ids}
        users = [u for u in self.s.users.values() if u.id in visible]
        if criteria.role_code:
            role_ids = {r.id for r in self.s.roles.values() if r.code == criteria.role_code}
            holders = {a.user_id for a in self.s.user_roles.values()
                       if a.role_id in role_ids and a.revoked_at is None and a.institution_id in criteria.institution_ids}
            users = [u for u in users if u.id in holders]
        if criteria.status:
            users = [u for u in users if u.account_status == criteria.status]
        if criteria.query:
            q = criteria.query.lower()
            users = [u for u in users if q in u.email.lower() or q in u.full_name.lower()]
        users.sort(key=lambda u: (u.full_name, str(u.id)))
        return users[criteria.offset: criteria.offset + criteria.limit], len(users)

    async def is_visible_in(self, user_id: UUID, institution_ids: frozenset[UUID]) -> bool:
        return any(a.user_id == user_id and a.institution_id in institution_ids for a in self.s.user_roles.values()) or any(
            p.user_id == user_id and p.institution_id in institution_ids for p in self.s.provisioning.values())


class FakeRoleRepository:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    async def list_roles(self) -> list[Role]:
        return sorted(self.s.roles.values(), key=lambda r: r.code)

    async def list_permissions(self) -> list[Permission]:
        return sorted(self.s.permissions.values(), key=lambda p: p.code)

    async def get_roles_by_codes(self, codes: Iterable[str]) -> dict[str, Role]:
        codes = set(codes)
        return {r.code: r for r in self.s.roles.values() if r.code in codes}

    async def role_permission_codes(self) -> dict[str, frozenset[str]]:
        result: dict[str, set[str]] = {r.code: set() for r in self.s.roles.values()}
        for role_id, permission_id in self.s.role_permissions:
            result[self.s.roles[role_id].code].add(self.s.permissions[permission_id].code)
        return {k: frozenset(v) for k, v in result.items()}

    async def add_role(self, role: Role) -> None:
        self.s.roles[role.id] = role

    async def add_permission(self, permission: Permission) -> None:
        self.s.permissions[permission.id] = permission

    async def add_role_permission(self, role_id: UUID, permission_id: UUID) -> None:
        self.s.role_permissions.add((role_id, permission_id))

    async def remove_role_permission(self, role_id: UUID, permission_id: UUID) -> None:
        self.s.role_permissions.discard((role_id, permission_id))

    def _role_permissions(self, role_id: UUID) -> frozenset[str]:
        return frozenset(self.s.permissions[p].code for r, p in self.s.role_permissions if r == role_id)

    async def get_grants(self, user_id: UUID) -> list[Grant]:
        return [Grant(self.s.roles[a.role_id].code, a.institution_id, self._role_permissions(a.role_id))
                for a in self.s.user_roles.values() if a.user_id == user_id and a.revoked_at is None]

    async def list_assignments(self, user_ids: Iterable[UUID], *, active_only: bool = True) -> dict[UUID, list[AssignmentView]]:
        ids = set(user_ids)
        result: dict[UUID, list[AssignmentView]] = {i: [] for i in ids}
        for a in sorted(self.s.user_roles.values(), key=lambda a: a.assigned_at):
            if a.user_id in ids and (not active_only or a.revoked_at is None):
                role = self.s.roles[a.role_id]
                result[a.user_id].append(AssignmentView(a.id, a.user_id, role.code, role.name, a.institution_id, a.assigned_at,
                                                        a.assigned_by_user_id, a.revoked_at, a.revoked_by_user_id))
        return result

    async def active_assignments_in(self, user_id: UUID, institution_id: UUID) -> list[tuple[UserRole, Role]]:
        return [(a, self.s.roles[a.role_id]) for a in self.s.user_roles.values()
                if a.user_id == user_id and a.institution_id == institution_id and a.revoked_at is None]

    async def add_assignment(self, assignment: UserRole) -> None:
        self.s.user_roles[assignment.id] = assignment

    async def count_active_holders(self, role_code: str, institution_id: UUID) -> int:
        return len({a.user_id for a in self.s.user_roles.values()
                    if self.s.roles[a.role_id].code == role_code and a.institution_id == institution_id and a.revoked_at is None
                    and self.s.users[a.user_id].account_status == ACCOUNT_ACTIVE})

    async def list_active_holders(self, role_code: str) -> list[tuple[User, UUID]]:
        return [(self.s.users[a.user_id], a.institution_id) for a in self.s.user_roles.values()
                if self.s.roles[a.role_id].code == role_code and a.revoked_at is None]

    async def active_role_institutions(self, user_id: UUID, role_code: str) -> list[UUID]:
        return [a.institution_id for a in self.s.user_roles.values()
                if a.user_id == user_id and a.revoked_at is None and self.s.roles[a.role_id].code == role_code]


class FakeProvisioningRepository:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    async def get(self, provisioning_id: UUID) -> UserProvisioning | None:
        return self.s.provisioning.get(provisioning_id)

    async def get_open_by_email(self, email: str) -> UserProvisioning | None:
        return next((p for p in self.s.provisioning.values() if p.email == email.lower() and p.is_open), None)

    async def add(self, record: UserProvisioning) -> None:
        self.s.provisioning[record.id] = record

    async def list(self, query: ProvisioningQuery) -> tuple[list[UserProvisioning], int]:
        rows = [p for p in self.s.provisioning.values() if p.institution_id in query.institution_ids
                and (not query.statuses or p.status in query.statuses)]
        rows.sort(key=lambda p: p.created_at, reverse=True)
        return rows[query.offset: query.offset + query.limit], len(rows)

    async def list_for_user(self, user_id: UUID, email: str) -> list[UserProvisioning]:
        return [p for p in self.s.provisioning.values() if p.user_id == user_id or p.email == email.lower()]


class FakeAuditRepository:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    async def add(self, entry: IdentityAuditLog) -> None:
        self.s.audit.append(entry)

    async def list(self, query: AuditQuery) -> tuple[list[IdentityAuditLog], int]:
        rows = [e for e in self.s.audit if e.institution_id in query.institution_ids
                and (query.action is None or e.action == query.action)
                and (query.target_user_id is None or e.target_user_id == query.target_user_id)
                and (query.actor_user_id is None or e.actor_user_id == query.actor_user_id)]
        rows.sort(key=lambda e: e.occurred_at, reverse=True)
        return rows[query.offset: query.offset + query.limit], len(rows)


class FakeUnitOfWork:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.users = FakeUserRepository(store)
        self.roles = FakeRoleRepository(store)
        self.provisioning = FakeProvisioningRepository(store)
        self.audit = FakeAuditRepository(store)

    async def commit(self) -> None:
        if self.store.fail_next_commits:
            error = self.store.fail_next_commits.pop(0)
            self.store.restore()
            raise error
        try:
            self.store.flush()
            self.store.check_constraints()
        except PersistenceConflictError:
            self.store.restore()
            raise
        self.store.snapshot()
        self.store.commits += 1

    async def flush(self) -> None:
        try:
            self.store.flush()
        except PersistenceConflictError:
            self.store.restore()
            raise

    async def rollback(self) -> None:
        self.store.restore()

    async def lock_privileged_changes(self) -> None:
        self.store.locks += 1


def uow_factory(store: FakeStore):
    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeUnitOfWork]:
        uow = FakeUnitOfWork(store)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.rollback()  # like the real session: uncommitted work is discarded

    return factory


class FakeCognito:
    """In-memory Cognito user pool."""

    def __init__(self) -> None:
        self.users: dict[str, CognitoUser] = {}  # by e-mail
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, list[CognitoError]] = {}

    def fail(self, operation: str, *errors: CognitoError) -> None:
        self.failures.setdefault(operation, []).extend(errors)

    def _maybe_fail(self, operation: str, subject: str) -> None:
        self.calls.append((operation, subject))
        if self.failures.get(operation):
            raise self.failures[operation].pop(0)

    def add_user(self, email: str, *, status: str = STATUS_CONFIRMED, enabled: bool = True, email_verified: bool = True,
                 name: str | None = None, sub: str | None = None) -> CognitoUser:
        user = CognitoUser(username=sub or str(uuid.uuid4()), sub="", email=email.lower(), email_verified=email_verified,
                           name=name, enabled=enabled, status=status)
        user = dataclasses.replace(user, sub=user.username)
        self.users[email.lower()] = user
        return user

    def _find(self, username: str) -> CognitoUser | None:
        username = username.lower()
        return self.users.get(username) or next((u for u in self.users.values() if u.sub == username), None)

    def _replace(self, username: str, **changes: Any) -> None:
        user = self._find(username)
        if user is None:
            raise CognitoUserNotFoundError("UserNotFoundException")
        self.users[user.email] = dataclasses.replace(user, **changes)

    async def create_user(self, *, email: str, full_name: str) -> CognitoUser:
        self._maybe_fail("create_user", email)
        if email.lower() in self.users:
            raise CognitoUserExistsError("UsernameExistsException")
        return self.add_user(email, status=STATUS_FORCE_CHANGE_PASSWORD, name=full_name)

    async def get_user(self, username: str) -> CognitoUser | None:
        self._maybe_fail("get_user", username)
        return self._find(username)

    async def resend_invitation(self, *, email: str) -> None:
        self._maybe_fail("resend_invitation", email)

    async def disable_user(self, username: str) -> None:
        self._maybe_fail("disable_user", username)
        self._replace(username, enabled=False)

    async def enable_user(self, username: str) -> None:
        self._maybe_fail("enable_user", username)
        self._replace(username, enabled=True)

    async def global_sign_out(self, username: str) -> None:
        self._maybe_fail("global_sign_out", username)

    async def update_name(self, username: str, full_name: str) -> None:
        self._maybe_fail("update_name", username)
        self._replace(username, name=full_name)

    async def describe_user_pool(self) -> dict:
        return {}

    def count(self, operation: str) -> int:
        return sum(1 for op, _ in self.calls if op == operation)
