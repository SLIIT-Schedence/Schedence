"""Operator-only creation of the first Super Admin.

Never reachable over HTTP. The operator must hold database credentials *and*
AWS credentials allowed to read the user pool, and the Cognito identity must
already be genuine: enabled, e-mail verified, and past first sign-in
(CONFIRMED). With ``provision=True`` the command can instead send the Cognito
invitation; the operator then re-runs it after the person has signed in.

Safe to retry: re-running for the person who is already Super Admin is a
no-op, and any *other* existing Super Admin blocks a second bootstrap.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from app.core.clock import Clock, utcnow
from app.integrations.cognito import STATUS_CONFIRMED, CognitoAdminClient, CognitoUserExistsError
from app.models import ACCOUNT_ACTIVE, ACTOR_OPERATOR, User, UserRole
from app.rbac.catalog import SUPER_ADMIN
from app.repositories.base import IdentityUnitOfWork
from app.services.audit import AuditRecorder
from app.services.users import clean_name, normalize_email

BOOTSTRAPPED = "BOOTSTRAPPED"
ALREADY_BOOTSTRAPPED = "ALREADY_BOOTSTRAPPED"  # same person: nothing to do
INVITATION_SENT = "INVITATION_SENT"


class BootstrapError(Exception):
    """Bootstrap refused. ``code`` is stable; the message tells the operator what to do."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class BootstrapResult:
    status: str
    user_id: UUID | None = None
    message: str = ""


class SuperAdminBootstrapService:
    def __init__(self, uow: IdentityUnitOfWork, cognito: CognitoAdminClient, *, clock: Clock = utcnow) -> None:
        self.uow = uow
        self.cognito = cognito
        self.clock = clock

    async def bootstrap(self, *, email: str, institution_id: UUID, operator: str, provision: bool = False,
                        full_name: str | None = None) -> BootstrapResult:
        email = normalize_email(email)
        role = (await self.uow.roles.get_roles_by_codes([SUPER_ADMIN])).get(SUPER_ADMIN)
        if role is None:
            raise BootstrapError("ROLES_NOT_SEEDED", "The SUPER_ADMIN role does not exist. Run scripts.seed_roles first.")

        await self.uow.lock_privileged_changes()  # serialises concurrent bootstrap attempts
        holders = await self.uow.roles.list_active_holders(SUPER_ADMIN)

        identity = await self.cognito.get_user(email)
        if identity is None:
            if holders:
                raise self._already_bootstrapped()
            if not provision:
                raise BootstrapError(
                    "IDENTITY_NOT_FOUND",
                    "No Cognito user exists for this e-mail. Create it in the Cognito console (Users > Create user), "
                    "complete first sign-in, then re-run; or re-run with --provision to send the invitation.",
                )
            try:
                await self.cognito.create_user(email=email, full_name=clean_name(full_name or email.split("@")[0]))
            except CognitoUserExistsError:
                pass
            await AuditRecorder(self.uow, clock=self.clock).record(
                "superadmin.bootstrap_invited", actor_type=ACTOR_OPERATOR, institution_id=institution_id,
                metadata={"operator": operator},
            )
            await self.uow.commit()
            return BootstrapResult(INVITATION_SENT, message=(
                "Cognito invitation sent. After the person completes first sign-in (new password + MFA), run this command again."
            ))

        if normalize_email(identity.email or "") != email:
            raise BootstrapError("IDENTITY_MISMATCH", "The Cognito user's e-mail does not match the e-mail given.")
        if not identity.enabled:
            raise BootstrapError("IDENTITY_DISABLED", "The Cognito user is disabled. Enable it in Cognito first.")
        if not identity.email_verified:
            raise BootstrapError("EMAIL_NOT_VERIFIED", "The Cognito user's e-mail is not verified.")
        if identity.status != STATUS_CONFIRMED:
            raise BootstrapError(
                "IDENTITY_NOT_CONFIRMED",
                f"The Cognito user has not completed first sign-in (status {identity.status}). Sign in once, then re-run.",
            )

        if holders:
            if any(user.cognito_sub == identity.sub for user, _ in holders):
                existing = next(user for user, _ in holders if user.cognito_sub == identity.sub)
                return BootstrapResult(ALREADY_BOOTSTRAPPED, existing.id, "This person is already a Super Admin. Nothing changed.")
            raise self._already_bootstrapped()

        now = self.clock()
        user = await self.uow.users.get_by_cognito_sub(identity.sub)
        if user is None:
            if await self.uow.users.get_by_email(email) is not None:
                raise BootstrapError("EMAIL_LINKED_ELSEWHERE", "A Schedence account with this e-mail is linked to a different identity.")
            user = User(id=uuid.uuid4(), cognito_sub=identity.sub, email=email,
                        full_name=clean_name(full_name or identity.name or email.split("@")[0]),
                        account_status=ACCOUNT_ACTIVE, created_at=now, updated_at=now)
            await self.uow.users.add(user)
            await self.uow.flush()  # the user row must exist before the role assignment and audit entry reference it
        elif user.account_status != ACCOUNT_ACTIVE:
            raise BootstrapError("ACCOUNT_DISABLED", "The existing Schedence account for this person is disabled.")

        await self.uow.roles.add_assignment(UserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id,
                                                     institution_id=institution_id, assigned_at=now, assigned_by_user_id=None))
        await AuditRecorder(self.uow, clock=self.clock).record(
            "superadmin.bootstrapped", actor_type=ACTOR_OPERATOR, target_user_id=user.id, institution_id=institution_id,
            metadata={"operator": operator, "role_code": SUPER_ADMIN},
        )
        await self.uow.commit()
        return BootstrapResult(BOOTSTRAPPED, user.id, "Super Admin bootstrapped.")

    @staticmethod
    def _already_bootstrapped() -> BootstrapError:
        return BootstrapError(
            "ALREADY_BOOTSTRAPPED",
            "A Super Admin already exists. Additional Super Admins must be granted by an existing Super Admin through the API.",
        )
