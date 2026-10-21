"""Request-scoped, GET-only owner service access. Never retains caller tokens."""

from urllib.parse import urlsplit
from uuid import UUID

import httpx

from .errors import ApiError


def trusted_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Owner service requires an HTTPS origin or loopback development origin")
    return value.rstrip("/")


class OwnerApiClient:
    def __init__(self, http, academic_origin, resource_origin=None, *, timeout=10):
        self.http = http
        self.origins = {"academic": trusted_origin(academic_origin)}
        if resource_origin is not None:
            self.origins["resource"] = trusted_origin(resource_origin)
        self.timeout = timeout

    async def get(self, owner, path, institution, bearer_token, *, params=None, error_code="READINESS_UNAVAILABLE"):
        if not bearer_token:
            raise ApiError(401, "UNAUTHENTICATED", "A caller bearer token is required for owner access.")
        if (owner not in self.origins or not path.startswith("/api/") or "?" in path
                or "#" in path or "\\" in path or ".." in path):
            raise ApiError(503, error_code, "The owner API route is not configured correctly.")
        try:
            response = await self.http.get(self.origins[owner] + path,
                params={**(params or {}), "institution_id": str(institution)},
                headers={"Authorization": "Bearer " + bearer_token, "Accept": "application/json"},
                timeout=self.timeout, follow_redirects=False)
        except httpx.HTTPError:
            raise ApiError(503, error_code, f"The {owner} service is unavailable.") from None
        if response.status_code == 401:
            raise ApiError(401, "INVALID_TOKEN", "An owning service rejected the caller's authentication.")
        if response.status_code == 403:
            raise ApiError(403, "OWNER_ACCESS_FORBIDDEN", "An owning service denied access to this institution's data.")
        if response.status_code == 404:
            raise ApiError(503, error_code, f"Required {owner} owner API is not available: {path}.")
        if response.status_code != 200 or len(response.content) > 16 * 1024 * 1024:
            raise ApiError(503, error_code, f"The {owner} service did not return a usable input export.")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or UUID(payload["institution_id"]) != institution:
                raise ValueError("owner tenant mismatch")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ApiError(503, error_code, "An owning service returned inconsistent institution data.") from None
        return payload
