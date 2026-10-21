"""Read-only exports for the Teaching service (docs: teaching-service/docs/owner-api-integration.md).

Teaching forwards the caller's own token, so every export is authorized here by this
service's permissions; a Teaching permission alone never grants access. Nothing is written.
"""

from __future__ import annotations

from collections import Counter
from uuid import UUID

from app.core.errors import NotFoundError, ServiceUnavailableError
from app.core.principal import Principal
from app.models import EMPLOYMENT_ACTIVE
from app.repositories.base import PersistenceError
from app.services.common import PERM_STAFF_READ, ServiceBase

TEACHING_STAFF_TYPES = {"ACADEMIC", "INSTRUCTOR"}


def eligible_lecturers(staff: list[dict], profiles: list[dict], departments: list[dict]) -> list[dict]:
    """Active staff with an Academic/Instructor teaching profile, with their account-link status.

    Links are counted over *all* staff rows (inactive and non-teaching too), so one login linked to
    two staff records is AMBIGUOUS_LINK rather than silently picking one. Never matched by e-mail or name.
    """
    names = {row["id"]: row["name"] for row in departments}
    department_ids: dict[str, set[UUID]] = {}
    for profile in profiles:
        if profile["staff_type"] not in TEACHING_STAFF_TYPES:
            continue
        department_ids.setdefault(profile["staff_number"], set())
        if profile["department_id"] is not None:
            department_ids[profile["staff_number"]].add(profile["department_id"])
    links = Counter(row["identity_user_id"] for row in staff if row["identity_user_id"] is not None)
    result = []
    for row in staff:
        if row["employment_status"] != EMPLOYMENT_ACTIVE or row["staff_number"] not in department_ids:
            continue
        user = row["identity_user_id"]
        status = "NO_LINK" if user is None else "AMBIGUOUS_LINK" if user.int == 0 or links[user] != 1 else "READY"
        ids = sorted(department_ids[row["staff_number"]], key=str)
        result.append({"staff_id": row["id"], "staff_number": row["staff_number"], "full_name": row["full_name"],
                       "identity_user_id": user, "link_status": status, "department_ids": ids,
                       "departments": [{"id": i, "name": names[i]} for i in ids if i in names]})
    return result


class TeachingInputService(ServiceBase):
    async def _export(self, institution_id: UUID) -> dict[str, list[dict]]:
        try:
            return await self.uow.teaching_inputs.export(institution_id)
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None

    async def generation_inputs(self, actor: Principal, institution_id: UUID) -> dict:
        self._authorize(actor, PERM_STAFF_READ, institution_id, "Institution")
        return {"institution_id": institution_id, "complete": True, "tables": await self._export(institution_id)}

    async def _recipients(self, institution_id: UUID) -> list[dict]:
        tables = await self._export(institution_id)
        return eligible_lecturers(tables["staff"], tables["staff_teaching_profiles"], tables["departments"])

    async def form_recipients(self, actor: Principal, institution_id: UUID) -> dict:
        self._authorize(actor, PERM_STAFF_READ, institution_id, "Institution")
        return {"institution_id": institution_id, "complete": True, "items": await self._recipients(institution_id)}

    async def my_recipient_link(self, actor: Principal, institution_id: UUID) -> dict:
        """Zero or one record: the caller's own unambiguous staff link. Needs only membership of the institution."""
        if institution_id not in actor.institution_ids:
            raise NotFoundError("Institution not found.")
        mine = [row for row in await self._recipients(institution_id)
                if row["identity_user_id"] == actor.user_id and row["link_status"] == "READY"]
        return {"institution_id": institution_id, "complete": True, "items": mine}
