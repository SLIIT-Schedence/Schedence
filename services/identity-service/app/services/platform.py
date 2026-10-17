"""System Admin operations: the institution registry and each institution's Institution Admins.

A System Admin holds only ``platform:*`` permissions, so nothing here reads or
changes anything else inside an institution. The only institution role it can
grant or revoke is INSTITUTION_ADMIN, and the only accounts it can disable or
re-invite are accounts holding that role in the institution named in the
request. Lecturers, students and academic data stay with the institution.

Unlike an Institution Admin, a System Admin may remove an institution's last
Institution Admin (e.g. a compromised account): it can always create a new one.

Every change records a ``platform.*`` audit event with the institution's ID, so
it appears both in the platform audit log and in that institution's own log.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from app.core.clock import Clock, utcnow
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ServiceUnavailableError
from app.core.principal import Principal
from app.integrations.cognito import CognitoAdminClient
from app.models import INSTITUTION_ACTIVE, INSTITUTION_DISABLED, IdentityAuditLog, Institution, Role, User
from app.rbac.catalog import INSTITUTION_ADMIN
from app.repositories.base import (
    IdentityUnitOfWork,
    InstitutionSearch,
    PersistenceConflictError,
    PersistenceError,
    PlatformAuditQuery,
    UserSearch,
)
from app.services.audit import AuditRecorder
from app.services.users import CreateUserResult, UserDetail, UserManagementService, clean_name

PERM_INSTITUTIONS_CREATE = "platform:institutions:create"
PERM_INSTITUTIONS_READ = "platform:institutions:read"
PERM_INSTITUTIONS_UPDATE = "platform:institutions:update"
PERM_INSTITUTIONS_DISABLE = "platform:institutions:disable"
PERM_ADMINS_CREATE = "platform:institution_admins:create"
PERM_ADMINS_READ = "platform:institution_admins:read"
PERM_ADMINS_REVOKE = "platform:institution_admins:revoke"
PERM_ADMINS_DISABLE = "platform:institution_admins:disable"
PERM_PLATFORM_AUDIT_READ = "platform:audit:read"


def normalize_code(code: str) -> str:
    return code.strip().upper()


class PlatformService:
    def __init__(self, uow: IdentityUnitOfWork, cognito: CognitoAdminClient, *, clock: Clock = utcnow,
                 request_id: str | None = None) -> None:
        self.uow = uow
        self.clock = clock
        self.audit = AuditRecorder(uow, clock=clock, request_id=request_id)
        self.users = UserManagementService(uow, cognito, clock=clock, request_id=request_id)

    # ------------------------------------------------------------------ institutions

    async def list_institutions(self, actor: Principal, *, query: str | None, status: str | None, offset: int,
                                limit: int) -> tuple[list[Institution], int]:
        self._require(actor, PERM_INSTITUTIONS_READ)
        return await self.uow.institutions.search(InstitutionSearch(query or None, status, offset, limit))

    async def get_institution(self, actor: Principal, institution_id: UUID) -> Institution:
        self._require(actor, PERM_INSTITUTIONS_READ)
        return await self._institution(institution_id)

    async def create_institution(self, actor: Principal, *, code: str, name: str) -> Institution:
        self._require(actor, PERM_INSTITUTIONS_CREATE)
        code, name = normalize_code(code), clean_name(name)
        await self._ensure_code_free(code)
        now = self.clock()
        institution = Institution(id=uuid.uuid4(), code=code, name=name, status=INSTITUTION_ACTIVE, created_by_user_id=actor.user_id,
                                  created_at=now, updated_at=now)
        await self.uow.institutions.add(institution)
        await self.audit.record("platform.institution_created", actor=actor, institution_id=institution.id,
                                metadata={"code": code, "name": name})
        await self._commit()
        return institution

    async def update_institution(self, actor: Principal, institution_id: UUID, *, code: str | None, name: str | None) -> Institution:
        self._require(actor, PERM_INSTITUTIONS_UPDATE)
        institution = await self._institution(institution_id)
        changes: dict[str, str] = {}
        if code is not None and normalize_code(code) != institution.code:
            changes["code"] = normalize_code(code)
            await self._ensure_code_free(changes["code"])
        if name is not None and clean_name(name) != institution.name:
            changes["name"] = clean_name(name)
        if not changes:
            return institution
        previous_code = institution.code
        for field, value in changes.items():
            setattr(institution, field, value)
        institution.updated_at = self.clock()
        await self.audit.record("platform.institution_updated", actor=actor, institution_id=institution.id,
                                metadata={"fields": sorted(changes), "previous_code": previous_code, **changes})
        await self._commit()
        return institution

    async def set_institution_status(self, actor: Principal, institution_id: UUID, *, active: bool,
                                     reason: str | None = None) -> Institution:
        """Disable or re-enable an institution. While disabled, roles held in it grant no permissions."""
        self._require(actor, PERM_INSTITUTIONS_DISABLE)
        institution = await self._institution(institution_id)
        status = INSTITUTION_ACTIVE if active else INSTITUTION_DISABLED
        if institution.status != status:
            institution.status, institution.updated_at = status, self.clock()
            await self.audit.record("platform.institution_enabled" if active else "platform.institution_disabled", actor=actor,
                                    institution_id=institution.id, metadata={"reason": (reason or None)})
            await self._commit()
        return institution

    # ------------------------------------------------------------------ institution admins

    async def list_institution_admins(self, actor: Principal, institution_id: UUID, *, offset: int,
                                      limit: int) -> tuple[list[UserDetail], int]:
        self._require(actor, PERM_ADMINS_READ)
        await self._institution(institution_id)
        users, total = await self.uow.users.search(
            UserSearch(institution_ids=frozenset({institution_id}), role_code=INSTITUTION_ADMIN, offset=offset, limit=limit)
        )
        return [await self._admin_detail(user, institution_id) for user in users], total

    async def create_institution_admin(self, actor: Principal, institution_id: UUID, *, email: str, full_name: str) -> CreateUserResult:
        """Onboard a new account as Institution Admin. Re-sending the same request resumes a failed attempt."""
        self._require(actor, PERM_ADMINS_CREATE)
        institution = await self._institution(institution_id)
        if not institution.is_active:
            raise ConflictError("The institution is disabled. Enable it before adding admins.", code="INSTITUTION_DISABLED")
        return await self.users.onboard(actor, email=email, full_name=full_name, role=await self._admin_role(),
                                        institution_id=institution_id, audit_action="platform.institution_admin_created")

    async def resend_admin_invitation(self, actor: Principal, institution_id: UUID, user_id: UUID) -> None:
        self._require(actor, PERM_ADMINS_CREATE)
        user = await self._admin(institution_id, user_id)
        await self.users.resend_invitation_to(actor, user, institution_id, audit_action="platform.institution_admin_invitation_resent")

    async def revoke_institution_admin(self, actor: Principal, institution_id: UUID, user_id: UUID) -> None:
        """Revoke the INSTITUTION_ADMIN role in this institution. The account and any other roles stay."""
        self._require(actor, PERM_ADMINS_REVOKE)
        user = await self._admin(institution_id, user_id)
        now = self.clock()
        for assignment, role in await self.uow.roles.active_assignments_in(user.id, institution_id):
            if role.code == INSTITUTION_ADMIN:
                assignment.revoked_at, assignment.revoked_by_user_id = now, actor.user_id
        await self.audit.record("user.role_revoked", actor=actor, target_user_id=user.id, institution_id=institution_id,
                                metadata={"role_code": INSTITUTION_ADMIN})
        await self.audit.record("platform.institution_admin_revoked", actor=actor, target_user_id=user.id, institution_id=institution_id)
        await self._commit()

    async def set_admin_account_status(self, actor: Principal, institution_id: UUID, user_id: UUID, *, active: bool,
                                       reason: str | None = None) -> tuple[UserDetail, str]:
        """Disable or re-enable an Institution Admin's account. Returns the admin and the Cognito sync result."""
        self._require(actor, PERM_ADMINS_DISABLE)
        user = await self._admin(institution_id, user_id)
        if active:
            await self.users.enable_account(actor, user, institution_id=institution_id, audit_action="platform.institution_admin_enabled")
            sync = "ok"
        else:
            sync = await self.users.disable_account(actor, user, institution_id=institution_id, reason=reason,
                                                    audit_action="platform.institution_admin_disabled")
        return await self._admin_detail(user, institution_id), sync

    # ------------------------------------------------------------------ audit

    async def list_audit_logs(self, actor: Principal, *, institution_id: UUID | None, action: str | None, actor_user_id: UUID | None,
                              target_user_id: UUID | None, occurred_from: datetime | None, occurred_to: datetime | None,
                              offset: int, limit: int) -> tuple[list[IdentityAuditLog], int]:
        self._require(actor, PERM_PLATFORM_AUDIT_READ)
        return await self.uow.audit.list_platform(
            PlatformAuditQuery(institution_id, action, actor_user_id, target_user_id, occurred_from, occurred_to, offset, limit)
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require(actor: Principal, permission: str) -> None:
        if not actor.has_platform_permission(permission):
            raise PermissionDeniedError(details={"required_permission": permission})

    async def _institution(self, institution_id: UUID) -> Institution:
        institution = await self.uow.institutions.get(institution_id)
        if institution is None:
            raise NotFoundError("Institution not found.")
        return institution

    async def _ensure_code_free(self, code: str) -> None:
        if await self.uow.institutions.get_by_code(code) is not None:
            raise ConflictError("Another institution already uses this code.", code="INSTITUTION_CODE_TAKEN")

    async def _admin_role(self) -> Role:
        role = (await self.uow.roles.get_roles_by_codes([INSTITUTION_ADMIN])).get(INSTITUTION_ADMIN)
        if role is None:
            raise ServiceUnavailableError("The INSTITUTION_ADMIN role is missing. Run scripts.seed_roles.", code="ROLES_NOT_SEEDED")
        return role

    async def _admin(self, institution_id: UUID, user_id: UUID) -> User:
        """The user, if they currently hold INSTITUTION_ADMIN in this institution (404 otherwise, revealing nothing else)."""
        await self._institution(institution_id)
        user = await self.uow.users.get(user_id)
        if user is None or institution_id not in await self.uow.roles.active_role_institutions(user.id, INSTITUTION_ADMIN):
            raise NotFoundError("No Institution Admin with this ID in this institution.")
        if await self.uow.roles.active_platform_assignments(user.id):
            raise PermissionDeniedError("Platform administrator accounts cannot be managed here.", code="PLATFORM_ACCOUNT")
        return user

    async def _admin_detail(self, user: User, institution_id: UUID) -> UserDetail:
        """The account with only its Institution Admin assignment in this institution: no other roles are disclosed."""
        assignments = (await self.uow.roles.list_assignments([user.id]))[user.id]
        return UserDetail(user, [a for a in assignments if a.institution_id == institution_id and a.role_code == INSTITUTION_ADMIN])

    async def _commit(self) -> None:
        try:
            await self.uow.commit()
        except PersistenceConflictError as exc:
            if exc.constraint == "uq_institutions_code":
                raise ConflictError("Another institution already uses this code.", code="INSTITUTION_CODE_TAKEN") from None
            raise ConflictError("The record was changed concurrently; reload and try again.", code="CONCURRENT_UPDATE") from None
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None
