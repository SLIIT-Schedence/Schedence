"""Idempotent seeding of built-in roles and permissions from :mod:`app.rbac.catalog`.

Running it again changes nothing unless the catalog changed. Permissions found
on a built-in role but missing from the catalog are reported, and removed only
with ``prune=True``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.core.clock import Clock, utcnow
from app.models import Permission, Role
from app.rbac.catalog import PERMISSIONS, ROLES
from app.repositories.base import IdentityUnitOfWork


@dataclass
class SeedReport:
    roles_created: list[str] = field(default_factory=list)
    roles_updated: list[str] = field(default_factory=list)
    permissions_created: list[str] = field(default_factory=list)
    permissions_updated: list[str] = field(default_factory=list)
    grants_added: list[str] = field(default_factory=list)
    grants_removed: list[str] = field(default_factory=list)
    extra_grants_kept: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any((self.roles_created, self.roles_updated, self.permissions_created, self.permissions_updated,
                    self.grants_added, self.grants_removed))


class RbacSeeder:
    def __init__(self, uow: IdentityUnitOfWork, *, clock: Clock = utcnow) -> None:
        self.uow = uow
        self.clock = clock

    async def seed(self, *, prune: bool = False, commit: bool = True) -> SeedReport:
        report = SeedReport()
        now = self.clock()

        permissions = {p.code: p for p in await self.uow.roles.list_permissions()}
        for code, description in PERMISSIONS.items():
            existing = permissions.get(code)
            if existing is None:
                permissions[code] = Permission(id=uuid.uuid4(), code=code, description=description, created_at=now)
                await self.uow.roles.add_permission(permissions[code])
                report.permissions_created.append(code)
            elif existing.description != description:
                existing.description = description
                report.permissions_updated.append(code)

        roles = {r.code: r for r in await self.uow.roles.list_roles()}
        for definition in ROLES:
            role = roles.get(definition.code)
            if role is None:
                roles[definition.code] = Role(id=uuid.uuid4(), code=definition.code, name=definition.name,
                                              description=definition.description, is_system_role=True, created_at=now, updated_at=now)
                await self.uow.roles.add_role(roles[definition.code])
                report.roles_created.append(definition.code)
            elif (role.name, role.description, role.is_system_role) != (definition.name, definition.description, True):
                role.name, role.description, role.is_system_role, role.updated_at = definition.name, definition.description, True, now
                report.roles_updated.append(definition.code)

        await self.uow.flush()  # roles and permissions must exist before role_permissions rows reference them
        current = await self.uow.roles.role_permission_codes()
        for definition in ROLES:
            role = roles[definition.code]
            granted = current.get(definition.code, frozenset())
            for code in sorted(definition.permissions - granted):
                await self.uow.roles.add_role_permission(role.id, permissions[code].id)
                report.grants_added.append(f"{definition.code} -> {code}")
            for code in sorted(granted - definition.permissions):
                if prune and code in permissions:
                    await self.uow.roles.remove_role_permission(role.id, permissions[code].id)
                    report.grants_removed.append(f"{definition.code} -> {code}")
                else:
                    report.extra_grants_kept.append(f"{definition.code} -> {code}")

        if commit and report.changed:
            await self.uow.commit()
        return report
