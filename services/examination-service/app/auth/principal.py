"""The caller as seen by this service: identity from Cognito, permissions from the Identity Service.

This service never stores or evaluates system roles itself and never reads the
Identity Service's tables (same contract as the Academic Data Service).
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

    def has_permission(self, code: str, institution_id: UUID) -> bool:
        """Deny by default; a permission counts only inside the given institution."""
        return code in self.permissions_by_institution.get(institution_id, frozenset())
