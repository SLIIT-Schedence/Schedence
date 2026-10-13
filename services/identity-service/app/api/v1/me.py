from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import CurrentUser, UserServiceDep
from app.api.v1.mappers import user_out
from app.schemas.rbac import InstitutionPermissionsOut, MeOut, MePermissionsOut

router = APIRouter(prefix="/me", tags=["current user"])


@router.get("", response_model=MeOut, summary="Current user's profile and roles")
async def get_me(principal: CurrentUser, users: UserServiceDep) -> MeOut:
    out = user_out(await users.me(principal))
    return MeOut(id=out.id, email=out.email, full_name=out.full_name, account_status=out.account_status, roles=out.roles)


@router.get("/permissions", response_model=MePermissionsOut, summary="Current user's effective permissions per institution")
async def get_my_permissions(principal: CurrentUser) -> MePermissionsOut:
    """Also the authorisation contract for other services, which call it with the caller's own access token."""
    institutions = sorted(principal.institution_ids, key=str)
    return MePermissionsOut(
        user_id=principal.user_id,
        cognito_sub=principal.cognito_sub,
        account_status="ACTIVE",
        institutions=[
            InstitutionPermissionsOut(
                institution_id=i,
                roles=sorted(g.role_code for g in principal.roles if g.institution_id == i),
                permissions=sorted(principal.permissions_by_institution.get(i, frozenset())),
            )
            for i in institutions
        ],
    )
