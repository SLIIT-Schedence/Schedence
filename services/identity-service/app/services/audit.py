from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from app.core.clock import Clock, utcnow
from app.core.principal import Principal
from app.models import ACTOR_SYSTEM, ACTOR_USER, IdentityAuditLog
from app.repositories.base import IdentityUnitOfWork


class AuditRecorder:
    """Adds audit entries to the current unit of work (committed with the change they describe)."""

    def __init__(self, uow: IdentityUnitOfWork, *, clock: Clock = utcnow, request_id: str | None = None) -> None:
        self.uow = uow
        self.clock = clock
        self.request_id = request_id

    async def record(
        self,
        action: str,
        *,
        actor: Principal | None = None,
        actor_type: str | None = None,
        target_user_id: UUID | None = None,
        institution_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IdentityAuditLog:
        entry = IdentityAuditLog(
            id=uuid.uuid4(),
            actor_user_id=actor.user_id if actor else None,
            actor_type=actor_type or (ACTOR_USER if actor else ACTOR_SYSTEM),
            action=action,
            target_user_id=target_user_id,
            institution_id=institution_id,
            safe_metadata={k: v for k, v in (metadata or {}).items() if v is not None},
            request_id=self.request_id,
            occurred_at=self.clock(),
        )
        await self.uow.audit.add(entry)
        return entry
