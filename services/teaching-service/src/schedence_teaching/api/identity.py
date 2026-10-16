"""Existing Identity /me/permissions contract; no role claims or second login system."""

from types import MappingProxyType
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, StrictStr, ValidationError

from .authorization import InstitutionGrant, Principal
from .errors import ApiError

PERMISSIONS_PATH = "/api/v1/identity/me/permissions"


class GrantResponse(BaseModel):
    institution_id: UUID
    roles: list[StrictStr]
    permissions: list[StrictStr]


class PermissionsResponse(BaseModel):
    user_id: UUID
    cognito_sub: StrictStr = Field(min_length=1)
    account_status: StrictStr
    institutions: list[GrantResponse]


class IdentityPermissionsClient:
    def __init__(self, base_url, http, *, timeout=5):
        self.base_url, self.http, self.timeout = base_url.rstrip("/"), http, timeout

    async def principal_for(self, token, claims):
        try:
            response = await self.http.get(self.base_url + PERMISSIONS_PATH, timeout=self.timeout,
                headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, follow_redirects=False)
        except httpx.HTTPError:
            raise ApiError(503, "IDENTITY_UNAVAILABLE", "Identity permissions cannot be verified.") from None
        if response.status_code == 401:
            raise ApiError(401, "INVALID_TOKEN", "Authentication was rejected.")
        if response.status_code == 403:
            raise ApiError(403, "ACCOUNT_FORBIDDEN", "The account is not authorized.")
        if response.status_code != 200:
            raise ApiError(503, "IDENTITY_UNAVAILABLE", "Identity permissions cannot be verified.")
        try:
            data = PermissionsResponse.model_validate(response.json())
            keys = [row.institution_id for row in data.institutions]
            if (data.cognito_sub != claims.sub or data.user_id.int == 0 or any(key.int == 0 for key in keys)
                    or len(set(keys)) != len(keys)):
                raise ValueError("inconsistent identity")
        except (ValidationError, ValueError, TypeError):
            raise ApiError(503, "IDENTITY_UNAVAILABLE", "Identity returned inconsistent account information.") from None
        if data.account_status != "ACTIVE":
            raise ApiError(403, "ACCOUNT_DISABLED", "The account is not active.")
        # Fetch on every request so revoked roles are not cached in this service.
        return Principal(data.user_id, data.cognito_sub, MappingProxyType({row.institution_id:
            InstitutionGrant(frozenset(row.roles), frozenset(row.permissions)) for row in data.institutions}))
