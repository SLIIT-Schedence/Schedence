"""The authenticated caller, with permissions resolved from the Identity database.

Institution permissions and platform permissions are kept apart on purpose:
``has_permission`` only ever answers for institutions, so a platform role such
as SYSTEM_ADMIN can never satisfy an institution check, and vice versa.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RoleGrant:
    role_code: str
    institution_id: UUID


@dataclass(frozen=True, slots=True)
class Grant:
    """One active role assignment and the permissions that role carries."""

    role_code: str
    institution_id: UUID
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class PlatformGrant:
    """One active platform role assignment (outside any institution) and its permissions."""

    role_code: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    cognito_sub: str
    email: str
    full_name: str
    roles: frozenset[RoleGrant]
    permissions_by_institution: Mapping[UUID, frozenset[str]] = field(default_factory=dict)
    platform_roles: frozenset[str] = frozenset()
    platform_permissions: frozenset[str] = frozenset()

    @classmethod
    def from_grants(cls, *, user_id: UUID, cognito_sub: str, email: str, full_name: str, grants: Iterable[Grant],
                    platform_grants: Iterable[PlatformGrant] = ()) -> Principal:
        grants, platform_grants = list(grants), list(platform_grants)
        by_institution: dict[UUID, set[str]] = {}
        for grant in grants:
            by_institution.setdefault(grant.institution_id, set()).update(grant.permissions)
        return cls(
            user_id=user_id,
            cognito_sub=cognito_sub,
            email=email,
            full_name=full_name,
            roles=frozenset(RoleGrant(g.role_code, g.institution_id) for g in grants),
            permissions_by_institution=MappingProxyType({k: frozenset(v) for k, v in by_institution.items()}),
            platform_roles=frozenset(g.role_code for g in platform_grants),
            platform_permissions=frozenset().union(*(g.permissions for g in platform_grants)),
        )

    @property
    def all_permissions(self) -> frozenset[str]:
        return frozenset().union(*self.permissions_by_institution.values()) if self.permissions_by_institution else frozenset()

    @property
    def institution_ids(self) -> frozenset[UUID]:
        return frozenset(g.institution_id for g in self.roles)

    def has_permission(self, code: str, institution_id: UUID | None = None) -> bool:
        """Deny by default. Without ``institution_id``: held in at least one institution."""
        if institution_id is None:
            return code in self.all_permissions
        return code in self.permissions_by_institution.get(institution_id, frozenset())

    def institutions_with(self, code: str) -> frozenset[UUID]:
        return frozenset(i for i, perms in self.permissions_by_institution.items() if code in perms)

    def has_role(self, role_code: str, institution_id: UUID | None = None) -> bool:
        return any(g.role_code == role_code and (institution_id is None or g.institution_id == institution_id) for g in self.roles)

    def has_platform_permission(self, code: str) -> bool:
        return code in self.platform_permissions
