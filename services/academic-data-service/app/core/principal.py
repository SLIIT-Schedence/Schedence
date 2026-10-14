"""The caller as seen by this service: identity from Cognito, permissions from the Identity Service.

This service never stores or evaluates system roles itself and never reads the
Identity Service's tables. Academic appointments held by a person play no part
in this object: an HOD or Program Coordinator appointment grants nothing here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    cognito_sub: str
    permissions_by_institution: Mapping[UUID, frozenset[str]] = field(default_factory=dict)
    roles_by_institution: Mapping[UUID, frozenset[str]] = field(default_factory=dict)

    @property
    def institution_ids(self) -> frozenset[UUID]:
        return frozenset(self.permissions_by_institution) | frozenset(self.roles_by_institution)

    def has_permission(self, code: str, institution_id: UUID | None = None) -> bool:
        if institution_id is None:
            return any(code in perms for perms in self.permissions_by_institution.values())
        return code in self.permissions_by_institution.get(institution_id, frozenset())

    def institutions_with(self, code: str) -> frozenset[UUID]:
        return frozenset(i for i, perms in self.permissions_by_institution.items() if code in perms)
