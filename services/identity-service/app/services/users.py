"""User onboarding, profile, status and role management.

Two systems are involved (Cognito for credentials, Neon for Schedence data),
so account creation is a small state machine recorded in ``user_provisioning``:

    PENDING ──Cognito ok──▶ COGNITO_CREATED ──local commit ok──▶ COMPLETED
       │                          │
       └──Cognito error──▶ FAILED ◀──local commit error──┘

Each transition is committed before the next external call, so a crash at any
point leaves a visible record that can be retried. Retrying is idempotent: an
existing Cognito identity for the same e-mail is linked instead of duplicated.

Public methods taking an ``actor`` authorise the caller as an institution user.
The account operations below them (``onboard``, ``disable_account``,
``enable_account``, ``resend_invitation_to``) do not authorise: the
institution-scoped methods here and :class:`app.services.platform.PlatformService`
call them after their own checks.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

from app.core.clock import Clock, utcnow
from app.core.errors import (
    ConflictError,
    IdentityProviderError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.principal import Principal
from app.integrations.cognito import (
    STATUS_FORCE_CHANGE_PASSWORD,
    CognitoAdminClient,
    CognitoError,
    CognitoMisconfiguredError,
    CognitoPermissionError,
    CognitoUser,
    CognitoUserExistsError,
)
from app.models import (
    ACCOUNT_ACTIVE,
    ACCOUNT_DISABLED,
    PROVISIONING_CANCELLED,
    PROVISIONING_COGNITO_CREATED,
    PROVISIONING_COMPLETED,
    PROVISIONING_FAILED,
    PROVISIONING_PENDING,
    Institution,
    Role,
    User,
    UserProvisioning,
    UserRole,
)
from app.rbac.catalog import INSTITUTION_ADMIN, PLATFORM_ROLE_CODES, PRIVILEGED_ROLES
from app.repositories.base import (
    AssignmentView,
    IdentityUnitOfWork,
    PersistenceConflictError,
    PersistenceError,
    ProvisioningQuery,
    UserSearch,
)
from app.services.audit import AuditRecorder

logger = logging.getLogger(__name__)

PERM_USERS_CREATE = "identity:users:create"
PERM_USERS_READ = "identity:users:read"
PERM_USERS_UPDATE = "identity:users:update"
PERM_USERS_DISABLE = "identity:users:disable"
PERM_ROLES_ASSIGN = "identity:roles:assign"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def clean_name(name: str) -> str:
    return " ".join(name.split())


@dataclass(frozen=True)
class UserDetail:
    user: User
    roles: list[AssignmentView]


@dataclass(frozen=True)
class CreateUserResult:
    detail: UserDetail
    provisioning: UserProvisioning
    linked_existing_identity: bool


@dataclass(frozen=True)
class ChangeResult:
    detail: UserDetail
    cognito_sync: str  # "ok" | "failed" | "not_required"


@dataclass(frozen=True)
class AccountStatus:
    user: User
    cognito_available: bool
    cognito_status: str | None = None
    cognito_enabled: bool | None = None
    cognito_email_verified: bool | None = None


class UserManagementService:
    def __init__(self, uow: IdentityUnitOfWork, cognito: CognitoAdminClient, *, clock: Clock = utcnow,
                 request_id: str | None = None) -> None:
        self.uow = uow
        self.cognito = cognito
        self.clock = clock
        self.audit = AuditRecorder(uow, clock=clock, request_id=request_id)

    # ------------------------------------------------------------------ queries

    async def me(self, principal: Principal) -> UserDetail:
        user = await self.uow.users.get(principal.user_id)
        if user is None:
            raise NotFoundError("User not found.")
        roles = (await self.uow.roles.list_assignments([user.id]))[user.id]
        return UserDetail(user, roles)

    async def institutions_of(self, detail: UserDetail) -> list[Institution]:
        """The institutions the user's active roles belong to (disabled ones included), sorted by name."""
        institutions = await self.uow.institutions.get_many(r.institution_id for r in detail.roles)
        return sorted(institutions.values(), key=lambda i: (i.name, str(i.id)))

    async def list_users(self, actor: Principal, *, query: str | None, status: str | None, role_code: str | None,
                         offset: int, limit: int) -> tuple[list[UserDetail], int]:
        institutions = self._institutions(actor, PERM_USERS_READ)
        users, total = await self.uow.users.search(
            UserSearch(institution_ids=institutions, query=query or None, status=status, role_code=role_code, offset=offset, limit=limit)
        )
        assignments = await self.uow.roles.list_assignments([u.id for u in users])
        return [UserDetail(u, [a for a in assignments[u.id] if a.institution_id in institutions]) for u in users], total

    async def get_user(self, actor: Principal, user_id: UUID) -> UserDetail:
        return await self._detail(actor, await self._visible_user(actor, user_id, PERM_USERS_READ))

    async def get_account_status(self, actor: Principal, user_id: UUID) -> AccountStatus:
        user = await self._visible_user(actor, user_id, PERM_USERS_READ)
        try:
            identity = await self.cognito.get_user(user.cognito_sub)
        except CognitoError:
            return AccountStatus(user=user, cognito_available=False)
        if identity is None:
            return AccountStatus(user=user, cognito_available=True, cognito_status="NOT_FOUND")
        return AccountStatus(user, True, identity.status, identity.enabled, identity.email_verified)

    async def provisioning_history(self, actor: Principal, user_id: UUID) -> list[UserProvisioning]:
        user = await self._visible_user(actor, user_id, PERM_USERS_READ)
        return await self.uow.provisioning.list_for_user(user.id, user.email)

    async def list_provisioning(self, actor: Principal, *, statuses: frozenset[str], offset: int, limit: int) -> tuple[list[UserProvisioning], int]:
        institutions = self._institutions(actor, PERM_USERS_CREATE)
        return await self.uow.provisioning.list(ProvisioningQuery(institutions, statuses, offset, limit))

    # ------------------------------------------------------------------ onboarding

    async def create_user(self, actor: Principal, *, email: str, full_name: str, role_code: str, institution_id: UUID) -> CreateUserResult:
        self._require(actor, PERM_USERS_CREATE, institution_id)
        self._require(actor, PERM_ROLES_ASSIGN, institution_id)
        role = await self._role(role_code)
        self._check_privileged_grant(actor, {role.code}, institution_id)
        return await self.onboard(actor, email=email, full_name=full_name, role=role, institution_id=institution_id)

    async def onboard(self, actor: Principal, *, email: str, full_name: str, role: Role, institution_id: UUID,
                      audit_action: str | None = None) -> CreateUserResult:
        """Start, or resume, onboarding a new account with ``role`` in ``institution_id``. Does not authorise.

        ``audit_action`` names one more audit event, recorded with the completed account.
        """
        email, full_name = normalize_email(email), clean_name(full_name)
        if await self.uow.users.get_by_email(email) is not None:
            raise ConflictError("An account with this e-mail address already exists.", code="EMAIL_ALREADY_REGISTERED")

        record = await self.uow.provisioning.get_open_by_email(email)
        if record is not None:
            if record.requested_role_id != role.id or record.institution_id != institution_id:
                raise ConflictError(
                    "Onboarding for this e-mail address is already in progress with different details.",
                    code="PROVISIONING_IN_PROGRESS",
                    details={"provisioning_id": str(record.id)},
                )
            # Same request again: resume it (safe retry).
        else:
            now = self.clock()
            record = UserProvisioning(
                id=uuid.uuid4(), email=email, full_name=full_name, requested_role_id=role.id, institution_id=institution_id,
                status=PROVISIONING_PENDING, attempt_count=0, requested_by_user_id=actor.user_id, created_at=now, updated_at=now,
            )
            await self.uow.provisioning.add(record)
            await self.audit.record("user.provisioning_started", actor=actor, institution_id=institution_id,
                                    metadata={"provisioning_id": str(record.id), "role_code": role.code})
            try:
                await self.uow.commit()
            except PersistenceConflictError:
                raise ConflictError("Onboarding for this e-mail address is already in progress.", code="PROVISIONING_IN_PROGRESS") from None
            except PersistenceError:
                raise ServiceUnavailableError("The database is temporarily unavailable.") from None
        return await self._provision(actor, record, role, audit_action)

    async def retry_provisioning(self, actor: Principal, provisioning_id: UUID) -> CreateUserResult:
        record = await self._visible_provisioning(actor, provisioning_id)
        if not record.is_open:
            raise ConflictError("Only pending or failed onboarding can be retried.", code="PROVISIONING_NOT_RETRYABLE")
        self._require(actor, PERM_ROLES_ASSIGN, record.institution_id)
        role = await self._role_by_id(record.requested_role_id)
        self._check_privileged_grant(actor, {role.code}, record.institution_id)
        existing = await self.uow.users.get_by_email(record.email)
        if existing is not None and existing.cognito_sub != record.cognito_sub:
            raise ConflictError("An account with this e-mail address already exists.", code="EMAIL_ALREADY_REGISTERED")
        return await self._provision(actor, record, role)

    async def cancel_provisioning(self, actor: Principal, provisioning_id: UUID) -> UserProvisioning:
        record = await self._visible_provisioning(actor, provisioning_id)
        if not record.is_open:
            raise ConflictError("Only pending or failed onboarding can be cancelled.", code="PROVISIONING_NOT_CANCELLABLE")
        if record.cognito_sub is not None:
            raise ConflictError(
                "A Cognito identity already exists for this onboarding; retry it instead of cancelling so it is not orphaned.",
                code="PROVISIONING_NOT_CANCELLABLE",
            )
        record.status, record.updated_at = PROVISIONING_CANCELLED, self.clock()
        await self.audit.record("user.provisioning_cancelled", actor=actor, institution_id=record.institution_id,
                                metadata={"provisioning_id": str(record.id)})
        await self._commit()
        return record

    async def resend_invitation(self, actor: Principal, user_id: UUID) -> None:
        user = await self._visible_user(actor, user_id, PERM_USERS_CREATE)
        await self._ensure_not_platform_account(user)
        await self.resend_invitation_to(actor, user, await self._audit_institution(actor, user, PERM_USERS_CREATE))

    async def resend_invitation_to(self, actor: Principal, user: User, institution_id: UUID | None, *,
                                   audit_action: str | None = None) -> None:
        """Send a new temporary password to a user who has not completed first sign-in. Does not authorise."""
        try:
            identity = await self.cognito.get_user(user.cognito_sub)
            if identity is None:
                raise ConflictError("The Cognito identity for this account no longer exists.", code="IDENTITY_NOT_FOUND")
            if identity.status != STATUS_FORCE_CHANGE_PASSWORD:
                raise ConflictError("This user has already completed first sign-in.", code="INVITATION_NOT_APPLICABLE")
            await self.cognito.resend_invitation(email=user.email)
        except CognitoError as exc:
            raise self._provider_error(exc) from None
        await self.audit.record("user.invitation_resent", actor=actor, target_user_id=user.id, institution_id=institution_id)
        if audit_action:
            await self.audit.record(audit_action, actor=actor, target_user_id=user.id, institution_id=institution_id)
        await self._commit()

    async def _provision(self, actor: Principal, record: UserProvisioning, role: Role, audit_action: str | None = None) -> CreateUserResult:
        record_id = record.id
        linked_existing = False
        record.attempt_count += 1
        record.updated_at = self.clock()

        if record.cognito_sub is None:
            try:
                identity = await self.cognito.create_user(email=record.email, full_name=record.full_name)
            except CognitoUserExistsError:
                identity = await self._existing_identity(record)
                linked_existing = True
            except CognitoError as exc:
                await self._fail(record, exc.category)
                raise self._provider_error(exc, record_id) from None

            if await self.uow.users.get_by_cognito_sub(identity.sub) is not None:
                await self._fail(record, "IDENTITY_ALREADY_LINKED")
                raise ConflictError("This Cognito identity is already linked to another account.", code="IDENTITY_ALREADY_LINKED",
                                    details={"provisioning_id": str(record_id)})
            record.cognito_sub, record.status, record.error_category = identity.sub, PROVISIONING_COGNITO_CREATED, None
            record.updated_at = self.clock()
            await self._commit()

        return await self._complete(actor, record_id, role, linked_existing, audit_action)

    async def _existing_identity(self, record: UserProvisioning) -> CognitoUser:
        try:
            identity = await self.cognito.get_user(record.email)
        except CognitoError as exc:
            await self._fail(record, exc.category)
            raise self._provider_error(exc, record.id) from None
        if identity is None or normalize_email(identity.email or "") != record.email:
            await self._fail(record, "IDENTITY_LOOKUP_MISMATCH")
            raise ConflictError("The existing Cognito identity could not be matched safely.", code="IDENTITY_LOOKUP_MISMATCH",
                                details={"provisioning_id": str(record.id)})
        return identity

    async def _complete(self, actor: Principal, record_id: UUID, role: Role, linked_existing: bool,
                        audit_action: str | None = None) -> CreateUserResult:
        record = await self.uow.provisioning.get(record_id)
        assert record is not None and record.cognito_sub is not None
        now = self.clock()
        user = User(id=uuid.uuid4(), cognito_sub=record.cognito_sub, email=record.email, full_name=record.full_name,
                    account_status=ACCOUNT_ACTIVE, created_at=now, updated_at=now)
        assignment = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id, institution_id=record.institution_id,
                              assigned_at=now, assigned_by_user_id=actor.user_id)
        try:
            await self.uow.users.add(user)
            await self.uow.flush()  # the user row must exist before rows that reference it
            await self.uow.roles.add_assignment(assignment)
            record.user_id, record.status, record.error_category = user.id, PROVISIONING_COMPLETED, None
            record.completed_at = record.updated_at = now
            await self.audit.record("user.created", actor=actor, target_user_id=user.id, institution_id=record.institution_id,
                                    metadata={"provisioning_id": str(record.id), "role_code": role.code,
                                              "linked_existing_identity": linked_existing})
            await self.audit.record("user.role_assigned", actor=actor, target_user_id=user.id,
                                    institution_id=record.institution_id, metadata={"role_code": role.code})
            if audit_action:
                await self.audit.record(audit_action, actor=actor, target_user_id=user.id, institution_id=record.institution_id,
                                        metadata={"provisioning_id": str(record.id), "role_code": role.code})
            await self.uow.commit()
        except PersistenceError as exc:
            conflict = isinstance(exc, PersistenceConflictError)
            await self._mark_failed(record_id, "LOCAL_CONFLICT" if conflict else "DATABASE_ERROR")
            details = {"provisioning_id": str(record_id)}
            if conflict:
                raise ConflictError("The account conflicts with an existing record.", code="ACCOUNT_CONFLICT", details=details) from None
            raise ServiceUnavailableError(
                "The Cognito identity was created but the account could not be saved. Retry this onboarding.",
                code="PROVISIONING_INCOMPLETE", details=details,
            ) from None

        view = AssignmentView(assignment.id, user.id, role.code, role.name, assignment.institution_id, now, actor.user_id)
        return CreateUserResult(UserDetail(user, [view]), record, linked_existing)

    async def _fail(self, record: UserProvisioning, category: str) -> None:
        record.status, record.error_category, record.updated_at = PROVISIONING_FAILED, category, self.clock()
        await self.audit.record("user.provisioning_failed", institution_id=record.institution_id,
                                actor_type="SYSTEM", metadata={"provisioning_id": str(record.id), "error_category": category})
        try:
            await self.uow.commit()
        except PersistenceError:
            logger.warning("Could not record provisioning failure (%s)", category)

    async def _mark_failed(self, record_id: UUID, category: str) -> None:
        try:
            record = await self.uow.provisioning.get(record_id)
            if record is not None and record.status != PROVISIONING_COMPLETED:
                await self._fail(record, category)
        except PersistenceError:
            logger.warning("Could not record provisioning failure (%s)", category)

    # ------------------------------------------------------------------ profile & status

    async def update_user(self, actor: Principal, user_id: UUID, *, full_name: str) -> ChangeResult:
        user = await self._visible_user(actor, user_id, PERM_USERS_UPDATE)
        await self._ensure_may_modify(actor, user)
        name = clean_name(full_name)
        if name == user.full_name:
            return ChangeResult(await self._detail(actor, user), "not_required")
        user.full_name, user.updated_at = name, self.clock()
        await self.audit.record("user.updated", actor=actor, target_user_id=user.id, metadata={"fields": ["full_name"]},
                                institution_id=await self._audit_institution(actor, user, PERM_USERS_UPDATE))
        await self._commit()
        sync = "ok"
        try:
            await self.cognito.update_name(user.cognito_sub, name)
        except CognitoError as exc:
            logger.warning("Cognito name sync failed: %s", exc.category)
            sync = "failed"
        return ChangeResult(await self._detail(actor, user), sync)

    async def disable_user(self, actor: Principal, user_id: UUID, *, reason: str | None = None) -> ChangeResult:
        user = await self._visible_user(actor, user_id, PERM_USERS_DISABLE)
        if user.id == actor.user_id:
            raise PermissionDeniedError("You cannot disable your own account.", code="CANNOT_DISABLE_SELF")
        await self._ensure_may_modify(actor, user)

        if user.account_status != ACCOUNT_DISABLED:
            await self.uow.lock_privileged_changes()
            for institution_id in await self.uow.roles.active_role_institutions(user.id, INSTITUTION_ADMIN):
                if await self.uow.roles.count_active_holders(INSTITUTION_ADMIN, institution_id) <= 1:
                    raise self._last_institution_admin()
        sync = await self.disable_account(actor, user, institution_id=await self._audit_institution(actor, user, PERM_USERS_DISABLE),
                                          reason=reason)
        return ChangeResult(await self._detail(actor, user), sync)

    async def disable_account(self, actor: Principal, user: User, *, institution_id: UUID | None, reason: str | None = None,
                              audit_action: str | None = None) -> str:
        """Disable locally (blocks API access at once), then in Cognito. Does not authorise. Returns the Cognito sync result.

        Calling it again for a disabled account retries the Cognito side.
        """
        if user.account_status != ACCOUNT_DISABLED:
            user.account_status, user.updated_at = ACCOUNT_DISABLED, self.clock()
            await self.audit.record("user.disabled", actor=actor, target_user_id=user.id, metadata={"reason": (reason or None)},
                                    institution_id=institution_id)
            if audit_action:
                await self.audit.record(audit_action, actor=actor, target_user_id=user.id, institution_id=institution_id,
                                        metadata={"reason": (reason or None)})
            await self._commit()

        # Local status already blocks API access; now block sign-in and revoke sessions in Cognito.
        try:
            await self.cognito.disable_user(user.cognito_sub)
            await self.cognito.global_sign_out(user.cognito_sub)
        except CognitoError as exc:
            logger.warning("Cognito disable failed: %s", exc.category)
            await self.audit.record("user.cognito_sync_failed", actor=actor, target_user_id=user.id, institution_id=institution_id,
                                    metadata={"operation": "disable", "error_category": exc.category})
            await self._commit()
            return "failed"
        return "ok"

    async def enable_user(self, actor: Principal, user_id: UUID) -> ChangeResult:
        user = await self._visible_user(actor, user_id, PERM_USERS_DISABLE)
        await self._ensure_may_modify(actor, user)
        await self.enable_account(actor, user, institution_id=await self._audit_institution(actor, user, PERM_USERS_DISABLE))
        return ChangeResult(await self._detail(actor, user), "ok")

    async def enable_account(self, actor: Principal, user: User, *, institution_id: UUID | None, audit_action: str | None = None) -> None:
        """Enable in Cognito first, then locally. Does not authorise."""
        try:
            await self.cognito.enable_user(user.cognito_sub)
        except CognitoError as exc:
            raise self._provider_error(exc) from None
        if user.account_status != ACCOUNT_ACTIVE:
            user.account_status, user.updated_at = ACCOUNT_ACTIVE, self.clock()
            await self.audit.record("user.enabled", actor=actor, target_user_id=user.id, institution_id=institution_id)
            if audit_action:
                await self.audit.record(audit_action, actor=actor, target_user_id=user.id, institution_id=institution_id)
            await self._commit()

    # ------------------------------------------------------------------ roles

    async def set_roles(self, actor: Principal, user_id: UUID, *, institution_id: UUID, role_codes: list[str]) -> UserDetail:
        """Replace the user's active roles *within one institution* with ``role_codes``."""
        self._require(actor, PERM_ROLES_ASSIGN, institution_id)
        user = await self.uow.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        if user.id == actor.user_id:
            raise PermissionDeniedError("You cannot change your own roles.", code="SELF_ROLE_CHANGE_FORBIDDEN")

        desired = {c.strip().upper() for c in role_codes}
        self._reject_platform_roles(desired)
        roles = await self.uow.roles.get_roles_by_codes(desired)
        unknown = desired - roles.keys()
        if unknown:
            raise ValidationFailedError("Unknown role code(s).", code="UNKNOWN_ROLE", details={"unknown_roles": sorted(unknown)})

        current = {role.code: assignment for assignment, role in await self.uow.roles.active_assignments_in(user.id, institution_id)}
        to_add, to_remove = desired - current.keys(), current.keys() - desired
        if not to_add and not to_remove:
            return await self._detail(actor, user)

        await self._ensure_may_modify(actor, user)
        self._check_privileged_grant(actor, to_add | to_remove, institution_id)
        if INSTITUTION_ADMIN in to_remove:
            await self.uow.lock_privileged_changes()
            if user.is_active and await self.uow.roles.count_active_holders(INSTITUTION_ADMIN, institution_id) <= 1:
                raise self._last_institution_admin()

        now = self.clock()
        for code in sorted(to_add):
            await self.uow.roles.add_assignment(UserRole(id=uuid.uuid4(), user_id=user.id, role_id=roles[code].id,
                                                         institution_id=institution_id, assigned_at=now, assigned_by_user_id=actor.user_id))
            await self.audit.record("user.role_assigned", actor=actor, target_user_id=user.id, institution_id=institution_id,
                                    metadata={"role_code": code})
        for code in sorted(to_remove):
            current[code].revoked_at, current[code].revoked_by_user_id = now, actor.user_id
            await self.audit.record("user.role_revoked", actor=actor, target_user_id=user.id, institution_id=institution_id,
                                    metadata={"role_code": code})
        try:
            await self.uow.commit()
        except PersistenceConflictError:
            raise ConflictError("The roles were changed concurrently; reload and try again.", code="CONCURRENT_UPDATE") from None
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None
        return await self._detail(actor, user)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _institutions(actor: Principal, permission: str) -> frozenset[UUID]:
        institutions = actor.institutions_with(permission)
        if not institutions:
            raise PermissionDeniedError(details={"required_permission": permission})
        return institutions

    @staticmethod
    def _require(actor: Principal, permission: str, institution_id: UUID) -> None:
        if not actor.has_permission(permission, institution_id):
            raise PermissionDeniedError(details={"required_permission": permission})

    @staticmethod
    def _check_privileged_grant(actor: Principal, role_codes: set[str], institution_id: UUID) -> None:
        if role_codes & PRIVILEGED_ROLES and not actor.has_role(INSTITUTION_ADMIN, institution_id):
            raise PermissionDeniedError("Only an Institution Admin of this institution can grant or revoke this role.",
                                        code="PRIVILEGED_ROLE_REQUIRED")

    @staticmethod
    def _reject_platform_roles(role_codes: set[str]) -> None:
        platform = role_codes & PLATFORM_ROLE_CODES
        if platform:
            raise ValidationFailedError("Platform roles cannot be granted within an institution.", code="PLATFORM_ROLE_NOT_ASSIGNABLE",
                                        details={"platform_roles": sorted(platform)})

    async def _ensure_may_modify(self, actor: Principal, user: User) -> None:
        await self._ensure_not_platform_account(user)
        for institution_id in await self.uow.roles.active_role_institutions(user.id, INSTITUTION_ADMIN):
            if not actor.has_role(INSTITUTION_ADMIN, institution_id):
                raise PermissionDeniedError("Only an Institution Admin can modify an Institution Admin account.", code="PRIVILEGED_TARGET")

    async def _ensure_not_platform_account(self, user: User) -> None:
        if await self.uow.roles.active_platform_assignments(user.id):
            raise PermissionDeniedError("Platform administrator accounts cannot be managed by an institution.", code="PLATFORM_ACCOUNT")

    async def _audit_institution(self, actor: Principal, user: User, permission: str) -> UUID | None:
        """The institution an action on ``user`` is recorded under: the first one where the actor holds
        ``permission`` and the user has (or had) a role, so the event appears in that institution's audit log."""
        allowed = actor.institutions_with(permission)
        history = (await self.uow.roles.list_assignments([user.id], active_only=False))[user.id]
        return next((a.institution_id for a in history if a.institution_id in allowed), None)

    async def _visible_user(self, actor: Principal, user_id: UUID, permission: str) -> User:
        institutions = self._institutions(actor, permission)
        user = await self.uow.users.get(user_id)
        if user is None or not await self.uow.users.is_visible_in(user_id, institutions):
            raise NotFoundError("User not found.")
        return user

    async def _visible_provisioning(self, actor: Principal, provisioning_id: UUID) -> UserProvisioning:
        record = await self.uow.provisioning.get(provisioning_id)
        if record is None or not actor.has_permission(PERM_USERS_CREATE, record.institution_id):
            raise NotFoundError("Provisioning record not found.")
        return record

    async def _detail(self, actor: Principal, user: User) -> UserDetail:
        visible = actor.institutions_with(PERM_USERS_READ)
        roles = (await self.uow.roles.list_assignments([user.id]))[user.id]
        return UserDetail(user, [r for r in roles if r.institution_id in visible or user.id == actor.user_id])

    async def _role(self, code: str) -> Role:
        """An institution role by code. Platform roles are refused."""
        code = code.strip().upper()
        self._reject_platform_roles({code})
        role = (await self.uow.roles.get_roles_by_codes([code])).get(code)
        if role is None:
            raise ValidationFailedError("Unknown role code.", code="UNKNOWN_ROLE", details={"unknown_roles": [code]})
        return role

    async def _role_by_id(self, role_id: UUID) -> Role:
        for role in await self.uow.roles.list_roles():
            if role.id == role_id:
                return role
        raise NotFoundError("Role not found.")

    async def _commit(self) -> None:
        try:
            await self.uow.commit()
        except PersistenceConflictError:
            raise ConflictError("The record was changed concurrently; reload and try again.", code="CONCURRENT_UPDATE") from None
        except PersistenceError:
            raise ServiceUnavailableError("The database is temporarily unavailable.") from None

    @staticmethod
    def _last_institution_admin() -> ConflictError:
        return ConflictError("This is the last active Institution Admin of the institution. Assign another Institution Admin first.",
                             code="LAST_INSTITUTION_ADMIN")

    @staticmethod
    def _provider_error(exc: CognitoError, provisioning_id: UUID | None = None) -> Exception:
        details = {"error_category": exc.category}
        if provisioning_id is not None:
            details["provisioning_id"] = str(provisioning_id)
        if isinstance(exc, CognitoPermissionError | CognitoMisconfiguredError):
            return ServiceUnavailableError("The Identity Service cannot use Cognito with its current AWS configuration.",
                                           code="IDENTITY_PROVIDER_MISCONFIGURED", details=details)
        if exc.retryable:
            return ServiceUnavailableError("Cognito is temporarily unavailable. Retry shortly.",
                                           code="IDENTITY_PROVIDER_UNAVAILABLE", details=details)
        return IdentityProviderError(details=details)
