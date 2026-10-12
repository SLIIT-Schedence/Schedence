"""Repository interfaces used by the service layer.

Business services depend only on these protocols. Production uses the
SQLAlchemy implementations; unit tests use in-memory fakes, so no test ever
needs a database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.principal import Grant
from app.models import IdentityAuditLog, Permission, Role, User, UserProvisioning, UserRole


class PersistenceError(Exception):
    """A database operation failed. The message is a fixed, safe description."""


class PersistenceConflictError(PersistenceError):
    """A uniqueness or integrity constraint was violated."""

    def __init__(self, constraint: str | None = None) -> None:
        self.constraint = constraint
        super().__init__(f"integrity constraint violated ({constraint or 'unknown'})")


@dataclass(frozen=True, slots=True)
class AssignmentView:
    assignment_id: UUID
    user_id: UUID
    role_code: str
    role_name: str
    institution_id: UUID
    assigned_at: datetime
    assigned_by_user_id: UUID | None
    revoked_at: datetime | None = None
    revoked_by_user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class UserSearch:
    institution_ids: frozenset[UUID]
    query: str | None = None
    status: str | None = None
    role_code: str | None = None
    offset: int = 0
    limit: int = 20


@dataclass(frozen=True, slots=True)
class AuditQuery:
    institution_ids: frozenset[UUID]
    action: str | None = None
    actor_user_id: UUID | None = None
    target_user_id: UUID | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ProvisioningQuery:
    institution_ids: frozenset[UUID]
    statuses: frozenset[str] = field(default_factory=frozenset)
    offset: int = 0
    limit: int = 20


class UserRepository(Protocol):
    async def get(self, user_id: UUID) -> User | None: ...
    async def get_by_cognito_sub(self, cognito_sub: str) -> User | None: ...
    async def get_by_email(self, email: str) -> User | None: ...
    async def add(self, user: User) -> None: ...
    async def search(self, criteria: UserSearch) -> tuple[list[User], int]: ...
    async def is_visible_in(self, user_id: UUID, institution_ids: frozenset[UUID]) -> bool:
        """True if the user has (or had) a role, or a provisioning record, in any of these institutions."""
        ...


class RoleRepository(Protocol):
    async def list_roles(self) -> list[Role]: ...
    async def list_permissions(self) -> list[Permission]: ...
    async def get_roles_by_codes(self, codes: Iterable[str]) -> dict[str, Role]: ...
    async def role_permission_codes(self) -> dict[str, frozenset[str]]: ...
    async def add_role(self, role: Role) -> None: ...
    async def add_permission(self, permission: Permission) -> None: ...
    async def add_role_permission(self, role_id: UUID, permission_id: UUID) -> None: ...
    async def remove_role_permission(self, role_id: UUID, permission_id: UUID) -> None: ...
    async def get_grants(self, user_id: UUID) -> list[Grant]: ...
    async def list_assignments(self, user_ids: Iterable[UUID], *, active_only: bool = True) -> dict[UUID, list[AssignmentView]]: ...
    async def active_assignments_in(self, user_id: UUID, institution_id: UUID) -> list[tuple[UserRole, Role]]: ...
    async def add_assignment(self, assignment: UserRole) -> None: ...
    async def count_active_holders(self, role_code: str, institution_id: UUID) -> int:
        """Active (non-revoked) assignments held by ACTIVE users."""
        ...
    async def list_active_holders(self, role_code: str) -> list[tuple[User, UUID]]: ...
    async def active_role_institutions(self, user_id: UUID, role_code: str) -> list[UUID]: ...


class ProvisioningRepository(Protocol):
    async def get(self, provisioning_id: UUID) -> UserProvisioning | None: ...
    async def get_open_by_email(self, email: str) -> UserProvisioning | None: ...
    async def add(self, record: UserProvisioning) -> None: ...
    async def list(self, query: ProvisioningQuery) -> tuple[list[UserProvisioning], int]: ...
    async def list_for_user(self, user_id: UUID, email: str) -> list[UserProvisioning]: ...


class AuditRepository(Protocol):
    async def add(self, entry: IdentityAuditLog) -> None: ...
    async def list(self, query: AuditQuery) -> tuple[list[IdentityAuditLog], int]: ...


class IdentityUnitOfWork(Protocol):
    users: UserRepository
    roles: RoleRepository
    provisioning: ProvisioningRepository
    audit: AuditRepository

    async def commit(self) -> None:
        """Commit. Raises PersistenceConflictError / PersistenceError (after rolling back)."""
        ...

    async def flush(self) -> None:
        """Send pending changes now, inside the transaction.

        The models declare foreign keys but no relationship(), so the ORM does not
        know which table must be written first. Call this after adding parent rows
        (users, roles, permissions) and before adding rows that reference them.
        """
        ...

    async def rollback(self) -> None: ...

    async def lock_privileged_changes(self) -> None:
        """Serialise changes that could remove the last SUPER_ADMIN (transaction-scoped)."""
        ...
