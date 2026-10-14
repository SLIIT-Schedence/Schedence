"""Minimal academic staff references (enough to attach appointments; not a full HR registry)."""

from __future__ import annotations

import uuid
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError
from app.core.principal import Principal
from app.models import EMPLOYMENT_ACTIVE, Staff
from app.services.common import PERM_STAFF_CREATE, PERM_STAFF_READ, ServiceBase


class StaffService(ServiceBase):
    async def create(self, actor: Principal, *, institution_id: UUID, staff_number: str, full_name: str, email: str | None,
                     academic_rank: str | None, identity_user_id: UUID | None) -> Staff:
        self._authorize(actor, PERM_STAFF_CREATE, institution_id, "Institution")
        if await self.uow.staff.get_by_number(institution_id, staff_number) is not None:
            raise ConflictError("A staff member with this staff number already exists.", code="STAFF_NUMBER_EXISTS")
        now = self.clock()
        staff = Staff(id=uuid.uuid4(), institution_id=institution_id, staff_number=staff_number, full_name=full_name,
                      email=email.lower() if email else None, academic_rank=academic_rank, employment_status=EMPLOYMENT_ACTIVE,
                      identity_user_id=identity_user_id, created_at=now, updated_at=now)
        await self.uow.staff.add(staff)
        await self._audit(actor, "staff.created", entity_type="STAFF", entity_id=staff.id, institution_id=institution_id, staff_id=staff.id,
                          metadata={"has_login_link": identity_user_id is not None})
        await self._commit(conflict_code="STAFF_NUMBER_EXISTS", conflict_message="A staff member with this staff number already exists.")
        return staff

    async def list(self, actor: Principal, *, institution_id: UUID | None, search: str | None, offset: int, limit: int
                   ) -> tuple[list[Staff], int]:
        return await self.uow.staff.search(self._institutions(actor, PERM_STAFF_READ, institution_id), search, offset, limit)

    async def get(self, actor: Principal, staff_id: UUID) -> Staff:
        staff = await self.uow.staff.get(staff_id)
        if staff is None:
            raise NotFoundError("Staff member not found.")
        self._authorize(actor, PERM_STAFF_READ, staff.institution_id, "Staff member")
        return staff
