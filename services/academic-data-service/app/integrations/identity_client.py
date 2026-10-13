"""Resolve a caller's permissions by asking the Identity Service.

Integration contract (service-to-service, no shared database):

1. This service verifies the caller's Cognito access token itself (signature,
   issuer, expiry, token_use, client_id) - nothing unverified is forwarded.
2. It then calls ``GET {IDENTITY_SERVICE_URL}/api/v1/identity/me/permissions``
   with the *caller's own* bearer token. The Identity Service re-verifies the
   token, checks the account is active, and returns permissions per institution.
3. The returned ``cognito_sub`` must equal the verified token's ``sub``.

Role or permission headers sent by the frontend are never read. Results are
cached for a few seconds for reads only (keyed by a SHA-256 of the token, never
the token itself); writes always fetch fresh permissions. If the Identity
Service cannot be reached the request is refused (fail closed).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx

from app.core.errors import AuthenticationError, PermissionDeniedError, ServiceUnavailableError
from app.core.principal import Principal
from schedence_auth import AccessTokenClaims

logger = logging.getLogger(__name__)
PERMISSIONS_PATH = "/api/v1/identity/me/permissions"
_FORWARDED_403_CODES = {"ACCOUNT_DISABLED", "ACCOUNT_NOT_PROVISIONED"}


class IdentityPermissionsClient:
    def __init__(self, base_url: str, *, http: httpx.AsyncClient, cache_seconds: int = 15, timeout_seconds: float = 5,
                 clock: Callable[[], float] = time.monotonic, max_entries: int = 2_000) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.cache_seconds = cache_seconds
        self.timeout = timeout_seconds
        self.clock = clock
        self.max_entries = max_entries
        self._cache: OrderedDict[str, tuple[float, Principal]] = OrderedDict()

    async def principal_for(self, token: str, claims: AccessTokenClaims, *, fresh: bool = False) -> Principal:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self.clock()
        cached = self._cache.get(key)
        if not fresh and cached and cached[0] > now:
            return cached[1]

        try:
            response = await self.http.get(f"{self.base_url}{PERMISSIONS_PATH}", timeout=self.timeout,
                                           headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        except httpx.HTTPError as exc:
            logger.warning("Identity Service unreachable (%s)", type(exc).__name__)
            raise ServiceUnavailableError("The Identity Service is unavailable; permissions cannot be verified.",
                                          code="IDENTITY_SERVICE_UNAVAILABLE") from None

        if response.status_code == 401:
            raise AuthenticationError("The access token was rejected by the Identity Service.", code="INVALID_TOKEN")
        if response.status_code == 403:
            code = _error_code(response)
            raise PermissionDeniedError(code=code if code in _FORWARDED_403_CODES else "FORBIDDEN")
        if response.status_code != 200:
            logger.warning("Identity Service returned HTTP %s", response.status_code)
            raise ServiceUnavailableError("The Identity Service returned an unexpected response.", code="IDENTITY_SERVICE_UNAVAILABLE")

        principal = _parse(response)
        if principal.cognito_sub != claims.sub:
            logger.error("Identity Service answered for a different subject")
            raise ServiceUnavailableError("Inconsistent identity information.", code="IDENTITY_SERVICE_UNAVAILABLE")

        if self.cache_seconds > 0:
            token_seconds_left = (claims.expires_at - datetime.now(UTC)).total_seconds()
            self._cache[key] = (now + min(self.cache_seconds, max(token_seconds_left, 0)), principal)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
        return principal


def _error_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def _parse(response: httpx.Response) -> Principal:
    try:
        data = response.json()
        permissions = {UUID(i["institution_id"]): frozenset(i.get("permissions", [])) for i in data["institutions"]}
        roles = {UUID(i["institution_id"]): frozenset(i.get("roles", [])) for i in data["institutions"]}
        return Principal(user_id=UUID(data["user_id"]), cognito_sub=str(data["cognito_sub"]),
                         permissions_by_institution=permissions, roles_by_institution=roles)
    except (ValueError, KeyError, TypeError):
        raise ServiceUnavailableError("The Identity Service returned an invalid response.", code="IDENTITY_SERVICE_UNAVAILABLE") from None
