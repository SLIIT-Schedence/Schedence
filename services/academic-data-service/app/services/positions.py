"""Administrative position types and their versioned administrative-hour policies.

No hour value is built in: every position's hours come from a policy record
that an authorised administrator enters, and changing it creates a new version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, ServiceUnavailableError, ValidationFailedError
from app.core.principal import Principal
from app.models import AdministrativePositionType, AdministrativeWorkloadPolicy
from app.repositories.base import PersistenceConflictError, PersistenceError
from app.services.common import (
    PERM_POSITIONS_CREATE,
    PERM_POSITIONS_DISABLE,
    PERM_POSITIONS_READ,
    PERM_POSITIONS_UPDATE,
    ServiceBase,
    overlaps,
)


@dataclass(frozen=True)
class PolicyInput:
    admin_hours_per_week: Decimal
    combination_rule: str = "UNSPECIFIED"
    cap_hours_per_week: Decimal | None = None
    policy_reference: str | None = None
    notes: str | None = None
    effective_from: date | None = None


@dataclass(frozen=True)
class PositionView:
    position: AdministrativePositionType
    policies: list[AdministrativeWorkloadPolicy]  # oldest version first
    current_policy: AdministrativeWorkloadPolicy | None


def policy_on(policies: list[AdministrativeWorkloadPolicy], day: date) -> AdministrativeWorkloadPolicy | None:
    return next((p for p in reversed(policies) if p.applies_on(day)), None)


class PositionService(ServiceBase):
    async def create(self, actor: Principal, *, institution_id: UUID, code: str, name: str, description: str,
                     unit_type: str | None, is_exclusive_per_unit: bool, policy: PolicyInput) -> PositionView:
        self._authorize(actor, PERM_POSITIONS_CREATE, institution_id, "Institution")
        if await self.uow.positions.get_by_code(institution_id, code) is not None:
            raise ConflictError("A position type with this code already exists.", code="POSITION_CODE_EXISTS")
        now = self.clock()
        position = AdministrativePositionType(
            id=uuid.uuid4(), institution_id=institution_id, code=code, name=name, description=description or "", unit_type=unit_type,
            is_exclusive_per_unit=is_exclusive_per_unit, is_active=True, created_at=now, updated_at=now,
            created_by_user_id=actor.user_id, updated_by_user_id=actor.user_id,
        )
        first = self._new_policy(actor, position.id, 1, policy)
        await self.uow.positions.add(position)
        try:
            await self.uow.flush()  # the position row must exist before its policy references it
        except PersistenceConflictError:
            raise ConflictError("A position type with this code already exists.", code="POSITION_CODE_EXISTS") from None
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None
        await self.uow.positions.add_policy(first)
        await self._audit(actor, "position.created", entity_type="POSITION_TYPE", entity_id=position.id, institution_id=institution_id,
                          metadata={"code": code, "policy_version": 1, "admin_hours_per_week": str(first.admin_hours_per_week)})
        await self._commit(conflict_code="POSITION_CODE_EXISTS", conflict_message="A position type with this code already exists.")
        return PositionView(position, [first], policy_on([first], self.today()))

    async def list(self, actor: Principal, *, institution_id: UUID | None, include_inactive: bool, offset: int, limit: int
                   ) -> tuple[list[PositionView], int]:
        institutions = self._institutions(actor, PERM_POSITIONS_READ, institution_id)
        positions, total = await self.uow.positions.list(institutions, include_inactive, offset, limit)
        policies = await self.uow.positions.policies([p.id for p in positions])
        today = self.today()
        return [PositionView(p, policies[p.id], policy_on(policies[p.id], today)) for p in positions], total

    async def get(self, actor: Principal, position_id: UUID) -> PositionView:
        position = await self._position(actor, position_id, PERM_POSITIONS_READ)
        return await self._view(position)

    async def update(self, actor: Principal, position_id: UUID, changes: dict) -> PositionView:
        position = await self._position(actor, position_id, PERM_POSITIONS_UPDATE, for_update=True)
        appointments = await self.uow.appointments.for_position(position.id)
        if "unit_type" in changes and changes["unit_type"] != position.unit_type and appointments:
            raise ConflictError("The unit type cannot change once the position has appointments.", code="POSITION_IN_USE")
        if changes.get("is_exclusive_per_unit") and not position.is_exclusive_per_unit:
            for a in appointments:
                if any(b.staff_id != a.staff_id and b.unit_code == a.unit_code
                       and overlaps(a.effective_from, a.effective_until, b.effective_from, b.effective_until) for b in appointments):
                    raise ConflictError("Existing appointments overlap for the same unit; resolve them first.", code="EXCLUSIVITY_CONFLICT")
        changed = sorted(k for k, v in changes.items() if getattr(position, k) != v)
        if changed:
            for key in changed:
                setattr(position, key, changes[key])
            position.updated_at, position.updated_by_user_id = self.clock(), actor.user_id
            await self._audit(actor, "position.updated", entity_type="POSITION_TYPE", entity_id=position.id,
                              institution_id=position.institution_id, metadata={"fields": changed})
            await self._commit()
        return await self._view(position)

    async def set_active(self, actor: Principal, position_id: UUID, active: bool) -> PositionView:
        position = await self._position(actor, position_id, PERM_POSITIONS_DISABLE, for_update=True)
        if position.is_active != active:
            if not active:
                today = self.today()
                in_use = [a for a in await self.uow.appointments.for_position(position.id) if a.status_on(today) != "ENDED"]
                if in_use:
                    raise ConflictError(f"The position has {len(in_use)} current or scheduled appointment(s); end them first.",
                                        code="POSITION_IN_USE")
            position.is_active, position.updated_at, position.updated_by_user_id = active, self.clock(), actor.user_id
            await self._audit(actor, "position.enabled" if active else "position.disabled", entity_type="POSITION_TYPE",
                              entity_id=position.id, institution_id=position.institution_id)
            await self._commit()
        return await self._view(position)

    async def add_policy_version(self, actor: Principal, position_id: UUID, policy: PolicyInput) -> PositionView:
        position = await self._position(actor, position_id, PERM_POSITIONS_UPDATE, for_update=True)
        history = (await self.uow.positions.policies([position.id]))[position.id]
        effective_from = policy.effective_from or self.today()
        latest = history[-1] if history else None
        if latest is not None:
            if effective_from <= latest.effective_from:
                raise ValidationFailedError("A new policy version must start after the current version's start date.",
                                            code="INVALID_EFFECTIVE_DATE", details={"current_effective_from": latest.effective_from.isoformat()})
            if latest.effective_until is None or latest.effective_until >= effective_from:
                latest.effective_until = effective_from - timedelta(days=1)
        new = self._new_policy(actor, position.id, (latest.version + 1) if latest else 1, policy, effective_from)
        await self.uow.positions.add_policy(new)
        position.updated_at, position.updated_by_user_id = self.clock(), actor.user_id
        await self._audit(actor, "workload_policy.versioned", entity_type="WORKLOAD_POLICY", entity_id=new.id,
                          institution_id=position.institution_id,
                          metadata={"position_type_id": str(position.id), "version": new.version,
                                    "admin_hours_per_week": str(new.admin_hours_per_week), "combination_rule": new.combination_rule,
                                    "effective_from": effective_from.isoformat()})
        await self._commit()
        return await self._view(position)

    def _new_policy(self, actor: Principal, position_id: UUID, version: int, policy: PolicyInput,
                    effective_from: date | None = None) -> AdministrativeWorkloadPolicy:
        return AdministrativeWorkloadPolicy(
            id=uuid.uuid4(), position_type_id=position_id, version=version, admin_hours_per_week=policy.admin_hours_per_week,
            combination_rule=policy.combination_rule, cap_hours_per_week=policy.cap_hours_per_week,
            policy_reference=policy.policy_reference, notes=policy.notes,
            effective_from=effective_from or policy.effective_from or self.today(), effective_until=None,
            created_at=self.clock(), created_by_user_id=actor.user_id,
        )

    async def _position(self, actor: Principal, position_id: UUID, permission: str, *, for_update: bool = False
                        ) -> AdministrativePositionType:
        position = await self.uow.positions.get(position_id, for_update=for_update)
        if position is None:
            raise NotFoundError("Position type not found.")
        self._authorize(actor, permission, position.institution_id, "Position type")
        return position

    async def _view(self, position: AdministrativePositionType) -> PositionView:
        history = (await self.uow.positions.policies([position.id]))[position.id]
        return PositionView(position, history, policy_on(history, self.today()))
