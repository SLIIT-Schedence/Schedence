"""Convert service-layer results to response schemas."""

from __future__ import annotations

from app.models import IdentityAuditLog, Institution, UserProvisioning
from app.schemas.platform import InstitutionOut, InstitutionSummaryOut
from app.schemas.rbac import AuditLogOut
from app.schemas.users import ProvisioningOut, RoleAssignmentOut, UserOut
from app.services.users import UserDetail


def user_out(detail: UserDetail) -> UserOut:
    user = detail.user
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        account_status=user.account_status,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=[
            RoleAssignmentOut(role_code=r.role_code, role_name=r.role_name, institution_id=r.institution_id,
                              assigned_at=r.assigned_at, assigned_by_user_id=r.assigned_by_user_id)
            for r in detail.roles
        ],
    )


def institution_out(institution: Institution) -> InstitutionOut:
    return InstitutionOut.model_validate(institution)


def institution_summary_out(institution: Institution) -> InstitutionSummaryOut:
    return InstitutionSummaryOut.model_validate(institution)


def provisioning_out(record: UserProvisioning) -> ProvisioningOut:
    return ProvisioningOut.model_validate(record)


def audit_out(entry: IdentityAuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=entry.id, actor_user_id=entry.actor_user_id, actor_type=entry.actor_type, action=entry.action,
        target_user_id=entry.target_user_id, institution_id=entry.institution_id, metadata=dict(entry.safe_metadata or {}),
        request_id=entry.request_id, occurred_at=entry.occurred_at,
    )
