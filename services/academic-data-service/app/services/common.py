from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from app.core.clock import Clock, utcnow
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ServiceUnavailableError
from app.core.principal import Principal
from app.models import AppointmentAuditLog
from app.repositories.base import AcademicUnitOfWork, PersistenceConflictError, PersistenceError

PERM_POSITIONS_CREATE = "academic:positions:create"
PERM_POSITIONS_READ = "academic:positions:read"
PERM_POSITIONS_UPDATE = "academic:positions:update"
PERM_POSITIONS_DISABLE = "academic:positions:disable"
PERM_APPOINTMENTS_ASSIGN = "academic:appointments:assign"
PERM_APPOINTMENTS_READ = "academic:appointments:read"
PERM_APPOINTMENTS_UPDATE = "academic:appointments:update"
PERM_APPOINTMENTS_END = "academic:appointments:end"
PERM_STAFF_CREATE = "academic:staff:create"
PERM_STAFF_READ = "academic:staff:read"
PERM_WORKLOAD_READ = "academic:workload:read"


def overlaps(a_from: date, a_until: date | None, b_from: date, b_until: date | None) -> bool:
    """Inclusive date ranges; ``None`` means open-ended."""
    return a_from <= (b_until or date.max) and b_from <= (a_until or date.max)


class ServiceBase:
    def __init__(self, uow: AcademicUnitOfWork, *, clock: Clock = utcnow, today: Callable[[], date] | None = None,
                 request_id: str | None = None) -> None:
        self.uow = uow
        self.clock = clock
        self.today = today or (lambda: clock().date())
        self.request_id = request_id

    @staticmethod
    def _authorize(actor: Principal, permission: str, institution_id: UUID, resource: str) -> None:
        """403 inside the caller's institutions, 404 outside them (existence is not revealed)."""
        if actor.has_permission(permission, institution_id):
            return
        if institution_id in actor.institution_ids:
            raise PermissionDeniedError(details={"required_permission": permission})
        raise NotFoundError(f"{resource} not found.")

    @staticmethod
    def _institutions(actor: Principal, permission: str, requested: UUID | None = None) -> frozenset[UUID]:
        institutions = actor.institutions_with(permission)
        if requested is not None:
            institutions = institutions & {requested}
        if not institutions:
            raise PermissionDeniedError(details={"required_permission": permission})
        return institutions

    async def _audit(self, actor: Principal, action: str, *, entity_type: str, entity_id: UUID, institution_id: UUID,
                     staff_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        await self.uow.audit.add(AppointmentAuditLog(
            id=uuid.uuid4(), institution_id=institution_id, actor_user_id=actor.user_id, action=action, entity_type=entity_type,
            entity_id=entity_id, staff_id=staff_id, safe_metadata={k: v for k, v in (metadata or {}).items() if v is not None},
            request_id=self.request_id, occurred_at=self.clock(),
        ))

    async def _commit(self, *, conflict_code: str = "CONCURRENT_UPDATE", conflict_message: str | None = None) -> None:
        try:
            await self.uow.commit()
        except PersistenceConflictError:
            raise ConflictError(conflict_message or "The record was changed concurrently or already exists.", code=conflict_code) from None
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None
