"""Verified access-token claims."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Claims of an access token that passed every check in :class:`CognitoTokenVerifier`.

    ``sub`` is the stable Cognito identity reference that services link their
    local user records to. Cognito groups are deliberately not exposed:
    Schedence authorises from its own database, not from token groups.
    """

    sub: str
    client_id: str
    username: str | None
    scopes: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    token_id: str | None = None
    origin_token_id: str | None = None
    auth_time: datetime | None = None

    def has_scopes(self, *scopes: str) -> bool:
        return set(scopes) <= self.scopes
